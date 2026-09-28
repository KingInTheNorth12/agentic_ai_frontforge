from __future__ import annotations

from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any, Iterator

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.store.sqlite import SqliteStore

import config as app_config


class ProjectMemory:
    """Small explicit long-term memory layer backed by LangGraph Store.

    Store only durable decisions/preferences here. Generated code stays on disk and
    graph checkpoints store execution state.
    """

    def __init__(self, store: SqliteStore):
        self.store = store

    def load(self, user_id: str, project_id: str) -> dict[str, Any]:
        return self.load_selective(user_id, project_id, ["user_preferences", "project_decisions", "dependency_memory", "repair_memory"])

    def load_selective(self, user_id: str, project_id: str, categories: list[str]) -> dict[str, Any]:
        """Selectively load only requested memory categories to minimize prompt context overhead."""
        category_map = {
            "user_preferences": (("users", user_id), "preferences"),
            "project_decisions": (("projects", project_id), "decisions"),
            "dependency_memory": (("projects", project_id), "dependencies"),
            "repair_memory": (("projects", project_id), "repairs"),
        }
        result: dict[str, Any] = {}
        for cat in categories:
            if cat in category_map:
                namespace, key = category_map[cat]
                item = self.store.get(namespace, key)
                if item is not None:
                    result[cat] = item.value
        return result


    def save_project_decisions(self, project_id: str, value: dict[str, Any]) -> None:
        self.store.put(("projects", project_id), "decisions", value)

    def save_dependencies(self, project_id: str, value: dict[str, Any]) -> None:
        self.store.put(("projects", project_id), "dependencies", value)

    def save_repairs(self, project_id: str, value: dict[str, Any]) -> None:
        self.store.put(("projects", project_id), "repairs", value)


@contextmanager
def open_memory(
    checkpoint_db: str | Path | None = None,
    store_db: str | Path | None = None,
) -> Iterator[tuple[SqliteSaver, SqliteStore, ProjectMemory]]:
    app_config.AGENT_DIR.mkdir(parents=True, exist_ok=True)
    checkpoint_db = str(Path(checkpoint_db or app_config.CHECKPOINT_DB))
    store_db = str(Path(store_db or app_config.STORE_DB))

    with ExitStack() as stack:
        checkpointer = stack.enter_context(SqliteSaver.from_conn_string(checkpoint_db))
        store = stack.enter_context(SqliteStore.from_conn_string(store_db))
        if hasattr(checkpointer, "setup"):
            checkpointer.setup()
        store.setup()
        yield checkpointer, store, ProjectMemory(store)
