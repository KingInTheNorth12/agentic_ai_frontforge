"""Continue a project through bounded, validated source edits; never regenerate its directory."""
from __future__ import annotations

import difflib
import json
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from cli.events import EventType, emit
from llm import make_model_for_role
from frameworks import generation_rules
from tools import safe_project_path, write_text, save_json
from state import ProjectState


class FileEdit(BaseModel):
    path: str
    content: str


class EditProposal(BaseModel):
    reply: str
    files: list[FileEdit] = Field(default_factory=list, max_length=24)


def run(state: ProjectState) -> dict[str, Any]:
    root = Path(state["project_root"])
    blueprint = dict(state["blueprint"])
    sources = {}
    for item in blueprint.get("filesystem", []):
        path = item.get("path", "")
        if item.get("kind") != "file" or not path or path == "package.json":
            continue
        target = safe_project_path(root, path)
        if target.is_file():
            sources[path] = target.read_text(encoding="utf-8")
    if sum(len(content) for content in sources.values()) > 180_000:
        raise ValueError("This project is too large for a single edit context. Narrow the project before retrying.")
    model = make_model_for_role("repair", temperature=0.1).with_structured_output(EditProposal)
    proposal = model.invoke([
        {"role": "system", "content": "You are continuing an existing frontend project. Answer questions honestly; for requested changes return complete replacement contents for only affected files. Preserve working behavior. New source files are allowed; deletion and dependency/config changes are not. Never modify package manifests, lockfiles, build configuration, or hidden files. Use existing dependencies. No placeholders. State clearly if the request needs unsupported backend infrastructure. " + generation_rules(blueprint.get("framework", "react"))},
        {"role": "user", "content": json.dumps({"request": state["request"], "summary": blueprint.get("summary"), "recent_conversation": state.get("conversation", [])[-8:], "files": sources, "dependencies": state.get("package_report", {}).get("locked_versions", {}), "last_errors": state.get("package_report", {}), "source_errors": state.get("errors", [])})},
    ])
    seen = set()
    for edit in proposal.files:
        path = Path(edit.path)
        safe_project_path(root, edit.path)
        if edit.path in seen or any(part.startswith(".") for part in path.parts) or not edit.path.startswith("src/") or path.suffix not in {".jsx", ".tsx", ".js", ".ts", ".css", ".vue", ".svelte"}:
            raise ValueError(f"Edit outside the allowed source contract: {edit.path}")
        if not edit.content.strip():
            raise ValueError(f"Empty replacement for {edit.path}")
        seen.add(edit.path)
    revision = uuid.uuid4().hex[:12]
    changes = []
    fs = list(blueprint.get("filesystem", []))
    items = list(blueprint.get("generation_items", []))
    for edit in proposal.files:
        target = safe_project_path(root, edit.path)
        old = target.read_text(encoding="utf-8") if target.is_file() else ""
        if old == edit.content:
            continue
        write_text(root / ".agent" / "revisions" / revision, edit.path, old)
        write_text(root, edit.path, edit.content.rstrip() + "\n")
        changes.append({"path": edit.path, "diff": "".join(difflib.unified_diff(old.splitlines(True), edit.content.splitlines(True), fromfile="before/" + edit.path, tofile="after/" + edit.path))})
        if edit.path not in {f["path"] for f in fs}:
            fs.append({"path": edit.path, "kind": "file"})
            items.append({"path": edit.path, "role": "source"})
        emit(EventType.FILE_GENERATED, path=edit.path, operation="edit")
    blueprint.update(filesystem=fs, generation_items=items)
    save_json(root / "ui_blueprint.json", blueprint)
    if changes:
        save_json(root / ".agent" / "changes.json", {"revision": revision, "files": changes})
    return {"blueprint": blueprint, "assistant_reply": proposal.reply, "edit_report": {"revision": revision, "files": changes}, "phase": "edited", "build_succeeded": False if changes else state.get("build_succeeded", False)}
