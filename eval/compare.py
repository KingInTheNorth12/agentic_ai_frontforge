from __future__ import annotations

from typing import Any

from eval.storage import load_run_jsonl


def compute_run_stats(records: list[dict[str, Any]]) -> dict[str, Any]:
    if not records:
        return {
            "total_cases": 0,
            "first_pass_build_rate": 0.0,
            "final_build_rate": 0.0,
            "avg_latency_s": 0.0,
            "wrong_major_version_rate": 0.0,
            "case_outcomes": {},
        }

    cases = [r.get("case", r) for r in records]
    total = len(cases)
    first_pass = sum(1 for c in cases if c.get("first_pass_build_succeeded"))
    final_pass = sum(1 for c in cases if c.get("build_succeeded"))
    latencies = [c.get("telemetry", {}).get("wall_clock_latency_s", 0.0) for c in cases]
    avg_latency = sum(latencies) / total if total > 0 else 0.0

    # RAG version safety rate if RAG benchmark
    wrong_version = sum(c.get("wrong_major_version_rate", 0.0) for c in cases) / total if total > 0 else 0.0

    case_outcomes = {c.get("case_id", f"idx_{i}"): bool(c.get("build_succeeded")) for i, c in enumerate(cases)}

    return {
        "total_cases": total,
        "first_pass_build_rate": (first_pass / total) if total > 0 else 0.0,
        "final_build_rate": (final_pass / total) if total > 0 else 0.0,
        "avg_latency_s": avg_latency,
        "wrong_major_version_rate": wrong_version,
        "case_outcomes": case_outcomes,
    }


def compare_runs(
    run_a_path: str,
    run_b_path: str,
) -> dict[str, Any]:
    records_a = load_run_jsonl(run_a_path)
    records_b = load_run_jsonl(run_b_path)

    stats_a = compute_run_stats(records_a)
    stats_b = compute_run_stats(records_b)

    fp_a = stats_a["first_pass_build_rate"] * 100
    fp_b = stats_b["first_pass_build_rate"] * 100
    fn_a = stats_a["final_build_rate"] * 100
    fn_b = stats_b["final_build_rate"] * 100

    wv_a = stats_a["wrong_major_version_rate"] * 100
    wv_b = stats_b["wrong_major_version_rate"] * 100

    lat_a = stats_a["avg_latency_s"]
    lat_b = stats_b["avg_latency_s"]

    # Identify regressions and improvements
    outcomes_a = stats_a["case_outcomes"]
    outcomes_b = stats_b["case_outcomes"]

    all_case_ids = set(outcomes_a.keys()) | set(outcomes_b.keys())
    regressions = []
    improvements = []

    for cid in sorted(all_case_ids):
        passed_a = outcomes_a.get(cid, False)
        passed_b = outcomes_b.get(cid, False)

        if passed_a and not passed_b:
            regressions.append(cid)
        elif not passed_a and passed_b:
            improvements.append(cid)

    return {
        "run_a_file": str(run_a_path),
        "run_b_file": str(run_b_path),
        "metrics": {
            "first_pass_build": {
                "run_a": f"{fp_a:.1f}%",
                "run_b": f"{fp_b:.1f}%",
                "delta": f"{(fp_b - fp_a):+.1f}%",
            },
            "final_build": {
                "run_a": f"{fn_a:.1f}%",
                "run_b": f"{fn_b:.1f}%",
                "delta": f"{(fn_b - fn_a):+.1f}%",
            },
            "wrong_version_rag": {
                "run_a": f"{wv_a:.1f}%",
                "run_b": f"{wv_b:.1f}%",
                "delta": f"{(wv_b - wv_a):+.1f}%",
            },
            "avg_latency": {
                "run_a": f"{lat_a:.1f}s",
                "run_b": f"{lat_b:.1f}s",
                "delta": f"{(lat_b - lat_a):+.1f}s",
            },
        },
        "regressions": regressions,
        "improvements": improvements,
    }


def format_comparison_summary(comparison: dict[str, Any]) -> str:
    m = comparison["metrics"]
    lines = [
        "============================================================",
        "             EXPERIMENT RUN COMPARISON SUMMARY              ",
        "============================================================",
        f" Run A: {comparison['run_a_file']}",
        f" Run B: {comparison['run_b_file']}",
        "------------------------------------------------------------",
        f" First-pass build:   {m['first_pass_build']['run_a']} → {m['first_pass_build']['run_b']} ({m['first_pass_build']['delta']})",
        f" Final build:        {m['final_build']['run_a']} → {m['final_build']['run_b']} ({m['final_build']['delta']})",
        f" Wrong-version RAG:  {m['wrong_version_rag']['run_a']} → {m['wrong_version_rag']['run_b']} ({m['wrong_version_rag']['delta']})",
        f" Average latency:    {m['avg_latency']['run_a']} → {m['avg_latency']['run_b']} ({m['avg_latency']['delta']})",
        "------------------------------------------------------------",
        f" Regressions ({len(comparison['regressions'])}): {', '.join(comparison['regressions']) if comparison['regressions'] else 'None'}",
        f" Improvements ({len(comparison['improvements'])}): {', '.join(comparison['improvements']) if comparison['improvements'] else 'None'}",
        "============================================================",
    ]
    return "\n".join(lines)
