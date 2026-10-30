"""Deterministic duplicate detection between articles and files (T-013), READ-ONLY.

Rules ``dedup-v1`` (nothing is ever merged or deleted: the output is a report for the user):

* ``stesso_doi_piu_file``  several documents (PDFs) linked to the same article: same article, more files;
* ``stesso_pmid``          two articles with the same PMID (impossible today: article.pmid is UNIQUE; kept as a guard);
* ``versioni``             Crossref says one DOI updates the other (correction, erratum, retraction, new version);
* ``stesso_lavoro``        different DOIs, same normalised title + same year + same first author (surname):
                           probably the same work published twice (e.g. preprint and article) — to check by hand;
* ``solo_titolo``          same normalised title but different year or first author (e.g. conference abstract
                           2021 and full article 2022): DIFFERENT articles, listed only for information.

Authors (``authors-v1``): proposed rows for ``author`` / ``article_author`` from the Crossref answers already stored
(no network). Nothing is written by :func:`propose_authors`; :func:`write_authors` writes them only when called
(needs the user's yes, see PROMPT_PROSSIMA_SESSIONE.md).
"""
from __future__ import annotations

import csv
import re
import sqlite3
import unicodedata
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path

from . import audit, crossref
from .ids import new_id
from .quality import _norm_title
from .verify_doi import read_snapshot

RULES_VERSION = "dedup-v1"
AUTHORS_VERSION = "authors-v1"
_ORCID = re.compile(r"(\d{4}-\d{4}-\d{4}-\d{3}[\dX])$")


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    return re.sub(r"[^a-z0-9]+", " ", "".join(c for c in text if not unicodedata.combining(c)).lower()).strip()


def bare_orcid(value: str | None) -> str | None:
    """'https://orcid.org/0000-0002-1825-0097' -> '0000-0002-1825-0097' (None if not valid)."""
    m = _ORCID.search((value or "").strip().upper().rstrip("/"))
    if not m:
        return None
    digits = m.group(1).replace("-", "")
    total = 0
    for ch in digits[:-1]:
        total = (total + int(ch)) * 2
    check = (12 - total % 11) % 11
    return m.group(1) if digits[-1] == ("X" if check == 10 else str(check)) else None


def display_name(a: dict) -> str:
    if a.get("name"):
        return a["name"].strip()
    return " ".join(p for p in (a.get("given", "").strip(), a.get("family", "").strip()) if p)


def first_family(authors: list[dict]) -> str | None:
    if not authors:
        return None
    first = next((a for a in authors if a.get("sequence") == "first"), authors[0])
    key = _fold(first.get("family") or first.get("name", "").split(" ")[-1])
    return key or None


def crossref_records(conn: sqlite3.Connection, home: Path) -> tuple[dict[str, dict], list[dict]]:
    """{article_id: crossref.summary} from the newest confirmed answer of each article; plus problems (no network)."""
    rows = conn.execute(
        """SELECT dc.article_id, dc.source_snapshot_id FROM doi_check dc
           WHERE dc.outcome = 'confirmed' AND dc.checked_at = (
               SELECT max(d2.checked_at) FROM doi_check d2 WHERE d2.article_id = dc.article_id AND d2.outcome = 'confirmed')
           ORDER BY dc.article_id""").fetchall()
    records, problems = {}, []
    for r in rows:
        if r["article_id"] in records:
            continue
        try:
            status, body = read_snapshot(conn, r["source_snapshot_id"], home)
            records[r["article_id"]] = crossref.summary(crossref.parse_work(body))
        except (OSError, ValueError, KeyError) as exc:
            problems.append({"articolo": r["article_id"], "problema": str(exc)})
    return records, problems


def find_pairs(conn: sqlite3.Connection, records: dict[str, dict]) -> list[dict]:
    pairs: list[dict] = []
    for r in conn.execute("""SELECT article_id, count(*) n, group_concat(id, ' ') docs FROM document
                             WHERE article_id IS NOT NULL GROUP BY article_id HAVING n > 1 ORDER BY article_id"""):
        pairs.append({"regola": "stesso_doi_piu_file", "tipo": "stesso articolo", "a": r["article_id"], "b": None,
                      "motivo": f"{r['n']} file collegati allo stesso articolo: {r['docs']}"})
    arts = [dict(r) for r in conn.execute("SELECT id, doi, pmid, title, publication_year FROM article ORDER BY id")]
    by_pmid: dict[str, list[dict]] = {}
    for a in arts:
        if a["pmid"]:
            by_pmid.setdefault(a["pmid"], []).append(a)
    for pmid, group in by_pmid.items():
        for x, y in combinations(group, 2):
            pairs.append({"regola": "stesso_pmid", "tipo": "stesso articolo", "a": x["id"], "b": y["id"],
                          "motivo": f"stesso PMID {pmid}"})
    by_doi = {a["doi"]: a for a in arts if a["doi"]}
    seen: set[tuple[str, str]] = set()
    for a in arts:
        rec = records.get(a["id"]) or {}
        for rel, kind in [(u, "aggiorna") for u in rec.get("update_to", [])] + \
                         [(u, "aggiornato da") for u in rec.get("updated_by", [])]:
            other = by_doi.get(rel["doi"])
            if other and other["id"] != a["id"]:
                key = tuple(sorted((a["id"], other["id"])))
                if key not in seen:
                    seen.add(key)
                    pairs.append({"regola": "versioni", "tipo": "versioni", "a": key[0], "b": key[1],
                                  "motivo": f"Crossref: {a['doi']} {kind} {other['doi']} ({rel.get('type') or '?'})"})
    by_title: dict[str, list[dict]] = {}
    for a in arts:
        key = _norm_title(a["title"])
        if key:
            by_title.setdefault(key, []).append(a)
    for group in by_title.values():
        for x, y in combinations(group, 2):
            if tuple(sorted((x["id"], y["id"]))) in seen:
                continue
            fx = first_family((records.get(x["id"]) or {}).get("authors", []))
            fy = first_family((records.get(y["id"]) or {}).get("authors", []))
            same_year = x["publication_year"] is not None and x["publication_year"] == y["publication_year"]
            same_first = fx is not None and fx == fy
            if same_year and same_first:
                pairs.append({"regola": "stesso_lavoro", "tipo": "probabile stesso lavoro (da controllare)",
                              "a": x["id"], "b": y["id"],
                              "motivo": f"stesso titolo, anno {x['publication_year']}, primo autore {fx}; DOI diversi"})
            else:
                diff = []
                if not same_year:
                    diff.append(f"anno {x['publication_year']} / {y['publication_year']}")
                if not same_first:
                    diff.append(f"primo autore {fx or '?'} / {fy or '?'}")
                pairs.append({"regola": "solo_titolo", "tipo": "solo titolo uguale (articoli diversi)",
                              "a": x["id"], "b": y["id"], "motivo": "stesso titolo; " + ", ".join(diff)})
    return pairs


def propose_authors(conn: sqlite3.Connection, records: dict[str, dict]) -> dict:
    """Rows that write_authors would insert; one author per ORCID, otherwise one per exact folded name."""
    filled = {r["article_id"] for r in conn.execute("SELECT DISTINCT article_id FROM article_author")}
    existing = {r["orcid"]: r["id"] for r in conn.execute("SELECT id, orcid FROM author WHERE orcid IS NOT NULL")}
    by_name = {f"nome:{_fold(r['display_name'])}": r["id"]
               for r in conn.execute("SELECT id, display_name FROM author WHERE orcid IS NULL ORDER BY id DESC")}
    authors: dict[str, dict] = {}
    links: list[dict] = []
    skipped = []
    for art_id in sorted(records):
        if art_id in filled:
            skipped.append(art_id)
            continue
        for pos, a in enumerate(records[art_id].get("authors") or [], start=1):
            name = display_name(a)
            if not name:
                continue
            orcid = bare_orcid(a.get("orcid"))
            key = f"orcid:{orcid}" if orcid else f"nome:{_fold(name)}"
            if key not in authors:
                authors[key] = {"key": key, "display_name": name, "orcid": orcid,
                                "id": existing.get(orcid) if orcid else by_name.get(key), "articoli": 0}
            authors[key]["articoli"] += 1
            links.append({"article_id": art_id, "position": pos, "author_key": key})
    shared = sum(1 for a in authors.values() if a["articoli"] > 1)
    return {"versione": AUTHORS_VERSION, "autori": list(authors.values()), "collegamenti": links,
            "articoli_gia_con_autori": skipped,
            "riepilogo": {"articoli": len({l["article_id"] for l in links}), "righe_article_author": len(links),
                          "autori": len(authors), "con_orcid": sum(1 for a in authors.values() if a["orcid"]),
                          "senza_orcid": sum(1 for a in authors.values() if not a["orcid"]),
                          "in_piu_articoli": shared, "articoli_gia_con_autori": len(skipped)}}


def write_authors(conn: sqlite3.Connection, proposal: dict, *, audit_actor: str | None = None) -> dict:
    """Insert the proposed rows in one transaction. ONLY after the user's yes (irreversible on the real database).

    With audit_actor, one audit event (action write_authors) is written in the same transaction."""
    ids: dict[str, str] = {}
    created = 0
    with conn:
        for a in proposal["autori"]:
            if a["id"]:
                ids[a["key"]] = a["id"]
                continue
            ids[a["key"]] = new_id("author")
            conn.execute("INSERT INTO author (id, display_name, orcid) VALUES (?, ?, ?)",
                         (ids[a["key"]], a["display_name"], a["orcid"]))
            created += 1
        for link in proposal["collegamenti"]:
            conn.execute("INSERT INTO article_author (article_id, position, author_id) VALUES (?, ?, ?)",
                         (link["article_id"], link["position"], ids[link["author_key"]]))
        out = {"autori_creati": created, "righe_article_author": len(proposal["collegamenti"]),
               "articoli": len({l["article_id"] for l in proposal["collegamenti"]})}
        if audit_actor and proposal["collegamenti"]:
            audit.record(conn, actor=audit_actor, action="write_authors", entity_type="author",
                         payload={"versione": proposal["versione"], **out}, in_transaction=True)
    return out


def build(conn: sqlite3.Connection, home: Path) -> dict:
    records, problems = crossref_records(conn, home)
    pairs = find_pairs(conn, records)
    proposal = propose_authors(conn, records)
    counts: dict[str, int] = {}
    for p in pairs:
        counts[p["regola"]] = counts.get(p["regola"], 0) + 1
    return {"regole": RULES_VERSION, "coppie": pairs, "conteggi": counts, "risposte_crossref_lette": len(records),
            "problemi": problems, "autori": proposal}


def write_csv(report: dict, home: Path, now: datetime | None = None) -> Path:
    now = now or datetime.now(timezone.utc)
    path = Path(home) / "rapporti" / f"doppioni_{now.strftime('%Y-%m-%d_%H%M%S')}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["regola", "tipo", "a", "b", "motivo"], delimiter=";")
        w.writeheader()
        w.writerows(report["coppie"])
    return path


def public(report: dict, path: Path) -> dict:
    return {"regole": report["regole"], "risposte_crossref_lette": report["risposte_crossref_lette"],
            "conteggi": report["conteggi"], "file_csv": str(path), "problemi": report["problemi"][:20],
            "esempi": report["coppie"][:10],
            "autori_proposta": {**report["autori"]["riepilogo"], "versione": report["autori"]["versione"],
                                "scritti_nel_database": False}}

