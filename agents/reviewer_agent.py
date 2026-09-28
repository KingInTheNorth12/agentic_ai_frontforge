from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

try:
    from cli.events import emit, EventType
except ImportError:
    emit = None  # type: ignore
    EventType = None  # type: ignore

from pydantic import BaseModel, Field

from llm import make_model_for_role
from state import ProjectState
from tools import read_text, safe_project_path, save_json, write_text
from validators import validate_imports
from frameworks import generation_rules
from agents.component_agent import _local_import_map


class ReviewDecision(BaseModel):
    change_required: bool
    reason: list[Literal["syntax_or_build_error", "wrong_import", "purpose_mismatch"]] = Field(default_factory=list)
    issues_found: list[str] = Field(default_factory=list)
    final_code: str = ""
    notes: str = ""


REVIEW_PROMPT = """
You are a conservative code reviewer in a multi-agent frontend pipeline.
Review exactly one existing file against its assigned architecture responsibility.

Change code only for:
1. a supplied deterministic import/build error,
2. a clearly wrong import,
3. a clear mismatch with the file's assigned purpose.

Do not refactor for taste. Do not add features or packages. Do not change valid code merely to make it prettier.
If a change is needed, final_code must be the complete corrected file. Otherwise final_code must be empty.
Use only imports/packages already authorized in the supplied context.
"""


def _spec_for(path: str, blueprint: dict[str, Any]) -> dict[str, Any]:
    for item in blueprint.get("generation_items", []):
        if isinstance(item, dict) and item.get("path") == path:
            return item
    return {"path": path, "role": "source"}


def _review_file(path: str, state: ProjectState, supplied_issues: list[dict[str, Any]]) -> dict[str, Any]:
    root = state["project_root"]
    code = read_text(root, path) if safe_project_path(root, path).is_file() else ""
    spec = _spec_for(path, state["blueprint"])
    payload = {
        "file_path": path,
        "file_spec": spec,
        "project_summary": state["blueprint"].get("summary"),
        "original_request": state.get("plan", {}).get("original_request", ""),
        "authorized_dependencies": state["blueprint"].get("dependencies", []),
        "framework": state["blueprint"].get("framework"),
        "framework_rules": generation_rules(state["blueprint"].get("framework", "react")),
        "allowed_local_imports": _local_import_map(path, state["blueprint"]),
        "related_source": {item["path"]: read_text(root, item["path"]) for item in state["blueprint"].get("generation_items", []) if item["path"] != path and safe_project_path(root, item["path"]).is_file()},
        "routes": state["blueprint"].get("routes", []),
        "known_issues": supplied_issues,
        "current_code": code,
    }
    role = "repair" if any(issue.get("stage") == "build" for issue in supplied_issues) else "review"
    model = make_model_for_role(role, temperature=0).with_structured_output(ReviewDecision)
    decision = model.invoke([
        {"role": "system", "content": REVIEW_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ])

    changed = False
    if decision.change_required:
        if not decision.final_code.strip():
            raise ValueError(f"Reviewer requested change for {path} but returned no final_code")
        write_text(root, path, decision.final_code.rstrip() + "\n")
        changed = True

    return {
        "path": path,
        "change_required": decision.change_required,
        "changed": changed,
        "reason": decision.reason,
        "issues_found": decision.issues_found,
        "notes": decision.notes,
    }


def _paths_from_issues(issues: list[dict[str, Any]], blueprint: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    known_paths = {
        item.get("path") for item in blueprint.get("generation_items", []) if isinstance(item, dict)
    }
    for issue in issues:
        candidate = issue.get("file") or issue.get("path")
        if candidate in known_paths:
            paths.append(candidate)
        # Build output often embeds a path in stderr rather than a structured field.
        text = f"{issue.get('stderr', '')}\n{issue.get('stdout', '')}"
        for path in known_paths:
            if path and path in text:
                paths.append(path)
    return list(dict.fromkeys(paths))


def preflight(state: ProjectState) -> dict[str, Any]:
    import_issues = validate_imports(state["project_root"], state["blueprint"])
    paths = _paths_from_issues(import_issues, state["blueprint"])
    results: list[dict[str, Any]] = []

    # 1. Review files with deterministic import/build issues
    for path in paths:
        file_issues = [i for i in import_issues if i.get("file") == path]
        results.append(_review_file(path, state, file_issues))
        if emit is not None:
            emit(EventType.FILE_REVIEWED, path=path)

    semantic_targets: list[str] = []
    for group in ("pages", "layouts", "components"):
        semantic_targets.extend(
            item["path"] for item in state["blueprint"].get(group, [])
            if isinstance(item, dict) and item.get("path")
        )

    # Unrelated files do not need another model call because a sibling has an error.
    for path in list(dict.fromkeys(semantic_targets)):
        if path not in paths:
            if emit is not None:
                emit(EventType.REVIEW_SKIPPED, path=path)
            results.append({"path": path, "change_required": False, "changed": False, "reason": [], "issues_found": [], "notes": "Selective review pass: skipped LLM review as this file has no deterministic issues."})

    post_issues = validate_imports(state["project_root"], state["blueprint"])
    errors = [e for e in state.get("errors", []) if e.get("stage") != "imports"]
    errors.extend(post_issues)
    report = {"mode": "preflight", "files": results, "remaining_import_issues": post_issues}
    save_json(Path(state["project_root"]) / "review_report.json", report)
    return {"review_report": report, "errors": errors, "phase": "reviewed"}



def repair_after_build_failure(state: ProjectState) -> dict[str, Any]:
    if emit is not None:
        emit(EventType.REPAIR_STARTED)
    package_report = state.get("package_report", {})
    failures: list[dict[str, Any]] = list(package_report.get("import_issues", []))
    for command in package_report.get("commands", []):
        if not command.get("ok", False):
            failures.append({
                "stage": "build",
                "action": command.get("action"),
                "stderr": command.get("stderr", ""),
                "stdout": command.get("stdout", ""),
            })

    targets = _paths_from_issues(failures, state["blueprint"])
    if not targets:
        # App wiring is the safest bounded fallback for build failures that don't name a file.
        targets = [
            path for path in [item["path"] for item in state["blueprint"].get("generation_items", []) if item.get("role") in {"app_root", "page", "entry"}]
            if safe_project_path(state["project_root"], path).is_file()
        ]

    results = [_review_file(path, state, failures) for path in targets]
    if emit is not None:
        emit(EventType.REPAIR_COMPLETED, files_changed=sum(1 for r in results if r["changed"]))
    attempts = state.get("repair_attempts", 0) + 1
    report = {"mode": "build_repair", "files": results, "build_failures": failures, "attempt": attempts}
    save_json(Path(state["project_root"]) / f"repair_report_{attempts}.json", report)
    return {
        "review_report": report,
        "repair_attempts": attempts,
        "phase": "repaired",
    }
