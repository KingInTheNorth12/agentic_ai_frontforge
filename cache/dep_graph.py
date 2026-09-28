from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from validators import IMPORT_RE, safe_project_path


class SourceDependencyGraph:
    """Project source file dependency graph for calculating transitive change impact."""

    def __init__(self, project_root: str | Path):
        self.root = Path(project_root).resolve()
        # forward_deps: file -> set of files it imports
        self.forward_deps: dict[str, set[str]] = {}
        # reverse_deps: file -> set of files that import it
        self.reverse_deps: dict[str, set[str]] = {}
        self.build_graph()

    def build_graph(self) -> None:
        """Scan project source files and build forward/reverse dependency graph."""
        self.forward_deps.clear()
        self.reverse_deps.clear()

        if not self.root.exists():
            return

        source_files = []
        for ext in ("*.jsx", "*.tsx", "*.js", "*.ts"):
            for fpath in self.root.rglob(ext):
                if "node_modules" in str(fpath) or ".git" in str(fpath):
                    continue
                try:
                    rel = str(fpath.relative_to(self.root)).replace("\\", "/")
                    source_files.append((rel, fpath))
                except ValueError:
                    pass

        file_paths_set = {rel for rel, _ in source_files}

        for rel, fpath in source_files:
            self.forward_deps[rel] = set()
            if rel not in self.reverse_deps:
                self.reverse_deps[rel] = set()

            try:
                text = fpath.read_text(encoding="utf-8", errors="ignore")
                for specifier in IMPORT_RE.findall(text):
                    if specifier.startswith(("./", "../")):
                        target_rel = self._resolve_relative_import(rel, specifier, file_paths_set)
                        if target_rel:
                            self.forward_deps[rel].add(target_rel)
                            if target_rel not in self.reverse_deps:
                                self.reverse_deps[target_rel] = set()
                            self.reverse_deps[target_rel].add(rel)
            except Exception:
                pass

    def _resolve_relative_import(self, current_rel: str, specifier: str, file_paths: set[str]) -> str | None:
        current_dir = Path(current_rel).parent
        base = (current_dir / specifier).as_posix()

        candidates = [base]
        if not Path(base).suffix:
            candidates += [f"{base}.jsx", f"{base}.tsx", f"{base}.js", f"{base}.ts", f"{base}/index.jsx", f"{base}/index.js"]

        for cand in candidates:
            # Normalize path
            norm = Path(cand).as_posix().lstrip("./")
            if norm in file_paths:
                return norm
        return None

    def get_affected_files(self, changed_files: list[str] | set[str]) -> set[str]:
        """Compute set of all directly and transitively affected files given a set of changed files."""
        affected = set(changed_files)
        queue = list(changed_files)

        while queue:
            curr = queue.pop(0)
            dependents = self.reverse_deps.get(curr, set())
            for dep in dependents:
                if dep not in affected:
                    affected.add(dep)
                    queue.append(dep)

        return affected
