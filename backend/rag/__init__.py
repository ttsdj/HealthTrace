def run_rag_graph(question: str) -> dict:
    from backend.rag.pipeline import run_rag_graph as _run_rag_graph

    return _run_rag_graph(question)

__all__ = ["run_rag_graph"]
