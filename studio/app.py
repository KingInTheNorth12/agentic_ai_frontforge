"""Local same-origin API and the compiled Studio UI. Start with python -m studio."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import secrets
import time
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from studio.service import StudioService


class ModelSelection(BaseModel):
    provider: str = "ollama"
    model: str = Field(min_length=1, max_length=200)


class NewProject(ModelSelection):
    title: str = Field(default="Untitled project", min_length=1, max_length=100)
    framework: str = "react"


class MessageInput(BaseModel):
    content: str = Field(min_length=1, max_length=20_000)
    request_id: str = Field(min_length=8, max_length=100)
    action: str | None = None


def create_app(directory: Path | None = None) -> FastAPI:
    import config as cfg
    data_dir = directory or Path(os.getenv("FRONTEND_AGENT_DATA_DIR", str(cfg.AGENT_DIR)))
    service = StudioService(data_dir)
    csrf = secrets.token_urlsafe(32)
    api_token = os.getenv("FRONTEND_AGENT_ACCESS_TOKEN", "")

    @asynccontextmanager
    async def lifespan(app):
        yield
        service.close()

    app = FastAPI(title="Forma Studio", lifespan=lifespan)
    app.state.service = service

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        # Same-origin reads and CSRF token for writes also block generated previews.
        origin = request.headers.get("origin")
        allowed = {str(request.base_url).rstrip("/"), os.getenv("FRONTEND_AGENT_PUBLIC_ORIGIN", "").rstrip("/")}
        if origin and origin.rstrip("/") not in allowed:
            return JSONResponse({"detail": "Cross-origin requests are not allowed"}, status_code=403)
        hostname = request.url.hostname
        public_host = urlsplit(os.getenv("FRONTEND_AGENT_PUBLIC_ORIGIN", "")).hostname
        if hostname not in {"localhost", "127.0.0.1", "::1", "testserver", public_host}:
            return JSONResponse({"detail": "Host not allowed"}, status_code=403)
        if request.url.path.startswith("/api/"):
            if api_token and not secrets.compare_digest(request.headers.get("authorization", ""), "Bearer " + api_token):
                return JSONResponse({"detail": "A server access token is required"}, status_code=401)
            if request.method not in {"GET", "HEAD", "OPTIONS"} and not secrets.compare_digest(request.headers.get("x-studio-csrf", ""), csrf):
                return JSONResponse({"detail": "Reload the workspace before submitting"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-src http://127.0.0.1:* http://localhost:* " + os.getenv("FRONTEND_AGENT_PREVIEW_ORIGIN", "") + "; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
        return response

    @app.exception_handler(KeyError)
    async def not_found(request, exc):
        return JSONResponse({"detail": "Project not found"}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(FileNotFoundError)
    async def missing(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.get("/api/bootstrap")
    def bootstrap():
        from frameworks import FRAMEWORKS
        from sandbox import get_sandbox_manager
        service.reconcile()
        capability = get_sandbox_manager().get_capability()
        return {"csrf_token": csrf, "frameworks": [{"id": k, "label": v["label"]} for k, v in FRAMEWORKS.items()], "runtime": capability.to_dict(), "active_project": service.active_id, "retrieval": "version-matched" if os.getenv("FRONTEND_AGENT_RAG") == "1" else "disabled", "data_directory": str(service.store.directory)}

    @app.get("/api/providers")
    def providers():
        from llm import discover_providers
        return discover_providers()

    @app.get("/api/projects")
    def projects():
        with service.lock:
            service.reconcile()
            return service.store.projects()

    @app.post("/api/projects", status_code=201)
    def create(payload: NewProject):
        from frameworks import FRAMEWORKS
        if payload.framework not in FRAMEWORKS or payload.provider not in {"ollama", "compatible"}:
            raise ValueError("Select a supported framework and provider")
        return service.store.create(payload.title, payload.framework, payload.provider, payload.model)

    @app.get("/api/projects/{pid}")
    def project(pid: str):
        with service.lock:
            service.reconcile()
            value = service.store.get(pid)
            return {**value, "messages": service.store.messages(pid), "files": service.files(pid), "preview": service.preview_info(pid), "recovery": service.recovery(pid), "changes": changes(pid)}

    @app.patch("/api/projects/{pid}/model")
    def select_model(pid: str, payload: ModelSelection):
        with service.lock:
            service.reconcile()
            if service.store.get(pid)["status"] == "running":
                raise HTTPException(409, "Stop the current operation before changing its model")
            if payload.provider not in {"ollama", "compatible"}:
                raise ValueError("Select a supported provider")
            service.store.update(pid, provider=payload.provider, model=payload.model)
            return service.store.get(pid)

    @app.post("/api/projects/{pid}/messages", status_code=202)
    def message(pid: str, payload: MessageInput):
        if payload.action not in {None, "resume", "build", "retry"}:
            raise ValueError("Unsupported operation")
        if not payload.content.strip():
            raise ValueError("A message is required")
        try:
            return service.submit(pid, payload.content.strip(), payload.request_id, payload.action)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/projects/{pid}/cancel")
    def cancel(pid: str):
        service.store.get(pid)
        service.cancel(pid)
        return service.store.get(pid)

    @app.get("/api/projects/{pid}/file")
    def file(pid: str, path: str):
        return service.file(pid, path)

    @app.get("/api/projects/{pid}/changes")
    def changes(pid: str):
        import difflib
        from tools import safe_project_path
        root = service.root(pid)
        path = safe_project_path(root, ".agent/changes.json")
        report = json.loads(path.read_text()) if path.is_file() else {"files": []}
        for change in report["files"]:
            rel = change["path"]
            before = safe_project_path(root, f".agent/revisions/{report['revision']}/{rel}")
            after = safe_project_path(root, rel)
            old, current = before.read_text() if before.is_file() else "", after.read_text() if after.is_file() else ""
            change["diff"] = "".join(difflib.unified_diff(old.splitlines(True), current.splitlines(True), fromfile="before/" + rel, tofile="after/" + rel))
        return report

    @app.post("/api/projects/{pid}/preview")
    def preview(pid: str):
        return service.start_preview(pid)

    @app.delete("/api/projects/{pid}/preview")
    def stop_preview(pid: str):
        service.store.get(pid)
        with service.lock:
            if service.preview_id == pid:
                service.stop_preview()
        return {"running": False}

    @app.get("/api/projects/{pid}/events")
    async def events(pid: str, request: Request, after: int = 0):
        service.store.get(pid)
        async def stream():
            cursor = max(after, 0)
            last_heartbeat = time.monotonic()
            while not await request.is_disconnected():
                with service.lock:
                    service.reconcile()
                rows = service.store.events(pid, cursor)
                for event in rows:
                    cursor = event["id"]
                    yield f"id: {cursor}\ndata: {json.dumps(event)}\n\n"
                if time.monotonic() - last_heartbeat > 10:
                    yield ": heartbeat\n\n"
                    last_heartbeat = time.monotonic()
                await asyncio.sleep(0.4)
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    dist = Path(os.getenv("FRONTEND_AGENT_UI_DIR", str(Path(__file__).resolve().parent / "static")))
    if (dist / "assets").exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}")
    def ui(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "Unknown API endpoint")
        if not (dist / "index.html").exists():
            return JSONResponse({"detail": "Build the UI first: cd web && npm install && npm run build"}, status_code=503)
        return FileResponse(dist / "index.html")

    return app


def main():
    import argparse
    import uvicorn
    parser = argparse.ArgumentParser(description="Start Forma Studio locally")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"} and not (os.getenv("FRONTEND_AGENT_ACCESS_TOKEN") and os.getenv("FRONTEND_AGENT_PUBLIC_ORIGIN")):
        parser.error("Non-local binding requires FRONTEND_AGENT_ACCESS_TOKEN and FRONTEND_AGENT_PUBLIC_ORIGIN")
    uvicorn.run(create_app(), host=args.host, port=args.port, timeout_graceful_shutdown=5)
