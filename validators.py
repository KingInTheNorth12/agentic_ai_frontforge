from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from tools import safe_project_path


IMPORT_RE = re.compile(
    r"(?:import\s+(?:[^;]*?\s+from\s+)?|export\s+[^;]*?\s+from\s+|require\()"
    r"[\"']([^\"']+)[\"']"
)
DYNAMIC_IMPORT_RE = re.compile(r"import\(\s*[\"']([^\"']+)[\"']\s*\)")

SOURCE_EXTENSIONS = (".js", ".jsx", ".ts", ".tsx", ".css", ".vue", ".svelte")


def _package_root(specifier: str) -> str:
    if specifier.startswith("@"):
        parts = specifier.split("/")
        return "/".join(parts[:2]) if len(parts) >= 2 else specifier
    return specifier.split("/")[0]


def _all_imports(text: str) -> list[str]:
    return list(dict.fromkeys(IMPORT_RE.findall(text) + DYNAMIC_IMPORT_RE.findall(text)))


def _resolve_local(source_file: Path, specifier: str) -> Path | None:
    base = (source_file.parent / specifier).resolve()
    candidates = [base]
    if base.suffix == "":
        candidates += [Path(str(base) + ext) for ext in SOURCE_EXTENSIONS]
        candidates += [base / f"index{ext}" for ext in SOURCE_EXTENSIONS]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def validate_imports(project_root: str, blueprint: dict[str, Any]) -> list[dict[str, Any]]:
    root = Path(project_root).resolve()
    declared = set(blueprint.get("dependencies", []))
    issues: list[dict[str, Any]] = []
    entry = root / "src/main.js"
    entry_owns_router = blueprint.get("framework") == "react" and entry.is_file() and "BrowserRouter" in entry.read_text(encoding="utf-8")

    for fs_item in blueprint.get("filesystem", []):
        if not isinstance(fs_item, dict) or fs_item.get("kind") != "file":
            continue
        rel = fs_item.get("path")
        if not rel or Path(rel).suffix.lower() not in {".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte"}:
            continue
        path = safe_project_path(root, rel)
        if not path.is_file() or not path.read_text(encoding="utf-8").strip():
            issues.append({"stage": "imports", "type": "missing_or_empty_source", "file": rel})
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if entry_owns_router and rel != "src/main.js":
            for match in re.finditer(r"\b(BrowserRouter|HashRouter|MemoryRouter|RouterProvider|Router)\b(?:\s+as\s+([A-Za-z_$][\w$]*))?", text):
                name = match.group(2) or match.group(1)
                if re.search(r"<" + re.escape(name) + r"(?:\s|>)", text):
                    issues.append({"stage": "imports", "type": "nested_router_provider", "file": rel, "detail": "src/main.js already mounts BrowserRouter. Remove this nested router wrapper; render the existing route component directly."})
                    break
        for specifier in _all_imports(text):
            if specifier.startswith(("./", "../")):
                resolved = _resolve_local(path, specifier)
                if resolved is None or not resolved.is_relative_to(root):
                    issues.append({
                        "stage": "imports",
                        "type": "unresolved_local_import",
                        "file": rel,
                        "import": specifier,
                    })
                continue
            if specifier.startswith("/"):
                issues.append({
                    "stage": "imports",
                    "type": "absolute_local_import_not_allowed",
                    "file": rel,
                    "import": specifier,
                })
                continue
            package = _package_root(specifier)
            if package not in declared:
                issues.append({
                    "stage": "imports",
                    "type": "undeclared_external_import",
                    "file": rel,
                    "import": specifier,
                    "package": package,
                })
    return issues


def compact_build_log(stderr: str, stdout: str = "") -> dict[str, Any]:
    """Extract error type, file, line number, and code frame from verbose build logs."""
    text = f"{stderr}\n{stdout}".strip()
    if not text:
        return {"summary": "No log output", "file": None, "line": None, "snippets": []}

    # Match common Vite/TypeScript/ESLint error frames (e.g. src/App.jsx:12:4: error message)
    match = re.search(r"([a-zA-Z0-9_./-]+\.(?:jsx?|tsx?|css|html|vue|svelte)):(\d+)(?::(\d+))?:?\s*(.*)", text)
    if match:
        file_path = match.group(1)
        line_num = int(match.group(2))
        message = match.group(4).strip()
    else:
        file_path = None
        line_num = None
        message = text[:500]

    # Extract relevant snippet lines containing ERROR or failed imports
    snippets = []
    for line in text.splitlines():
        if any(kw in line for kw in ("Error:", "error:", "FAIL", "Cannot find module", "Unexpected token")):
            snippets.append(line.strip())
            if len(snippets) >= 5:
                break

    return {
        "summary": message or text[:200],
        "file": file_path,
        "line": line_num,
        "snippets": snippets or [text[:300]],
    }
