#!/usr/bin/env python3
"""Seed the local mock Elasticsearch with mock-logs-23.csv.

Transforms the raw Kibana Discover CSV export into structured gateway-log
documents (parsing the free-text `log` column into level/logger/request_id/
timestamp, plus method/path/status/duration/user/client_ip for access-log
rows) and bulk-loads them into a real index via plain HTTP -- no
`elasticsearch` package dependency, matching cmp-mcp's own client convention
of raw REST calls.

Usage:
    docker compose up -d
    python seed.py [--es-url http://localhost:9200] [--index iaas-api-2026.08.23]
                    [--csv mock-logs-23.csv]

Re-running is safe: the index is dropped and rebuilt from scratch each time
(this is disposable local mock data, not something to accumulate).
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

_DEFAULT_ES_URL = "http://localhost:9200"
_DEFAULT_INDEX = "iaas-api-2026.08.23"
_DEFAULT_CSV = Path(__file__).with_name("mock-logs-23.csv")
_BULK_BATCH_SIZE = 1000

# Matches e.g.:
#   INFO     [2026-08-23 23:30:00,036] [aa6466d159b24f76994da36192023252] log.response: method=GET ...
#   ERROR    [2026-08-23 23:26:37,519] [df3daa616eb14781866673d4c67d062e] vblockstorage.views.volume_views: Volume ...
_STRUCTURED_LINE = re.compile(
    r"^(?P<level>INFO|WARNING|ERROR|DEBUG)\s+"
    r"\[(?P<ts>[^\]]+)\]\s+\[(?P<request_id>[0-9a-f]+)\]\s+"
    r"(?P<logger>[\w.]+):\s?(?P<message>.*)$"
)

# Applied to the `message` group only when logger == "log.response".
_ACCESS_LOG_DETAIL = re.compile(
    r"^method=(?P<method>\S+) path=(?P<path_full>\S+) status=(?P<status>\d+) "
    r"duration=(?P<duration_ms>\d+)ms user=(?P<user_id>\S+) username=(?P<username>\S*) "
    r"client_ip=(?P<client_ip>\S+)$"
)

# Applied only to rows that don't match _STRUCTURED_LINE at all (raw stderr /
# uWSGI worker-lifecycle chatter) -- everything else in that bucket (the one
# incident's traceback + raw access line) is left unclassified on purpose.
_NOISE_OTHER_PATTERNS = (
    re.compile(r"^Respawned uWSGI worker"),
    re.compile(r"^worker \d+ killed successfully"),
    re.compile(r"mem-collector thread started"),
    re.compile(r"^WSGI app \d+ \(mountpoint="),
    re.compile(r"^\.\.\.The work of process \d+ is done\. Seeya!$"),
    re.compile(r"^\s*warnings\.warn\(msg\)$"),
    re.compile(r"UserWarning: The 'interface' argument is deprecated"),
)

_BRACKETED_TS_FORMAT = "%Y-%m-%d %H:%M:%S,%f"
_KIBANA_TS_FORMAT = "%b %d, %Y @ %H:%M:%S.%f"

_INDEX_MAPPING = {
    "mappings": {
        "properties": {
            "@timestamp": {"type": "date"},
            "level": {"type": "keyword"},
            "logger": {"type": "keyword"},
            "request_id": {"type": "keyword"},
            "message": {"type": "text"},
            "raw_log": {"type": "text"},
            "hostname": {"type": "keyword"},
            "hostaddress": {"type": "ip"},
            "method": {"type": "keyword"},
            "path": {"type": "keyword"},
            "status": {"type": "integer"},
            "duration_ms": {"type": "integer"},
            "user_id": {"type": "integer"},
            "username": {"type": "keyword"},
            "client_ip": {"type": "ip"},
            "noise_template": {"type": "keyword"},
            "source_index": {"type": "keyword"},
            "source_row": {"type": "integer"},
        }
    }
}


def _classify_noise(level: str, logger: str | None, message: str) -> str | None:
    if level == "WARNING" and logger == "neutronclient.v2_0.client":
        return "neutronclient_deprecation"
    if level == "WARNING" and logger == "matplotlib":
        return "matplotlib_cache_notice"
    if level == "OTHER" and any(pattern.search(message) for pattern in _NOISE_OTHER_PATTERNS):
        return "uwsgi_lifecycle"
    return None


def _parse_row(row: dict[str, str], row_index: int, index_name: str) -> dict[str, Any]:
    log_line = row["log"]
    doc: dict[str, Any] = {
        "hostname": row["hostname"] or None,
        "hostaddress": row["hostaddress"] or None,
        "raw_log": log_line,
        "source_index": index_name,
        "source_row": row_index,
    }

    match = _STRUCTURED_LINE.match(log_line)
    if match:
        level = match.group("level")
        logger = match.group("logger")
        message = match.group("message")
        doc.update(
            {
                "@timestamp": datetime.strptime(match.group("ts"), _BRACKETED_TS_FORMAT).isoformat(),
                "level": level,
                "logger": logger,
                "request_id": match.group("request_id"),
                "message": message,
            }
        )
        if logger == "log.response":
            detail = _ACCESS_LOG_DETAIL.match(message)
            if detail:
                user_id_raw = detail.group("user_id")
                username_raw = detail.group("username")
                doc.update(
                    {
                        "method": detail.group("method"),
                        "path": detail.group("path_full").split("?", 1)[0],
                        "status": int(detail.group("status")),
                        "duration_ms": int(detail.group("duration_ms")),
                        "user_id": None if user_id_raw == "None" else int(user_id_raw),
                        "username": username_raw or None,
                        "client_ip": detail.group("client_ip"),
                    }
                )
        doc["noise_template"] = _classify_noise(level, logger, message)
    else:
        doc.update(
            {
                "@timestamp": datetime.strptime(row["@timestamp"], _KIBANA_TS_FORMAT).isoformat(),
                "level": "OTHER",
                "logger": None,
                "request_id": None,
                "message": log_line,
            }
        )
        doc["noise_template"] = _classify_noise("OTHER", None, log_line)

    return doc


def _iter_documents(csv_path: Path, index_name: str) -> list[tuple[str, dict[str, Any]]]:
    documents: list[tuple[str, dict[str, Any]]] = []
    with csv_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row_index, row in enumerate(reader):
            documents.append((row["_id"], _parse_row(row, row_index, index_name)))
    return documents


def _request(
    url: str, *, method: str = "GET", data: bytes | None = None, headers: dict[str, str] | None = None
) -> dict[str, Any] | None:
    """Never raises on 404 (treated as "not found" -- fine for idempotent
    delete/create); raises on any other HTTP error after printing the body,
    since a seed script should fail loudly rather than silently skip data."""
    request = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(request) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        error_body = exc.read()
        print(f"HTTP {exc.code} from {url}: {error_body.decode('utf-8', errors='replace')}", file=sys.stderr)
        raise
    if not body:
        return None
    return json.loads(body)


def _reset_index(es_url: str, index_name: str) -> None:
    if _request(f"{es_url}/{index_name}", method="DELETE") is not None:
        print(f"Deleted existing index {index_name!r}")
    _request(
        f"{es_url}/{index_name}",
        method="PUT",
        data=json.dumps(_INDEX_MAPPING).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    print(f"Created index {index_name!r} with explicit mapping")


def _bulk_load(es_url: str, index_name: str, documents: list[tuple[str, dict[str, Any]]]) -> None:
    total = len(documents)
    for start in range(0, total, _BULK_BATCH_SIZE):
        batch = documents[start : start + _BULK_BATCH_SIZE]
        lines: list[str] = []
        for doc_id, doc in batch:
            lines.append(json.dumps({"index": {"_index": index_name, "_id": doc_id}}))
            lines.append(json.dumps(doc))
        payload = ("\n".join(lines) + "\n").encode("utf-8")
        result = _request(
            f"{es_url}/{index_name}/_bulk",
            method="POST",
            data=payload,
            headers={"Content-Type": "application/x-ndjson"},
        )
        if result and result.get("errors"):
            failed = [item["index"] for item in result["items"] if item["index"].get("status", 200) >= 300]
            print(f"WARNING: {len(failed)} of {len(batch)} documents failed in this batch", file=sys.stderr)
            for item in failed[:3]:
                print(f"  {item.get('error')}", file=sys.stderr)
        print(f"Indexed {min(start + _BULK_BATCH_SIZE, total)}/{total} documents")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--es-url", default=_DEFAULT_ES_URL, help=f"default: {_DEFAULT_ES_URL}")
    parser.add_argument("--index", default=_DEFAULT_INDEX, help=f"default: {_DEFAULT_INDEX}")
    parser.add_argument("--csv", type=Path, default=_DEFAULT_CSV, help=f"default: {_DEFAULT_CSV}")
    args = parser.parse_args()
    es_url = args.es_url.rstrip("/")

    print(f"Parsing {args.csv}...")
    documents = _iter_documents(args.csv, args.index)
    expected = len(documents)
    level_counts = Counter(doc["level"] for _, doc in documents)
    print(f"Parsed {expected} rows. Level breakdown: {dict(level_counts)}")

    print(f"Resetting index {args.index!r} at {es_url}...")
    _reset_index(es_url, args.index)

    print("Bulk loading...")
    _bulk_load(es_url, args.index, documents)

    _request(f"{es_url}/{args.index}/_refresh", method="POST")
    count_result = _request(f"{es_url}/{args.index}/_count")
    actual_count = count_result.get("count") if count_result else None
    if actual_count == expected:
        print(f"OK -- index {args.index!r} now has {actual_count} documents")
    else:
        print(f"WARNING -- expected {expected} documents, index reports {actual_count}", file=sys.stderr)

    error_docs = [doc for _, doc in documents if doc["level"] == "ERROR"]
    if error_docs:
        print(f"\n{len(error_docs)} ERROR-level document(s) indexed, e.g.:")
        print(f"  request_id={error_docs[0].get('request_id')}: {error_docs[0]['message']}")


if __name__ == "__main__":
    main()
