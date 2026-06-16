"""
Google Drive → ChromaDB ingestion pipeline.

Reads regulation documents from a designated Drive folder, chunks them,
embeds them via Claude Haiku, and upserts into ChromaDB.

Trigger: called manually, via `/reload-kb` Slack command, or on a daily schedule.
Credentials: GOOGLE_SERVICE_ACCOUNT_JSON (path to service account JSON) in .env.
Drive folder: GOOGLE_DRIVE_FOLDER_ID in .env.
"""

import json
import hashlib
from pathlib import Path
from datetime import datetime, timezone

from langchain_community.document_loaders import GoogleDriveLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter

from core.memory import chroma
from core.secrets.loader import get, get_optional

_CHUNK_SIZE = int(get_optional("INGEST_CHUNK_SIZE", "800"))
_CHUNK_OVERLAP = int(get_optional("INGEST_CHUNK_OVERLAP", "100"))


def _doc_id(file_id: str, chunk_index: int) -> str:
    return f"{file_id}__chunk_{chunk_index}"


def _chunk_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def run(folder_id: str = None) -> dict:
    """
    Ingest all documents from the Drive folder into ChromaDB.
    Returns {"ingested": int, "skipped": int, "errors": list}.
    """
    folder_id = folder_id or get("GOOGLE_DRIVE_FOLDER_ID")
    service_account_path = get("GOOGLE_SERVICE_ACCOUNT_JSON")

    print(f"[ingest] Loading documents from Drive folder {folder_id}...")

    loader = GoogleDriveLoader(
        folder_id=folder_id,
        service_account_key=service_account_path,
        recursive=False,
    )
    docs = loader.load()
    print(f"[ingest] Loaded {len(docs)} documents from Drive.")

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
            modified = chunk.metadata.get("modifiedTime", datetime.now(timezone.utc).isoformat())
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

            # Flush in batches of 50
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


if __name__ == "__main__":
    result = run()
    print(json.dumps(result, indent=2))
