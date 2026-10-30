"""Keep the `source` table in step with config/sources.yaml."""

from __future__ import annotations

import sqlite3

from .config import SourceConfig, load_sources
from .db import transaction


def sync_sources(conn: sqlite3.Connection, sources: list[SourceConfig] | None = None) -> int:
    """Insert new sources and update changed ones; return how many rows actually changed.

    Unchanged rows are not rewritten, so callers can tell whether the database changed
    (and therefore whether an audit event is due).
    """
    sources = sources if sources is not None else load_sources()
    before = conn.total_changes
    with transaction(conn):
        for s in sources:
            conn.execute(
                """INSERT INTO source (id, name, kind, acquisition, base_url, auth, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET name=excluded.name, kind=excluded.kind,
                     acquisition=excluded.acquisition, base_url=excluded.base_url,
                     auth=excluded.auth, notes=excluded.notes
                   WHERE source.name IS NOT excluded.name OR source.kind IS NOT excluded.kind
                      OR source.acquisition IS NOT excluded.acquisition OR source.base_url IS NOT excluded.base_url
                      OR source.auth IS NOT excluded.auth OR source.notes IS NOT excluded.notes""",
                (s.id, s.name, s.kind, s.acquisition, s.base_url, s.auth, s.notes),
            )
    return conn.total_changes - before
