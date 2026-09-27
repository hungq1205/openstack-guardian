"""FastAPI app for the hands-on VM-creation exercise set. Serves the static
frontend plus a small API that runs each exercise's init/check/answer logic
against the real OpenStack cluster through a self-managed SSH tunnel.

Run from the cmp-mcp repo root:
    PYTHONPATH=. uv run --with openstacksdk --with fastapi --with "uvicorn[standard]" \\
        uvicorn ops.openstack_testing.exercises.backend.app:app --port 8910
"""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, state_store, tunnel_manager
from .exercises_data import EXERCISES, EXERCISES_BY_ID
from .os_client import get_conn

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    tunnel_manager.start()
    yield
    tunnel_manager.stop()


app = FastAPI(lifespan=lifespan)


@app.middleware("http")
async def no_cache_static(request, call_next):
    """This app's static files change constantly during development, and
    browsers were caching them indefinitely (no Cache-Control was set at
    all) -- forcing a revalidation on every request keeps edits visible
    without needing a hard-refresh or cache-busting query strings."""
    response = await call_next(request)
    if request.url.path.startswith("/static/") or request.url.path == "/":
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


class AnswerBody(BaseModel):
    answer: str


class PositionBody(BaseModel):
    index: int


def _safe_conn():
    try:
        return get_conn(), None
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"


@app.get("/api/tunnel/status")
def tunnel_status():
    return {"up": tunnel_manager.is_up()}


@app.get("/api/position")
def get_position():
    return {"index": state_store.get_state()["position"]}


@app.post("/api/position")
def set_position(body: PositionBody):
    if not (0 <= body.index < len(EXERCISES)):
        raise HTTPException(400, "index out of range")
    state_store.set_position(body.index)
    return {"ok": True}


@app.get("/api/exercises")
def list_exercises():
    state = state_store.get_state()
    completed = set(state["completed"])
    warned = set(state["warned"])
    return [
        {
            "id": e.id,
            "title": e.title,
            "tier": e.tier,
            "risk": e.risk,
            "goal": e.goal,
            "task": e.task,
            "commands": [
                {"name": c.name, "description": c.description, "options": c.options}
                for c in e.commands
            ],
            "warning": e.warning,
            "has_question": e.question is not None,
            "question_prompt": e.question.prompt if e.question else None,
            "completed": e.id in completed,
            "warned": e.id in warned,
        }
        for e in EXERCISES
    ]


@app.get("/api/exercises/{exercise_id}/hint")
def get_hint(exercise_id: str):
    ex = EXERCISES_BY_ID.get(exercise_id)
    if ex is None:
        raise HTTPException(404, "unknown exercise")
    return {"hint": ex.hint}


@app.get("/api/exercises/{exercise_id}/solution")
def get_solution(exercise_id: str):
    ex = EXERCISES_BY_ID.get(exercise_id)
    if ex is None:
        raise HTTPException(404, "unknown exercise")
    return {"solution": ex.solution}


@app.post("/api/exercises/{exercise_id}/warned")
def mark_warned(exercise_id: str):
    if exercise_id not in EXERCISES_BY_ID:
        raise HTTPException(404, "unknown exercise")
    state_store.mark_warned(exercise_id)
    return {"ok": True}


@app.post("/api/exercises/{exercise_id}/init")
def run_init(exercise_id: str):
    ex = EXERCISES_BY_ID.get(exercise_id)
    if ex is None:
        raise HTTPException(404, "unknown exercise")
    if not tunnel_manager.is_up():
        return {"ok": False, "message": "tunnel to the OpenStack VM isn't up yet -- wait a few seconds and retry"}
    conn, err = _safe_conn()
    if err:
        return {"ok": False, "message": f"couldn't connect: {err}"}
    try:
        ok, message = ex.init_fn(conn)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": f"{type(exc).__name__}: {exc}"}
    return {"ok": ok, "message": message}


@app.post("/api/exercises/{exercise_id}/check")
def run_check(exercise_id: str):
    ex = EXERCISES_BY_ID.get(exercise_id)
    if ex is None:
        raise HTTPException(404, "unknown exercise")
    if ex.check_fn is None:
        raise HTTPException(400, "this exercise has no check")
    if not tunnel_manager.is_up():
        return {"passed": False, "message": "tunnel to the OpenStack VM isn't up yet -- wait a few seconds and retry"}
    conn, err = _safe_conn()
    if err:
        return {"passed": False, "message": f"couldn't connect: {err}"}
    try:
        passed, message = ex.check_fn(conn)
    except Exception as exc:  # noqa: BLE001
        return {"passed": False, "message": f"{type(exc).__name__}: {exc}"}
    if passed:
        state_store.mark_completed(exercise_id)
    return {"passed": passed, "message": message}


@app.post("/api/exercises/{exercise_id}/answer")
def submit_answer(exercise_id: str, body: AnswerBody):
    ex = EXERCISES_BY_ID.get(exercise_id)
    if ex is None:
        raise HTTPException(404, "unknown exercise")
    if ex.question is None:
        raise HTTPException(400, "this exercise has no question")
    conn, err = _safe_conn()
    if err:
        return {"correct": False, "message": f"couldn't connect: {err}"}
    try:
        correct, message = ex.question.checker(conn, body.answer)
    except Exception as exc:  # noqa: BLE001
        return {"correct": False, "message": f"{type(exc).__name__}: {exc}"}
    return {"correct": correct, "message": message}


# One real interactive shell on the OpenStack VM per browser tab, bridged over a
# WebSocket via xterm.js on the frontend. admin-openrc.sh is sourced and the
# openstack CLI auto-installed (best-effort, only if missing) so the terminal
# is immediately usable for the exercises without a manual setup step.
_REMOTE_SHELL_CMD = (
    "source /etc/kolla/admin-openrc.sh; "
    "export PATH=$HOME/.local/bin:$PATH; "
    "command -v openstack >/dev/null 2>&1 || "
    "pip install --user -q python-openstackclient 2>/dev/null || "
    "pip install --user --break-system-packages -q python-openstackclient 2>/dev/null; "
    "exec bash -l"
)


@app.websocket("/ws/terminal")
async def terminal_ws(websocket: WebSocket):
    await websocket.accept()
    proc = await asyncio.create_subprocess_exec(
        "ssh", "-tt", "-p", str(config.SSH_PORT), "-i", config.SSH_KEY,
        "-o", "StrictHostKeyChecking=accept-new",
        f"{config.SSH_USER}@{config.SSH_HOST}", _REMOTE_SHELL_CMD,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    async def pump_output():
        try:
            while True:
                data = await proc.stdout.read(4096)
                if not data:
                    break
                await websocket.send_text(data.decode(errors="replace"))
        except Exception:  # noqa: BLE001
            pass

    output_task = asyncio.create_task(pump_output())
    try:
        while True:
            msg = await websocket.receive_text()
            if proc.stdin is None or proc.returncode is not None:
                break
            proc.stdin.write(msg.encode())
            await proc.stdin.drain()
    except WebSocketDisconnect:
        pass
    finally:
        output_task.cancel()
        if proc.returncode is None:
            proc.terminate()


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index():
    return FileResponse(str(STATIC_DIR / "index.html"))
