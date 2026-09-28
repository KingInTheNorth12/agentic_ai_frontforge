from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

import config as app_config
from llm import make_model_for_role
from state import ProjectState
from frameworks import adapt_blueprint
from tools import pascal_case, safe_project_path, sanitize_project_name, save_json


class ArchitectureComponent(BaseModel):
    name: str
    description: str
    kind: Literal["page", "layout", "feature", "common"]
    owner: str | None = None


class ArchitectureProposal(BaseModel):
    summary: str
    components: list[ArchitectureComponent] = Field(default_factory=list)
    layout_order: list[str] = Field(default_factory=list)
    component_hierarchy: dict[str, list[str]] = Field(default_factory=dict)
    styling_notes: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)


SYSTEM_PROMPT = """
You are the UI Architecture stage of a frontend generation pipeline.
You receive a typed application plan. Refine component ownership and hierarchy without writing code.

Rules:
- Do not add major pages or features.
- Preserve every planned page and component unless two entries are obvious duplicates.
- Refine only the existing planned components. Do not split a page into extra button, text, or styling components; an empty components list is intentional.
- Classify components as page, layout, feature, or common.
- owner is the page/layout that primarily owns a feature when known; leave null when shared.
- Keep hierarchy shallow and practical.
- Do not produce filesystem paths, package versions, source code, CSS, or shell commands.
"""


def _proposal_from_plan(plan: dict[str, Any]) -> ArchitectureProposal:
    model = make_model_for_role("architect", temperature=0).with_structured_output(ArchitectureProposal)
    return model.invoke([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(plan, ensure_ascii=False)},
    ])


def _component_path(name: str, kind: str) -> str:
    filename = f"{pascal_case(name)}.jsx"
    if kind == "page":
        return f"src/pages/{filename}"
    if kind == "layout":
        return f"src/components/layout/{filename}"
    if kind == "feature":
        return f"src/components/features/{filename}"
    return f"src/components/common/{filename}"


def _build_blueprint(plan: dict[str, Any], proposal: ArchitectureProposal) -> dict[str, Any]:
    components_by_name: dict[str, dict[str, Any]] = {}

    # Start from the planner, then let the architect refine classification/ownership.
    for source_key in ("pages", "components"):
        for item in plan.get(source_key, []):
            if not isinstance(item, dict) or not item.get("name"):
                continue
            kind = "page" if source_key == "pages" else item.get("kind", "common")
            components_by_name[item["name"]] = {
                "name": item["name"],
                "description": item.get("description", ""),
                "kind": kind,
                "owner": None,
            }

    for item in proposal.components:
        if item.name not in components_by_name:
            continue
        existing = components_by_name[item.name]
        existing.update({
            "description": item.description or existing.get("description", ""),
            "kind": "page" if existing.get("kind") == "page" else item.kind,
            "owner": item.owner,
        })

    for item in components_by_name.values():
        item["path"] = _component_path(item["name"], item["kind"])

    pages = [v for v in components_by_name.values() if v["kind"] == "page"]
    layouts = [v for v in components_by_name.values() if v["kind"] == "layout"]
    components = [v for v in components_by_name.values() if v["kind"] in {"feature", "common"}]

    routes: list[dict[str, str]] = []
    page_lookup = {p["name"]: p for p in pages}
    for route in plan.get("routes", []):
        if not isinstance(route, dict):
            continue
        page_name = route.get("page")
        if page_name in page_lookup:
            routes.append({
                "path": route.get("path", "/"),
                "page": page_name,
                "file": page_lookup[page_name]["path"],
            })

    # If there are pages but no explicit routes, provide the minimum useful home route.
    if pages and not routes:
        routes.append({"path": "/", "page": pages[0]["name"], "file": pages[0]["path"]})

    dependencies = list(dict.fromkeys(plan.get("dependencies", [])))
    if routes and "react-router-dom" not in dependencies:
        dependencies.append("react-router-dom")

    infrastructure = [
        {"path": "index.html", "kind": "file", "role": "html_entry"},
        {"path": "vite.config.js", "kind": "file", "role": "vite_config"},
        {"path": "src/main.jsx", "kind": "file", "role": "entry"},
        {"path": "src/App.jsx", "kind": "file", "role": "app_root"},
        {"path": "src/styles/globals.css", "kind": "file", "role": "stylesheet"},
    ]
    if routes:
        infrastructure.append({"path": "src/routes/AppRoutes.jsx", "kind": "file", "role": "routes"})

    generated_items: list[dict[str, Any]] = []
    for item in layouts + pages + components:
        generated_items.append({**item, "role": item["kind"]})
    generated_items.extend(infrastructure)

    # Build order is deterministic. Shared/layout components first, pages next, then app wiring.
    order = (
        [x["path"] for x in layouts]
        + [x["path"] for x in components]
        + [x["path"] for x in pages]
        + ["src/styles/globals.css"]
        + (["src/routes/AppRoutes.jsx"] if routes else [])
        + ["src/App.jsx", "src/main.jsx", "vite.config.js", "index.html"]
    )

    filesystem_paths = {
        "package.json",
        *(item["path"] for item in generated_items),
    }
    directories = {
        str(Path(path).parent).replace("\\", "/")
        for path in filesystem_paths
        if str(Path(path).parent) not in {"", "."}
    }

    return {
        "project_name": sanitize_project_name(plan.get("title")),
        "framework": plan.get("framework", "React + Vite"),
        "summary": proposal.summary or plan.get("purpose", ""),
        "pages": pages,
        "components": components,
        "layouts": layouts,
        "routes": routes,
        "dependencies": dependencies,
        "styling": {
            "library": plan.get("styling", "CSS"),
            "theme": plan.get("theme", ""),
            "notes": proposal.styling_notes,
        },
        "component_hierarchy": {parent: [child for child in children if child in components_by_name] for parent, children in proposal.component_hierarchy.items() if parent in components_by_name},
        "unknowns": proposal.unknowns,
        "entrypoints": infrastructure,
        "build_order": order,
        "generation_items": generated_items,
        "filesystem": (
            [{"path": d, "kind": "directory"} for d in sorted(directories)]
            + [{"path": f, "kind": "file"} for f in sorted(filesystem_paths)]
        ),
        "handoff": {
            "component_agent": "Generate only files in generation_items and use the import map supplied per file.",
            "styling_agent": "Change styling only in explicit style targets.",
            "package_manager_agent": "Own package.json versions and package-lock.json; never infer dependencies from hallucinated imports.",
            "reviewer_agent": "Repair only validated defects or clear purpose mismatches.",
        },
    }


def _scaffold(project_root: Path, blueprint: dict[str, Any]) -> None:
    for item in blueprint["filesystem"]:
        path = safe_project_path(project_root, item["path"])
        if item["kind"] == "directory":
            path.mkdir(parents=True, exist_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch(exist_ok=True)


def run(state: ProjectState) -> dict[str, Any]:
    proposal = _proposal_from_plan(state["plan"])
    plan = dict(state["plan"])
    if state.get("framework"):
        plan["framework"] = state["framework"]
    blueprint = adapt_blueprint(_build_blueprint(plan, proposal))

    # Studio owns a stable directory; fresh CLI generations keep unique roots.
    app_config.WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    project_root = Path(state["managed_project_root"]) if state.get("managed_project_root") else app_config.WORKSPACE_ROOT / f"{blueprint['project_name']}-{uuid.uuid4().hex[:12]}"
    project_root.mkdir(parents=True, exist_ok=True)
    _scaffold(project_root, blueprint)
    from tools import write_text
    for path, content in blueprint["templates"].items():
        write_text(project_root, path, content)
    save_json(project_root / "ui_blueprint.json", blueprint)
    save_json(project_root / "planner.json", state["plan"])

    return {
        "blueprint": blueprint,
        "project_root": str(project_root),
        "phase": "architecture_ready",
    }
