"""FlipIntel application entrypoint."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import router
from .config import settings
from .db import Database
from .scanner import Scanner
from .utils import iso

log = logging.getLogger("rigradar")


def create_app(db: Database | None = None, autostart: bool | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database = db or Database()
        app.state.db = database
        app.state.scanner = Scanner(database)
        database.execute(
            "UPDATE marketplace_status SET state='unavailable', last_error=? WHERE marketplace='facebook'",
            (
                "Facebook Marketplace requires a signed-in session and does not permit automated collection.",
            ),
        )
        should_start = settings.scanner_autostart if autostart is None else autostart
        app.state.scanner.scheduled_enabled = should_start
        if should_start:
            await app.state.scanner.start()
        log.info("FlipIntel ready — database %s", database.path)
        try:
            yield
        finally:
            await app.state.scanner.stop()
            database.close()

    app = FastAPI(
        title="FlipIntel",
        description="Automatic UK second-hand hardware deal discovery",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )
    app.include_router(router)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        return response

    dist = settings.frontend_dist
    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_frontend(full_path: str):
        if full_path.startswith("api/") or full_path == "api":
            return JSONResponse({"detail": "Not found"}, status_code=404)
        if dist.is_dir():
            candidate = (dist / full_path).resolve()
            try:
                candidate.relative_to(dist.resolve())
            except ValueError:
                return JSONResponse({"detail": "Not found"}, status_code=404)
            if full_path and candidate.is_file():
                return FileResponse(candidate)
            index = dist / "index.html"
            if index.exists():
                return FileResponse(index)
        return HTMLResponse(
            """
            <!doctype html><html><head><title>FlipIntel</title>
            <style>body{font-family:system-ui;background:#0b0f0d;color:#e8f0eb;
            display:grid;place-items:center;min-height:100vh;margin:0}
            .card{max-width:520px;padding:40px;border:1px solid #23352b;border-radius:20px}
            code{background:#15211a;padding:2px 6px;border-radius:6px}</style></head>
            <body><div class="card"><h1>FlipIntel API is running</h1>
            <p>The dashboard has not been built yet. Run:</p>
            <pre><code>cd frontend
npm install
npm run build</code></pre>
            <p>Then refresh this page. API docs: <a style="color:#6ee7a8"
            href="/docs">/docs</a></p></div></body></html>
            """,
            status_code=200,
        )

    return app


app = create_app()
