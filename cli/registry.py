from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Sequence


class CommandCategory(str, Enum):
    PROJECT = "PROJECT"
    GENERATION = "GENERATION"
    CODE = "CODE"
    RUN = "RUN"
    AGENT = "AGENT"
    KNOWLEDGE = "KNOWLEDGE"
    DEBUG = "DEBUG"
    SYSTEM = "SYSTEM"


@dataclass
class CommandSpec:
    """Metadata schema for a CLI slash command."""

    name: str
    aliases: list[str] = field(default_factory=list)
    category: CommandCategory = CommandCategory.SYSTEM
    short_description: str = ""
    long_description: str = ""
    usage: str = ""
    examples: list[str] = field(default_factory=list)
    subcommands: dict[str, str] = field(default_factory=dict)
    is_common: bool = False
    availability_predicate: Callable[[Any], bool] | None = None

    @property
    def primary_name(self) -> str:
        return self.name if self.name.startswith("/") else f"/{self.name}"


class CommandRegistry:
    """Central registry of all CLI slash commands and metadata."""

    def __init__(self) -> None:
        self._commands: dict[str, CommandSpec] = {}
        self._alias_map: dict[str, str] = {}
        self._register_default_commands()

    def register(self, spec: CommandSpec) -> None:
        name = spec.primary_name
        self._commands[name] = spec
        for alias in spec.aliases:
            alias_name = alias if alias.startswith("/") else f"/{alias}"
            self._alias_map[alias_name] = name

    def get(self, name: str) -> CommandSpec | None:
        clean = name if name.startswith("/") else f"/{name}"
        canonical = self._alias_map.get(clean, clean)
        return self._commands.get(canonical)

    def resolve_alias(self, name: str) -> str:
        clean = name if name.startswith("/") else f"/{name}"
        return self._alias_map.get(clean, clean)

    def all_commands(self) -> list[CommandSpec]:
        return list(self._commands.values())

    def by_category(self) -> dict[CommandCategory, list[CommandSpec]]:
        categorized: dict[CommandCategory, list[CommandSpec]] = {
            cat: [] for cat in CommandCategory
        }
        for cmd in self._commands.values():
            categorized[cmd.category].append(cmd)
        return categorized

    def get_common_commands(self) -> list[CommandSpec]:
        return [cmd for cmd in self._commands.values() if cmd.is_common]

    def find_matching(self, prefix: str) -> list[CommandSpec]:
        """Find commands matching prefix or search text."""
        clean = prefix.lower().lstrip("/")
        matches = []
        for cmd in self._commands.values():
            cmd_name_clean = cmd.primary_name.lstrip("/").lower()
            if cmd_name_clean.startswith(clean) or any(a.lstrip("/").lower().startswith(clean) for a in cmd.aliases):
                matches.append(cmd)
        return matches

    def suggest_similar(self, unknown_cmd: str, limit: int = 3) -> list[str]:
        """Suggest close command names using fuzzy matching."""
        clean = unknown_cmd if unknown_cmd.startswith("/") else f"/{unknown_cmd}"
        all_names = list(self._commands.keys()) + list(self._alias_map.keys())
        matches = difflib.get_close_matches(clean, all_names, n=limit, cutoff=0.5)
        # Deduplicate to canonical names
        return list(dict.fromkeys(self._alias_map.get(m, m) for m in matches))

    def get_suggestions_for_state(self, state: dict[str, Any] | None) -> list[str]:
        """Derive 2-4 contextual next-action command suggestions based on structured state."""
        if not state:
            return ["/new", "/model", "/help"]

        phase = state.get("phase", "")
        errors = state.get("errors", [])
        build_succeeded = state.get("build_succeeded", False)
        plan = state.get("plan")
        blueprint = state.get("blueprint")

        if phase == "failed" or errors:
            return ["/errors", "/repair", "/logs", "/why"]

        if build_succeeded:
            return ["/preview", "/visual", "/diff", "/files"]

        if phase == "generated" or phase == "styled" or phase == "reviewed":
            return ["/build", "/files", "/diff", "/preview"]

        if blueprint and not phase:
            return ["/architecture", "/build", "/files"]

        if plan:
            return ["/plan", "/architecture", "/status"]

        return ["/status", "/model", "/help"]

    def _register_default_commands(self) -> None:
        # PROJECT
        self.register(CommandSpec(
            name="/status",
            aliases=["/s"],
            category=CommandCategory.PROJECT,
            short_description="Show current project and agent state",
            long_description="Displays active project, active thread, model profile, current node, dependency state, sandbox state, and build health.",
            usage="/status",
            examples=["/status", "/s"],
            is_common=True,
        ))
        self.register(CommandSpec(
            name="/new",
            category=CommandCategory.PROJECT,
            short_description="Create a new frontend generation project",
            long_description="Resets the active thread and prepares a fresh project workspace for a new natural-language build request.",
            usage="/new",
            examples=["/new"],
            is_common=True,
        ))
        self.register(CommandSpec(
            name="/resume",
            aliases=["/r"],
            category=CommandCategory.PROJECT,
            short_description="Resume interrupted or previous workflow",
            long_description="Resumes execution for the current thread or a specified thread ID from the most recent checkpoint.",
            usage="/resume [thread_id]",
            examples=["/resume", "/resume f8a2c10b"],
            is_common=True,
        ))
        self.register(CommandSpec(
            name="/restart",
            category=CommandCategory.PROJECT,
            short_description="Reset current thread and restart project",
            long_description="Clears the current thread state to allow starting a new project in the workspace.",
            usage="/restart",
            examples=["/restart"],
        ))

        # GENERATION
        self.register(CommandSpec(
            name="/plan",
            category=CommandCategory.GENERATION,
            short_description="Inspect generated application plan",
            long_description="Displays the high-level application plan created by the Planner Agent, including key sections, theme, and requirements.",
            usage="/plan [--raw]",
            examples=["/plan", "/plan --raw"],
            is_common=True,
        ))
        self.register(CommandSpec(
            name="/architecture",
            aliases=["/arch"],
            category=CommandCategory.GENERATION,
            short_description="Inspect UI architecture and routes",
            long_description="Displays the UI component hierarchy, page routing, and design system specifications from the UI Architect.",
            usage="/architecture [--raw]",
            examples=["/architecture", "/arch"],
            is_common=True,
        ))
        self.register(CommandSpec(
            name="/review",
            category=CommandCategory.GENERATION,
            short_description="Run code review pass",
            long_description="Executes the conservative code reviewer pass checking import validity and purpose alignment.",
            usage="/review",
            examples=["/review"],
        ))
        self.register(CommandSpec(
            name="/repair",
            category=CommandCategory.GENERATION,
            short_description="Run targeted repair on build/runtime failures",
            long_description="Analyzes recent build or import failures and invokes the repair agent on affected files.",
            usage="/repair",
            examples=["/repair"],
            is_common=True,
        ))

        # CODE
        self.register(CommandSpec(
            name="/files",
            aliases=["/f"],
            category=CommandCategory.CODE,
            short_description="Browse generated project files",
            long_description="Lists all generated React components, pages, routes, styles, and configs in the workspace.",
            usage="/files",
            examples=["/files", "/f"],
            is_common=True,
        ))
        self.register(CommandSpec(
            name="/show",
            aliases=["/cat"],
            category=CommandCategory.CODE,
            short_description="View source content of a generated file",
            long_description="Displays the formatted syntax-highlighted code for a specified file path.",
            usage="/show <path>",
            examples=["/show src/App.jsx", "/show src/components/Header.jsx"],
            is_common=True,
        ))
        self.register(CommandSpec(
            name="/diff",
            aliases=["/d"],
            category=CommandCategory.CODE,
            short_description="Show changes from generation, styling, or repair",
            long_description="Displays unified diffs comparing current code against backups created before styling or repairs.",
            usage="/diff",
            examples=["/diff"],
            is_common=True,
        ))

        # RUN
        self.register(CommandSpec(
            name="/build",
            aliases=["/b"],
            category=CommandCategory.RUN,
            short_description="Build current project inside sandbox",
            long_description="Executes `npm run build` inside the isolated sandbox BUILD container.",
            usage="/build",
            examples=["/build", "/b"],
            is_common=True,
        ))
        self.register(CommandSpec(
            name="/preview",
            category=CommandCategory.RUN,
            short_description="Launch sandboxed preview server",
            long_description="Starts the Vite preview/dev server inside the PREVIEW sandbox profile on port 5173.",
            usage="/preview [--port 5173]",
            examples=["/preview", "/preview --port 3000"],
            is_common=True,
        ))
        self.register(CommandSpec(
            name="/visual",
            category=CommandCategory.RUN,
            short_description="Run browser visual & functional evaluation",
            long_description="Evaluates page rendering, console errors, horizontal overflow, and layout across mobile/tablet/desktop viewports.",
            usage="/visual [--route <route>] [--mobile]",
            examples=["/visual", "/visual --route /about", "/visual --mobile"],
            is_common=True,
        ))
        self.register(CommandSpec(
            name="/screenshots",
            category=CommandCategory.RUN,
            short_description="List captured browser screenshots",
            long_description="Lists screenshots captured across routes and viewports during visual evaluation.",
            usage="/screenshots",
            examples=["/screenshots"],
        ))

        # AGENT
        self.register(CommandSpec(
            name="/model",
            aliases=["/m"],
            category=CommandCategory.AGENT,
            short_description="Configure models, role routing, and profiles",
            long_description="Interactive model selector and role-based model router. Allows setting global models, role models, or presets (fast, balanced, quality).",
            usage="/model [list | set <model> | profile <fast|balanced|quality> | <role> <model> | reset]",
            examples=[
                "/model",
                "/model list",
                "/model set qwen3:latest",
                "/model profile balanced",
                "/model component qwen3-coder:14b",
                "/model reset",
            ],
            subcommands={
                "list": "List discovered local models",
                "set": "Set global default model: /model set <model>",
                "profile": "Apply model preset profile: /model profile <fast|balanced|quality>",
                "planner": "Set Planner model: /model planner <model>",
                "architect": "Set Architect model: /model architect <model>",
                "component": "Set Component model: /model component <model>",
                "reviewer": "Set Reviewer model: /model reviewer <model>",
                "repair": "Set Repair model: /model repair <model>",
                "reset": "Reset all role routing to default",
            },
            is_common=True,
        ))
        self.register(CommandSpec(
            name="/deliberation",
            aliases=["/delib"],
            category=CommandCategory.AGENT,
            short_description="Configure deliberation mode (OFF/LIGHT/DEEP/AUTO)",
            long_description="Controls test-time deliberation depth. OFF (single-pass), LIGHT (draft/critique), DEEP (multi-candidate evaluation), AUTO (system decides).",
            usage="/deliberation [OFF | LIGHT | DEEP | AUTO]",
            examples=["/deliberation", "/deliberation AUTO", "/deliberation DEEP"],
            is_common=True,
        ))
        self.register(CommandSpec(
            name="/graph",
            category=CommandCategory.AGENT,
            short_description="Show LangGraph multi-agent topology",
            long_description="Renders the execution graph structure and node transitions.",
            usage="/graph",
            examples=["/graph"],
        ))
        self.register(CommandSpec(
            name="/memory",
            category=CommandCategory.AGENT,
            short_description="Inspect long-term project memory",
            long_description="Displays durable user preferences, project decisions, and repair memories.",
            usage="/memory [--verbose]",
            examples=["/memory", "/memory --verbose"],
        ))
        self.register(CommandSpec(
            name="/context",
            category=CommandCategory.AGENT,
            short_description="Show compiled prompt context statistics",
            long_description="Displays context compilation reductions and prompt token footprint metrics.",
            usage="/context",
            examples=["/context"],
        ))

        # KNOWLEDGE
        self.register(CommandSpec(
            name="/docs",
            category=CommandCategory.KNOWLEDGE,
            short_description="Documentation RAG index (status, query)",
            long_description="Inspect documentation vector index status or query documentation reference snippets.",
            usage="/docs [status | query <query>]",
            examples=["/docs status", "/docs query react router useLoaderData"],
            subcommands={
                "status": "Show RAG collection chunk counts and models",
                "query": "Search documentation: /docs query <query>",
            },
        ))
        self.register(CommandSpec(
            name="/deps",
            category=CommandCategory.KNOWLEDGE,
            short_description="Dependency management (tree, why)",
            long_description="Displays resolved npm dependencies, lockfile status, or explains why a package is needed.",
            usage="/deps [tree | why <package>]",
            examples=["/deps", "/deps tree", "/deps why lucide-react"],
            subcommands={
                "tree": "Display dependency tree",
                "why": "Explain why a package is needed: /deps why <pkg>",
            },
        ))

        # DEBUG
        self.register(CommandSpec(
            name="/logs",
            category=CommandCategory.DEBUG,
            short_description="Show agent execution logs",
            long_description="Displays recent execution logs with optional node filtering or error filtering.",
            usage="/logs [node_name] [--errors]",
            examples=["/logs", "/logs component_agent", "/logs --errors"],
        ))
        self.register(CommandSpec(
            name="/errors",
            category=CommandCategory.DEBUG,
            short_description="Inspect active errors and failure diagnostics",
            long_description="Displays structured error details, compacted build frames, and import issues.",
            usage="/errors",
            examples=["/errors"],
            is_common=True,
        ))
        self.register(CommandSpec(
            name="/stats",
            category=CommandCategory.DEBUG,
            short_description="Show performance, latency, and cache telemetry",
            long_description="Displays run metrics including LLM calls, cache hits/misses, prompt reduction, and build timings.",
            usage="/stats",
            examples=["/stats"],
        ))
        self.register(CommandSpec(
            name="/doctor",
            category=CommandCategory.DEBUG,
            short_description="Run system health & capability checks",
            long_description="Verifies Python version, Ollama connection, Node.js/npm, sandbox engine runtime, and permissions.",
            usage="/doctor",
            examples=["/doctor"],
            is_common=True,
        ))
        self.register(CommandSpec(
            name="/why",
            category=CommandCategory.DEBUG,
            short_description="Explain recent system & architectural decisions",
            long_description="Explains why specific models, dependencies, deliberation modes, or repairs were chosen based on observable state.",
            usage="/why",
            examples=["/why"],
            is_common=True,
        ))

        # SYSTEM
        self.register(CommandSpec(
            name="/config",
            category=CommandCategory.SYSTEM,
            short_description="Show active runtime configuration",
            long_description="Displays current CLI and backend configuration settings.",
            usage="/config",
            examples=["/config"],
        ))
        self.register(CommandSpec(
            name="/commands",
            category=CommandCategory.SYSTEM,
            short_description="List all available slash commands by category",
            long_description="Displays the complete categorized command reference.",
            usage="/commands",
            examples=["/commands"],
            is_common=True,
        ))
        self.register(CommandSpec(
            name="/help",
            aliases=["/h", "/?"],
            category=CommandCategory.SYSTEM,
            short_description="Show guidance and command help",
            long_description="Displays helpful common commands or detailed usage for a specific command.",
            usage="/help [command_name]",
            examples=["/help", "/help model", "/h status"],
            is_common=True,
        ))
        self.register(CommandSpec(
            name="/exit",
            aliases=["/quit", "/q"],
            category=CommandCategory.SYSTEM,
            short_description="Exit interactive session",
            long_description="Terminates the interactive session and closes database handles.",
            usage="/exit",
            examples=["/exit", "/quit"],
        ))


_registry_instance: CommandRegistry | None = None


def get_command_registry() -> CommandRegistry:
    global _registry_instance
    if _registry_instance is None:
        _registry_instance = CommandRegistry()
    return _registry_instance
