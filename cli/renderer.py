from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, List, Optional

from rich.console import Console, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree
from rich.syntax import Syntax
from rich.live import Live
from rich.text import Text
from rich.markdown import Markdown
from rich.console import Group
from rich import box

from cli.events import Event, EventType


class Renderer:
    """Rich-based terminal rendering module."""

    def __init__(self, *, use_color: bool = True, quiet: bool = False, json_mode: bool = False):
        self.console = Console(force_terminal=use_color, no_color=not use_color, quiet=quiet)
        self.json_mode = json_mode
        self.quiet = quiet
        self._live: Live | None = None
        self._node_states: dict[str, str] = {}
        self._file_states: dict[str, dict[str, str]] = {}
        self._node_times: dict[str, float] = {}

    def banner(self, context_info: dict) -> None:
        if self.json_mode or self.quiet:
            return

        from config import __version__

        table = Table(box=None, show_header=False, pad_edge=False)
        table.add_column("Field", style="bold cyan", width=18)
        table.add_column("Value", style="white")

        for key, value in context_info.items():
            label = key.replace("_", " ").title()
            table.add_row(
                Text(f"{label}:", style="bold cyan"),
                Text(str(value), style="white"),
            )

        panel = Panel(
            table,
            title=f"[bold blue]Frontend Agent[/bold blue] (v{__version__})",
            title_align="left",
            border_style="blue",
            expand=False,
        )
        self.console.print(panel)


    def node_progress_start(self, nodes: list[str]) -> None:
        if self.json_mode or self.quiet:
            return

        for node in nodes:
            self._node_states[node] = "waiting"
            self._file_states[node] = {}

        self._live = Live(self._build_progress_display(), console=self.console, refresh_per_second=4)
        self._live.start()

    def update_node(self, node: str, status: str) -> None:
        if self.json_mode or self.quiet:
            return

        self._node_states[node] = status
        if status == "running":
            self._node_times[node] = time.time()

        if self._live:
            self._live.update(self._build_progress_display())

    def update_file(self, node: str, file: str, status: str) -> None:
        if self.json_mode or self.quiet:
            return

        if node not in self._file_states:
            self._file_states[node] = {}
        self._file_states[node][file] = status

        if self._live:
            self._live.update(self._build_progress_display())

    def node_progress_stop(self) -> None:
        if self._live:
            self._live.stop()
            self._live = None

    def _build_progress_display(self) -> RenderableType:
        tree = Tree("Graph Execution Progress", guide_style="bold blue")

        indicators = {
            "complete": "[bold green]●[/bold green]",
            "running": "[bold yellow]◐[/bold yellow]",
            "waiting": "[dim]○[/dim]",
            "failed": "[bold red]✗[/bold red]",
        }

        for node, status in self._node_states.items():
            ind = indicators.get(status, indicators["waiting"])
            node_label = Text.from_markup(f"{ind} {node}")
            
            if status == "running" and node in self._node_times:
                elapsed = time.time() - self._node_times[node]
                node_label.append(f" ({elapsed:.1f}s)", style="dim")

            node_tree = tree.add(node_label)

            for file_name, file_status in self._file_states.get(node, {}).items():
                f_ind = indicators.get(file_status, indicators["waiting"])
                node_tree.add(Text.from_markup(f"{f_ind} {file_name}"))

        return panel_wrap(tree, title="Execution Progress")

    def render_status(self, data: dict) -> None:
        if self.json_mode:
            self.json_output(data)
            return

        table = Table(title="Project Status", box=box.ROUNDED, show_header=True)
        table.add_column("Component", style="cyan", no_wrap=True)
        table.add_column("Status", style="magenta")

        for key, value in data.items():
            table.add_row(str(key).replace("_", " ").title(), str(value))

        self.console.print(table)

    def render_plan(self, plan: dict, *, raw: bool = False) -> None:
        if self.json_mode or raw:
            self.json_output(plan)
            return

        table = Table(title="Execution Plan", box=box.SIMPLE)
        table.add_column("Type", style="cyan", bold=True)
        table.add_column("Name", style="white")
        table.add_column("Dependencies", style="dim")

        for page in plan.get("pages", []):
            table.add_row("Page", page.get("name", ""), ", ".join(page.get("deps", [])))
        for comp in plan.get("components", []):
            table.add_row("Component", comp.get("name", ""), ", ".join(comp.get("deps", [])))
        for route in plan.get("routes", []):
            table.add_row("Route", route.get("path", ""), "")

        self.console.print(table)

    def render_architecture(self, blueprint: dict, *, raw: bool = False) -> None:
        if self.json_mode or raw:
            self.json_output(blueprint)
            return

        tree = Tree("[bold cyan]Architecture Blueprint[/bold cyan]")
        
        def dict_to_tree(d: dict, t: Tree):
            for k, v in d.items():
                if isinstance(v, dict):
                    branch = t.add(f"[yellow]{k}[/yellow]")
                    dict_to_tree(v, branch)
                elif isinstance(v, list):
                    branch = t.add(f"[yellow]{k}[/yellow]")
                    for item in v:
                        branch.add(str(item))
                else:
                    t.add(f"[green]{k}[/green]: {v}")

        dict_to_tree(blueprint, tree)
        self.console.print(panel_wrap(tree))

    def render_files(self, file_tree: list[dict]) -> None:
        if self.json_mode:
            self.json_output(file_tree)
            return

        tree = Tree("[bold]Workspace Files[/bold]")
        for item in file_tree:
            icon = "📁" if item.get("is_dir") else "📄"
            size = f" ({item.get('size', 0)} bytes)" if not item.get("is_dir") else ""
            tree.add(f"{icon} {item.get('path', '')}{size}")

        self.console.print(panel_wrap(tree))

    def render_file(self, path: str, content: str, *, language: str | None = None) -> None:
        if self.json_mode:
            self.json_output({"path": path, "content": content})
            return

        if not language:
            language = path.split(".")[-1] if "." in path else "text"
            
        syntax = Syntax(content, language, theme="monokai", line_numbers=True, word_wrap=True)
        panel = Panel(syntax, title=f"File: [bold]{path}[/bold]", border_style="blue")
        self.console.print(panel)

    def render_diff(self, diff_data: dict) -> None:
        if self.json_mode:
            self.json_output(diff_data)
            return

        self.console.print(Text(diff_data.get("summary", ""), style="bold magenta"))
        for diff in diff_data.get("diffs", []):
            syntax = Syntax(diff.get("diff_text", ""), "diff", theme="monokai")
            self.console.print(Panel(syntax, title=diff.get("file", ""), border_style="dim"))

    def render_deps(self, deps_data: dict) -> None:
        if self.json_mode:
            self.json_output(deps_data)
            return

        table = Table(title="Dependencies", box=box.ROUNDED)
        table.add_column("Name", style="bold cyan")
        table.add_column("Version", style="green")
        table.add_column("Type", style="magenta")
        table.add_column("Status", style="yellow")

        for dep in deps_data.get("dependencies", []):
            table.add_row(
                dep.get("name", ""),
                dep.get("version", ""),
                dep.get("type", "runtime"),
                dep.get("status", "installed"),
            )

        self.console.print(table)

    def render_deps_tree(self, tree_data: dict) -> None:
        if self.json_mode:
            self.json_output(tree_data)
            return

        tree = Tree("[bold cyan]Dependency Tree[/bold cyan]")
        
        def build(node_data: dict, current_tree: Tree):
            for k, v in node_data.items():
                if isinstance(v, dict):
                    branch = current_tree.add(f"[green]{k}[/green]")
                    build(v, branch)
                else:
                    current_tree.add(f"[white]{k}[/white]: {v}")

        build(tree_data, tree)
        self.console.print(panel_wrap(tree))

    def render_graph(self, topology: dict) -> None:
        if self.json_mode:
            self.json_output(topology)
            return

        tree = Tree("[bold cyan]Graph Topology[/bold cyan]")
        nodes = topology.get("nodes", [])
        edges = topology.get("edges", [])

        for node in nodes:
            node_branch = tree.add(f"[bold]{node}[/bold]")
            node_edges = [e for e in edges if e.get("source") == node]
            for edge in node_edges:
                node_branch.add(f"→ {edge.get('target', '')}")

        self.console.print(panel_wrap(tree))

    def render_memory(self, memory_data: dict, *, verbose: bool = False) -> None:
        if self.json_mode:
            self.json_output(memory_data)
            return

        table = Table(title="Project Memory", box=box.MINIMAL)
        table.add_column("Category", style="cyan")
        table.add_column("Items", style="white")

        for key, value in memory_data.items():
            if isinstance(value, list) and not verbose:
                table.add_row(key.capitalize(), f"{len(value)} items")
            else:
                table.add_row(key.capitalize(), str(value))

        self.console.print(table)

    def render_doctor(self, checks: list[dict]) -> None:
        if self.json_mode:
            self.json_output(checks)
            return

        table = Table(title="Doctor Checks", box=box.ROUNDED)
        table.add_column("Check", style="cyan", no_wrap=True)
        table.add_column("Status", justify="center")
        table.add_column("Message", style="white")
        table.add_column("Remediation", style="dim")

        for check in checks:
            status = check.get("status", "").lower()
            if status == "pass":
                badge = "[green]PASS[/green]"
            elif status == "warn":
                badge = "[yellow]WARN[/yellow]"
            elif status == "fail":
                badge = "[red]FAIL[/red]"
            else:
                badge = f"[{status}]"

            table.add_row(
                check.get("name", ""),
                badge,
                check.get("message", ""),
                check.get("remediation", ""),
            )

        self.console.print(table)

    def render_config(self, config_data: dict) -> None:
        if self.json_mode:
            self.json_output(config_data)
            return

        table = Table(title="Configuration", box=box.SIMPLE)
        table.add_column("Key", style="bold cyan")
        table.add_column("Value", style="green")
        table.add_column("Source", style="dim")

        for k, v in config_data.items():
            source = "default" if isinstance(v, dict) and "source" not in v else (v.get("source", "") if isinstance(v, dict) else "")
            val = v.get("value", v) if isinstance(v, dict) else v
            table.add_row(k, str(val), source)

        self.console.print(table)

    def render_threads(self, threads: list[dict]) -> None:
        if self.json_mode:
            self.json_output(threads)
            return

        table = Table(title="Active Threads", box=box.ROUNDED)
        table.add_column("ID", style="cyan")
        table.add_column("Project", style="white")
        table.add_column("Last Activity", style="dim")
        table.add_column("Status", style="magenta")

        for thread in threads:
            table.add_row(
                thread.get("id", ""),
                thread.get("project", ""),
                str(thread.get("last_activity", "")),
                thread.get("status", ""),
            )

        self.console.print(table)

    def render_logs(self, entries: list[dict]) -> None:
        if self.json_mode:
            self.json_output(entries)
            return

        table = Table(title="Log Entries", box=box.SIMPLE)
        table.add_column("Timestamp", style="dim", no_wrap=True)
        table.add_column("Level", style="bold")
        table.add_column("Message")

        level_styles = {
            "INFO": "green",
            "WARN": "yellow",
            "WARNING": "yellow",
            "ERROR": "red",
            "DEBUG": "dim",
        }

        for entry in entries:
            lvl = entry.get("level", "INFO").upper()
            style = level_styles.get(lvl, "white")
            table.add_row(
                entry.get("timestamp", ""),
                f"[{style}]{lvl}[/{style}]",
                entry.get("message", ""),
            )

        self.console.print(table)

    def render_metrics(self, metrics: dict) -> None:
        if self.json_mode:
            self.json_output(metrics)
            return

        table = Table(title="Performance Metrics", box=box.ROUNDED)
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="bold green")

        for k, v in metrics.items():
            table.add_row(k.replace("_", " ").title(), str(v))

        self.console.print(table)

    def render_docs(self, docs_data: dict) -> None:
        if self.json_mode:
            self.json_output(docs_data)
            return
            
        table = Table(title="Documentation Index Status", box=box.SIMPLE)
        table.add_column("Library", style="cyan")
        table.add_column("Chunks", style="magenta")
        
        for lib in docs_data.get("libraries", []):
            table.add_row(lib.get("name", ""), str(lib.get("chunks", 0)))
            
        self.console.print(table)
        self.console.print(f"[dim]Model: {docs_data.get('embedding_model', 'N/A')}[/dim]")

    def render_docs_query(self, results: list[dict]) -> None:
        if self.json_mode:
            self.json_output(results)
            return

        for idx, res in enumerate(results, 1):
            score = res.get("score", 0.0)
            title = f"Result {idx} ({res.get('library', 'unknown')} | score: {score:.3f})"
            content = res.get("content", "")
            
            panel = Panel(Markdown(content), title=title, border_style="cyan")
            self.console.print(panel)

    def render_build_error(self, errors: list[dict]) -> None:
        if self.json_mode:
            self.json_output(errors)
            return

        for err in errors:
            loc = f"{err.get('file', '')}:{err.get('line', '')}"
            msg = err.get('message', '')
            etype = err.get('type', 'Error')
            
            text = Text()
            text.append(f"[{etype}] ", style="bold red")
            text.append(f"at {loc}\n", style="dim")
            text.append(msg)
            
            self.console.print(Panel(text, border_style="red"))

    def render_repair_attempt(self, attempt: int, max_attempts: int, result: dict) -> None:
        if self.json_mode:
            self.json_output({"attempt": attempt, "max_attempts": max_attempts, "result": result})
            return

        success = result.get("success", False)
        color = "green" if success else "yellow"
        msg = f"Repair attempt {attempt}/{max_attempts}: {'Succeeded' if success else 'Failed'}"
        
        self.console.print(Text(msg, style=f"bold {color}"))

    def clarification_prompt(self, questions: list[dict]) -> None:
        if self.json_mode:
            self.json_output(questions)
            return

        for q in questions:
            self.console.print(f"[bold magenta]? {q.get('question', '')}[/bold magenta]")

    def error(self, message: str, *, run_id: str = '', thread_id: str = '', resumable: bool = False) -> None:
        if self.json_mode:
            self.json_output({"error": message, "run_id": run_id, "thread_id": thread_id, "resumable": resumable})
            return

        text = Text("ERROR: ", style="bold red")
        text.append(message)
        if run_id:
            text.append(f"\nRun ID: {run_id}", style="dim")
        if thread_id:
            text.append(f"\nThread ID: {thread_id}", style="dim")
            
        self.console.print(Panel(text, border_style="red"))

    def warning(self, message: str) -> None:
        if self.json_mode:
            self.json_output({"warning": message})
            return
        self.console.print(f"[bold yellow]WARNING:[/] {message}")

    def success(self, message: str) -> None:
        if self.json_mode:
            self.json_output({"success": message})
            return
        self.console.print(f"[bold green]SUCCESS:[/] {message}")

    def info(self, message: str) -> None:
        if self.json_mode:
            self.json_output({"info": message})
            return
        self.console.print(f"[bold cyan]INFO:[/] {message}")

    def json_output(self, data: Any) -> None:
        print(json.dumps(data, indent=2))

    def generation_summary(self, report: dict) -> None:
        if self.json_mode:
            self.json_output(report)
            return

        created = report.get("files_created", [])
        failures = report.get("failures", [])
        
        table = Table(title="Generation Summary", box=box.SIMPLE)
        table.add_column("Statistic", style="cyan")
        table.add_column("Count", style="white")
        
        table.add_row("Files Created", str(len(created)))
        table.add_row("Failures", str(len(failures)))
        
        self.console.print(Panel(table, border_style="blue"))

    def render_summary_help(self, registry: Any) -> None:
        """Render concise summary help showing most common commands and guidance."""
        if self.json_mode:
            self.json_output([cmd.name for cmd in registry.get_common_commands()])
            return

        table = Table(box=box.SIMPLE, show_header=True, padding=(0, 2))
        table.add_column("Command", style="bold cyan")
        table.add_column("Description", style="white")
        table.add_column("Category", style="dim magenta")

        for cmd in registry.get_common_commands():
            alias_str = f" ({', '.join(cmd.aliases)})" if cmd.aliases else ""
            table.add_row(f"{cmd.primary_name}{alias_str}", cmd.short_description, cmd.category.value)

        guidance = (
            "[bold white]Tips & Navigation:[/bold white]\n"
            "  • Type [bold cyan]/[/bold cyan] at prompt to open the searchable command palette.\n"
            "  • Type [bold cyan]/commands[/bold cyan] to see all commands categorized.\n"
            "  • Type [bold cyan]/help <command>[/bold cyan] (e.g. [bold]/help model[/bold]) for detailed usage.\n"
            "  • Enter natural-language text directly to build or refine your application."
        )

        content = Group(
            table,
            Panel(guidance, border_style="cyan", padding=(0, 1)),
        )
        self.console.print(Panel(content, title="[bold blue]Frontend Agent · Quick Help[/bold blue]", border_style="blue"))

    def render_command_detail_help(self, spec: Any) -> None:
        """Render detailed usage help for a single command."""
        if self.json_mode:
            self.json_output({
                "name": spec.name,
                "aliases": spec.aliases,
                "category": spec.category.value,
                "short_description": spec.short_description,
                "long_description": spec.long_description,
                "usage": spec.usage,
                "examples": spec.examples,
                "subcommands": spec.subcommands,
            })
            return

        grid = Table.grid(padding=(0, 1))
        grid.add_column("Key", style="bold cyan")
        grid.add_column("Value")

        grid.add_row("Command:", f"[bold white]{spec.primary_name}[/bold white]")
        if spec.aliases:
            grid.add_row("Aliases:", ", ".join(spec.aliases))
        grid.add_row("Category:", spec.category.value)
        grid.add_row("Usage:", f"[yellow]{spec.usage}[/yellow]")
        grid.add_row("Description:", spec.long_description or spec.short_description)

        if spec.subcommands:
            sub_table = Table(box=box.SIMPLE_HEAD, show_header=True, padding=(0, 1))
            sub_table.add_column("Subcommand", style="bold cyan")
            sub_table.add_column("Purpose")
            for sub, desc in spec.subcommands.items():
                sub_table.add_row(sub, desc)
            grid.add_row("Subcommands:", sub_table)

        if spec.examples:
            ex_text = "\n".join(f"  [dim]•[/dim] [green]{ex}[/green]" for ex in spec.examples)
            grid.add_row("Examples:", ex_text)

        self.console.print(Panel(grid, title=f"[bold blue]Help: {spec.primary_name}[/bold blue]", border_style="blue"))

    def render_commands_categorized(self, registry: Any) -> None:
        """Render all commands organized by category."""
        if self.json_mode:
            self.json_output({cat.value: [c.name for c in cmds] for cat, cmds in registry.by_category().items()})
            return

        by_cat = registry.by_category()
        tables = []
        for cat, cmds in by_cat.items():
            if not cmds:
                continue
            table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
            table.add_column("Command", style="bold cyan", width=22)
            table.add_column("Description", style="white")
            for cmd in cmds:
                alias_str = f" [dim]({', '.join(cmd.aliases)})[/dim]" if cmd.aliases else ""
                table.add_row(f"{cmd.primary_name}{alias_str}", cmd.short_description)
            tables.append(Panel(table, title=f"[bold magenta]{cat.value}[/bold magenta]", border_style="dim"))

        self.console.print(Panel(
            Group(*tables),
            title="[bold blue]All Commands Reference[/bold blue]",
            subtitle="[dim]Use /help <command> for detailed options[/dim]",
            border_style="blue",
        ))

    def render_unknown_command(self, unknown_cmd: str, suggestions: list[str]) -> None:
        """Render helpful error for an unknown command with fuzzy matches."""
        if self.json_mode:
            self.json_output({"error": f"Unknown command: {unknown_cmd}", "suggestions": suggestions})
            return

        text = f"[bold red]Unknown command:[/] [white]{unknown_cmd}[/white]\n"
        if suggestions:
            sug_str = ", ".join(f"[bold cyan]{s}[/bold cyan]" for s in suggestions)
            text += f"Did you mean: {sug_str}?\n\n"
        text += "[dim]Type '/' to search the command palette, or '/help' for guidance.[/dim]"
        self.console.print(Panel(text, border_style="red", padding=(0, 1)))

    def render_command_suggestions(self, suggestions: list[str]) -> None:
        """Render contextual next-action command suggestions."""
        if not suggestions or self.json_mode or self.quiet:
            return
        sug_str = "   ".join(f"[bold cyan]{s}[/bold cyan]" for s in suggestions)
        self.console.print(f"\n[dim]Suggested next actions:[/] {sug_str}\n")

    def render_model_selector(self, discovered: list[Any], current_routing: dict[str, str], profile: str) -> None:
        """Render model selector and role routing tables."""
        if self.json_mode:
            self.json_output({"discovered": [m.__dict__ for m in discovered], "routing": current_routing, "profile": profile})
            return

        # Table 1: Discovered Models
        m_table = Table(title="Available Local Models", box=box.SIMPLE, show_header=True, padding=(0, 2))
        m_table.add_column("Model Name", style="bold cyan")
        m_table.add_column("Size", style="green")
        m_table.add_column("Status", style="yellow")
        m_table.add_column("Assigned Roles", style="magenta")

        for m in discovered:
            roles_str = ", ".join(m.assigned_roles) if m.assigned_roles else "—"
            m_table.add_row(m.name, f"{m.size_gb}GB", m.status, roles_str)

        # Table 2: Role Routing
        r_table = Table(title=f"Active Role Routing (Profile: [bold cyan]{profile}[/bold cyan])", box=box.SIMPLE, show_header=True, padding=(0, 2))
        r_table.add_column("Pipeline Role", style="bold white")
        r_table.add_column("Assigned Model", style="bold cyan")

        for role, mname in current_routing.items():
            r_table.add_row(role.capitalize(), mname)

        instructions = (
            "[dim]Commands to change model configuration:[/dim]\n"
            "  • [bold cyan]/model set <model>[/bold cyan]             — Set global default model\n"
            "  • [bold cyan]/model profile <fast|balanced|quality>[/bold cyan] — Apply preset profile\n"
            "  • [bold cyan]/model <role> <model>[/bold cyan]          — Route role (planner, architect, component, reviewer, repair)\n"
            "  • [bold cyan]/model reset[/bold cyan]                   — Reset to default routing"
        )

        content = Group(m_table, r_table, Panel(instructions, border_style="cyan", padding=(0, 1)))
        self.console.print(Panel(content, title="[bold blue]Model Management & Routing[/bold blue]", border_style="blue"))

    def render_deliberation_selector(self, current_mode: str) -> None:
        """Render deliberation mode configuration."""
        if self.json_mode:
            self.json_output({"deliberation_mode": current_mode})
            return

        modes = [
            ("OFF", "Single generation pass without critique or revision loops."),
            ("LIGHT", "Draft → Critique → Revision (single iteration for moderate tasks)."),
            ("DEEP", "Multi-candidate sampling → Evaluation → Best candidate selection."),
            ("AUTO", "System dynamically determines deliberation depth based on task complexity."),
        ]

        table = Table(box=box.SIMPLE, show_header=True, padding=(0, 2))
        table.add_column("Mode", style="bold cyan")
        table.add_column("Description", style="white")
        table.add_column("Active", style="bold green")

        for mode, desc in modes:
            is_active = "● ACTIVE" if mode.upper() == current_mode.upper() else ""
            table.add_row(mode, desc, is_active)

        instructions = (
            f"[bold]Current Mode:[/] [yellow]{current_mode}[/yellow]\n\n"
            "Change deliberation mode using: [bold cyan]/deliberation <OFF|LIGHT|DEEP|AUTO>[/bold cyan]"
        )

        content = Group(table, Panel(instructions, border_style="cyan", padding=(0, 1)))
        self.console.print(Panel(content, title="[bold blue]Deliberation Mode Configuration[/bold blue]", border_style="blue"))

    def render_why(self, why_data: dict[str, Any]) -> None:
        """Render observable explanation of recent system decisions."""
        if self.json_mode:
            self.json_output(why_data)
            return

        table = Table(box=box.SIMPLE, show_header=True, padding=(0, 2))
        table.add_column("Decision Area", style="bold cyan", width=22)
        table.add_column("Observable System Rationale", style="white")

        for area, explanation in why_data.items():
            table.add_row(area, str(explanation))

        self.console.print(Panel(
            table,
            title="[bold blue]Recent System Decisions & Rationale (/why)[/bold blue]",
            subtitle="[dim]Derived from structured state and telemetry logs[/dim]",
            border_style="blue",
        ))



def panel_wrap(renderable: RenderableType, title: str = "") -> Panel:
    return Panel(renderable, title=title, border_style="blue", expand=False)


def create_event_handler(renderer: Renderer) -> Callable[[Event], None]:
    """Returns an event handler function suitable for EventBus subscriptions."""
    def handler(event: Event) -> None:
        node = event.node or event.data.get("node", "")
        if event.type == EventType.NODE_STARTED:
            renderer.update_node(node, "running")
        elif event.type == EventType.NODE_COMPLETED:
            renderer.update_node(node, "complete")
        elif event.type == EventType.NODE_FAILED:
            renderer.update_node(node, "failed")
        elif event.type == EventType.FILE_GENERATION_STARTED:
            renderer.update_file(node, event.data.get("path", ""), "running")
        elif event.type == EventType.FILE_GENERATED:
            renderer.update_file(node, event.data.get("path", ""), "complete")
        elif event.type == EventType.FILE_GENERATION_FAILED:
            renderer.update_file(node, event.data.get("path", ""), "failed")
        elif event.type == EventType.FILE_STYLED:
            renderer.update_file(node, event.data.get("path", ""), "complete")
        elif event.type == EventType.FILE_REVIEWED:
            renderer.update_file(node, event.data.get("path", ""), "complete")

    return handler

