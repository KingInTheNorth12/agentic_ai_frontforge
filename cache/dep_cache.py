from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import config as app_config
from tools import load_json, save_json

class DependencyInstallCache:
    """Cache for successful npm package lock installations."""

    def __init__(self, cache_dir: Path | None = None):
        self.cache_dir = Path(cache_dir) if cache_dir is not None else app_config.AGENT_DIR / "cache" / "npm_locks"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def compute_key(self, project_root: str | Path) -> str | None:
        lock_path = Path(project_root) / "package-lock.json"
        if not lock_path.is_file():
            return None
        content = lock_path.read_text(encoding="utf-8", errors="ignore")
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def is_cached(self, key: str | None) -> bool:
        if not key:
            return False
        return (self.cache_dir / f"{key}.json").is_file()

    def get(self, key: str | None) -> dict[str, Any] | None:
        if not key:
            return None
        path = self.cache_dir / f"{key}.json"
        if not path.is_file():
            return None
        return load_json(path)

    def put(self, key: str | None, data: dict[str, Any]) -> None:
        if not key:
            return
        path = self.cache_dir / f"{key}.json"
        save_json(path, {
            "key": key,
            "data": data,
        })


_dep_cache_instance: DependencyInstallCache | None = None


def get_dependency_cache() -> DependencyInstallCache:
    global _dep_cache_instance
    if _dep_cache_instance is None:
        _dep_cache_instance = DependencyInstallCache()
    return _dep_cache_instance
