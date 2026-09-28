from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from llm import make_model_for_role
from state import ProjectState


class ComponentSpec(BaseModel):
    name: str
    description: str
    kind: Literal["page", "layout", "feature", "common"] = "common"


class RouteSpec(BaseModel):
    path: str
    page: str


class ApplicationPlan(BaseModel):
    title: str
    purpose: str
    framework: str
    theme: str
    styling: str
    authentication: bool
    responsive: bool
    icons: bool
    pages: list[ComponentSpec]
    components: list[ComponentSpec]
    dependencies: list[str] = Field(default_factory=list)
    routes: list[RouteSpec] = Field(default_factory=list)
    key_sections: list[str] = Field(default_factory=list)
    other: list[str] = Field(default_factory=list)


SYSTEM_PROMPT = """
You are the planning stage of a multi-agent frontend builder.
Turn the normalized requirements into a concrete implementation plan.
The original request and clarification answers are authoritative; normalized requirements may omit details.

Important boundaries:
- Plan product structure and component responsibilities; do not write source code.
- Do not produce filesystem paths. The UI Architect owns paths.
- dependencies contains npm PACKAGE NAMES ONLY. Never include versions, URLs, git specs, shell commands, or flags.
- Prefer the smallest dependency set that actually adds value. Native React/CSS is preferred over unnecessary libraries.
- Honor the requested framework: react, next, vue, or svelte. Use native component conventions. Always include a home page.
- Prefer ordinary CSS with a distinctive theme; do not request Tailwind without its build integration.
- Do not include react, react-dom, next, vue, svelte, vite, or framework Vite plugins; the package manager owns framework baseline packages.
- A dependency may be listed only when generated code genuinely needs it.
- Pages are route-level screens. Reusable site-wide shell pieces are layouts. Feature-specific pieces are features.
- Avoid component fragmentation: do not create a separate component for every small visual element.
- The components list may be empty. Honor requests to keep a small page's controls and state together.
- Page descriptions must retain the requested initial state, actions, labels, and behavior.
- Do not invent major product features outside the requirements.
"""


def run(state: ProjectState) -> dict[str, Any]:
    model = make_model_for_role("planner", temperature=0.2).with_structured_output(ApplicationPlan)
    result = model.invoke([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps({"original_request": state.get("request", ""), "clarification_answers": state.get("clarification_answers", {}), "requirements": state["requirements"]}, ensure_ascii=False)},
    ])
    plan = result.model_dump()
    plan.update(original_request=state.get("request", ""), clarification_answers=state.get("clarification_answers", {}))
    return {"plan": plan, "phase": "planned"}
