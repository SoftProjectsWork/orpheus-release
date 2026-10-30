"""Fase 5 — the dashboard (cruscotto): a page in the browser served by the PC itself (rules dashboard-v1).

User's decisions of 2026-10-15 19:58: "una dashboard interattiva e smart, dove si possa cliccare, filtrare" (the system is
Claude's choice) and "Anche lanciare lavora".
Choice: Starlette + Uvicorn (free, pure Python) listening only on 127.0.0.1; one page (HTML, CSS, JavaScript inside the
package: nothing is loaded from the internet, the data never leave the PC).

Language (user's decision of 2026-10-15 23:16): Italian for the interface (titles, information, instructions), English for
everything about the articles and their content: only the English claims are shown, questions are asked in English.
Manuscripts (T-037, Fase 6): the section Manoscritti checks a .docx of the manuscripts folder against the journal cards
(compliance-v1); the text is read on this PC only and nothing is written (R10).
Reading: every API answer is built with read-only queries; every widget carries its source (tables, rules) and its date
(the newest row it reads). Writing: only POST /api/azioni/avvia (and its old name /api/lavora/avvia), which starts one
of the double-click commands exactly as its .cmd does (PowerShell orpheus_comandi.ps1 -Comando <azione>: backup when it
writes, steps, registro sent to GitHub) and needs the token of the page (another web site open in the browser cannot
start it) and a request addressed to 127.0.0.1 / localhost. One action at a time.
Actions (2026-10-17, user: "unisci le funzioni create in base alla logica di utilizzo (ordine di utilizzo) e crea dei
nuovi bottoni nella dashboard, così come lavora"): the functions grouped in the order of use — articles, journals, the
work of Claude, a manuscript for a journal, maintenance — plus lavora (everything).
"""

from __future__ import annotations

import json
import os
import re
import secrets
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import (__version__, audit, card_store, claims, config, content, db, engine, journal_cards, journal_profile, layout,
               lineage, policy_changes, policy_versions, report, semantic, stats_terms, verify_doi)
from .localstate import LocalState

RULES_VERSION = "dashboard-v1"
STATIC_DIR = Path(__file__).with_name("dashboard_static")
PORTS = range(8765, 8786)
PLAN_CACHE_SECONDS = 60
LANG_NAMES = {"it": "italiano", "en": "inglese"}
REASONS = {"manual": "copia di sicurezza (backup)", "auto": "automatica dopo modifiche", "before_migrate": "prima della migrazione"}


def utc_now() -> str:
    return audit.utc_now()


def _one(conn, sql: str, *args):
    r = conn.execute(sql, args).fetchone()
    return r[0] if r else None


def _src(fonte: str, data=None) -> dict:
    return {"fonte": fonte, "data": data}


# ---------------------------------------------------------------- context

@dataclass
class Settings:
    home: Path
    project: object
    local_root: Path
    db_path: Path
    commands_script: Path | None = None        # orpheus_comandi.ps1 of this PC (for "Avvia lavora")
    powershell: str = "powershell"

    @classmethod
    def from_config(cls, commands_script: Path | None = None, powershell: str | None = None) -> "Settings":
        project = config.load_project()
        home = config.home()
        if commands_script is None:
            root = Path(os.environ.get("ORPHEUS_TOOLS_ROOT") or r"C:\ORPHEUS")
            candidate = root / "orpheus_comandi.ps1"
            commands_script = candidate if candidate.exists() else None
        return cls(home, project, config.local_dir(), home / project.storage.db_path, commands_script,
                   powershell or os.environ.get("ORPHEUS_PWSH") or "powershell")

    def connect(self) -> sqlite3.Connection:
        if not self.db_path.exists():
            raise FileNotFoundError(f"database non trovato in {self.db_path}: il disco esterno è collegato?")
        return db.connect(self.db_path)

    def journal_list(self) -> list[dict]:
        rows, _problems = layout.read_journal_list(self.home / self.project.storage.journals_csv)
        return rows


def _sigla_by_journal(conn, journal_list: list[dict]) -> dict[str, str]:
    exact = {r["sigla"].lower(): r["sigla"] for r in journal_list if r.get("sigla")}
    return {jid: exact.get(sg, sg.upper()) for sg, jid in policy_versions.journal_by_sigla(conn, journal_list).items()}


# ---------------------------------------------------------------- overview

def overview(s: Settings, conn) -> dict:
    jl = s.journal_list()
    r = report.build(conn, s.home, journal_list=jl)
    v, c, cards, cl = r["verifiche"], r["citazioni"], r["schede"], r["affermazioni"]
    cov = r["copertura"]["righe"]
    complete = sum(x["articoli_completi"] for x in cov)
    confirmed = v["doi"]["esiti"].get("confermato", 0)
    doi_total = sum(v["doi"]["esiti"].values())
    vectors = semantic.vector_status(conn)
    kpis = [
        {"id": "articoli", "etichetta": "Articoli", "valore": r["articoli"], "nota": f"{complete} completi",
         "vai": "articoli", **_src("article; classe del PDF (content.py)", r["documenti"]["data"])},
        {"id": "doi", "etichetta": "DOI confermati da Crossref", "valore": confirmed, "totale": doi_total,
         "vai": "verifiche", **_src(v["doi"]["fonte"], v["doi"]["data"])},
        {"id": "pubmed", "etichetta": "In PubMed", "valore": v["pubmed"]["esiti"].get("confermato", 0),
         "nota": f"MeSH per {v['pubmed']['con_mesh']} articoli", "vai": "verifiche",
         **_src(v["pubmed"]["fonte"], v["pubmed"]["data"])},
        {"id": "testi_aperti", "etichetta": "Testi aperti salvati", "valore": v["europepmc"]["testi_salvati"],
         "nota": f"{v['europepmc']['sezioni']} sezioni", "vai": "verifiche",
         **_src(v["europepmc"]["fonte"], v["europepmc"]["data"])},
        {"id": "citazioni", "etichetta": "Riferimenti verificati", "valore": c["stato"].get("verificato", 0),
         "totale": c["riferimenti"], "vai": "verifiche", **_src(c["fonte"], c["data"])},
        {"id": "schede", "etichetta": "Schede degli articoli", "valore": cards["schede_accettate"],
         "nota": f"{cards['valori']} valori con pagina e frase", "vai": "articoli", **_src(cards["fonte"], cards["data"])},
        {"id": "affermazioni", "etichetta": "Affermazioni (in inglese)", "valore": cl.get("per_lingua", {}).get("en", 0),
         "nota": "ognuna con pagina e frase del PDF", "vai": "affermazioni", **_src(cl["fonte"], cl["data"])},
        {"id": "pezzi", "etichetta": "Pezzi di testo con vettore", "valore": vectors["con_vettore"],
         "totale": vectors["segmenti"], "vai": "ricerca",
         **_src("segment, segment_vector", _one(conn, "SELECT max(created_at) FROM segment_vector"))},
        {"id": "riviste", "etichetta": "Riviste", "valore": _one(conn, "SELECT count(*) FROM journal"),
         "nota": f"{r['metriche']['metriche']} metriche", "vai": "riviste", **_src(r["metriche"]["fonte"], r["metriche"]["data"])},
        {"id": "costo", "etichetta": "Costo", "valore": "0 €", "nota": "solo servizi gratuiti", "vai": "sistema",
         **_src("config/orpheus.yaml (paid_services_allowed: false), config/sources.yaml", None)},
    ]
    by_year = [dict(x) for x in conn.execute(
        """SELECT a.publication_year AS anno, a.journal_id, count(*) AS n FROM article a
           WHERE a.publication_year IS NOT NULL GROUP BY 1, 2 ORDER BY 1""")]
    sigla = _sigla_by_journal(conn, jl)
    for x in by_year:
        x["rivista"] = sigla.get(x.pop("journal_id"), "altre")
    alerts = []
    for a in r["avvisi_linee_guida"]["avvisi"]:
        alerts.append({"tipo": "linee guida", "testo": policy_changes.summary_line(a), "vai": "riviste"})
    last = _last_runs(conn, 30)
    for run in last:
        if run["stato"] == "failed" or run["esito"] == "fatto con errori":
            alerts.append({"tipo": "lavora", "testo": f"{run['titolo']}: {run['esito']}"
                           + (f" — {run['errore'][:160]}" if run.get("errore") else ""), "vai": "lavora"})
            break
    others = engine.work_for_others(engine.Env(conn, s.home, s.project, s.local_root))
    for x in others["per_te"]:
        alerts.append({"tipo": "per te", "testo": f"{x['lavoro']}: {x['quanti']}", "vai": x.get("vai", "affermazioni")})
    with_claims = _one(conn, "SELECT count(DISTINCT article_id) FROM scientific_claim WHERE status = 'current' "
                             "AND language = 'en'")
    chain = [
        {"id": "pdf", "etichetta": "PDF letti", "valore": r["documenti"]["pdf"], "vai": "articoli",
         **_src(r["documenti"]["fonte"], r["documenti"]["data"])},
        {"id": "doi", "etichetta": "DOI confermati", "valore": confirmed, "vai": "verifiche",
         **_src(v["doi"]["fonte"], v["doi"]["data"])},
        {"id": "completi", "etichetta": "Articoli completi", "valore": complete, "vai": "articoli",
         **_src("document, document_page (content.py)", r["documenti"]["data"])},
        {"id": "pubmed", "etichetta": "In PubMed", "valore": v["pubmed"]["esiti"].get("confermato", 0), "vai": "verifiche",
         **_src(v["pubmed"]["fonte"], v["pubmed"]["data"])},
        {"id": "riviste", "etichetta": "Riviste", "valore": _one(conn, "SELECT count(*) FROM journal"),
         "nota": f"{r['metriche']['metriche']} metriche", "vai": "riviste", **_src(r["metriche"]["fonte"], r["metriche"]["data"])},
        {"id": "schede", "etichetta": "Con scheda", "valore": cards["schede_accettate"], "vai": "articoli",
         **_src(cards["fonte"], cards["data"])},
        {"id": "affermazioni", "etichetta": "Consenso", "valore": with_claims, "vai": "affermazioni",
         "nota": f"{cl.get('per_lingua', {}).get('en', 0)} affermazioni", **_src(cl["fonte"], cl["data"])},
    ]
    return {"regole": RULES_VERSION, "generato": utc_now(), "kpi": kpis, "catena": chain,
            "copertura": {**_src(r["copertura"]["fonte"], r["documenti"]["data"]), "righe": cov},
            "per_anno": {**_src("article.publication_year, article.journal_id", r["documenti"]["data"]), "righe": by_year},
            "avvisi": {**_src("journal_policy_version, journal_card, workflow_run, scientific_claim", utc_now()),
                       "righe": alerts},
            "lavori": {**_src("article_card, scientific_claim, journal_policy_version, cartelle da_inviare", utc_now()),
                       **others},
            "ultimo_lavora": last[0] if last else None}


# ---------------------------------------------------------------- articles

def articles(s: Settings, conn) -> dict:
    jl = s.journal_list()
    sigla = _sigla_by_journal(conn, jl)
    classes = content.classify_all(conn)
    latest = verify_doi.latest_checks(conn)
    docs: dict[str, list] = {}
    for d in conn.execute("SELECT id, article_id, page_count, journal_hint FROM document WHERE kind = 'article' "
                          "AND article_id IS NOT NULL"):
        docs.setdefault(d["article_id"], []).append(d)
    pubmed = {r["article_id"]: r for r in conn.execute(
        """SELECT p.article_id, p.outcome, p.pmid FROM pubmed_check p JOIN (SELECT article_id, max(checked_at) AS t
           FROM pubmed_check GROUP BY 1) l ON l.article_id = p.article_id AND l.t = p.checked_at""")}
    ft = {r["article_id"]: r for r in conn.execute(
        """SELECT f.article_id, f.availability, f.licence, f.text_stored FROM fulltext_check f JOIN (SELECT article_id,
           max(checked_at) AS t FROM fulltext_check GROUP BY 1) l ON l.article_id = f.article_id AND l.t = f.checked_at""")}
    saved = {r[0] for r in conn.execute("SELECT DISTINCT article_id FROM article_section")}
    refs = dict(conn.execute("SELECT citing_article_id, count(*) FROM citation GROUP BY 1").fetchall())
    refs_ok = dict(conn.execute("SELECT citing_article_id, count(*) FROM citation WHERE verification_status = 'verified' "
                                "GROUP BY 1").fetchall())
    cited = dict(conn.execute("SELECT cited_article_id, count(*) FROM citation WHERE cited_article_id IS NOT NULL "
                              "GROUP BY 1").fetchall())
    card = {r[0] for r in conn.execute("SELECT article_id FROM article_card WHERE status = 'current'")}
    nclaims: dict[str, dict] = {}
    for r in conn.execute("""SELECT article_id, coalesce(language, 'it') AS l, count(*) AS n FROM scientific_claim
                             WHERE status = 'current' AND article_id IS NOT NULL GROUP BY 1, 2"""):
        nclaims.setdefault(r["article_id"], {})[r["l"]] = r["n"]
    level = dict(conn.execute("""SELECT c.article_id, max(e.appraisal_level) FROM scientific_claim c
                                 JOIN evidence_record e ON e.claim_id = c.id WHERE c.status = 'current' GROUP BY 1""").fetchall())
    segs = dict(conn.execute("SELECT article_id, count(*) FROM segment WHERE article_id IS NOT NULL GROUP BY 1").fetchall())
    authors: dict[str, list] = {}
    for r in conn.execute("""SELECT aa.article_id, au.display_name FROM article_author aa JOIN author au ON au.id = aa.author_id
                             ORDER BY aa.article_id, aa.position"""):
        authors.setdefault(r["article_id"], []).append(r["display_name"])
    rows = []
    for a in conn.execute("SELECT * FROM article ORDER BY publication_year DESC, title"):
        ds = docs.get(a["id"], [])
        complete = any((classes.get(d["id"]) or {}).get("classe") == content.COMPLETE for d in ds)
        confirmed = any((latest.get(d["id"]) is not None and latest[d["id"]]["outcome"] == "confirmed") for d in ds)
        hint = next((d["journal_hint"] for d in ds if d["journal_hint"]), None)
        p, f = pubmed.get(a["id"]), ft.get(a["id"])
        au = authors.get(a["id"], [])
        rows.append({
            "id": a["id"], "titolo": a["title"], "doi": a["doi"], "anno": a["publication_year"],
            "rivista": sigla.get(a["journal_id"]) or (hint.upper() if hint else None),
            "autori": (au[0] + (" et al." if len(au) > 1 else "")) if au else None, "n_autori": len(au),
            "completo": complete, "doi_confermato": confirmed,
            "pubmed": (p["outcome"] == "confirmed") if p else None, "pmid": a["pmid"] or (p["pmid"] if p else None),
            "testo_aperto": a["id"] in saved, "accesso_europepmc": f["availability"] if f else None,
            "licenza": a["licence"] or (f["licence"] if f else None),
            "riferimenti": refs.get(a["id"], 0), "riferimenti_verificati": refs_ok.get(a["id"], 0),
            "citato_da": cited.get(a["id"], 0), "scheda": a["id"] in card,
            "affermazioni": nclaims.get(a["id"], {}).get("en", 0),
            "livello": level.get(a["id"]), "pezzi": segs.get(a["id"], 0), "pdf": len(ds)})
    return {**_src("article, document, doi_check, pubmed_check, fulltext_check, citation, article_card, scientific_claim, "
                   "evidence_record, segment, article_author", _one(conn, "SELECT max(updated_at) FROM article")),
            "righe": rows}


def article_detail(s: Settings, conn, article_id: str) -> dict | None:
    a = conn.execute("SELECT * FROM article WHERE id = ?", (article_id,)).fetchone()
    if a is None:
        return None
    sigla = _sigla_by_journal(conn, s.journal_list())
    j = conn.execute("SELECT title, publisher FROM journal WHERE id = ?", (a["journal_id"],)).fetchone() if a["journal_id"] else None
    classes = content.classify_all(conn)
    latest = verify_doi.latest_checks(conn)
    docs = []
    for d in conn.execute("""SELECT id, original_path, page_count, text_status, ingested_at FROM document
                             WHERE article_id = ? ORDER BY page_count DESC""", (article_id,)):
        chk = latest.get(d["id"])
        docs.append({"file": (d["original_path"] or "").replace("\\", "/").rsplit("/", 1)[-1], "pagine": d["page_count"],
                     "classe": (classes.get(d["id"]) or {}).get("classe"), "letto": d["ingested_at"],
                     "doi": {"esito": chk["outcome"], "regole": chk["rules_version"], "data": chk["checked_at"]} if chk else None})
    authors = [dict(r) for r in conn.execute(
        """SELECT au.display_name AS nome, au.orcid, aa.position AS posizione FROM article_author aa
           JOIN author au ON au.id = aa.author_id WHERE aa.article_id = ? ORDER BY aa.position""", (article_id,))]
    pm = conn.execute("SELECT outcome, pmid, checked_at FROM pubmed_check WHERE article_id = ? ORDER BY checked_at DESC LIMIT 1",
                      (article_id,)).fetchone()
    mesh = [r[0] for r in conn.execute("SELECT descriptor_name FROM article_mesh WHERE article_id = ? ORDER BY 1",
                                       (article_id,))] if _has_column(conn, "article_mesh", "descriptor_name") else []
    ft = conn.execute("""SELECT outcome, availability, licence, pmcid, checked_at FROM fulltext_check WHERE article_id = ?
                         ORDER BY checked_at DESC LIMIT 1""", (article_id,)).fetchone()
    card = conn.execute("SELECT id, created_at, model_ref FROM article_card WHERE article_id = ? AND status = 'current' "
                        "ORDER BY created_at DESC LIMIT 1", (article_id,)).fetchone()
    values = [dict(r) for r in conn.execute(
        """SELECT field AS campo, name AS nome, value AS valore, unit AS unita, page_no AS pagina, quote AS citazione,
                  origin AS origine, human_verdict AS controllo FROM card_value WHERE card_id = ? ORDER BY ord""",
        (card["id"],))] if card else []
    claim_rows = []
    for c in conn.execute("""SELECT id, statement, claim_type, language AS lingua, ord, human_verdict, created_at
                             FROM scientific_claim WHERE article_id = ? AND status = 'current' AND language = 'en'
                             ORDER BY ord""", (article_id,)):
        ev = [dict(r) for r in conn.execute(
            """SELECT page_no AS pagina, quote AS citazione, support AS sostegno, appraisal_level AS livello,
                      appraisal_reasons AS ragioni FROM evidence_record WHERE claim_id = ? ORDER BY rowid""", (c["id"],))]
        for e in ev:
            e["ragioni"] = json.loads(e["ragioni"] or "{}").get("passi", [])
        claim_rows.append({"id": c["id"], "testo": c["statement"], "tipo": c["claim_type"], "lingua": c["lingua"],
                           "controllo": c["human_verdict"], "data": c["created_at"], "evidenze": ev})
    refs = dict(conn.execute("SELECT verification_status, count(*) FROM citation WHERE citing_article_id = ? GROUP BY 1",
                             (article_id,)).fetchall())
    cited_by = [dict(r) for r in conn.execute(
        """SELECT a.id, a.title AS titolo, a.publication_year AS anno FROM citation c JOIN article a ON a.id = c.citing_article_id
           WHERE c.cited_article_id = ? ORDER BY a.publication_year DESC""", (article_id,))]
    cites = [dict(r) for r in conn.execute(
        """SELECT a.id, a.title AS titolo, a.publication_year AS anno FROM citation c JOIN article a ON a.id = c.cited_article_id
           WHERE c.citing_article_id = ? ORDER BY a.publication_year DESC""", (article_id,))]
    return {"regole": RULES_VERSION,
            "articolo": {"id": a["id"], "titolo": a["title"], "doi": a["doi"], "anno": a["publication_year"],
                         "data": a["publication_date"], "tipo": a["article_type"], "accesso": a["access_level"],
                         "licenza": a["licence"], "pmid": a["pmid"], "pmcid": a["pmcid"],
                         "rivista": sigla.get(a["journal_id"]), "nome_rivista": j["title"] if j else None,
                         "editore": j["publisher"] if j else None, "aggiornato": a["updated_at"]},
            "autori": authors, "documenti": docs,
            "pubmed": {"esito": pm["outcome"], "pmid": pm["pmid"], "data": pm["checked_at"], "mesh": mesh} if pm else None,
            "europepmc": dict(ft) if ft else None,
            "scheda": {"data": card["created_at"], "modello": card["model_ref"], "valori": values} if card else None,
            "affermazioni": claim_rows,
            "riferimenti": {"per_stato": refs, "cita_articoli_di_orpheus": cites, "citato_da": cited_by},
            "pezzi": _one(conn, "SELECT count(*) FROM segment WHERE article_id = ?", article_id),
            "revisioni": _article_reviews(conn, article_id),
            **_src("article, document, doi_check, pubmed_check, article_mesh, fulltext_check, article_card, card_value, "
                   "scientific_claim, evidence_record, citation, segment", a["updated_at"])}


def _has_column(conn, table: str, column: str) -> bool:
    return any(r[1] == column for r in conn.execute(f"PRAGMA table_info({table})"))


# ---------------------------------------------------------------- claims

def claims_list(s: Settings, conn) -> dict:
    sigla = _sigla_by_journal(conn, s.journal_list())
    rows = []
    for c in conn.execute("""SELECT c.id, c.statement, c.claim_type, c.language AS lingua, c.human_verdict,
                                    c.article_id, a.title, a.publication_year, a.journal_id, a.doi
                             FROM scientific_claim c JOIN article a ON a.id = c.article_id
                             WHERE c.status = 'current' AND c.language = 'en'
                             ORDER BY a.publication_year DESC, a.title, c.ord"""):
        ev = [dict(r) for r in conn.execute(
            """SELECT page_no AS pagina, quote AS citazione, support AS sostegno, appraisal_level AS livello
               FROM evidence_record WHERE claim_id = ? ORDER BY rowid""", (c["id"],))]
        rows.append({"id": c["id"], "testo": c["statement"], "tipo": c["claim_type"], "lingua": c["lingua"],
                     "controllo": {"right": "giusta", "wrong": "sbagliata"}.get(c["human_verdict"], "da controllare"),
                     "article_id": c["article_id"], "articolo": c["title"], "anno": c["publication_year"],
                     "doi": c["doi"], "rivista": sigla.get(c["journal_id"]),
                     "livello": ev[0]["livello"] if ev else None, "evidenze": ev})
    return {**_src("scientific_claim (solo in inglese), evidence_record (claims-v1, appraisal-v1)",
                   _one(conn, "SELECT max(created_at) FROM scientific_claim")), "righe": rows}


# ---------------------------------------------------------------- journals

def journal_profiles(s: Settings, conn) -> dict:
    """T-057: profiles of the journals from their analysed articles (stats-terms), with the sigla of the list."""
    sigla = _sigla_by_journal(conn, s.journal_list())
    out = journal_profile.build(conn)
    for r in out["riviste"]:
        r["sigla"] = sigla.get(r["journal_id"])
    status = stats_terms.status(conn)
    last = _one(conn, "SELECT max(created_at) FROM text_structure WHERE rules = ?", stats_terms.RULES_VERSION)
    return {**out, "stato": status, "precisione": stats_terms.checked_precision(conn),
            **_src(f"PDF degli articoli completi, regole {stats_terms.RULES_VERSION} (comando metodi)", last)}


def journal_profile_sentences(s: Settings, conn, sigla: str, term: str) -> dict:
    jid = {v.lower(): k for k, v in _sigla_by_journal(conn, s.journal_list()).items() if v}.get((sigla or "").lower())
    if jid is None or term not in stats_terms.TERMS_BY_KEY:
        return {"frasi": [], "errore": "rivista o voce non trovata"}
    return {"rivista": sigla, "voce": term, "nome": stats_terms.TERMS_BY_KEY[term].label,
            "frasi": journal_profile.examples(conn, jid, term, limit=30)}


def journals(s: Settings, conn) -> dict:
    jl = s.journal_list()
    r = report.build(conn, s.home, journal_list=jl)
    sigla = _sigla_by_journal(conn, jl)
    history = [dict(x) for x in conn.execute(
        """SELECT journal_id, metric, value, reference_year AS anno, provider FROM journal_metric
           WHERE value IS NOT NULL ORDER BY reference_year""")]
    for h in history:
        h["rivista"] = sigla.get(h.pop("journal_id"))
    counts = {x["rivista"]: x for x in r["copertura"]["righe"]}
    rows = []
    for j in conn.execute("SELECT id, title, publisher, homepage_url FROM journal ORDER BY title"):
        sg = sigla.get(j["id"])
        met = next((m for m in r["metriche"]["righe"] if m.get("rivista") == sg or m.get("titolo") == j["title"]), None)
        rows.append({"id": j["id"], "sigla": sg, "titolo": j["title"], "editore": j["publisher"], "sito": j["homepage_url"],
                     "metriche": met, "copertura": counts.get(sg),
                     "linee_guida": [x for x in r["linee_guida"]["righe"] if x.get("rivista") in (sg, j["title"])]})
    return {"riviste": rows,
            "metriche": {**_src(r["metriche"]["fonte"], r["metriche"]["data"]), "storia": history},
            "confronto": {**_src(r["confronto"]["fonte"], r["confronto"]["data"]), "righe": r["confronto"]["righe"]},
            "linee_guida": {**_src(r["linee_guida"]["fonte"], r["linee_guida"]["data"])},
            "avvisi": {**_src(r["avvisi_linee_guida"]["fonte"], r["avvisi_linee_guida"]["data"]),
                       "righe": [policy_changes.summary_line(a) for a in r["avvisi_linee_guida"]["avvisi"]]},
            "verifiche": r["verifiche"], "citazioni": {k: v for k, v in r["citazioni"].items()}}


# ---------------------------------------------------------------- search

class Searcher:
    """The model is loaded once, at the first search by meaning."""
    def __init__(self, local_root: Path):
        self.local_root = local_root
        self._embedder = None
        self._index = None
        self._lock = threading.Lock()

    def _load(self, conn) -> None:
        with self._lock:
            if self._embedder is None:
                try:
                    self._embedder = semantic.OnnxEmbedder(semantic.model_dir(self.local_root))
                except semantic.ModelMissing:
                    self._embedder = False
            if self._embedder and self._index is None:
                self._index = semantic.VectorIndex.load(conn)

    def matcher(self, conn):
        """T-040 parte 2: the same model and vectors for the closeness of a manuscript to the journals."""
        from . import journal_match
        self._load(conn)
        if self._embedder and self._index is not None and self._index.ids:
            return journal_match.Matcher(self._embedder, self._index)
        return journal_match.Matcher()

    def search(self, conn, text: str, mode: str = "ibrida", limit: int = 15) -> dict:
        note = None
        embedder = index = None
        if mode in ("ibrida", "significato"):
            self._load(conn)
            embedder, index = (self._embedder or None), self._index
            if embedder is None or index is None or not index.ids:
                note = "modello o vettori non disponibili su questo PC: ricerca solo per parole (lavora.cmd lo scarica)"
                mode = "parole"
        ranked = semantic.ranked_segments(conn, text, mode, embedder=embedder, index=index)
        found = semantic.by_article(conn, ranked, limit=limit)
        ids = [f["article_id"] for f in found if f["article_id"]]
        claim_rows: dict[str, list] = {}
        if ids:
            marks = ",".join("?" for _ in ids)
            for c in conn.execute(f"""SELECT article_id, statement, language AS lingua, claim_type
                                      FROM scientific_claim WHERE status = 'current' AND language = 'en'
                                      AND article_id IN ({marks}) ORDER BY ord""", ids):
                claim_rows.setdefault(c["article_id"], []).append({"testo": c["statement"], "lingua": c["lingua"],
                                                                   "tipo": c["claim_type"]})
        for f in found:
            f["affermazioni"] = claim_rows.get(f["article_id"], [])
        return {"domanda": text, "modo": mode, "nota": note, "regole": semantic.RULES_VERSION, "risultati": found,
                **_src("segment, segment_fts, segment_vector, scientific_claim", utc_now())}


# ---------------------------------------------------------------- actions (buttons): the functions in the order of use
# key = the -Comando of orpheus_comandi.ps1 (and the name of the .cmd). "gruppo": the steps of lavora it runs.
ACTIONS: dict[str, dict] = {
    "aggiorna_articoli": {"titolo": "Aggiungi e verifica gli articoli nuovi", "sezione": "funzioni", "gruppo": "articoli",
                          "scrive": True, "descrizione": "Legge i PDF nuovi della inbox, poi DOI con Crossref, riviste, "
                          "PubMed, Europe PMC, bibliografie, autori, pezzi di testo e vettori per la ricerca, report."},
    "pacchetti": {"titolo": "Prepara i pacchetti per le schede", "sezione": "funzioni", "gruppo": None, "scrive": True,
                  "priorita": True,
                  "passi": ["Sceglie gli articoli senza scheda secondo la priorità (una rivista, lo stesso numero per "
                            "rivista, oppure a sorte)", "Un pacchetto per articolo in rapporti\\pacchetti"],
                  "descrizione": "Prepara il lavoro delle schede degli articoli: le scrive Claude in sessione (strada "
                  "principale) oppure, in emergenza, una chat gratuita. Il testo resta su questo PC."},
    "chat_schede": {"titolo": "Schede con una chat gratuita (emergenza)", "sezione": "funzioni", "gruppo": None,
                    "scrive": False, "finestra": True,
                    "passi": ["Prepara il prossimo articolo e copia i messaggi negli appunti, uno alla volta",
                              "Legge la risposta della chat dagli appunti e la controlla",
                              "Se serve, copia negli appunti la correzione da dare alla chat"],
                    "descrizione": "Solo per pochi articoli o quando Claude non può lavorare. Si apre una finestra che "
                    "guida passo per passo: incolli nella chat, copi la risposta, ORPHEUS la controlla. Il database "
                    "non cambia: per quello c'è \"Invia le schede della chat\"."},
    "invia_schede_chat": {"titolo": "Invia le schede della chat", "sezione": "funzioni", "gruppo": None,
                          "scrive": True,
                          "passi": ["Copia di sicurezza del database",
                                    "Le schede accettate della chat, con gli stessi controlli delle schede di Claude",
                                    "Registro senza testo degli articoli"],
                          "descrizione": "Mette nel database le schede della chat gratuita già accettate (in "
                          "rapporti\\schede\\chat). Ognuna viene ricontrollata sul testo del database."},
    "aggiorna_riviste": {"titolo": "Aggiorna le riviste", "sezione": "funzioni", "gruppo": "riviste", "scrive": True,
                         "descrizione": "Riviste dell'elenco e linee guida salvate (con gli avvisi se cambiano), schede "
                         "delle riviste scritte da Claude e controllo a mano, metriche da SCImago (file in inbox\\metriche) "
                         "e OpenAlex, report."},
    "invia_lavoro_claude": {"titolo": "Invia il lavoro di Claude", "sezione": "affermazioni", "gruppo": "conoscenza",
                            "scrive": True, "descrizione": "Schede degli articoli e affermazioni scritte da Claude "
                            "(controllate prima di entrare nel database), controllo a mano, pezzi e vettori, report."},
    "prepara_manoscritto": {"titolo": "Prepara il manoscritto per la rivista", "sezione": "manoscritti", "gruppo": None,
                            "scrive": False, "manoscritto": True,
                            "passi": ["Controllo con le regole della rivista e verifica dei riferimenti (Crossref: solo "
                                      "DOI e titoli citati)", "Pacchetto della revisione simulata per Claude",
                                      "Pacchetto della lettera di presentazione per Claude", "File di validazione"],
                            "descrizione": "Per il manoscritto e la rivista scelti: tutto quello che si prepara sul PC. "
                            "Revisione e lettera le scrive poi Claude in sessione. Il manoscritto resta sul PC."},
    "manutenzione": {"titolo": "Manutenzione", "sezione": "sistema", "gruppo": None, "scrive": False,
                     "passi": ["Controllo generale", "Prova di ripristino dell'ultima copia di sicurezza",
                               "Salute di ORPHEUS", "Report HTML"],
                     "descrizione": "Controlla il database e le copie di sicurezza; non modifica il database."},
    "esporta_schede": {"titolo": "Esporta le schede per i colleghi", "sezione": "condivisione", "gruppo": None,
                       "scrive": False,
                       "passi": ["Schede delle riviste e degli articoli, metadati, pezzi di testo e vettori",
                                 "Un file .zip nella cartella condivisione dei dati (niente PDF, niente manoscritti)"],
                       "descrizione": "Il file da mandare ai colleghi con gli stessi accessi agli articoli. Contiene "
                       "testo di articoli non aperti: mai su GitHub. Non modifica il database."},
    "importa_schede": {"titolo": "Importa le schede di un collega", "sezione": "condivisione", "gruppo": None,
                       "scrive": False, "finestra": True,
                       "passi": ["Prova: cosa entrerebbe e cosa hai già", "Dopo il tuo sì: copia di sicurezza e unione",
                                 "Quello che hai già resta tuo"],
                       "descrizione": "Metti il file .zip ricevuto in ORPHEUS_DATI\\inbox\\condivisione. Si apre una "
                       "finestra che mostra cosa entrerebbe e chiede conferma."},
    "lavora": {"titolo": "Lavora: tutto quello che manca", "sezione": "funzioni", "gruppo": "tutto", "scrive": True,
               "descrizione": "Tutti i passi di lavora.cmd, in ordine."},
}
ACTION_ORDER = ("aggiorna_articoli", "aggiorna_riviste", "pacchetti", "invia_lavoro_claude", "chat_schede",
                "invia_schede_chat", "prepara_manoscritto", "manutenzione", "esporta_schede", "importa_schede", "lavora")
PRIORITY_MODES = ("casuale", "rivista", "equo")


def actions_list() -> list[dict]:
    out = []
    for key in ACTION_ORDER:
        a = ACTIONS[key]
        steps = ([{"passo": k, "titolo": engine.BY_KEY[k].title} for k in engine.GROUPS[a["gruppo"]]["passi"]]
                 if a["gruppo"] else [{"passo": None, "titolo": t} for t in a["passi"]])
        out.append({"azione": key, "comando": f"{key}.cmd", "titolo": a["titolo"], "sezione": a["sezione"],
                    "gruppo": a["gruppo"], "scrive": a["scrive"], "manoscritto": bool(a.get("manoscritto")),
                    "priorita": bool(a.get("priorita")), "finestra": bool(a.get("finestra")),
                    "descrizione": a["descrizione"], "passi": steps})
    return out


# ---------------------------------------------------------------- lavora (the queue)

# ---------------------------------------------------------------- manuscripts (T-037, Fase 6)

KINDS_ALLOWED = ("auto", "originale", "revisione", "protocollo")


RECENT_MANUSCRIPTS = "manoscritti_recenti.json"     # local folder of the PC: paths never go to GitHub or the registri
RECENT_MAX = 30


def _recent_file(s: Settings) -> Path:
    return Path(s.local_root) / RECENT_MANUSCRIPTS


def recent_manuscripts(s: Settings) -> list[dict]:
    """Manuscripts chosen with "Scegli il manoscritto" (2026-10-30): one path per manuscript, most recent first."""
    try:
        rows = json.loads(_recent_file(s).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [r for r in rows if isinstance(r, dict) and isinstance(r.get("percorso"), str)] if isinstance(rows, list) else []


def _save_recent(s: Settings, rows: list[dict]) -> None:
    f = _recent_file(s)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows[:RECENT_MAX], ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, f)


def remember_manuscript(s: Settings, path: str) -> dict:
    """Checks a path chosen by the user (an existing .docx) and puts it first in the history."""
    p = Path(str(path or "").strip().strip('"'))
    if not str(p) or not p.is_absolute():
        raise ValueError("scrivi il percorso completo del file Word (.docx)")
    if p.suffix.lower() != ".docx" or p.name.startswith("~$"):
        raise ValueError("il manoscritto deve essere un file Word .docx")
    if not p.is_file():
        raise ValueError(f"file non trovato: {p}")
    rows = [r for r in recent_manuscripts(s) if os.path.normcase(r["percorso"]) != os.path.normcase(str(p))]
    entry = {"percorso": str(p), "nome": p.name, "ultimo_uso": utc_now()}
    _save_recent(s, [entry, *rows])
    return entry


def forget_manuscript(s: Settings, path: str) -> None:
    _save_recent(s, [r for r in recent_manuscripts(s) if os.path.normcase(r["percorso"]) != os.path.normcase(str(path))])


def _chosen_path(s: Settings, key: str) -> Path | None:
    """A manuscript key: the name of a file of the manuscripts folder, or a path of the history (nothing else)."""
    from . import manuscript
    by_name = {f.name: f for f in manuscript.list_files(s.home)}
    if key in by_name:
        return by_name[key]
    for r in recent_manuscripts(s):
        if os.path.normcase(r["percorso"]) == os.path.normcase(key):
            p = Path(r["percorso"])
            return p if p.is_file() else None
    return None


def choose_manuscript_dialog(s: Settings) -> str | None:
    """The Windows window "Apri" (the page runs on this PC): returns the chosen path or None if cancelled."""
    if os.name != "nt":
        raise ValueError("la finestra per scegliere il file c'è solo su Windows: incolla il percorso completo")
    script = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8; Add-Type -AssemblyName System.Windows.Forms; "
              "$o=New-Object System.Windows.Forms.Form -Property @{TopMost=$true}; "
              "$d=New-Object System.Windows.Forms.OpenFileDialog; $d.Filter='Word (*.docx)|*.docx'; "
              "$d.Title='Scegli il manoscritto (ORPHEUS)'; "
              "if($d.ShowDialog($o) -eq [System.Windows.Forms.DialogResult]::OK){ $d.FileName }")
    r = subprocess.run([s.powershell, "-NoProfile", "-STA", "-Command", script], capture_output=True, timeout=900,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    out = r.stdout.decode("utf-8", "replace").strip()
    return out or None


def manuscripts(s: Settings, conn) -> dict:
    """The .docx in the manuscripts folder of the data disk, the ones chosen elsewhere (history) and the journals with
    a card. Read-only."""
    from . import compliance, manuscript
    files = manuscript.list_files(s.home)
    js = compliance.journals_with_cards(conn, s.journal_list())
    info = lambda f: {"kb": max(1, round(f.stat().st_size / 1024)),  # noqa: E731
                      "modificato": datetime.fromtimestamp(f.stat().st_mtime, timezone.utc).isoformat()}
    chosen = []
    for r in recent_manuscripts(s):
        p = Path(r["percorso"])
        chosen.append({"percorso": r["percorso"], "nome": p.name, "ultimo_uso": r.get("ultimo_uso"),
                       "esiste": p.is_file(), **(info(p) if p.is_file() else {})})
    return {"cartella": str(manuscript.folder(s.home)),
            "scelti": chosen,
            "file": [{"nome": f.name, **info(f)} for f in files],
            "riviste": [{"sigla": j["sigla"] or j["titolo"], "titolo": j["titolo"], "data_scheda": j["data_scheda"]}
                        for j in js],
            "tipi": compliance.KINDS,
            **_src("cartella manoscritti del disco dei dati (file .docx); journal_card (schede correnti)",
                   max((j["data_scheda"] for j in js), default=None))}


def manuscript_check(s: Settings, conn, name: str, wanted: list[str] | None, kind: str, matcher=None) -> dict | None:
    """compliance-v1 on one file of the folder (only a name listed there: no other path can be asked). Read-only."""
    from . import compliance, manuscript
    path = _chosen_path(s, name)
    if path is None:
        return None
    out = compliance.run(conn, path, s.journal_list(), wanted, kind if kind in KINDS_ALLOWED else "auto",
                         matcher=matcher)
    out["file"] = name
    if not out.get("verifica_riferimenti"):           # T-038: the last saved check, no network from the dashboard
        out["verifica_riferimenti"] = compliance.last_reference_check(s.home, out["manoscritto"]["impronta"])
    out.update(_src(f"{path.name} ({manuscript.MANUSCRIPT_VERSION}) e schede delle riviste (journal_card_value, "
                    f"{compliance.COMPLIANCE_VERSION})", utc_now()))
    return out


def _last_runs(conn, limit: int = 100) -> list[dict]:
    out = []
    for r in conn.execute("""SELECT w.id, t.step, w.status, w.attempt, w.interrupted, w.started_at, w.finished_at, w.error,
                                    w.validation_status, w.parameters, w.commands, w.host
                             FROM workflow_run w JOIN task t ON t.id = w.task_id WHERE t.step IS NOT NULL
                             ORDER BY w.started_at DESC, w.rowid DESC LIMIT ?""", (limit,)):
        params = json.loads(r["parameters"] or "{}")
        esito = ("in corso" if r["status"] == "running" else "interrotto" if r["interrupted"] else
                 "fatto" if r["status"] == "succeeded" and r["validation_status"] == "pass" else
                 "fatto con errori" if r["status"] == "succeeded" else "non riuscito")
        out.append({"id": r["id"], "passo": r["step"],
                    "titolo": engine.BY_KEY[r["step"]].title if r["step"] in engine.BY_KEY else r["step"],
                    "stato": r["status"], "esito": esito, "tentativo": r["attempt"], "iniziato": r["started_at"],
                    "finito": r["finished_at"], "errore": r["error"], "giro": params.get("giro"),
                    "da_fare": params.get("da_fare"), "pc": r["host"], "comandi": json.loads(r["commands"] or "[]")})
    return out


class PlanCache:
    def __init__(self):
        self.value, self.when = None, 0.0
        self._lock = threading.Lock()

    def get(self, s: Settings, conn, refresh: bool = False) -> dict:
        with self._lock:
            if refresh or self.value is None or time.monotonic() - self.when > PLAN_CACHE_SECONDS:
                env = engine.Env(conn, s.home, s.project, s.local_root)
                self.value = {**engine.plan(env, counts=True), "calcolato": utc_now(),
                              "gruppi": {g: list(v["passi"]) for g, v in engine.GROUPS.items()}}
                self.when = time.monotonic()
            return self.value

    def clear(self):
        with self._lock:
            self.value = None


class LavoraRunner:
    """Starts one action exactly as its .cmd does (PowerShell orpheus_comandi.ps1 -Comando <azione>) and keeps its
    output; one action at a time. `command`: the command line of lavora (tests: any command, used for every action) or
    a function action -> command line."""
    def __init__(self, command):
        self.command = command
        self.lines: list[str] = []
        self.running = False
        self.code = None
        self.action = None
        self.started = self.finished = None
        self._lock = threading.Lock()

    def command_for(self, action: str) -> list[str] | None:
        return self.command(action) if callable(self.command) else self.command

    def start(self, action: str = "lavora", extra_env: dict | None = None) -> tuple[bool, str]:
        cmd = self.command_for(action)
        with self._lock:
            if cmd is None:
                return False, "comandi di ORPHEUS non trovati su questo PC (avvia il cruscotto con cruscotto.cmd)"
            if self.running:
                return False, f"{ACTIONS.get(self.action, {}).get('titolo', self.action)}: già in corso da questo cruscotto"
            self.lines, self.code, self.running, self.action = [], None, True, action
            self.started, self.finished = utc_now(), None
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    text=True, encoding="utf-8", errors="replace",
                                    env=dict(os.environ, PYTHONUTF8="1", **(extra_env or {})))
        except OSError as exc:
            with self._lock:
                self.running, self.finished = False, utc_now()
                self.lines.append(f"ERRORE: lavora non partito: {exc}")
            return False, str(exc)
        threading.Thread(target=self._read, args=(proc,), daemon=True).start()
        return True, "partito"

    def _read(self, proc):
        for line in proc.stdout:
            with self._lock:
                self.lines.append(line.rstrip("\r\n").lstrip("\ufeff"))
        code = proc.wait()
        with self._lock:
            self.code, self.running, self.finished = code, False, utc_now()

    def snapshot(self, since: int = 0) -> dict:
        with self._lock:
            return {"in_corso": self.running, "righe": self.lines[since:], "prossima": len(self.lines), "codice": self.code,
                    "iniziato": self.started, "finito": self.finished, "azione": self.action,
                    "titolo": ACTIONS.get(self.action, {}).get("titolo") if self.action else None,
                    "disponibile": self.command_for("lavora") is not None}


def action_command(s: Settings, action: str = "lavora") -> list[str] | None:
    if action not in ACTIONS or s.commands_script is None or not Path(s.commands_script).exists():
        return None
    return [s.powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(s.commands_script), "-Comando", action]


def open_window(s: Settings, action: str) -> tuple[bool, str]:
    """Commands that ask questions (chat_schede): the dashboard cannot answer them, so it opens the same .cmd in a
    window of its own, as a double click would (Windows only)."""
    if action not in ACTIONS or s.commands_script is None:
        return False, "comandi di ORPHEUS non trovati su questo PC (avvia il cruscotto con cruscotto.cmd)"
    cmd_file = Path(s.commands_script).parent / f"{action}.cmd"
    if not cmd_file.exists():
        return False, f"{cmd_file.name} non trovato in {cmd_file.parent}"
    try:
        subprocess.Popen(["cmd", "/c", "start", "", str(cmd_file)], cwd=str(cmd_file.parent))
    except OSError as exc:
        return False, f"finestra non aperta: {exc}"
    return True, f"si è aperta la finestra di {cmd_file.name}"


def lavora_command(s: Settings) -> list[str] | None:
    return action_command(s, "lavora")


def work(s: Settings, conn) -> dict:
    env = engine.Env(conn, s.home, s.project, s.local_root)
    lock = engine.Lock(s.local_root, "", 0).read()
    return {"esecuzioni": _last_runs(conn, 150), "lavori": engine.work_for_others(env),
            "in_corso_sul_pc": lock if engine.lock_alive(lock) else None,
            "passi": [{"passo": st.key, "titolo": st.title, "unita": st.unit} for st in engine.STEPS],
            **_src("task, workflow_run (engine-v1), cartelle da_inviare", utc_now())}


# ---------------------------------------------------------------- system

def _health(s: Settings, conn, state: LocalState) -> dict:
    from . import health
    try:
        return health.check(conn, state=state, project=s.project, db_path=s.db_path, home=s.home)
    except Exception as exc:  # noqa: BLE001 — the page must still show the rest of the section
        return {"contratto": health.CONTRACT, "esito": "errore", "controlli": [],
                "da_guardare": [f"Controllo della salute non riuscito: {exc}"], "controllato_il": utc_now()}


# ---------------------------------------------------------------- condivisione con i colleghi (T-053, 0.54.1)

SHARE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ ()-]{0,150}\.zip$")
SHARE_MAX_BYTES = 1_000_000_000


def _share_files(folder: Path) -> list[dict]:
    if not folder.is_dir():
        return []
    out = []
    for f in sorted(folder.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True):
        st = f.stat()
        out.append({"nome": f.name, "mb": round(st.st_size / 1e6, 1),
                    "data": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(st.st_mtime))})
    return out


def share_info(s: Settings) -> dict:
    from . import sharing
    out_dir, in_dir = s.home / sharing.FOLDER_OUT, s.home / sharing.FOLDER_IN
    return {"creati": _share_files(out_dir), "ricevuti": _share_files(in_dir),
            "cartella_creati": str(out_dir), "cartella_ricevuti": str(in_dir),
            **_src("cartelle condivisione e inbox\\condivisione dei dati", utc_now())}


def _share_guard(s: Settings, conn):
    report = lineage.check(conn, LocalState(s.local_root))
    if report.blocking:
        raise ValueError(report.message)


def share_export(s: Settings, conn) -> dict:
    """Package for colleagues, made from the page (same as esporta_schede.cmd; the database does not change)."""
    from . import sharing
    _share_guard(s, conn)
    res = sharing.export_package(conn, dest_dir=s.home / sharing.FOLDER_OUT)
    return {"nome": Path(res["pacchetto"]).name, "pacchetto": res["pacchetto"], "dimensione_mb": res["dimensione_mb"],
            "riepilogo": res["riepilogo"], "creato_il": res["creato_il"]}


def share_file(s: Settings, name: str, *, received: bool = False) -> Path:
    """A package of one of the two folders, by bare name only (nothing outside them can be reached)."""
    from . import sharing
    if not name or not SHARE_NAME.match(name) or Path(name).name != name:
        raise ValueError("nome del file non valido")
    path = s.home / (sharing.FOLDER_IN if received else sharing.FOLDER_OUT) / name
    if not path.is_file():
        raise FileNotFoundError(f"{name} non trovato")
    return path


def share_upload(s: Settings, name: str, data: bytes) -> dict:
    """A package chosen in the browser: saved in inbox\\condivisione after checking it is a package of ORPHEUS."""
    import tempfile
    from . import sharing
    name = Path(name or "").name
    if not SHARE_NAME.match(name):
        raise ValueError("scegli il file .zip ricevuto dal collega (ORPHEUS_condivisione_….zip)")
    if not data:
        raise ValueError("il file è vuoto")
    if len(data) > SHARE_MAX_BYTES:
        raise ValueError("il file è troppo grande per un pacchetto di condivisione")
    folder = s.home / sharing.FOLDER_IN
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    if target.exists() and target.read_bytes() != data:
        stem, n = target.stem, 2
        while (folder / f"{stem} ({n}).zip").exists():
            n += 1
        target = folder / f"{stem} ({n}).zip"
    partial = target.with_name(target.name + ".partial")
    partial.write_bytes(data)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            manifest, _db = sharing.read_package(partial, Path(tmp))
    except sharing.SharingError:
        partial.unlink(missing_ok=True)
        raise
    partial.replace(target)
    return {"nome": target.name, "creato_il": manifest.get("creato_il"), "riepilogo": manifest.get("riepilogo")}


def share_merge(s: Settings, conn, name: str, *, trial: bool) -> dict:
    """Trial or real merge of a received package; the real one makes a backup first (keep yours)."""
    from . import backups as backup_mod
    from . import sharing
    path = share_file(s, name, received=True)
    if trial:
        _share_guard(s, conn)
        res = sharing.merge_package(conn, path, dry_run=True)
        return {**res, "nome": name}

    def write():
        entry = backup_mod.make_backup(conn, LocalState(s.local_root), reason="prima di condivisione-unisci (cruscotto)")
        res = sharing.merge_package(conn, path)
        return {**res, "nome": name, "copia_di_sicurezza": str(LocalState(s.local_root).backup_dir / entry["file"]),
                "controllo": db.integrity_check(conn)}
    return _protected_write(s, conn, write)


def open_in_folder(path: Path) -> tuple[bool, str]:
    """Shows the file in Explorer (Windows); elsewhere only says where it is."""
    if os.name != "nt":
        return False, str(path)
    try:
        subprocess.Popen(["explorer", f"/select,{path}"])
    except OSError as exc:
        return False, f"cartella non aperta: {exc}"
    return True, str(path)


def system(s: Settings, conn) -> dict:
    state = LocalState(s.local_root)
    rep = lineage.check(conn, state)
    head = lineage.head(conn)
    sources = [{"id": x.id, "nome": getattr(x, "name", x.id), "costo": x.cost, "abilitata": x.enabled}
               for x in config.load_sources()]
    backups = []
    if head.database_id:
        for e in state.backups(head.database_id)[-10:]:
            reason = e.get("reason") or ""
            backups.append({"file": e.get("file"), "data": e.get("created_at"),
                            "motivo": REASONS.get(reason, reason.replace("before_", "prima di ").replace("_", " ")),
                            "posizione": e.get("head_seq")})
    return {"versione": __version__, "regole": RULES_VERSION, "pc": socket.gethostname(),
            "database": {"percorso": str(s.db_path), "mb": round(s.db_path.stat().st_size / 1e6, 1),
                         "id": head.database_id, "posizione": head.seq},
            "migrazioni": [{"versione": m["version"], "stato": m["state"]} for m in db.status(conn)],
            "controllo_copia": rep.as_dict(),
            "budget": {"servizi_a_pagamento": bool(s.project.limits.paid_services_allowed), "fonti": sources},
            "audit": {"eventi": _one(conn, "SELECT count(*) FROM audit_event"),
                      "ultimo": _one(conn, "SELECT max(occurred_at) FROM audit_event")},
            "copie_di_sicurezza": list(reversed(backups)),
            "salute": _health(s, conn, state),
            "modello_ricerca": (semantic.model_dir(s.local_root) / semantic.MODEL_FILE).exists(),
            **_src("schema_migrations, audit_event, config, cartella locale del PC", utc_now())}


# ---------------------------------------------------------------- EURYDIKE: reviews (T-051n)

def _article_reviews(conn, article_id: str) -> list[dict]:
    if not _one(conn, "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'review_study'"):
        return []
    from .eurydike import overview
    return overview.article_reviews(conn, article_id)


def eurydike_reviews(conn) -> dict:
    from .eurydike import overview
    return overview.reviews(conn)


def eurydike_review(conn, review_id: str) -> dict | None:
    from .eurydike import overview
    return overview.review_overview(conn, review_id)


def eurydike_records(conn, review_id: str, q) -> dict | None:
    from .eurydike import overview
    try:
        start, many = int(q.get("da") or 0), int(q.get("quanti") or overview.PAGE)
    except ValueError:
        start, many = 0, overview.PAGE
    return overview.records(conn, review_id, filtro=q.get("filtro") or "tutti", q=(q.get("q") or "")[:200],
                            da=start, quanti=many)


def _protected_write(s: Settings, conn, fn):
    """A write from the page with the protections of a command that writes: refused when the copy check blocks;
    afterwards this PC records what it has seen and backs up if the last backup is old."""
    from . import backups as backup_mod
    from .eurydike import review as rv
    state = LocalState(s.local_root)
    report = lineage.check(conn, state)
    if report.blocking:
        raise rv.ReviewError(report.message)
    before = lineage.head(conn)
    out = fn()
    h = lineage.head(conn)
    if h.database_id:
        state.record_seen(h, s.db_path)
        if getattr(s.project, "local", None) is not None:
            backup_mod.maybe_auto_backup(conn, state, s.project.local, head_before=before)
    return out


# ---------------------------------------------------------------- T-056: submissions diary and lessons (R10: local only)

def submissions_view(s: Settings, conn) -> dict:
    """The diary of the user's submissions with letters, comments and labels, the lessons and the journals of the list.
    Shown only in this page on this PC (R10): never logged, never shared."""
    from . import submissions as sb
    journals = [{"id": r["id"], "titolo": r["title"]} for r in conn.execute("SELECT id, title FROM journal ORDER BY title")]
    return {**sb.diary(conn, home=s.home), "lezioni": sb.lessons(conn), "stato": sb.status(conn), "riviste": journals,
            **_src("my_manuscript, submission, submission_event, submission_letter, reviewer_comment, comment_label "
                   "(migrazione 0017, feedback-v1)", utc_now())}


def submissions_write(s: Settings, conn, what: str, body: dict) -> dict:
    """One write from the page (new paper, submission, event with the letter, correction, label decision), with the
    protections of a command that writes (_protected_write)."""
    from . import submissions as sb
    g = lambda k: body.get(k)  # noqa: E731

    def run():
        if what == "lavoro":
            return {"id": sb.add_manuscript(conn, g("nome") or "", title=g("titolo"), kind=g("tipo") or None,
                                            note=g("nota"))}
        if what == "nuova":
            if not g("rivista_id"):
                raise ValueError("scegli la rivista dall'elenco (se manca: Riviste → Nuova rivista)")
            return {"id": sb.add_submission(conn, g("lavoro") or "", "", g("data") or "", journal_id=g("rivista_id"),
                                            previous_id=g("precedente") or None, note=g("nota"))}
        if what == "evento":
            return sb.add_event(conn, g("sottomissione") or "", g("evento") or "", g("data") or "",
                                round_no=g("giro") or 1, letter=g("lettera"), due_on=g("scadenza") or None,
                                note=g("nota"))
        if what == "correggi":
            return {"id": sb.correct_event(conn, g("evento_id") or "", event=g("evento") or None,
                                           event_on=g("data") or None, due_on=g("scadenza") or None, note=g("nota"),
                                           round_no=g("giro") or None)}
        if what == "togli_evento":
            sb.remove_event(conn, g("evento_id") or "")
            return {}
        if what == "commento":
            return sb.add_comment(conn, g("lettera") or "", g("chi") or "", g("testo") or "")
        if what == "togli_commento":
            sb.remove_comment(conn, g("commento") or "")
            return {}
        if what == "nessun_commento":
            sb.set_no_comments(conn, g("lettera") or "", bool(g("valore")))
            return {}
        if what == "etichetta":
            return {"id": sb.set_label(conn, g("commento") or "", category=g("categoria") or None,
                                       severity=g("gravita") or None, followed=g("seguito") or None,
                                       quote=g("citazione") or None)}
        raise ValueError("azione sconosciuta")
    return {"ok": True, **_protected_write(s, conn, run)}


# ---------------------------------------------------------------- T-054: journal list and "Da assegnare"

def _locator(s: Settings):
    return layout.PdfLocator(s.home, s.project.storage.inbox_dir, s.local_root)


def journal_list_rows(s: Settings, conn) -> dict:
    """The rows of riviste_prioritarie.csv for the window "Nuova rivista" / "Modifica" (read-only)."""
    from .journal_list_write import JournalList
    path = s.home / s.project.storage.journals_csv
    jl = JournalList.load(path)
    rows = jl.data_rows()
    by_sigla = policy_versions.journal_by_sigla(conn, rows)
    gs = journal_cards.guideline_status(conn, s.home, s.project.storage.inbox_dir, rows)
    guide = {g["sigla"]: g for g in gs["righe"]}
    out = []
    for r in rows:
        x = {k: r.get(k, "") for k in layout.CSV_COLUMNS}
        x["nel_database"] = r["sigla"].strip().lower() in by_sigla
        x["automatica"] = (r.get("note") or "").startswith("aggiunta automatica")
        x["linee_guida"] = guide.get(r["sigla"])
        out.append(x)
    when = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") \
        if path.exists() else None
    return {"righe": out, "colonne": list(layout.CSV_COLUMNS), "linee_guida": {k: gs[k] for k in ("conteggi", "sigle")},
            "stati_linee_guida": journal_cards.GUIDELINE_STATES, **_src(s.project.storage.journals_csv, when)}


def journal_check(s: Settings, conn, body: dict) -> dict:
    """The checks of the window, field by field, without writing."""
    from .journal_list_write import JournalList
    jl = JournalList.load(s.home / s.project.storage.journals_csv)
    values = {k: str((body.get("valori") or {}).get(k) or "").strip() for k in layout.CSV_COLUMNS}
    original = body.get("sigla_originale") or None
    editing = jl.find(original) if original else None
    return {"problemi": jl.check(values, conn=conn, editing=editing)}


def journal_save(s: Settings, conn, body: dict) -> dict:
    from . import journal_folders as jf
    values = body.get("valori") or {}
    if not isinstance(values, dict):
        raise ValueError("valori non leggibili")
    return _protected_write(s, conn, lambda: jf.save_journal(
        conn, s.home, s.project.storage.inbox_dir, s.home / s.project.storage.journals_csv, values,
        body.get("sigla_originale") or None, _locator(s), archive_dir=getattr(s.project.storage, "archive_dir", "data/archive")))


def journal_lookup(s: Settings, body: dict, *, clients=None) -> dict:
    """"Compila dall'ISSN": Crossref + OpenAlex (network), nothing written."""
    from . import journal_folders as jf
    from . import crossref as cr
    from . import journal_metrics
    if clients is None:
        secrets = config.load_secrets()
        if not cr.valid_email(secrets.contact_email):
            raise ValueError("manca l'indirizzo e-mail in ORPHEUS_CONTACT_EMAIL (file .env): serve per Crossref e OpenAlex")
        key = secrets.openalex_api_key.get_secret_value() if secrets.openalex_api_key else None
        clients = (cr.CrossrefClient(secrets.contact_email, max_retries=1),
                   journal_metrics.OpenAlexSources(secrets.contact_email, api_key=key, max_retries=1))
    return jf.lookup_issn(str(body.get("issn") or ""), crossref_client=clients[0], openalex_client=clients[1])


def journal_card_view(conn, journal_id: str) -> dict:
    """T-052: the card of a journal (PDF values and values by hand) for the page. Read-only."""
    return {**journal_cards.card_view(conn, journal_id),
            **_src("journal_card, journal_card_value e journal_card_manual (valori scritti a mano)", utc_now())}


def journal_card_write(s: Settings, conn, what: str, body: dict) -> dict:
    """T-052: a value by hand (new row, the old one superseded) or its withdrawal, with the protections of a command
    that writes (_protected_write)."""
    g = lambda k: body.get(k)  # noqa: E731
    if what == "valore":
        return {"ok": True, "id": _protected_write(s, conn, lambda: journal_cards.set_manual(
            conn, str(g("journal_id") or ""), str(g("campo") or ""), value=g("valore"), source=str(g("fonte") or ""),
            read_on=str(g("letto_il") or ""), article_type=g("tipo"), item=g("item") or 1, unit=g("unita"),
            note=g("nota"), not_available=bool(g("non_indicato"))))}
    if what == "togli":
        _protected_write(s, conn, lambda: journal_cards.withdraw_manual(conn, str(g("id") or "")))
        return {"ok": True}
    raise ValueError("azione sconosciuta")


def unassigned(s: Settings, conn) -> dict:
    from . import journal_folders as jf
    loc = _locator(s)
    out = jf.unassigned(conn, s.home, s.project.storage.inbox_dir, s.journal_list(), loc)
    loc.save()
    return {**out, **_src("document, doi_check, journal_issn; cartella inbox/articoli_vari", utc_now())}


def assign_journal(s: Settings, conn, body: dict) -> dict:
    from . import journal_folders as jf
    doc, sigla = str(body.get("document_id") or ""), str(body.get("sigla") or "")
    if not doc or not sigla:
        raise ValueError("scegli la rivista")
    loc = _locator(s)
    out = _protected_write(s, conn, lambda: jf.assign(conn, s.home, s.project.storage.inbox_dir, s.journal_list(), loc,
                                                       doc, sigla))
    loc.save()
    return out


def eurydike_new_review(s: Settings, conn, body: dict) -> dict:
    from .eurydike import protocol
    return _protected_write(s, conn, lambda: protocol.new_review(conn, s.home, body))


def eurydike_import(s: Settings, conn, review_id: str) -> dict:
    from .eurydike import protocol
    return _protected_write(s, conn, lambda: protocol.import_new(conn, s.home, review_id))


def eurydike_search_files(s: Settings, conn, review_id: str) -> dict:
    from .eurydike import protocol
    return {**protocol.search_files(conn, s.home, review_id), **_src("cartella ricerca della revisione, search_import",
                                                                     utc_now())}


def eurydike_tools() -> dict:
    from .eurydike.core.tools import QUAL_TOOLS, ROB_TOOLS
    return {"rob": [{"id": k, "nome": v.name} for k, v in ROB_TOOLS.items()],
            "qualita": [{"id": k, "nome": v.name} for k, v in QUAL_TOOLS.items()]}


# ---------------------------------------------------------------- EURYDIKE: cards (T-051o)

def eurydike_cards(s: Settings, conn) -> dict:
    """Every article with a cards-v2 card, with what is left to check."""
    from .eurydike import store
    out = []
    for aid in store.articles_with_card(conn):
        c = store.card(conn, aid)
        a = conn.execute("SELECT publication_year FROM article WHERE id = ?", (aid,)).fetchone()
        out.append({"id": aid, "titolo": c["articolo"]["titolo"], "doi": c["articolo"]["doi"],
                    "anno": a[0] if a else None, **c["riepilogo"]})
    return {"schede": out, **_src("card_item, card_outcome_row (cards-v2)", utc_now())}


def eurydike_card(s: Settings, conn, aid: str) -> dict | None:
    from .eurydike import store
    try:
        c = store.card(conn, aid)
    except store.StoreError:
        return None
    exact = sum(1 for x in c["valori"] + c[store.OUTCOME_KEY] if x["da_controllare"] and x["controllo_citazione"] == "exact")
    return {**c, "esatte_da_confermare": exact, **_src("card_item, card_outcome_row (cards-v2)", utc_now())}


def eurydike_history(conn, aid: str, q) -> dict:
    from .eurydike import store
    if q.get("esito") is not None:
        rows = store.outcome_history(conn, aid, q.get("esito") or "", q.get("gruppo") or "", q.get("tempo") or "")
    else:
        rows = store.history(conn, aid, q.get("campo") or "", int(q.get("ordine") or 1))
    keep = ("value", "unit", "quote", "page_no", "quote_check", "state", "actor", "note", "created_at", *store.STATS)
    return {"storia": [{k: r.get(k) for k in keep if k in r} for r in rows]}


def eurydike_write(s: Settings, conn, aid: str, body: dict) -> dict:
    """One decision of the user on the card, from the page. Same protections as a command that writes: refused when
    the copy check blocks; afterwards this PC records what it has seen and backs up if the last backup is old."""
    from . import backups as backup_mod
    from .eurydike import store
    state = LocalState(s.local_root)
    report = lineage.check(conn, state)
    if report.blocking:
        raise store.StoreError(report.message)
    before = lineage.head(conn)
    action = body.get("azione")
    txt = lambda k: (str(body[k]).strip() or None) if body.get(k) not in (None, "") else None  # noqa: E731
    page = txt("pagina")
    try:
        page = int(page) if page is not None else None
    except ValueError as exc:
        raise store.StoreError("la pagina è un numero") from exc
    if action == "conferma_esatte":
        out = store.confirm_exact(conn, aid)
    elif action == "esporta":
        out = store.export_card(conn, aid, s.home)
    elif action in ("conferma", "correggi", "nr") and body.get("esito") is None:
        out = store.set_item(conn, aid, str(body.get("campo") or ""), item_ord=int(body.get("ordine") or 1),
                             state={"conferma": "confirmed", "correggi": "edited", "nr": "nr"}[action],
                             value=txt("valore"), unit=txt("unita"), quote=txt("citazione"), page=page, note=txt("nota"))
    elif action in ("conferma", "correggi"):
        stats = {k: body.get(k) for k in store.STATS if k in body}
        out = store.set_outcome_row(conn, aid, str(body.get("esito") or ""), str(body.get("gruppo") or ""),
                                    str(body.get("tempo") or ""), state="confirmed" if action == "conferma" else "edited",
                                    unit=txt("unita"), stats=stats, quote=txt("citazione"), page=page,
                                    table_label=txt("tabella"), note=txt("nota"))
    else:
        raise store.StoreError("azione sconosciuta")
    h = lineage.head(conn)
    if h.database_id and action != "esporta":
        state.record_seen(h, s.db_path)
        if getattr(s.project, "local", None) is not None:
            backup_mod.maybe_auto_backup(conn, state, s.project.local, head_before=before)
    return {"esito": out, "scheda": eurydike_card(s, conn, aid)}


# ---------------------------------------------------------------- web app

def abbreviations(s: Settings) -> dict:
    """Abbreviations and full names of the journal list, with the articles of each journal (for the colours: the
    journals with more articles get the most distinct colours). Works also without the database."""
    jl = s.journal_list()
    counts = {}
    try:
        conn = s.connect()
        try:
            sig = _sigla_by_journal(conn, jl)
            for jid, n in conn.execute("SELECT journal_id, count(*) FROM article WHERE journal_id IS NOT NULL GROUP BY 1"):
                if jid in sig:
                    counts[sig[jid]] = counts.get(sig[jid], 0) + n
        finally:
            conn.close()
    except (FileNotFoundError, sqlite3.Error):
        pass
    return {"sigle": sorted({x["sigla"] for x in jl if x.get("sigla")}),
            "titoli": {x["sigla"]: x.get("titolo") or x["sigla"] for x in jl if x.get("sigla")},
            "articoli": counts, "fonte": s.project.storage.journals_csv}


TEXTS_FILE = "testi_cruscotto.json"      # in the data folder: the same texts on every PC that uses the disk


def page_texts(s: Settings) -> dict:
    """The texts of the page changed by the user (2026-10-30): {original Italian text: new text}."""
    try:
        d = json.loads((Path(s.home) / TEXTS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in d.items() if isinstance(k, str) and isinstance(v, str)} if isinstance(d, dict) else {}


def set_page_text(s: Settings, original: str, new: str) -> dict:
    original, new = str(original or ""), str(new or "")
    if not original.strip() or len(original) > 4000 or len(new) > 4000:
        raise ValueError("testo vuoto o troppo lungo")
    d = page_texts(s)
    if not new.strip() or new == original:
        d.pop(original, None)
    else:
        if original not in d and len(d) >= 5000:
            raise ValueError("troppi testi modificati")
        d[original] = new
    f = Path(s.home) / TEXTS_FILE
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(dict(sorted(d.items())), ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, f)
    return d


LOOK_FILE = "aspetto_cruscotto.json"     # in the data folder: style and blocks of each section, the same on every PC
STYLES = ("classico", "moderno", "industriale", "oceano", "terra")


def page_look(s: Settings) -> dict:
    """The look chosen by the user (2026-10-30): {"stile": ..., "sezioni": {section: {ordine, nascosti, larghezza}}}."""
    try:
        d = json.loads((Path(s.home) / LOOK_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    if not isinstance(d, dict):
        d = {}
    style = d.get("stile") if d.get("stile") in STYLES else "classico"
    sections = d.get("sezioni") if isinstance(d.get("sezioni"), dict) else {}
    return {"stile": style, "sezioni": sections}


def _keys(value, limit=200) -> list[str]:
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError("elenco dei blocchi non valido")
    out = []
    for x in value:
        if not isinstance(x, str) or not x or len(x) > 300:
            raise ValueError("nome di blocco non valido")
        out.append(x)
    return out


def set_page_look(s: Settings, body: dict) -> dict:
    """Changes the style, or the layout of one section (order, hidden blocks, width), or restores a section."""
    d = page_look(s)
    if "stile" in body:
        if body["stile"] not in STYLES:
            raise ValueError("stile sconosciuto")
        d["stile"] = body["stile"]
    if "sezione" in body:
        sec = str(body.get("sezione") or "")
        if not re.fullmatch(r"[a-z/]{1,40}", sec):
            raise ValueError("sezione non valida")
        if body.get("ripristina"):
            d["sezioni"].pop(sec, None)
        else:
            width = body.get("larghezza") or {}
            if not isinstance(width, dict) or len(width) > 200 or any(
                    not isinstance(k, str) or len(k) > 300 or v not in ("intera", "mezza") for k, v in width.items()):
                raise ValueError("larghezza non valida")
            d["sezioni"][sec] = {"ordine": _keys(body.get("ordine") or []), "nascosti": _keys(body.get("nascosti") or []),
                                 "larghezza": width}
    if len(d["sezioni"]) > 60:
        raise ValueError("troppe sezioni")
    f = Path(s.home) / LOOK_FILE
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, f)
    return d


def create_app(s: Settings, *, runner: LavoraRunner | None = None, token: str | None = None, allowed_hosts=None,
               opener=None, lookup_clients=None, chooser=None):
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request
    from starlette.responses import HTMLResponse, JSONResponse, Response
    from starlette.routing import Mount, Route
    from starlette.staticfiles import StaticFiles

    token = token or secrets.token_urlsafe(24)
    runner = runner or LavoraRunner(lambda action: action_command(s, action))
    plans = PlanCache()
    searcher = Searcher(s.local_root)
    hosts = set(allowed_hosts or ()) | {"127.0.0.1", "localhost", "testserver"}

    class HostCheck(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]")
            if host not in hosts:
                return JSONResponse({"errore": "richiesta non permessa"}, status_code=403)
            response = await call_next(request)
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            return response

    def with_db(fn):
        def endpoint(request: Request):
            try:
                conn = s.connect()
            except FileNotFoundError as exc:
                return JSONResponse({"errore": str(exc)}, status_code=503)
            try:
                out = fn(request, conn)
                if isinstance(out, Response):
                    return out
                return JSONResponse(out)
            except Exception as exc:                                     # noqa: BLE001 - shown in the page
                return JSONResponse({"errore": f"{type(exc).__name__}: {exc}"}, status_code=500)
            finally:
                conn.close()
        return endpoint

    def index(_request):
        page = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(page.replace("__ORPHEUS_TOKEN__", token).replace("__ORPHEUS_VERSION__", __version__))

    def start_lavora(request: Request):
        if request.headers.get("x-orpheus-token") != token:
            return JSONResponse({"errore": "token mancante o sbagliato"}, status_code=403)
        ok, msg = runner.start()
        plans.clear()
        return JSONResponse({"partito": ok, "messaggio": msg, **({} if ok else {"errore": msg}), **runner.snapshot()},
                            status_code=200 if ok else 409)

    async def start_action(request: Request):
        if request.headers.get("x-orpheus-token") != token:
            return JSONResponse({"errore": "token mancante o sbagliato"}, status_code=403)
        try:
            body = await request.json()
        except Exception:                                                # noqa: BLE001
            body = {}
        action = (body or {}).get("azione")
        if action not in ACTIONS:
            return JSONResponse({"errore": "azione sconosciuta"}, status_code=400)
        extra = {}
        if ACTIONS[action].get("finestra"):
            ok, msg = (opener or open_window)(s, action)
            return JSONResponse({"partito": ok, "finestra": True, "messaggio": msg, **({} if ok else {"errore": msg})},
                                status_code=200 if ok else 409)
        if ACTIONS[action].get("priorita"):
            mode = str(body.get("modo") or "casuale")
            if mode not in PRIORITY_MODES:
                return JSONResponse({"errore": "priorità sconosciuta"}, status_code=400)
            sigla = str(body.get("rivista") or "")
            if mode == "rivista" and sigla not in {x["sigla"] for x in s.journal_list() if x.get("sigla")}:
                return JSONResponse({"errore": "scegli una rivista"}, status_code=400)
            try:
                how_many = int(body.get("quanti", 10))
            except (TypeError, ValueError):
                how_many = 0
            if not 1 <= how_many <= 100:
                return JSONResponse({"errore": "quanti pacchetti: da 1 a 100"}, status_code=400)
            extra = {"ORPHEUS_PACCHETTI_MODO": mode, "ORPHEUS_PACCHETTI_RIVISTA": sigla if mode == "rivista" else "",
                     "ORPHEUS_PACCHETTI_QUANTI": str(how_many)}
        if ACTIONS[action].get("manoscritto"):
            from . import manuscript
            name = str(body.get("file") or "")
            sigla = str(body.get("rivista") or "")
            chosen = _chosen_path(s, name)
            if chosen is None:
                return JSONResponse({"errore": "scegli prima il manoscritto"}, status_code=400)
            name = chosen.name if chosen.parent == manuscript.folder(s.home) else str(chosen)
            if sigla not in {x["sigla"] for x in s.journal_list() if x.get("sigla")}:
                return JSONResponse({"errore": "scegli una rivista"}, status_code=400)
            extra = {"ORPHEUS_SCELTA_FILE": name, "ORPHEUS_SCELTA_RIVISTA": sigla}
        ok, msg = runner.start(action, extra)
        plans.clear()
        return JSONResponse({"partito": ok, "messaggio": msg, **({} if ok else {"errore": msg}), **runner.snapshot()},
                            status_code=200 if ok else 409)

    def actions_endpoint(request, conn):
        lock = engine.Lock(s.local_root, "", 0).read()
        from . import chat_cards
        try:
            st = chat_cards.status(s.home)
            chat = {k: st[k] for k in ("preparati", "accettate", "rifiutate", "da_inviare", "inviate", "in_attesa",
                                       "da_leggere", "ancora_da_preparare")}
        except Exception:                                                # noqa: BLE001 — the buttons still show
            chat = None
        return {"azioni": actions_list(), "avvio": runner.snapshot(10 ** 9), "schede_chat": chat,
                "riviste": [x["sigla"] for x in s.journal_list() if x.get("sigla")],
                "in_corso_sul_pc": lock if engine.lock_alive(lock) else None,
                **_src("engine-v1 (gruppi di passi), orpheus_comandi.ps1", utc_now())}

    def lavora_output(request: Request):
        since = int(request.query_params.get("da", "0") or 0)
        return JSONResponse(runner.snapshot(max(0, since)))

    async def eurydike_post(request: Request, fn):
        from .eurydike import review as rv
        if request.headers.get("x-orpheus-token") != token:
            return JSONResponse({"errore": "token mancante o sbagliato"}, status_code=403)
        try:
            body = await request.json()
        except Exception:                                                # noqa: BLE001
            body = {}
        if not isinstance(body, dict):
            return JSONResponse({"errore": "richiesta non leggibile"}, status_code=400)
        try:
            conn = s.connect()
        except FileNotFoundError as exc:
            return JSONResponse({"errore": str(exc)}, status_code=503)
        try:
            return JSONResponse(fn(conn, body))
        except (rv.ReviewError, ValueError) as exc:
            return JSONResponse({"errore": str(exc)}, status_code=422)
        except sqlite3.DatabaseError as exc:
            return JSONResponse({"errore": f"il database ha rifiutato la modifica: {exc}"}, status_code=422)
        finally:
            conn.close()

    async def share_post(request: Request, fn, *, needs_db: bool = True):
        from . import sharing
        if request.headers.get("x-orpheus-token") != token:
            return JSONResponse({"errore": "token mancante o sbagliato"}, status_code=403)
        try:
            body = await request.json()
        except Exception:                                                # noqa: BLE001
            body = {}
        body = body if isinstance(body, dict) else {}
        conn = None
        try:
            if needs_db:
                conn = s.connect()
            return JSONResponse(fn(conn, body))
        except FileNotFoundError as exc:
            return JSONResponse({"errore": str(exc)}, status_code=404 if not needs_db or conn else 503)
        except (sharing.SharingError,) as exc:
            return JSONResponse({"errore": exc.message}, status_code=422)
        except (ValueError, rv_error()) as exc:
            return JSONResponse({"errore": str(exc)}, status_code=422)
        except sqlite3.DatabaseError as exc:
            return JSONResponse({"errore": f"il database ha rifiutato la modifica: {exc}"}, status_code=422)
        finally:
            if conn is not None:
                conn.close()

    async def journal_post(request: Request, fn, *, needs_db: bool = True):
        """POST of the journal list / assignments: token, copy check and backup (_protected_write), clear messages."""
        from .journal_list_write import ListError
        if request.headers.get("x-orpheus-token") != token:
            return JSONResponse({"errore": "token mancante o sbagliato"}, status_code=403)
        try:
            body = await request.json()
        except Exception:                                                # noqa: BLE001
            body = None
        if not isinstance(body, dict):
            return JSONResponse({"errore": "richiesta non leggibile"}, status_code=400)
        conn = None
        try:
            if needs_db:
                conn = s.connect()
            return JSONResponse(fn(conn, body))
        except FileNotFoundError as exc:
            return JSONResponse({"errore": str(exc)}, status_code=503)
        except ListError as exc:
            return JSONResponse({"errore": exc.message, "problemi": exc.problems}, status_code=422)
        except (ValueError, rv_error()) as exc:
            return JSONResponse({"errore": str(exc)}, status_code=422)
        except sqlite3.DatabaseError as exc:
            return JSONResponse({"errore": f"il database ha rifiutato la modifica: {exc}"}, status_code=422)
        finally:
            if conn is not None:
                conn.close()

    async def submissions_endpoint(request: Request):
        what = request.path_params["what"]
        return await journal_post(request, lambda c, b: submissions_write(s, c, what, b))

    async def journal_card_endpoint(request: Request):
        what = request.path_params["what"]
        return await journal_post(request, lambda c, b: journal_card_write(s, c, what, b))

    def journal_card_get(request, conn):
        try:
            return journal_card_view(conn, request.query_params.get("id") or "")
        except journal_cards.ManualError as exc:
            return JSONResponse({"errore": str(exc)}, status_code=404)

    async def assign_endpoint(request: Request):
        return await journal_post(request, lambda c, b: assign_journal(s, c, b))

    async def journal_check_endpoint(request: Request):
        return await journal_post(request, lambda c, b: journal_check(s, c, b))

    async def journal_save_endpoint(request: Request):
        return await journal_post(request, lambda c, b: journal_save(s, c, b))

    async def journal_lookup_endpoint(request: Request):
        return await journal_post(request, lambda c, b: journal_lookup(s, b, clients=lookup_clients), needs_db=False)

    def rv_error():
        from .eurydike import review as rv
        return rv.ReviewError

    async def share_export_endpoint(request: Request):
        return await share_post(request, lambda c, b: share_export(s, c))

    async def share_merge_endpoint(request: Request):
        return await share_post(request, lambda c, b: share_merge(s, c, str(b.get("nome") or ""),
                                                                  trial=bool(b.get("prova", True))))

    async def share_show_endpoint(request: Request):
        def show(_c, b):
            ok, where = open_in_folder(share_file(s, str(b.get("nome") or ""), received=bool(b.get("ricevuto"))))
            return {"aperta": ok, "percorso": where}
        return await share_post(request, show, needs_db=False)

    async def share_upload_endpoint(request: Request):
        from . import sharing
        if request.headers.get("x-orpheus-token") != token:
            return JSONResponse({"errore": "token mancante o sbagliato"}, status_code=403)
        size = int(request.headers.get("content-length") or 0)
        if size > SHARE_MAX_BYTES:
            return JSONResponse({"errore": "il file è troppo grande per un pacchetto di condivisione"}, status_code=413)
        data = await request.body()
        from urllib.parse import unquote
        try:
            return JSONResponse(share_upload(s, unquote(request.headers.get("x-nome-file") or ""), data))
        except sharing.SharingError as exc:
            return JSONResponse({"errore": exc.message}, status_code=422)
        except ValueError as exc:
            return JSONResponse({"errore": str(exc)}, status_code=422)

    def share_download_endpoint(request: Request):
        from starlette.responses import FileResponse
        try:
            path = share_file(s, request.query_params.get("nome") or "")
        except (ValueError, FileNotFoundError) as exc:
            return JSONResponse({"errore": str(exc)}, status_code=404)
        return FileResponse(path, media_type="application/zip", filename=path.name)

    async def new_review_endpoint(request: Request):
        return await eurydike_post(request, lambda c, b: eurydike_new_review(s, c, b))

    async def import_endpoint(request: Request):
        rid = request.path_params["rid"]
        return await eurydike_post(request, lambda c, b: eurydike_import(s, c, rid))

    def search_files_endpoint(request, conn):
        from .eurydike import review as rv
        try:
            return eurydike_search_files(s, conn, request.path_params["rid"])
        except rv.ReviewError as exc:
            return JSONResponse({"errore": str(exc)}, status_code=404)

    def review_endpoint(request, conn):
        d = eurydike_review(conn, request.path_params["rid"])
        return d if d is not None else JSONResponse({"errore": "revisione non trovata"}, status_code=404)

    def records_endpoint(request, conn):
        d = eurydike_records(conn, request.path_params["rid"], request.query_params)
        return d if d is not None else JSONResponse({"errore": "revisione non trovata"}, status_code=404)

    def eurydike_card_endpoint(request, conn):
        d = eurydike_card(s, conn, request.path_params["aid"])
        return d if d is not None else JSONResponse({"errore": "scheda non trovata"}, status_code=404)

    async def eurydike_write_endpoint(request: Request):
        from .eurydike import store
        if request.headers.get("x-orpheus-token") != token:
            return JSONResponse({"errore": "token mancante o sbagliato"}, status_code=403)
        try:
            body = await request.json()
        except Exception:                                                # noqa: BLE001
            body = None
        if not isinstance(body, dict):
            return JSONResponse({"errore": "richiesta non leggibile"}, status_code=400)
        try:
            conn = s.connect()
        except FileNotFoundError as exc:
            return JSONResponse({"errore": str(exc)}, status_code=503)
        try:
            return JSONResponse(eurydike_write(s, conn, request.path_params["aid"], body))
        except (store.StoreError, ValueError) as exc:
            return JSONResponse({"errore": str(exc)}, status_code=422)
        except sqlite3.DatabaseError as exc:
            return JSONResponse({"errore": f"il database ha rifiutato la modifica: {exc}"}, status_code=422)
        finally:
            conn.close()

    def detail(request, conn):
        d = article_detail(s, conn, request.path_params["aid"])
        return d if d is not None else JSONResponse({"errore": "articolo non trovato"}, status_code=404)

    def search_endpoint(request, conn):
        q = (request.query_params.get("q") or "").strip()
        if not q:
            return JSONResponse({"errore": "scrivi una domanda"}, status_code=400)
        mode = request.query_params.get("modo") or "ibrida"
        if mode not in ("ibrida", "parole", "significato"):
            mode = "ibrida"
        return searcher.search(conn, q[:500], mode)

    def manuscript_args(request: Request):
        wanted = [x.strip() for x in (request.query_params.get("riviste") or "").split(",") if x.strip()]
        return request.query_params.get("file") or "", wanted or None, request.query_params.get("tipo") or "auto"

    def manuscript_endpoint(request, conn, as_html=False):
        from . import compliance, manuscript
        name, wanted, kind = manuscript_args(request)
        try:
            out = manuscript_check(s, conn, name, wanted, kind, searcher.matcher(conn))
        except manuscript.ManuscriptError as exc:
            return JSONResponse({"errore": f"manoscritto non leggibile: {exc}"}, status_code=422)
        if out is None:
            return JSONResponse({"errore": "manoscritto non trovato: sceglilo di nuovo"}, status_code=404)
        if as_html:
            return HTMLResponse(compliance.render_html(out, Path(name).name, datetime.now()))
        return out

    async def simple_post(request: Request, fn):
        """POST that writes no database: token, JSON body, clear messages."""
        if request.headers.get("x-orpheus-token") != token:
            return JSONResponse({"errore": "token mancante o sbagliato"}, status_code=403)
        try:
            body = await request.json()
        except Exception:                                                # noqa: BLE001
            body = None
        if not isinstance(body, dict):
            return JSONResponse({"errore": "richiesta non leggibile"}, status_code=400)
        from starlette.concurrency import run_in_threadpool
        try:
            return JSONResponse(await run_in_threadpool(fn, body))
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            return JSONResponse({"errore": str(exc)}, status_code=422)

    def choose(body):
        path = body.get("percorso")
        if not path:
            path = (chooser or choose_manuscript_dialog)(s)
            if not path:
                return {"scelto": None}
        return {"scelto": remember_manuscript(s, path)}

    async def choose_endpoint(request: Request):
        return await simple_post(request, choose)

    async def forget_endpoint(request: Request):
        return await simple_post(request, lambda b: (forget_manuscript(s, str(b.get("percorso") or "")), {"ok": True})[1])

    async def look_endpoint(request: Request):
        return await simple_post(request, lambda b: set_page_look(s, b))

    async def texts_endpoint(request: Request):
        return await simple_post(request, lambda b: {"testi": set_page_text(s, b.get("originale"), b.get("nuovo"))})

    routes = [
        Route("/", index),
        Route("/api/testi", lambda r: JSONResponse({"testi": page_texts(s), "file": str(Path(s.home) / TEXTS_FILE)})),
        Route("/api/testi", texts_endpoint, methods=["POST"]),
        Route("/api/aspetto", lambda r: JSONResponse(page_look(s))),
        Route("/api/aspetto", look_endpoint, methods=["POST"]),
        Route("/api/manoscritti/scegli", choose_endpoint, methods=["POST"]),
        Route("/api/manoscritti/dimentica", forget_endpoint, methods=["POST"]),
        Route("/api/manoscritti", with_db(lambda r, c: manuscripts(s, c))),
        Route("/api/manoscritti/controllo", with_db(manuscript_endpoint)),
        Route("/api/manoscritti/rapporto", with_db(lambda r, c: manuscript_endpoint(r, c, as_html=True))),
        Route("/api/sigle", lambda r: JSONResponse(abbreviations(s))),
        Route("/api/sottomissioni", with_db(lambda r, c: submissions_view(s, c))),
        Route("/api/sottomissioni/{what}", submissions_endpoint, methods=["POST"]),
        Route("/api/panoramica", with_db(lambda r, c: overview(s, c))),
        Route("/api/articoli", with_db(lambda r, c: articles(s, c))),
        Route("/api/articoli/da-assegnare", with_db(lambda r, c: unassigned(s, c))),
        Route("/api/articoli/assegna", assign_endpoint, methods=["POST"]),
        Route("/api/riviste/elenco", with_db(lambda r, c: journal_list_rows(s, c))),
        Route("/api/riviste/profilo", with_db(lambda r, c: journal_profiles(s, c))),
        Route("/api/riviste/profilo/frasi", with_db(lambda r, c: journal_profile_sentences(
            s, c, r.query_params.get("rivista", ""), r.query_params.get("voce", "")))),
        Route("/api/riviste/scheda", with_db(journal_card_get)),
        Route("/api/riviste/scheda/{what}", journal_card_endpoint, methods=["POST"]),
        Route("/api/riviste/controlla", journal_check_endpoint, methods=["POST"]),
        Route("/api/riviste/salva", journal_save_endpoint, methods=["POST"]),
        Route("/api/riviste/compila", journal_lookup_endpoint, methods=["POST"]),
        Route("/api/articoli/{aid}", with_db(detail)),
        Route("/api/affermazioni", with_db(lambda r, c: claims_list(s, c))),
        Route("/api/riviste", with_db(lambda r, c: journals(s, c))),
        Route("/api/ricerca", with_db(search_endpoint)),
        Route("/api/lavora", with_db(lambda r, c: {**work(s, c), "avvio": runner.snapshot(10 ** 9)})),
        Route("/api/lavora/piano", with_db(lambda r, c: plans.get(s, c, r.query_params.get("ricalcola") == "1"))),
        Route("/api/lavora/avvia", start_lavora, methods=["POST"]),
        Route("/api/lavora/uscita", lavora_output),
        Route("/api/azioni", with_db(actions_endpoint)),
        Route("/api/azioni/avvia", start_action, methods=["POST"]),
        Route("/api/azioni/uscita", lavora_output),
        Route("/api/sistema", with_db(lambda r, c: system(s, c))),
        Route("/api/condivisione", lambda r: JSONResponse(share_info(s))),
        Route("/api/condivisione/esporta", share_export_endpoint, methods=["POST"]),
        Route("/api/condivisione/carica", share_upload_endpoint, methods=["POST"]),
        Route("/api/condivisione/unisci", share_merge_endpoint, methods=["POST"]),
        Route("/api/condivisione/mostra", share_show_endpoint, methods=["POST"]),
        Route("/api/condivisione/scarica", share_download_endpoint),
        Route("/api/eurydike/revisioni", with_db(lambda r, c: eurydike_reviews(c))),
        Route("/api/eurydike/nuova", new_review_endpoint, methods=["POST"]),
        Route("/api/eurydike/strumenti", lambda r: JSONResponse(eurydike_tools())),
        Route("/api/eurydike/revisioni/{rid}/ricerca", with_db(search_files_endpoint)),
        Route("/api/eurydike/revisioni/{rid}/importa", import_endpoint, methods=["POST"]),
        Route("/api/eurydike/revisioni/{rid}", with_db(review_endpoint)),
        Route("/api/eurydike/revisioni/{rid}/record", with_db(records_endpoint)),
        Route("/api/eurydike/schede", with_db(lambda r, c: eurydike_cards(s, c))),
        Route("/api/eurydike/schede/{aid}", with_db(eurydike_card_endpoint)),
        Route("/api/eurydike/schede/{aid}/storia", with_db(lambda r, c: eurydike_history(c, r.path_params["aid"], r.query_params))),
        Route("/api/eurydike/schede/{aid}/decisione", eurydike_write_endpoint, methods=["POST"]),
        Mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static"),
    ]
    app = Starlette(routes=routes, middleware=[Middleware(HostCheck)])
    app.state.token, app.state.runner = token, runner
    return app


def free_port(ports=PORTS) -> int:
    for port in ports:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise OSError(f"nessuna porta libera fra {ports.start} e {ports.stop - 1}")


def serve(s: Settings, *, port: int | None = None, open_browser: bool = True) -> None:
    import uvicorn
    port = port or free_port()
    app = create_app(s)
    url = f"http://127.0.0.1:{port}/"
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))

    def _open():
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.1)
        print(f"Cruscotto di ORPHEUS: {url}  (si chiude chiudendo questa finestra)", flush=True)
        if open_browser:
            import webbrowser
            webbrowser.open(url)

    threading.Thread(target=_open, daemon=True).start()
    server.run()
