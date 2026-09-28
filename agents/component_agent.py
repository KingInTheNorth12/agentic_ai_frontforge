from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

try:
    from cli.events import emit, EventType
except ImportError:
    emit = None  # type: ignore
    EventType = None  # type: ignore

from langchain_core.messages import HumanMessage

from llm import make_model
from rag.retriever import retrieve_for_versions as retrieve
from state import ProjectState
from frameworks import generation_rules
from tools import read_text, safe_project_path, save_json, sha256_text, write_text


CODE_FENCE_RE = re.compile(r"```(?:jsx|tsx|javascript|js|css|html|vue|svelte)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


SYSTEM_PROMPT = """
You are the code generation stage of a frontend application pipeline.
Generate exactly one complete source file.

The architecture payload is authoritative.
The original request and clarification answers define the required behavior. Preserve their details; do not add product features beyond that request.

Rules:
- Return only the complete file content. No explanation and no markdown fences.
- Never import or reference a local file that is not present in allowed_local_imports.
- For a local import, use the exact import string supplied in allowed_local_imports.
- Never import an npm package outside allowed_packages.
- Do not add new pages, components, assets, hooks, utilities, providers, or packages.
- Do not create placeholders, TODO implementations, or truncated JSX.
- Keep responsibilities local to the requested file.
- Do not duplicate routing, root mounting, global layout, or providers.
- Use the requested styling strategy consistently.
- Do not use Tailwind utility classes unless Tailwind is explicitly configured. With plain CSS, define every visual class in the shared stylesheet or use inline styles.
- Existing source files establish actual exports and component props. Match those contracts; do not invent stores or shared state modules.
- Mock backend data only when the product requirement needs data but no backend exists.

The framework_rules in the payload override React-specific examples below.
Role constraints:
- entry: mount <App /> exactly once; no routes or page UI.
- app_root: compose the app shell and route component; never call createRoot.
- routes: define routing only from the supplied routes/pages.
- stylesheet: tokens, reset, and all shared/page/component classes used by the generated application.
- vite_config: configure Vite React only; no product UI.
- html_entry: minimal Vite HTML shell with #root and src/main.jsx.
"""


def _clean(raw: str) -> str:
    text = raw.strip()
    match = CODE_FENCE_RE.search(text)
    return (match.group(1) if match else text).strip()


def _relative_import(from_file: str, to_file: str) -> str:
    start = os.path.dirname(from_file) or "."
    rel = os.path.relpath(to_file, start=start).replace("\\", "/")
    root, ext = os.path.splitext(rel)
    if ext in {".js", ".jsx", ".ts", ".tsx"}:
        rel = root
    if not rel.startswith("."):
        rel = "./" + rel
    return rel


def _local_import_map(current_path: str, blueprint: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for fs_item in blueprint.get("filesystem", []):
        if not isinstance(fs_item, dict) or fs_item.get("kind") != "file":
            continue
        target = fs_item.get("path")
        if not target or target == current_path:
            continue
        if Path(target).suffix.lower() not in {".js", ".jsx", ".ts", ".tsx", ".css", ".vue", ".svelte"}:
            continue
        key = Path(target).stem
        # Keep path as fallback key too; duplicate basenames remain unambiguous via full path.
        result[target] = _relative_import(current_path, target)
        if key not in result:
            result[key] = result[target]
    return result


def _spec_for(path: str, blueprint: dict[str, Any]) -> dict[str, Any]:
    for item in blueprint.get("generation_items", []):
        if isinstance(item, dict) and item.get("path") == path:
            return item
    return {"path": path, "role": "source"}


def _rag_context(path: str, spec: dict[str, Any], blueprint: dict[str, Any], versions: dict[str, str]) -> str:
    query = f"{spec.get('role', 'frontend')} {Path(path).name} {blueprint.get('styling', {}).get('library', '')}"
    try:
        docs = retrieve(query, versions)
    except Exception:
        return ""
    snippets: list[str] = []
    for doc in docs:
        content = getattr(doc, "page_content", str(doc))
        if content:
            snippets.append(content)
    return "\n---\n".join(snippets[:4])


def _prompt_for(path: str, blueprint: dict[str, Any], plan: dict[str, Any], package_report: dict[str, Any], sources: dict[str, str] | None = None) -> str:
    spec = _spec_for(path, blueprint)
    payload = {
        "target_file": path,
        "file_role": spec.get("role") or spec.get("kind"),
        "file_spec": spec,
        "project_summary": blueprint.get("summary"),
        "framework": blueprint.get("framework"),
        "framework_rules": generation_rules(blueprint.get("framework", "react")),
        "styling": blueprint.get("styling"),
        "routes": blueprint.get("routes", []),
        "pages": blueprint.get("pages", []),
        "layouts": blueprint.get("layouts", []),
        "component_hierarchy": blueprint.get("component_hierarchy", {}),
        "allowed_packages": sorted(set(blueprint.get("dependencies", [])) | set(package_report.get("locked_versions", {}))),
        "resolved_package_versions": package_report.get("locked_versions", {}),
        "allowed_local_imports": _local_import_map(path, blueprint),
        "existing_sources": sources or {},
        "planner_context": {
            "original_request": plan.get("original_request"),
            "clarification_answers": plan.get("clarification_answers", {}),
            "constraints": plan.get("other", []),
            "purpose": plan.get("purpose"),
            "theme": plan.get("theme"),
            "responsive": plan.get("responsive"),
            "authentication": plan.get("authentication"),
            "key_sections": plan.get("key_sections"),
        },
        "rag_context": _rag_context(path, spec, blueprint, package_report.get("locked_versions", {})),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


from cache.content_cache import get_content_cache
from llm import make_model_for_role, model_identity


def run(state: ProjectState) -> dict[str, Any]:
    blueprint = state["blueprint"]
    plan = state["plan"]
    project_root = state["project_root"]
    model = make_model_for_role("component_generation")
    cache = get_content_cache()
    locked_versions = state.get("package_report", {}).get("locked_versions", {})
    styling = blueprint.get("styling", {}).get("library", "CSS")

    successes: list[dict[str, str]] = []
    failures: list[dict[str, str]] = []

    for path in blueprint.get("build_order", []):
        if path == "package.json":
            continue
        try:
            if emit is not None:
                emit(EventType.FILE_GENERATION_STARTED, path=path)
            # Ensure the requested path is inside the project before an LLM call.
            safe_project_path(project_root, path)
            spec = _spec_for(path, blueprint)
            # Components are generated before pages: pass their real interfaces forward.
            sources = {item["path"]: read_text(project_root, item["path"]) for item in successes}
            prompt = _prompt_for(path, blueprint, plan, state.get("package_report", {}), sources)

            # Compute content-addressable cache key
            cache_key = cache.compute_key(
                component_spec=spec,
                arch_context={"prompt": prompt, "system_prompt": SYSTEM_PROMPT},
                dependencies=locked_versions,
                styling=styling,
                model_name=model_identity(model),
            )

            cached_code = cache.get(cache_key)
            if cached_code is not None:
                # Cache Hit! Reuse previously generated code
                code = cached_code
                if emit is not None:
                    emit(EventType.CACHE_HIT, path=path, key=cache_key)
            else:
                # Cache miss: preserve the file-specific generation contract.
                if emit is not None:
                    emit(EventType.CACHE_MISS, path=path, key=cache_key)

                response = model.invoke([
                    {"role": "system", "content": SYSTEM_PROMPT},
                    HumanMessage(content=prompt),
                ])
                code = _clean(response.content)
                if not code:
                    raise ValueError("Model returned empty file content")
                cache.put(cache_key, code)

            write_text(project_root, path, code + "\n")
            if emit is not None:
                emit(EventType.FILE_GENERATED, path=path)
            successes.append({"path": path, "sha256": sha256_text(code)})
        except Exception as exc:
            if emit is not None:
                emit(EventType.FILE_GENERATION_FAILED, path=path, error=str(exc))
            failures.append({"path": path, "error": str(exc)})

    report = {"success": successes, "failed": failures}
    save_json(Path(project_root) / "component_agent_log.json", report)
    errors = list(state.get("errors", []))
    errors.extend({"stage": "generation", **item} for item in failures)
    return {
        "generation_report": report,
        "errors": errors,
        "phase": "generated",
    }
