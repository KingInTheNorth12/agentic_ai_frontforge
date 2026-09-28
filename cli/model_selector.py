from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Any

import config as app_config
from llm import make_model


class ModelProfile(str, Enum):
    FAST = "FAST"
    BALANCED = "BALANCED"
    QUALITY = "QUALITY"
    CUSTOM = "CUSTOM"


MODEL_ROLES = [
    "planner",
    "architect",
    "component",
    "reviewer",
    "repair",
    "clarifier",
    "deliberation",
]

_CURRENT_PROFILE: ModelProfile = ModelProfile.BALANCED


@dataclass
class DiscoveredModel:
    name: str
    size_gb: float = 0.0
    status: str = "available"
    assigned_roles: list[str] = None

    def __post_init__(self):
        if self.assigned_roles is None:
            self.assigned_roles = []


def discover_models() -> list[DiscoveredModel]:
    """Dynamically discover available models from Ollama / local provider."""
    discovered: list[DiscoveredModel] = []
    current_routing = get_role_routing()

    try:
        import httpx
        ollama_host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
        resp = httpx.get(f"{ollama_host}/api/tags", timeout=1.5)
        if resp.status_code == 200:
            data = resp.json()
            for m in data.get("models", []):
                name = m.get("name", "")
                size_bytes = m.get("size", 0)
                size_gb = round(size_bytes / (1024**3), 1) if size_bytes else 0.0
                roles = [r for r, mname in current_routing.items() if mname == name]
                discovered.append(DiscoveredModel(
                    name=name,
                    size_gb=size_gb,
                    status="running" if roles else "available",
                    assigned_roles=roles,
                ))
    except Exception:
        pass

    if not discovered:
        # Fallback to configured model and standard candidates
        known = list(dict.fromkeys([
            app_config.MODEL_NAME,
            "qwen3:latest",
            "qwen3-coder:14b",
            "qwen3-coder:8b",
            "qwen3:8b",
            "qwen3:4b",
        ]))
        for name in known:
            roles = [r for r, mname in current_routing.items() if mname == name]
            discovered.append(DiscoveredModel(
                name=name,
                size_gb=4.5 if "4b" in name else (8.5 if "8b" in name else 14.5),
                status="configured" if name == app_config.MODEL_NAME else "available",
                assigned_roles=roles,
            ))

    return discovered


def get_role_routing() -> dict[str, str]:
    """Return active role-to-model routing dictionary."""
    routing: dict[str, str] = {}
    for role in MODEL_ROLES:
        env_key = f"FRONTEND_AGENT_MODEL_{role.upper()}"
        routing[role] = os.getenv(env_key, app_config.MODEL_NAME)
    return routing


def set_role_model(role: str, model_name: str) -> None:
    """Assign specific model to a role."""
    global _CURRENT_PROFILE
    clean_role = role.lower().strip()
    if clean_role == "global" or clean_role == "all":
        for r in MODEL_ROLES:
            os.environ[f"FRONTEND_AGENT_MODEL_{r.upper()}"] = model_name
        os.environ["FRONTEND_AGENT_MODEL"] = model_name
        app_config.MODEL_NAME = model_name
    elif clean_role in MODEL_ROLES:
        os.environ[f"FRONTEND_AGENT_MODEL_{clean_role.upper()}"] = model_name
    _CURRENT_PROFILE = ModelProfile.CUSTOM


def reset_role_routing(default_model: str | None = None) -> None:
    """Reset all role routings back to default model."""
    global _CURRENT_PROFILE
    for r in MODEL_ROLES:
        env_key = f"FRONTEND_AGENT_MODEL_{r.upper()}"
        if env_key in os.environ:
            del os.environ[env_key]
    app_config.MODEL_NAME = default_model or app_config.MODEL_NAME
    os.environ["FRONTEND_AGENT_MODEL"] = app_config.MODEL_NAME
    _CURRENT_PROFILE = ModelProfile.BALANCED


def get_current_profile() -> ModelProfile:
    return _CURRENT_PROFILE


def apply_profile(profile_name: str | ModelProfile) -> dict[str, str]:
    """Apply a preset model profile (FAST, BALANCED, QUALITY)."""
    global _CURRENT_PROFILE
    prof = ModelProfile(profile_name.upper()) if isinstance(profile_name, str) else profile_name
    discovered = discover_models()
    model_names = [m.name for m in discovered]

    def pick_best(candidates: list[str], fallback: str) -> str:
        for cand in candidates:
            for name in model_names:
                if cand.lower() in name.lower():
                    return name
        return fallback

    if prof == ModelProfile.FAST:
        fast_small = pick_best(["4b", "8b", "mini"], app_config.MODEL_NAME)
        for r in MODEL_ROLES:
            os.environ[f"FRONTEND_AGENT_MODEL_{r.upper()}"] = fast_small
        _CURRENT_PROFILE = ModelProfile.FAST

    elif prof == ModelProfile.QUALITY:
        coder_strong = pick_best(["coder", "14b", "32b", "latest"], app_config.MODEL_NAME)
        eval_model = pick_best(["8b", "14b", "latest"], app_config.MODEL_NAME)
        for r in ("planner", "architect", "component", "repair"):
            os.environ[f"FRONTEND_AGENT_MODEL_{r.upper()}"] = coder_strong
        for r in ("reviewer", "clarifier", "deliberation"):
            os.environ[f"FRONTEND_AGENT_MODEL_{r.upper()}"] = eval_model
        _CURRENT_PROFILE = ModelProfile.QUALITY

    else:  # BALANCED
        coder_model = pick_best(["coder", "latest"], app_config.MODEL_NAME)
        light_model = pick_best(["8b", "4b", "latest"], app_config.MODEL_NAME)
        os.environ["FRONTEND_AGENT_MODEL_PLANNER"] = light_model
        os.environ["FRONTEND_AGENT_MODEL_ARCHITECT"] = coder_model
        os.environ["FRONTEND_AGENT_MODEL_COMPONENT"] = coder_model
        os.environ["FRONTEND_AGENT_MODEL_REVIEWER"] = light_model
        os.environ["FRONTEND_AGENT_MODEL_REPAIR"] = coder_model
        os.environ["FRONTEND_AGENT_MODEL_CLARIFIER"] = light_model
        os.environ["FRONTEND_AGENT_MODEL_DELIBERATION"] = light_model
        _CURRENT_PROFILE = ModelProfile.BALANCED

    return get_role_routing()
