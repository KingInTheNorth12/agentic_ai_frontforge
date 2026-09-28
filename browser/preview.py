from __future__ import annotations

import socket
import subprocess
import time
import urllib.request
import uuid
from pathlib import Path

from sandbox import SandboxProfile, get_profile_policy, get_sandbox_manager
from sandbox.backend import (
    DEFAULT_SANDBOX_IMAGE,
    AppleContainerSandboxBackend,
    DockerSandboxBackend,
    MockSandboxBackend,
)


def find_free_port(start_port: int = 5173) -> int:
    """Find available local TCP port."""
    for port in range(start_port, start_port + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return start_port


class PreviewServerManager:
    """Spawns and manages preview servers inside SandboxProfile.PREVIEW policy boundary."""

    def __init__(self, project_root: str | Path, port: int = 5173):
        self.project_root = Path(project_root).resolve()
        self.port = port
        self.container_id: str | None = None
        self.process: subprocess.Popen[str] | None = None
        self.is_running: bool = False
        self.server_url: str = f"http://localhost:{self.port}"

    def start(self, timeout_seconds: int = 30) -> str:
        """Start Vite dev/preview server inside sandbox PREVIEW policy boundary."""
        manager = get_sandbox_manager()
        argv = ["npx", "vite", "preview", "--port", str(self.port), "--host", "0.0.0.0"]
        policy = get_profile_policy(SandboxProfile.PREVIEW, self.project_root)
        backend = manager.backend
        self.container_id = f"fa-preview-{uuid.uuid4().hex[:12]}"

        if isinstance(backend, MockSandboxBackend):
            self.process = subprocess.Popen(
                argv,
                cwd=self.project_root,
                env=policy.env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
                start_new_session=True,
            )
        elif isinstance(backend, DockerSandboxBackend):
            safe_root = backend.validate_mount_path(self.project_root)
            command = [
                "docker", "run", "--rm", "--detach", "--name", self.container_id,
                "--label", "frontend-agent-owned=true", "--security-opt", "no-new-privileges:true",
                "--cap-drop", "ALL", "--user", policy.container_user,
                f"--cpus={policy.cpu_limit}", f"--memory={policy.memory_limit}",
                f"--pids-limit={policy.pids_limit}", "--read-only", "--tmpfs", "/tmp:exec,mode=1777",
                "-p", f"127.0.0.1:{self.port}:{self.port}",
                "--mount", f"type=bind,source={safe_root},target=/workspace,readonly",
                "-w", "/workspace",
            ]
            for key, value in policy.env.items():
                command.extend(["-e", f"{key}={value}"])
            command.extend([DEFAULT_SANDBOX_IMAGE, *argv])
            result = subprocess.run(command, capture_output=True, text=True, check=False)
            if result.returncode != 0:
                raise RuntimeError(f"Could not start preview container: {result.stderr.strip()}")
        elif isinstance(backend, AppleContainerSandboxBackend):
            safe_root = backend.validate_mount_path(self.project_root)
            command = [
                "container", "run", "--rm", "--detach", "--id", self.container_id,
                "--label", "frontend-agent-owned=true", "--cpu", str(policy.cpu_limit),
                "--memory", str(policy.memory_limit),
                "--publish", f"127.0.0.1:{self.port}:{self.port}",
                "--volume", f"{safe_root}:/workspace:ro", "--workdir", "/workspace",
            ]
            for key, value in policy.env.items():
                command.extend(["--env", f"{key}={value}"])
            command.extend([DEFAULT_SANDBOX_IMAGE, *argv])
            result = subprocess.run(command, capture_output=True, text=True, check=False)
            if result.returncode != 0:
                raise RuntimeError(f"Could not start preview container: {result.stderr.strip()}")
        else:
            raise RuntimeError(f"Unsupported preview backend: {type(backend).__name__}")

        # Wait for server readiness
        start_time = time.time()
        while time.time() - start_time < timeout_seconds:
            try:
                req = urllib.request.Request(self.server_url, headers={"User-Agent": "antigravity-eval"})
                with urllib.request.urlopen(req, timeout=2) as response:
                    if response.status in (200, 304):
                        self.is_running = True
                        return self.server_url
            except Exception:
                if self.process is not None and self.process.poll() is not None:
                    self.stop()
                    raise RuntimeError("Preview server exited before becoming ready")
                time.sleep(0.5)

        self.stop()
        raise TimeoutError(f"Preview server did not become ready at {self.server_url}")

    def stop(self) -> None:
        """Clean up preview server instance."""
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
            self.process = None
        elif self.container_id:
            backend = get_sandbox_manager().backend
            command = (
                ["docker", "stop", self.container_id]
                if isinstance(backend, DockerSandboxBackend)
                else ["container", "stop", self.container_id]
            )
            subprocess.run(command, capture_output=True, text=True, check=False)
        self.container_id = None
        self.is_running = False



_preview_manager_instance: PreviewServerManager | None = None


def get_preview_manager(project_root: str | Path, port: int = 5173) -> PreviewServerManager:
    return PreviewServerManager(project_root, port)
