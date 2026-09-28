"""Sandbox Subsystem for safe, isolated execution of untrusted workloads and package scripts."""

from __future__ import annotations

from sandbox.capability import SandboxCapability
from sandbox.manager import SandboxManager, get_sandbox_manager
from sandbox.policy import NetworkPolicy, SandboxPolicy, SandboxProfile, get_profile_policy
from sandbox.result import SandboxExecutionResult

__all__ = [
    "SandboxProfile",
    "NetworkPolicy",
    "SandboxPolicy",
    "SandboxCapability",
    "SandboxExecutionResult",
    "SandboxManager",
    "get_sandbox_manager",
    "get_profile_policy",
]
