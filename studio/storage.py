"""Durable project/conversation/event records. LangGraph keeps its own checkpoints."""
from __future__ import annotations

from contextlib import contextmanager
import json
import sqlite3
import time
import uuid
from pathlib import Path


class StudioStore:
    def __init__(self, directory: Path):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "studio.sqlite"
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, title TEXT NOT NULL,
                    framework TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'idle', state TEXT NOT NULL DEFAULT '{}',
                    error TEXT, operation TEXT, created REAL NOT NULL, updated REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL REFERENCES projects(id),
                    role TEXT NOT NULL, content TEXT NOT NULL, request_id TEXT,
                    created REAL NOT NULL, UNIQUE(project_id, request_id)
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL REFERENCES projects(id),
                    type TEXT NOT NULL, data TEXT NOT NULL, created REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_project ON events(project_id, id);
                CREATE INDEX IF NOT EXISTS messages_project ON messages(project_id, id);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def create(self, title, framework, provider, model, owner="local"):
        pid = uuid.uuid4().hex
        with self.connect() as db:
            db.execute("INSERT INTO projects(id,owner_id,title,framework,provider,model,created,updated) VALUES(?,?,?,?,?,?,?,?)", (pid, owner, title[:100], framework, provider, model, time.time(), time.time()))
        return self.get(pid, owner)

    def get(self, pid, owner="local"):
        with self.connect() as db:
            row = db.execute("SELECT * FROM projects WHERE id=? AND owner_id=?", (pid, owner)).fetchone()
        if row is None:
            raise KeyError("Project not found")
        data = dict(row)
        data["state"] = json.loads(data["state"])
        return data

    def projects(self, owner="local"):
        with self.connect() as db:
            rows = db.execute("SELECT id,title,framework,provider,model,status,error,created,updated FROM projects WHERE owner_id=? ORDER BY updated DESC", (owner,)).fetchall()
        return [dict(row) for row in rows]

    def update(self, pid, **fields):
        allowed = {"status", "state", "error", "operation", "title", "provider", "model"}
        if not fields or not fields.keys() <= allowed:
            raise ValueError("Invalid project update")
        if "state" in fields:
            fields["state"] = json.dumps(fields["state"], default=str)
        fields["updated"] = time.time()
        with self.connect() as db:
            db.execute("UPDATE projects SET " + ",".join(f"{key}=?" for key in fields) + " WHERE id=?", (*fields.values(), pid))

    def message(self, pid, role, content, request_id=None):
        with self.connect() as db:
            cur = db.execute("INSERT OR IGNORE INTO messages(project_id,role,content,request_id,created) VALUES(?,?,?,?,?)", (pid, role, content, request_id, time.time()))
            return cur.rowcount > 0

    def has_request(self, pid, request_id):
        with self.connect() as db:
            return db.execute("SELECT 1 FROM messages WHERE project_id=? AND request_id=?", (pid, request_id)).fetchone() is not None

    def messages(self, pid):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM messages WHERE project_id=? ORDER BY id", (pid,))]

    def event(self, pid, event_type, data):
        with self.connect() as db:
            cur = db.execute("INSERT INTO events(project_id,type,data,created) VALUES(?,?,?,?)", (pid, event_type, json.dumps(data, default=str), time.time()))
            return cur.lastrowid

    def events(self, pid, after=0):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM events WHERE project_id=? AND id>? ORDER BY id LIMIT 250", (pid, after)).fetchall()
        return [{**dict(row), "data": json.loads(row["data"])} for row in rows]
