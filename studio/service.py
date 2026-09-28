"""Single-worker execution ownership, safe file inspection, and static preview lifecycle."""
from __future__ import annotations

import multiprocessing
import os
import signal
import subprocess
import re
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from studio.storage import StudioStore
from tools import safe_project_path

SKIP_DIRS = {"node_modules", ".git", ".agent", ".next", "dist", "out", ".frontend_agent", "__pycache__"}


class PreviewHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def translate_path(self, path):
        rel = unquote(urlsplit(path).path).lstrip("/")
        root = Path(self.directory).resolve()
        target = (root / rel).resolve()
        if not target.is_relative_to(root) or any(part.startswith(".") for part in Path(rel).parts):
            return str(root / "__not_found__")
        if target.is_dir():
            target = target / "index.html"
        elif not target.is_file():
            html = target.with_suffix(".html")
            target = html if html.is_file() else root / "index.html" if not Path(rel).suffix else target
        target = target.resolve()
        return str(target if target.is_relative_to(root) else root / "__not_found__")

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def list_directory(self, path):
        self.send_error(404)
        return None


class StudioService:
    def __init__(self, directory: Path):
        self.store = StudioStore(directory)
        self.lock = threading.RLock()
        self.process = None
        self.active_id = None
        self.preview = None
        self.preview_id = None
        self.preview_thread = None
        # Interrupted runs can recover from the last durable LangGraph checkpoint.
        for project in self.store.projects():
            if project["status"] == "running":
                self.store.update(project["id"], status="interrupted", error="The server stopped during this operation. Use the available recovery action to continue.")
                self.cleanup_containers(project["id"])

    def root(self, pid):
        self.store.get(pid)
        return self.store.directory / "projects" / pid

    def recovery(self, pid):
        project = self.store.get(pid)
        if project["status"] not in {"failed", "cancelled", "interrupted"}:
            return None
        checkpoint_path = self.store.directory / "checkpoints.sqlite"
        if checkpoint_path.exists():
            from langgraph.checkpoint.sqlite import SqliteSaver
            from graph import build_graph
            with SqliteSaver.from_conn_string(str(checkpoint_path)) as saver:
                graph = build_graph(checkpointer=saver, store=None, memory=None)
                if graph.get_state({"configurable": {"thread_id": pid}}).next:
                    return {"action": "resume", "label": "Resume saved checkpoint"}
        if project["state"].get("generation_report", {}).get("success"):
            return {"action": "build", "label": "Retry build"}
        return {"action": "retry", "label": "Retry generation"}

    def cleanup_containers(self, pid):
        # Only containers recorded for this project's unfinished commands belong to us.
        active, runtime, cursor = {}, None, 0
        while rows := self.store.events(pid, cursor):
            for event in rows:
                data = event["data"]
                if event["type"] == "sandbox_create_started":
                    runtime = data.get("runtime")
                elif event["type"] == "sandbox_created":
                    active[data.get("container_id", "")] = runtime
                elif event["type"] == "sandbox_destroyed":
                    active.pop(data.get("container_id"), None)
                cursor = event["id"]
        for container_id, runtime in active.items():
            if not re.fullmatch(r"fa-sbx-[a-f0-9]{12}", container_id):
                continue
            argv = ["docker", "rm", "-f", container_id] if runtime == "docker" else ["container", "stop", container_id] if runtime in {"apple", "apple-container"} else None
            if argv:
                try:
                    subprocess.run(argv, capture_output=True, timeout=10, check=False)
                except (OSError, subprocess.TimeoutExpired):
                    self.store.event(pid, "cleanup_failed", {"container_id": container_id, "runtime": runtime})

    def reconcile(self):
        with self.lock:
            if self.process is not None and not self.process.is_alive():
                self.process.join(timeout=0)
                project = self.store.get(self.active_id)
                if project["status"] == "running":
                    self.store.update(self.active_id, status="interrupted", error="Worker exited before completing. Use the recovery action below.")
                    self.store.event(self.active_id, "settled", {"status": "interrupted"})
                    self.cleanup_containers(self.active_id)
                self.process.close()
                self.process = None
                self.active_id = None

    def submit(self, pid, content, request_id, action=None):
        with self.lock:
            self.reconcile()
            project = self.store.get(pid)
            if self.store.has_request(pid, request_id):
                return project
            if self.process is not None:
                raise RuntimeError("Another operation is already running. Wait for it or stop it first.")
            if action is None:
                action = "clarify" if project["status"] == "awaiting_input" else ("edit" if project["state"].get("blueprint") else "generate")
            if action == "resume" and project["status"] not in {"interrupted", "cancelled", "failed"}:
                raise ValueError("This project is not waiting for a checkpoint retry")
            if action == "resume" and self.recovery(pid)["action"] != "resume":
                raise ValueError("No unfinished checkpoint remains. Retry the build or send a new request.")
            if action == "retry":
                if not self.recovery(pid) or self.recovery(pid)["action"] != "retry":
                    raise ValueError("Use the available recovery action for this project")
                content = project["state"].get("request") or self.store.messages(pid)[0]["content"]
                action = "generate"
            if action == "build" and not project["state"].get("blueprint"):
                raise ValueError("There is no generated project to build yet")
            if self.preview_id == pid:
                self.stop_preview()
            self.store.message(pid, "user", content, request_id)
            self.store.update(pid, status="running", error=None, operation=action)
            self.store.event(pid, "accepted", {"action": action})
            from studio.worker import run_worker
            process = multiprocessing.get_context("spawn").Process(target=run_worker, args=(str(self.store.directory), pid, action, content), daemon=False)
            try:
                process.start()
            except Exception:
                self.store.update(pid, status="failed", error="Could not launch the generation worker")
                raise
            self.process, self.active_id = process, pid
            return self.store.get(pid)

    def cancel(self, pid):
        with self.lock:
            self.reconcile()
            if self.active_id != pid or self.process is None:
                raise ValueError("This project has no running operation")
            process = self.process
            try:
                if os.name == "posix" and os.getpgid(process.pid) == process.pid:
                    os.killpg(process.pid, signal.SIGTERM)
                else:
                    process.terminate()
            except ProcessLookupError:
                pass  # The worker may have exited since the last liveness check.
            process.join(timeout=3)
            if process.is_alive():
                if os.name == "posix" and os.getpgid(process.pid) == process.pid:
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                process.join(timeout=2)
            process.close()
            self.process, self.active_id = None, None
            self.cleanup_containers(pid)
            self.store.update(pid, status="cancelled", error="Stopped by you. Completed checkpoints and source files are retained.")
            self.store.message(pid, "assistant", "Stopped. Your project files and completed checkpoints are saved. Use the recovery action below when you are ready.")
            self.store.event(pid, "settled", {"status": "cancelled"})

    def files(self, pid):
        root = self.root(pid)
        entries = []
        if root.exists():
            for directory, dirs, files in os.walk(root, followlinks=False):
                dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith(".") and not (Path(directory) / d).is_symlink())
                for name in sorted(files):
                    path = Path(directory) / name
                    if name.startswith(".") or path.is_symlink():
                        continue
                    entries.append({"path": path.relative_to(root).as_posix(), "size": path.stat().st_size})
        return entries

    def file(self, pid, rel):
        if any(part in SKIP_DIRS or part.startswith(".") for part in Path(rel).parts):
            raise ValueError("Only visible project source and reports can be inspected")
        target = safe_project_path(self.root(pid), rel)
        if not target.is_file():
            raise FileNotFoundError("File not found")
        if target.stat().st_size > 1_000_000:
            raise ValueError("File exceeds the 1 MB source-view limit")
        return {"path": rel, "content": target.read_text(encoding="utf-8")}

    def start_preview(self, pid):
        with self.lock:
            self.reconcile()
            project = self.store.get(pid)
            if project["status"] == "running" or not project["state"].get("build_succeeded"):
                raise ValueError("A successful current build is required before previewing")
            self.stop_preview()
            from frameworks import FRAMEWORKS
            output = self.root(pid) / FRAMEWORKS[project["framework"]]["output"]
            if not output.resolve().is_relative_to(self.root(pid).resolve()):
                raise ValueError("Preview output must remain inside this project")
            if not (output / "index.html").is_file():
                raise ValueError("Build output is missing. Run Build again.")
            host = os.getenv("FRONTEND_AGENT_PREVIEW_BIND", "127.0.0.1")
            port = int(os.getenv("FRONTEND_AGENT_PREVIEW_PORT", "0"))
            self.preview = ThreadingHTTPServer((host, port), partial(PreviewHandler, directory=str(output)))
            self.preview_thread = threading.Thread(target=self.preview.serve_forever, daemon=True)
            self.preview_thread.start()
            self.preview_id = pid
            return self.preview_info(pid)

    def preview_info(self, pid):
        if self.preview is None or self.preview_id != pid:
            return {"running": False}
        url = os.getenv("FRONTEND_AGENT_PREVIEW_ORIGIN") or f"http://127.0.0.1:{self.preview.server_port}"
        return {"running": True, "url": url}

    def stop_preview(self):
        if self.preview:
            self.preview.shutdown()
            self.preview.server_close()
            self.preview_thread.join(timeout=2)
            self.preview, self.preview_id, self.preview_thread = None, None, None

    def close(self):
        with self.lock:
            self.reconcile()
            if self.active_id:
                self.cancel(self.active_id)
            self.stop_preview()
