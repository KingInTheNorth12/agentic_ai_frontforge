from __future__ import annotations

import sqlite3
import uuid
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.store.sqlite import SqliteStore

from cli.config import CLIConfig, apply_to_env
from cli.events import EventBus, get_bus
from cli.metrics import MetricsCollector
import config as app_config
from memory import ProjectMemory, open_memory
from state import ProjectState


class SessionContext:
    """
    Session context shared across the CLI session.
    Single authoritative container holding active project, thread, memory handles,
    config, event bus, run metrics, and state.
    """

    def __init__(
        self,
        config: CLIConfig,
        thread_id: Optional[str] = None,
        project_id: Optional[str] = None,
        project_root: Optional[Path] = None,
        bus: Optional[EventBus] = None,
        metrics: Optional[MetricsCollector] = None,
        renderer: Optional[Any] = None,
    ):
        self.config: CLIConfig = config
        self.thread_id: Optional[str] = thread_id
        self.project_id: Optional[str] = project_id or thread_id
        self.project_root: Optional[Path] = project_root
        self.run_id: str = uuid.uuid4().hex
        self.bus: EventBus = bus if bus is not None else get_bus()
        self.metrics: MetricsCollector = metrics if metrics is not None else MetricsCollector()
        if metrics is None:
            self.metrics.attach(self.bus)
        self.renderer: Optional[Any] = renderer


        self._checkpointer: Optional[SqliteSaver] = None
        self._store: Optional[SqliteStore] = None
        self._memory: Optional[ProjectMemory] = None
        self._graph: Any = None
        self._state: Optional[ProjectState] = None
        self._exit_stack: Optional[ExitStack] = None

    @property
    def workspace_root(self) -> Path:
        if self.project_root is not None:
            return self.project_root
        return Path(self.config.workspace_root).resolve()

    def ensure_memory(self) -> Tuple[SqliteSaver, SqliteStore, ProjectMemory]:
        """
        Lazily open memory/store/checkpointer using open_memory().
        Uses ExitStack to keep resources open.
        """
        if self._memory is not None and self._checkpointer is not None and self._store is not None:
            return self._checkpointer, self._store, self._memory

        if self._exit_stack is None:
            self._exit_stack = ExitStack()

        app_config.AGENT_DIR.mkdir(parents=True, exist_ok=True)

        checkpointer, store, memory = self._exit_stack.enter_context(
            open_memory(app_config.CHECKPOINT_DB, app_config.STORE_DB)
        )
        self._checkpointer = checkpointer
        self._store = store
        self._memory = memory

        return self._checkpointer, self._store, self._memory

    def ensure_graph(self) -> Any:
        """
        Lazily build graph using build_graph() with the checkpointer/store/memory.
        """
        if self._graph is not None:
            return self._graph

        checkpointer, store, memory = self.ensure_memory()
        from graph import build_graph
        self._graph = build_graph(memory=memory, checkpointer=checkpointer, store=store)
        return self._graph

    def latest_state(self) -> Optional[ProjectState]:
        """
        If thread_id is set, get latest checkpoint state from checkpointer.
        """
        if not self.thread_id:
            return self._state

        try:
            self.ensure_graph()
            config_dict = {"configurable": {"thread_id": self.thread_id}}
            state = self._graph.get_state(config_dict)
            if state and hasattr(state, "values") and state.values:
                self._state = state.values
                return self._state
        except Exception:
            pass
        return self._state

    def list_threads(self) -> List[Dict[str, Any]]:
        """
        List all threads from the checkpointer by querying the sqlite db directly.
        """
        try:
            self.ensure_memory()
            if self._checkpointer is None:
                return []
            conn = self._checkpointer.conn
            threads: List[Dict[str, Any]] = []
            cursor = conn.cursor()
            cursor.execute("""
                SELECT thread_id
                FROM checkpoints 
                GROUP BY thread_id 
            """)
            rows = cursor.fetchall()
            for row in rows:
                threads.append({
                    "thread_id": row[0],
                })
            return threads
        except Exception:
            return []

    def get_thread_info(self, thread_id: str) -> Optional[Dict[str, Any]]:
        """
        Detailed info for a specific thread.
        """
        try:
            self.ensure_graph()
            config_dict = {"configurable": {"thread_id": thread_id}}
            state = self._graph.get_state(config_dict)
            if not state:
                return None

            return {
                "thread_id": thread_id,
                "values": state.values if hasattr(state, "values") else None,
                "next": state.next if hasattr(state, "next") else None,
                "metadata": state.metadata if hasattr(state, "metadata") else None,
            }
        except Exception:
            return None

    def close(self) -> None:
        """
        Cleanup and close exit stack.
        """
        if self._exit_stack is not None:
            try:
                self._exit_stack.close()
            except Exception:
                pass
            self._exit_stack = None
        self._checkpointer = None
        self._store = None
        self._memory = None
        self._graph = None


@contextmanager
def open_session(
    config: CLIConfig,
    thread_id: Optional[str] = None,
    project_id: Optional[str] = None,
    project_root: Optional[Path] = None,
    renderer: Optional[Any] = None,
) -> Iterator[SessionContext]:
    """
    Creates a SessionContext, applies config to env, yields it, and cleans up.
    """
    apply_to_env(config)
    session = SessionContext(
        config=config,
        thread_id=thread_id,
        project_id=project_id,
        project_root=project_root,
        renderer=renderer,
    )
    try:
        yield session
    finally:
        session.close()
