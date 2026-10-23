"""This PC's own memory (ORPHEUS_LOCAL_DIR) — ADR-0007.

Two things live here, on the PC's internal disk, and never travel with the data:
  state.json         for each database this PC has opened: the most advanced state seen
                     (audit chain head), when, and at which path;
  backups/           this PC's copies of the database, with backups/index.json describing them.

Because this folder stays with the PC while the data folder moves (external disk,
export bundles), comparing the two is how ORPHEUS notices that it is being handed an
older or divergent copy of a database it already knows.
"""

from __future__ import annotations

import json
import os
import platform
import tempfile
from datetime import datetime, timezone
from pathlib import Path

STATE_FILE = "state.json"
BACKUP_DIR = "backups"
BACKUP_INDEX = "index.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_utc(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


def _atomic_write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


class LocalState:
    def __init__(self, root: Path):
        self.root = Path(root)

    @property
    def state_path(self) -> Path:
        return self.root / STATE_FILE

    @property
    def backup_dir(self) -> Path:
        return self.root / BACKUP_DIR

    @property
    def index_path(self) -> Path:
        return self.backup_dir / BACKUP_INDEX

    # ---------- seen-state register ----------

    def databases(self) -> dict[str, dict]:
        return _read_json(self.state_path, {"format": 1, "databases": {}})["databases"]

    def seen(self, database_id: str) -> dict | None:
        return self.databases().get(database_id)

    def record_seen(self, head, db_path: Path, *, accept: bool = False) -> None:
        """Remember `head` as the latest state of its database on this PC.

        accept=True also sets a baseline: references created before it (older backups that
        are ahead of or divergent from `head`) stop counting. Used when the user deliberately
        adopts the current state.
        """
        data = _read_json(self.state_path, {"format": 1, "databases": {}})
        entry = data["databases"].get(head.database_id, {})
        now = utc_now()
        entry.update({"head_seq": head.seq, "head_hash": head.hash, "db_path": str(db_path),
                      "seen_at": now, "host": platform.node()})
        if accept:
            entry["baseline_at"] = now
        data["databases"][head.database_id] = entry
        _atomic_write_json(self.state_path, data)

    def baseline(self, database_id: str) -> str | None:
        entry = self.seen(database_id)
        return entry.get("baseline_at") if entry else None

    # ---------- local backups ----------

    def _index(self) -> list[dict]:
        return _read_json(self.index_path, [])

    def index(self) -> list[dict]:
        """All backup entries recorded on this PC (including files that no longer exist)."""
        return self._index()

    def add_backup(self, entry: dict) -> None:
        entries = self._index()
        entries.append(entry)
        _atomic_write_json(self.index_path, entries)

    def backups(self, database_id: str, *, include_superseded: bool = False) -> list[dict]:
        """Backups of this database whose file still exists, oldest first.

        Backups taken before an accepted baseline are 'superseded' and excluded unless asked.
        """
        base = None if include_superseded else self.baseline(database_id)
        out = []
        for e in self._index():
            if e.get("database_id") != database_id:
                continue
            if not (self.backup_dir / e["file"]).exists():
                continue
            if base and e["created_at"] < base:
                continue
            out.append(e)
        return sorted(out, key=lambda e: e["created_at"])

    def newest_backup(self, database_id: str) -> dict | None:
        found = self.backups(database_id, include_superseded=True)
        return found[-1] if found else None

    def prune(self, database_id: str, keep: int) -> list[str]:
        """Keep the `keep` most recent backups of this database; delete the older files."""
        entries = self._index()
        mine = sorted((e for e in entries if e.get("database_id") == database_id), key=lambda e: e["created_at"])
        doomed = mine[:-keep] if len(mine) > keep else []
        removed = []
        for e in doomed:
            path = self.backup_dir / e["file"]
            try:
                path.unlink(missing_ok=True)
                removed.append(e["file"])
            except OSError:
                continue  # in use or protected: keep it listed, try again next time
        names = set(removed)
        kept = [e for e in entries if not (e.get("database_id") == database_id and e["file"] in names)]
        kept = [e for e in kept if (self.backup_dir / e["file"]).exists()]
        _atomic_write_json(self.index_path, kept)
        return removed
