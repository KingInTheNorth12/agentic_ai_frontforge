from __future__ import annotations

import json
from typing import Any

try:
    from cli.events import emit, EventType
except ImportError:
    emit = None  # type: ignore
    EventType = None  # type: ignore


MINIMAL_INVARIANTS = """
1. Export the component as default export matching exact component name.
2. Use modern React 18 functional components with hooks.
3. Import only from declared locked dependencies or valid relative local paths.
4. Output valid, clean code without placeholder or TODO comments.
"""


def compile_component_context(
    component_spec: dict[str, Any],
    blueprint: dict[str, Any] | None = None,
    locked_versions: dict[str, str] | None = None,
    rag_chunks: list[dict[str, Any]] | None = None,
    styling: str = "CSS",
) -> str:
    """Compile minimal, focused prompt context for component generation.

    Deliberately excludes unneeded Planner JSON, full Blueprint tree, or complete database memory.
    """
    comp_name = component_spec.get("name", "Component")
    blueprint_dict = blueprint or {}
    versions = locked_versions or {}

    # Extract allowed local component files from blueprint
    fs = blueprint_dict.get("filesystem", [])
    local_files = [f.get("path") for f in fs if isinstance(f, dict) and f.get("kind") == "file"]

    # Extract relevant routes
    routes = [r for r in blueprint_dict.get("routes", []) if isinstance(r, dict)]
    rel_routes = [r for r in routes if comp_name.lower() in str(r.get("component", "")).lower()]

    # Extract relevant RAG documentation chunks
    docs_summary = []
    if rag_chunks:
        for chunk in rag_chunks[:3]:
            content = chunk.get("content", str(chunk))
            docs_summary.append(content[:500])

    compiled_payload = {
        "invariants": MINIMAL_INVARIANTS.strip(),
        "target_component": {
            "name": comp_name,
            "description": component_spec.get("description", ""),
            "props": component_spec.get("props", []),
            "children": component_spec.get("children", []),
        },
        "styling_strategy": styling,
        "available_local_files": local_files[:20],
        "relevant_routes": rel_routes,
        "exact_dependency_versions": versions,
        "reference_docs_snippets": docs_summary,
    }

    compiled_text = json.dumps(compiled_payload, indent=2, ensure_ascii=False)

    # Calculate uncompiled context size (simulated full blueprint + planner dump)
    uncompiled_text = json.dumps({
        "blueprint": blueprint_dict,
        "component_spec": component_spec,
        "rag_chunks": rag_chunks,
        "full_dependency_tree": versions,
    }, ensure_ascii=False)

    if emit is not None and EventType is not None:
        emit(
            EventType.CONTEXT_COMPILED,
            component=comp_name,
            compiled_bytes=len(compiled_text),
            uncompiled_bytes=len(uncompiled_text),
        )

    return compiled_text
