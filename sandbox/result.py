from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class SandboxExecutionResult:
    """Structured execution result returned by SandboxManager."""

    exit_code: int
    stdout: str
    stderr: str
    timeout: bool = False
    signal: int | None = None
    profile: str = ""
    runtime_backend: str = ""
    duration_seconds: float = 0.0
    truncated: bool = False
    resource_limit_failure: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timeout

    def to_dict(self) -> dict[str, Any]:
        return {
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "timeout": self.timeout,
            "signal": self.signal,
            "profile": self.profile,
            "runtime_backend": self.runtime_backend,
            "duration_seconds": round(self.duration_seconds, 3),
            "truncated": self.truncated,
            "resource_limit_failure": self.resource_limit_failure,
            "ok": self.ok,
        }
