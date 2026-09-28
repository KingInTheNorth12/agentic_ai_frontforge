from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any

from cli.context import SessionContext
from cli.events import EventType, emit
import config as app_config
from tools import safe_project_path


def cmd_status(ctx: SessionContext) -> dict:
    """Gather project status overview."""
    from cli.status import build_status_dict
    return build_status_dict(ctx)



# Known pipeline topology
_PIPELINE_NODES = [
    "load_memory", "clarify_analyze", "ask_clarifications",
    "finalize_requirements", "planner", "architect",
    "resolve_packages", "generate", "style", "review",
    "packages", "repair", "persist_memory", "persist_failure",
]

_PIPELINE_EDGES = [
    {"source": "load_memory", "target": "clarify_analyze"},
    {"source": "clarify_analyze", "target": "ask_clarifications", "condition": "questions"},
    {"source": "clarify_analyze", "target": "finalize_requirements", "condition": "no questions"},
    {"source": "ask_clarifications", "target": "finalize_requirements"},
    {"source": "finalize_requirements", "target": "planner"},
    {"source": "planner", "target": "architect"},
    {"source": "architect", "target": "resolve_packages"},
    {"source": "resolve_packages", "target": "generate", "condition": "resolution_ok"},
    {"source": "resolve_packages", "target": "persist_failure", "condition": "resolution_failed"},
    {"source": "generate", "target": "style"},
    {"source": "style", "target": "review"},
    {"source": "review", "target": "packages"},
    {"source": "packages", "target": "persist_memory", "condition": "build_passed"},
    {"source": "packages", "target": "repair", "condition": "build_failed, retries left"},
    {"source": "packages", "target": "persist_failure", "condition": "build_failed, no retries"},
    {"source": "repair", "target": "packages"},
]

# Mapping of graph phase -> last completed node
_PHASE_NODE_MAP = {
    "memory_loaded": "load_memory",
    "clarification_analyzed": "clarify_analyze",
    "clarified": "ask_clarifications",
    "requirements_ready": "finalize_requirements",
    "planned": "planner",
    "architecture_ready": "architect",
    "dependencies_resolved": "resolve_packages",
    "dependency_resolution_failed": "resolve_packages",
    "generated": "generate",
    "styled": "style",
    "reviewed": "review",
    "build_passed": "packages",
    "build_failed": "packages",
    "package_blocked": "packages",
    "repaired": "repair",
    "complete": "persist_memory",
    "failed": "persist_failure",
}


def cmd_graph(ctx: SessionContext) -> dict:
    """Return graph topology with current node status."""
    state = ctx.latest_state() or {}
    phase = state.get("phase", "not_started")
    completed_node = _PHASE_NODE_MAP.get(phase)

    # Determine status for each node
    node_statuses = {}
    found_current = False
    for node in _PIPELINE_NODES:
        if node == completed_node:
            node_statuses[node] = "complete"
            found_current = True
        elif not found_current:
            node_statuses[node] = "complete"  # All before current are complete
        else:
            node_statuses[node] = "waiting"

    # If phase indicates failure, mark accordingly
    if phase in ("dependency_resolution_failed", "build_failed", "package_blocked", "failed"):
        if completed_node:
            node_statuses[completed_node] = "failed"

    return {
        "nodes": [{"name": n, "status": node_statuses.get(n, "waiting")} for n in _PIPELINE_NODES],
        "edges": _PIPELINE_EDGES,
        "current_phase": phase,
    }


def cmd_plan(ctx: SessionContext, *, raw: bool = False) -> dict | None:
    """Return the application plan from state."""
    state = ctx.latest_state() or {}
    return state.get("plan")


def cmd_architecture(ctx: SessionContext, *, raw: bool = False) -> dict | None:
    """Return the blueprint from state."""
    state = ctx.latest_state() or {}
    return state.get("blueprint")


def cmd_files(ctx: SessionContext) -> list[dict]:
    """Walk the project root and return file tree entries."""
    root = ctx.project_root
    if not root or not root.exists():
        return []

    entries: list[dict] = []
    skip_dirs = {"node_modules", ".git", ".agent", "__pycache__", ".frontend_agent"}
    for dirpath, dirnames, filenames in os.walk(root):
        # Filter directories in-place
        dirnames[:] = [d for d in dirnames if d not in skip_dirs and not d.startswith(".")]
        rel_root = os.path.relpath(dirpath, root)
        if rel_root == ".":
            rel_root = ""

        for d in sorted(dirnames):
            rel = os.path.join(rel_root, d) if rel_root else d
            entries.append({"path": rel, "is_dir": True, "size": 0})

        for f in sorted(filenames):
            if f.startswith("."):
                continue
            rel = os.path.join(rel_root, f) if rel_root else f
            try:
                target = safe_project_path(root, rel)
                size = target.stat().st_size
            except (ValueError, OSError):
                continue
            entries.append({"path": rel, "is_dir": False, "size": size})
    return entries


def cmd_show(ctx: SessionContext, path: str) -> dict:
    """Return file content with syntax highlighting language."""
    if not ctx.project_root:
        raise ValueError("No project root set")
    target = safe_project_path(ctx.project_root, path)
    if not target.is_file():
        raise FileNotFoundError(f"File not found: {path}")

    content = target.read_text(encoding="utf-8")
    ext = target.suffix.lower().lstrip(".")
    lang_map = {"jsx": "javascript", "tsx": "typescript", "ts": "typescript", "js": "javascript"}
    return {
        "path": path,
        "content": content,
        "language": lang_map.get(ext, ext or "text"),
    }


def cmd_diff(ctx: SessionContext) -> dict:
    """Compare current files with style backups."""
    if not ctx.project_root:
        return {"summary": "No project root", "diffs": []}

    backup_root = ctx.project_root / ".agent" / "backups" / "style"
    if not backup_root.exists():
        return {"summary": "No style backups found", "diffs": []}

    diffs: list[dict] = []
    added, removed = 0, 0
    for backup_file in backup_root.rglob("*"):
        if not backup_file.is_file():
            continue
        rel = str(backup_file.relative_to(backup_root))
        current_file = ctx.project_root / rel
        if not current_file.is_file():
            continue
        old = backup_file.read_text(encoding="utf-8", errors="ignore")
        new = current_file.read_text(encoding="utf-8", errors="ignore")
        if old != new:
            import difflib
            diff_lines = list(difflib.unified_diff(
                old.splitlines(keepends=True),
                new.splitlines(keepends=True),
                fromfile=f"a/{rel}",
                tofile=f"b/{rel}",
            ))
            add = sum(1 for l in diff_lines if l.startswith("+") and not l.startswith("+++"))
            rem = sum(1 for l in diff_lines if l.startswith("-") and not l.startswith("---"))
            added += add
            removed += rem
            diffs.append({"file": rel, "diff_text": "".join(diff_lines)})

    return {
        "summary": f"{len(diffs)} files changed · +{added} -{removed}",
        "diffs": diffs,
    }


def cmd_deps(ctx: SessionContext, subcmd: str | None = None, package: str | None = None) -> dict:
    """Dependency info. subcmd can be 'tree' or 'why'."""
    state = ctx.latest_state() or {}
    report = state.get("package_report", {})
    blueprint = state.get("blueprint", {})

    if subcmd == "tree" and ctx.project_root and ctx.project_root.exists():
        from agents.package_manager import RestrictedNpmRunner, NpmAction
        try:
            runner = RestrictedNpmRunner(ctx.project_root)
            result = runner.execute(NpmAction.LIST_TREE)
            import json
            try:
                tree = json.loads(result.stdout)
            except Exception:
                tree = {"raw": result.stdout}
            return {"tree": tree, "ok": result.ok}
        except Exception as exc:
            return {"error": str(exc)}

    if subcmd == "why" and package:
        # Search locked versions
        locked = report.get("locked_versions", {})
        if package in locked:
            return {"package": package, "version": locked[package], "reason": "direct dependency"}
        return {"package": package, "reason": "not found in direct dependencies"}

    # Default: show dependency list
    deps_list = []
    for pkg in report.get("runtime_dependencies", []):
        version = report.get("locked_versions", {}).get(pkg, "")
        deps_list.append({"name": pkg, "version": version, "type": "runtime", "status": "locked" if version else "unresolved"})
    for pkg in report.get("dev_dependencies", []):
        version = report.get("locked_versions", {}).get(pkg, "")
        deps_list.append({"name": pkg, "version": version, "type": "dev", "status": "locked" if version else "unresolved"})

    return {
        "dependencies": deps_list,
        "resolution_ok": report.get("resolution_ok", False),
    }


def cmd_docs_status(ctx: SessionContext) -> dict:
    """RAG index status."""
    from config import CHROMA_DIR, EMBEDDING_MODEL, RAG_COLLECTION
    exists = CHROMA_DIR.exists()
    result: dict[str, Any] = {
        "collection": RAG_COLLECTION,
        "embedding_model": EMBEDDING_MODEL,
        "chroma_dir": str(CHROMA_DIR),
        "status": "ready" if exists else "not_initialized",
        "libraries": [],
    }
    if exists:
        try:
            from langchain_chroma import Chroma
            from langchain_ollama import OllamaEmbeddings
            store = Chroma(
                persist_directory=str(CHROMA_DIR),
                collection_name=RAG_COLLECTION,
                embedding_function=OllamaEmbeddings(model=EMBEDDING_MODEL),
            )
            collection = store._collection
            count = collection.count()
            result["total_chunks"] = count
        except Exception:
            result["total_chunks"] = "unknown"
    return result


def cmd_docs_query(ctx: SessionContext, query: str) -> list[dict]:
    """Run retrieval and return results."""
    from rag.retriever import retrieve
    try:
        docs = retrieve(query, k=ctx.config.rag_k)
    except Exception as exc:
        return [{"error": str(exc)}]
    results = []
    for doc in docs:
        results.append({
            "content": getattr(doc, "page_content", str(doc)),
            "metadata": getattr(doc, "metadata", {}),
            "library": getattr(doc, "metadata", {}).get("library", "unknown"),
            "score": 0.0,  # Score not directly available from retriever interface
        })
    return results


def cmd_docs_sync(ctx: SessionContext, paths: list[str], library: str) -> dict:
    """Ingest documentation."""
    from rag.ingest import ingest
    count = ingest(paths, library)
    return {"chunks": count, "library": library, "paths": paths}


def cmd_memory(ctx: SessionContext, *, verbose: bool = False) -> dict:
    """Load and display project memory."""
    _, _, memory = ctx.ensure_memory()
    project_id = ctx.project_id or "default-project"
    user_id = "local-user"
    data = memory.load(user_id, project_id)

    if verbose:
        return data

    # Summarize
    summary: dict[str, Any] = {"project_id": project_id}
    if "project_decisions" in data:
        decisions = data["project_decisions"]
        summary["last_status"] = decisions.get("last_status", "unknown")
        summary["has_requirements"] = bool(decisions.get("requirements"))
        summary["has_plan"] = bool(decisions.get("plan"))
    if "dependency_memory" in data:
        dep_mem = data["dependency_memory"]
        summary["locked_versions_count"] = len(dep_mem.get("locked_versions", {}))
    if "repair_memory" in data:
        summary["past_repair_attempts"] = data["repair_memory"].get("repair_attempts", 0)
    if "user_preferences" in data:
        summary["user_preferences"] = data["user_preferences"]
    return summary


def cmd_threads(ctx: SessionContext) -> list[dict]:
    """List threads."""
    return ctx.list_threads()


def cmd_build(ctx: SessionContext) -> dict:
    """Run install_and_build via the restricted package manager."""
    state = ctx.latest_state() or {}
    if not state.get("project_root") and ctx.project_root:
        state["project_root"] = str(ctx.project_root)
    if not state.get("project_root"):
        raise ValueError("No project root. Run a workflow first or set --project.")
    if not state.get("blueprint"):
        raise ValueError("No blueprint. Run a workflow first.")

    from agents.package_manager import install_and_build
    return install_and_build(state)


def cmd_review(ctx: SessionContext) -> dict:
    """Run reviewer preflight."""
    state = ctx.latest_state() or {}
    if not state.get("project_root"):
        raise ValueError("No project root. Run a workflow first.")
    if not state.get("blueprint"):
        raise ValueError("No blueprint. Run a workflow first.")

    from agents.reviewer_agent import preflight
    return preflight(state)


def cmd_repair(ctx: SessionContext) -> dict:
    """Run repair pass."""
    state = ctx.latest_state() or {}
    if not state.get("project_root"):
        raise ValueError("No project root. Run a workflow first.")

    from agents.reviewer_agent import repair_after_build_failure
    return repair_after_build_failure(state)


def cmd_logs(ctx: SessionContext, *, node: str | None = None, errors_only: bool = False) -> list[dict]:
    """Query structured logs."""
    from cli.logging import query_logs
    log_dir = app_config.AGENT_DIR / "logs"
    return query_logs(log_dir, run_id=ctx.run_id, node=node, errors_only=errors_only)


def cmd_stats(ctx: SessionContext) -> dict:
    """Return current metrics (if a MetricsCollector is attached to the bus)."""
    # Metrics are collected by MetricsCollector subscribed to EventBus.
    # We look for it in the bus subscribers.
    from cli.metrics import MetricsCollector
    bus = ctx.bus
    for sub in bus._subscribers:
        if isinstance(sub, MetricsCollector.handle_event.__func__):
            return sub.__self__.current.summary()  # type: ignore
    # Fallback — check if there's a collector bound as a method
    for sub in bus._subscribers:
        obj = getattr(sub, "__self__", None)
        if isinstance(obj, MetricsCollector):
            return obj.current.summary()
    return {"message": "No metrics collector active"}


def cmd_config(ctx: SessionContext) -> dict:
    """Return current configuration."""
    return ctx.config.model_dump()


def cmd_doctor(ctx: SessionContext) -> list[dict]:
    """Run system health checks."""
    checks: list[dict] = []

    # 1. Python version
    v = sys.version_info
    ok = v.major == 3 and v.minor >= 11
    checks.append({
        "name": "Python version",
        "status": "pass" if ok else "fail",
        "message": f"{v.major}.{v.minor}.{v.micro}",
        "remediation": "Upgrade to Python >= 3.11" if not ok else "",
    })

    # 2. Ollama availability
    try:
        import ollama as ollama_client
        ollama_client.list()
        checks.append({
            "name": "Ollama service",
            "status": "pass",
            "message": "Reachable",
            "remediation": "",
        })
    except Exception as exc:
        checks.append({
            "name": "Ollama service",
            "status": "fail",
            "message": str(exc)[:100],
            "remediation": "Install and start Ollama: https://ollama.ai",
        })

    # 3. Required model
    try:
        import ollama as ollama_client
        models = ollama_client.list()
        model_names = [m.model for m in getattr(models, "models", [])]
        has_model = any(ctx.config.model in name for name in model_names)
        checks.append({
            "name": f"Model ({ctx.config.model})",
            "status": "pass" if has_model else "warn",
            "message": "Available" if has_model else "Not found locally",
            "remediation": f"ollama pull {ctx.config.model}" if not has_model else "",
        })
    except Exception:
        checks.append({
            "name": f"Model ({ctx.config.model})",
            "status": "warn",
            "message": "Cannot check (Ollama unavailable)",
            "remediation": "",
        })

    # 4. Node.js/npm
    npm = shutil.which("npm")
    node = shutil.which("node")
    checks.append({
        "name": "Node.js / npm",
        "status": "pass" if (npm and node) else "fail",
        "message": f"node={node or 'missing'}, npm={npm or 'missing'}",
        "remediation": "Install Node.js: https://nodejs.org" if not (npm and node) else "",
    })

    # 5. Checkpoint database
    checks.append({
        "name": "Checkpoint database",
        "status": "pass" if app_config.CHECKPOINT_DB.parent.exists() else "warn",
        "message": str(app_config.CHECKPOINT_DB),
        "remediation": "Will be created on first run" if not app_config.CHECKPOINT_DB.exists() else "",
    })

    # 6. Chroma directory
    checks.append({
        "name": "Chroma (RAG) index",
        "status": "pass" if app_config.CHROMA_DIR.exists() else "warn",
        "message": str(app_config.CHROMA_DIR),
        "remediation": "Run `frontend-agent docs sync` to build" if not app_config.CHROMA_DIR.exists() else "",
    })

    # 7. Write permissions
    workspace = Path(ctx.config.workspace_root).resolve()
    writable = os.access(workspace, os.W_OK)
    checks.append({
        "name": "Workspace writable",
        "status": "pass" if writable else "fail",
        "message": str(workspace),
        "remediation": f"Check permissions on {workspace}" if not writable else "",
    })

    # 8. Package manager safety
    checks.append({
        "name": "npm install scripts",
        "status": "pass" if not app_config.ALLOW_NPM_INSTALL_SCRIPTS else "warn",
        "message": "Disabled (safe)" if not app_config.ALLOW_NPM_INSTALL_SCRIPTS else "ENABLED — lifecycle scripts can run",
        "remediation": "Unset FRONTEND_AGENT_ALLOW_NPM_SCRIPTS for safety" if app_config.ALLOW_NPM_INSTALL_SCRIPTS else "",
    })

    # 9. Sandbox subsystem capabilities
    from sandbox import get_sandbox_manager
    sbx_cap = get_sandbox_manager().get_capability()
    checks.append({
        "name": f"Sandbox Runtime ({sbx_cap.runtime})",
        "status": "pass" if sbx_cap.available else "warn",
        "message": f"available={sbx_cap.available}, net_none={sbx_cap.network_none}, read_only={sbx_cap.readonly_root}, cpu={sbx_cap.cpu_limit}, mem={sbx_cap.memory_limit}",
        "remediation": sbx_cap.details if not sbx_cap.available else "",
    })

    return checks



def cmd_run(ctx: SessionContext, request: str) -> dict:
    """Execute the full pipeline."""
    graph = ctx.ensure_graph()
    thread_id = ctx.thread_id or "default-thread"
    config = {"configurable": {"thread_id": thread_id}}

    initial_state = {
        "request": request,
        "user_id": "local-user",
        "project_id": ctx.project_id or "default-project",
        "errors": [],
        "repair_attempts": 0,
    }

    emit(EventType.WORKFLOW_STARTED, request=request, thread_id=thread_id)
    result = graph.invoke(initial_state, config=config)

    # Update context
    ctx._state = result
    if result.get("project_root"):
        ctx.project_root = Path(result["project_root"])

    emit(
        EventType.WORKFLOW_COMPLETED if result.get("build_succeeded") else EventType.WORKFLOW_FAILED,
        phase=result.get("phase", "unknown"),
    )
    return result


def cmd_resume(ctx: SessionContext) -> dict:
    """Resume an interrupted workflow (no answer)."""
    graph = ctx.ensure_graph()
    config = {"configurable": {"thread_id": ctx.thread_id or "default-thread"}}

    result = graph.invoke(None, config=config)
    ctx._state = result
    if result.get("project_root"):
        ctx.project_root = Path(result["project_root"])
    return result


def cmd_resume_with_answer(ctx: SessionContext, answer: dict[str, str]) -> dict:
    """Resume an interrupted workflow with clarification answers."""
    from langgraph.types import Command

    graph = ctx.ensure_graph()
    config = {"configurable": {"thread_id": ctx.thread_id or "default-thread"}}

    result = graph.invoke(Command(resume=answer), config=config)
    ctx._state = result
    if result.get("project_root"):
        ctx.project_root = Path(result["project_root"])
    return result


def cmd_eval_run(ctx: SessionContext, suite_name: str = "e2e", deliberation: str = "AUTO") -> dict:
    """Run E2E evaluation suite."""
    from eval.runner import E2EEvaluator
    from eval.storage import EvalStorage

    evaluator = E2EEvaluator()

    def run_fn(request: str, case_id: str) -> dict:
        return cmd_run(ctx, request)

    run_data = evaluator.run_suite(runner_func=run_fn)
    storage = EvalStorage()
    jsonl_path, summary_path = storage.save_run(run_data)
    run_data["jsonl_path"] = str(jsonl_path)
    run_data["summary_path"] = str(summary_path)
    return run_data


def cmd_eval_rag(ctx: SessionContext, collection: str | None = None) -> dict:
    """Run RAG evaluation suite."""
    from eval.runner import RAGEvaluator
    from eval.storage import EvalStorage

    evaluator = RAGEvaluator()

    def retriever_fn(query: str, library: str | None) -> list[dict]:
        return cmd_docs_query(ctx, query)

    run_data = evaluator.run_evaluation(retriever_func=retriever_fn)
    return run_data


def cmd_eval_compare(ctx: SessionContext, run_a: str, run_b: str) -> dict:
    """Compare two evaluation runs."""
    from eval.compare import compare_runs, format_comparison_summary
    comparison = compare_runs(run_a, run_b)
    comparison["formatted"] = format_comparison_summary(comparison)
    return comparison


def cmd_eval_failures(ctx: SessionContext, run_id: str) -> list[dict]:
    """Retrieve failure artifacts for a run."""
    import json
    run_failures_dir = app_config.AGENT_DIR / "eval_results" / "failures" / run_id
    if not run_failures_dir.exists():
        return []
    artifacts = []
    for fpath in run_failures_dir.glob("*.json"):
        try:
            artifacts.append(json.loads(fpath.read_text(encoding="utf-8")))
        except Exception:
            pass
    return artifacts


def cmd_preview(ctx: SessionContext, port: int = 5173) -> dict:
    """Launch sandboxed preview server inside SandboxProfile.PREVIEW policy boundary."""
    root = ctx.workspace_root
    from browser.preview import PreviewServerManager
    manager = PreviewServerManager(root, port)
    url = manager.start()
    return {"status": "success", "url": url, "port": port}


def cmd_visual(
    ctx: SessionContext,
    route: str | None = None,
    mobile_only: bool = False,
) -> dict:
    """Run browser visual & functional evaluation across viewports."""
    root = ctx.workspace_root
    from browser.preview import PreviewServerManager
    from browser.evaluator import BrowserEvaluator
    from browser.viewports import DEFAULT_VIEWPORTS, ViewportName

    preview = PreviewServerManager(root)
    url = preview.start()

    routes = [route] if route else ["/"]
    viewports = [DEFAULT_VIEWPORTS[ViewportName.MOBILE]] if mobile_only else list(DEFAULT_VIEWPORTS.values())

    try:
        evaluator = BrowserEvaluator(url, routes=routes, viewports=viewports, run_id=ctx.thread_id or "cli-run")
        return evaluator.evaluate_all()
    finally:
        preview.stop()


def cmd_screenshots(ctx: SessionContext) -> dict:
    """List captured screenshots for current run."""
    from browser.screenshots import get_screenshot_store
    store = get_screenshot_store()
    run_id = ctx.thread_id or "cli-run"
    run_dir = store.base_dir / run_id / "screenshots"

    screenshots = []
    if run_dir.exists():
        for p in run_dir.rglob("*.png"):
            screenshots.append(str(p))

    return {"run_id": run_id, "count": len(screenshots), "screenshots": screenshots}


def cmd_model(
    ctx: SessionContext,
    subcmd: str | None = None,
    arg: str | None = None,
) -> dict[str, Any]:
    """Inspect or configure models, role routing, and profiles."""
    from cli.model_selector import (
        apply_profile,
        discover_models,
        get_current_profile,
        get_role_routing,
        reset_role_routing,
        set_role_model,
        MODEL_ROLES,
    )

    clean_sub = (subcmd or "").lower().strip()

    if clean_sub == "list":
        models = discover_models()
        return {"discovered": [m.__dict__ for m in models], "routing": get_role_routing(), "profile": get_current_profile().value}

    if clean_sub == "reset":
        reset_role_routing()
        return {"status": "success", "message": "Model routing reset to defaults", "routing": get_role_routing(), "profile": get_current_profile().value}

    if clean_sub == "set" and arg:
        set_role_model("global", arg)
        ctx.config.model = arg
        return {"status": "success", "message": f"Global model set to {arg}", "routing": get_role_routing(), "profile": get_current_profile().value}

    if clean_sub == "profile" and arg:
        routing = apply_profile(arg)
        return {"status": "success", "profile": get_current_profile().value, "routing": routing}

    if clean_sub in MODEL_ROLES and arg:
        set_role_model(clean_sub, arg)
        return {"status": "success", "message": f"Role '{clean_sub}' set to {arg}", "routing": get_role_routing(), "profile": get_current_profile().value}

    # Default view
    return {
        "discovered": [m.__dict__ for m in discover_models()],
        "routing": get_role_routing(),
        "profile": get_current_profile().value,
    }


def cmd_deliberation(ctx: SessionContext, mode: str | None = None) -> dict[str, Any]:
    """Configure or view test-time deliberation mode."""
    import os
    if mode:
        clean_mode = mode.upper().strip()
        if clean_mode in ("OFF", "LIGHT", "DEEP", "AUTO"):
            os.environ["FRONTEND_AGENT_DELIBERATION"] = clean_mode
            ctx.config.deliberation_config = clean_mode
            return {"status": "success", "deliberation_mode": clean_mode}

    current = os.getenv("FRONTEND_AGENT_DELIBERATION", ctx.config.deliberation_config or "AUTO")
    return {"deliberation_mode": current}


def cmd_why(ctx: SessionContext) -> dict[str, Any]:
    """Explain recent observable system decisions."""
    state = ctx.latest_state() or {}
    blueprint = state.get("blueprint", {})
    package_report = state.get("package_report", {})
    repair_report = state.get("repair_report", {})
    from cli.model_selector import get_current_profile, get_role_routing
    from config import DELIBERATION_CONFIG

    reasons: dict[str, str] = {}

    prof = get_current_profile()
    reasons["Active Model Profile"] = f"Using {prof.value} profile ({get_role_routing().get('component', 'default')}) based on system configuration."
    reasons["Deliberation Policy"] = f"Deliberation mode is set to {DELIBERATION_CONFIG}."

    deps = blueprint.get("dependencies", [])
    if deps:
        reasons["Selected Dependencies"] = f"Selected {len(deps)} packages ({', '.join(deps[:4])}) for declared UI features and routing."
    else:
        reasons["Selected Dependencies"] = "Only minimal React core dependencies required."

    if state.get("repair_attempts", 0) > 0:
        failures = repair_report.get("build_failures", [])
        reasons["Targeted Repair"] = f"Repair triggered after build failures ({len(failures)} issues detected in vite/npm validation)."
    else:
        reasons["Targeted Repair"] = "No repair required; deterministic validation clean."

    from sandbox import get_sandbox_manager
    cap = get_sandbox_manager().get_capability()
    reasons["Sandbox Runtime"] = f"Executing under {cap.runtime} backend to isolate untrusted npm lifecycle scripts."

    return reasons


def cmd_commands(ctx: SessionContext) -> dict[str, Any]:
    """List all commands categorized."""
    from cli.registry import get_command_registry
    registry = get_command_registry()
    return {cat.value: [c.name for c in cmds] for cat, cmds in registry.by_category().items()}


def cmd_help(ctx: SessionContext, command_name: str | None = None) -> dict[str, Any]:
    """Get help summary or detailed usage for a command."""
    from cli.registry import get_command_registry
    registry = get_command_registry()

    if command_name:
        spec = registry.get(command_name)
        if spec:
            return {
                "name": spec.name,
                "aliases": spec.aliases,
                "category": spec.category.value,
                "short_description": spec.short_description,
                "long_description": spec.long_description,
                "usage": spec.usage,
                "examples": spec.examples,
                "subcommands": spec.subcommands,
            }
        return {"error": f"Command '{command_name}' not found", "suggestions": registry.suggest_similar(command_name)}

    return {"common_commands": [cmd.name for cmd in registry.get_common_commands()]}


def cmd_errors(ctx: SessionContext) -> dict[str, Any]:
    """Inspect active errors and diagnostics."""
    state = ctx.latest_state() or {}
    errors = state.get("errors", [])
    return {"count": len(errors), "errors": errors}


def cmd_context(ctx: SessionContext) -> dict[str, Any]:
    """Inspect compiled prompt context reductions."""
    metrics = ctx.metrics.current
    return {
        "compiled_prompt_bytes": metrics.compiled_prompt_bytes,
        "uncompiled_prompt_bytes": metrics.uncompiled_prompt_bytes,
        "reduction_percent": metrics.summary().get("prompt_context_reduction_percent", 0.0),
    }
