from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from eval.benchmark import (
    E2EBenchmarkCase,
    RAGBenchmarkCase,
    load_e2e_benchmark_suite,
    load_rag_benchmark_suite,
)
from eval.metrics import (
    aggregate_rag_metrics,
    evaluate_architect,
    evaluate_generation,
    evaluate_planner,
    evaluate_rag_case,
    evaluate_repair,
)


class RAGEvaluator:
    """Evaluator for documentation retrieval accuracy and version safety."""

    def __init__(self, suite: list[RAGBenchmarkCase] | None = None):
        self.suite = suite or load_rag_benchmark_suite()

    def run_evaluation(
        self,
        retriever_func: Callable[[str, str | None], list[dict[str, Any]]] | None = None,
        config_name: str = "dense_default",
    ) -> dict[str, Any]:
        case_results = []
        start_suite_time = time.time()

        for case in self.suite:
            start_case = time.time()
            results = []
            if retriever_func is not None:
                try:
                    results = retriever_func(case.query, case.target_library)
                except Exception as exc:
                    results = [{"error": str(exc)}]
            else:
                # Fallback mock/simulated retrieval for standalone testing
                results = self._mock_retrieval(case)

            latency = (time.time() - start_case) * 1000.0
            case_eval = evaluate_rag_case(case, results, latency_ms=latency)
            case_results.append(case_eval)

        aggregated = aggregate_rag_metrics(case_results)
        aggregated["config_name"] = config_name
        aggregated["total_suite_latency_s"] = time.time() - start_suite_time
        aggregated["case_details"] = case_results
        return aggregated

    def _mock_retrieval(self, case: RAGBenchmarkCase) -> list[dict[str, Any]]:
        # High quality baseline match simulation for test mode
        return [
            {
                "content": f"Documentation chunk for {case.query} in {case.target_library}. {case.expected_symbols[0] if case.expected_symbols else ''}",
                "metadata": {
                    "library": case.target_library,
                    "version": f"{case.target_major_version}.0.0",
                    "major_version": case.target_major_version,
                    "path": case.expected_paths[0] if case.expected_paths else "docs/index.md",
                },
            }
        ]


class E2EEvaluator:
    """Evaluator for full end-to-end multi-agent pipeline and subsystem nodes."""

    def __init__(self, suite: list[E2EBenchmarkCase] | None = None):
        self.suite = suite or load_e2e_benchmark_suite()

    def run_case(
        self,
        case: E2EBenchmarkCase,
        runner_func: Callable[[str, str], dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        run_id = f"eval-run-{uuid.uuid4().hex[:8]}"
        start_time = time.time()

        node_latencies: dict[str, float] = {}
        llm_calls = 0
        state: dict[str, Any] = {}
        build_succeeded = False
        error_msg = None

        if runner_func is not None:
            try:
                state = runner_func(case.prompt, case.id)
                build_succeeded = state.get("build_succeeded", False)
            except Exception as exc:
                error_msg = str(exc)
                state = {"phase": "errored", "errors": [{"message": error_msg}]}
        else:
            # Deterministic simulation baseline for testing
            state = self._mock_e2e_run(case)
            build_succeeded = state.get("build_succeeded", False)

        wall_clock = time.time() - start_time

        # Calculate subsystem metrics
        plan_eval = evaluate_planner(case, state.get("plan"), state)
        architect_eval = evaluate_architect(state.get("blueprint"), state.get("project_root"))
        gen_eval = evaluate_generation(state.get("project_root"), state.get("generation_report"), state)
        repair_eval = evaluate_repair(state)

        first_pass_build = gen_eval.get("first_pass_build_rate", False)

        return {
            "run_id": run_id,
            "case_id": case.id,
            "difficulty": case.difficulty,
            "styling": case.styling,
            "tags": case.tags,
            "prompt": case.prompt,
            "build_succeeded": build_succeeded,
            "first_pass_build_succeeded": bool(first_pass_build),
            "planner_metrics": plan_eval,
            "architect_metrics": architect_eval,
            "generation_metrics": gen_eval,
            "repair_metrics": repair_eval,
            "telemetry": {
                "wall_clock_latency_s": wall_clock,
                "per_node_latency_s": node_latencies,
                "llm_calls": llm_calls,
                "build_attempts": 1 + state.get("repair_attempts", 0),
                "repair_attempts": state.get("repair_attempts", 0),
                "peak_memory_mb": 128.0,
            },
            "state_snapshot": {
                "phase": state.get("phase"),
                "project_root": state.get("project_root"),
                "plan": state.get("plan"),
                "blueprint": state.get("blueprint"),
                "errors": state.get("errors", []),
            },
            "error": error_msg,
        }

    def run_suite(
        self,
        runner_func: Callable[[str, str], dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        results = []
        for case in self.suite:
            results.append(self.run_case(case, runner_func))

        total = len(results)
        first_pass_pass = sum(1 for r in results if r["first_pass_build_succeeded"])
        final_pass = sum(1 for r in results if r["build_succeeded"])

        return {
            "total_cases": total,
            "first_pass_build_rate": first_pass_pass / total if total > 0 else 0.0,
            "final_build_rate": final_pass / total if total > 0 else 0.0,
            "avg_latency_s": sum(r["telemetry"]["wall_clock_latency_s"] for r in results) / total if total > 0 else 0.0,
            "case_results": results,
        }

    def _mock_e2e_run(self, case: E2EBenchmarkCase) -> dict[str, Any]:
        """Baseline deterministic simulation run."""
        routes = [{"path": r, "component": "PageComponent"} for r in case.expected_routes]
        pages = [{"name": f"Page_{idx}", "deps": []} for idx, r in enumerate(case.expected_routes)]

        return {
            "phase": "complete",
            "build_succeeded": True,
            "repair_attempts": 0,
            "project_root": "/tmp/mock-project",
            "plan": {
                "pages": pages,
                "components": [{"name": "Header", "deps": []}],
                "routes": routes,
                "dependencies": case.required_dependencies,
            },
            "blueprint": {
                "project_name": case.id,
                "routes": routes,
                "components": [{"name": "Header"}],
                "build_order": ["src/App.jsx"],
                "dependencies": case.required_dependencies,
                "filesystem": [
                    {"kind": "file", "path": "index.html"},
                    {"kind": "file", "path": "src/main.jsx"},
                    {"kind": "file", "path": "src/App.jsx"},
                ],
            },
            "generation_report": {
                "files_created": ["index.html", "src/main.jsx", "src/App.jsx"],
                "failures": [],
            },
            "package_report": {
                "resolution_ok": True,
                "locked_versions": {dep: "1.0.0" for dep in case.required_dependencies},
            },
            "clarification_questions": [{"key": "q1"}] if case.expected_clarification_needed else [],
        }
