"""One-time migration: backfill `tool_annotation_overrides` rows so cmp-
admin's opt-out `hidden` flip (see `mcp_servers.openapi_bridge.ToolAnnotation`)
doesn't silently make every never-pinned operation visible.

Context: `hidden` (opt-out -- visible unless explicitly hidden) replaces the
old opt-in `pinned` field (hidden unless explicitly pinned). `db.py`'s
`_migrate_legacy_overrides` already copies the *old override tables'* rows
(`pinned_overrides`/`annotation_overrides`) into the new unified
`tool_annotation_overrides` automatically, at schema-init time -- but those
tables only ever held rows for the handful of operations an admin actually
touched through the GUI. Every operation that was simply never pinned (the
overwhelming majority -- that's what "opt-in, default hidden" means) has no
old row to migrate, and so gets no new row either: read fresh under the new
opt-out model, it now defaults to `hidden=False` (visible). This script
closes that gap directly, by re-deriving each operation's old pinned state
from the curated JSON files' original `pinned` key -- read raw here,
deliberately bypassing `openapi_bridge.load_annotations` (already repointed
at the new `hidden` key by the time this migration is written) -- and
upserting the equivalent `tool_annotation_overrides` row.

Complements, rather than replaces, separately rewriting the curated JSON
files' own `pinned` key to `hidden` in place (see this repo's CLAUDE.md and
`mcp_servers/annotations/*.json`): the JSON rewrite is what fixes the
default for a fresh install or an isolated test DB, which has no DB
overrides to fall back on; this script is what fixes it immediately for
*this* database, and stays a safety net after the JSON rewrite too, since it
covers whatever's actually live right now regardless of which JSON (built-in
or an admin-uploaded spec source) an operation's curated annotation happens
to come from.

Idempotent -- safe to re-run. Reading each entry's raw `pinned` key directly
means it stays correct even if run *after* the JSON files have themselves
been rewritten to `hidden`: an already-rewritten entry simply has no
`pinned` key left, which reads as `pinned=False` here, the exact same value
an entry that was always absent already gets -- so a previously-pinned
operation whose JSON has already been flipped to `hidden: false` needs no
correction from this script (it's already correct at the JSON layer, no DB
override required), and this script harmlessly leaves it alone rather than
re-deriving a wrong answer from a key that's no longer there.

Only cmp-admin has a pinned/hidden concept as of this migration (cmp-logs's
retrofit and cmp-notify's deliberate exclusion are separate, later steps --
see CLAUDE.md) -- hardcoded to "cmp-admin" throughout, not a general
cross-server tool.

    uv run python -m mcp_servers.cmp_admin_mcp.migrations.backfill_tool_visibility

Moved here from the old `mcp_servers/shared/migrations/` (2026-09-14, when the genuinely
shared modules were extracted into the standalone `guardian_platform` package) -- this one
was never actually shared/generic, it's hardcoded to cmp-admin as noted above, so it belongs
in cmp-admin's own tree, not the platform-wide library.
"""

from __future__ import annotations

import json
from typing import Any

from guardian_platform.config_store import set_tool_annotation_override

from mcp_servers.cmp_admin_mcp.main import sources

_SERVER_ID = "cmp-admin"


def _raw_pinned_by_operation_id() -> dict[str, bool]:
    """Every operation_id's raw `pinned` value straight off disk, across
    every annotation file `sources()` currently resolves (built-in domains
    plus any GUI-configured extra spec source) -- not `load_annotations()`,
    which by now reads `hidden` instead. Missing key (never pinned, or
    already rewritten to `hidden`) reads as `False`, same as the old
    opt-in model's own default."""
    pinned: dict[str, bool] = {}
    for source in sources():
        if source.annotations_path is None or not source.annotations_path.exists():
            continue
        raw: dict[str, dict[str, Any]] = json.loads(source.annotations_path.read_text(encoding="utf-8"))
        for operation_id, entry in raw.items():
            pinned[operation_id] = bool(entry.get("pinned", False))
    return pinned


def backfill() -> int:
    """Upsert a `tool_annotation_overrides` row for every operation with a
    curated annotation entry, `hidden = not <its old raw pinned value>`.
    Returns the number of rows written."""
    pinned_by_id = _raw_pinned_by_operation_id()
    for operation_id, was_pinned in pinned_by_id.items():
        set_tool_annotation_override(_SERVER_ID, operation_id, {"hidden": not was_pinned})
    return len(pinned_by_id)


def main() -> None:
    count = backfill()
    print(f"backfilled hidden state for {count} cmp-admin operation(s)")


if __name__ == "__main__":
    main()
