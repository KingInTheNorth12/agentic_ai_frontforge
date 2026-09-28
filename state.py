from __future__ import annotations

from typing import Any, TypedDict


class ProjectState(TypedDict, total=False):
    operation: str
    framework: str
    managed_project_root: str
    conversation: list[dict[str, str]]
    assistant_reply: str
    edit_report: dict[str, Any]
    request: str
    user_id: str
    project_id: str

    remembered_context: dict[str, Any]
    clarification_draft: dict[str, Any]
    clarification_questions: list[dict[str, str]]
    clarification_answers: dict[str, str]
    requirements: dict[str, Any]

    plan: dict[str, Any]
    blueprint: dict[str, Any]
    project_root: str

    generation_report: dict[str, Any]
    style_report: dict[str, Any]
    package_report: dict[str, Any]
    review_report: dict[str, Any]
    validation_report: dict[str, Any]

    errors: list[dict[str, Any]]
    repair_attempts: int
    build_succeeded: bool
    phase: str
