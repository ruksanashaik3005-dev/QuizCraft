from __future__ import annotations

from typing import Any

from vector_store import VectorStoreError, search_chunks


class RAGError(RuntimeError):
    """A retrieval or source-material operation failed."""


def chunk_text(text: str, chunk_size: int = 180, overlap: int = 35) -> list[str]:
    if chunk_size <= 0 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("Chunk size must be positive and overlap must be smaller than the chunk size.")
    words = text.split()
    if not words:
        return []
    chunks = []
    step = chunk_size - overlap
    for start in range(0, len(words), step):
        chunk = " ".join(words[start : start + chunk_size]).strip()
        if chunk:
            chunks.append(chunk)
        if start + chunk_size >= len(words):
            break
    return chunks


def retrieve_study_context(
    collection_id: str, query: str, top_k: int = 4
) -> list[dict[str, Any]]:
    if not collection_id:
        raise RAGError("No indexed study material is available.")
    if not query.strip():
        raise RAGError("Enter a search topic or question.")
    if top_k < 1:
        raise ValueError("top_k must be at least one.")
    try:
        return search_chunks(collection_id, query.strip(), top_k)
    except VectorStoreError:
        raise
    except (ValueError, KeyError) as exc:
        raise RAGError(f"Could not retrieve study passages: {exc}") from exc
