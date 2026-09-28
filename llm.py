"""Provider-neutral model entry point shared by the graph and the local studio."""
from __future__ import annotations

import os
from typing import Any

import httpx
from langchain_ollama import ChatOllama
from langchain_core.messages import SystemMessage
from langchain_core.runnables import RunnableLambda
import config as app_config

_MODEL_CACHE: dict[tuple, Any] = {}
_run_provider: str | None = None
_run_model: str | None = None


def configure_run(provider: str, model: str) -> None:
    """Called inside an isolated worker, never shared across concurrent projects."""
    global _run_provider, _run_model
    if provider not in {"ollama", "compatible"}:
        raise ValueError("Unknown model provider")
    _run_provider, _run_model = provider, model


def provider_settings(provider: str) -> dict[str, str]:
    if provider == "ollama":
        return {"id": provider, "label": "Ollama · local", "url": os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/"), "model": app_config.MODEL_NAME, "mode": "json_schema"}
    if provider == "compatible":
        return {"id": provider, "label": "Compatible API", "url": os.getenv("FRONTEND_AGENT_API_BASE_URL", "").rstrip("/"), "model": os.getenv("FRONTEND_AGENT_API_MODEL", ""), "mode": os.getenv("FRONTEND_AGENT_STRUCTURED_OUTPUT", "json_mode")}
    raise ValueError("Unknown provider")


def discover_providers() -> list[dict[str, Any]]:
    results = []
    for provider in ("ollama", "compatible"):
        spec = provider_settings(provider)
        result = {"id": provider, "label": spec["label"], "configured": bool(spec["url"]), "available": False, "models": [], "default_model": spec["model"], "structured_output": spec["mode"]}
        if not spec["url"]:
            result["error"] = "Set FRONTEND_AGENT_API_BASE_URL and optionally FRONTEND_AGENT_API_KEY on the server."
        else:
            try:
                headers = {}
                if provider == "compatible" and os.getenv("FRONTEND_AGENT_API_KEY"):
                    headers["Authorization"] = "Bearer " + os.environ["FRONTEND_AGENT_API_KEY"]
                response = httpx.get(spec["url"] + ("/api/tags" if provider == "ollama" else "/models"), headers=headers, timeout=3)
                response.raise_for_status()
                body = response.json()
                result["models"] = [m["name"] for m in body.get("models", [])] if provider == "ollama" else [m["id"] for m in body.get("data", [])]
                result["available"] = True
            except Exception:
                result["error"] = "Model discovery unavailable. Check the endpoint, credentials, and service. A configured model can still be entered manually."
        results.append(result)
    return results


class CompatibleModel:
    def __init__(self, client, mode: str):
        self.client, self.mode, self.model = client, mode, client.model_name

    def invoke(self, messages):
        return self.client.invoke(messages)

    def with_structured_output(self, schema):
        if self.mode in {"json_schema", "function_calling"}:
            return self.client.with_structured_output(schema, method=self.mode)
        if self.mode != "json_mode":
            raise ValueError("FRONTEND_AGENT_STRUCTURED_OUTPUT must be json_mode, json_schema, or function_calling")
        # JSON mode does not enforce a schema: provide it explicitly and validate locally.
        import json
        instruction = SystemMessage(content="Return only a JSON object matching this JSON schema: " + json.dumps(schema.model_json_schema()))
        client = self.client.bind(response_format={"type": "json_object"})
        def invoke(messages):
            response = client.invoke([instruction, *messages])
            return schema.model_validate_json(response.content)
        return RunnableLambda(invoke)


def make_model(*, model_name: str | None = None, temperature: float = 0.2, json_mode: bool = False):
    provider = _run_provider or os.getenv("FRONTEND_AGENT_PROVIDER", "ollama")
    spec = provider_settings(provider)
    name = _run_model or model_name or spec["model"]
    if not name:
        raise ValueError("Select a model before starting generation")
    key = (provider, spec["url"], name, temperature, json_mode, spec["mode"])
    if key not in _MODEL_CACHE:
        if provider == "ollama":
            kwargs = {"model": name, "base_url": spec["url"], "temperature": temperature, "validate_model_on_init": False, "client_kwargs": {"timeout": 180}, "num_ctx": int(os.getenv("FRONTEND_AGENT_CONTEXT_LENGTH", "16384")), "num_predict": int(os.getenv("FRONTEND_AGENT_MAX_TOKENS", "8192"))}
            reasoning = os.getenv("FRONTEND_AGENT_REASONING", "false" if name.startswith("qwen3") else "")
            if reasoning:
                kwargs["reasoning"] = reasoning.lower() == "true"
            if json_mode:
                kwargs["format"] = "json"
            _MODEL_CACHE[key] = ChatOllama(**kwargs)
        else:
            if not spec["url"]:
                raise ValueError("Compatible provider is not configured on the server")
            from langchain_openai import ChatOpenAI
            client = ChatOpenAI(model=name, base_url=spec["url"], api_key=os.getenv("FRONTEND_AGENT_API_KEY", "local"), temperature=temperature, timeout=180, max_retries=1, use_responses_api=False)
            _MODEL_CACHE[key] = CompatibleModel(client, spec["mode"])
    return _MODEL_CACHE[key]


def model_identity(model) -> str:
    provider = _run_provider or os.getenv("FRONTEND_AGENT_PROVIDER", "ollama")
    return f"{provider}:{provider_settings(provider)['url']}:{model.model}"


def make_model_for_role(role: str, *, temperature: float | None = None, json_mode: bool = False):
    cli_role = {"component_generation": "component", "clarification": "clarifier", "review": "reviewer"}.get(role, role)
    role_model = os.getenv(f"FRONTEND_AGENT_MODEL_{cli_role.upper()}") or os.getenv(f"FRONTEND_AGENT_MODEL_{role.upper()}")
    temperatures = {"clarification": 0.1, "planner": 0.1, "architect": 0.1, "review": 0.0, "repair": 0.1}
    return make_model(model_name=role_model, temperature=temperature if temperature is not None else temperatures.get(role, 0.2), json_mode=json_mode)
