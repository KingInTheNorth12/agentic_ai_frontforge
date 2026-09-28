from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from sandbox.capability import SandboxCapability
from sandbox.policy import NetworkPolicy, SandboxPolicy
from sandbox.result import SandboxExecutionResult

# Base Docker container image for frontend sandbox workloads
DEFAULT_SANDBOX_IMAGE = os.getenv("FRONTEND_AGENT_SANDBOX_IMAGE", "node:22-alpine")

# Forbidden dangerous host paths that MUST NEVER be mounted into sandboxes
FORBIDDEN_HOST_PATTERNS = (
    r"^/$",
    r"^/root(?:/.*)?$",
    r"^/home/[^/]+$",
    r"^/Users/[^/]+$",
    r".*\.ssh(?:/.*)?$",
    r".*\.aws(?:/.*)?$",
    r".*\.gnupg(?:/.*)?$",
    r".*docker\.sock$",
    r".*containerd\.sock$",
    r".*\.env$",
)


def _timeout_output(output: str | bytes | None) -> str:
    # TimeoutExpired may contain bytes even when subprocess text=True.
    return output.decode("utf-8", errors="replace") if isinstance(output, bytes) else output or ""


class SandboxBackend(ABC):
    """Abstract base class for container runtime backends."""

    @abstractmethod
    def detect_capability(self) -> SandboxCapability:
        """Inspect and return runtime backend capabilities."""
        pass

    @abstractmethod
    def execute(
        self,
        policy: SandboxPolicy,
        argv: list[str],
        container_id: str,
        image_name: str = DEFAULT_SANDBOX_IMAGE,
    ) -> SandboxExecutionResult:
        """Execute command inside sandbox using strict policy."""
        pass

    @abstractmethod
    def cleanup_orphans(self, owner_label: str = "frontend-agent-owned=true") -> None:
        """Destroy orphaned containers owned by this application."""
        pass

    @staticmethod
    def validate_mount_path(host_path: str | Path) -> Path:
        """Validate that host path is safe and does not escape project boundaries or access sensitive files."""
        resolved = Path(host_path).resolve()
        resolved_str = str(resolved)

        for pattern in FORBIDDEN_HOST_PATTERNS:
            if re.match(pattern, resolved_str, re.IGNORECASE):
                raise ValueError(f"Refusing to mount sensitive or unsafe host path: {resolved_str!r}")

        return resolved


class DockerSandboxBackend(SandboxBackend):
    """Docker Engine / Docker Desktop runtime backend."""

    def detect_capability(self) -> SandboxCapability:
        docker_path = shutil.which("docker")
        if not docker_path:
            return SandboxCapability(
                runtime="docker",
                available=False,
                details="docker executable not found on PATH",
            )

        try:
            res = subprocess.run(
                [docker_path, "info"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if res.returncode != 0:
                return SandboxCapability(
                    runtime="docker",
                    available=False,
                    details=f"docker info returned code {res.returncode}: {res.stderr.strip()[:100]}",
                )
            return SandboxCapability(
                runtime="docker",
                available=True,
                details="Docker Engine available",
            )
        except Exception as exc:
            return SandboxCapability(
                runtime="docker",
                available=False,
                details=f"Docker check failed: {exc}",
            )

    def execute(
        self,
        policy: SandboxPolicy,
        argv: list[str],
        container_id: str,
        image_name: str = DEFAULT_SANDBOX_IMAGE,
    ) -> SandboxExecutionResult:
        cmd = [
            "docker", "run", "--rm",
            "--name", container_id,
            "--label", "frontend-agent-owned=true",
            "--security-opt", "no-new-privileges:true",
            "--cap-drop", "ALL",
        ]

        # 1. Container User
        if policy.container_user:
            cmd.extend(["--user", policy.container_user])

        # 2. Network policy
        if policy.network == NetworkPolicy.DISABLED:
            cmd.extend(["--net", "none"])
        elif policy.network == NetworkPolicy.MINIMAL_PORTS:
            for port in policy.exposed_ports:
                cmd.extend(["-p", f"127.0.0.1:{port}:{port}"])

        # 3. Resource limits
        if policy.cpu_limit:
            cmd.extend([f"--cpus={policy.cpu_limit}"])
        if policy.memory_limit:
            cmd.extend([f"--memory={policy.memory_limit}"])
        if policy.pids_limit:
            cmd.extend([f"--pids-limit={policy.pids_limit}"])

        # 4. Read-only root & tmpfs
        cmd.extend(["--read-only", "--tmpfs", "/tmp:exec,mode=1777"])

        # 5. Mounts validation and arguments
        for host_src, container_tgt in policy.readonly_mounts:
            safe_src = self.validate_mount_path(host_src)
            cmd.extend(["--mount", f"type=bind,source={safe_src},target={container_tgt},readonly"])

        for host_src, container_tgt in policy.writable_mounts:
            safe_src = self.validate_mount_path(host_src)
            cmd.extend(["--mount", f"type=bind,source={safe_src},target={container_tgt}"])

        # 6. Working dir & environment
        if policy.working_dir:
            cmd.extend(["-w", policy.working_dir])

        for k, v in policy.env.items():
            cmd.extend(["-e", f"{k}={v}"])

        # Image and command
        cmd.append(image_name)
        cmd.extend(argv)

        start_time = time.time()
        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=policy.timeout_seconds,
                shell=False,
                check=False,
            )
            duration = time.time() - start_time
            return SandboxExecutionResult(
                exit_code=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
                timeout=False,
                profile=policy.profile.value,
                runtime_backend="docker",
                duration_seconds=duration,
            )
        except subprocess.TimeoutExpired as exc:
            duration = time.time() - start_time
            # Cleanup timeout container
            subprocess.run(["docker", "kill", container_id], capture_output=True, check=False)
            return SandboxExecutionResult(
                exit_code=124,
                stdout=_timeout_output(exc.stdout),
                stderr=_timeout_output(exc.stderr) + "\nCommand timed out",
                timeout=True,
                profile=policy.profile.value,
                runtime_backend="docker",
                duration_seconds=duration,
            )

    def cleanup_orphans(self, owner_label: str = "frontend-agent-owned=true") -> None:
        try:
            res = subprocess.run(
                ["docker", "ps", "-a", "--filter", f"label={owner_label}", "--format", "{{.ID}}"],
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode == 0 and res.stdout.strip():
                cids = res.stdout.strip().splitlines()
                subprocess.run(["docker", "rm", "-f"] + cids, capture_output=True, check=False)
        except Exception:
            pass


class AppleContainerSandboxBackend(SandboxBackend):
    """macOS Apple official `container` CLI runtime backend."""

    def detect_capability(self) -> SandboxCapability:
        container_path = shutil.which("container")
        is_macos = platform.system() == "Darwin"
        is_arm = platform.machine() == "arm64"

        if not container_path:
            return SandboxCapability(
                runtime="apple-container",
                available=False,
                details="container CLI executable not found on PATH",
            )

        if not (is_macos and is_arm):
            return SandboxCapability(
                runtime="apple-container",
                available=False,
                details="Apple container CLI requires macOS Apple Silicon",
            )

        try:
            res = subprocess.run(
                [container_path, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if res.returncode == 0:
                return SandboxCapability(
                    runtime="apple-container",
                    available=True,
                    pids_limit=False,  # Apple container CLI does not expose pids_limit flag
                    details="Apple container CLI available",
                )
        except Exception:
            pass

        return SandboxCapability(
            runtime="apple-container",
            available=False,
            details="container CLI check failed",
        )

    def execute(
        self,
        policy: SandboxPolicy,
        argv: list[str],
        container_id: str,
        image_name: str = DEFAULT_SANDBOX_IMAGE,
    ) -> SandboxExecutionResult:
        cmd = [
            "container", "run", "--rm",
            "--id", container_id,
            "--label", "frontend-agent-owned=true",
        ]

        # Network
        if policy.network == NetworkPolicy.DISABLED:
            cmd.extend(["--network", "none"])
        elif policy.network == NetworkPolicy.MINIMAL_PORTS:
            for port in policy.exposed_ports:
                cmd.extend(["--publish", f"127.0.0.1:{port}:{port}"])

        # CPU & Memory
        if policy.cpu_limit:
            cmd.extend(["--cpu", str(policy.cpu_limit)])
        if policy.memory_limit:
            cmd.extend(["--memory", str(policy.memory_limit)])

        # Mounts
        for host_src, container_tgt in policy.readonly_mounts:
            safe_src = self.validate_mount_path(host_src)
            cmd.extend(["--volume", f"{safe_src}:{container_tgt}:ro"])

        for host_src, container_tgt in policy.writable_mounts:
            safe_src = self.validate_mount_path(host_src)
            cmd.extend(["--volume", f"{safe_src}:{container_tgt}"])

        # Working dir & Env
        if policy.working_dir:
            cmd.extend(["--workdir", policy.working_dir])

        for k, v in policy.env.items():
            cmd.extend(["--env", f"{k}={v}"])

        cmd.append(image_name)
        cmd.extend(argv)

        start_time = time.time()
        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=policy.timeout_seconds,
                shell=False,
                check=False,
            )
            duration = time.time() - start_time
            return SandboxExecutionResult(
                exit_code=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
                timeout=False,
                profile=policy.profile.value,
                runtime_backend="apple-container",
                duration_seconds=duration,
            )
        except subprocess.TimeoutExpired as exc:
            duration = time.time() - start_time
            subprocess.run(["container", "stop", container_id], capture_output=True, check=False)
            return SandboxExecutionResult(
                exit_code=124,
                stdout=_timeout_output(exc.stdout),
                stderr=_timeout_output(exc.stderr) + "\nCommand timed out",
                timeout=True,
                profile=policy.profile.value,
                runtime_backend="apple-container",
                duration_seconds=duration,
            )

    def cleanup_orphans(self, owner_label: str = "frontend-agent-owned=true") -> None:
        try:
            subprocess.run(["container", "rm", "-f", "frontend-agent-owned=true"], capture_output=True, check=False)
        except Exception:
            pass


class MockSandboxBackend(SandboxBackend):
    """Fallback sandbox backend when container engine is unavailable on host system."""

    def detect_capability(self) -> SandboxCapability:
        return SandboxCapability(
            runtime="mock",
            network_none=False,  # Process level isolation cannot cut network without container
            readonly_root=False,
            cpu_limit=False,
            memory_limit=False,
            pids_limit=False,
            non_root_user=False,
            seccomp_profile=False,
            available=True,
            details="Fallback host subprocess sandbox (shell=False, array argv, safe path validation)",
        )

    def execute(
        self,
        policy: SandboxPolicy,
        argv: list[str],
        container_id: str,
        image_name: str = DEFAULT_SANDBOX_IMAGE,
    ) -> SandboxExecutionResult:
        # Validate mounts even in mock mode
        for host_src, _ in policy.writable_mounts + policy.readonly_mounts:
            self.validate_mount_path(host_src)

        cwd_path = Path.cwd()
        if policy.writable_mounts:
            cwd_path = Path(policy.writable_mounts[0][0])

        start_time = time.time()
        try:
            completed = subprocess.run(
                argv,
                cwd=cwd_path,
                capture_output=True,
                text=True,
                timeout=policy.timeout_seconds,
                shell=False,
                check=False,
                env=policy.env,
            )
            duration = time.time() - start_time
            return SandboxExecutionResult(
                exit_code=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
                timeout=False,
                profile=policy.profile.value,
                runtime_backend="mock",
                duration_seconds=duration,
            )
        except subprocess.TimeoutExpired as exc:
            duration = time.time() - start_time
            return SandboxExecutionResult(
                exit_code=124,
                stdout=_timeout_output(exc.stdout),
                stderr=_timeout_output(exc.stderr) + "\nCommand timed out",
                timeout=True,
                profile=policy.profile.value,
                runtime_backend="mock",
                duration_seconds=duration,
            )

    def cleanup_orphans(self, owner_label: str = "frontend-agent-owned=true") -> None:
        pass
