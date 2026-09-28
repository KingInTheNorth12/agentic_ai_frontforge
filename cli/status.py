from __future__ import annotations

import os
from pathlib import Path
from typing import Any


from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from cli.model_selector import get_current_profile, get_role_routing
from config import CHROMA_DIR, DELIBERATION_CONFIG, MODEL_NAME
from sandbox import get_sandbox_manager


def build_status_dict(ctx: Any) -> dict[str, Any]:
    """Compile structured status information from all subsystems."""
    state = getattr(ctx, "_state", None) or {}
    blueprint = state.get("blueprint", {}) if isinstance(state, dict) else {}
    plan = state.get("plan", {}) if isinstance(state, dict) else {}
    package_report = state.get("package_report", {}) if isinstance(state, dict) else {}
    errors = state.get("errors", []) if isinstance(state, dict) else []

    try:
        sbx_cap = get_sandbox_manager().get_capability()
        if sbx_cap.runtime == "mock" and os.getenv("FRONTEND_AGENT_SANDBOX_BACKEND") == "mock":
            sbx_display = "mock (test mode)"
            sbx_available = True
        elif sbx_cap.available and sbx_cap.runtime in ("docker", "apple-container", "apple"):
            sbx_display = f"{sbx_cap.runtime} (✓ active)"
            sbx_available = True
        else:
            sbx_display = "unavailable (no container engine)"
            sbx_available = False
    except Exception:
        sbx_display = "unavailable"
        sbx_available = False

    try:
        role_routing = get_role_routing()
    except Exception:
        role_routing = {}

    try:
        profile = get_current_profile().value
    except Exception:
        profile = "BALANCED"

    locked_deps = package_report.get("locked_versions", {}) if isinstance(package_report, dict) else {}
    dep_count = len(locked_deps) or len(blueprint.get("dependencies", []))

    files = [
        item.get("path")
        for item in blueprint.get("filesystem", [])
        if isinstance(item, dict) and item.get("kind") == "file"
    ]

    cfg = getattr(ctx, "config", None)
    ws_root = getattr(cfg, "workspace_root", ".") if cfg else "."
    delib_val = os.getenv("FRONTEND_AGENT_DELIBERATION", getattr(cfg, "deliberation_config", DELIBERATION_CONFIG) if cfg else DELIBERATION_CONFIG)

    has_rag = bool(CHROMA_DIR.exists() and any(CHROMA_DIR.iterdir())) if hasattr(CHROMA_DIR, "exists") and CHROMA_DIR.exists() else False

    from config import __version__

    return {
        "version": __version__,
        "project_name": blueprint.get("project_name", "unknown") if isinstance(blueprint, dict) else "unknown",
        "project_id": getattr(ctx, "project_id", None) or "default-project",
        "thread_id": getattr(ctx, "thread_id", None) or "no-active-thread",
        "workspace": str(Path(ws_root).resolve()),
        "model_profile": profile,
        "default_model": getattr(cfg, "model", MODEL_NAME) if cfg else MODEL_NAME,
        "role_routing": role_routing,
        "deliberation": delib_val,
        "phase": state.get("phase", "not_started") if isinstance(state, dict) else "not_started",
        "plan_available": bool(plan),
        "blueprint_available": bool(blueprint),
        "files_count": len(files),
        "dependencies_count": dep_count,
        "dependencies_locked": bool(locked_deps),
        "build_succeeded": state.get("build_succeeded", False) if isinstance(state, dict) else False,
        "repair_attempts": state.get("repair_attempts", 0) if isinstance(state, dict) else 0,
        "unresolved_errors": len(errors),
        "sandbox_runtime": sbx_display,
        "sandbox_available": sbx_available,
        "rag_available": has_rag,
    }


def render_full_status(status_data: dict[str, Any], console: Console) -> None:
    """Render high-density, beautiful single-screen status panel in terminal."""
    from config import __version__

    # Top Grid: Core State & Sandbox
    grid = Table.grid(expand=True, padding=(0, 2))
    grid.add_column(ratio=1)
    grid.add_column(ratio=1)

    # Column 1: Project & Pipeline
    p_table = Table(show_header=False, box=None, padding=(0, 1))
    p_table.add_column("Key", style="bold cyan")
    p_table.add_column("Value")
    p_table.add_row("Project:", str(status_data["project_id"]))
    p_table.add_row("Thread ID:", str(status_data["thread_id"])[:16] + "...")
    p_table.add_row("Phase:", f"[bold green]{status_data['phase']}[/bold green]" if status_data['phase'] not in ("failed", "not_started") else ("[bold red]failed[/bold red]" if status_data['phase'] == 'failed' else "[dim]not started[/dim]"))
    p_table.add_row("Deliberation:", f"[yellow]{status_data['deliberation']}[/yellow]")
    p_table.add_row("Plan / Arch:", "✓ Ready" if status_data["plan_available"] and status_data["blueprint_available"] else "— (not generated)")
    p_table.add_row("Generated Files:", f"{status_data['files_count']} files" if status_data['files_count'] > 0 else "0 files")
    p_table.add_row("Build Health:", "[bold green]PASS[/bold green]" if status_data["build_succeeded"] else ("[bold red]FAIL[/bold red]" if status_data["unresolved_errors"] > 0 else "— (not built)"))

    # Column 2: Models & Infrastructure
    m_table = Table(show_header=False, box=None, padding=(0, 1))
    m_table.add_column("Key", style="bold magenta")
    m_table.add_column("Value")
    m_table.add_row("Profile:", f"[bold cyan]{status_data['model_profile']}[/bold cyan]")
    m_table.add_row("Planner Model:", status_data["role_routing"].get("planner", status_data["default_model"]))
    m_table.add_row("Coder Model:", status_data["role_routing"].get("component", status_data["default_model"]))
    m_table.add_row("Repair Model:", status_data["role_routing"].get("repair", status_data["default_model"]))
    deps_status_str = f"{status_data['dependencies_count']} pkgs ({'locked' if status_data['dependencies_locked'] else 'unresolved'})" if status_data['dependencies_count'] > 0 else "none (not resolved)"
    m_table.add_row("Dependencies:", deps_status_str)
    m_table.add_row("Sandbox:", str(status_data["sandbox_runtime"]))
    m_table.add_row("Docs Index:", "✓ indexed" if status_data["rag_available"] else "— not indexed")

    grid.add_row(
        Panel(p_table, title="[bold]Workflow & Pipeline State[/bold]", border_style="cyan"),
        Panel(m_table, title="[bold]Models & Runtime Infrastructure[/bold]", border_style="magenta"),
    )

    console.print(Panel(
        grid,
        title=f"[bold blue]Frontend Agent (v{__version__}) · System Status[/bold blue]",
        border_style="blue",
        subtitle="[dim]Type '/' for command palette · /help <command> for details[/dim]",
    ))

