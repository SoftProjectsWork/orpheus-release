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

from . import (__version__, audit, card_store, claims, config, content, db, engine, layout, lineage, policy_changes,
               policy_versions, report, semantic, verify_doi)
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
        alerts.append({"tipo": "per te", "testo": f"{x['lavoro']}: {x['quanti']}", "vai": "affermazioni"})
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
    "aggiorna_articoli": {"titolo": "Aggiungi e verifica gli articoli nuovi", "sezione": "articoli", "gruppo": "articoli",
                          "scrive": True, "descrizione": "Legge i PDF nuovi della inbox, poi DOI con Crossref, riviste, "
                          "PubMed, Europe PMC, bibliografie, autori, pezzi di testo e vettori per la ricerca, report."},
    "pacchetti": {"titolo": "Prepara i pacchetti per le schede", "sezione": "articoli", "gruppo": None, "scrive": True,
                  "priorita": True,
                  "passi": ["Sceglie gli articoli senza scheda secondo la priorità (una rivista, lo stesso numero per "
                            "rivista, oppure a sorte)", "Un pacchetto per articolo in rapporti\\pacchetti"],
                  "descrizione": "Prepara il lavoro delle schede degli articoli: le scrive Claude in sessione (strada "
                  "principale) oppure, in emergenza, una chat gratuita. Il testo resta su questo PC."},
    "chat_schede": {"titolo": "Schede con una chat gratuita (emergenza)", "sezione": "articoli", "gruppo": None,
                    "scrive": False, "finestra": True,
                    "passi": ["Prepara il prossimo articolo e copia i messaggi negli appunti, uno alla volta",
                              "Legge la risposta della chat dagli appunti e la controlla",
                              "Se serve, copia negli appunti la correzione da dare alla chat"],
                    "descrizione": "Solo per pochi articoli o quando Claude non può lavorare. Si apre una finestra che "
                    "guida passo per passo: incolli nella chat, copi la risposta, ORPHEUS la controlla. Il database "
                    "non cambia: per quello c'è \"Invia le schede della chat\"."},
    "invia_schede_chat": {"titolo": "Invia le schede della chat", "sezione": "articoli", "gruppo": None,
                          "scrive": True,
                          "passi": ["Copia di sicurezza del database",
                                    "Le schede accettate della chat, con gli stessi controlli delle schede di Claude",
                                    "Registro senza testo degli articoli"],
                          "descrizione": "Mette nel database le schede della chat gratuita già accettate (in "
                          "rapporti\\schede\\chat). Ognuna viene ricontrollata sul testo del database."},
    "aggiorna_riviste": {"titolo": "Aggiorna le riviste", "sezione": "riviste", "gruppo": "riviste", "scrive": True,
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
    "esporta_schede": {"titolo": "Esporta le schede per i colleghi", "sezione": "sistema", "gruppo": None,
                       "scrive": False,
                       "passi": ["Schede delle riviste e degli articoli, metadati, pezzi di testo e vettori",
                                 "Un file .zip nella cartella condivisione dei dati (niente PDF, niente manoscritti)"],
                       "descrizione": "Il file da mandare ai colleghi con gli stessi accessi agli articoli. Contiene "
                       "testo di articoli non aperti: mai su GitHub. Non modifica il database."},
    "importa_schede": {"titolo": "Importa le schede di un collega", "sezione": "sistema", "gruppo": None,
                       "scrive": False, "finestra": True,
                       "passi": ["Prova: cosa entrerebbe e cosa hai già", "Dopo il tuo sì: copia di sicurezza e unione",
                                 "Quello che hai già resta tuo"],
                       "descrizione": "Metti il file .zip ricevuto in ORPHEUS_DATI\\inbox\\condivisione. Si apre una "
                       "finestra che mostra cosa entrerebbe e chiede conferma."},
    "lavora": {"titolo": "Lavora: tutto quello che manca", "sezione": "lavora", "gruppo": "tutto", "scrive": True,
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


def manuscripts(s: Settings, conn) -> dict:
    """The .docx in the manuscripts folder of the data disk and the journals with a card. Read-only."""
    from . import compliance, manuscript
    files = manuscript.list_files(s.home)
    js = compliance.journals_with_cards(conn, s.journal_list())
    return {"cartella": str(manuscript.folder(s.home)),
            "file": [{"nome": f.name, "kb": max(1, round(f.stat().st_size / 1024)),
                      "modificato": datetime.fromtimestamp(f.stat().st_mtime, timezone.utc).isoformat()} for f in files],
            "riviste": [{"sigla": j["sigla"] or j["titolo"], "titolo": j["titolo"], "data_scheda": j["data_scheda"]}
                        for j in js],
            "tipi": compliance.KINDS,
            **_src("cartella manoscritti del disco dei dati (file .docx); journal_card (schede correnti)",
                   max((j["data_scheda"] for j in js), default=None))}


def manuscript_check(s: Settings, conn, name: str, wanted: list[str] | None, kind: str, matcher=None) -> dict | None:
    """compliance-v1 on one file of the folder (only a name listed there: no other path can be asked). Read-only."""
    from . import compliance, manuscript
    path = {f.name: f for f in manuscript.list_files(s.home)}.get(name)
    if path is None:
        return None
    out = compliance.run(conn, path, s.journal_list(), wanted, kind if kind in KINDS_ALLOWED else "auto",
                         matcher=matcher)
    out["file"] = name
    if not out.get("verifica_riferimenti"):           # T-038: the last saved check, no network from the dashboard
        out["verifica_riferimenti"] = compliance.last_reference_check(s.home, out["manoscritto"]["impronta"])
    out.update(_src(f"{name} ({manuscript.MANUSCRIPT_VERSION}) e schede delle riviste (journal_card_value, "
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

def create_app(s: Settings, *, runner: LavoraRunner | None = None, token: str | None = None, allowed_hosts=None,
               opener=None):
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
            name = Path(str(body.get("file") or "")).name
            sigla = str(body.get("rivista") or "")
            names = {f.name for f in manuscript.list_files(s.home)}
            if name not in names:
                return JSONResponse({"errore": "scegli un manoscritto della cartella manoscritti"}, status_code=400)
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
            return JSONResponse({"errore": "manoscritto non trovato nella cartella manoscritti"}, status_code=404)
        if as_html:
            return HTMLResponse(compliance.render_html(out, name, datetime.now()))
        return out

    routes = [
        Route("/", index),
        Route("/api/manoscritti", with_db(lambda r, c: manuscripts(s, c))),
        Route("/api/manoscritti/controllo", with_db(manuscript_endpoint)),
        Route("/api/manoscritti/rapporto", with_db(lambda r, c: manuscript_endpoint(r, c, as_html=True))),
        Route("/api/sigle", lambda r: JSONResponse({"sigle": sorted({x["sigla"] for x in s.journal_list() if x.get("sigla")}),
                                                     "titoli": {x["sigla"]: x.get("titolo") or x["sigla"]
                                                                for x in s.journal_list() if x.get("sigla")},
                                                     "fonte": s.project.storage.journals_csv})),
        Route("/api/panoramica", with_db(lambda r, c: overview(s, c))),
        Route("/api/articoli", with_db(lambda r, c: articles(s, c))),
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
