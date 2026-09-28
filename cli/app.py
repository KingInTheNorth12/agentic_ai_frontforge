from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Annotated, Optional

import typer

from cli.config import CLIConfig, load_config, resolve_color


app = typer.Typer(
    name="frontend-agent",
    help="LangGraph multi-agent frontend builder",
    no_args_is_help=False,
    add_completion=False,
    pretty_exceptions_enable=False,
)

docs_app = typer.Typer(help="Documentation index management")
app.add_typer(docs_app, name="docs")

eval_app = typer.Typer(help="Evaluation harness management")
app.add_typer(eval_app, name="eval")

# ---------------------------------------------------------------------------
# Shared options
# ---------------------------------------------------------------------------

OptThread = Annotated[Optional[str], typer.Option("--thread", "-t", help="LangGraph thread ID")]
OptProject = Annotated[Optional[str], typer.Option("--project", "-p", help="Project memory namespace")]
OptModel = Annotated[Optional[str], typer.Option("--model", "-m", help="Ollama model name")]
OptJSON = Annotated[bool, typer.Option("--json", help="Machine-readable JSON output")]
OptQuiet = Annotated[bool, typer.Option("--quiet", "-q", help="Minimal output")]
OptNoColor = Annotated[bool, typer.Option("--no-color", help="Disable color output")]
OptVerbose = Annotated[bool, typer.Option("--verbose", "-v", help="Verbose output")]
OptDebug = Annotated[bool, typer.Option("--debug", help="Debug output")]


def _build_config(
    *,
    model: str | None = None,
    json_output: bool = False,
    quiet: bool = False,
    no_color: bool = False,
    verbose: bool = False,
    debug: bool = False,
) -> CLIConfig:
    """Build CLI config from command-line flags."""
    verbosity = "debug" if debug else ("verbose" if verbose else "normal")
    color = "never" if no_color else "auto"
    return load_config(
        model=model,
        json_output=json_output,
        quiet=quiet,
        color=color,
        verbosity=verbosity,
    )


def _make_renderer(config: CLIConfig):
    """Create a Renderer instance from config."""
    from cli.renderer import Renderer

    use_color = resolve_color(config, sys.stdout.isatty())
    return Renderer(use_color=use_color, quiet=config.quiet, json_mode=config.json_output)


def _make_context(
    config: CLIConfig,
    thread: str | None = None,
    project: str | None = None,
    renderer: Any = None,
):
    """Create a SessionContext in a single composition root with explicit dependencies."""
    from cli.config import apply_to_env
    from cli.context import SessionContext
    from cli.events import get_bus
    from cli.metrics import MetricsCollector

    apply_to_env(config)
    bus = get_bus()
    metrics = MetricsCollector()
    metrics.attach(bus)

    return SessionContext(
        config=config,
        thread_id=thread,
        project_id=project or thread,
        bus=bus,
        metrics=metrics,
        renderer=renderer,
    )


def _output(renderer, data, render_fn) -> None:
    """JSON mode prints raw, otherwise calls render function."""
    if renderer.json_mode:
        renderer.json_output(data)
    else:
        render_fn(data)


def _version_callback(value: bool):
    if value:
        from config import __version__
        typer.echo(f"frontend-agent {__version__}")
        raise typer.Exit()


# ---------------------------------------------------------------------------
# Default command — interactive REPL
# ---------------------------------------------------------------------------

@app.callback(invoke_without_command=True)
def main_callback(
    ctx: typer.Context,
    version: Annotated[
        Optional[bool],
        typer.Option("--version", "-v", help="Show version and exit.", callback=_version_callback, is_eager=True),
    ] = None,
    thread: OptThread = None,
    project: OptProject = None,
    model: OptModel = None,
    json_output: OptJSON = False,
    quiet: OptQuiet = False,
    no_color: OptNoColor = False,
    verbose: OptVerbose = False,
    debug: OptDebug = False,
) -> None:
    """LangGraph multi-agent frontend builder.

    Run without a subcommand to enter interactive mode.
    """
    if ctx.invoked_subcommand is not None:
        return

    config = _build_config(

        model=model, json_output=json_output, quiet=quiet,
        no_color=no_color, verbose=verbose, debug=debug,
    )
    renderer = _make_renderer(config)
    session = _make_context(config, thread=thread, project=project, renderer=renderer)


    try:
        from cli import commands
        from cli.repl import InteractiveREPL

        repl = InteractiveREPL(session, renderer, commands)
        repl.run()
    except KeyboardInterrupt:
        renderer.info("Goodbye")
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

@app.command()
def run(
    request: Annotated[str, typer.Argument(help="What to build")],
    thread: OptThread = None,
    project: OptProject = None,
    model: OptModel = None,
    json_output: OptJSON = False,
    quiet: OptQuiet = False,
    no_color: OptNoColor = False,
    verbose: OptVerbose = False,
    debug: OptDebug = False,
) -> None:
    """Run a full pipeline non-interactively."""
    config = _build_config(
        model=model, json_output=json_output, quiet=quiet,
        no_color=no_color, verbose=verbose, debug=debug,
    )
    renderer = _make_renderer(config)
    session = _make_context(config, thread=thread or uuid.uuid4().hex, project=project)

    try:
        from cli import commands
        from cli.renderer import create_event_handler

        handler = create_event_handler(renderer)
        session.bus.subscribe(handler)

        result = commands.cmd_run(session, request)
        _output(renderer, result, lambda d: (
            renderer.success(f"Build passed · {d.get('project_root', '')}")
            if d.get("build_succeeded")
            else renderer.error(
                f"Workflow ended: {d.get('phase', 'unknown')}",
                run_id=session.run_id,
                thread_id=session.thread_id or "",
                resumable=True,
            )
        ))
    except KeyboardInterrupt:
        renderer.warning("Interrupted — use `resume` to continue")
    except Exception as exc:
        renderer.error(str(exc), run_id=session.run_id, thread_id=session.thread_id or "")
        if config.verbosity == "debug":
            raise
        raise typer.Exit(1)
    finally:
        session.close()


@app.command()
def new(
    thread: OptThread = None,
    project: OptProject = None,
    model: OptModel = None,
    json_output: OptJSON = False,
    quiet: OptQuiet = False,
    no_color: OptNoColor = False,
    verbose: OptVerbose = False,
) -> None:
    """Create a new project interactively."""
    config = _build_config(
        model=model, json_output=json_output, quiet=quiet, no_color=no_color, verbose=verbose,
    )
    renderer = _make_renderer(config)
    session = _make_context(config, thread=thread or uuid.uuid4().hex, project=project)

    try:
        from cli import commands
        from cli.repl import InteractiveREPL

        repl = InteractiveREPL(session, renderer, commands)
        repl.run()
    finally:
        session.close()


@app.command()
def resume(
    thread_id: Annotated[str, typer.Argument(help="Thread ID to resume")],
    model: OptModel = None,
    json_output: OptJSON = False,
    quiet: OptQuiet = False,
    no_color: OptNoColor = False,
    verbose: OptVerbose = False,
    debug: OptDebug = False,
) -> None:
    """Resume an interrupted workflow."""
    config = _build_config(
        model=model, json_output=json_output, quiet=quiet,
        no_color=no_color, verbose=verbose, debug=debug,
    )
    renderer = _make_renderer(config)
    session = _make_context(config, thread=thread_id, project=thread_id)

    try:
        from cli import commands

        result = commands.cmd_resume(session)
        _output(renderer, result, lambda d: renderer.render_status(d))
    except Exception as exc:
        renderer.error(str(exc), run_id=session.run_id, thread_id=thread_id)
        raise typer.Exit(1)
    finally:
        session.close()


@app.command()
def status(
    thread: OptThread = None,
    project: OptProject = None,
    json_output: OptJSON = False,
    quiet: OptQuiet = False,
    no_color: OptNoColor = False,
) -> None:
    """Show project and system status overview."""
    config = _build_config(json_output=json_output, quiet=quiet, no_color=no_color)
    renderer = _make_renderer(config)
    session = _make_context(config, thread=thread, project=project)

    try:
        from cli import commands
        from cli.status import render_full_status
        data = commands.cmd_status(session)
        if json_output:
            renderer.json_output(data)
        else:
            render_full_status(data, renderer.console)
    finally:
        session.close()


@app.command()
def model(
    subcommand: Annotated[Optional[str], typer.Argument(help="Subcommand: list, set, profile, reset, or role name")] = None,
    name: Annotated[Optional[str], typer.Argument(help="Model name or profile name")] = None,
    json_output: OptJSON = False,
    no_color: OptNoColor = False,
) -> None:
    """Inspect or configure model routing and profiles."""
    config = _build_config(json_output=json_output, no_color=no_color)
    renderer = _make_renderer(config)
    session = _make_context(config)

    try:
        from cli import commands
        from cli.model_selector import discover_models, get_current_profile, get_role_routing

        data = commands.cmd_model(session, subcmd=subcommand, arg=name)
        if json_output:
            renderer.json_output(data)
        elif subcommand in ("set", "profile", "reset") or (subcommand and name):
            renderer.success(data.get("message", "Model configuration updated"))
        else:
            renderer.render_model_selector(
                discover_models(),
                get_role_routing(),
                get_current_profile().value,
            )
    finally:
        session.close()


@app.command()
def deliberation(
    mode: Annotated[Optional[str], typer.Argument(help="Deliberation mode: OFF, LIGHT, DEEP, AUTO")] = None,
    json_output: OptJSON = False,
    no_color: OptNoColor = False,
) -> None:
    """Inspect or set test-time deliberation mode."""
    config = _build_config(json_output=json_output, no_color=no_color)
    renderer = _make_renderer(config)
    session = _make_context(config)

    try:
        from cli import commands
        data = commands.cmd_deliberation(session, mode=mode)
        if json_output:
            renderer.json_output(data)
        elif mode and data.get("status") == "success":
            renderer.success(f"Deliberation mode set to {data.get('deliberation_mode')}")
        else:
            renderer.render_deliberation_selector(data.get("deliberation_mode", "AUTO"))
    finally:
        session.close()


@app.command()
def why(
    json_output: OptJSON = False,
    no_color: OptNoColor = False,
) -> None:
    """Explain recent observable system and architectural decisions."""
    config = _build_config(json_output=json_output, no_color=no_color)
    renderer = _make_renderer(config)
    session = _make_context(config)

    try:
        from cli import commands
        data = commands.cmd_why(session)
        renderer.render_why(data)
    finally:
        session.close()


@app.command("commands")
def list_commands(
    json_output: OptJSON = False,
    no_color: OptNoColor = False,
) -> None:
    """List all available slash commands categorized."""
    config = _build_config(json_output=json_output, no_color=no_color)
    renderer = _make_renderer(config)
    session = _make_context(config)

    try:
        from cli.registry import get_command_registry
        reg = get_command_registry()
        renderer.render_commands_categorized(reg)
    finally:
        session.close()



@app.command()
def build(
    thread: OptThread = None,
    project: OptProject = None,
    json_output: OptJSON = False,
    quiet: OptQuiet = False,
    no_color: OptNoColor = False,
) -> None:
    """Run build in current project."""
    config = _build_config(json_output=json_output, quiet=quiet, no_color=no_color)
    renderer = _make_renderer(config)
    session = _make_context(config, thread=thread, project=project)

    try:
        from cli import commands

        data = commands.cmd_build(session)
        _output(renderer, data, lambda d: (
            renderer.success("Build passed")
            if d.get("build_succeeded")
            else renderer.render_build_error(d.get("errors", []))
        ))
    finally:
        session.close()


@app.command()
def doctor(
    json_output: OptJSON = False,
    no_color: OptNoColor = False,
) -> None:
    """Run system health checks."""
    config = _build_config(json_output=json_output, no_color=no_color)
    renderer = _make_renderer(config)
    session = _make_context(config)

    try:
        from cli import commands

        data = commands.cmd_doctor(session)
        _output(renderer, data, lambda d: renderer.render_doctor(d))
    finally:
        session.close()


@app.command()
def graph(
    thread: OptThread = None,
    json_output: OptJSON = False,
    no_color: OptNoColor = False,
) -> None:
    """Show LangGraph topology."""
    config = _build_config(json_output=json_output, no_color=no_color)
    renderer = _make_renderer(config)
    session = _make_context(config, thread=thread)

    try:
        from cli import commands

        data = commands.cmd_graph(session)
        _output(renderer, data, lambda d: renderer.render_graph(d))
    finally:
        session.close()


@app.command()
def config(
    json_output: OptJSON = False,
    no_color: OptNoColor = False,
) -> None:
    """Show current configuration."""
    config_obj = _build_config(json_output=json_output, no_color=no_color)
    renderer = _make_renderer(config_obj)
    session = _make_context(config_obj)

    try:
        from cli import commands

        data = commands.cmd_config(session)
        _output(renderer, data, lambda d: renderer.render_config(d))
    finally:
        session.close()


@app.command()
def deps(
    thread: OptThread = None,
    project: OptProject = None,
    json_output: OptJSON = False,
    no_color: OptNoColor = False,
) -> None:
    """Show dependency information."""
    config = _build_config(json_output=json_output, no_color=no_color)
    renderer = _make_renderer(config)
    session = _make_context(config, thread=thread, project=project)

    try:
        from cli import commands

        data = commands.cmd_deps(session)
        _output(renderer, data, lambda d: renderer.render_deps(d))
    finally:
        session.close()


@app.command()
def preview(
    port: Annotated[int, typer.Option("--port", "-p", help="Preview server port")] = 5173,
    json_output: OptJSON = False,
    no_color: OptNoColor = False,
) -> None:
    """Launch sandboxed preview server inside SandboxProfile.PREVIEW boundary."""
    config = _build_config(json_output=json_output, no_color=no_color)
    renderer = _make_renderer(config)
    session = _make_context(config)

    try:
        from cli import commands
        data = commands.cmd_preview(session, port=port)
        _output(renderer, data, lambda d: renderer.success(f"Preview server running at {d.get('url')}"))
    finally:
        session.close()


@app.command()
def visual(
    route: Annotated[Optional[str], typer.Option("--route", "-r", help="Specific route to evaluate")] = None,
    mobile: Annotated[bool, typer.Option("--mobile", help="Evaluate mobile viewport only")] = False,
    json_output: OptJSON = False,
    no_color: OptNoColor = False,
) -> None:
    """Run browser visual & functional evaluation across viewports."""
    config = _build_config(json_output=json_output, no_color=no_color)
    renderer = _make_renderer(config)
    session = _make_context(config)

    try:
        from cli import commands
        data = commands.cmd_visual(session, route=route, mobile_only=mobile)
        _output(renderer, data, lambda d: renderer.success(
            f"Visual eval completed: pass_rate={d.get('render_success_rate', 0.0)*100:.1f}%, "
            f"console_errors={d.get('console_errors_count', 0)}, "
            f"overflow_rate={d.get('overflow_failure_rate', 0.0)*100:.1f}%"
        ))
    finally:
        session.close()


@app.command()
def screenshots(
    json_output: OptJSON = False,
    no_color: OptNoColor = False,
) -> None:
    """List captured screenshots for current run."""
    config = _build_config(json_output=json_output, no_color=no_color)
    renderer = _make_renderer(config)
    session = _make_context(config)

    try:
        from cli import commands
        data = commands.cmd_screenshots(session)
        _output(renderer, data, lambda d: renderer.info(f"Run {d.get('run_id')}: {d.get('count', 0)} screenshots saved"))
    finally:
        session.close()


@app.command()
def threads(
    json_output: OptJSON = False,
    no_color: OptNoColor = False,
) -> None:
    """List LangGraph threads."""
    config = _build_config(json_output=json_output, no_color=no_color)
    renderer = _make_renderer(config)
    session = _make_context(config)

    try:
        from cli import commands

        data = commands.cmd_threads(session)
        _output(renderer, data, lambda d: renderer.render_threads(d))
    finally:
        session.close()



# ---------------------------------------------------------------------------
# Docs subcommands
# ---------------------------------------------------------------------------

@docs_app.command("status")
def docs_status(
    json_output: OptJSON = False,
    no_color: OptNoColor = False,
) -> None:
    """Show documentation index status."""
    config = _build_config(json_output=json_output, no_color=no_color)
    renderer = _make_renderer(config)
    session = _make_context(config)

    try:
        from cli import commands

        data = commands.cmd_docs_status(session)
        _output(renderer, data, lambda d: renderer.render_docs(d))
    finally:
        session.close()


@docs_app.command("sync")
def docs_sync(
    paths: Annotated[list[str], typer.Argument(help="Paths to documentation directories")],
    library: Annotated[str, typer.Option("--library", "-l", help="Library name (react, vite, etc.)")],
    json_output: OptJSON = False,
    no_color: OptNoColor = False,
) -> None:
    """Ingest documentation into the RAG index."""
    config = _build_config(json_output=json_output, no_color=no_color)
    renderer = _make_renderer(config)
    session = _make_context(config)

    try:
        from cli import commands

        data = commands.cmd_docs_sync(session, paths, library)
        _output(renderer, data, lambda d: renderer.success(
            f"Ingested {d.get('chunks', 0)} chunks for {d.get('library', library)}"
        ))
    finally:
        session.close()


@docs_app.command("query")
def docs_query(
    query: Annotated[str, typer.Argument(help="Search query")],
    json_output: OptJSON = False,
    no_color: OptNoColor = False,
) -> None:
    """Test documentation retrieval."""
    config = _build_config(json_output=json_output, no_color=no_color)
    renderer = _make_renderer(config)
    session = _make_context(config)

    try:
        from cli import commands

        data = commands.cmd_docs_query(session, query)
        _output(renderer, data, lambda d: renderer.render_docs_query(d))
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Eval subcommands
# ---------------------------------------------------------------------------

@eval_app.command("run")
def eval_run(
    suite: Annotated[str, typer.Argument(help="Benchmark suite name")] = "e2e",
    deliberation: Annotated[str, typer.Option("--deliberation", "-d", help="Deliberation mode")] = "AUTO",
    json_output: OptJSON = False,
    no_color: OptNoColor = False,
) -> None:
    """Run E2E evaluation suite."""
    config = _build_config(json_output=json_output, no_color=no_color)
    renderer = _make_renderer(config)
    session = _make_context(config)

    try:
        from cli import commands

        data = commands.cmd_eval_run(session, suite_name=suite, deliberation=deliberation)
        _output(renderer, data, lambda d: renderer.success(
            f"Eval run completed: {d.get('total_cases', 0)} cases, "
            f"first-pass={d.get('first_pass_build_rate', 0.0)*100:.1f}%, "
            f"final-pass={d.get('final_build_rate', 0.0)*100:.1f}%"
        ))
    finally:
        session.close()


@eval_app.command("rag")
def eval_rag(
    json_output: OptJSON = False,
    no_color: OptNoColor = False,
) -> None:
    """Run RAG documentation retrieval benchmark."""
    config = _build_config(json_output=json_output, no_color=no_color)
    renderer = _make_renderer(config)
    session = _make_context(config)

    try:
        from cli import commands

        data = commands.cmd_eval_rag(session)
        _output(renderer, data, lambda d: renderer.success(
            f"RAG Eval: {d.get('total_cases', 0)} cases, "
            f"Recall@1={d.get('recall_at_1', 0.0)*100:.1f}%, "
            f"MRR={d.get('mrr', 0.0):.3f}, "
            f"wrong_major_version_rate={d.get('wrong_major_version_rate', 0.0)*100:.1f}%"
        ))
    finally:
        session.close()


@eval_app.command("compare")
def eval_compare(
    run_a: Annotated[str, typer.Argument(help="Path to baseline run JSONL")],
    run_b: Annotated[str, typer.Argument(help="Path to candidate run JSONL")],
    json_output: OptJSON = False,
    no_color: OptNoColor = False,
) -> None:
    """Compare two evaluation runs."""
    config = _build_config(json_output=json_output, no_color=no_color)
    renderer = _make_renderer(config)
    session = _make_context(config)

    try:
        from cli import commands

        data = commands.cmd_eval_compare(session, run_a, run_b)
        if json_output:
            renderer.json_output(data)
        else:
            print(data.get("formatted", ""))
    finally:
        session.close()


@eval_app.command("failures")
def eval_failures(
    run_id: Annotated[str, typer.Argument(help="Run ID")],
    json_output: OptJSON = False,
    no_color: OptNoColor = False,
) -> None:
    """Display failure artifacts for a specific evaluation run."""
    config = _build_config(json_output=json_output, no_color=no_color)
    renderer = _make_renderer(config)
    session = _make_context(config)

    try:
        from cli import commands

        artifacts = commands.cmd_eval_failures(session, run_id)
        _output(renderer, artifacts, lambda d: (
            renderer.info(f"Found {len(d)} failure artifacts for {run_id}")
            if d else renderer.warning(f"No failure artifacts found for {run_id}")
        ))
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """CLI entry point."""
    try:
        app()
    except KeyboardInterrupt:
        print()  # Clean line after ^C
        raise typer.Exit(130)

