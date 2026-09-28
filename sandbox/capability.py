from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class SandboxCapability:
    """Capability description of a sandbox runtime backend."""

    runtime: str
    network_none: bool = True
    readonly_root: bool = True
    cpu_limit: bool = True
    memory_limit: bool = True
    pids_limit: bool = True
    non_root_user: bool = True
    seccomp_profile: bool = True
    available: bool = True
    details: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
