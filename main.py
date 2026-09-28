from __future__ import annotations

import argparse
import json
import uuid
from typing import Any

from langgraph.types import Command

from graph import build_graph
from memory import open_memory


def _interrupt_payload(result: dict[str, Any]) -> Any | None:
    interrupts = result.get("__interrupt__")
    if not interrupts:
        return None
    first = interrupts[0]
    return getattr(first, "value", first)


def _answer_clarification(payload: Any) -> dict[str, str]:
    if not isinstance(payload, dict) or payload.get("type") != "clarification":
        return {"freeform": input(f"Agent paused: {payload}\n> ")}

    answers: dict[str, str] = {}
    for question in payload.get("questions", []):
        if not isinstance(question, dict):
            continue
        key = question.get("key", "answer")
        prompt = question.get("prompt", key)
        answers[key] = input(f"{prompt}\n> ").strip()
    return answers


def main() -> None:
    parser = argparse.ArgumentParser(description="LangGraph multi-agent frontend builder")
    parser.add_argument("--thread", default=None, help="Persistent LangGraph thread id")
    parser.add_argument("--project-id", default=None, help="Long-term project-memory namespace")
    parser.add_argument("--user-id", default="local-user")
    parser.add_argument("--request", default=None)
    args = parser.parse_args()

    thread_id = args.thread or uuid.uuid4().hex
    project_id = args.project_id or thread_id
    request = args.request or input("What do you want to build?\n> ").strip()
    if not request:
        raise SystemExit("A build request is required")

    print(f"Thread: {thread_id}")
    print(f"Project memory: {project_id}")

    config = {"configurable": {"thread_id": thread_id}}
    initial_state = {
        "request": request,
        "user_id": args.user_id,
        "project_id": project_id,
        "errors": [],
        "repair_attempts": 0,
    }

    with open_memory() as (checkpointer, store, memory):
        graph = build_graph(checkpointer=checkpointer, store=store, memory=memory)
        result = graph.invoke(initial_state, config=config)

        while True:
            payload = _interrupt_payload(result)
            if payload is None:
                break
            answer = _answer_clarification(payload)
            result = graph.invoke(Command(resume=answer), config=config)

    print(json.dumps({
        "phase": result.get("phase"),
        "build_succeeded": result.get("build_succeeded", False),
        "project_root": result.get("project_root"),
        "repair_attempts": result.get("repair_attempts", 0),
        "errors": result.get("errors", []),
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
