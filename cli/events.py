from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class EventType(str, Enum):
    """Semantic event types emitted by the agent pipeline."""

    # Workflow lifecycle
    WORKFLOW_STARTED = "workflow_started"
    WORKFLOW_COMPLETED = "workflow_completed"
    WORKFLOW_FAILED = "workflow_failed"

    # Graph node lifecycle
    NODE_STARTED = "node_started"
    NODE_COMPLETED = "node_completed"
    NODE_FAILED = "node_failed"

    # File operations
    FILE_GENERATION_STARTED = "file_generation_started"
    FILE_GENERATED = "file_generated"
    FILE_GENERATION_FAILED = "file_generation_failed"
    FILE_STYLED = "file_styled"
    FILE_REVIEWED = "file_reviewed"

    # Dependencies
    DEPENDENCY_RESOLUTION_STARTED = "dependency_resolution_started"
    DEPENDENCY_RESOLVED = "dependency_resolved"
    DEPENDENCY_RESOLUTION_FAILED = "dependency_resolution_failed"

    # Build
    BUILD_STARTED = "build_started"
    BUILD_PASSED = "build_passed"
    BUILD_FAILED = "build_failed"

    # Repair
    REPAIR_STARTED = "repair_started"
    REPAIR_COMPLETED = "repair_completed"

    # RAG
    RETRIEVAL_STARTED = "retrieval_started"
    RETRIEVAL_COMPLETED = "retrieval_completed"

    # Clarification / interrupt
    INTERRUPT_REQUESTED = "interrupt_requested"
    INTERRUPT_RESUMED = "interrupt_resumed"

    # Persistence
    CHECKPOINT_SAVED = "checkpoint_saved"
    MEMORY_SAVED = "memory_saved"

    # Logging
    LOG_MESSAGE = "log_message"

    # Sandbox Subsystem
    SANDBOX_CREATE_STARTED = "sandbox_create_started"
    SANDBOX_CREATED = "sandbox_created"
    SANDBOX_COMMAND_STARTED = "sandbox_command_started"
    SANDBOX_COMMAND_COMPLETED = "sandbox_command_completed"
    SANDBOX_TIMEOUT = "sandbox_timeout"
    SANDBOX_FAILED = "sandbox_failed"
    SANDBOX_DESTROYED = "sandbox_destroyed"

    # Performance & Caching
    CACHE_HIT = "cache_hit"
    CACHE_MISS = "cache_miss"
    CONTEXT_COMPILED = "context_compiled"
    REVIEW_SKIPPED = "review_skipped"
    STYLER_SKIPPED = "styler_skipped"
    DEPENDENCY_CACHE_HIT = "dependency_cache_hit"

    # Visual & Functional Evaluation Subsystem
    VISUAL_EVAL_STARTED = "visual_eval_started"
    VISUAL_EVAL_COMPLETED = "visual_eval_completed"
    VISUAL_DEFECT_DETECTED = "visual_defect_detected"
    VISUAL_REPAIR_STARTED = "visual_repair_started"
    VISUAL_REPAIR_COMPLETED = "visual_repair_completed"




@dataclass(frozen=True, slots=True)
class Event:
    """A single semantic event from the pipeline."""

    type: EventType
    node: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    run_id: str = ""


# Subscriber callback type
Subscriber = Callable[[Event], None]


class EventBus:
    """Thread-safe publish/subscribe event bus.

    Agents emit events here. The CLI renderer and logger subscribe.
    No terminal/rendering imports live in this module.
    """

    def __init__(self) -> None:
        self._subscribers: list[Subscriber] = []
        self._lock = threading.Lock()
        self._run_id: str = ""

    @property
    def run_id(self) -> str:
        return self._run_id

    @run_id.setter
    def run_id(self, value: str) -> None:
        self._run_id = value

    def subscribe(self, callback: Subscriber) -> None:
        """Register a subscriber to receive all events."""
        with self._lock:
            if callback not in self._subscribers:
                self._subscribers.append(callback)

    def unsubscribe(self, callback: Subscriber) -> None:
        """Remove a subscriber."""
        with self._lock:
            self._subscribers = [s for s in self._subscribers if s is not callback]

    def emit(self, event_type: EventType, *, node: str = "", **data: Any) -> Event:
        """Emit an event to all subscribers.

        Returns the created Event for convenience.
        """
        event = Event(
            type=event_type,
            node=node,
            data=data,
            run_id=self._run_id,
        )
        with self._lock:
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber(event)
            except Exception:
                # Subscriber errors must never crash the pipeline
                pass
        return event

    def clear(self) -> None:
        """Remove all subscribers."""
        with self._lock:
            self._subscribers.clear()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_bus: EventBus | None = None
_bus_lock = threading.Lock()


def get_bus() -> EventBus:
    """Return the global EventBus singleton (created on first call)."""
    global _bus
    if _bus is None:
        with _bus_lock:
            if _bus is None:
                _bus = EventBus()
    return _bus


def emit(event_type: EventType, *, node: str = "", **data: Any) -> Event:
    """Convenience wrapper: emit an event on the global bus."""
    return get_bus().emit(event_type, node=node, **data)
