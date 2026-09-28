from __future__ import annotations

from typing import Iterable

from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document

from cli.model_selector import discover_models, MODEL_ROLES, ModelProfile
from cli.registry import get_command_registry


class SlashCommandCompleter(Completer):
    """Contextual autocomplete for slash commands, aliases, subcommands, and dynamic arguments."""

    def __init__(
        self,
        *,
        project_files: Iterable[str] = (),
        thread_ids: Iterable[str] = (),
        package_names: Iterable[str] = (),
    ) -> None:
        self._project_files: list[str] = list(project_files)
        self._thread_ids: list[str] = list(thread_ids)
        self._package_names: list[str] = list(package_names)
        self._registry = get_command_registry()

    def update_project_files(self, files: Iterable[str]) -> None:
        self._project_files = list(files)

    def update_thread_ids(self, ids: Iterable[str]) -> None:
        self._thread_ids = list(ids)

    def update_package_names(self, names: Iterable[str]) -> None:
        self._package_names = list(names)

    def get_completions(
        self, document: Document, complete_event: object
    ) -> Iterable[Completion]:
        text = document.text_before_cursor.lstrip()

        # Not a slash command — no completions
        if not text.startswith("/"):
            return

        parts = text.split(None, 1)
        cmd_raw = parts[0]
        arg = parts[1] if len(parts) > 1 else ""

        # Complete the command itself
        if len(parts) == 1 and not text.endswith(" "):
            # Match primary commands
            for cmd in self._registry.all_commands():
                if cmd.primary_name.startswith(cmd_raw):
                    yield Completion(
                        cmd.primary_name,
                        start_position=-len(cmd_raw),
                        display_meta=cmd.short_description,
                    )
            # Match aliases
            for alias, target in self._registry._alias_map.items():
                if alias.startswith(cmd_raw):
                    spec = self._registry.get(target)
                    desc = f"Alias for {target}" + (f": {spec.short_description}" if spec else "")
                    yield Completion(
                        alias,
                        start_position=-len(cmd_raw),
                        display_meta=desc,
                    )
            return

        # Canonical command name
        canonical = self._registry.resolve_alias(cmd_raw)

        # Complete arguments based on command
        if canonical in ("/show", "/cat", "/diff"):
            yield from self._complete_files(arg)
        elif canonical == "/resume":
            yield from self._complete_threads(arg)
        elif canonical == "/deps":
            yield from self._complete_deps_subcmds(arg)
        elif canonical == "/docs":
            yield from self._complete_docs_subcmds(arg)
        elif canonical == "/model":
            yield from self._complete_model_subcmds(arg)
        elif canonical == "/deliberation":
            yield from self._complete_deliberation_modes(arg)
        elif canonical == "/logs":
            yield from self._complete_log_nodes(arg)
        elif canonical in ("/plan", "/architecture"):
            if not arg:
                yield Completion("--raw", display_meta="Show raw JSON output")

    def _complete_files(self, prefix: str) -> Iterable[Completion]:
        for path in self._project_files:
            if path.startswith(prefix) or not prefix:
                yield Completion(
                    path,
                    start_position=-len(prefix),
                    display_meta="file",
                )

    def _complete_threads(self, prefix: str) -> Iterable[Completion]:
        for tid in self._thread_ids:
            if tid.startswith(prefix) or not prefix:
                yield Completion(
                    tid,
                    start_position=-len(prefix),
                    display_meta="thread",
                )

    def _complete_deps_subcmds(self, prefix: str) -> Iterable[Completion]:
        subcmds = {"tree": "Show dependency tree", "why": "Explain why a package is needed"}
        parts = prefix.split(None, 1)
        sub = parts[0] if parts else ""

        if not prefix or (len(parts) == 1 and not prefix.endswith(" ")):
            for name, desc in subcmds.items():
                if name.startswith(sub):
                    yield Completion(name, start_position=-len(sub), display_meta=desc)
        elif sub == "why":
            pkg_prefix = parts[1] if len(parts) > 1 else ""
            for pkg in self._package_names:
                if pkg.startswith(pkg_prefix) or not pkg_prefix:
                    yield Completion(pkg, start_position=-len(pkg_prefix), display_meta="package")

    def _complete_docs_subcmds(self, prefix: str) -> Iterable[Completion]:
        subcmds = {"status": "Index status", "query": "Search documents", "sync": "Ingest documents"}
        for name, desc in subcmds.items():
            if name.startswith(prefix) or not prefix:
                yield Completion(name, start_position=-len(prefix), display_meta=desc)

    def _complete_model_subcmds(self, prefix: str) -> Iterable[Completion]:
        parts = prefix.split(None, 1)
        sub = parts[0] if parts else ""

        subcmds = {
            "list": "List discovered local models",
            "set": "Set global default model",
            "profile": "Apply model preset profile (fast, balanced, quality)",
            "reset": "Reset role routing to default",
        }
        for r in MODEL_ROLES:
            subcmds[r] = f"Set model for {r} role"

        if not prefix or (len(parts) == 1 and not prefix.endswith(" ")):
            for name, desc in subcmds.items():
                if name.startswith(sub):
                    yield Completion(name, start_position=-len(sub), display_meta=desc)
        elif sub == "profile":
            prof_prefix = parts[1] if len(parts) > 1 else ""
            for p in ["fast", "balanced", "quality"]:
                if p.startswith(prof_prefix) or not prof_prefix:
                    yield Completion(p, start_position=-len(prof_prefix), display_meta=f"{p.upper()} profile")
        elif sub in ("set", *MODEL_ROLES):
            m_prefix = parts[1] if len(parts) > 1 else ""
            try:
                models = discover_models()
                for m in models:
                    if m.name.startswith(m_prefix) or not m_prefix:
                        yield Completion(m.name, start_position=-len(m_prefix), display_meta=f"{m.size_gb}GB")
            except Exception:
                pass

    def _complete_deliberation_modes(self, prefix: str) -> Iterable[Completion]:
        modes = {
            "OFF": "Single generation pass",
            "LIGHT": "Draft → Critique → Revision",
            "DEEP": "Multiple candidates → Evaluation",
            "AUTO": "System selects depth based on complexity",
        }
        for mode, desc in modes.items():
            if mode.lower().startswith(prefix.lower()) or not prefix:
                yield Completion(mode, start_position=-len(prefix), display_meta=desc)

    def _complete_log_nodes(self, prefix: str) -> Iterable[Completion]:
        nodes = [
            "clarify_analyze", "planner", "architect", "resolve_packages",
            "generate", "style", "review", "packages", "repair",
            "--errors",
        ]
        for node in nodes:
            if node.startswith(prefix) or not prefix:
                yield Completion(node, start_position=-len(prefix))
