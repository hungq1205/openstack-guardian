"""Shared SQLite storage for the admin GUI's config/masking/log-event data.

Lazy and best-effort by design: `ensure_schema()` only runs when something
actually needs the store (an MCP server logging a call, the GUI backend
reading/writing config), and every reader in `config_store.py`/`masking.py`
degrades to an empty/default result if the store can't be reached at all --
a server that never touches the GUI behaves exactly as it did before this
module existed, just with a `data/guardian.db` appearing on first real use.

WAL journal mode + a busy timeout let the GUI (a reader) and multiple
concurrent Claude Code sessions (writers logging calls) share the file
without blocking each other out -- not a general multi-tenant solution, just
enough for this project's actual scale (one operator, occasional concurrent
sessions).
"""

from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DATA_DIR_ENV_VAR = "CMP_MCP_DATA_DIR"
_BUSY_TIMEOUT_MS = 5000

# Canonical seed data for the built-in masking patterns -- lives here (schema
# concern) rather than in masking.py, so masking.py can import it without a
# circular dependency (masking.py already needs to import this module for
# connect()/ensure_schema()).
BUILT_IN_MASKING_PATTERNS: tuple[tuple[str, str], ...] = (
    ("EMAIL", r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"),
    ("IPV4", r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    ("PHONE", r"\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    server TEXT NOT NULL,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    status TEXT NOT NULL,
    error_message TEXT,
    result_summary TEXT NOT NULL,
    duration_ms REAL NOT NULL,
    pid INTEGER NOT NULL,
    action TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_server_kind_name ON events(server, kind, name);

CREATE TABLE IF NOT EXISTS masking_patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    regex TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    built_in INTEGER NOT NULL DEFAULT 0,
    sample_retention_count INTEGER NOT NULL DEFAULT 20,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mask_match_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_id INTEGER NOT NULL REFERENCES masking_patterns(id) ON DELETE CASCADE,
    matched_at TEXT NOT NULL,
    matched_text TEXT NOT NULL,
    source TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mask_samples_pattern ON mask_match_samples(pattern_id);

CREATE TABLE IF NOT EXISTS connection_config (
    key TEXT PRIMARY KEY,
    fields_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS spec_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    server TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    domain TEXT,
    spec_path TEXT NOT NULL,
    annotations_path TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pinned_overrides (
    operation_id TEXT PRIMARY KEY,
    pinned INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS annotation_overrides (
    operation_id TEXT PRIMARY KEY,
    fields_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    resource_id TEXT,
    state TEXT NOT NULL DEFAULT 'investigating',
    created_at TEXT NOT NULL,
    closed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tickets_session_id ON tickets(session_id);
CREATE INDEX IF NOT EXISTS idx_tickets_state ON tickets(state);

CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_comments_event_id ON comments(event_id);

CREATE TABLE IF NOT EXISTS ticket_state_transitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    from_state TEXT,
    to_state TEXT NOT NULL,
    ts TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ticket_state_transitions_ticket_id ON ticket_state_transitions(ticket_id);
CREATE INDEX IF NOT EXISTS idx_ticket_state_transitions_ts ON ticket_state_transitions(ts);
"""


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def data_dir() -> Path:
    configured = os.environ.get(_DATA_DIR_ENV_VAR)
    return Path(configured) if configured else _REPO_ROOT / "data"


def db_path() -> Path:
    return data_dir() / "guardian.db"


def connect() -> sqlite3.Connection:
    """Open a connection, creating the data directory and schema on first use."""
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=_BUSY_TIMEOUT_MS / 1000)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema() -> None:
    """Idempotent: safe to call before every operation that needs the store."""
    conn = connect()
    try:
        conn.executescript(_SCHEMA)
        _seed_built_in_masking_patterns(conn)
        _migrate_spec_sources_columns(conn)
        _migrate_events_columns(conn)
        _migrate_tickets_columns(conn)
        conn.commit()
    finally:
        conn.close()


def _migrate_events_columns(conn: sqlite3.Connection) -> None:
    """`events.action` (the real endpoint/command a call actually ran, e.g.
    `GET /admin-api/servers/{id}/`) and `events.ticket_id` (which investigation,
    if any, this event belongs to -- see `tickets.py`) were both added after
    some `data/guardian.db` files already existed -- same reasoning as
    `_migrate_spec_sources_columns`. `idx_events_ticket_id` is created here,
    not in `_SCHEMA`, because `_SCHEMA`'s `executescript` runs before this
    migration on a pre-existing DB and would fail with "no such column"
    if the index were declared there instead."""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(events)")}
    if "action" not in columns:
        conn.execute("ALTER TABLE events ADD COLUMN action TEXT")
    if "ticket_id" not in columns:
        conn.execute("ALTER TABLE events ADD COLUMN ticket_id INTEGER")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_ticket_id ON events(ticket_id)")


def _migrate_spec_sources_columns(conn: sqlite3.Connection) -> None:
    """`spec_sources.name`/`.domain` were added after some `data/guardian.db`
    files already existed -- `CREATE TABLE IF NOT EXISTS` alone never adds a
    column to an already-created table, so a plain reader/writer would 500
    with 'no such column' until this runs once. `replaces` was this
    project's first (short-lived) attempt at the same idea, renamed to
    `domain` before it shipped to anyone but this dev environment -- carry
    forward any value already written under the old name."""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(spec_sources)")}
    if "name" not in columns:
        conn.execute("ALTER TABLE spec_sources ADD COLUMN name TEXT NOT NULL DEFAULT ''")
    if "domain" not in columns:
        if "replaces" in columns:
            conn.execute("ALTER TABLE spec_sources RENAME COLUMN replaces TO domain")
        else:
            conn.execute("ALTER TABLE spec_sources ADD COLUMN domain TEXT")


def _migrate_tickets_columns(conn: sqlite3.Connection) -> None:
    """`tickets.initial_prompt` (the immutable original request, split out
    from `title` once `title` became renamable in the admin GUI) and
    `tickets.deleted_at` (the admin GUI's soft-delete/Trash marker -- an
    admin-GUI-only concept, never touched by any MCP tool) were both added
    after some `data/guardian.db` files already existed -- same reasoning as
    `_migrate_events_columns`. Backfill: a ticket that predates this
    migration never had a separately captured prompt, so best-effort copy
    its existing `title` into `initial_prompt` once -- the only prompt-like
    text this project ever recorded for that row."""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(tickets)")}
    if "initial_prompt" not in columns:
        conn.execute("ALTER TABLE tickets ADD COLUMN initial_prompt TEXT")
        conn.execute("UPDATE tickets SET initial_prompt = title WHERE initial_prompt IS NULL")
    if "deleted_at" not in columns:
        conn.execute("ALTER TABLE tickets ADD COLUMN deleted_at TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tickets_deleted_at ON tickets(deleted_at)")


def _seed_built_in_masking_patterns(conn: sqlite3.Connection) -> None:
    now = utc_now_iso()
    for name, regex in BUILT_IN_MASKING_PATTERNS:
        conn.execute(
            "INSERT OR IGNORE INTO masking_patterns "
            "(name, regex, enabled, built_in, sample_retention_count, created_at, updated_at) "
            "VALUES (?, ?, 1, 1, 20, ?, ?)",
            (name, regex, now, now),
        )


__all__ = [
    "BUILT_IN_MASKING_PATTERNS",
    "connect",
    "data_dir",
    "db_path",
    "ensure_schema",
    "utc_now_iso",
]
