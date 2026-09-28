from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import config as app_config
from tools import load_json, save_json

PROMPT_VERSION = "v1.0.0"


class ContentAddressableCache:
    """Content-addressable cache for generated frontend source components."""

    def __init__(self, cache_dir: Path | None = None):
        self.cache_dir = Path(cache_dir) if cache_dir is not None else app_config.AGENT_DIR / "cache" / "components"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def compute_key(
        self,
        component_spec: dict[str, Any],
        arch_context: dict[str, Any] | None = None,
        dependencies: dict[str, str] | None = None,
        styling: str = "CSS",
        rag_chunks: list[dict[str, Any]] | None = None,
        model_name: str | None = None,
        prompt_version: str = PROMPT_VERSION,
    ) -> str:
        """Calculate a deterministic SHA256 key incorporating all generation inputs."""
        payload = {
            "component_spec": component_spec,
            "arch_context": arch_context or {},
            "dependencies": sorted((dependencies or {}).items()),
            "styling": styling,
            "rag_chunks": rag_chunks or [],
            "model_name": model_name or app_config.MODEL_NAME,
            "prompt_version": prompt_version,
        }
        serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def get(self, key: str) -> str | None:
        """Retrieve cached code by key if present."""
        path = self.cache_dir / f"{key}.json"
        if not path.is_file():
            return None
        data = load_json(path)
        if isinstance(data, dict) and "code" in data:
            return data["code"]
        return None

    def put(self, key: str, code: str, metadata: dict[str, Any] | None = None) -> None:
        """Store generated code in content-addressable cache."""
        path = self.cache_dir / f"{key}.json"
        save_json(path, {
            "key": key,
            "code": code,
            "metadata": metadata or {},
        })

    def is_cached(self, key: str) -> bool:
        return (self.cache_dir / f"{key}.json").is_file()


_content_cache_instance: ContentAddressableCache | None = None


def get_content_cache() -> ContentAddressableCache:
    global _content_cache_instance
    if _content_cache_instance is None or _content_cache_instance.cache_dir != app_config.AGENT_DIR / "cache" / "components":
        _content_cache_instance = ContentAddressableCache()
    return _content_cache_instance
