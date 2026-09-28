from __future__ import annotations

import time
from typing import Any, Callable
from functools import wraps

try:
    from cli.events import emit, EventType
except ImportError:
    emit = None  # type: ignore
    EventType = None  # type: ignore

from langgraph.graph import END, START, StateGraph

from agents import editor_agent, UI_Architect, clarification_agent, component_agent, package_manager, planner_agent, reviewer_agent, styler_agent
import config as app_config
from memory import ProjectMemory
from state import ProjectState

def _wrap_node(name: str, func: Callable) -> Callable:
    @wraps(func)
    def wrapper(state: ProjectState) -> dict[str, Any]:
        if emit is not None:
            emit(EventType.NODE_STARTED, node=name)
        start = time.time()
        try:
            result = func(state)
            if emit is not None:
                emit(EventType.NODE_COMPLETED, node=name, duration=time.time() - start)
            return result
        except Exception as e:
            if emit is not None:
                emit(EventType.NODE_FAILED, node=name, error=str(e), duration=time.time() - start)
            raise
    return wrapper

def _resolution_route(state: ProjectState) -> str:
    return "generate" if state.get("package_report", {}).get("resolution_ok") else "persist_failure"


def _package_route(state: ProjectState) -> str:
    if state.get("build_succeeded"):
        return "persist_memory"

    report = state.get("package_report", {})
    if report.get("reason") == "import_contract_violation":
        return "repair" if state.get("repair_attempts", 0) < app_config.MAX_REPAIR_ATTEMPTS else "persist_failure"

    failed_action = None
    for command in report.get("commands", []):
        if not command.get("ok", False):
            failed_action = command.get("action")
            break

    # Source repair can help compiler/build errors. It cannot safely solve an npm
    # registry/peer-dependency resolution failure by rewriting application code.
    if failed_action == "build" and state.get("repair_attempts", 0) < app_config.MAX_REPAIR_ATTEMPTS:
        return "repair"
    return "persist_failure"


def build_graph(*, checkpointer, store, memory: ProjectMemory):
    if emit is not None:
        emit(EventType.WORKFLOW_STARTED)
        
    builder = StateGraph(ProjectState)

    def load_memory(state: ProjectState) -> dict[str, Any]:
        user_id = state.get("user_id", "local-user")
        project_id = state.get("project_id", "default-project")
        return {
            "remembered_context": memory.load(user_id, project_id),
            "repair_attempts": state.get("repair_attempts", 0),
            "errors": state.get("errors", []),
            "phase": "memory_loaded",
        }

    def persist_success(state: ProjectState) -> dict[str, Any]:
        project_id = state.get("project_id", "default-project")
        memory.save_project_decisions(project_id, {
            "requirements": state.get("requirements", {}),
            "plan": state.get("plan", {}),
            "project_root": state.get("project_root"),
            "last_status": "success",
        })
        package_report = state.get("package_report", {})
        memory.save_dependencies(project_id, {
            "locked_versions": package_report.get("locked_versions", {}),
            "runtime_dependencies": package_report.get("runtime_dependencies", []),
            "dev_dependencies": package_report.get("dev_dependencies", []),
        })
        if state.get("repair_attempts", 0):
            memory.save_repairs(project_id, {
                "repair_attempts": state.get("repair_attempts"),
                "review_report": state.get("review_report", {}),
            })
        return {"phase": "complete", "build_succeeded": True}

    def persist_failure(state: ProjectState) -> dict[str, Any]:
        project_id = state.get("project_id", "default-project")
        memory.save_project_decisions(project_id, {
            "requirements": state.get("requirements", {}),
            "plan": state.get("plan", {}),
            "project_root": state.get("project_root"),
            "last_status": "failed",
        })
        return {"phase": "failed", "build_succeeded": False}

    builder.add_node("load_memory", _wrap_node("load_memory", load_memory))
    builder.add_node("clarify_analyze", _wrap_node("clarify_analyze", clarification_agent.analyze))
    builder.add_node("ask_clarifications", _wrap_node("ask_clarifications", clarification_agent.ask_clarifications))
    builder.add_node("finalize_requirements", _wrap_node("finalize_requirements", clarification_agent.finalize))
    builder.add_node("edit", _wrap_node("edit", editor_agent.run))
    builder.add_node("planner", _wrap_node("planner", planner_agent.run))
    builder.add_node("architect", _wrap_node("architect", UI_Architect.run))
    builder.add_node("generate", _wrap_node("generate", component_agent.run))
    builder.add_node("style", _wrap_node("style", styler_agent.run))
    builder.add_node("review", _wrap_node("review", reviewer_agent.preflight))
    builder.add_node("resolve_packages", _wrap_node("resolve_packages", package_manager.resolve))
    builder.add_node("packages", _wrap_node("packages", package_manager.install_and_build))
    builder.add_node("repair", _wrap_node("repair", reviewer_agent.repair_after_build_failure))
    builder.add_node("persist_memory", _wrap_node("persist_memory", persist_success))
    builder.add_node("persist_failure", _wrap_node("persist_failure", persist_failure))

    builder.add_edge(START, "load_memory")
    builder.add_conditional_edges("load_memory", lambda s: s.get("operation", "generate") if s.get("operation") in {"edit", "build"} else "generate", {"generate": "clarify_analyze", "edit": "edit", "build": "packages"})
    builder.add_conditional_edges("edit", lambda s: "review" if s.get("edit_report", {}).get("files") else "done", {"review": "review", "done": END})
    builder.add_conditional_edges(
        "clarify_analyze",
        clarification_agent.route_after_analysis,
        {
            "ask_clarifications": "ask_clarifications",
            "finalize_requirements": "finalize_requirements",
        },
    )
    builder.add_edge("ask_clarifications", "finalize_requirements")
    builder.add_edge("finalize_requirements", "planner")
    builder.add_edge("planner", "architect")
    builder.add_edge("architect", "resolve_packages")
    builder.add_conditional_edges(
        "resolve_packages",
        _resolution_route,
        {"generate": "generate", "persist_failure": "persist_failure"},
    )
    builder.add_edge("generate", "style")
    builder.add_edge("style", "review")
    builder.add_edge("review", "packages")
    builder.add_conditional_edges(
        "packages",
        _package_route,
        {
            "persist_memory": "persist_memory",
            "repair": "repair",
            "persist_failure": "persist_failure",
        },
    )
    builder.add_edge("repair", "packages")
    builder.add_edge("persist_memory", END)
    builder.add_edge("persist_failure", END)

    graph = builder.compile(checkpointer=checkpointer, store=store)
    if emit is not None:
        emit(EventType.CHECKPOINT_SAVED)
    return graph
