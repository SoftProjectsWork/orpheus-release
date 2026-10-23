"""SQLite access and a minimal, checksum-guarded migration runner (ADR-0005).

The whole knowledge base is one file (default data/orpheus.db) that lives on the
user's PC, can be copied for backup, and can be moved between machines.

Each file in migrations/ is applied once, in lexical order, inside a single
transaction. Its SHA-256 is recorded; if an already-applied file is later
edited, `migrate` refuses to run (history changes only through new migrations).
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .config import ROOT

MIGRATIONS_DIR = ROOT / "migrations"

_BOOKKEEPING = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    filename   TEXT NOT NULL,
    sha256     TEXT NOT NULL,
    applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
) STRICT
"""


FK_OFF = "-- orpheus-migration: foreign_keys=off"


class MigrationIntegrityError(RuntimeError):
    """An applied migration file no longer matches its recorded checksum."""


@dataclass(frozen=True)
class Migration:
    version: str
    path: Path

    @property
    def sql(self) -> str:
        return self.path.read_text(encoding="utf-8")

    @property
    def sha256(self) -> str:
        # Line endings are normalised so a Windows (CRLF) checkout of an unchanged
        # file is not mistaken for an edited migration.
        return hashlib.sha256(self.path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def connect(path: str | Path) -> sqlite3.Connection:
    """Open the database with the settings every ORPHEUS connection must have.

    isolation_level=None: no implicit transactions; every write goes through
    `transaction()`, so nothing is ever left half-committed.
    """
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def connect_readonly(path: str | Path) -> sqlite3.Connection:
    """Open a backup or export copy without changing it in any way (not even its journal mode)."""
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection):
    """BEGIN IMMEDIATE … COMMIT, rolling back on any exception."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def discover(migrations_dir: Path = MIGRATIONS_DIR) -> list[Migration]:
    found = []
    for path in sorted(migrations_dir.glob("*.sql")):
        version = path.name.split("_", 1)[0]
        if not version.isdigit():
            raise ValueError(f"migration file must start with a number: {path.name}")
        found.append(Migration(version, path))
    versions = [m.version for m in found]
    if len(versions) != len(set(versions)):
        raise ValueError("duplicate migration version numbers")
    return found


def status(conn: sqlite3.Connection, migrations_dir: Path = MIGRATIONS_DIR) -> list[dict]:
    conn.execute(_BOOKKEEPING)
    applied = {r["version"]: r["sha256"] for r in conn.execute("SELECT version, sha256 FROM schema_migrations")}
    out = []
    for m in discover(migrations_dir):
        rec = applied.get(m.version)
        state = "pending" if rec is None else ("applied" if rec == m.sha256 else "MODIFIED")
        out.append({"version": m.version, "file": m.path.name, "state": state})
    return out


def applied_versions(conn: sqlite3.Connection) -> list[str]:
    """Migration versions recorded in the database (read-only; [] for an empty database)."""
    try:
        return sorted(r[0] for r in conn.execute("SELECT version FROM schema_migrations"))
    except sqlite3.OperationalError:
        return []


def unknown_applied(conn: sqlite3.Connection, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    """Versions applied to the database that this code does not know: the database was
    upgraded by a newer ORPHEUS, so this code must not touch it (`git pull` first)."""
    known = {m.version for m in discover(migrations_dir)}
    return [v for v in applied_versions(conn) if v not in known]


def migrate(conn: sqlite3.Connection, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply pending migrations; return the versions applied in this call."""
    rows = status(conn, migrations_dir)
    for row in rows:
        if row["state"] == "MODIFIED":
            raise MigrationIntegrityError(
                f"migration {row['file']} was edited after being applied; add a new migration instead")
    applied_now: list[str] = []
    for m in discover(migrations_dir):
        if conn.execute("SELECT 1 FROM schema_migrations WHERE version = ?", (m.version,)).fetchone():
            continue
        # executescript runs the DDL inside our explicit transaction (no implicit COMMIT
        # because no transaction is pending when it starts; the script itself opens one).
        # A migration that rebuilds a table (SQLite cannot change a CHECK in place) declares
        # FK_OFF on its first line: foreign keys are switched off outside the transaction,
        # checked with PRAGMA foreign_key_check before COMMIT, then switched on again.
        fk_off = m.sql.lstrip().startswith(FK_OFF)
        if fk_off:
            conn.execute("PRAGMA foreign_keys = OFF")
        try:
            conn.executescript("BEGIN IMMEDIATE;\n" + m.sql)
            if fk_off and conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise MigrationIntegrityError(f"migration {m.path.name} would break foreign keys; nothing applied")
            conn.execute("INSERT INTO schema_migrations (version, filename, sha256) VALUES (?, ?, ?)",
                         (m.version, m.path.name, m.sha256))
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            if fk_off:
                conn.execute("PRAGMA foreign_keys = ON")
        applied_now.append(m.version)
    return applied_now


def integrity_check(conn: sqlite3.Connection) -> dict:
    """SQLite page-level check + foreign-key check."""
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    fk_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    return {"integrity": integrity, "foreign_key_violations": len(fk_violations)}


def backup(conn: sqlite3.Connection, dest: str | Path) -> Path:
    """Consistent online copy of the database (SQLite backup API) as one self-contained file.

    The copy is written under a temporary name and renamed at the end, so a half-written
    file is never mistaken for a backup. Its journal mode is set to DELETE: no -wal/-shm
    side files, so the single .db file is the whole copy.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".partial")
    if partial.exists():
        partial.unlink()
    target = sqlite3.connect(str(partial))
    try:
        conn.backup(target)
        target.execute("PRAGMA journal_mode = DELETE")
    finally:
        target.close()
    os.replace(partial, dest)
    return dest
