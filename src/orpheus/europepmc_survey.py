"""Open-access full texts in Europe PMC for the complete articles (T-010): report (parte 2b) and storage (parte 4).

For every article with access_level 'full_text', a DOI and at least one PDF linked (as verify_pubmed):
  1. search the DOI in Europe PMC (europepmc.py) and apply the rules epmc-v1;
  2. when the full text is in Europe PMC (a PMCID and inEPMC = Y), ask for the JATS XML and read it in memory:
     licence, sections, words, figures, tables, references (with DOI / PMID);
  3. write one row per article in rapporti/europepmc_<data>.csv and return the counts.
Without `save`, NOTHING is written in the database and nothing is kept of the answers.

With `save` (migration 0006, user's yes of 2026-10-11 "Anche i testi (0006)"), one transaction per article:
  - the raw search answer (source_snapshot) and one fulltext_check row (append-only), with an audit event;
  - for 'found': article.pmcid (if free) and article.licence (licence of the JATS, else of the search record);
  - the JATS full text is KEPT only when the JATS itself carries a Creative Commons licence and its DOI is the
    article's: then source_snapshot (storage_allowed = 1, licence) and its sections in article_section.
A network failure is not an outcome: the article stays to be asked on the next run.
"""

from __future__ import annotations

import csv
import math
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import json

from . import audit, europepmc
from .db import transaction
from .ids import new_id
from .verify_doi import store_snapshot

SNAPSHOT_SUBDIR = "europepmc"
SEARCH_LICENCE = ("Europe PMC search record (EMBL-EBI terms of use: no restriction beyond the data owner's); "
                  "abstracts may be copyrighted.")

OUTCOME_IT = {"found": "trovato", "not_found": "non trovato", "mismatch": "discordante", "ambiguous": "ambiguo"}
AVAILABILITY_IT = {"open_access": "testo aperto (open access)", "free_not_oa": "testo in Europe PMC, non open access",
                   "metadata_only": "solo metadati"}
STOP_AFTER_ERRORS = 3
CSV_FIELDS = ["doi", "pmid_orpheus", "esito", "motivo", "pmcid", "pmid_europepmc", "disponibilita", "open_access",
              "in_europepmc", "licenza_ricerca", "xml_http", "licenza_xml", "sezioni", "parole", "figure", "tabelle",
              "riferimenti", "riferimenti_con_doi", "riferimenti_con_pmid", "titolo_europepmc", "testo_licenza_xml",
              "salvato", "errore"]


def candidates(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Complete articles with a DOI and at least one PDF linked (DOI confirmed by verify-dois)."""
    return conn.execute(
        """SELECT a.id, a.doi, a.pmid, a.title FROM article a
           WHERE a.access_level = 'full_text' AND a.doi IS NOT NULL
             AND EXISTS (SELECT 1 FROM document d WHERE d.article_id = a.id)
           ORDER BY a.doi""").fetchall()


def latest_checks(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    rows = conn.execute(
        """SELECT c.* FROM fulltext_check c
           WHERE c.rowid = (SELECT c2.rowid FROM fulltext_check c2 WHERE c2.article_id = c.article_id
                            ORDER BY c2.checked_at DESC, c2.rowid DESC LIMIT 1)""").fetchall()
    return {r["article_id"]: r for r in rows}


def select(conn: sqlite3.Connection, mode: str = "nuovi") -> tuple[list[sqlite3.Row], int]:
    """(articles to ask, articles skipped) for a run that saves. 'nuovi': never checked (or DOI changed);
    'ricontrolla': also those not found, discordant or ambiguous."""
    latest = latest_checks(conn)
    todo, skipped = [], 0
    for art in candidates(conn):
        last = latest.get(art["id"])
        same = last is not None and last["doi"] == art["doi"]
        if same and (last["outcome"] == "found" or mode == "nuovi"):
            skipped += 1
            continue
        todo.append(art)
    return todo, skipped


def overall(conn: sqlite3.Connection) -> dict:
    """Current state over the candidate articles (latest check each) and what is stored."""
    latest = latest_checks(conn)
    cands = candidates(conn)
    counts = {v.replace(" ", "_"): 0 for v in OUTCOME_IT.values()}
    avail = {"testo_aperto": 0, "testo_non_aperto": 0, "solo_metadati": 0}
    names = {"open_access": "testo_aperto", "free_not_oa": "testo_non_aperto", "metadata_only": "solo_metadati"}
    checked = 0
    for art in cands:
        last = latest.get(art["id"])
        if last is not None and last["doi"] == art["doi"]:
            checked += 1
            counts[OUTCOME_IT[last["outcome"]].replace(" ", "_")] += 1
            avail[names[last["availability"]]] += 1
    one = lambda sql: conn.execute(sql).fetchone()[0]                          # noqa: E731
    return {**counts, **avail, "da_cercare": len(cands) - checked,
            "testi_salvati": one("SELECT count(DISTINCT article_id) FROM article_section"),
            "sezioni_salvate": one("SELECT count(*) FROM article_section"),
            "articoli_con_pmcid": one("SELECT count(*) FROM article WHERE pmcid IS NOT NULL"),
            "articoli_con_licenza": one("SELECT count(*) FROM article WHERE licence IS NOT NULL")}


def estimate(n: int) -> dict:
    """At least one search per article; a full text more for those in Europe PMC (unknown before the run)."""
    return {"richieste_minime": n, "minuti_stimati": max(1, math.ceil(n * 1.3 / 60)) if n else 0}


@dataclass
class SurveyResult:
    rows: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    requests: int = 0
    stopped: str | None = None
    missing_fields: set[str] = field(default_factory=set)
    skipped: int = 0
    written: list[dict] = field(default_factory=list)


def _yn(value: bool | None) -> str:
    return {True: "S", False: "N"}.get(value, "?")


def survey_one(client: europepmc.EuropePMCClient, article, *, fetch_xml: bool = True) -> dict:
    """One article: search, rules, JATS read in memory. Returns the CSV row and what `record` needs."""
    resp = client.search_doi(article["doi"])
    search = europepmc.parse_search(resp.body)
    verdict = europepmc.evaluate(article["doi"], search, article["pmid"])
    rec = verdict["record"] if verdict["outcome"] == "found" else None
    avail = europepmc.availability(rec)
    row = {"doi": article["doi"], "pmid_orpheus": article["pmid"] or "", "esito": OUTCOME_IT[verdict["outcome"]],
           "motivo": "; ".join(verdict["reasons"]), "pmcid": (rec or {}).get("pmcid") or "",
           "pmid_europepmc": (rec or {}).get("pmid") or "", "disponibilita": AVAILABILITY_IT[avail],
           "open_access": _yn((rec or {}).get("is_open_access")), "in_europepmc": _yn((rec or {}).get("in_epmc")),
           "licenza_ricerca": europepmc.normalize_licence((rec or {}).get("license")) or "",
           "titolo_europepmc": (rec or {}).get("title") or "", "_disponibilita": avail}
    xml, art = None, None
    if fetch_xml and avail != "metadata_only":
        xml = client.full_text_xml(rec["pmcid"])
        row["xml_http"] = xml.status
        if xml.status == 200:
            try:
                art = europepmc.parse_jats(xml.body)
            except ValueError as exc:
                row["errore"] = f"testo XML non letto: {exc}"
            else:
                refs = art["references"]
                row.update({"licenza_xml": art["licence"] or "", "sezioni": len(art["sections"]), "parole": art["words"],
                            "figure": art["figures"], "tabelle": art["tables"], "riferimenti": len(refs),
                            "riferimenti_con_doi": sum(1 for r in refs if r["doi"]),
                            "riferimenti_con_pmid": sum(1 for r in refs if r["pmid"]),
                            "testo_licenza_xml": (art["licence_text"] or art["copyright"])[:300]})
                if art["ids"].get("doi") and art["ids"]["doi"] != article["doi"]:
                    row["errore"] = f"il testo XML ha un altro DOI: {art['ids']['doi']}"
    return {"row": row, "missing": set(search["missing_fields"]), "search_resp": resp, "verdict": verdict,
            "rec": rec, "availability": avail, "xml_resp": xml, "jats": art}


def is_cc(licence: str | None) -> bool:
    return bool(licence) and (licence.startswith("CC ") or licence.startswith("CC0"))


def record(conn: sqlite3.Connection, article, r: dict, *, home: Path, archive_dir: Path, actor: str = "cli") -> dict:
    """Write one look-up (migration 0006) in one transaction; see the module docstring. Returns what was written."""
    verdict, rec, avail, art = r["verdict"], r["rec"], r["availability"], r["jats"]
    jats_lic = art["licence"] if art else None
    search_lic = europepmc.normalize_licence(rec.get("license")) if rec else None
    licence, lsrc = (jats_lic, "jats") if jats_lic else ((search_lic, "search") if search_lic else (None, None))
    reasons = list(verdict["reasons"])
    doi_ok = not art or not art["ids"].get("doi") or art["ids"]["doi"] == article["doi"]
    store_text = bool(art and is_cc(jats_lic) and doi_ok)
    if art and not store_text:
        reasons.append("testo integrale non conservato: " + (
            "il testo XML ha un altro DOI" if not doi_ok else
            f"licenza Creative Commons non scritta nel testo XML (ricerca: {search_lic or 'nessuna'})"))
    details = {"pmid_europepmc": (rec or {}).get("pmid"), "pmcid": (rec or {}).get("pmcid"),
               "open_access": (rec or {}).get("is_open_access"), "in_europepmc": (rec or {}).get("in_epmc"),
               "licenza_ricerca": search_lic, "licenza_xml": jats_lic,
               "testo_licenza_xml": ((art["licence_text"] or art["copyright"])[:300] if art else None),
               "xml_http": r["xml_resp"].status if r["xml_resp"] is not None else None}
    if art:
        details |= {"sezioni": len(art["sections"]), "parole": art["words"], "figure": art["figures"],
                    "tabelle": art["tables"], "riferimenti": len(art["references"]),
                    "riferimenti_con_doi": sum(1 for x in art["references"] if x["doi"])}
    check_id = new_id("fulltext_check")
    out = {"testo_salvato": store_text, "pmcid_scritto": False, "licenza_scritta": False, "sezioni": 0}
    with transaction(conn):
        search_snap = store_snapshot(conn, r["search_resp"], home=home, archive_dir=archive_dir,
                                     source_id=europepmc.SOURCE_ID, subdir=SNAPSHOT_SUBDIR, licence=SEARCH_LICENCE)
        fetch_snap = (store_snapshot(conn, r["xml_resp"], home=home, archive_dir=archive_dir, source_id=europepmc.SOURCE_ID,
                                     subdir=SNAPSHOT_SUBDIR, licence=f"{jats_lic} (dal testo JATS)")
                      if store_text else None)
        pmcid = (rec or {}).get("pmcid") if verdict["outcome"] == "found" and avail != "metadata_only" else None
        conn.execute(
            """INSERT INTO fulltext_check (id, article_id, doi, source_id, outcome, availability, pmcid, licence,
                   licence_source, text_stored, reasons, details, rules_version, search_snapshot_id, fetch_snapshot_id,
                   checked_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (check_id, article["id"], article["doi"], europepmc.SOURCE_ID, verdict["outcome"], avail, pmcid,
             licence if verdict["outcome"] == "found" else None, lsrc if verdict["outcome"] == "found" else None,
             int(store_text), json.dumps(reasons, ensure_ascii=False), json.dumps(details, ensure_ascii=False),
             europepmc.RULES_VERSION, search_snap, fetch_snap, audit.utc_now()))
        if verdict["outcome"] == "found":
            cur = conn.execute("SELECT pmcid, licence FROM article WHERE id = ?", (article["id"],)).fetchone()
            pmc = (rec or {}).get("pmcid")
            if pmc and cur["pmcid"] is None and conn.execute(
                    "SELECT 1 FROM article WHERE pmcid = ? AND id <> ?", (pmc, article["id"])).fetchone() is None:
                conn.execute("UPDATE article SET pmcid = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id = ?",
                             (pmc, article["id"]))
                out["pmcid_scritto"] = True
            if licence and (cur["licence"] is None or lsrc == "jats") and cur["licence"] != licence:
                conn.execute("UPDATE article SET licence = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id = ?",
                             (licence, article["id"]))
                out["licenza_scritta"] = True
        if store_text:
            conn.execute("DELETE FROM article_section WHERE article_id = ?", (article["id"],))
            parts = []
            if art["abstract"]:
                parts.append({"level": 1, "sec_type": "abstract", "title": "Abstract", "text": art["abstract"],
                              "words": europepmc._words(art["abstract"])})
            else:
                parts.append(None)
            parts += art["sections"]
            for ord_, sec in enumerate(parts):
                if sec is None or not (sec["text"] or sec["title"]):
                    continue
                conn.execute(
                    """INSERT INTO article_section (article_id, source_snapshot_id, ord, level, sec_type, title, text, words,
                           fulltext_check_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (article["id"], fetch_snap, ord_, sec["level"], sec["sec_type"], sec["title"], sec["text"],
                     sec["words"], check_id))
                out["sezioni"] += 1
        audit.record(conn, actor=actor, action="europepmc_check", entity_type="article", entity_id=article["id"],
                     payload={"doi": article["doi"], "outcome": verdict["outcome"], "availability": avail,
                              "pmcid": pmcid, "licence": licence, "licence_source": lsrc, "text_stored": store_text,
                              "fulltext_check_id": check_id, "search_snapshot_id": search_snap,
                              "fetch_snapshot_id": fetch_snap, "rules": europepmc.RULES_VERSION},
                     in_transaction=True)
    return out


def run(conn: sqlite3.Connection, client: europepmc.EuropePMCClient, *, limit: int | None = None,
        fetch_xml: bool = True, save: bool = False, home: Path | None = None, archive_dir: Path | None = None,
        mode: str = "nuovi", actor: str = "cli") -> SurveyResult:
    """Ask Europe PMC about the candidates (all of them for the report; with `save` only those not yet checked,
    see `select`). A network failure for one article is an error (asked again next time); 3 in a row, a 403 or a
    refused certificate stop the run."""
    res = SurveyResult()
    if save:
        todo, res.skipped = select(conn, mode)
    else:
        todo = candidates(conn)
    if limit is not None:
        res.skipped += max(0, len(todo) - max(0, limit))
        todo = todo[:max(0, limit)]
    in_a_row = 0
    for art in todo:
        try:
            r = survey_one(client, art, fetch_xml=fetch_xml)
            row, missing = r["row"], r["missing"]
            if save:
                written = record(conn, art, r, home=home, archive_dir=archive_dir, actor=actor)
                row["salvato"] = ("testo e " if written["testo_salvato"] else "") + "esito"
                res.written.append({"doi": art["doi"], **written})
        except europepmc.EuropePMCBlocked as exc:
            res.errors.append({"doi": art["doi"], "errore": str(exc)})
            res.stopped = str(exc)
            break
        except (europepmc.EuropePMCError, ValueError, KeyError, UnicodeDecodeError) as exc:
            res.errors.append({"doi": art["doi"], "errore": str(exc)})
            res.rows.append({"doi": art["doi"], "pmid_orpheus": art["pmid"] or "", "esito": "errore",
                             "errore": str(exc), "_disponibilita": None})
            in_a_row += 1
            if in_a_row >= STOP_AFTER_ERRORS:
                res.stopped = f"{STOP_AFTER_ERRORS} errori di fila: Europe PMC non risponde, riprova più tardi"
                break
            continue
        in_a_row = 0
        res.missing_fields |= missing
        res.rows.append(row)
    res.requests = client.requests_made
    return res


def summary(res: SurveyResult) -> dict:
    rows = res.rows
    by = lambda key, value: sum(1 for r in rows if r.get(key) == value)          # noqa: E731
    licences: dict[str, int] = {}
    search_licences: dict[str, int] = {}
    for r in rows:
        if r.get("_disponibilita") in ("open_access", "free_not_oa"):
            name = r.get("licenza_xml") or (f"non scritta nel testo (ricerca: {r.get('licenza_ricerca') or 'nessuna'})"
                                            if r.get("xml_http") == 200 else "testo non letto")
            licences[name] = licences.get(name, 0) + 1
        if r.get("esito") == "trovato":
            k = r.get("licenza_ricerca") or "nessuna"
            search_licences[k] = search_licences.get(k, 0) + 1
    xml_ok = [r for r in rows if r.get("xml_http") == 200 and "sezioni" in r]
    return {
        "riepilogo": {
            "cercati": len(rows), "trovati": by("esito", "trovato"), "non_trovati": by("esito", "non trovato"),
            "discordanti": by("esito", "discordante"), "ambigui": by("esito", "ambiguo"), "errori": len(res.errors),
            "testo_aperto": by("_disponibilita", "open_access"), "testo_non_aperto": by("_disponibilita", "free_not_oa"),
            "solo_metadati": by("_disponibilita", "metadata_only"),
            "con_pmcid": sum(1 for r in rows if r.get("pmcid")),
            "xml_letti": len(xml_ok), "xml_non_disponibili": sum(1 for r in rows if r.get("xml_http") == 404),
            "sezioni": sum(r["sezioni"] for r in xml_ok), "parole": sum(r["parole"] for r in xml_ok),
            "riferimenti": sum(r["riferimenti"] for r in xml_ok),
            "riferimenti_con_doi": sum(r["riferimenti_con_doi"] for r in xml_ok),
            "richieste_a_europepmc": res.requests},
        "licenze": dict(sorted(licences.items(), key=lambda kv: (-kv[1], kv[0]))),
        "licenze_nella_ricerca": dict(sorted(search_licences.items(), key=lambda kv: (-kv[1], kv[0]))),
    }


def write_csv(res: SurveyResult, home: Path, now: datetime | None = None) -> Path:
    now = now or datetime.now(timezone.utc)
    path = Path(home) / "rapporti" / f"europepmc_{now.strftime('%Y-%m-%d_%H%M%S')}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_FIELDS, delimiter=";", extrasaction="ignore")
        w.writeheader()
        w.writerows(res.rows)
    return path


def public(res: SurveyResult, path: Path) -> dict:
    out = {**summary(res), "file_csv": str(path),
           "con_testo": [{k: r.get(k) for k in ("doi", "pmcid", "disponibilita", "licenza_xml", "licenza_ricerca",
                                                 "sezioni", "riferimenti") if r.get(k) not in (None, "")}
                         for r in res.rows if r.get("_disponibilita") in ("open_access", "free_not_oa")],
           "da_guardare": [{k: r.get(k) for k in ("doi", "esito", "motivo", "errore") if r.get(k)}
                           for r in res.rows if r.get("esito") in ("discordante", "ambiguo") or r.get("errore")],
           "errori": res.errors[:20],
           "campi_mancanti_nelle_risposte": sorted(res.missing_fields),
           "scritto_nel_database": bool(res.written),
           "nota_europepmc": europepmc.ATTRIBUTION}
    if res.written:
        out["salvati"] = {"articoli": len(res.written), "testi": sum(1 for w in res.written if w["testo_salvato"]),
                          "sezioni": sum(w["sezioni"] for w in res.written),
                          "pmcid_scritti": sum(1 for w in res.written if w["pmcid_scritto"]),
                          "licenze_scritte": sum(1 for w in res.written if w["licenza_scritta"]),
                          "gia_cercati": res.skipped}
    if res.stopped:
        out["interrotto"] = res.stopped
    return out
