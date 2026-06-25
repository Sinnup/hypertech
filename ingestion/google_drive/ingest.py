"""
Google Drive → ChromaDB ingestion pipeline.

Reads regulation documents from a designated Drive folder, chunks them,
embeds them, and upserts into ChromaDB.

**Public folders** (default): uses ``gdown`` to download without credentials.
Set ``GOOGLE_SERVICE_ACCOUNT_JSON`` in ``.env`` for private / shared-drive folders.

Trigger: called manually, via ``/reload-kb`` Slack command, or on a schedule.
Drive folder: ``GOOGLE_DRIVE_FOLDER_ID`` in ``.env``.
"""

import json
import hashlib
import tempfile
import shutil
from pathlib import Path
from datetime import datetime, timezone

from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import TextLoader

# Prefer pdfminer (better text extraction), fall back to pypdf
try:
    from langchain_community.document_loaders import PDFMinerLoader as PDFLoader
except ImportError:
    try:
        from langchain_community.document_loaders import PyPDFLoader as PDFLoader
    except ImportError:
        PDFLoader = None

from core.memory import chroma
from core.secrets.loader import get, get_optional

_CHUNK_SIZE = int(get_optional("INGEST_CHUNK_SIZE", "800"))
_CHUNK_OVERLAP = int(get_optional("INGEST_CHUNK_OVERLAP", "100"))

# Bundled regulation snippets for offline seeding (no Drive access needed).
_SAMPLE_DOCS_DIR = Path(__file__).resolve().parents[1] / "sample_docs"


def _is_placeholder_sa(path: str) -> bool:
    """True when GOOGLE_SERVICE_ACCOUNT_JSON is the unset placeholder value."""
    if not path:
        return True
    p = path.strip().lower()
    return p in ("path/to/service-account.json", "service-account.json") or "path/to" in p


def _doc_id(file_id: str, chunk_index: int) -> str:
    return f"{file_id}__chunk_{chunk_index}"


def _chunk_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Document download — public folder via gdown, private via service account
# ---------------------------------------------------------------------------

def _download_public_folder(folder_id: str, target_dir: str) -> list[Path]:
    """
    Download all files from a public Google Drive folder using ``gdown``.
    Returns a list of downloaded file paths.
    """
    import gdown

    # gdown can download entire public folders
    url = f"https://drive.google.com/drive/folders/{folder_id}"
    downloaded = gdown.download_folder(
        url=url,
        output=target_dir,
        quiet=False,
        skip_download=False,
    )

    if downloaded is None:
        return []

    # gdown returns a list of file paths (or a single string for one file)
    if isinstance(downloaded, str):
        downloaded = [downloaded]
    return [Path(p) for p in downloaded if Path(p).exists() and Path(p).stat().st_size > 0]


def _download_with_service_account(folder_id: str, service_account_path: str) -> list:
    """
    Download files using LangChain's GoogleDriveLoader (requires service account).
    Returns a list of LangChain Document objects.
    """
    from langchain_community.document_loaders import GoogleDriveLoader

    loader = GoogleDriveLoader(
        folder_id=folder_id,
        service_account_key=service_account_path,
        recursive=False,
    )
    return loader.load()


# ---------------------------------------------------------------------------
# File loading
# ---------------------------------------------------------------------------

def _load_documents(file_paths: list[Path]) -> list:
    """
    Load downloaded files into LangChain Document objects.
    Supports PDF, TXT, MD, JSON, CSV, DOCX.
    """
    docs = []
    for path in file_paths:
        suffix = path.suffix.lower()
        loader = None

        if suffix == ".pdf":
            if PDFLoader is None:
                print(f"  ⚠️  Skipping {path.name}: no PDF loader installed (pip install pdfminer.six)")
                continue
            loader = PDFLoader(str(path))
        elif suffix in (".txt", ".md", ".json", ".csv", ".xml", ".html", ".htm"):
            loader = TextLoader(str(path), encoding="utf-8")
        else:
            # Try text loader for unknown types
            try:
                loader = TextLoader(str(path), encoding="utf-8")
            except Exception:
                print(f"  ⚠️  Skipping unsupported file: {path.name}")
                continue

        try:
            docs.extend(loader.load())
        except Exception as e:
            print(f"  ⚠️  Failed to load {path.name}: {e}")
            continue

    return docs


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run(folder_id: str = None) -> dict:
    """
    Ingest all documents from the Drive folder into ChromaDB.

    Uses ``gdown`` for public folders by default.  Falls back to the
    service-account loader when ``GOOGLE_SERVICE_ACCOUNT_JSON`` points
    to an existing file.
    """
    folder_id = folder_id or get("GOOGLE_DRIVE_FOLDER_ID")
    service_account_path = get_optional("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    sa_is_placeholder = _is_placeholder_sa(service_account_path)
    use_service_account = (
        service_account_path
        and not sa_is_placeholder
        and Path(service_account_path).exists()
    )

    print(f"[ingest] Drive folder: {folder_id}")
    print(f"[ingest] Auth mode: {'service account' if use_service_account else 'public (no credentials)'}")
    if not use_service_account and service_account_path and not Path(service_account_path).exists():
        if sa_is_placeholder:
            print("[ingest] ⚠️  GOOGLE_SERVICE_ACCOUNT_JSON is a placeholder "
                  "('path/to/service-account.json') — falling back to public gdown. "
                  "A private/org Drive folder will NOT download this way.")
        else:
            print(f"[ingest] ⚠️  Service-account file not found at "
                  f"'{service_account_path}' — falling back to public gdown.")

    # ── Download ──────────────────────────────────────────────────────
    tmp_dir = tempfile.mkdtemp(prefix="hypertech_ingest_")

    try:
        if use_service_account:
            print(f"[ingest] Loading documents via service account...")
            docs = _download_with_service_account(folder_id, service_account_path)
        else:
            print(f"[ingest] Downloading from public folder via gdown...")
            file_paths = _download_public_folder(folder_id, tmp_dir)
            if not file_paths:
                hint = (
                    "Configure GOOGLE_SERVICE_ACCOUNT_JSON with a real key file, "
                    "or share the folder as 'anyone with the link'."
                ) if sa_is_placeholder else (
                    "Verify the folder is shared publicly or the service account "
                    "has access."
                )
                return {
                    "ingested": 0,
                    "skipped": 0,
                    "errors": [{
                        "error": (
                            f"No files downloaded from folder {folder_id}. {hint} "
                            f"Folder: https://drive.google.com/drive/folders/{folder_id}. "
                            f"To seed the KB without Drive, run: "
                            f"python -m ingestion.google_drive.ingest --seed-local"
                        )
                    }],
                }
            print(f"[ingest] Downloaded {len(file_paths)} file(s).")
            docs = _load_documents(file_paths)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    if not docs:
        return {
            "ingested": 0,
            "skipped": 0,
            "errors": [{"error": "No documents loaded — unsupported file types or empty folder."}],
        }

    print(f"[ingest] Loaded {len(docs)} document(s).")
    return _chunk_and_upsert(docs)


# ---------------------------------------------------------------------------
# Chunk + upsert (shared by Drive ingest and local seeding)
# ---------------------------------------------------------------------------

def _chunk_and_upsert(docs: list) -> dict:
    """Split LangChain documents into chunks and upsert them into ChromaDB."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=_CHUNK_SIZE,
        chunk_overlap=_CHUNK_OVERLAP,
    )
    chunks = splitter.split_documents(docs)
    print(f"[ingest] Split into {len(chunks)} chunks.")

    ingested = 0
    skipped = 0
    errors = []
    batch_docs, batch_ids, batch_meta = [], [], []

    for i, chunk in enumerate(chunks):
        try:
            file_id = chunk.metadata.get("id", chunk.metadata.get("source", f"doc_{i}"))
            modified = chunk.metadata.get(
                "modifiedTime", datetime.now(timezone.utc).isoformat()
            )
            doc_id = _doc_id(file_id, i)

            batch_docs.append(chunk.page_content)
            batch_ids.append(doc_id)
            batch_meta.append({
                "drive_file_id": file_id,
                "source": chunk.metadata.get("name", file_id),
                "modified_time": modified,
                "chunk_hash": _chunk_hash(chunk.page_content),
                "ingested_at": datetime.now(timezone.utc).isoformat(),
            })
            ingested += 1

            if len(batch_docs) >= 50:
                chroma.upsert(batch_docs, batch_ids, batch_meta)
                batch_docs, batch_ids, batch_meta = [], [], []

        except Exception as e:
            errors.append({"chunk": i, "error": str(e)})
            skipped += 1

    if batch_docs:
        chroma.upsert(batch_docs, batch_ids, batch_meta)

    summary = {"ingested": ingested, "skipped": skipped, "errors": errors}
    print(f"[ingest] Done — {ingested} chunks ingested, {skipped} skipped.")
    return summary


# ---------------------------------------------------------------------------
# Local seeding — populate the KB from bundled sample docs (no Drive needed)
# ---------------------------------------------------------------------------

def seed_local(docs_dir: Path | None = None) -> dict:
    """Ingest the bundled sample regulation docs into ChromaDB.

    Lets demos and tests populate the knowledge base without any Google Drive
    access (useful while the service-account credential is a placeholder).
    """
    docs_dir = docs_dir or _SAMPLE_DOCS_DIR
    files = sorted(p for p in docs_dir.glob("*.md") if p.stat().st_size > 0)
    if not files:
        return {"ingested": 0, "skipped": 0,
                "errors": [{"error": f"No sample docs found in {docs_dir}"}]}

    print(f"[ingest] Seeding KB from {len(files)} local sample doc(s) in {docs_dir}")
    docs = _load_documents(files)
    if not docs:
        return {"ingested": 0, "skipped": 0,
                "errors": [{"error": "Sample docs failed to load."}]}
    return _chunk_and_upsert(docs)


if __name__ == "__main__":
    import sys

    if "--seed-local" in sys.argv:
        result = seed_local()
    else:
        result = run()
    print(json.dumps(result, indent=2))
    try:
        print(json.dumps({"kb_health": chroma.health()}, indent=2))
    except Exception:
        pass
