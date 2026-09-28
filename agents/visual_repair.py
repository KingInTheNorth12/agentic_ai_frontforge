from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from browser.evaluator import BrowserEvaluator
from browser.preview import PreviewServerManager
from browser.visual_llm import VisualDefect, VisualEvaluator
from llm import make_model_for_role
from state import ProjectState
from tools import read_text, safe_project_path, save_json, write_text

try:
    from cli.events import emit, EventType
except ImportError:
    emit = None  # type: ignore
    EventType = None  # type: ignore


MAX_VISUAL_REPAIR_ATTEMPTS = 2

VISUAL_REPAIR_PROMPT = """
You are a specialized CSS and layout visual repair agent in a frontend generation pipeline.
You receive a specific visual defect report for a rendered file and its current source code.

Rules:
- Return only the complete corrected source code. No explanation and no markdown fences.
- Target only the visual spacing, alignment, overflow, or contrast defect identified.
- Do not refactor unrelated functionality, routing, state, or exports.
- Ensure className or style properties fix the layout defect (e.g. overflow-x-hidden, flex-wrap, max-w-full).
"""


class VisualRepairAgent:
    """Targeted visual repair agent mapping rendered defects to affected files and executing bounded repairs."""

    def __init__(self, max_attempts: int = MAX_VISUAL_REPAIR_ATTEMPTS):
        self.max_attempts = max_attempts
        self.model = make_model_for_role("repair", temperature=0.1)

    def map_defect_to_file(self, defect: dict[str, Any] | VisualDefect, blueprint: dict[str, Any]) -> str:
        """Map visual defect to likely target source file using component architecture."""
        target_file = getattr(defect, "target_file", None) or (defect.get("target_file") if isinstance(defect, dict) else None)
        if target_file and target_file.startswith("src/"):
            return target_file

        route = getattr(defect, "route", None) or (defect.get("route") if isinstance(defect, dict) else "/")
        region = getattr(defect, "region", None) or (defect.get("region") if isinstance(defect, dict) else "main")

        # Check routes in blueprint
        for r in blueprint.get("routes", []):
            if isinstance(r, dict) and r.get("path") == route:
                comp = r.get("component")
                if comp:
                    return f"src/pages/{comp}.jsx" if not comp.endswith((".jsx", ".tsx")) else f"src/{comp}"

        # Check components
        for c in blueprint.get("components", []):
            if isinstance(c, dict) and c.get("path"):
                if region and region.lower() in c["path"].lower():
                    return c["path"]

        return "src/App.jsx"

    def repair_visual_defects(self, state: ProjectState, visual_report: dict[str, Any]) -> dict[str, Any]:
        """Execute bounded visual repairs on affected target files."""
        if emit is not None and EventType is not None:
            emit(EventType.VISUAL_REPAIR_STARTED)

        root = state["project_root"]
        blueprint = state.get("blueprint", {})
        findings = visual_report.get("findings", [])

        # Filter findings with overflow or render issues
        defects = [f for f in findings if f.get("horizontal_overflow") or not f.get("render_success")]
        if not defects:
            return {"repaired": False, "files_changed": 0, "attempts": 0}

        repaired_count = 0
        attempts = state.get("visual_repair_attempts", 0) + 1

        if attempts > self.max_attempts:
            return {"repaired": False, "files_changed": 0, "attempts": attempts, "reason": "Max repair attempts reached"}

        target_files = list({self.map_defect_to_file(d, blueprint) for d in defects})

        for rel in target_files:
            try:
                fpath = safe_project_path(root, rel)
                if not fpath.is_file():
                    continue
                code = read_text(root, rel)

                defect_summary = json.dumps([d for d in defects if d.get("route") in rel or "main" in rel], indent=2)
                response = self.model.invoke([
                    {"role": "system", "content": VISUAL_REPAIR_PROMPT},
                    {"role": "user", "content": f"DEFECT REPORT:\n{defect_summary}\n\nCURRENT FILE:\n{code}"},
                ])
                repaired_code = response.content.strip()
                if repaired_code.startswith("```"):
                    lines = repaired_code.splitlines()
                    if len(lines) >= 3 and lines[-1].strip() == "```":
                        repaired_code = "\n".join(lines[1:-1]).strip()

                if repaired_code and repaired_code != code.strip():
                    write_text(root, rel, repaired_code + "\n")
                    repaired_count += 1
            except Exception:
                pass

        if emit is not None and EventType is not None:
            emit(EventType.VISUAL_REPAIR_COMPLETED, files_changed=repaired_count)

        return {
            "repaired": repaired_count > 0,
            "files_changed": repaired_count,
            "attempts": attempts,
            "target_files": target_files,
        }
