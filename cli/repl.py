from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory

from cli.completion import SlashCommandCompleter
from cli.context import SessionContext
from cli.events import EventType
from cli.model_selector import get_current_profile, get_role_routing
from cli.registry import get_command_registry
from cli.status import render_full_status
from config import DELIBERATION_CONFIG, MODEL_NAME
from sandbox import get_sandbox_manager


def _parse_slash(line: str) -> tuple[str, list[str]]:
    """Parse a slash command into (command, args).

    Returns ('', []) if not a slash command.
    """
    stripped = line.strip()
    if not stripped.startswith("/"):
        return "", []
    try:
        parts = shlex.split(stripped)
    except ValueError:
        parts = stripped.split()
    return parts[0].lower(), parts[1:]


def _history_path() -> Path:
    """Return path to persistent history file."""
    try:
        config_dir = Path.home() / ".config" / "frontend-agent"
        config_dir.mkdir(parents=True, exist_ok=True)
        return config_dir / "history"
    except Exception:
        fallback_dir = Path(".frontend_agent")
        fallback_dir.mkdir(parents=True, exist_ok=True)
        return fallback_dir / "history"


class InteractiveREPL:
    """Modern interactive terminal REPL for Frontend Agent.

    Features:
    - Persistent interactive prompt & dynamic bottom status bar
    - Discoverable slash command palette with autocompletion and categories
    - Dynamic model selector and role-based routing
    - Deliberation depth selector
    - State-aware contextual next-action suggestions
    - Full system status and decision rationale (/why)
    """

    def __init__(
        self,
        ctx: SessionContext,
        renderer: Any,
        commands_module: Any,
    ) -> None:
        self.ctx = ctx
        self.renderer = renderer
        self.commands = commands_module
        self._registry = get_command_registry()
        self._completer = SlashCommandCompleter()
        self._session: PromptSession | None = None
        self._running = False
        self._first_launch = True

    def _bottom_toolbar(self) -> HTML:
        """Render a clean, native status bar below the interactive prompt."""
        try:
            cfg = getattr(self.ctx, "config", None)
            model_name = getattr(cfg, "model", None) or MODEL_NAME
            delib_config = getattr(cfg, "deliberation_config", None) or DELIBERATION_CONFIG
            delib_mode = os.getenv("FRONTEND_AGENT_DELIBERATION", delib_config)
            project_name = getattr(self.ctx, "project_id", None) or "default"
            try:
                profile = get_current_profile().value
            except Exception:
                profile = "BALANCED"

            # Context reduction or telemetry state
            ctx_str = "ctx —"
            if hasattr(self.ctx, "metrics") and self.ctx.metrics:
                try:
                    metrics = self.ctx.metrics.current
                    if metrics:
                        reduction = metrics.summary().get("prompt_context_reduction_percent", 0.0)
                        if reduction > 0:
                            ctx_str = f"ctx -{reduction:.0f}%"
                        elif metrics.compiled_prompt_bytes > 0:
                            ctx_str = "ctx 100%"
                except Exception:
                    pass

            # Sandbox state (Never leak 'mock' unless explicitly launched in mock test mode)
            sbx_str = "sbx unavailable"
            try:
                sbx = get_sandbox_manager().get_capability()
                if sbx.runtime == "mock" and os.getenv("FRONTEND_AGENT_SANDBOX_BACKEND") == "mock":
                    sbx_str = "sbx mock (test)"
                elif sbx.available and sbx.runtime in ("docker", "apple-container", "apple"):
                    sbx_str = f"sbx {sbx.runtime}"
                else:
                    sbx_str = "sbx unavailable"
            except Exception:
                pass

            # Build health indicator
            build_str = "build —"
            state = getattr(self.ctx, "_state", None) or {}
            if state.get("build_succeeded"):
                build_str = "<ansigreen>build ✓</ansigreen>"
            elif state.get("errors"):
                build_str = "<ansired>build ✗</ansired>"

            return HTML(
                f" <b>{model_name}</b> ({profile}) · "
                f"<ansiyellow>{delib_mode}</ansiyellow> · "
                f"<b>{project_name}</b> · "
                f"{ctx_str} · "
                f"{sbx_str} · "
                f"{build_str} "
            )
        except Exception:
            return HTML(" <b>frontend-agent</b> · <ansigreen>ready</ansigreen> ")

    def _make_session(self) -> PromptSession:
        history: Any
        try:
            hpath = _history_path()
            # Test writability before attaching FileHistory
            with open(hpath, "a") as f:
                pass
            history = FileHistory(str(hpath))
        except Exception:
            from prompt_toolkit.history import InMemoryHistory
            history = InMemoryHistory()

        return PromptSession(
            history=history,
            completer=self._completer,
            complete_while_typing=True,
            enable_history_search=True,
            bottom_toolbar=self._bottom_toolbar,
        )

    def _prompt_text(self) -> HTML:

        project = self.ctx.project_id or "frontend-agent"
        return HTML(f"<ansicyan><b>{project}</b></ansicyan> <ansibrightblack>›</ansibrightblack> ")

    def _refresh_completions(self) -> None:
        """Update completer with current project data."""
        state = self.ctx._state
        if state:
            blueprint = state.get("blueprint", {})
            files = [
                item.get("path", "")
                for item in blueprint.get("filesystem", [])
                if isinstance(item, dict) and item.get("kind") == "file"
            ]
            self._completer.update_project_files(files)
            self._completer.update_package_names(blueprint.get("dependencies", []))

        try:
            threads = self.ctx.list_threads()
            self._completer.update_thread_ids([t["thread_id"] for t in threads])
        except Exception:
            pass

    def run(self) -> None:
        """Main REPL loop."""
        self._session = self._make_session()
        self._running = True

        # Show initial banner and discoverability onboarding hint
        self.renderer.banner(self._build_context_info())
        if self._first_launch:
            self.renderer.console.print(
                "[dim]💡 Tip: Type your project request normally, or type '[bold cyan]/[/bold cyan]' to browse commands. Use [bold cyan]/model[/bold cyan] to choose models.[/dim]\n"
            )
            self._first_launch = False

        while self._running:
            try:
                line = self._session.prompt(self._prompt_text())
            except KeyboardInterrupt:
                self.renderer.info("Use /exit or Ctrl+D to exit")
                continue
            except EOFError:
                self.renderer.info("Goodbye")
                break

            line = line.strip()
            if not line:
                continue

            try:
                if line.startswith("/"):
                    self._dispatch_slash(line)
                else:
                    self._handle_natural_language(line)
            except KeyboardInterrupt:
                self.renderer.warning("Interrupted")
            except Exception as exc:
                self.renderer.error(
                    str(exc),
                    run_id=self.ctx.run_id,
                    thread_id=self.ctx.thread_id or "",
                    resumable=self.ctx.thread_id is not None,
                )
                if self.ctx.config.verbosity == "debug":
                    self.renderer.console.print_exception()

    def _build_context_info(self) -> dict[str, Any]:
        """Build context dict for the banner."""
        from config import __version__
        cfg = getattr(self.ctx, "config", None)
        ws_root = getattr(cfg, "workspace_root", ".") if cfg else "."
        return {
            "version": __version__,
            "project_id": getattr(self.ctx, "project_id", None) or "default-project",
            "thread_id": getattr(self.ctx, "thread_id", None) or "none",
            "model": getattr(cfg, "model", MODEL_NAME) if cfg else MODEL_NAME,
            "profile": get_current_profile().value,
            "deliberation": os.getenv("FRONTEND_AGENT_DELIBERATION", getattr(cfg, "deliberation_config", DELIBERATION_CONFIG) if cfg else DELIBERATION_CONFIG),
            "workspace": str(Path(ws_root).resolve()),
        }


    def _dispatch_slash(self, line: str) -> None:
        """Parse and dispatch a slash command with alias resolution and suggestions."""
        cmd_raw, args = _parse_slash(line)
        canonical = self._registry.resolve_alias(cmd_raw)

        if canonical in ("/exit", "/quit"):
            self._running = False
            self.renderer.info("Goodbye")
            return

        if canonical == "/help":
            sub_target = args[0] if args else None
            if sub_target:
                spec = self._registry.get(sub_target)
                if spec:
                    self.renderer.render_command_detail_help(spec)
                else:
                    suggestions = self._registry.suggest_similar(sub_target)
                    self.renderer.render_unknown_command(sub_target, suggestions)
            else:
                self.renderer.render_summary_help(self._registry)

        elif canonical == "/commands":
            self.renderer.render_commands_categorized(self._registry)

        elif canonical == "/status":
            data = self.commands.cmd_status(self.ctx)
            if self.renderer.json_mode:
                self.renderer.json_output(data)
            else:
                render_full_status(data, self.renderer.console)

        elif canonical == "/model":
            subcmd = args[0] if args else None
            arg_val = args[1] if len(args) > 1 else None
            data = self.commands.cmd_model(self.ctx, subcmd=subcmd, arg=arg_val)
            if subcmd in ("set", "profile", "reset") or (subcmd and arg_val):
                if data.get("status") == "success":
                    self.renderer.success(data.get("message", "Model configuration updated"))
            else:
                from cli.model_selector import discover_models, get_current_profile, get_role_routing
                self.renderer.render_model_selector(
                    discover_models(),
                    get_role_routing(),
                    get_current_profile().value,
                )

        elif canonical == "/deliberation":
            mode = args[0] if args else None
            data = self.commands.cmd_deliberation(self.ctx, mode=mode)
            if mode and data.get("status") == "success":
                self.renderer.success(f"Deliberation mode updated to [yellow]{data.get('deliberation_mode')}[/yellow]")
            else:
                self.renderer.render_deliberation_selector(data.get("deliberation_mode", "AUTO"))

        elif canonical == "/why":
            data = self.commands.cmd_why(self.ctx)
            self.renderer.render_why(data)

        elif canonical == "/errors":
            data = self.commands.cmd_errors(self.ctx)
            if self.renderer.json_mode:
                self.renderer.json_output(data)
            else:
                errs = data.get("errors", [])
                if errs:
                    self.renderer.render_build_error(errs)
                else:
                    self.renderer.success("No active errors recorded in state")

        elif canonical == "/context":
            data = self.commands.cmd_context(self.ctx)
            self._output(data, lambda d: self.renderer.info(
                f"Context Compilation: -{d.get('reduction_percent', 0.0):.1f}% reduction "
                f"({d.get('compiled_prompt_bytes', 0)} / {d.get('uncompiled_prompt_bytes', 0)} bytes)"
            ))

        elif canonical == "/plan":
            raw = "--raw" in args
            data = self.commands.cmd_plan(self.ctx, raw=raw)
            if data is None:
                self.renderer.warning("No plan available. Run a workflow first.")
            else:
                self._output(data, lambda d: self.renderer.render_plan(d, raw=raw))

        elif canonical == "/architecture":
            raw = "--raw" in args
            data = self.commands.cmd_architecture(self.ctx, raw=raw)
            if data is None:
                self.renderer.warning("No architecture available. Run a workflow first.")
            else:
                self._output(data, lambda d: self.renderer.render_architecture(d, raw=raw))

        elif canonical == "/files":
            data = self.commands.cmd_files(self.ctx)
            self._output(data, lambda d: self.renderer.render_files(d))

        elif canonical in ("/show", "/cat"):
            if not args:
                self.renderer.warning("Usage: /show <file-path>")
                return
            data = self.commands.cmd_show(self.ctx, args[0])
            self._output(data, lambda d: self.renderer.render_file(
                d["path"], d["content"], language=d.get("language")
            ))

        elif canonical == "/diff":
            data = self.commands.cmd_diff(self.ctx)
            self._output(data, lambda d: self.renderer.render_diff(d))

        elif canonical == "/preview":
            port = int(args[0]) if args and args[0].isdigit() else 5173
            data = self.commands.cmd_preview(self.ctx, port=port)
            self._output(data, lambda d: self.renderer.success(f"Preview running at {d.get('url')}"))

        elif canonical == "/visual":
            route = None
            mobile = "--mobile" in args
            for a in args:
                if a.startswith("/") and a != "--mobile":
                    route = a
            data = self.commands.cmd_visual(self.ctx, route=route, mobile_only=mobile)
            self._output(data, lambda d: self.renderer.success(
                f"Visual eval completed: pass_rate={d.get('render_success_rate', 0.0)*100:.1f}%, "
                f"console_errors={d.get('console_errors_count', 0)}, "
                f"overflow_rate={d.get('overflow_failure_rate', 0.0)*100:.1f}%"
            ))

        elif canonical == "/screenshots":
            data = self.commands.cmd_screenshots(self.ctx)
            self._output(data, lambda d: self.renderer.info(f"Screenshots for {d.get('run_id')}: {d.get('count', 0)} files captured"))

        elif canonical == "/deps":
            subcmd = args[0] if args else None
            package = args[1] if len(args) > 1 else None
            data = self.commands.cmd_deps(self.ctx, subcmd=subcmd, package=package)
            if subcmd == "tree":
                self._output(data, lambda d: self.renderer.render_deps_tree(d))
            else:
                self._output(data, lambda d: self.renderer.render_deps(d))

        elif canonical == "/docs":
            subcmd = args[0] if args else "status"
            if subcmd == "status":
                data = self.commands.cmd_docs_status(self.ctx)
                self._output(data, lambda d: self.renderer.render_docs(d))
            elif subcmd == "query" and len(args) > 1:
                query = " ".join(args[1:])
                data = self.commands.cmd_docs_query(self.ctx, query)
                self._output(data, lambda d: self.renderer.render_docs_query(d))
            else:
                self.renderer.warning("Usage: /docs status | /docs query <query>")

        elif canonical == "/memory":
            verbose = "--verbose" in args
            data = self.commands.cmd_memory(self.ctx, verbose=verbose)
            self._output(data, lambda d: self.renderer.render_memory(d, verbose=verbose))

        elif canonical == "/threads":
            data = self.commands.cmd_threads(self.ctx)
            self._output(data, lambda d: self.renderer.render_threads(d))

        elif canonical == "/resume":
            tid = args[0] if args else self.ctx.thread_id
            if not tid:
                self.renderer.warning("Usage: /resume <thread-id>")
                return
            self.ctx.thread_id = tid
            data = self.commands.cmd_resume(self.ctx)
            self._handle_workflow_result(data)

        elif canonical == "/restart":
            self.renderer.warning("This will start a new workflow. Continue? [y/N]")
            try:
                confirm = self._session.prompt("> ") if self._session else input("> ")
            except (KeyboardInterrupt, EOFError):
                return
            if confirm.strip().lower() not in ("y", "yes"):
                self.renderer.info("Cancelled")
                return
            self.ctx.thread_id = None
            self.renderer.info("Thread reset. Enter a build request to start.")

        elif canonical == "/new":
            import uuid
            self.ctx.thread_id = uuid.uuid4().hex
            self.ctx.project_id = self.ctx.thread_id
            self.renderer.success(f"Initialized new project workspace (thread: {self.ctx.thread_id[:8]})")

        elif canonical == "/build":
            data = self.commands.cmd_build(self.ctx)
            self._output(data, lambda d: self.renderer.render_build_error(
                d.get("errors", [])
            ) if not d.get("ok") else self.renderer.success("Build passed"))

        elif canonical == "/review":
            data = self.commands.cmd_review(self.ctx)
            self._output(data, lambda d: self.renderer.render_status(d))

        elif canonical == "/repair":
            data = self.commands.cmd_repair(self.ctx)
            self._output(data, lambda d: self.renderer.render_repair_attempt(
                d.get("attempt", 0), self.ctx.config.max_repair_attempts, d
            ))

        elif canonical == "/logs":
            node_filter = None
            errors_only = False
            for a in args:
                if a == "--errors":
                    errors_only = True
                else:
                    node_filter = a
            data = self.commands.cmd_logs(self.ctx, node=node_filter, errors_only=errors_only)
            self._output(data, lambda d: self.renderer.render_logs(d))

        elif canonical == "/stats":
            data = self.commands.cmd_stats(self.ctx)
            self._output(data, lambda d: self.renderer.render_metrics(d))

        elif canonical == "/config":
            data = self.commands.cmd_config(self.ctx)
            self._output(data, lambda d: self.renderer.render_config(d))

        elif canonical == "/doctor":
            data = self.commands.cmd_doctor(self.ctx)
            self._output(data, lambda d: self.renderer.render_doctor(d))

        else:
            # Fuzzy match suggestion for unknown command
            suggestions = self._registry.suggest_similar(cmd_raw)
            self.renderer.render_unknown_command(cmd_raw, suggestions)

        # Refresh completions and offer state-aware suggestions
        self._refresh_completions()
        self._offer_suggestions()

    def _offer_suggestions(self) -> None:
        """Offer 2-4 contextual suggestions based on current state."""
        state = self.ctx._state
        suggestions = self._registry.get_suggestions_for_state(state)
        self.renderer.render_command_suggestions(suggestions)

    def _handle_natural_language(self, request: str) -> None:
        """Handle a natural-language build request."""
        import uuid

        if not self.ctx.thread_id:
            self.ctx.thread_id = uuid.uuid4().hex
        if not self.ctx.project_id:
            self.ctx.project_id = self.ctx.thread_id

        self.renderer.info(f"Active Thread: {self.ctx.thread_id}")
        result = self.commands.cmd_run(self.ctx, request)
        self._handle_workflow_result(result)

    def _handle_workflow_result(self, result: dict[str, Any]) -> None:
        """Handle workflow result, including interrupt-based clarifications."""
        while True:
            interrupts = result.get("__interrupt__")
            if not interrupts:
                break

            first = interrupts[0]
            payload = getattr(first, "value", first)

            if isinstance(payload, dict) and payload.get("type") == "clarification":
                answers = self._handle_clarification(payload)
            else:
                self.renderer.info(f"Agent paused: {payload}")
                try:
                    answer = self._session.prompt(
                        HTML("<ansiyellow>› </ansiyellow>")
                    ) if self._session else input("> ")
                    answers = {"freeform": answer.strip()}
                except (KeyboardInterrupt, EOFError):
                    self.renderer.warning("Interrupted — workflow paused. Use /resume to continue.")
                    return

            result = self.commands.cmd_resume_with_answer(self.ctx, answers)

        # Show final status
        if result.get("build_succeeded"):
            self.renderer.success(
                f"Build passed · {result.get('project_root', 'unknown')}"
            )
        elif result.get("phase") == "failed":
            self.renderer.error(
                f"Workflow failed at phase: {result.get('phase', 'unknown')}",
                run_id=self.ctx.run_id,
                thread_id=self.ctx.thread_id or "",
                resumable=True,
            )
        else:
            self.renderer.info(f"Workflow ended: {result.get('phase', 'unknown')}")

        # Update cached state and provide contextual suggestions
        self.ctx._state = result
        self._refresh_completions()
        self._offer_suggestions()

    def _handle_clarification(self, payload: dict) -> dict[str, str]:
        """Render clarification questions and collect answers."""
        questions = payload.get("questions", [])
        self.renderer.clarification_prompt(questions)

        answers: dict[str, str] = {}
        for q in questions:
            if not isinstance(q, dict):
                continue
            key = q.get("key", "answer")
            prompt_text = q.get("prompt", key)
            try:
                answer = self._session.prompt(
                    HTML(f"<ansicyan>? </ansicyan><b>{prompt_text}</b>\n<ansiyellow>› </ansiyellow>")
                ) if self._session else input(f"? {prompt_text}\n> ")
                answers[key] = answer.strip()
            except (KeyboardInterrupt, EOFError):
                self.renderer.warning("Skipped remaining questions")
                break
        return answers

    def _output(self, data: Any, render_fn: Any) -> None:
        """Output data: JSON mode prints raw, otherwise calls render function."""
        if self.renderer.json_mode:
            self.renderer.json_output(data)
        else:
            render_fn(data)
