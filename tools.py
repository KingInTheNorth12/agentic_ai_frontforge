from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any


def sanitize_project_name(name: str | None) -> str:
    if not isinstance(name, str) or not name.strip():
        return "frontend-project"
    clean = re.sub(r"[^a-z0-9]+", "-", name.strip().lower())
    clean = re.sub(r"-+", "-", clean).strip("-")
    return clean or "frontend-project"


def normalize_path(path: str | None) -> str | None:
    if not isinstance(path, str) or not path.strip():
        return None
    value = path.strip().strip("\"'` ").replace("\\", "/")
    if "://" in value or value.startswith("~"):
        return None
    value = value.lstrip("/")
    while value.startswith("./"):
        value = value[2:]
    value = re.sub(r"/+", "/", value)
    if value in {"", ".", "/"} or any(part == ".." for part in value.split("/")):
        return None
    return value


def safe_project_path(root: str | Path, rel: str | None) -> Path:
    normalized = normalize_path(rel)
    if not normalized:
        raise ValueError(f"Unsafe or empty relative path: {rel!r}")
    root_path = Path(root).resolve()
    full = (root_path / normalized).resolve()
    if os.path.commonpath([str(root_path), str(full)]) != str(root_path):
        raise ValueError(f"Path escapes project root: {rel!r}")
    return full


def reset_output_folder(folder: str | Path, *, clean: bool = True) -> Path:
    folder_path = Path(folder).resolve()
    cwd = Path.cwd().resolve()
    if os.path.commonpath([str(cwd), str(folder_path)]) != str(cwd) or folder_path == cwd:
        raise ValueError(f"Refusing to reset unsafe output folder: {folder_path}")
    if clean and folder_path.exists():
        shutil.rmtree(folder_path)
    folder_path.mkdir(parents=True, exist_ok=True)
    return folder_path


def save_json(path: str | Path, data: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def load_json(path: str | Path, default: Any = None) -> Any:
    target = Path(path)
    if not target.is_file():
        return default
    return json.loads(target.read_text(encoding="utf-8"))


def write_text(root: str | Path, rel: str, content: str) -> Path:
    target = safe_project_path(root, rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def read_text(root: str | Path, rel: str) -> str:
    return safe_project_path(root, rel).read_text(encoding="utf-8")


def sha256_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def pascal_case(value: str) -> str:
    parts = re.split(r"[^A-Za-z0-9]+", value or "")
    return "".join(part[:1].upper() + part[1:] for part in parts if part) or "Unnamed"
