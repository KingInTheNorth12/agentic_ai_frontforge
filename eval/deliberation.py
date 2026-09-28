from __future__ import annotations

import time
from typing import Any, Callable, Literal

DeliberationMode = Literal["OFF", "LIGHT", "DEEP", "AUTO"]


class DeliberationEvaluator:
    """Evaluates deliberation performance and candidate selection across modes."""

    def __init__(self, mode: DeliberationMode = "AUTO"):
        self.mode = mode

    def evaluate_candidates(
        self,
        node_name: str,
        candidates: list[dict[str, Any]],
        candidate_eval_func: Callable[[dict[str, Any]], float],
        *,
        mode: DeliberationMode | None = None,
    ) -> dict[str, Any]:
        eval_mode = mode or self.mode
        start_time = time.time()

        if eval_mode == "OFF" or not candidates:
            return {
                "deliberation_mode": eval_mode,
                "node_type": node_name,
                "candidate_count": len(candidates),
                "winning_candidate_index": 0 if candidates else -1,
                "deterministic_selection_used": True,
                "semantic_evaluator_used": False,
                "synthesis_occurred": False,
                "build_rate_per_candidate": [1.0] if candidates else [],
                "additional_latency_s": 0.0,
                "additional_model_calls": 0,
                "selected_candidate": candidates[0] if candidates else None,
            }

        scores = []
        for idx, cand in enumerate(candidates):
            score = candidate_eval_func(cand)
            scores.append((score, idx))

        scores.sort(key=lambda x: x[0], reverse=True)
        winner_score, winner_idx = scores[0]

        # Determine if semantic evaluator or synthesis was needed
        needs_semantic = (eval_mode in ("DEEP", "AUTO")) and len(scores) > 1 and (scores[0][0] == scores[1][0])
        synthesis = (eval_mode == "DEEP") and len(candidates) > 2

        latency = time.time() - start_time
        additional_calls = len(candidates) - 1 if eval_mode != "OFF" else 0

        return {
            "deliberation_mode": eval_mode,
            "node_type": node_name,
            "candidate_count": len(candidates),
            "winning_candidate_index": winner_idx,
            "winning_score": winner_score,
            "deterministic_selection_used": not needs_semantic,
            "semantic_evaluator_used": needs_semantic,
            "synthesis_occurred": synthesis,
            "build_rate_per_candidate": [s[0] for s in scores],
            "additional_latency_s": latency,
            "additional_model_calls": additional_calls,
            "selected_candidate": candidates[winner_idx],
        }


def compare_deliberation_modes(
    benchmark_cases: list[Any],
    runner_factory: Callable[[DeliberationMode], Any],
) -> dict[str, dict[str, Any]]:
    """Compare OFF, LIGHT, DEEP, AUTO deliberation modes on the same benchmark dataset."""
    results: dict[str, dict[str, Any]] = {}
    modes: list[DeliberationMode] = ["OFF", "LIGHT", "DEEP", "AUTO"]

    for mode in modes:
        evaluator = runner_factory(mode)
        start_time = time.time()

        # Run benchmark under mode
        suite_res = evaluator.run_suite()
        suite_res["total_time_s"] = time.time() - start_time
        suite_res["mode"] = mode

        results[mode] = {
            "mode": mode,
            "first_pass_build_rate": suite_res.get("first_pass_build_rate", 0.0),
            "final_build_rate": suite_res.get("final_build_rate", 0.0),
            "avg_latency_s": suite_res.get("avg_latency_s", 0.0),
            "total_time_s": suite_res["total_time_s"],
        }

    return results
