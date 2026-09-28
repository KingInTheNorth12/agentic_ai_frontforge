"""A cancellable process owns one graph invocation and its subprocess group."""
from __future__ import annotations

import os
from pathlib import Path
from studio.storage import StudioStore


def run_worker(directory: str, project_id: str, action: str, content: str):
    if os.name == "posix":
        os.setsid()
    store = StudioStore(Path(directory))
    project = store.get(project_id)
    import config as cfg
    cfg.WORKSPACE_ROOT = Path(directory) / "projects"
    cfg.AGENT_DIR = Path(directory)
    cfg.CHECKPOINT_DB = cfg.AGENT_DIR / "checkpoints.sqlite"
    cfg.STORE_DB = cfg.AGENT_DIR / "store.sqlite"
    cfg.CHROMA_DIR = cfg.AGENT_DIR / "chroma"
    from llm import configure_run
    configure_run(project["provider"], project["model"])
    from cli.events import get_bus
    get_bus().subscribe(lambda event: store.event(project_id, event.type.value, {"node": event.node, **event.data}))
    from memory import open_memory
    from graph import build_graph
    from langgraph.types import Command
    config = {"configurable": {"thread_id": project_id}, "recursion_limit": 60}
    result = project["state"]
    try:
        with open_memory() as (checkpointer, graph_store, memory):
            graph = build_graph(checkpointer=checkpointer, store=graph_store, memory=memory)
            if action == "clarify":
                payload = Command(resume={"freeform": content})
            elif action == "resume":
                payload = None
            else:
                payload = {"request": content, "project_id": project_id, "user_id": project["owner_id"], "framework": project["framework"], "project_root": str(cfg.WORKSPACE_ROOT / project_id), "operation": action, "errors": [], "repair_attempts": 0, "assistant_reply": "", "conversation": [{"role": m["role"], "content": m["content"]} for m in store.messages(project_id)[-8:]]}
                if action in {"generate", "edit"}:
                    payload["edit_report"] = {}
                payload["managed_project_root"] = str(cfg.WORKSPACE_ROOT / project_id)
                if action == "generate":
                    payload.update(build_succeeded=False, clarification_answers={})
            for snapshot in graph.stream(payload, config=config, stream_mode="values"):
                result = {k: v for k, v in snapshot.items() if k != "__interrupt__"}
                title = result.get("requirements", {}).get("title")
                store.update(project_id, state=result, **({"title": title[:100]} if title else {}))
                store.event(project_id, "state", {"phase": result.get("phase"), "build_succeeded": result.get("build_succeeded", False)})
            checkpoint = graph.get_state(config)
            if checkpoint.next:
                questions = result.get("clarification_questions", [])
                store.update(project_id, status="awaiting_input", error=None)
                store.message(project_id, "assistant", "Before I build, I need a little direction:\n\n" + "\n".join(q["prompt"] for q in questions))
            else:
                ok = bool(result.get("build_succeeded"))
                conversational = action == "edit" and not result.get("edit_report", {}).get("files") and bool(result.get("assistant_reply"))
                status = "ready" if ok else ("idle" if conversational else "failed")
                reply = result.get("assistant_reply", "")
                if not conversational:
                    reply += ("\n\nBuild verified. You can inspect the files or open a preview." if ok else "\n\nThe build did not pass. The source is saved; inspect diagnostics or ask me to fix the errors.")
                store.message(project_id, "assistant", reply.strip())
                store.update(project_id, status=status, error=None if ok or conversational else "Build validation failed")
            store.event(project_id, "settled", {"status": store.get(project_id)["status"]})
    except BaseException as exc:
        # Do not expose credential-bearing provider error bodies.
        message = f"{type(exc).__name__}: {str(exc)[:1200]}"
        for name in ("FRONTEND_AGENT_API_KEY", "OPENAI_API_KEY"):
            secret = os.getenv(name)
            if secret:
                message = message.replace(secret, "[redacted]")
        store.update(project_id, status="failed", error=message, state=result)
        store.message(project_id, "assistant", "The operation stopped: " + message + "\nYour saved project is still available. Check the provider or diagnostics, then retry.")
        store.event(project_id, "settled", {"status": "failed", "error": message})
