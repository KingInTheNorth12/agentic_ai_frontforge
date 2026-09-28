from __future__ import annotations

from functools import lru_cache
import os

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_ollama import OllamaEmbeddings

import config as app_config


@lru_cache(maxsize=1)
def _vector_store(directory: str, embedding: str, collection: str) -> Chroma:
    embedding_model = OllamaEmbeddings(model=embedding)
    return Chroma(
        persist_directory=directory,
        collection_name=collection,
        embedding_function=embedding_model,
    )


try:
    from cli.events import emit, EventType
except ImportError:
    emit = None  # type: ignore
    EventType = None  # type: ignore

from cache.rag_cache import get_rag_cache


def retrieve(query: str, *, k: int = 4, library: str | None = None, major_version: str | None = None):
    if not query.strip():
        return []

    if emit is not None and EventType is not None:
        emit(EventType.RETRIEVAL_STARTED, query=query, k=k)

    cache = get_rag_cache()
    cache_library = f"{library}@{major_version}" if major_version else library
    cached_res = cache.get(query, library=cache_library, k=k)
    if cached_res is not None:
        if emit is not None and EventType is not None:
            emit(EventType.RETRIEVAL_COMPLETED, count=len(cached_res), bytes=sum(len(str(x)) for x in cached_res))
        return [Document(page_content=item["page_content"], metadata=item.get("metadata", {})) for item in cached_res]

    store = _vector_store(str(app_config.CHROMA_DIR), app_config.EMBEDDING_MODEL, app_config.RAG_COLLECTION)
    search_kwargs = {"k": k}
    if library:
        search_kwargs["filter"] = {"library": library}
    if major_version:
        search_kwargs["filter"] = {"$and": [{"library": library}, {"major_version": major_version}]}
    docs = store.as_retriever(search_kwargs=search_kwargs).invoke(query)

    serializable_res = [
        {
            "page_content": getattr(d, "page_content", str(d)),
            "metadata": getattr(d, "metadata", {}),
        }
        for d in docs
    ]
    cache.put(query, cache_library, k, serializable_res)

    if emit is not None and EventType is not None:
        emit(EventType.RETRIEVAL_COMPLETED, count=len(docs), bytes=sum(len(str(x)) for x in serializable_res))

    return docs



def retrieve_for_versions(query: str, versions: dict[str, str]):
    """Generation only uses explicitly indexed docs matching installed major versions."""
    if os.getenv("FRONTEND_AGENT_RAG", "0") != "1":
        return []
    docs = []
    for library in ("react", "next", "vue", "svelte", "vite"):
        version = versions.get(library)
        if version:
            docs.extend(retrieve(query, k=2, library=library, major_version=version.split(".")[0]))
    return docs[:4]
