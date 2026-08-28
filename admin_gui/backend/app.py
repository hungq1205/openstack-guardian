"""FastAPI app factory for the admin GUI backend.

In dev, the frontend runs separately via `npm run dev` (Vite proxies `/api`
to this app -- see `admin_gui/frontend/vite.config.ts`). For normal use,
`npm run build` once and this same process serves the built frontend too --
`_FRONTEND_DIST` mounts only if that build exists, so running the backend
alone (as every existing test already does) is completely unaffected.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from admin_gui.backend.routers import (
    config,
    events,
    failure_patterns,
    masking,
    servers,
    spec_sources,
    tickets,
)

_FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


def create_app(frontend_dist: Path | None = None) -> FastAPI:
    """`frontend_dist` defaults to the real build output; overridable so
    tests can point it at an isolated `tmp_path` instead of depending on
    whether `npm run build` has actually been run in this checkout."""
    dist = frontend_dist if frontend_dist is not None else _FRONTEND_DIST
    app = FastAPI(title="cmp-mcp admin GUI")
    app.include_router(servers.router, prefix="/api")
    app.include_router(events.router, prefix="/api")
    app.include_router(config.router, prefix="/api")
    app.include_router(masking.router, prefix="/api")
    app.include_router(spec_sources.router, prefix="/api")
    app.include_router(failure_patterns.router, prefix="/api")
    app.include_router(tickets.router, prefix="/api")

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    if dist.is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="frontend-assets")

        @app.get("/{full_path:path}")
        async def spa_fallback(full_path: str) -> FileResponse:
            """Every non-API, non-asset path is a client-side route (e.g.
            `/catalog` loaded directly) -- hand back `index.html` and let
            React Router take over. Routed last so it never shadows the
            `/api/*` routers or the `/assets` mount registered above."""
            return FileResponse(dist / "index.html")

    return app


app = create_app()

__all__ = ["app", "create_app"]
