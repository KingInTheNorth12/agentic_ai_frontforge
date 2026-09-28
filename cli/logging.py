from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cli.events import Event, EventBus, EventType


@dataclass
class LogEntry:
    """A single structured log record."""

    timestamp: float
    run_id: str
    thread_id: str
    project_id: str
    node: str
    event: str
    status: str
    duration: float | None = None
    model: str | None = None
    error: str | None = None
    file: str | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = {
            "ts": self.timestamp,
            "run_id": self.run_id,
            "thread_id": self.thread_id,
            "project_id": self.project_id,
            "node": self.node,
            "event": self.event,
            "status": self.status,
        }
        if self.duration is not None:
            d["duration"] = round(self.duration, 3)
        if self.model:
            d["model"] = self.model
        if self.error:
            d["error"] = self.error
        if self.file:
            d["file"] = self.file
        if self.data:
            d["data"] = self.data
        return d


class StructuredLogger:
    """Writes structured JSONL logs for debugging and audit.

    Subscribes to the EventBus and writes one line per event
    to `.frontend_agent/logs/<run_id>.jsonl`.
    """

    def __init__(
        self,
        log_dir: Path,
        *,
        run_id: str = "",
        thread_id: str = "",
        project_id: str = "",
    ) -> None:
        self.log_dir = log_dir
        self.run_id = run_id
        self.thread_id = thread_id
        self.project_id = project_id
        self._file = None
        self._node_start_times: dict[str, float] = {}

    def _ensure_file(self) -> None:
        if self._file is not None:
            return
        self.log_dir.mkdir(parents=True, exist_ok=True)
        log_path = self.log_dir / f"{self.run_id or 'unknown'}.jsonl"
        self._file = open(log_path, "a", encoding="utf-8")  # noqa: SIM115

    def handle_event(self, event: Event) -> None:
        """EventBus subscriber callback."""
        duration = None

        if event.type == EventType.NODE_STARTED:
            self._node_start_times[event.node] = event.timestamp
        elif event.type in (EventType.NODE_COMPLETED, EventType.NODE_FAILED):
            start = self._node_start_times.pop(event.node, None)
            if start is not None:
                duration = event.timestamp - start

        status = "ok"
        if "FAILED" in event.type.value or "failed" in event.type.value:
            status = "error"
        elif "STARTED" in event.type.value or "started" in event.type.value:
            status = "running"

        # Extract useful fields from event data without leaking model reasoning
        safe_data: dict[str, Any] = {}
        for key in ("path", "action", "count", "attempt", "returncode", "reason"):
            if key in event.data:
                safe_data[key] = event.data[key]

        entry = LogEntry(
            timestamp=event.timestamp,
            run_id=event.run_id or self.run_id,
            thread_id=self.thread_id,
            project_id=self.project_id,
            node=event.node,
            event=event.type.value,
            status=status,
            duration=duration,
            model=event.data.get("model"),
            error=event.data.get("error"),
            file=event.data.get("file") or event.data.get("path"),
            data=safe_data,
        )
        self._write(entry)

    def _write(self, entry: LogEntry) -> None:
        try:
            self._ensure_file()
            assert self._file is not None
            self._file.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
            self._file.flush()
        except Exception:
            pass  # Logging must never crash the pipeline

    def close(self) -> None:
        if self._file is not None:
            try:
                self._file.close()
            except Exception:
                pass
            self._file = None

    def attach(self, bus: EventBus) -> None:
        """Subscribe to an EventBus."""
        bus.subscribe(self.handle_event)

    def detach(self, bus: EventBus) -> None:
        """Unsubscribe from an EventBus."""
        bus.unsubscribe(self.handle_event)


def query_logs(
    log_dir: Path,
    *,
    run_id: str | None = None,
    node: str | None = None,
    errors_only: bool = False,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Read and filter log entries from disk.

    If run_id is given, reads only that run's log file.
    Otherwise reads the most recent log file.
    """
    if not log_dir.is_dir():
        return []

    if run_id:
        log_file = log_dir / f"{run_id}.jsonl"
        files = [log_file] if log_file.is_file() else []
    else:
        files = sorted(log_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)

    results: list[dict[str, Any]] = []
    for log_file in files[:1]:  # Most recent only unless run_id specified
        try:
            for line in log_file.read_text(encoding="utf-8").strip().splitlines():
                if not line.strip():
                    continue
                entry = json.loads(line)
                if node and entry.get("node") != node:
                    continue
                if errors_only and entry.get("status") != "error":
                    continue
                results.append(entry)
                if len(results) >= limit:
                    break
        except Exception:
            continue
    return results
