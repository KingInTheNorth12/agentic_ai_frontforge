from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from typing import Any

try:
    from cli.events import emit, EventType
except ImportError:
    emit = None  # type: ignore
    EventType = None  # type: ignore

import config as app_config
from sandbox.backend import (
    AppleContainerSandboxBackend,
    DockerSandboxBackend,
    MockSandboxBackend,
    SandboxBackend,
)
from sandbox.capability import SandboxCapability
from sandbox.policy import SandboxPolicy, SandboxProfile, get_profile_policy
from sandbox.result import SandboxExecutionResult

MAX_LOG_CHARS = 20_000


def truncate_log_summary(text: str, max_chars: int = MAX_LOG_CHARS) -> tuple[str, bool]:
    """Truncate log safely preserving head and tail diagnostic context."""
    if not text or len(text) <= max_chars:
        return text, False

    half = max_chars // 2
    head = text[:half]
    tail = text[-half:]
    omitted = len(text) - max_chars
    truncated_msg = f"\n\n... [TRUNCATED {omitted} CHARACTERS FOR LOG SAFETY] ...\n\n"
    return head + truncated_msg + tail, True


class SandboxManager:
    """Central sandbox manager for executing untrusted workloads through isolated container backends."""

    def __init__(self, backend_override: str | None = None):
        self.requested_backend = (
            backend_override or os.getenv("FRONTEND_AGENT_SANDBOX_BACKEND", "auto")
        ).lower()
        self.backend = self._discover_backend()

    def _discover_backend(self) -> SandboxBackend:
        if self.requested_backend == "docker":
            return DockerSandboxBackend()
        if self.requested_backend in ("apple", "apple-container"):
            return AppleContainerSandboxBackend()
        if self.requested_backend == "mock":
            return MockSandboxBackend()

        # Automatic discovery: test Apple Container, then Docker, then fallback Mock
        apple_backend = AppleContainerSandboxBackend()
        if apple_backend.detect_capability().available:
            return apple_backend

        docker_backend = DockerSandboxBackend()
        if docker_backend.detect_capability().available:
            return docker_backend

        return MockSandboxBackend()

    def get_capability(self) -> SandboxCapability:
        """Return selected runtime backend capability object."""
        return self.backend.detect_capability()

    def create_run_workspace(self, run_id: str | None = None) -> tuple[str, Path, Path, Path]:
        """Create unique run directory structure under AGENT_DIR/runs/<run_id>/."""
        rid = run_id or f"run-{uuid.uuid4().hex[:10]}"
        run_dir = app_config.AGENT_DIR / "runs" / rid
        workspace_dir = run_dir / "workspace"
        artifacts_dir = run_dir / "artifacts"
        logs_dir = run_dir / "logs"

        workspace_dir.mkdir(parents=True, exist_ok=True)
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        logs_dir.mkdir(parents=True, exist_ok=True)

        return rid, workspace_dir, artifacts_dir, logs_dir

    def execute(
        self,
        policy: SandboxPolicy,
        argv: list[str],
        run_id: str | None = None,
    ) -> SandboxExecutionResult:
        """Execute command in sandbox with strict lifecycle, event emission, and cleanup."""
        rid = run_id or f"sbx-{uuid.uuid4().hex[:8]}"
        container_id = f"fa-sbx-{uuid.uuid4().hex[:12]}"

        if emit is not None and EventType is not None:
            emit(
                EventType.SANDBOX_CREATE_STARTED,
                run_id=rid,
                profile=policy.profile.value,
                runtime=self.backend.detect_capability().runtime,
            )

        if emit is not None and EventType is not None:
            emit(EventType.SANDBOX_CREATED, run_id=rid, container_id=container_id)

        if emit is not None and EventType is not None:
            emit(
                EventType.SANDBOX_COMMAND_STARTED,
                run_id=rid,
                command=" ".join(argv),
                profile=policy.profile.value,
            )

        start_time = time.time()
        try:
            result = self.backend.execute(policy, argv, container_id)
        except Exception as exc:
            duration = time.time() - start_time
            if emit is not None and EventType is not None:
                emit(EventType.SANDBOX_FAILED, run_id=rid, error=str(exc))
            return SandboxExecutionResult(
                exit_code=1,
                stdout="",
                stderr=f"Sandbox execution error: {exc}",
                timeout=False,
                profile=policy.profile.value,
                runtime_backend=self.backend.detect_capability().runtime,
                duration_seconds=duration,
            )

        # Process log truncation
        stdout_trunc, std_tr = truncate_log_summary(result.stdout)
        stderr_trunc, err_tr = truncate_log_summary(result.stderr)
        is_truncated = std_tr or err_tr

        result.stdout = stdout_trunc
        result.stderr = stderr_trunc
        result.truncated = is_truncated

        if emit is not None and EventType is not None:
            if result.timeout:
                emit(EventType.SANDBOX_TIMEOUT, run_id=rid, duration=result.duration_seconds)
            elif result.ok:
                emit(
                    EventType.SANDBOX_COMMAND_COMPLETED,
                    run_id=rid,
                    exit_code=result.exit_code,
                    duration=result.duration_seconds,
                )
            else:
                emit(
                    EventType.SANDBOX_FAILED,
                    run_id=rid,
                    exit_code=result.exit_code,
                    duration=result.duration_seconds,
                )

            emit(EventType.SANDBOX_DESTROYED, run_id=rid, container_id=container_id)

        return result

    def execute_npm(
        self,
        action_name: str,
        argv: list[str],
        project_root: str | Path,
        profile: SandboxProfile = SandboxProfile.BUILD,
        run_id: str | None = None,
    ) -> SandboxExecutionResult:
        """Execute npm action inside sandbox policy boundary."""
        policy = get_profile_policy(profile, project_root)
        return self.execute(policy, argv, run_id=run_id)

    def cleanup_orphans(self) -> None:
        self.backend.cleanup_orphans()


_manager_instance: SandboxManager | None = None


def get_sandbox_manager() -> SandboxManager:
    """Return SandboxManager global instance."""
    global _manager_instance
    if _manager_instance is None:
        _manager_instance = SandboxManager()
    return _manager_instance
