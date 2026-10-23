"""T-028 — Fase 2: official metrics of the journals in `journal_metric` (table of migration 0001), rules metrics-v1.
User's decision of 2026-10-15: "SCImago + OpenAlex".

SCImago Journal & Country Rank (Scopus data): the CSV the user downloads from https://www.scimagojr.com/journalrank.php
(button "Download") into inbox/metriche/, e.g. "scimagojr 2025.csv". Format seen on 2026-10-15: ';'-separated, titles
in quotes, decimal comma ("104,065"), ISSNs without hyphen in one cell ("15424863, 00079235"); columns Rank, Sourceid,
Title, Type, Issn, Publisher, …, SJR, SJR Best Quartile, H index, Total Docs. (2025), …, Citations / Doc. (2years), …,
Categories ("Orthopedics and Sports Medicine (Q1); Sports Science (Q1)"). Columns are found by name, not position.
The year of the metrics is the one in "Total Docs. (YYYY)", otherwise the 4 digits of the file name.
  A journal of the table matches the row whose ISSNs include one of its ISSNs; more than one row → ambiguous, nothing
  written. Metrics: SJR, best quartile (subject_category ''), quartile of every subject category, h-index,
  "Citations / Doc. (2years)" as two_year_mean_citedness. Rows of a year are never rewritten: a different value in
  another file of the same year is reported ("diverso, non riscritto").
OpenAlex (CC0): GET /sources/issn:<ISSN> for the ISSNs of the journal, one at a time, until one answers 200 (404 → next
ISSN). Single-record lookups are free (docs read 2026-10-15: no key needed for this, free key optional). The answer is
accepted only if it is a journal whose ISSNs include one of ours. Metrics: summary_stats.2yr_mean_citedness
(two_year_mean_citedness) and summary_stats.h_index, reference_year = the year of the request; OpenAlex recomputes them
continuously, so a different value in the same year updates the row (old and new value in the audit event).
journal.openalex_id is written when empty (never changed; a different id is reported and nothing of OpenAlex written).
Every row: provider, is_official = 1 (published by the owner of the indicator), definition_url, retrieved_at and the
source_snapshot of the file or answer (kept in the archive). `plan` writes nothing; `write` = one transaction, one audit
event per journal changed.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import audit, crossref
from .db import transaction
from .identifiers import is_valid_issn, normalize_issn
from .ids import new_id
from .ingest_pdf import portable

RULES_VERSION = "metrics-v1"
METRICS_DIR = "metriche"                       # inside inbox/
SCIMAGO_SOURCE, OPENALEX_SOURCE = "scimago", "openalex"
SCIMAGO_PROVIDER, OPENALEX_PROVIDER = "SCImago", "OpenAlex"
SCIMAGO_URL = "https://www.scimagojr.com/journalrank.php?out=xls"
SCIMAGO_HELP = "https://www.scimagojr.com/help.php"
SCIMAGO_LICENCE = ("SCImago Journal & Country Rank (dati Scopus): file pubblico scaricato dall'utente dal sito "
                   "www.scimagojr.com; si cita la fonte. Copia tenuta solo sul PC.")
OPENALEX_API = "https://api.openalex.org"
OPENALEX_DEF = "https://docs.openalex.org/api-entities/sources/source-object#summary_stats"
OPENALEX_LICENCE = "OpenAlex: metadata CC0 (free to use)."
_QUARTILE = re.compile(r"^(.*?)\s*\((Q[1-4])\)\s*$")
_YEAR_COL = re.compile(r"total docs\.?\s*\((\d{4})\)", re.I)


class MetricsFileError(ValueError):
    """The SCImago file cannot be read."""


# ---------------------------------------------------------------- SCImago file

def _key(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").replace("﻿", "")).strip().casefold()


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def _number(text: str | None) -> float | None:
    """'104,065' -> 104.065; '1.234,5' -> 1234.5; '' -> None (SCImago uses the decimal comma)."""
    t = (text or "").strip().replace(" ", "").replace(" ", "")
    if not t or t in ("-", "—"):
        return None
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def _issns(cell: str | None) -> set[str]:
    out = set()
    for part in re.split(r"[,;\s]+", cell or ""):
        p = part.strip().upper()
        if re.fullmatch(r"\d{7}[\dX]", p):
            out.add(f"{p[:4]}-{p[4:]}")
        elif re.fullmatch(r"\d{4}-\d{3}[\dX]", p):
            out.add(p)
    return out


def _categories(cell: str | None) -> list[tuple[str, str]]:
    out = []
    for part in (cell or "").split(";"):
        m = _QUARTILE.match(part.strip())
        if m and m.group(1):
            out.append((m.group(1).strip(), m.group(2)))
    return out


@dataclass
class ScimagoFile:
    path: Path
    year: int
    sha256: str
    rows: list[dict]
    columns: list[str]


def read_scimago(path: Path) -> ScimagoFile:
    raw = Path(path).read_bytes()
    text = _decode(raw)
    sample = text[:4096]
    delim = ";" if sample.count(";") >= sample.count(",") else ","
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delim)
    try:
        header = next(reader)
    except StopIteration as exc:
        raise MetricsFileError(f"{Path(path).name}: file vuoto") from exc
    cols = {_key(h): i for i, h in enumerate(header)}
    need = {"title", "issn", "sjr"}
    if not need <= cols.keys():
        raise MetricsFileError(f"{Path(path).name}: non è il file di SCImago (mancano le colonne "
                               f"{', '.join(sorted(need - cols.keys()))})")
    year = None
    for h in header:
        m = _YEAR_COL.search(h or "")
        if m:
            year = int(m.group(1))
            break
    if year is None:
        m = re.search(r"(19|20)\d{2}", Path(path).stem)
        year = int(m.group(0)) if m else None
    if year is None:
        raise MetricsFileError(f"{Path(path).name}: anno delle metriche non trovato (né nelle colonne né nel nome)")

    def get(row, *names):
        for n in names:
            i = cols.get(n)
            if i is not None and i < len(row):
                return row[i]
        return None

    rows = []
    for row in reader:
        if not row or not any(c.strip() for c in row):
            continue
        rows.append({"sourceid": (get(row, "sourceid") or "").strip(), "title": (get(row, "title") or "").strip(),
                     "type": (get(row, "type") or "").strip(), "issn": _issns(get(row, "issn")),
                     "sjr": _number(get(row, "sjr")), "quartile": (get(row, "sjr best quartile") or "").strip(),
                     "h_index": _number(get(row, "h index")),
                     "cites_2y": _number(get(row, "citations / doc. (2years)", "cites / doc. (2years)")),
                     "categories": _categories(get(row, "categories"))})
    return ScimagoFile(Path(path), year, hashlib.sha256(raw).hexdigest(), rows, list(header))


def scimago_files(home: Path, inbox_dir: str = "inbox") -> list[Path]:
    folder = Path(home) / inbox_dir / METRICS_DIR
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".csv"
                  and p.name.lower().startswith("scimagojr"))


def scimago_metrics(rec: dict) -> list[dict]:
    out = []
    if rec["sjr"] is not None:
        out.append({"metric": "SJR", "value": rec["sjr"]})
    if rec["quartile"] in ("Q1", "Q2", "Q3", "Q4"):
        out.append({"metric": "quartile", "value_text": rec["quartile"]})
    for cat, q in rec["categories"]:
        out.append({"metric": "quartile", "value_text": q, "subject_category": cat})
    if rec["h_index"] is not None:
        out.append({"metric": "h_index", "value": rec["h_index"]})
    if rec["cites_2y"] is not None:
        out.append({"metric": "two_year_mean_citedness", "value": rec["cites_2y"]})
    return out


# ---------------------------------------------------------------- OpenAlex

def source_url(issn: str) -> str:
    return f"{OPENALEX_API}/sources/issn:{issn}"


@dataclass
class OpenAlexSources:
    """GET /sources/issn:<ISSN> with the shared client of crossref.py (retries, 1 request/s, TLS, 403 = stop)."""
    contact_email: str
    api_key: str | None = None
    max_requests_per_second: float = 1.0
    max_retries: int = 3
    transport: callable = None
    sleep: callable = None
    log: list[dict] = field(default_factory=list)

    def __post_init__(self):
        kw = {"max_requests_per_second": self.max_requests_per_second, "max_retries": self.max_retries,
              "transport": self.transport}
        if self.sleep is not None:
            kw["sleep"] = self.sleep
        self._http = crossref.CrossrefClient(self.contact_email, **kw)
        self._http.log = self.log

    @property
    def requests_made(self) -> int:
        return self._http.requests_made

    def by_issn(self, issn: str) -> crossref.Response:
        from urllib.parse import urlencode
        public = source_url(issn)
        extra = [("mailto", self._http.contact_email)] + ([("api_key", self.api_key)] if self.api_key else [])
        try:
            return self._http._request(public, f"{public}?{urlencode(extra)}", f"ISSN {issn}", (200, 404))
        except crossref.CrossrefError as exc:
            msg = str(exc).replace("api.crossref.org", "api.openalex.org").replace("Crossref", "OpenAlex")
            raise type(exc)(msg) from exc


def parse_source(body: bytes) -> dict:
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"risposta di OpenAlex non leggibile: {exc}") from exc
    if not isinstance(data, dict) or not str(data.get("id") or "").startswith("https://openalex.org/S"):
        raise ValueError("la risposta non è una fonte (source) di OpenAlex")
    stats = data.get("summary_stats") or {}
    return {"id": data["id"], "display_name": data.get("display_name"), "type": data.get("type"),
            "issn": {normalize_issn(i) for i in data.get("issn") or [] if isinstance(i, str) and is_valid_issn(i)},
            "issn_l": data.get("issn_l"), "two_year_mean_citedness": stats.get("2yr_mean_citedness"),
            "h_index": stats.get("h_index"), "works_count": data.get("works_count"),
            "cited_by_count": data.get("cited_by_count")}


def openalex_metrics(src: dict) -> list[dict]:
    out = []
    v = src.get("two_year_mean_citedness")
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        out.append({"metric": "two_year_mean_citedness", "value": float(v)})
    v = src.get("h_index")
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        out.append({"metric": "h_index", "value": float(v)})
    return out


# ---------------------------------------------------------------- plan / write

def journals(conn: sqlite3.Connection) -> list[dict]:
    out = []
    for j in conn.execute("SELECT id, title, openalex_id FROM journal ORDER BY title"):
        issns = conn.execute("""SELECT issn, issn_type FROM journal_issn WHERE journal_id = ?
                                ORDER BY CASE issn_type WHEN 'print' THEN 0 WHEN 'electronic' THEN 1
                                WHEN 'linking' THEN 2 ELSE 3 END, issn""", (j["id"],)).fetchall()
        out.append({"id": j["id"], "titolo": j["title"], "openalex_id": j["openalex_id"],
                    "issn": [r["issn"] for r in issns]})
    return out


def _existing(conn, jid, m, year, provider):
    return conn.execute("""SELECT id, value, value_text FROM journal_metric WHERE journal_id = ? AND metric = ?
                           AND reference_year = ? AND provider = ? AND subject_category = ?""",
                        (jid, m["metric"], year, provider, m.get("subject_category", ""))).fetchone()


def _same(row, m) -> bool:
    if m.get("value") is not None:
        return row["value"] is not None and abs(row["value"] - m["value"]) < 1e-9
    return row["value_text"] == m.get("value_text")


def _shown(m) -> str:
    v = m.get("value_text") if m.get("value") is None else f"{m['value']:g}"
    cat = f" [{m['subject_category']}]" if m.get("subject_category") else ""
    return f"{m['metric']}{cat} = {v}"


def plan_scimago(conn: sqlite3.Connection, files: list[ScimagoFile], js: list[dict]) -> list[dict]:
    """For every file and journal: the matching SCImago row and what would be written."""
    out = []
    for f in files:
        by_issn: dict[str, list[dict]] = {}
        for rec in f.rows:
            for i in rec["issn"]:
                by_issn.setdefault(i, []).append(rec)
        for j in js:
            recs = {id(r): r for i in j["issn"] for r in by_issn.get(i, [])}
            item = {"journal_id": j["id"], "titolo": j["titolo"], "fonte": SCIMAGO_PROVIDER, "anno": f.year,
                    "file": f.path.name, "metriche": [], "nuove": [], "uguali": 0, "diverse": [], "problemi": []}
            if not recs:
                item["esito"] = "non trovata"
                item["problemi"].append(f"nessuna riga di {f.path.name} con gli ISSN {', '.join(j['issn'])}")
            elif len(recs) > 1:
                item["esito"] = "ambigua"
                item["problemi"].append("più righe con gli stessi ISSN: " +
                                        "; ".join(f"{r['title']} ({r['sourceid']})" for r in recs.values()))
            else:
                rec = next(iter(recs.values()))
                item |= {"esito": "trovata", "sourceid": rec["sourceid"], "titolo_scimago": rec["title"],
                         "metriche": scimago_metrics(rec)}
                _classify(conn, item, j["id"], f.year, SCIMAGO_PROVIDER, update=False)
            out.append(item)
    return out


def _classify(conn, item, jid, year, provider, *, update: bool) -> None:
    for m in item["metriche"]:
        row = _existing(conn, jid, m, year, provider)
        if row is None:
            item["nuove"].append(m)
        elif _same(row, m):
            item["uguali"] += 1
        else:
            old = row["value_text"] if row["value"] is None else f"{row['value']:g}"
            item["diverse"].append({**m, "row_id": row["id"], "prima": old, "aggiorna": update})


def plan_openalex(conn: sqlite3.Connection, client: OpenAlexSources | None, js: list[dict], *, year: int) -> list[dict]:
    """With a client: ask OpenAlex (network) and classify; without: only the ISSNs that would be asked."""
    out = []
    for j in js:
        item = {"journal_id": j["id"], "titolo": j["titolo"], "fonte": OPENALEX_PROVIDER, "anno": year,
                "metriche": [], "nuove": [], "uguali": 0, "diverse": [], "problemi": [], "richieste": []}
        if client is None:
            item |= {"esito": "da chiedere", "issn_da_chiedere": j["issn"]}
            out.append(item)
            continue
        found = None
        for issn in j["issn"]:
            resp = client.by_issn(issn)
            item["richieste"].append({"issn": issn, "http": resp.status})
            if resp.status == 404:
                continue
            try:
                src = parse_source(resp.body)
            except ValueError as exc:
                item["problemi"].append(f"ISSN {issn}: {exc}")
                continue
            if not (src["issn"] & set(j["issn"])) or (src["type"] and src["type"] != "journal"):
                item["problemi"].append(f"ISSN {issn}: OpenAlex risponde con {src['display_name']} ({src['id']}, "
                                        f"tipo {src['type']}, ISSN {', '.join(sorted(src['issn']))}): non è la rivista")
                continue
            found = (src, resp)
            break
        if found is None:
            item["esito"] = "non trovata"
            if not item["problemi"]:
                item["problemi"].append(f"OpenAlex non conosce gli ISSN {', '.join(j['issn'])}")
            out.append(item)
            continue
        src, resp = found
        item |= {"esito": "trovata", "openalex_id": src["id"], "titolo_openalex": src["display_name"],
                 "lavori": src["works_count"], "citazioni": src["cited_by_count"], "metriche": openalex_metrics(src),
                 "_response": resp}
        if j["openalex_id"] and j["openalex_id"] != src["id"]:
            item["esito"] = "id diverso"
            item["problemi"].append(f"la rivista ha già l'id OpenAlex {j['openalex_id']}, OpenAlex risponde {src['id']}: "
                                    "niente scritto")
            item["metriche"] = []
        else:
            other = conn.execute("SELECT title FROM journal WHERE openalex_id = ? AND id <> ?",
                                 (src["id"], j["id"])).fetchone()
            if other:
                item["esito"] = "id di un'altra rivista"
                item["problemi"].append(f"l'id OpenAlex {src['id']} è già di {other['title']}: niente scritto")
                item["metriche"] = []
            else:
                item["scrivi_id"] = not j["openalex_id"]
        _classify(conn, item, j["id"], year, OPENALEX_PROVIDER, update=True)
        out.append(item)
    return out


def _store_file_snapshot(conn, f: ScimagoFile, *, home: Path, archive_dir: Path) -> str:
    uri = f"{SCIMAGO_URL}&year={f.year}"
    row = conn.execute("SELECT id FROM source_snapshot WHERE source_id = ? AND request_uri = ? AND content_sha256 = ?",
                       (SCIMAGO_SOURCE, uri, f.sha256)).fetchone()
    if row:
        return row["id"]
    dest = Path(archive_dir) / SCIMAGO_SOURCE / f.sha256[:2] / f.sha256[2:4] / f"{f.sha256}.csv"
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".partial")
        shutil.copyfile(f.path, tmp)
        tmp.replace(dest)
    when = datetime.fromtimestamp(f.path.stat().st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    sid = new_id("source_snapshot")
    conn.execute("""INSERT INTO source_snapshot (id, source_id, request_uri, retrieved_at, http_status, content_sha256,
                        content_type, storage_path, size_bytes, licence, storage_allowed)
                    VALUES (?, ?, ?, ?, NULL, ?, 'text/csv', ?, ?, ?, 1)""",
                 (sid, SCIMAGO_SOURCE, uri, when, f.sha256, portable(dest, home), dest.stat().st_size, SCIMAGO_LICENCE))
    return sid


def _insert(conn, jid, m, year, provider, definition, retrieved_at, snap_id) -> None:
    conn.execute("""INSERT INTO journal_metric (id, journal_id, metric, value, value_text, reference_year,
                        subject_category, provider, is_official, definition_url, retrieved_at, source_snapshot_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)""",
                 (new_id("journal_metric"), jid, m["metric"], m.get("value"), m.get("value_text"), year,
                  m.get("subject_category", ""), provider, definition, retrieved_at, snap_id))


def write(conn: sqlite3.Connection, scimago: list[dict], files: list[ScimagoFile], openalex: list[dict], *,
          home: Path, archive_dir: Path, actor: str = "orpheus metriche-riviste") -> dict:
    from .verify_doi import store_snapshot
    counts = {"metriche_scritte": 0, "metriche_aggiornate": 0, "id_openalex_scritti": 0, "riviste_cambiate": 0}
    by_name = {f.path.name: f for f in files}
    with transaction(conn):
        snaps: dict[str, str] = {}
        per_journal: dict[str, dict] = {}
        for it in scimago:
            if it["esito"] != "trovata" or not it["nuove"]:
                continue
            f = by_name[it["file"]]
            if f.path.name not in snaps:
                snaps[f.path.name] = _store_file_snapshot(conn, f, home=home, archive_dir=archive_dir)
            for m in it["nuove"]:
                _insert(conn, it["journal_id"], m, f.year, SCIMAGO_PROVIDER, SCIMAGO_HELP,
                        audit.utc_now(), snaps[f.path.name])
                counts["metriche_scritte"] += 1
            per_journal.setdefault(it["journal_id"], {"scimago": [], "openalex": [], "aggiornate": []})
            per_journal[it["journal_id"]]["scimago"] += [_shown(m) for m in it["nuove"]]
        for it in openalex:
            resp = it.get("_response")
            if resp is None or it["esito"] != "trovata":
                continue
            if not (it["nuove"] or it["diverse"] or it.get("scrivi_id")):
                continue
            snap = store_snapshot(conn, resp, home=home, archive_dir=archive_dir, source_id=OPENALEX_SOURCE,
                                  subdir=OPENALEX_SOURCE, licence=OPENALEX_LICENCE)
            when = resp.retrieved_at or audit.utc_now()
            pj = per_journal.setdefault(it["journal_id"], {"scimago": [], "openalex": [], "aggiornate": []})
            if it.get("scrivi_id"):
                conn.execute("UPDATE journal SET openalex_id = ?, updated_at = ? WHERE id = ? AND openalex_id IS NULL",
                             (it["openalex_id"], audit.utc_now(), it["journal_id"]))
                counts["id_openalex_scritti"] += 1
                pj["openalex_id"] = it["openalex_id"]
            for m in it["nuove"]:
                _insert(conn, it["journal_id"], m, it["anno"], OPENALEX_PROVIDER, OPENALEX_DEF, when, snap)
                counts["metriche_scritte"] += 1
                pj["openalex"].append(_shown(m))
            for d in it["diverse"]:
                conn.execute("""UPDATE journal_metric SET value = ?, value_text = ?, retrieved_at = ?,
                                    source_snapshot_id = ? WHERE id = ?""",
                             (d.get("value"), d.get("value_text"), when, snap, d["row_id"]))
                counts["metriche_aggiornate"] += 1
                pj["aggiornate"].append(f"{_shown(d)} (prima {d['prima']})")
        for jid, payload in per_journal.items():
            audit.record(conn, actor=actor, action="journal_metrics_written", entity_type="journal", entity_id=jid,
                         payload={"regole": RULES_VERSION, **payload}, in_transaction=True)
        counts["riviste_cambiate"] = len(per_journal)
    return counts


def public(items: list[dict]) -> list[dict]:
    """The plan as printed (without the raw answers and internal ids)."""
    out = []
    for it in items:
        x = {k: v for k, v in it.items() if not k.startswith("_") and k not in ("journal_id", "metriche", "nuove",
                                                                              "diverse")}
        x["metriche"] = [_shown(m) for m in it["metriche"]]
        x["nuove"] = len(it["nuove"])
        if it["diverse"]:
            x["diverse"] = [f"{_shown(d)} (prima {d['prima']}; {'aggiornata' if d['aggiorna'] else 'non riscritta'})"
                            for d in it["diverse"]]
        out.append(x)
    return out


def status(conn: sqlite3.Connection) -> dict:
    q = lambda sql: conn.execute(sql).fetchone()[0]   # noqa: E731
    by = conn.execute("""SELECT provider, count(*) AS n, count(DISTINCT journal_id) AS j, max(reference_year) AS y
                         FROM journal_metric GROUP BY provider ORDER BY provider""").fetchall()
    return {"metriche": q("SELECT count(*) FROM journal_metric"),
            "riviste_con_metriche": q("SELECT count(DISTINCT journal_id) FROM journal_metric"),
            "riviste": q("SELECT count(*) FROM journal"),
            "riviste_con_id_openalex": q("SELECT count(*) FROM journal WHERE openalex_id IS NOT NULL"),
            "per_fonte": {r["provider"]: {"metriche": r["n"], "riviste": r["j"], "anno": r["y"]} for r in by}}


def table(conn: sqlite3.Connection) -> list[dict]:
    """One row per journal with the latest value of each main metric (for the log and the report)."""
    out = []
    for j in conn.execute("SELECT id, title FROM journal ORDER BY title"):
        row = {"titolo": j["title"]}
        for key, metric, provider in (("sjr", "SJR", SCIMAGO_PROVIDER), ("quartile", "quartile", SCIMAGO_PROVIDER),
                                      ("h_index_scimago", "h_index", SCIMAGO_PROVIDER),
                                      ("citazioni_2_anni_scimago", "two_year_mean_citedness", SCIMAGO_PROVIDER),
                                      ("citazioni_2_anni_openalex", "two_year_mean_citedness", OPENALEX_PROVIDER),
                                      ("h_index_openalex", "h_index", OPENALEX_PROVIDER)):
            r = conn.execute("""SELECT value, value_text, reference_year FROM journal_metric WHERE journal_id = ?
                                AND metric = ? AND provider = ? AND subject_category = ''
                                ORDER BY reference_year DESC LIMIT 1""", (j["id"], metric, provider)).fetchone()
            if r is not None:
                v = r["value"]
                row[key] = r["value_text"] if v is None else (int(v) if metric == "h_index" and v == int(v) else round(v, 3))
                row.setdefault("anni", {})[provider] = r["reference_year"]
        cats = conn.execute("""SELECT subject_category, value_text FROM journal_metric WHERE journal_id = ?
                               AND metric = 'quartile' AND provider = ? AND subject_category <> ''
                               AND reference_year = (SELECT max(reference_year) FROM journal_metric WHERE journal_id = ?
                                                     AND provider = ? AND metric = 'quartile')
                               ORDER BY subject_category""", (j["id"], SCIMAGO_PROVIDER, j["id"], SCIMAGO_PROVIDER)).fetchall()
        if cats:
            row["categorie"] = [f"{c['subject_category']} ({c['value_text']})" for c in cats]
        out.append(row)
    return out
