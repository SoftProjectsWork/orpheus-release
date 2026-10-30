"""Backups of the database on this PC's internal disk (ORPHEUS_LOCAL_DIR/backups) — ADR-0007.

A backup only READS the database: it never writes an event into it, so taking one cannot
make two copies drift apart. Each backup is a single self-contained .db file plus an entry
in backups/index.json (database id, audit head, time, integrity, sha256).
"""

from __future__ import annotations

import hashlib
import platform
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import db, lineage
from .config import LocalSettings
from .localstate import LocalState, parse_utc, utc_now


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def make_backup(conn: sqlite3.Connection, state: LocalState, *, reason: str) -> dict:
    h = lineage.head(conn)
    if h.database_id is None:
        raise ValueError("database without identity: run `orpheus migrate` first")
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%S") + f"{now.microsecond // 1000:03d}Z"
    name = f"orpheus-{h.database_id[:8]}-{h.seq:07d}-{stamp}.db"
    dest = db.backup(conn, state.backup_dir / name)
    copy = db.connect_readonly(dest)
    try:
        check = db.integrity_check(copy)
        copied_head = lineage.head(copy)
    finally:
        copy.close()
    entry = {"file": name, "database_id": h.database_id, "head_seq": copied_head.seq,
             "head_hash": copied_head.hash, "created_at": utc_now(), "reason": reason,
             "host": platform.node(), "size": dest.stat().st_size, "sha256": _sha256(dest), "integrity": check}
    state.add_backup(entry)
    return entry


def maybe_auto_backup(conn: sqlite3.Connection, state: LocalState, settings: LocalSettings, *,
                      head_before: lineage.Head) -> dict | None:
    """After a command that changed the database: back up if the last backup is old enough."""
    if settings.auto_backup_minutes == 0:
        return None
    h = lineage.head(conn)
    if h == head_before or h.database_id is None:
        return None
    newest = state.newest_backup(h.database_id)
    if newest and datetime.now(timezone.utc) - parse_utc(newest["created_at"]) < timedelta(minutes=settings.auto_backup_minutes):
        return None
    entry = make_backup(conn, state, reason="auto")
    state.prune(h.database_id, settings.keep_local_backups)
    return entry


# ---------------------------------------------------------------- restore test (Fase 1 gate)

MAIN_TABLES = ("document", "document_page", "article", "author", "article_author", "citation",
               "doi_check", "pubmed_check", "fulltext_check", "article_card", "card_value", "audit_event")


def _counts(conn: sqlite3.Connection) -> dict[str, int | None]:
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
    return {t: (conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] if t in names else None) for t in MAIN_TABLES}


def restore_test(conn: sqlite3.Connection, state: LocalState, *, file: str | None = None,
                 work_dir: Path | None = None) -> dict:
    """Restore a local backup into a temporary folder and check it — the database is only READ.

    Takes the newest backup of this database recorded in backups/index.json (or `file`), copies it
    away from the backup folder, then checks: sha256 against the index, that it opens, integrity
    and foreign keys, the audit chain, the migrations, its identity and position compared with the
    database in use, the counts of the main tables. The temporary copy is deleted at the end.
    """
    import shutil
    import tempfile

    from . import audit

    current = lineage.head(conn)
    if current.database_id is None:
        raise ValueError("database without identity: run `orpheus migrate` first")
    found = state.backups(current.database_id, include_superseded=True)
    if file:
        found = [e for e in found if e["file"] == file]
        if not found:
            raise ValueError(f"backup {file} non trovato tra i backup locali di questo database")
    if not found:
        raise ValueError("nessun backup locale di questo database: esegui prima `orpheus backup`")
    entry = found[-1]
    src = state.backup_dir / entry["file"]
    checks: dict[str, dict] = {}

    def check(name: str, ok: bool, **detail) -> None:
        checks[name] = {"ok": bool(ok), **detail}

    tmp = Path(tempfile.mkdtemp(prefix="orpheus-prova-ripristino-", dir=work_dir))
    try:
        dest = shutil.copy2(src, tmp / "orpheus.db")
        sha = _sha256(Path(dest))
        check("sha256", sha == entry.get("sha256"), atteso=entry.get("sha256"), trovato=sha)
        copy = db.connect(dest)  # opened like a restored database (it is a throw-away copy)
        try:
            integ = db.integrity_check(copy)
            check("integrita", integ["integrity"] == "ok" and integ["foreign_key_violations"] == 0, **integ)
            chain = audit.verify_chain(copy)
            check("catena_audit", chain.ok, eventi=chain.events_checked, primo_errore=chain.first_bad_seq,
                  motivo=chain.reason)
            states = db.status(copy)
            modified = [m["version"] for m in states if m["state"] == "MODIFIED"]
            pending = [m["version"] for m in states if m["state"] == "pending"]
            unknown = db.unknown_applied(copy)
            check("migrazioni", not modified and not unknown, applicate=len(states) - len(pending),
                  da_applicare=pending, modificate=modified, sconosciute=unknown)
            h = lineage.head(copy)
            check("identita", h.database_id == current.database_id, database_id=h.database_id)
            check("posizione_come_indice", h.seq == entry.get("head_seq") and h.hash == entry.get("head_hash"),
                  posizione=h.seq, nell_indice=entry.get("head_seq"))
            rel = lineage.relation(conn, copy)
            check("rispetto_al_database_attuale", rel in ("identical", "other_behind"), relazione=rel,
                  posizione_backup=h.seq, posizione_attuale=current.seq)
            counts_copy, counts_now = _counts(copy), _counts(conn)
        finally:
            copy.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    ok = all(c["ok"] for c in checks.values())
    return {"esito": "riuscita" if ok else "fallita", "backup": entry["file"], "creato_il": entry.get("created_at"),
            "dimensione": entry.get("size"), "controlli": checks,
            "conteggi": {t: {"backup": counts_copy[t], "attuale": counts_now[t]} for t in MAIN_TABLES},
            "copia_temporanea_rimossa": not tmp.exists()}
