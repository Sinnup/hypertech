"""
Knowledge Base agent — answers regulatory/policy queries from ChromaDB.
Called by the BA/Compliance agent and other agents that need KB context.
Returns the top-N relevant document chunks with source metadata.
"""

from langfuse import observe
from core.memory import chroma
from core.tracing.langfuse import get_client


@observe(name="kb-query")
def query(question: str, n_results: int = 5) -> dict:
    """
    Search the KB for content relevant to `question`.
    Returns {"hits": [...], "empty": bool}.
    """
    client = get_client()
    if client:
        client.update_current_generation(input={"question": question, "n_results": n_results})

    hits = chroma.query(question, n_results=n_results)

    result = {"hits": hits, "empty": len(hits) == 0}

    if client:
        client.update_current_generation(
            output={"hit_count": len(hits), "empty": result["empty"]},
        )

    return result
