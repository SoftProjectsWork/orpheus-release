"""T-054 passo 1 — safe writing of riviste_prioritarie.csv (rules journal-list-write-v1).

Every change to the user's journal list (automatic journals, sigla renames, URL cleaning, the "Nuova rivista" window of
the dashboard) goes through this module:
  - the file is read as Excel saved it and written back the same way: same separator, same BOM, same encoding, same
    line endings, same columns in the same order (unknown columns kept), same rows in the same order;
  - before writing, a copy of the old file goes to data/archive/riviste_prioritarie_<date time>.csv;
  - if the file is open in Excel (locked on Windows) nothing is written and the message says to close it;
  - checks: sigla with layout.SIGLA_RE and unique (case-insensitive); ISSN with a valid check digit, not already in
    another row and not in journal_issn of another journal;
  - rows are never deleted; one audit event for every row added or changed.
"""

from __future__ import annotations

import csv
import io
import os
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import audit, layout
from .identifiers import is_valid_issn, normalize_issn

RULES_VERSION = "journal-list-write-v1"
ARCHIVE_PREFIX = "riviste_prioritarie_"
ISSN_COLUMNS = ("issn_print", "issn_online")


class ListError(Exception):
    """The list cannot be changed (problems with the values or the file)."""

    def __init__(self, message: str, problems: list[str] | None = None):
        super().__init__(message)
        self.message, self.problems = message, problems or []


class ListLocked(ListError):
    """The file is open in another program (Excel): nothing written."""


@dataclass
class JournalList:
    path: Path
    header: list[str]                          # the header cells as in the file
    keys: list[str]                            # the same, stripped and lower case (the row keys)
    rows: list[dict]                           # every data row (also empty or wrong ones), values as in the file
    delimiter: str = ";"
    encoding: str = "utf-8-sig"
    newline: str = "\r\n"
    changes: list[dict] = field(default_factory=list)

    # ------------------------------------------------------------ reading

    @classmethod
    def load(cls, path: Path) -> "JournalList":
        path = Path(path)
        if not path.exists():
            return cls(path, list(layout.CSV_COLUMNS), list(layout.CSV_COLUMNS), [])
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise ListLocked(_locked_message(path, exc)) from exc
        if raw.startswith(b"\xef\xbb\xbf"):
            encoding, text = "utf-8-sig", raw.decode("utf-8-sig")
        else:
            try:
                encoding, text = "utf-8", raw.decode("utf-8")
            except UnicodeDecodeError:
                encoding, text = "cp1252", raw.decode("cp1252", errors="replace")
        newline = "\r\n" if "\r\n" in text else "\n"
        lines = text.splitlines()
        if not lines or not lines[0].strip():
            return cls(path, list(layout.CSV_COLUMNS), list(layout.CSV_COLUMNS), [], encoding=encoding, newline=newline)
        first = lines[0]
        delimiter = ";" if first.count(";") >= max(first.count(","), 1) else ("\t" if "\t" in first else ",")
        reader = csv.reader(io.StringIO(text), delimiter=delimiter)
        header = next(reader)
        keys = [(h or "").strip().lower() for h in header]
        rows = []
        for cells in reader:
            if not cells:
                rows.append({"_blank": True})
                continue
            row = {k: (cells[i] if i < len(cells) else "") for i, k in enumerate(keys) if k}
            if len(cells) > len(keys):
                row["_extra"] = cells[len(keys):]
            rows.append(row)
        for col in layout.CSV_COLUMNS:                 # a column missing from an old file is added at the end
            if col not in keys:
                header.append(col)
                keys.append(col)
        return cls(path, header, keys, rows, delimiter, encoding, newline)

    def data_rows(self) -> list[dict]:
        """The rows with a sigla (what read_journal_list uses)."""
        return [_plain(r) for r in self.rows if (r.get("sigla") or "").strip()]

    def find(self, sigla: str) -> dict | None:
        want = (sigla or "").strip().lower()
        return next((r for r in self.rows if (r.get("sigla") or "").strip().lower() == want and want), None)

    # ------------------------------------------------------------ checks

    def check(self, values: dict, *, conn: sqlite3.Connection | None = None, editing: dict | None = None) -> dict[str, str]:
        """Problems of a row to add (editing=None) or of the new values of `editing`: {column: message}."""
        out: dict[str, str] = {}
        sigla = (values.get("sigla") or "").strip()
        if not sigla:
            out["sigla"] = "la sigla è obbligatoria"
        elif not layout.SIGLA_RE.match(sigla):
            out["sigla"] = "solo lettere, numeri, - e _, senza spazi (al massimo 40 caratteri)"
        else:
            other = self.find(sigla)
            if other is not None and other is not editing:
                out["sigla"] = f"la sigla {other['sigla'].strip()} c'è già nell'elenco"
        if not (values.get("titolo") or "").strip():
            out["titolo"] = "il titolo è obbligatorio"
        mine: set[str] = set()
        jid_self = None
        if conn is not None and editing is not None:
            from .policy_versions import journal_by_sigla
            jid_self = journal_by_sigla(conn, [_plain(editing)]).get((editing.get("sigla") or "").strip().lower())
        known = {}
        if conn is not None:
            known = {r[0]: r[1] for r in conn.execute("SELECT issn, journal_id FROM journal_issn")}
        for col in ISSN_COLUMNS:
            value = (values.get(col) or "").strip()
            if not value:
                continue
            if not is_valid_issn(value):
                out[col] = f"ISSN {value} non valido (formato 1234-567X, controlla la cifra finale)"
                continue
            issn = normalize_issn(value)
            if issn in mine:
                continue                               # same ISSN for print and online (MDPI, BMC): allowed
            mine.add(issn)
            for r in self.rows:
                if r is editing or not (r.get("sigla") or "").strip():
                    continue
                if issn in {normalize_issn(r.get(c) or "") for c in ISSN_COLUMNS if is_valid_issn(r.get(c) or "")}:
                    out[col] = f"ISSN {issn} è già della riga {r['sigla'].strip()}"
                    break
            if col not in out and issn in known and known[issn] != jid_self:
                out[col] = f"ISSN {issn} è già di un'altra rivista nel database"
        if not mine and "issn_print" not in out:
            out["issn_print"] = "serve almeno un ISSN (stampa o online): collega la rivista agli articoli"
        return out

    # ------------------------------------------------------------ changes (in memory)

    def add(self, values: dict, *, conn: sqlite3.Connection | None = None, why: str = "") -> dict:
        problems = self.check(values, conn=conn)
        if problems:
            raise ListError("riga non aggiunta: " + "; ".join(f"{k}: {v}" for k, v in problems.items()),
                            [f"{k}: {v}" for k, v in problems.items()])
        row = {k: "" for k in self.keys if k}
        for k, v in values.items():
            if k in row or k in layout.CSV_COLUMNS:
                row[k] = "" if v is None else str(v).strip()
        self.rows.append(row)
        self.changes.append({"azione": "aggiunta", "sigla": row["sigla"], "dopo": _plain(row), "motivo": why})
        return row

    def update(self, sigla: str, values: dict, *, conn: sqlite3.Connection | None = None, why: str = "",
               check: bool = True) -> dict:
        row = self.find(sigla)
        if row is None:
            raise ListError(f"la sigla {sigla} non è nell'elenco")
        new = {**_plain(row), **{k: ("" if v is None else str(v).strip()) for k, v in values.items()
                                 if k in layout.CSV_COLUMNS or k in row}}
        if check:
            problems = self.check(new, conn=conn, editing=row)
            if problems:
                raise ListError("riga non cambiata: " + "; ".join(f"{k}: {v}" for k, v in problems.items()),
                                [f"{k}: {v}" for k, v in problems.items()])
        before = _plain(row)
        changed = {k: {"prima": before.get(k, ""), "dopo": v} for k, v in new.items() if before.get(k, "") != v}
        if changed:
            row.update({k: v for k, v in new.items()})
            self.changes.append({"azione": "cambiata", "sigla": before.get("sigla"), "sigla_nuova": row.get("sigla"),
                                 "campi": changed, "motivo": why})
        return row

    # ------------------------------------------------------------ writing

    def render(self) -> str:
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=self.delimiter, lineterminator=self.newline, quoting=csv.QUOTE_MINIMAL)
        w.writerow(self.header)
        for r in self.rows:
            if r.get("_blank"):
                w.writerow([])
                continue
            w.writerow([r.get(k, "") if k else "" for k in self.keys] + list(r.get("_extra") or []))
        return buf.getvalue()

    def save(self, *, home: Path, archive_dir: str = "data/archive", conn: sqlite3.Connection | None = None,
             actor: str = "orpheus", now: datetime | None = None) -> dict:
        """Write the file if something changed: archive copy first, then the new content (atomic replace).
        Returns {"scritto": bool, "copia": path or None, "modifiche": [...]}"""
        if not self.changes:
            return {"scritto": False, "copia": None, "modifiche": []}
        text = self.render()
        encoding = self.encoding
        try:
            data = text.encode(encoding)
        except UnicodeEncodeError:                     # an old cp1252 file and a title with other characters
            encoding, data = "utf-8-sig", text.encode("utf-8-sig")
        if encoding == "utf-8-sig" and not data.startswith(b"\xef\xbb\xbf"):
            data = b"\xef\xbb\xbf" + data
        _check_not_locked(self.path)
        copy = None
        if self.path.exists():
            stamp = (now or datetime.now()).strftime("%Y-%m-%d_%H%M%S")
            folder = Path(home) / archive_dir
            folder.mkdir(parents=True, exist_ok=True)
            copy = folder / f"{ARCHIVE_PREFIX}{stamp}.csv"
            n = 2
            while copy.exists():
                copy = folder / f"{ARCHIVE_PREFIX}{stamp}_{n}.csv"
                n += 1
            shutil.copy2(self.path, copy)
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            tmp.write_bytes(data)
            os.replace(tmp, self.path)
        except OSError as exc:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise ListLocked(_locked_message(self.path, exc)) from exc
        if conn is not None:
            for ch in self.changes:
                audit.record(conn, actor=actor, action="journal_list_" + ("add" if ch["azione"] == "aggiunta" else "change"),
                             entity_type="journal_list", entity_id=ch.get("sigla_nuova") or ch.get("sigla"),
                             payload={"regole": RULES_VERSION, **ch,
                                      "copia": layout.shown_path(home, copy) if copy else None})
        done = list(self.changes)
        self.changes.clear()
        return {"scritto": True, "copia": layout.shown_path(home, copy) if copy else None, "modifiche": done}


def _plain(row: dict) -> dict:
    return {k: v for k, v in row.items() if not k.startswith("_")}


def _locked_message(path: Path, exc: BaseException) -> str:
    return (f"{path.name} non si può scrivere ({exc.__class__.__name__}): se è aperto in Excel, chiudilo e riprova. "
            "Non è stato cambiato niente.")


def _check_not_locked(path: Path) -> None:
    """On Windows Excel keeps the file locked: opening it for writing fails. Nothing is changed by this check."""
    if not path.exists():
        return
    try:
        with open(path, "r+b"):
            pass
    except OSError as exc:
        raise ListLocked(_locked_message(path, exc)) from exc
