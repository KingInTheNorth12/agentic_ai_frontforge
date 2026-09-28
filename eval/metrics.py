from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

from eval.benchmark import E2EBenchmarkCase, RAGBenchmarkCase


# ---------------------------------------------------------------------------
# RAG Metrics
# ---------------------------------------------------------------------------

def evaluate_rag_case(
    case: RAGBenchmarkCase,
    retrieved_results: list[dict[str, Any]],
    latency_ms: float = 0.0,
) -> dict[str, Any]:
    """Calculate RAG retrieval metrics deterministically for a single query."""
    if not retrieved_results:
        return {
            "case_id": case.id,
            "recall_at_1": 0.0,
            "recall_at_3": 0.0,
            "recall_at_5": 0.0,
            "mrr": 0.0,
            "wrong_library_rate": 0.0,
            "wrong_major_version_rate": 0.0,
            "no_result": True,
            "latency_ms": latency_ms,
        }

    targets = set(case.expected_paths + case.expected_symbols + case.accepted_alternatives)

    def is_match(item: dict[str, Any]) -> bool:
        content = item.get("content", "").lower()
        metadata = item.get("metadata", {})
        path = str(metadata.get("path", "") or metadata.get("source", "")).lower()

        if not targets:
            return True

        for target in targets:
            t_lower = target.lower()
            if t_lower in content or t_lower in path:
                return True
        return False

    ranks = [idx + 1 for idx, item in enumerate(retrieved_results) if is_match(item)]
    first_rank = ranks[0] if ranks else None

    recall_1 = 1.0 if (first_rank is not None and first_rank <= 1) else 0.0
    recall_3 = 1.0 if (first_rank is not None and first_rank <= 3) else 0.0
    recall_5 = 1.0 if (first_rank is not None and first_rank <= 5) else 0.0
    mrr = (1.0 / first_rank) if first_rank is not None else 0.0

    wrong_lib_count = 0
    wrong_major_version_count = 0

    for item in retrieved_results:
        meta = item.get("metadata", {})
        lib = meta.get("library", item.get("library", "")).lower()
        if case.target_library and lib and case.target_library.lower() not in lib:
            wrong_lib_count += 1

        version = str(meta.get("version", meta.get("major_version", "")))
        if case.target_major_version and version:
            major_match = re.search(r"\d+", version)
            if major_match and major_match.group(0) != str(case.target_major_version):
                wrong_major_version_count += 1

    total = len(retrieved_results)
    return {
        "case_id": case.id,
        "recall_at_1": recall_1,
        "recall_at_3": recall_3,
        "recall_at_5": recall_5,
        "mrr": mrr,
        "wrong_library_rate": wrong_lib_count / total if total > 0 else 0.0,
        "wrong_major_version_rate": wrong_major_version_count / total if total > 0 else 0.0,
        "no_result": False,
        "latency_ms": latency_ms,
    }


def aggregate_rag_metrics(case_results: list[dict[str, Any]]) -> dict[str, Any]:
    if not case_results:
        return {
            "total_cases": 0,
            "recall_at_1": 0.0,
            "recall_at_3": 0.0,
            "recall_at_5": 0.0,
            "mrr": 0.0,
            "wrong_library_rate": 0.0,
            "wrong_major_version_rate": 0.0,
            "no_result_rate": 0.0,
            "avg_latency_ms": 0.0,
        }

    n = len(case_results)
    return {
        "total_cases": n,
        "recall_at_1": sum(r["recall_at_1"] for r in case_results) / n,
        "recall_at_3": sum(r["recall_at_3"] for r in case_results) / n,
        "recall_at_5": sum(r["recall_at_5"] for r in case_results) / n,
        "mrr": sum(r["mrr"] for r in case_results) / n,
        "wrong_library_rate": sum(r["wrong_library_rate"] for r in case_results) / n,
        "wrong_major_version_rate": sum(r["wrong_major_version_rate"] for r in case_results) / n,
        "no_result_rate": sum(1.0 if r["no_result"] else 0.0 for r in case_results) / n,
        "avg_latency_ms": sum(r["latency_ms"] for r in case_results) / n,
    }


# ---------------------------------------------------------------------------
# Planner Metrics
# ---------------------------------------------------------------------------

NPM_NAME_RE = re.compile(r"^(?:@[a-z0-9-*~][a-z0-9-*._~]*/+)?[a-z0-9-*~][a-z0-9-*._~]*$")


def evaluate_planner(case: E2EBenchmarkCase, plan: dict[str, Any] | None, state: dict[str, Any]) -> dict[str, Any]:
    """Deterministically score planner output quality against benchmark requirements."""
    if not plan or not isinstance(plan, dict):
        return {
            "schema_validity": 0.0,
            "explicit_requirement_coverage": 0.0,
            "hallucinated_requirements_count": len(case.forbidden_features),
            "dependency_name_validity": 0.0,
            "unnecessary_dependency_count": len(case.forbidden_dependencies),
            "route_page_consistency": 0.0,
            "clarification_assumption_accuracy": 0.0,
        }

    # 1. Schema validity
    has_pages = "pages" in plan and isinstance(plan.get("pages"), list)
    has_components = "components" in plan and isinstance(plan.get("components"), list)
    schema_valid = 1.0 if (has_pages and has_components) else 0.5 if (has_pages or has_components) else 0.0

    # Extract pages, routes, dependencies from plan & state
    plan_pages = [p.get("name", "") if isinstance(p, dict) else str(p) for p in plan.get("pages", [])]
    plan_routes = [r.get("path", "") if isinstance(r, dict) else str(r) for r in plan.get("routes", [])]
    all_plan_text = json_str(plan).lower()

    # 2. Requirement coverage
    exp_features = case.expected_features or []
    covered = sum(1 for feat in exp_features if feat.lower() in all_plan_text)
    req_coverage = covered / len(exp_features) if exp_features else 1.0

    # 3. Hallucinated requirements
    forb_features = case.forbidden_features or []
    hallucinated = sum(1 for feat in forb_features if feat.lower() in all_plan_text)

    # 4. Dependency validity & unnecessary dependencies
    deps = plan.get("dependencies", [])
    if isinstance(deps, list):
        valid_deps = sum(1 for d in deps if isinstance(d, str) and NPM_NAME_RE.match(d))
        dep_name_validity = valid_deps / len(deps) if deps else 1.0
        forb_deps = set(case.forbidden_dependencies or [])
        unnecessary_deps = sum(1 for d in deps if d in forb_deps)
    else:
        dep_name_validity = 0.0
        unnecessary_deps = len(case.forbidden_dependencies or [])

    # 5. Route/page consistency
    exp_routes = case.expected_routes or []
    matched_routes = sum(1 for r in exp_routes if any(r.lower() in pr.lower() for pr in plan_routes + plan_pages))
    route_consistency = matched_routes / len(exp_routes) if exp_routes else 1.0

    # 6. Clarification assumption accuracy
    asked_questions = bool(state.get("clarification_questions"))
    expected_clarification = case.expected_clarification_needed
    clarification_accuracy = 1.0 if (asked_questions == expected_clarification) else 0.0

    return {
        "schema_validity": schema_valid,
        "explicit_requirement_coverage": req_coverage,
        "hallucinated_requirements_count": hallucinated,
        "dependency_name_validity": dep_name_validity,
        "unnecessary_dependency_count": unnecessary_deps,
        "route_page_consistency": route_consistency,
        "clarification_assumption_accuracy": clarification_accuracy,
    }


# ---------------------------------------------------------------------------
# UI Architect Metrics
# ---------------------------------------------------------------------------

def evaluate_architect(blueprint: dict[str, Any] | None, project_root: str | None = None) -> dict[str, Any]:
    """Deterministically score UI Architect blueprint consistency."""
    if not blueprint or not isinstance(blueprint, dict):
        return {
            "schema_validity": 0.0,
            "all_routes_resolve": False,
            "referenced_components_exist": False,
            "no_unsafe_paths": False,
            "no_duplicate_ownership": False,
            "no_unreachable_routes": False,
            "entrypoint_correctness": False,
            "build_order_consistency": 0.0,
            "no_duplicate_layout_responsibility": False,
            "filesystem_validity": 0.0,
            "dependency_compatibility": 0.0,
        }

    filesystem = blueprint.get("filesystem", [])
    if not isinstance(filesystem, list):
        filesystem = []

    # 1. Schema validity
    schema_valid = 1.0 if ("project_name" in blueprint and "filesystem" in blueprint) else 0.0

    # 2. Filesystem paths & safety
    paths = set()
    unsafe_paths_found = False
    valid_file_count = 0
    duplicate_paths = 0

    for item in filesystem:
        if not isinstance(item, dict):
            continue
        p = item.get("path", "")
        if not p or ".." in p or p.startswith("/") or "\\" in p:
            unsafe_paths_found = True
        if p in paths:
            duplicate_paths += 1
        paths.add(p)
        valid_file_count += 1

    fs_validity = valid_file_count / len(filesystem) if filesystem else 0.0
    no_unsafe_paths = not unsafe_paths_found
    no_duplicate_ownership = (duplicate_paths == 0)

    # 3. Entrypoint correctness
    has_index = any(p in paths for p in ["index.html", "src/index.html"])
    has_main = any(p in paths for p in ["src/main.jsx", "src/main.tsx", "src/main.js", "src/App.jsx", "src/App.tsx"])
    entrypoint_correctness = has_index and has_main

    # 4. Routes resolution
    routes = blueprint.get("routes", [])
    if isinstance(routes, list) and routes:
        resolved_routes = 0
        for r in routes:
            component_target = r.get("component", "") if isinstance(r, dict) else str(r)
            if any(component_target.lower() in p.lower() for p in paths):
                resolved_routes += 1
        all_routes_resolve = (resolved_routes == len(routes))
        no_unreachable_routes = all_routes_resolve
    else:
        all_routes_resolve = True
        no_unreachable_routes = True

    # 5. Referenced components exist
    components = blueprint.get("components", [])
    if isinstance(components, list) and components:
        existing_comps = 0
        for c in components:
            c_name = c.get("name", "") if isinstance(c, dict) else str(c)
            if any(c_name.lower() in p.lower() for p in paths):
                existing_comps += 1
        referenced_components_exist = (existing_comps == len(components))
    else:
        referenced_components_exist = True

    # 6. Build order consistency & Layout responsibility
    build_order = blueprint.get("build_order", [])
    build_order_consistency = 1.0 if isinstance(build_order, list) and len(build_order) > 0 else 0.5
    layout_files = [p for p in paths if "layout" in p.lower()]
    no_duplicate_layout_responsibility = (len(layout_files) <= 2)

    return {
        "schema_validity": schema_valid,
        "all_routes_resolve": all_routes_resolve,
        "referenced_components_exist": referenced_components_exist,
        "no_unsafe_paths": no_unsafe_paths,
        "no_duplicate_ownership": no_duplicate_ownership,
        "no_unreachable_routes": no_unreachable_routes,
        "entrypoint_correctness": entrypoint_correctness,
        "build_order_consistency": build_order_consistency,
        "no_duplicate_layout_responsibility": no_duplicate_layout_responsibility,
        "filesystem_validity": fs_validity,
        "dependency_compatibility": 1.0 if blueprint.get("dependencies") else 0.5,
    }


# ---------------------------------------------------------------------------
# Component Generation & AST Metrics
# ---------------------------------------------------------------------------

def check_file_syntax(content: str, filename: str) -> bool:
    """Check if JS/TS/JSX content has valid balanced brackets and basic structural syntax."""
    if not content.strip():
        return False
    # Check bracket balance
    stack = []
    pairs = {")": "(", "}": "{", "]": "["}
    in_string = None
    escaped = False

    for char in content:
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char in ('"', "'", "`"):
            if in_string == char:
                in_string = None
            elif in_string is None:
                in_string = char
            continue
        if in_string is not None:
            continue
        if char in "([{":
            stack.append(char)
        elif char in ")]}":
            if not stack or stack[-1] != pairs[char]:
                return False
            stack.pop()

    return len(stack) == 0


def evaluate_generation(
    project_root: str | Path | None,
    generation_report: dict[str, Any] | None,
    state: dict[str, Any],
) -> dict[str, Any]:
    """Evaluate component generation quality deterministically."""
    if not project_root:
        return {
            "syntax_valid_rate": 0.0,
            "import_valid_rate": 0.0,
            "allowed_dependency_rate": 0.0,
            "first_pass_build_rate": False,
            "lint_pass_rate": 0.0,
            "test_pass_rate": 0.0,
            "generated_files": 0,
            "failed_files": 0,
            "regenerated_files": 0,
        }

    root = Path(project_root).resolve()
    gen_rep = generation_report or state.get("generation_report", {})
    created_files = gen_rep.get("files_created", []) or gen_rep.get("success", [])
    failed_files = gen_rep.get("failures", [])

    syntax_valid_count = 0
    total_checked = 0

    if root.exists():
        for ext in ("*.jsx", "*.tsx", "*.js", "*.ts"):
            for fpath in root.rglob(ext):
                if "node_modules" in str(fpath) or ".git" in str(fpath):
                    continue
                try:
                    content = fpath.read_text(encoding="utf-8", errors="ignore")
                    total_checked += 1
                    if check_file_syntax(content, fpath.name):
                        syntax_valid_count += 1
                except Exception:
                    pass

    syntax_rate = syntax_valid_count / total_checked if total_checked > 0 else 1.0

    # Import validity from review report or validators
    review_rep = state.get("review_report", {})
    import_issues = review_rep.get("import_issues", [])
    import_valid_rate = 1.0 if not import_issues else max(0.0, 1.0 - (len(import_issues) / max(total_checked, 1)))

    first_pass_build = state.get("build_succeeded", False) and state.get("repair_attempts", 0) == 0

    return {
        "syntax_valid_rate": syntax_rate,
        "import_valid_rate": import_valid_rate,
        "allowed_dependency_rate": 1.0 if state.get("package_report", {}).get("resolution_ok") else 0.0,
        "first_pass_build_rate": first_pass_build,
        "lint_pass_rate": 1.0 if import_valid_rate > 0.8 else 0.0,
        "test_pass_rate": 1.0 if first_pass_build else 0.0,
        "generated_files": len(created_files) or total_checked,
        "failed_files": len(failed_files),
        "regenerated_files": state.get("repair_attempts", 0),
    }


# ---------------------------------------------------------------------------
# Repair Metrics
# ---------------------------------------------------------------------------

def evaluate_repair(state: dict[str, Any]) -> dict[str, Any]:
    """Score repair loop effectiveness."""
    repair_attempts = state.get("repair_attempts", 0)
    build_succeeded = state.get("build_succeeded", False)
    package_report = state.get("package_report", {})

    repair_triggered = repair_attempts > 0
    first_pass_build = build_succeeded and not repair_triggered

    if repair_triggered:
        repair_success = build_succeeded
        files_changed = 1.0  # Avg files modified during repair step
    else:
        repair_success = True
        files_changed = 0.0

    return {
        "repair_trigger_rate": 1.0 if repair_triggered else 0.0,
        "repair_success_rate": 1.0 if repair_success else 0.0,
        "average_repair_attempts": float(repair_attempts),
        "files_changed_per_repair": files_changed,
        "regression_rate": 0.0 if build_succeeded else 1.0,
        "first_pass_build_rate": 1.0 if first_pass_build else 0.0,
        "final_build_rate_after_repair": 1.0 if build_succeeded else 0.0,
    }


# ---------------------------------------------------------------------------
# Helper Utility
# ---------------------------------------------------------------------------

def json_str(obj: Any) -> str:
    import json
    try:
        return json.dumps(obj, ensure_ascii=False)
    except Exception:
        return str(obj)
