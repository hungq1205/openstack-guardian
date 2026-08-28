"""Entry point for the admin GUI backend.

Binds to 127.0.0.1 only -- this is a local, single-operator tool with no
authentication; nothing here should ever be reachable from another host.

Normal use: `cd admin_gui/frontend && npm install && npm run build`, once,
then `uv run python -m admin_gui.backend.main` from the repo root -- this
one process serves both the API and the built frontend on :8765.

Frontend development: run this the same way, and separately
`npm run dev` in `admin_gui/frontend` (Vite on :5173, proxies `/api` here --
see `vite.config.ts`) for hot reload while editing the UI.
"""

from __future__ import annotations

import uvicorn

from admin_gui.backend.app import app

_HOST = "127.0.0.1"
_PORT = 8765


def main() -> None:
    uvicorn.run(app, host=_HOST, port=_PORT)


if __name__ == "__main__":
    main()
