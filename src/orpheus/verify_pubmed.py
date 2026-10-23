"""Look up the confirmed articles in PubMed (T-009 parte 2b), deterministically.

For every article with access_level 'full_text', a DOI and at least one PDF linked (its DOI was confirmed
by Crossref, verify_doi.py):
  1. ESearch the DOI in PubMed (pubmed.py) -> 0, 1 or more PMIDs; the raw answer is kept (source_snapshot);
  2. with exactly one PMID, EFetch the record (in groups of up to 200 PMIDs) -> raw XML kept as well;
  3. apply the rules pubmed-v1 (pubmed.evaluate) and write one pubmed_check row (append-only) and an audit event;
  4. only for 'confirmed': article.pmid, article_mesh and article_publication_type (replaced by a newer
     confirmed check). Nothing else of the article is changed; PubMed's correction notices (retraction,
     erratum …) are recorded in the check's details and shown to the user, not applied.
A network failure is not an outcome: the article stays "da verificare" and is asked again on the next run.
"""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from . import audit, pubmed
from .db import transaction
from .ids import new_id
from .verify_doi import store_snapshot

OUTCOME_IT = {"confirmed": "trovato", "not_found": "non trovato", "mismatch": "discordante", "ambiguous": "ambiguo"}
SNAPSHOT_SUBDIR = "pubmed"
NOTICES_IT = {"RetractionIn": "ritrattato", "ExpressionOfConcernIn": "espressione di preoccupazione",
              "ErratumIn": "errata corrige", "UpdateIn": "aggiornato", "CorrectedandRepublishedIn": "corretto e ripubblicato",
              "RetractionOf": "è un avviso di ritrattazione"}


def candidates(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Articles to look up: complete text, a DOI, and at least one PDF linked (confirmed by verify-dois)."""
    return conn.execute(
        """SELECT a.* FROM article a
           WHERE a.access_level = 'full_text' AND a.doi IS NOT NULL
             AND EXISTS (SELECT 1 FROM document d WHERE d.article_id = a.id)
           ORDER BY a.doi""").fetchall()


def latest_checks(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    rows = conn.execute(
        """SELECT c.* FROM pubmed_check c
           WHERE c.rowid = (SELECT c2.rowid FROM pubmed_check c2 WHERE c2.article_id = c.article_id
                            ORDER BY c2.checked_at DESC, c2.rowid DESC LIMIT 1)""").fetchall()
    return {r["article_id"]: r for r in rows}


def _store(conn, resp, home, archive_dir) -> str:
    return store_snapshot(conn, resp, home=home, archive_dir=archive_dir, source_id=pubmed.SOURCE_ID,
                          subdir=SNAPSHOT_SUBDIR, licence=pubmed.LICENCE)


def record(conn: sqlite3.Connection, article: sqlite3.Row, verdict: dict, *, search_resp: pubmed.Response,
           search: dict, fetch_resp: pubmed.Response | None, rec: dict | None, home: Path, archive_dir: Path,
           actor: str) -> dict:
    """One transaction: the raw answers (if new), the pubmed_check row, the PMID / MeSH / publication types
    (only if confirmed), and the audit event — all or nothing."""
    check_id = new_id("pubmed_check")
    outcome = verdict["outcome"]
    details = {"pmid_trovati": search["pmids"], "conteggio": search["count"]}
    if rec is not None:
        details |= {"doi_pubmed": rec["dois"], "titolo": rec["title"], "rivista": rec["journal_iso"] or rec["journal"],
                    "anno": rec["year"], "avvisi": rec["corrections"]}
    with transaction(conn):
        search_snap = _store(conn, search_resp, home, archive_dir)
        fetch_snap = _store(conn, fetch_resp, home, archive_dir) if fetch_resp is not None and rec is not None else None
        conn.execute(
            """INSERT INTO pubmed_check (id, article_id, doi, pmid, outcome, reasons, details, rules_version, source_id,
                   search_snapshot_id, fetch_snapshot_id, checked_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (check_id, article["id"], article["doi"], verdict["pmid"], outcome,
             json.dumps(verdict["reasons"], ensure_ascii=False), json.dumps(details, ensure_ascii=False),
             pubmed.RULES_VERSION, pubmed.SOURCE_ID, search_snap, fetch_snap, audit.utc_now()))
        old_pmid, n_mesh = article["pmid"], 0
        if outcome == "confirmed":
            conn.execute("UPDATE article SET pmid = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id = ?",
                         (verdict["pmid"], article["id"]))
            conn.execute("DELETE FROM article_mesh WHERE article_id = ?", (article["id"],))
            conn.execute("DELETE FROM article_publication_type WHERE article_id = ?", (article["id"],))
            for m in rec["mesh"]:
                conn.execute(
                    """INSERT INTO article_mesh (article_id, descriptor_ui, descriptor_name, major_topic, qualifiers,
                           pubmed_check_id, source_snapshot_id) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (article["id"], m["ui"], m["name"], int(m["major"]), json.dumps(m["qualifiers"], ensure_ascii=False),
                     check_id, fetch_snap))
                n_mesh += 1
            for p in rec["publication_types"]:
                conn.execute(
                    """INSERT INTO article_publication_type (article_id, type_ui, type_name, pubmed_check_id,
                           source_snapshot_id) VALUES (?, ?, ?, ?, ?)""",
                    (article["id"], p["ui"], p["name"], check_id, fetch_snap))
        audit.record(conn, actor=actor, action="verify_pubmed", entity_type="article", entity_id=article["id"],
                     payload={"doi": article["doi"], "outcome": outcome, "pmid": verdict["pmid"],
                              "reasons": verdict["reasons"], "pubmed_check_id": check_id,
                              "search_snapshot_id": search_snap, "fetch_snapshot_id": fetch_snap,
                              "pmid_before": old_pmid, "mesh": n_mesh, "rules": pubmed.RULES_VERSION},
                     in_transaction=True)
    return {"pubmed_check_id": check_id, "mesh": n_mesh}


@dataclass
class RunResult:
    processed: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    skipped: int = 0
    todo: int = 0
    stopped: str | None = None
    requests: int = 0


def select(conn: sqlite3.Connection, mode: str = "nuovi") -> tuple[list[sqlite3.Row], int]:
    """(articles to ask PubMed about, articles skipped). mode 'nuovi': never checked or DOI changed;
    'ricontrolla': also those not found, discordant or ambiguous."""
    latest = latest_checks(conn)
    todo, skipped = [], 0
    for art in candidates(conn):
        last = latest.get(art["id"])
        same = last is not None and last["doi"] == art["doi"]
        if same and (last["outcome"] == "confirmed" or mode == "nuovi"):
            skipped += 1
            continue
        todo.append(art)
    return todo, skipped


def estimate(n_articles: int) -> dict:
    """Requests and minutes for a run at one request per second (ESearch each + EFetch per 200)."""
    requests = n_articles + math.ceil(n_articles / pubmed.MAX_IDS_PER_FETCH)
    return {"richieste_stimate": requests, "minuti_stimati": math.ceil(requests / 60)}


def run(conn: sqlite3.Connection, *, home: Path, archive_dir: Path, client: pubmed.PubMedClient, mode: str = "nuovi",
        limit: int | None = None, actor: str = "cli", max_consecutive_failures: int = 3) -> RunResult:
    out = RunResult()
    todo, out.skipped = select(conn, mode)
    if limit is not None:
        out.skipped += max(0, len(todo) - limit)
        todo = todo[:limit]
    out.todo = len(todo)

    def _done(art, verdict, rec):
        item = {"doi": art["doi"], "esito": OUTCOME_IT[verdict["outcome"]], "motivo": "; ".join(verdict["reasons"])}
        if verdict["pmid"]:
            item["pmid"] = verdict["pmid"]
        if rec and rec["corrections"] and verdict["outcome"] == "confirmed":
            item["avvisi"] = [f"{NOTICES_IT.get(c['type'], c['type'])}" + (f" (PMID {c['pmid']})" if c["pmid"] else "")
                              for c in rec["corrections"]]
        return item

    # 1. ESearch, one DOI at a time
    single: list[tuple[sqlite3.Row, pubmed.Response, dict]] = []
    failures, blocked = 0, False
    for art in todo:
        try:
            resp = client.esearch_doi(art["doi"])
            try:
                search = pubmed.parse_esearch(resp.body)
            except (ValueError, UnicodeDecodeError) as exc:
                raise pubmed.PubMedError(f"risposta di ESearch non leggibile: {exc}") from exc
        except pubmed.PubMedBlocked as exc:
            out.stopped, blocked = str(exc), True
            out.errors.append({"doi": art["doi"], "errore": str(exc)})
            break
        except pubmed.PubMedError as exc:
            out.errors.append({"doi": art["doi"], "errore": str(exc)})
            failures += 1
            if failures >= max_consecutive_failures:
                out.stopped = (f"{failures} errori di fila: verifica interrotta. Controlla la connessione e rilancia "
                               "(gli articoli già verificati non vengono richiesti di nuovo).")
                break
            continue
        failures = 0
        if len(search["pmids"]) == 1:
            single.append((art, resp, search))
            continue
        verdict = pubmed.evaluate(art["doi"], search["pmids"], None)
        record(conn, art, verdict, search_resp=resp, search=search, fetch_resp=None, rec=None, home=home,
               archive_dir=archive_dir, actor=actor)
        out.processed.append(_done(art, verdict, None))

    # 2. EFetch the single PMIDs in groups of up to 200 (not after a 403)
    if not blocked:
        for i in range(0, len(single), pubmed.MAX_IDS_PER_FETCH):
            group = single[i:i + pubmed.MAX_IDS_PER_FETCH]
            try:
                fetch = client.efetch([s[2]["pmids"][0] for s in group])
                try:
                    records = pubmed.parse_pubmed_xml(fetch.body)
                except (ValueError, SyntaxError) as exc:        # ET.ParseError is a SyntaxError
                    raise pubmed.PubMedError(f"risposta di EFetch non leggibile: {exc}") from exc
            except pubmed.PubMedError as exc:
                out.errors += [{"doi": art["doi"], "errore": str(exc)} for art, _, _ in group]
                if isinstance(exc, pubmed.PubMedBlocked):
                    out.stopped = str(exc)
                    break
                continue
            for art, resp, search in group:
                pmid = search["pmids"][0]
                rec = records.get(pmid)
                if rec is None:
                    out.errors.append({"doi": art["doi"], "errore": f"il record del PMID {pmid} non è arrivato"})
                    continue
                verdict = pubmed.evaluate(art["doi"], [pmid], rec)
                other = conn.execute("SELECT doi FROM article WHERE pmid = ? AND id <> ?", (pmid, art["id"])).fetchone()
                if verdict["outcome"] == "confirmed" and other is not None:     # article.pmid is unique
                    verdict = {"outcome": "mismatch", "pmid": pmid,
                               "reasons": [f"il PMID {pmid} è già di un altro articolo (DOI {other['doi']})"]}
                record(conn, art, verdict, search_resp=resp, search=search, fetch_resp=fetch, rec=rec, home=home,
                       archive_dir=archive_dir, actor=actor)
                out.processed.append(_done(art, verdict, rec))
    out.requests = client.requests_made
    if out.errors or out.stopped:
        audit.record(conn, actor=actor, action="verify_pubmed_run",
                     payload={"mode": mode, "processed": len(out.processed), "errors": out.errors,
                              "stopped": out.stopped, "requests": out.requests, "rules": pubmed.RULES_VERSION})
    return out


def overall(conn: sqlite3.Connection) -> dict:
    """Current state over all candidate articles (latest check each)."""
    latest = latest_checks(conn)
    counts = {v: 0 for v in OUTCOME_IT.values()}
    cands = candidates(conn)
    checked = 0
    for art in cands:
        last = latest.get(art["id"])
        if last is not None and last["doi"] == art["doi"]:
            counts[OUTCOME_IT[last["outcome"]]] += 1
            checked += 1
    return {**{k.replace(" ", "_"): v for k, v in counts.items()}, "da_verificare": len(cands) - checked,
            "articoli_candidati": len(cands),
            "articoli_con_pmid": conn.execute("SELECT count(*) FROM article WHERE pmid IS NOT NULL").fetchone()[0],
            "voci_mesh": conn.execute("SELECT count(*) FROM article_mesh").fetchone()[0]}
