"""Append-only audit log with a SHA-256 hash chain.

Each event's hash covers its own content plus the previous event's hash.
SQLite triggers forbid UPDATE/DELETE on audit_event; the hash chain also makes
tampering detectable by someone who drops the triggers or edits the file,
via `verify_chain`.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from .db import transaction
from .ids import new_id

GENESIS_HASH = "0" * 64


def _canonical(payload) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def compute_hash(*, event_id: str, occurred_at: str, actor: str, action: str,
                 entity_type: str | None, entity_id: str | None, payload_json: str, prev_hash: str) -> str:
    material = _canonical({
        "id": event_id, "occurred_at": occurred_at, "actor": actor, "action": action,
        "entity_type": entity_type, "entity_id": entity_id,
        "payload": payload_json, "prev_hash": prev_hash,
    })
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def record(conn: sqlite3.Connection, *, actor: str, action: str, entity_type: str | None = None,
           entity_id: str | None = None, payload: dict | None = None, in_transaction: bool = False) -> str:
    """Append one event and return its id.

    The payload is stored exactly as the canonical JSON string that is hashed.
    Pass in_transaction=True when the caller already holds a write transaction.
    """
    payload_json = _canonical(payload or {})
    occurred_at = utc_now()
    event_id = new_id("audit_event")

    def _write():
        row = conn.execute("SELECT event_hash FROM audit_event ORDER BY seq DESC LIMIT 1").fetchone()
        prev_hash = row[0] if row else GENESIS_HASH
        event_hash = compute_hash(event_id=event_id, occurred_at=occurred_at, actor=actor, action=action,
                                  entity_type=entity_type, entity_id=entity_id, payload_json=payload_json,
                                  prev_hash=prev_hash)
        conn.execute(
            """INSERT INTO audit_event (id, occurred_at, actor, action, entity_type, entity_id,
                                        payload, prev_hash, event_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (event_id, occurred_at, actor, action, entity_type, entity_id, payload_json, prev_hash, event_hash),
        )

    if in_transaction:
        _write()
    else:
        with transaction(conn):
            _write()
    return event_id


@dataclass(frozen=True)
class ChainReport:
    events_checked: int
    ok: bool
    first_bad_seq: int | None = None
    reason: str | None = None


def verify_chain(conn: sqlite3.Connection) -> ChainReport:
    prev = GENESIS_HASH
    n = 0
    rows = conn.execute(
        """SELECT seq, id, occurred_at, actor, action, entity_type, entity_id, payload, prev_hash, event_hash
           FROM audit_event ORDER BY seq""")
    for seq, eid, occ, actor, action, etype, ent_id, payload, prev_hash, event_hash in rows:
        n += 1
        if prev_hash != prev:
            return ChainReport(n, False, seq, "prev_hash does not match previous event")
        expected = compute_hash(event_id=eid, occurred_at=occ, actor=actor, action=action, entity_type=etype,
                                entity_id=ent_id, payload_json=payload, prev_hash=prev_hash)
        if expected != event_hash:
            return ChainReport(n, False, seq, "event content does not match its hash")
        prev = event_hash
    return ChainReport(n, True)
