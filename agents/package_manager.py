from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

try:
    from cli.events import emit, EventType
except ImportError:
    emit = None  # type: ignore
    EventType = None  # type: ignore

import config as app_config
from state import ProjectState
from frameworks import FRAMEWORKS, framework_id, manifest
from tools import sanitize_project_name, save_json
from validators import validate_imports


PACKAGE_RE = re.compile(r"^(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*$", re.IGNORECASE)
EXACT_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")


class NpmAction(str, Enum):
    VERSION = "version"
    RESOLVE_RUNTIME = "resolve_runtime"
    RESOLVE_DEV = "resolve_dev"
    CI_INSTALL = "ci_install"
    LIST_TREE = "list_tree"
    BUILD = "build"
    LINT = "lint"
    TEST = "test"


@dataclass
class CommandResult:
    action: str
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


from sandbox import SandboxProfile, get_sandbox_manager


class RestrictedNpmRunner:
    """Executes only typed npm actions inside SandboxManager. No arbitrary command strings or shell=True."""

    def __init__(self, project_root: str | Path, *, allow_install_scripts: bool | None = None):
        self.project_root = Path(project_root).resolve()
        self.allow_install_scripts = (
            app_config.ALLOW_NPM_INSTALL_SCRIPTS
            if allow_install_scripts is None
            else allow_install_scripts
        )
        if not self.project_root.is_dir():
            raise FileNotFoundError(self.project_root)
        if shutil.which("npm") is None:
            raise RuntimeError("npm is not available on PATH")

    @staticmethod
    def _validate_packages(packages: Iterable[str]) -> list[str]:
        result: list[str] = []
        for package in packages:
            value = str(package).strip()
            name, version = _split_package_spec(value)
            if not PACKAGE_RE.fullmatch(name):
                raise ValueError(f"Unsafe or unsupported npm package name: {package!r}")
            if version is not None and not EXACT_VERSION_RE.fullmatch(version):
                raise ValueError(f"Only exact remembered semver versions are allowed: {package!r}")
            result.append(value)
        return sorted(set(result))

    def _run(self, action: NpmAction, argv: list[str]) -> CommandResult:
        if action in {NpmAction.RESOLVE_RUNTIME, NpmAction.RESOLVE_DEV, NpmAction.CI_INSTALL}:
            profile = SandboxProfile.DEPENDENCY
        elif action is NpmAction.TEST:
            profile = SandboxProfile.TEST
        else:
            profile = SandboxProfile.BUILD

        manager = get_sandbox_manager()
        res = manager.execute_npm(action.value, argv, self.project_root, profile=profile)

        return CommandResult(
            action=action.value,
            argv=argv,
            returncode=res.exit_code,
            stdout=res.stdout,
            stderr=res.stderr,
        )


    def execute(self, action: NpmAction, packages: Iterable[str] = ()) -> CommandResult:
        if action is NpmAction.VERSION:
            return self._run(action, ["npm", "--version"])

        if action in {NpmAction.RESOLVE_RUNTIME, NpmAction.RESOLVE_DEV}:
            safe_packages = self._validate_packages(packages)
            if not safe_packages:
                return CommandResult(action.value, [], 0, "No packages requested", "")
            argv = ["npm", "install", "--save-exact", "--package-lock-only"]
            if action is NpmAction.RESOLVE_DEV:
                argv.append("--save-dev")
            if not self.allow_install_scripts:
                argv.append("--ignore-scripts")
            argv += safe_packages
            return self._run(action, argv)

        if action is NpmAction.CI_INSTALL:
            argv = ["npm", "ci", "--include=dev"]
            if not self.allow_install_scripts:
                argv.append("--ignore-scripts")
            return self._run(action, argv)

        if action is NpmAction.LIST_TREE:
            return self._run(action, ["npm", "ls", "--all", "--json", "--include=dev"])

        if action is NpmAction.BUILD:
            return self._run(action, ["npm", "run", "build"])

        if action is NpmAction.LINT:
            return self._run(action, ["npm", "run", "lint", "--if-present"])

        if action is NpmAction.TEST:
            return self._run(action, ["npm", "test", "--if-present", "--", "--run"])

        raise ValueError(f"Unsupported npm action: {action}")


DEV_PACKAGES = {
    "vite",
    "@vitejs/plugin-react",
    "tailwindcss",
    "postcss",
    "autoprefixer",
    "eslint",
    "@eslint/js",
    "eslint-plugin-react-hooks",
    "eslint-plugin-react-refresh",
}

BASE_RUNTIME = {"react", "react-dom"}
BASE_DEV = {"vite", "@vitejs/plugin-react"}


def _split_package_spec(value: str) -> tuple[str, str | None]:
    if value.startswith("@"):
        slash = value.find("/")
        if slash == -1:
            return value, None
        at = value.find("@", slash)
    else:
        at = value.find("@")
    if at == -1:
        return value, None
    return value[:at], value[at + 1:] or None


def _with_remembered_versions(packages: list[str], state: ProjectState) -> list[str]:
    remembered = (
        state.get("remembered_context", {})
        .get("dependency_memory", {})
        .get("locked_versions", {})
    )
    result: list[str] = []
    for package in packages:
        version = remembered.get(package) if isinstance(remembered, dict) else None
        if isinstance(version, str) and EXACT_VERSION_RE.fullmatch(version):
            result.append(f"{package}@{version}")
        else:
            result.append(package)
    return result


def _write_base_manifest(project_root: str, project_name: str, framework: str = "react") -> None:
    save_json(Path(project_root) / "package.json", manifest(sanitize_project_name(project_name), framework))


def _split_dependencies(blueprint: dict[str, Any]) -> tuple[list[str], list[str]]:
    contract = FRAMEWORKS[framework_id(blueprint.get("framework"))]
    requested = set(blueprint.get("dependencies", []))
    dev = set(contract["dev"]) | (requested & DEV_PACKAGES)
    runtime = set(contract["runtime"]) | (requested - dev)
    return sorted(runtime), sorted(dev)


def _locked_top_level_versions(project_root: str) -> dict[str, str]:
    lock_path = Path(project_root) / "package-lock.json"
    if not lock_path.is_file():
        return {}
    data = json.loads(lock_path.read_text(encoding="utf-8"))
    root_pkg = data.get("packages", {}).get("", {})
    combined: dict[str, str] = {}
    combined.update(root_pkg.get("dependencies", {}))
    combined.update(root_pkg.get("devDependencies", {}))
    return combined


def resolve(state: ProjectState) -> dict[str, Any]:
    """Resolve the dependency tree before code generation.

    This creates package.json/package-lock.json but deliberately does not install
    node_modules yet. The resulting exact top-level versions are fed into code
    generation so prompts/RAG can be version-aware.
    """
    project_root = state["project_root"]
    blueprint = state["blueprint"]
    errors = list(state.get("errors", []))

    if emit is not None:
        emit(EventType.DEPENDENCY_RESOLUTION_STARTED)

    _write_base_manifest(project_root, blueprint.get("project_name", "frontend-project"), blueprint.get("framework", "react"))
    runner = RestrictedNpmRunner(project_root)
    runtime, dev = _split_dependencies(blueprint)
    contract = FRAMEWORKS[framework_id(blueprint.get("framework"))]
    baseline = contract["runtime"] | contract["dev"]
    runtime_specs = [f"{p}@{baseline[p]}" if p in baseline else p for p in _with_remembered_versions(runtime, state)]
    dev_specs = [f"{p}@{baseline[p]}" if p in baseline else p for p in _with_remembered_versions(dev, state)]

    command_results: list[CommandResult] = []
    for action, packages in [
        (NpmAction.VERSION, []),
        (NpmAction.RESOLVE_RUNTIME, runtime_specs),
        (NpmAction.RESOLVE_DEV, dev_specs),
    ]:
        result = runner.execute(action, packages)
        command_results.append(result)
        if not result.ok:
            errors.append({
                "stage": "dependency_resolution",
                "action": result.action,
                "returncode": result.returncode,
                "stderr": result.stderr,
                "stdout": result.stdout,
            })
            break

    ok = bool(command_results) and all(result.ok for result in command_results)
    if emit is not None:
        if ok:
            emit(EventType.DEPENDENCY_RESOLVED)
        else:
            emit(EventType.DEPENDENCY_RESOLUTION_FAILED)
    report = {
        "resolution_ok": ok,
        "ok": False,  # Full package/build stage is not complete yet.
        "runtime_dependencies": runtime,
        "dev_dependencies": dev,
        "resolution_specs": {"runtime": runtime_specs, "dev": dev_specs},
        "locked_versions": _locked_top_level_versions(project_root),
        "install_scripts_enabled": runner.allow_install_scripts,
        "resolution_commands": [asdict(result) | {"ok": result.ok} for result in command_results],
        "commands": [],
    }
    save_json(Path(project_root) / "package_manager_report.json", report)
    return {
        "package_report": report,
        "errors": errors,
        "phase": "dependencies_resolved" if ok else "dependency_resolution_failed",
    }


def install_and_build(state: ProjectState) -> dict[str, Any]:
    """Install the frozen lockfile, validate the tree, and run the real build."""
    project_root = state["project_root"]
    blueprint = state["blueprint"]
    errors = list(state.get("errors", []))
    report = dict(state.get("package_report", {}))

    import_issues = validate_imports(project_root, blueprint)
    errors = [e for e in errors if e.get("stage") != "imports"]
    errors.extend(import_issues)
    if import_issues:
        report.update({
            "ok": False,
            "reason": "import_contract_violation",
            "import_issues": import_issues,
            "commands": [],
        })
        save_json(Path(project_root) / "package_manager_report.json", report)
        return {"package_report": report, "errors": errors, "build_succeeded": False, "phase": "package_blocked"}

    if emit is not None:
        emit(EventType.BUILD_STARTED)

    runner = RestrictedNpmRunner(project_root)
    command_results: list[CommandResult] = []
    actions = [NpmAction.CI_INSTALL, NpmAction.LIST_TREE, NpmAction.BUILD]
    for action in actions:
        result = runner.execute(action)
        command_results.append(result)
        if not result.ok:
            errors.append({
                "stage": "package_manager",
                "action": result.action,
                "returncode": result.returncode,
                "stderr": result.stderr,
                "stdout": result.stdout,
            })
            break

    ok = bool(command_results) and all(result.ok for result in command_results)
    if emit is not None:
        if ok:
            emit(EventType.BUILD_PASSED)
        else:
            emit(EventType.BUILD_FAILED)
    report.update({
        "ok": ok,
        "reason": None if ok else "command_failed",
        "locked_versions": _locked_top_level_versions(project_root),
        "commands": [asdict(result) | {"ok": result.ok} for result in command_results],
    })
    save_json(Path(project_root) / "package_manager_report.json", report)
    return {
        "package_report": report,
        "build_succeeded": ok,
        "errors": errors,
        "phase": "build_passed" if ok else "build_failed",
    }
