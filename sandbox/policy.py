from __future__ import annotations

import os
from dataclasses import dataclass, field

from enum import Enum
from pathlib import Path
from typing import Any

import config as app_config



class SandboxProfile(str, Enum):
    DEPENDENCY = "dependency"
    BUILD = "build"
    TEST = "test"
    PREVIEW = "preview"


class NetworkPolicy(str, Enum):
    DISABLED = "disabled"
    MINIMAL_PORTS = "minimal_ports"
    FULL_OUTBOUND = "full_outbound"


# Explicit safe environment variable allowlist
SAFE_ENV_ALLOWLIST = {
    "PATH": f"/opt/homebrew/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:{os.getenv('PATH', '')}",
    "NODE_ENV": "production",
    "CI": "1",
    "NPM_CONFIG_CACHE": "/tmp/frontend-agent-npm",
    "NEXT_TELEMETRY_DISABLED": "1",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "TERM": "xterm",
}

# Sensitive keys that MUST NEVER be passed to sandboxes
FORBIDDEN_ENV_PREFIXES = (
    "AWS_",
    "AZURE_",
    "GCP_",
    "GOOGLE_",
    "SSH_",
    "GITHUB_",
    "OLLAMA_",
    "FRONTEND_AGENT_",
    "OPENAI_",
    "ANTHROPIC_",
    "SECRET_",
    "TOKEN_",
    "KEY_",
    "PASS",
)


@dataclass
class SandboxPolicy:
    """Explicit policy object for sandbox workload execution."""

    profile: SandboxProfile
    network: NetworkPolicy
    cpu_limit: float | None = 2.0
    memory_limit: str | None = "2g"
    pids_limit: int | None = 100
    timeout_seconds: int = 180
    working_dir: str = "/workspace"
    env: dict[str, str] = field(default_factory=dict)
    readonly_mounts: list[tuple[str, str]] = field(default_factory=list)
    writable_mounts: list[tuple[str, str]] = field(default_factory=list)
    exposed_ports: list[int] = field(default_factory=list)
    container_user: str = "1000:1000"
    cleanup_behavior: str = "destroy_always"

    def __post_init__(self) -> None:
        # Enforce max timeout limit
        if self.timeout_seconds <= 0 or self.timeout_seconds > 600:
            self.timeout_seconds = 300

        # Sanitize env dictionary to prevent secret leak
        cleaned_env = dict(SAFE_ENV_ALLOWLIST)
        for k, v in self.env.items():
            k_upper = k.upper()
            if any(k_upper.startswith(prefix) for prefix in FORBIDDEN_ENV_PREFIXES):
                continue
            cleaned_env[k] = str(v)
        self.env = cleaned_env


def get_profile_policy(
    profile: SandboxProfile,
    project_root: str | Path,
    *,
    custom_policy: SandboxPolicy | None = None,
) -> SandboxPolicy:
    """Factory creating strict, profile-specific sandbox policies."""
    root_str = str(Path(project_root).resolve())

    if custom_policy is not None:
        policy = custom_policy
        policy.profile = profile
        return policy

    if profile == SandboxProfile.DEPENDENCY:
        return SandboxPolicy(
            profile=SandboxProfile.DEPENDENCY,
            network=NetworkPolicy.FULL_OUTBOUND,
            cpu_limit=2.0,
            memory_limit="2g",
            pids_limit=150,
            timeout_seconds=app_config.COMMAND_TIMEOUT_SECONDS,
            working_dir="/workspace",
            writable_mounts=[(root_str, "/workspace")],
        )

    if profile == SandboxProfile.BUILD:
        return SandboxPolicy(
            profile=SandboxProfile.BUILD,
            network=NetworkPolicy.DISABLED,
            cpu_limit=2.0,
            memory_limit="2g",
            pids_limit=100,
            timeout_seconds=app_config.COMMAND_TIMEOUT_SECONDS,
            working_dir="/workspace",
            writable_mounts=[(root_str, "/workspace")],
        )

    if profile == SandboxProfile.TEST:
        return SandboxPolicy(
            profile=SandboxProfile.TEST,
            network=NetworkPolicy.DISABLED,
            cpu_limit=1.5,
            memory_limit="1g",
            pids_limit=50,
            timeout_seconds=120,
            working_dir="/workspace",
            writable_mounts=[(root_str, "/workspace")],
        )

    if profile == SandboxProfile.PREVIEW:
        return SandboxPolicy(
            profile=SandboxProfile.PREVIEW,
            network=NetworkPolicy.MINIMAL_PORTS,
            cpu_limit=1.0,
            memory_limit="1g",
            pids_limit=50,
            timeout_seconds=300,
            working_dir="/workspace",
            readonly_mounts=[(root_str, "/workspace")],
            exposed_ports=[5173],
        )

    raise ValueError(f"Unknown sandbox profile: {profile}")
