from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import config as app_config
from tools import save_json

class ScreenshotStore:
    """Manages organized screenshot capture & metadata storage per run/viewport/route."""

    def __init__(self, base_dir: Path | None = None):
        self.base_dir = Path(base_dir) if base_dir is not None else app_config.AGENT_DIR / "runs"

    def _get_run_dir(self, run_id: str) -> Path:
        run_dir = self.base_dir / run_id / "screenshots"
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir

    def save_screenshot(
        self,
        run_id: str,
        viewport_name: str,
        route: str,
        image_bytes: bytes,
        metadata: dict[str, Any] | None = None,
    ) -> Path:
        """Save screenshot PNG file and associated JSON metadata."""
        run_dir = self._get_run_dir(run_id)
        viewport_dir = run_dir / viewport_name
        viewport_dir.mkdir(parents=True, exist_ok=True)

        clean_route = route.strip("/").replace("/", "_") or "index"
        img_path = viewport_dir / f"{clean_route}.png"
        meta_path = viewport_dir / f"{clean_route}.json"

        img_path.write_bytes(image_bytes)

        payload = {
            "run_id": run_id,
            "viewport": viewport_name,
            "route": route,
            "image_path": str(img_path),
            "timestamp": time.time(),
            "metadata": metadata or {},
        }
        save_json(meta_path, payload)
        return img_path

    def get_screenshot(self, run_id: str, viewport_name: str, route: str) -> Path | None:
        clean_route = route.strip("/").replace("/", "_") or "index"
        path = self.base_dir / run_id / "screenshots" / viewport_name / f"{clean_route}.png"
        return path if path.is_file() else None


_screenshot_store_instance: ScreenshotStore | None = None


def get_screenshot_store() -> ScreenshotStore:
    global _screenshot_store_instance
    if _screenshot_store_instance is None:
        _screenshot_store_instance = ScreenshotStore()
    return _screenshot_store_instance
