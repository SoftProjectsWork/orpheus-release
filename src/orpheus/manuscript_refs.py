"""References of a manuscript checked with Crossref (T-038, Fase 6; user's decision of 2026-10-16 00:30).

Only the DOIs and the titles of the cited works go to Crossref, never the text of the manuscript (REQUIREMENTS R10).
For every reference of the .docx (manuscript.Reference):
  1. with a DOI already in ORPHEUS (table `article`): compared with that record, no network;
  2. with another DOI: Crossref /works/{doi};
  3. without a DOI: a bibliographic search (title taken from the reference + first author + year), the best
     candidate that passes the rules;
then the rules cite-v2 of verify_citations (year ±1, first author, normalized title) give
  verified   = year and title agree, the first author does not disagree (with source and date, ADR-0004);
  mismatch   = the DOI's record disagrees on year, first author or title;
  not_found  = Crossref does not know the DOI, or no record matches the title, author and year;
  unverified = not enough to compare, or the network failed.
The answers (Crossref metadata only) are cached in this PC's local folder (ORPHEUS_LOCAL_DIR/crossref_manoscritti),
never in the database (no migration) and never on GitHub. Read-only on the database. An LLM never takes part.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import crossref
from .identifiers import IdentifierError, normalize_doi
from .verify_citations import MIN_TITLE_WORDS, STATUS_IT, _words, evaluate

RULES_VERSION = "mref-v1"            # cite-v2 + title from the reference text + bibliographic search without DOI
CACHE_SUBDIR = "crossref_manoscritti"
CACHE_DAYS = 180
SEARCH_ROWS = 3
SOURCE_ORPHEUS = "ORPHEUS (articolo nel database)"
SOURCE_CROSSREF = "Crossref"

_LABEL = re.compile(r"^\s*\[?\d{1,4}[\].)]?\s+")
_URL = re.compile(r"(?:https?://\S+|doi\s*:\s*\S+|\b10\.\d{4,9}/\S+)", re.I)
_END = re.compile(r"[.?!](?:\s|$)")
_AUTHORS_END = re.compile(r"(?:\bet\s+al|\b[A-ZÀ-Ý]{1,3}|\b(?:[A-ZÀ-Ý]\.\s?){1,3}[A-ZÀ-Ý]?)\.\s+(?=[A-ZÀ-Ý0-9\"“'‘])")


def _cut(text: str) -> str | None:
    m = _END.search(text)
    title = (text[:m.start() + (1 if text[m.start()] in "?!" else 0)] if m else text).strip(" .,;:\"“”'‘’")
    return title if len(_words(title)) >= MIN_TITLE_WORDS else None


def title_of(text: str) -> str | None:
    """The title of the cited work, read from the reference: APA "(2019). Title." / Harvard "2019. Title." /
    Vancouver "Surname AB, Surname C. Title. Journal". None when it cannot be told apart."""
    body = _URL.sub(" ", _LABEL.sub("", text or "", count=1))
    body = re.sub(r"\s+", " ", body).strip()
    m = re.search(r"\((?:19|20)\d{2}[a-z]?(?:,[^)]{0,20})?\)\.?\s+", body)
    if m:
        return _cut(body[m.end():])
    m = re.search(r"[,.]\s(?:19|20)\d{2}[a-z]?\.\s+", body)
    if m and m.start() < len(body) // 2:
        return _cut(body[m.end():])
    m = _AUTHORS_END.search(body)
    if m and m.end() < len(body):
        return _cut(body[m.end():])
    return None


def _meta(ref) -> dict:
    year = None
    try:
        year = int(ref.year) if ref.year else None
    except (TypeError, ValueError):
        pass
    return {"year": year, "first_author": ref.first_author or "", "title": title_of(ref.text) or "",
            "text": ref.text or "", "origine": "manoscritto"}


def _doi(d: str | None) -> str | None:
    try:
        return normalize_doi(d) if d else None
    except (IdentifierError, ValueError):
        return None


# ---------------------------------------------------------------- local cache (this PC only)

class Cache:
    def __init__(self, folder: Path | None, days: int = CACHE_DAYS, now: datetime | None = None):
        self.folder = Path(folder) / CACHE_SUBDIR if folder else None
        self.days = days
        self.now = now or datetime.now(timezone.utc)
        self.hits = 0

    def _path(self, key: str) -> Path:
        return self.folder / (hashlib.sha256(key.encode("utf-8")).hexdigest()[:32] + ".json")

    def get(self, key: str) -> dict | None:
        if not self.folder:
            return None
        p = self._path(key)
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            when = datetime.fromisoformat(data["retrieved_at"].replace("Z", "+00:00"))
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if self.now - when > timedelta(days=self.days):
            return None
        self.hits += 1
        return data

    def put(self, key: str, data: dict) -> None:
        if not self.folder:
            return
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            self._path(key).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass


# ---------------------------------------------------------------- lookups

def orpheus_record(conn: sqlite3.Connection | None, doi: str) -> dict | None:
    """The article of ORPHEUS with this DOI, in the shape of crossref.summary (title, years, authors)."""
    if conn is None:
        return None
    row = conn.execute("SELECT id, title, publication_year, updated_at FROM article WHERE doi = ?", (doi,)).fetchone()
    if not row:
        return None
    first = conn.execute("SELECT a.display_name FROM article_author aa JOIN author a ON a.id = aa.author_id "
                         "WHERE aa.article_id = ? ORDER BY aa.position LIMIT 1", (row[0],)).fetchone()
    return {"doi": doi, "title": row[1] or "", "subtitle": "", "years": [row[2]] if row[2] else [],
            "authors": [{"name": first[0]}] if first else [], "data": (row[3] or "")[:10]}


def _by_doi(client, cache: Cache, doi: str) -> dict:
    key = "doi:" + doi
    hit = cache.get(key)
    if hit:
        return hit
    resp = client.get_work(doi)
    data = {"retrieved_at": resp.retrieved_at, "http": resp.status, "records": []}
    if resp.status == 200:
        data["records"] = [crossref.summary(crossref.parse_work(resp.body))]
    cache.put(key, data)
    return data


def _by_search(client, cache: Cache, query: str) -> dict:
    key = "ricerca:" + query
    hit = cache.get(key)
    if hit:
        return hit
    resp = client.search_bibliographic(query, rows=SEARCH_ROWS)
    data = {"retrieved_at": resp.retrieved_at, "http": resp.status,
            "records": [crossref.summary(w) for w in crossref.parse_work_list(resp.body)]}
    cache.put(key, data)
    return data


def _row(ref, status: str, reasons: list[str], source: str | None, date: str | None, doi_found: str | None) -> dict:
    if status == "verified":
        assert source and date, "ADR-0004: nessun riferimento verificato senza fonte e data"
    return {"n": ref.n, "numero": ref.label, "doi": _doi(ref.doi), "esito": status, "esito_testo": STATUS_IT[status],
            "motivi": reasons, "fonte": source, "data": date, "doi_trovato": doi_found, "testo": (ref.text or "")[:160]}


def check_one(ref, conn: sqlite3.Connection | None, client, cache: Cache) -> dict:
    meta = _meta(ref)
    doi = _doi(ref.doi)
    if doi:
        rec = orpheus_record(conn, doi)
        if rec:
            status, reasons, _ = evaluate(meta, rec, doi)
            return _row(ref, status, reasons, SOURCE_ORPHEUS, rec["data"] or None, doi)
        if client is None:
            return _row(ref, "unverified", ["DOI non presente in ORPHEUS; Crossref non interrogato"], None, None, None)
        data = _by_doi(client, cache, doi)
        rec = data["records"][0] if data["records"] else None
        status, reasons, _ = evaluate(meta, rec, doi)
        return _row(ref, status, reasons, SOURCE_CROSSREF, data["retrieved_at"][:10], doi if rec else None)
    if not meta["title"]:
        return _row(ref, "unverified", ["riferimento senza DOI e senza un titolo riconoscibile"], None, None, None)
    if not meta["first_author"] and meta["year"] is None:
        return _row(ref, "unverified", ["riferimento senza DOI, senza primo autore e senza anno"], None, None, None)
    if client is None:
        return _row(ref, "unverified", ["riferimento senza DOI; Crossref non interrogato"], None, None, None)
    query = " ".join(str(x) for x in (meta["title"], meta["first_author"], meta["year"]) if x)
    data = _by_search(client, cache, query)
    for rec in data["records"]:
        status, reasons, checks = evaluate(meta, rec, rec.get("doi"))
        if status == "verified" and checks.get("author") is not False:
            return _row(ref, "verified", ["trovato con titolo, primo autore e anno: " + reasons[0]], SOURCE_CROSSREF,
                        data["retrieved_at"][:10], rec.get("doi"))
    return _row(ref, "not_found", ["nessun record Crossref con lo stesso titolo, primo autore e anno"],
                SOURCE_CROSSREF, data["retrieved_at"][:10], None)


def run(references: list, conn: sqlite3.Connection | None, client, local_dir: Path | None,
        now: datetime | None = None) -> dict:
    """Check every reference; a 403 stops the run (the rest stay 'non verificato'). Read-only on the database."""
    cache = Cache(local_dir, now=now)
    rows, stopped = [], None
    for ref in references:
        if stopped:
            rows.append(_row(ref, "unverified", ["verifica interrotta: " + stopped], None, None, None))
            continue
        try:
            rows.append(check_one(ref, conn, client, cache))
        except crossref.CrossrefBlocked as exc:
            stopped = str(exc)
            rows.append(_row(ref, "unverified", ["verifica interrotta: " + stopped], None, None, None))
        except (crossref.CrossrefError, ValueError) as exc:
            rows.append(_row(ref, "unverified", [f"Crossref non ha risposto: {exc}"], None, None, None))
    counts = {v: sum(1 for r in rows if r["esito"] == k) for k, v in STATUS_IT.items()}
    return {"regole": RULES_VERSION, "conteggi": counts, "riferimenti": rows,
            "richieste_crossref": getattr(client, "requests_made", 0) if client else 0,
            "dalla_cache": cache.hits, "interrotta": stopped,
            "data": (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M")}


def public(result: dict) -> dict:
    """For the log (on GitHub): numbers only, no titles, no text of the references."""
    return {"regole": result["regole"], "conteggi": result["conteggi"], "richieste_crossref": result["richieste_crossref"],
            "dalla_cache": result["dalla_cache"], "interrotta": bool(result["interrotta"])}
