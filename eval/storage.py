from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config as app_config

RESULTS_DIR = app_config.AGENT_DIR / "eval_results"
FAILURES_DIR = RESULTS_DIR / "failures"
FINE_TUNING_DIR = RESULTS_DIR / "fine_tuning"


def get_git_commit() -> str:
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if res.returncode == 0:
            return res.stdout.strip()
    except Exception:
        pass
    return "unknown"


def capture_run_context(
    run_id: str | None = None,
    benchmark_version: str = "1.0.0",
    deliberation_config: str = "AUTO",
) -> dict[str, Any]:
    return {
        "run_id": run_id or f"run-{int(time.time())}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "git_commit": get_git_commit(),
        "benchmark_version": benchmark_version,
        "model_name": app_config.MODEL_NAME,
        "embedding_model": app_config.EMBEDDING_MODEL,
        "rag_collection": app_config.RAG_COLLECTION,
        "deliberation_config": deliberation_config,
        "system_info": {
            "os": platform.platform(),
            "python_version": sys.version,
            "machine": platform.machine(),
        },
    }


class EvalStorage:
    """Storage manager for evaluation runs, summaries, failure artifacts, and fine-tuning datasets."""

    def __init__(self, base_dir: Path | None = None):
        self.base_dir = Path(base_dir) if base_dir is not None else app_config.AGENT_DIR / "eval_results"
        self.failures_dir = self.base_dir / "failures"
        self.fine_tuning_dir = self.base_dir / "fine_tuning"

        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.failures_dir.mkdir(parents=True, exist_ok=True)
        self.fine_tuning_dir.mkdir(parents=True, exist_ok=True)

    def save_run(
        self,
        run_data: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> tuple[Path, Path]:
        if not context:
            context = capture_run_context(run_id=run_data.get("run_id"))
        run_id = run_data.get("run_id") or context.get("run_id") or f"run-{int(time.time())}"
        run_ctx = context


        # 1. Save per-case JSONL stream
        jsonl_path = self.base_dir / f"{run_id}.jsonl"
        with open(jsonl_path, "w", encoding="utf-8") as f:
            for case_res in run_data.get("case_results", []):
                record = {
                    "run_id": run_id,
                    "timestamp": run_ctx["timestamp"],
                    "case": case_res,
                }
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

        # 2. Save summary JSON
        summary_path = self.base_dir / f"{run_id}_summary.json"
        summary_payload = {
            "context": run_ctx,
            "metrics": {
                "total_cases": run_data.get("total_cases", 0),
                "first_pass_build_rate": run_data.get("first_pass_build_rate", 0.0),
                "final_build_rate": run_data.get("final_build_rate", 0.0),
                "avg_latency_s": run_data.get("avg_latency_s", 0.0),
            },
            "cases_summary": [
                {
                    "case_id": c.get("case_id"),
                    "build_succeeded": c.get("build_succeeded"),
                    "first_pass": c.get("first_pass_build_succeeded"),
                    "latency_s": c.get("telemetry", {}).get("wall_clock_latency_s"),
                }
                for c in run_data.get("case_results", [])
            ],
        }
        summary_path.write_text(json.dumps(summary_payload, indent=2, ensure_ascii=False), encoding="utf-8")

        # 3. Save failure artifacts
        for c in run_data.get("case_results", []):
            if not c.get("build_succeeded"):
                self.save_failure_artifact(run_id, c)

        return jsonl_path, summary_path

    def save_failure_artifact(self, run_id: str, case_result: dict[str, Any]) -> Path:
        case_id = case_result.get("case_id", "unknown_case")
        case_dir = self.failures_dir / run_id
        case_dir.mkdir(parents=True, exist_ok=True)
        file_path = case_dir / f"{case_id}.json"

        snapshot = case_result.get("state_snapshot") or {}
        blueprint = snapshot.get("blueprint") or {}
        resolved_deps = blueprint.get("dependencies", []) if isinstance(blueprint, dict) else []

        artifact = {
            "run_id": run_id,
            "case_id": case_id,
            "prompt": case_result.get("prompt"),
            "planner_output": snapshot.get("plan"),
            "architecture": blueprint,
            "resolved_dependencies": resolved_deps,
            "retrieved_docs_metadata": [],
            "failing_file": snapshot.get("project_root"),
            "validation_errors": snapshot.get("errors", []),
            "build_errors": snapshot.get("errors", []),
            "repair_attempts": case_result.get("repair_metrics", {}).get("average_repair_attempts", 0),
        }
        file_path.write_text(json.dumps(artifact, indent=2, ensure_ascii=False), encoding="utf-8")
        return file_path


    def export_fine_tuning_traces(self, run_data: dict[str, Any]) -> Path:
        """Export observable traces for future model fine-tuning."""
        run_id = run_data.get("run_id", f"export-{int(time.time())}")
        export_path = self.fine_tuning_dir / f"{run_id}_fine_tuning.jsonl"

        with open(export_path, "w", encoding="utf-8") as f:
            for case_res in run_data.get("case_results", []):
                snapshot = case_res.get("state_snapshot", {})
                if case_res.get("build_succeeded"):
                    # Generation Trace
                    gen_example = {
                        "type": "code_generation",
                        "input": {
                            "prompt": case_res.get("prompt"),
                            "requirements": snapshot.get("plan"),
                            "architecture": snapshot.get("blueprint"),
                            "dependencies": snapshot.get("blueprint", {}).get("dependencies"),
                        },
                        "target": {
                            "generated_files": snapshot.get("blueprint", {}).get("filesystem", []),
                        },
                    }
                    f.write(json.dumps(gen_example, ensure_ascii=False) + "\n")

                if case_res.get("repair_metrics", {}).get("average_repair_attempts", 0) > 0:
                    # Repair Trace
                    repair_example = {
                        "type": "code_repair",
                        "input": {
                            "broken_source": snapshot.get("blueprint"),
                            "compiler_error": snapshot.get("errors"),
                            "context": case_res.get("prompt"),
                        },
                        "target": {
                            "corrected_source": snapshot.get("blueprint"),
                        },
                    }
                    f.write(json.dumps(repair_example, ensure_ascii=False) + "\n")

        return export_path


def load_run_jsonl(path: str | Path) -> list[dict[str, Any]]:
    records = []
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Run file not found: {file_path}")
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                records.append(json.loads(line))
    return records
