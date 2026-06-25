"""
ChromaDB client — shared singleton for all agents that need KB lookups.
"""

import logging

import chromadb
from core.secrets.loader import get_optional

logger = logging.getLogger(__name__)

_CHROMA_HOST = get_optional("CHROMADB_HOST", "localhost")
_CHROMA_PORT = int(get_optional("CHROMADB_PORT", "8000"))
_COLLECTION_NAME = get_optional("CHROMADB_COLLECTION", "regulations")

_client: chromadb.HttpClient = None
_collection = None


def _get_client() -> chromadb.HttpClient:
    global _client
    if _client is None:
        _client = chromadb.HttpClient(host=_CHROMA_HOST, port=_CHROMA_PORT)
    return _client


def get_collection():
    global _collection
    if _collection is None:
        client = _get_client()
        _collection = client.get_or_create_collection(
            name=_COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
    return _collection


def _reset() -> None:
    """Drop cached client/collection so a transient failure can recover."""
    global _client, _collection
    _client = None
    _collection = None


def health() -> dict:
    """Return KB health: reachable, document count, and collection name.

    Never raises — callers (agents, /status, tests) use this to distinguish
    "ChromaDB is down" from "the collection is empty", which the bare
    :func:`query` cannot (both return no hits).
    """
    try:
        collection = get_collection()
        return {
            "reachable": True,
            "count": collection.count(),
            "collection": _COLLECTION_NAME,
            "host": f"{_CHROMA_HOST}:{_CHROMA_PORT}",
        }
    except Exception as exc:  # noqa: BLE001 — health probe must not raise
        _reset()
        logger.warning(
            "ChromaDB unreachable at %s:%s — %s", _CHROMA_HOST, _CHROMA_PORT, exc
        )
        return {
            "reachable": False,
            "count": 0,
            "collection": _COLLECTION_NAME,
            "host": f"{_CHROMA_HOST}:{_CHROMA_PORT}",
            "error": str(exc),
        }


def count() -> int:
    """Document count in the collection (0 if empty or unreachable)."""
    try:
        return get_collection().count()
    except Exception:  # noqa: BLE001
        _reset()
        return 0


def query(text: str, n_results: int = 5) -> list[dict]:
    """Similarity search. Returns list of {id, document, metadata, distance}.

    Resilient: on an unreachable server or an empty collection this returns an
    empty list (and logs why) rather than raising, so a missing KB degrades the
    pipeline gracefully instead of crashing it.
    """
    try:
        collection = get_collection()
        n = collection.count()
    except Exception as exc:  # noqa: BLE001
        _reset()
        logger.warning(
            "ChromaDB query failed (server unreachable at %s:%s?): %s",
            _CHROMA_HOST, _CHROMA_PORT, exc,
        )
        return []

    if n == 0:
        logger.info(
            "ChromaDB collection '%s' is EMPTY — ingest the knowledge base "
            "(/reload-kb, or `python -m ingestion.google_drive.ingest --seed-local`).",
            _COLLECTION_NAME,
        )
        return []

    results = collection.query(
        query_texts=[text],
        n_results=min(n_results, n),
        include=["documents", "metadatas", "distances"],
    )
    docs = []
    for i, doc_id in enumerate(results["ids"][0]):
        docs.append({
            "id": doc_id,
            "document": results["documents"][0][i],
            "metadata": results["metadatas"][0][i],
            "distance": results["distances"][0][i],
        })
    return docs


def upsert(documents: list[str], ids: list[str], metadatas: list[dict] = None):
    """Add or update documents in the collection."""
    collection = get_collection()
    collection.upsert(
        documents=documents,
        ids=ids,
        metadatas=metadatas or [{} for _ in ids],
    )
