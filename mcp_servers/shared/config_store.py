"""Runtime-editable config the admin GUI writes and the MCP servers read at
server-build time (once per process, same as today's `from_env()` timing --
these servers are ephemeral stdio subprocesses re-spawned per Claude Code
session, so there's no need for live reload of an already-running server).

Every getter degrades to "nothing configured" (`None` / `{}` / `[]`) if the
store can't be reached or has no row for the given key -- callers layer this
under an env-var check that still wins (see each client's `from_env()`), so
a server run without ever touching the GUI is unaffected.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from mcp_servers.shared import db


def get_config_value(prefix: str, field: str) -> str | None:
    """One field of one connection's config, e.g. `get_config_value("CMP_ADMIN_V2", "base_url")`."""
    value = get_connection_config(prefix).get(field)
    return value or None


def get_connection_config(key: str) -> dict[str, Any]:
    try:
        db.ensure_schema()
        conn = db.connect()
    except OSError:
        return {}
    try:
        row = conn.execute(
            "SELECT fields_json FROM connection_config WHERE key = ?", (key,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return {}
    result: dict[str, Any] = json.loads(row["fields_json"])
    return result


def set_connection_config(key: str, fields: dict[str, Any]) -> None:
    db.ensure_schema()
    conn = db.connect()
    try:
        now = db.utc_now_iso()
        conn.execute(
            "INSERT INTO connection_config (key, fields_json, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET fields_json = excluded.fields_json, "
            "updated_at = excluded.updated_at",
            (key, json.dumps(fields), now),
        )
        conn.commit()
    finally:
        conn.close()


@dataclass(frozen=True)
class SpecSourceRow:
    id: int
    server: str
    name: str
    spec_path: str
    annotations_path: str | None
    domain: str | None
    """One of `main.BUILT_IN_SPEC_DOMAINS` ('server'/'block_storage'/
    'network') if this row is the (edited, or explicitly disabled) version
    of that built-in pair, or `None` for a purely additive extra pair that
    sits alongside all 3 built-ins."""
    enabled: bool
    sort_order: int


def get_spec_sources(server: str, *, enabled_only: bool = True) -> list[SpecSourceRow]:
    try:
        db.ensure_schema()
        conn = db.connect()
    except OSError:
        return []
    try:
        query = (
            "SELECT id, server, name, spec_path, annotations_path, domain, enabled, sort_order "
            "FROM spec_sources WHERE server = ?"
        )
        params: tuple[Any, ...] = (server,)
        if enabled_only:
            query += " AND enabled = 1"
        query += " ORDER BY sort_order, id"
        rows = conn.execute(query, params).fetchall()
    finally:
        conn.close()
    return [
        SpecSourceRow(
            id=row["id"],
            server=row["server"],
            name=row["name"],
            spec_path=row["spec_path"],
            annotations_path=row["annotations_path"],
            domain=row["domain"],
            enabled=bool(row["enabled"]),
            sort_order=row["sort_order"],
        )
        for row in rows
    ]


def add_spec_source(
    server: str,
    name: str,
    spec_path: str,
    annotations_path: str | None = None,
    *,
    domain: str | None = None,
    sort_order: int = 0,
) -> int:
    db.ensure_schema()
    conn = db.connect()
    try:
        cursor = conn.execute(
            "INSERT INTO spec_sources "
            "(server, name, spec_path, annotations_path, domain, enabled, sort_order, created_at) "
            "VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
            (server, name, spec_path, annotations_path, domain, sort_order, db.utc_now_iso()),
        )
        conn.commit()
        row_id = cursor.lastrowid
        assert row_id is not None
        return row_id
    finally:
        conn.close()


def upsert_domain_spec_source(
    server: str,
    domain: str,
    *,
    name: str | None = None,
    spec_path: str | None = None,
    annotations_path: str | None = None,
    enabled: bool | None = None,
) -> SpecSourceRow:
    """Edit (or explicitly disable) one of the 3 built-in domains, or create
    the override row on first edit. Only the fields actually passed are
    changed -- an existing row's other fields, and any field not yet
    materialized, fall back to `default_...` (the built-in path) so a
    partial edit (e.g. just toggling `enabled`) never has to know or resend
    the other fields."""
    db.ensure_schema()
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT id, name, spec_path, annotations_path, enabled FROM spec_sources "
            "WHERE server = ? AND domain = ?",
            (server, domain),
        ).fetchone()
        if row is None:
            if spec_path is None:
                raise ValueError(f"no existing override for domain {domain!r} and no spec_path given")
            cursor = conn.execute(
                "INSERT INTO spec_sources "
                "(server, name, domain, spec_path, annotations_path, enabled, sort_order, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
                (
                    server,
                    name if name is not None else domain,
                    domain,
                    spec_path,
                    annotations_path,
                    int(enabled) if enabled is not None else 1,
                    db.utc_now_iso(),
                ),
            )
            row_id = cursor.lastrowid
        else:
            row_id = row["id"]
            conn.execute(
                "UPDATE spec_sources SET name = ?, spec_path = ?, annotations_path = ?, enabled = ? "
                "WHERE id = ?",
                (
                    name if name is not None else row["name"],
                    spec_path if spec_path is not None else row["spec_path"],
                    annotations_path if annotations_path is not None else row["annotations_path"],
                    int(enabled) if enabled is not None else row["enabled"],
                    row_id,
                ),
            )
        conn.commit()
        result = conn.execute(
            "SELECT id, server, name, spec_path, annotations_path, domain, enabled, sort_order "
            "FROM spec_sources WHERE id = ?",
            (row_id,),
        ).fetchone()
    finally:
        conn.close()
    assert result is not None
    return SpecSourceRow(
        id=result["id"],
        server=result["server"],
        name=result["name"],
        spec_path=result["spec_path"],
        annotations_path=result["annotations_path"],
        domain=result["domain"],
        enabled=bool(result["enabled"]),
        sort_order=result["sort_order"],
    )


def set_spec_source_enabled(source_id: int, enabled: bool) -> None:
    db.ensure_schema()
    conn = db.connect()
    try:
        conn.execute("UPDATE spec_sources SET enabled = ? WHERE id = ?", (int(enabled), source_id))
        conn.commit()
    finally:
        conn.close()


def delete_spec_source(source_id: int) -> None:
    db.ensure_schema()
    conn = db.connect()
    try:
        conn.execute("DELETE FROM spec_sources WHERE id = ?", (source_id,))
        conn.commit()
    finally:
        conn.close()


def get_pinned_overrides() -> dict[str, bool]:
    try:
        db.ensure_schema()
        conn = db.connect()
    except OSError:
        return {}
    try:
        rows = conn.execute("SELECT operation_id, pinned FROM pinned_overrides").fetchall()
    finally:
        conn.close()
    return {row["operation_id"]: bool(row["pinned"]) for row in rows}


def set_pinned_override(operation_id: str, pinned: bool) -> None:
    db.ensure_schema()
    conn = db.connect()
    try:
        now = db.utc_now_iso()
        conn.execute(
            "INSERT INTO pinned_overrides (operation_id, pinned, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(operation_id) DO UPDATE SET pinned = excluded.pinned, "
            "updated_at = excluded.updated_at",
            (operation_id, int(pinned), now),
        )
        conn.commit()
    finally:
        conn.close()


def get_annotation_overrides() -> dict[str, dict[str, Any]]:
    """Every operation_id -> edited-field overlay from the GUI's tool-detail
    editor. Sparse: only fields an admin actually changed are present in each
    row's dict, layered over the curated `ToolAnnotation` the same way
    `pinned_overrides` layers over `curated.pinned`."""
    try:
        db.ensure_schema()
        conn = db.connect()
    except OSError:
        return {}
    try:
        rows = conn.execute("SELECT operation_id, fields_json FROM annotation_overrides").fetchall()
    finally:
        conn.close()
    return {row["operation_id"]: json.loads(row["fields_json"]) for row in rows}


def set_annotation_override(operation_id: str, fields: dict[str, Any]) -> None:
    """Merge-upsert -- an edit to one field doesn't clobber previously edited
    fields for the same operation."""
    db.ensure_schema()
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT fields_json FROM annotation_overrides WHERE operation_id = ?", (operation_id,)
        ).fetchone()
        merged = {**(json.loads(row["fields_json"]) if row else {}), **fields}
        now = db.utc_now_iso()
        conn.execute(
            "INSERT INTO annotation_overrides (operation_id, fields_json, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(operation_id) DO UPDATE SET fields_json = excluded.fields_json, "
            "updated_at = excluded.updated_at",
            (operation_id, json.dumps(merged), now),
        )
        conn.commit()
    finally:
        conn.close()


__all__ = [
    "SpecSourceRow",
    "add_spec_source",
    "delete_spec_source",
    "get_annotation_overrides",
    "get_config_value",
    "get_connection_config",
    "get_pinned_overrides",
    "get_spec_sources",
    "set_annotation_override",
    "set_connection_config",
    "set_pinned_override",
    "set_spec_source_enabled",
    "upsert_domain_spec_source",
]
