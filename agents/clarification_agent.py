from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field
from langgraph.types import interrupt

try:
    from cli.events import emit, EventType
except ImportError:
    emit = None  # type: ignore
    EventType = None  # type: ignore

from llm import make_model_for_role
from state import ProjectState


class WebsiteRequirements(BaseModel):
    title: str
    purpose: str
    framework: str = "React + Vite"
    styling: str
    theme: str
    authentication: bool
    key_sections: list[str]
    responsive: bool = True
    icons: bool = True
    other: list[str] = Field(default_factory=list)


class RequirementDraft(BaseModel):
    title: str | None = None
    purpose: str | None = None
    framework: str | None = None
    styling: str | None = None
    theme: str | None = None
    authentication: bool | None = None
    key_sections: list[str] | None = None
    responsive: bool | None = None
    icons: bool | None = None
    other: list[str] = Field(default_factory=list)


class ClarificationQuestion(BaseModel):
    key: str
    prompt: str


class ClarificationAnalysis(BaseModel):
    draft: RequirementDraft
    questions: list[ClarificationQuestion] = Field(default_factory=list)


ANALYZE_PROMPT = """
You are the clarification stage of a frontend application builder.
Extract only requirements explicitly stated by the user or already stored as project-specific decisions.
Do not invent missing product decisions.

The implementation supports React + Vite, Vue + Vite, Svelte + Vite, and Next.js static export. Honor the selected framework. Ask only genuinely blocking questions. A sensible complete first version is preferred to unnecessary questions.

Ask only questions whose answers materially affect architecture or generation. Do not interrogate the user
about cosmetic details that can safely be chosen by the planner. Prefer at most six concise questions.
If the user explicitly asks you to choose defaults or proceed without clarification, return no questions.

Project memory is advisory. Reuse project-specific decisions when this is clearly a continuation of the same
project; user text always overrides memory. Generic user preferences are not explicit requirements.
"""

FINALIZE_PROMPT = """
You finalize requirements for a frontend application builder.
Combine the original request, the extracted draft, and the user's clarification answers.
Return a complete WebsiteRequirements object.

Rules:
- Preserve explicit user choices exactly.
- Use React + Vite only when no different framework was requested.
- Styling may be CSS, CSS Modules, Tailwind, or another explicitly requested approach.
- key_sections must be a list of concrete product sections/pages/features.
- Do not add major product features that were never requested.
- Minor conventional implementation details may be placed in 'other'.
"""


def analyze(state: ProjectState) -> dict[str, Any]:
    model = make_model_for_role("clarification", temperature=0.1).with_structured_output(ClarificationAnalysis)
    payload = {
        "request": state.get("request", ""),
        "selected_framework": state.get("framework"),
        "project_memory": state.get("remembered_context", {}).get("project_decisions", {}),
        "user_preferences": state.get("remembered_context", {}).get("user_preferences", {}),
    }
    result = model.invoke([
        {"role": "system", "content": ANALYZE_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ])
    return {
        "clarification_draft": result.draft.model_dump(),
        "clarification_questions": [q.model_dump() for q in result.questions],
        "phase": "clarification_analyzed",
    }


def route_after_analysis(state: ProjectState) -> str:
    return "ask_clarifications" if state.get("clarification_questions") else "finalize_requirements"


def ask_clarifications(state: ProjectState) -> dict[str, Any]:
    questions = state.get("clarification_questions", [])
    if emit is not None:
        emit(EventType.INTERRUPT_REQUESTED)
    response = interrupt({
        "type": "clarification",
        "questions": questions,
        "instruction": "Answer each question using its key.",
    })

    if isinstance(response, dict):
        answers = {str(k): str(v) for k, v in response.items()}
    elif isinstance(response, list):
        answers = {
            q["key"]: str(value)
            for q, value in zip(questions, response)
            if isinstance(q, dict) and q.get("key")
        }
    else:
        answers = {"freeform": str(response)}

    return {"clarification_answers": answers, "phase": "clarified"}


def finalize(state: ProjectState) -> dict[str, Any]:
    model = make_model_for_role("clarification", temperature=0.1).with_structured_output(WebsiteRequirements)
    payload = {
        "request": state.get("request", ""),
        "selected_framework": state.get("framework"),
        "draft": state.get("clarification_draft", {}),
        "answers": state.get("clarification_answers", {}),
    }
    result = model.invoke([
        {"role": "system", "content": FINALIZE_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ])
    requirements = result.model_dump()
    if state.get("framework"):
        requirements["framework"] = state["framework"]
    return {"requirements": requirements, "phase": "requirements_ready"}
