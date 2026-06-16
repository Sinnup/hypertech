"""
ChromaDB client — shared singleton for all agents that need KB lookups.
"""

import chromadb
from core.secrets.loader import get_optional

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


def query(text: str, n_results: int = 5) -> list[dict]:
    """Similarity search. Returns list of {id, document, metadata, distance}."""
    collection = get_collection()
    count = collection.count()
    if count == 0:
        return []
    results = collection.query(
        query_texts=[text],
        n_results=min(n_results, count),
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
