from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


try:
    from cli.events import emit, EventType
except ImportError:
    emit = None  # type: ignore
    EventType = None  # type: ignore

from langchain_core.messages import HumanMessage

from frameworks import generation_rules
from state import ProjectState
from tools import read_text, safe_project_path, save_json, sha256_text, write_text


PROMPT = """
You are the styling stage of a frontend generation pipeline.
You receive one already-functional source file and its architecture context.

Rules:
- Return only the complete updated file, with no markdown fences or explanation.
- Change styling and accessibility presentation only.
- Preserve imports, exports, routing, state behavior, event behavior, data flow, component names, and public props.
- Do not add packages, files, pages, routes, hooks, providers, or features.
- Do not remove working functionality.
- Respect the declared styling strategy and theme.
- Improve hierarchy, spacing, typography, responsive behavior, focus-visible states, hover states, contrast, and reduced-motion behavior where appropriate.
- If the file is already well styled, return it unchanged.
"""


def _targets(blueprint: dict[str, Any]) -> list[str]:
    targets: list[str] = []
    for group in ("layouts", "pages", "components"):
        for item in blueprint.get(group, []):
            if isinstance(item, dict) and item.get("path"):
                targets.append(item["path"])
    targets.append("src/styles/globals.css")
    return list(dict.fromkeys(targets))


def _context(path: str, blueprint: dict[str, Any], plan: dict[str, Any]) -> str:
    return json.dumps({
        "target_file": path,
        "theme": plan.get("theme"),
        "styling": blueprint.get("styling"),
        "responsive": plan.get("responsive", True),
        "project_summary": blueprint.get("summary"),
        "framework_rules": generation_rules(blueprint.get("framework", "react")),
    }, ensure_ascii=False, indent=2)


from llm import make_model_for_role


def _has_valid_styling(content: str, rel_path: str) -> bool:
    """Check if component already has intended styling classes or styles."""
    if rel_path.endswith(".css"):
        return len(content.strip()) > 10
    # Check for Tailwind classes, CSS modules, or inline styles
    return bool(re.search(r"(?:className|class)\s*=|style\s*=|<style(?:\s|>)", content))


def run(state: ProjectState) -> dict[str, Any]:
    root = state["project_root"]
    blueprint = state["blueprint"]
    plan = state["plan"]
    model = make_model_for_role("styler", temperature=0.3)

    report: list[dict[str, Any]] = []
    backup_root = Path(root) / ".agent" / "backups" / "style"

    # Check if user explicitly requested a separate styling pass
    force_styler = bool(state.get("force_restyler", False))

    for rel in _targets(blueprint):
        path = safe_project_path(root, rel)
        if not path.is_file():
            report.append({"path": rel, "status": "missing"})
            continue
        current = path.read_text(encoding="utf-8")
        if not current.strip():
            report.append({"path": rel, "status": "empty"})
            continue

        # Selective Styling Check
        if not force_styler and _has_valid_styling(current, rel):
            if emit is not None:
                emit(EventType.STYLER_SKIPPED, path=rel)
            report.append({"path": rel, "status": "skipped_selective", "sha256": sha256_text(current)})
            continue

        try:
            response = model.invoke([
                {"role": "system", "content": PROMPT},
                HumanMessage(content=_context(rel, blueprint, plan) + "\n\nCURRENT FILE:\n" + current),
            ])
            updated = response.content.strip()
            if updated.startswith("```"):
                # Component generation already strips fences; the styler is deliberately strict.
                lines = updated.splitlines()
                if len(lines) >= 3 and lines[-1].strip() == "```":
                    updated = "\n".join(lines[1:-1]).strip()
            if not updated:
                raise ValueError("Styler returned empty content")
            if updated == current.strip():
                report.append({"path": rel, "status": "unchanged", "sha256": sha256_text(current)})
                continue

            backup = backup_root / rel
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_text(current, encoding="utf-8")
            write_text(root, rel, updated + "\n")
            if emit is not None:
                emit(EventType.FILE_STYLED, path=rel)
            report.append({"path": rel, "status": "styled", "sha256": sha256_text(updated)})
        except Exception as exc:
            report.append({"path": rel, "status": "failed", "error": str(exc)})

    payload = {"files": report}
    save_json(Path(root) / "style_report.json", payload)

    errors = list(state.get("errors", []))
    errors.extend(
        {"stage": "styler", "path": item["path"], "error": item.get("error", item["status"])}
        for item in report if item["status"] == "failed"
    )
    return {"style_report": payload, "errors": errors, "phase": "styled"}
