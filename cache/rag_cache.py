from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import config as app_config
from tools import load_json, save_json

class RAGCache:
    """Deterministic cache for RAG documentation retrieval queries."""

    def __init__(self, cache_dir: Path | None = None):
        self.cache_dir = Path(cache_dir) if cache_dir is not None else app_config.AGENT_DIR / "cache" / "rag"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def compute_key(
        self,
        query: str,
        library: str | None = None,
        k: int = 4,
        embedding_model: str | None = None,
        collection: str | None = None,
    ) -> str:
        payload = {
            "query": query,
            "library": library,
            "k": k,
            "embedding_model": embedding_model or app_config.EMBEDDING_MODEL,
            "collection": collection or app_config.RAG_COLLECTION,
            "directory": str(app_config.CHROMA_DIR),
        }
        serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def get(
        self,
        query: str,
        library: str | None = None,
        k: int = 4,
    ) -> list[dict[str, Any]] | None:
        key = self.compute_key(query, library, k)
        path = self.cache_dir / f"{key}.json"
        if not path.is_file():
            return None
        data = load_json(path)
        if isinstance(data, dict) and "results" in data:
            return data["results"]
        return None

    def put(
        self,
        query: str,
        library: str | None,
        k: int,
        results: list[dict[str, Any]],
    ) -> None:
        key = self.compute_key(query, library, k)
        path = self.cache_dir / f"{key}.json"
        save_json(path, {
            "key": key,
            "query": query,
            "library": library,
            "k": k,
            "results": results,
        })


_rag_cache_instance: RAGCache | None = None


def get_rag_cache() -> RAGCache:
    global _rag_cache_instance
    if _rag_cache_instance is None or _rag_cache_instance.cache_dir != app_config.AGENT_DIR / "cache" / "rag":
        _rag_cache_instance = RAGCache()
    return _rag_cache_instance
