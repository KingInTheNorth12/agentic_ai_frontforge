from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from cli.events import Event, EventBus, EventType


@dataclass
class RunMetrics:
    """Accumulated metrics for a single pipeline run."""

    started_at: float = 0.0
    finished_at: float = 0.0
    node_durations: dict[str, float] = field(default_factory=dict)
    llm_request_count: int = 0
    files_generated: int = 0
    files_styled: int = 0
    files_reviewed: int = 0
    generation_failures: int = 0
    retrieved_chunks: int = 0
    build_attempts: int = 0
    build_passed: bool = False
    repair_attempts: int = 0
    repaired_files: int = 0
    dependencies_resolved: int = 0

    # Extended Performance & Caching Metrics
    cache_hits: int = 0
    cache_misses: int = 0
    files_reused: int = 0
    files_regenerated: int = 0
    rag_calls: int = 0
    retrieved_context_bytes: int = 0
    skipped_reviewer_calls: int = 0
    skipped_styler_calls: int = 0
    dependency_install_duration_s: float = 0.0
    compiled_prompt_bytes: int = 0
    uncompiled_prompt_bytes: int = 0
    model_calls_by_role: dict[str, int] = field(default_factory=dict)
    models_used: list[str] = field(default_factory=list)

    # Visual & Functional Evaluation Metrics
    route_render_success_rate: float = 0.0
    console_error_count: int = 0
    overflow_failure_rate: float = 0.0
    responsive_failure_rate: float = 0.0
    visual_repair_attempts: int = 0
    visual_score: float = 0.0
    final_visual_pass_rate: float = 0.0

    @property
    def total_duration(self) -> float:
        if self.started_at and self.finished_at:
            return self.finished_at - self.started_at
        return 0.0

    def summary(self) -> dict[str, Any]:
        """Return a clean summary dict for rendering."""
        prompt_reduction = (
            round((1.0 - (self.compiled_prompt_bytes / self.uncompiled_prompt_bytes)) * 100, 1)
            if self.uncompiled_prompt_bytes > 0
            else 0.0
        )
        return {
            "total_duration_s": round(self.total_duration, 2),
            "node_durations": {
                k: round(v, 2) for k, v in self.node_durations.items()
            },
            "llm_requests": self.llm_request_count,
            "files_generated": self.files_generated,
            "files_styled": self.files_styled,
            "files_reviewed": self.files_reviewed,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "files_reused": self.files_reused,
            "files_regenerated": self.files_regenerated,
            "skipped_reviewer_calls": self.skipped_reviewer_calls,
            "skipped_styler_calls": self.skipped_styler_calls,
            "rag_calls": self.rag_calls,
            "retrieved_chunks": self.retrieved_chunks,
            "build_attempts": self.build_attempts,
            "build_passed": self.build_passed,
            "repair_attempts": self.repair_attempts,
            "prompt_context_reduction_percent": prompt_reduction,
            "model_calls_by_role": self.model_calls_by_role,
            "route_render_success_rate": self.route_render_success_rate,
            "console_error_count": self.console_error_count,
            "overflow_failure_rate": self.overflow_failure_rate,
            "responsive_failure_rate": self.responsive_failure_rate,
            "visual_repair_attempts": self.visual_repair_attempts,
            "visual_score": self.visual_score,
            "final_visual_pass_rate": self.final_visual_pass_rate,
        }



class MetricsCollector:
    """Subscribes to EventBus and accumulates run metrics.

    Only collects data. Never produces terminal output.
    """

    def __init__(self) -> None:
        self.current = RunMetrics()
        self._node_start_times: dict[str, float] = {}

    def reset(self) -> None:
        self.current = RunMetrics()
        self._node_start_times.clear()

    def handle_event(self, event: Event) -> None:
        """EventBus subscriber callback."""
        m = self.current
        t = event.type

        if t == EventType.WORKFLOW_STARTED:
            m.started_at = event.timestamp

        elif t in (EventType.WORKFLOW_COMPLETED, EventType.WORKFLOW_FAILED):
            m.finished_at = event.timestamp

        elif t == EventType.NODE_STARTED:
            self._node_start_times[event.node] = event.timestamp

        elif t in (EventType.NODE_COMPLETED, EventType.NODE_FAILED):
            start = self._node_start_times.pop(event.node, None)
            if start is not None:
                m.node_durations[event.node] = event.timestamp - start

        elif t == EventType.FILE_GENERATED:
            m.files_generated += 1
            m.files_regenerated += 1
            m.llm_request_count += 1

        elif t == EventType.CACHE_HIT:
            m.cache_hits += 1
            m.files_reused += 1

        elif t == EventType.CACHE_MISS:
            m.cache_misses += 1

        elif t == EventType.CONTEXT_COMPILED:
            m.compiled_prompt_bytes += event.data.get("compiled_bytes", 0)
            m.uncompiled_prompt_bytes += event.data.get("uncompiled_bytes", 0)

        elif t == EventType.REVIEW_SKIPPED:
            m.skipped_reviewer_calls += 1

        elif t == EventType.STYLER_SKIPPED:
            m.skipped_styler_calls += 1

        elif t == EventType.FILE_GENERATION_FAILED:
            m.generation_failures += 1

        elif t == EventType.FILE_STYLED:
            m.files_styled += 1
            m.llm_request_count += 1

        elif t == EventType.FILE_REVIEWED:
            m.files_reviewed += 1
            m.llm_request_count += 1

        elif t == EventType.RETRIEVAL_STARTED:
            m.rag_calls += 1

        elif t == EventType.RETRIEVAL_COMPLETED:
            m.retrieved_chunks += event.data.get("count", 0)
            m.retrieved_context_bytes += event.data.get("bytes", 0)

        elif t == EventType.DEPENDENCY_RESOLVED:
            m.dependencies_resolved += event.data.get("count", 0)

        elif t == EventType.BUILD_STARTED:
            m.build_attempts += 1

        elif t == EventType.BUILD_PASSED:
            m.build_passed = True

        elif t == EventType.REPAIR_STARTED:
            m.repair_attempts += 1

        elif t == EventType.REPAIR_COMPLETED:
            m.repaired_files += event.data.get("files_changed", 0)

        elif t == EventType.VISUAL_EVAL_COMPLETED:
            m.route_render_success_rate = event.data.get("render_success_rate", 0.0)
            m.console_error_count += event.data.get("console_errors", 0)
            m.overflow_failure_rate = event.data.get("overflow_failure_rate", 0.0)
            m.responsive_failure_rate = event.data.get("responsive_failure_rate", 0.0)
            m.visual_score = event.data.get("visual_score", 0.0)
            m.final_visual_pass_rate = event.data.get("final_pass_rate", 0.0)

        elif t == EventType.VISUAL_REPAIR_STARTED:
            m.visual_repair_attempts += 1


    def attach(self, bus: EventBus) -> None:
        bus.subscribe(self.handle_event)

    def detach(self, bus: EventBus) -> None:
        bus.unsubscribe(self.handle_event)

