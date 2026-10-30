"""Which copy of a database is newer, older or divergent — ADR-0007.

Every change to the knowledge base appends an event to the hash-chained audit log.
The last event (seq, hash) — the "head" — therefore identifies the whole history:
  * copy B contains copy A's head at the same position  →  B is A plus later work (A is older);
  * neither contains the other's head                    →  the copies DIVERGED: work was done on both.
Copies of different databases are told apart by db_instance.id.

Invariant this relies on: every command that changes the database records an audit event
(tested in tests/test_cli.py::test_every_change_is_audited).
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .audit import GENESIS_HASH
from .db import connect_readonly
from .localstate import LocalState


@dataclass(frozen=True)
class Head:
    database_id: str | None
    seq: int
    hash: str


def database_id(conn: sqlite3.Connection) -> str | None:
    try:
        row = conn.execute("SELECT id FROM db_instance").fetchone()
    except sqlite3.OperationalError:
        return None
    return row[0] if row else None


def head(conn: sqlite3.Connection) -> Head:
    row = conn.execute("SELECT seq, event_hash FROM audit_event ORDER BY seq DESC LIMIT 1").fetchone()
    return Head(database_id(conn), row[0] if row else 0, row[1] if row else GENESIS_HASH)


def hash_at(conn: sqlite3.Connection, seq: int) -> str | None:
    if seq == 0:
        return GENESIS_HASH
    row = conn.execute("SELECT event_hash FROM audit_event WHERE seq = ?", (seq,)).fetchone()
    return row[0] if row else None


def contains(conn: sqlite3.Connection, h: Head) -> bool:
    """True if `h` is a state in this database's history (h itself or an ancestor of its head)."""
    return hash_at(conn, h.seq) == h.hash


def common_seq(a: sqlite3.Connection, b: sqlite3.Connection) -> int:
    """Last position at which the two histories are identical (0 = nothing in common)."""
    ours = dict(a.execute("SELECT seq, event_hash FROM audit_event").fetchall())
    last = 0
    for seq, h in b.execute("SELECT seq, event_hash FROM audit_event ORDER BY seq"):
        if ours.get(seq) != h:
            break
        last = seq
    return last


def relation(local: sqlite3.Connection, other: sqlite3.Connection) -> str:
    """How `other` relates to `local`: identical | other_ahead | other_behind | diverged | different_database."""
    lh, oh = head(local), head(other)
    if lh.database_id != oh.database_id:
        return "different_database"
    if lh == oh:
        return "identical"
    if lh.seq < oh.seq and contains(other, lh):
        return "other_ahead"
    if oh.seq < lh.seq and contains(local, oh):
        return "other_behind"
    return "diverged"


# ---------- check against this PC's memory ----------

BLOCKING = frozenset({"older", "diverged", "behind_unknown"})
_SEVERITY = {"first_time": 0, "ok": 0, "no_identity": 0, "behind_unknown": 1, "older": 2, "diverged": 3}


@dataclass
class Report:
    status: str            # first_time | ok | no_identity | behind_unknown | older | diverged
    current: Head
    reference: dict | None = None
    common_seq: int | None = None
    message: str = ""
    details: dict = field(default_factory=dict)

    @property
    def blocking(self) -> bool:
        return self.status in BLOCKING

    def as_dict(self) -> dict:
        d = {"stato": self.status, "bloccante": self.blocking, "database_id": self.current.database_id,
             "posizione_attuale": self.current.seq}
        if self.reference:
            d["riferimento"] = {k: self.reference[k] for k in ("kind", "seq", "when", "file", "host") if self.reference.get(k)}
        if self.common_seq is not None:
            d["ultimo_punto_comune"] = self.common_seq
        if self.message:
            d["messaggio"] = self.message
        return d


def _when(ref: dict) -> str:
    w = ref.get("when") or ""
    return (w[:16].replace("T", " ") + " UTC") if w else "data sconosciuta"


def _source(ref: dict) -> str:
    if ref["kind"] == "backup":
        return f"il backup {ref['file']} creato il {_when(ref)}"
    return f"lo stato visto su questo PC il {_when(ref)}"


_ESCAPE = ("Nessuna modifica è consentita su questa copia. Vedi RUNBOOK.md, sezione 'Copie vecchie o divergenti'. "
           "Solo se sei sicuro che questa sia la copia da tenere: `orpheus accept-state --yes`.")


def _message(status: str, cur: Head, ref: dict | None, common: int | None) -> str:
    if status == "first_time":
        return "Prima volta che questo PC apre questo database: da ora lo terrà d'occhio."
    if status == "no_identity":
        return "Database senza identità (versione precedente): esegui `orpheus migrate`."
    if status == "older":
        return (f"ATTENZIONE: questa copia del database (posizione #{cur.seq}) è PIÙ VECCHIA di {_source(ref)} "
                f"(posizione #{ref['seq']}). Probabilmente stai usando una copia superata. " + _ESCAPE)
    if status == "behind_unknown":
        return (f"ATTENZIONE: questo PC ha già visto questo database in uno stato più avanzato "
                f"(posizione #{ref['seq']}, {_when(ref)}) di quello che stai aprendo (#{cur.seq}). "
                "Stai usando una copia più vecchia o divergente. " + _ESCAPE)
    if status == "diverged":
        point = f" dal punto #{common}" if common is not None else ""
        return (f"ATTENZIONE: questa copia (posizione #{cur.seq}) e {_source(ref)} (posizione #{ref['seq']}) "
                f"hanno storie DIVERSE{point}: sono state fatte modifiche su due copie separate. "
                "ORPHEUS non le unisce automaticamente. " + _ESCAPE)
    return ""


def check(conn: sqlite3.Connection, state: LocalState) -> Report:
    """Compare the database being opened with everything this PC remembers about it."""
    cur = head(conn)
    if cur.database_id is None:
        return Report("no_identity", cur, message=_message("no_identity", cur, None, None))

    refs: list[dict] = []
    seen = state.seen(cur.database_id)
    if seen:
        refs.append({"kind": "seen", "seq": seen["head_seq"], "hash": seen["head_hash"],
                     "when": seen.get("seen_at"), "host": seen.get("host"), "file": None})
    for b in state.backups(cur.database_id):
        refs.append({"kind": "backup", "seq": b["head_seq"], "hash": b["head_hash"], "when": b["created_at"],
                     "host": b.get("host"), "file": b["file"]})
    if not refs:
        return Report("first_time", cur, message=_message("first_time", cur, None, None))

    worst = Report("ok", cur)
    for ref in sorted(refs, key=lambda r: r["seq"], reverse=True):
        common = None
        if ref["seq"] <= cur.seq:
            status = "ok" if hash_at(conn, ref["seq"]) == ref["hash"] else "diverged"
            if status == "diverged" and ref["file"]:
                with _open_backup(state, ref) as other:
                    common = common_seq(conn, other)
        elif ref["file"]:
            with _open_backup(state, ref) as other:
                if contains(other, cur):
                    status = "older"
                else:
                    status, common = "diverged", common_seq(conn, other)
        else:
            status = "behind_unknown"
        if _SEVERITY[status] > _SEVERITY[worst.status]:
            worst = Report(status, cur, reference=ref, common_seq=common, message=_message(status, cur, ref, common))
    return worst


class _open_backup:
    def __init__(self, state: LocalState, ref: dict):
        self.path = Path(state.backup_dir) / ref["file"]

    def __enter__(self) -> sqlite3.Connection:
        self.conn = connect_readonly(self.path)
        return self.conn

    def __exit__(self, *exc):
        self.conn.close()
        return False


def head_dict(h: Head) -> dict:
    return asdict(h)
