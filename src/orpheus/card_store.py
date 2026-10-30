"""Article cards in the database (T-019, ADR-0008): first lot, work packets, submit, sample for the hand check.

Flow
  1. `orpheus pacchetti` (pacchetti.cmd): for every article of the lot without an accepted card, one `task` and one
     `workflow_run` (executor claude_session, agent C1 Knowledge Extraction) and the packet file
     ORPHEUS_HOME/rapporti/pacchetti/<article_id>.json: verified metadata, the text by PDF page (and by JATS section for
     the open-access texts), the fields, the rules and the JSON schema. Run again: same packets, no duplicate tasks.
  2. Claude, in a session, reads the packet on the PC and writes rapporti/schede/da_inviare/<article_id>.json.
  3. `orpheus submit` (schede.cmd): every card is checked against the text IN THE DATABASE (not the packet file).
     One transaction per card: the validation_result rows always; article_card + card_value, the run 'succeeded' and
     the task 'done' only when every check passes. Accepted files move to rapporti/schede/accettate/; refused ones stay
     with <name>.motivi.txt beside them. The CLI makes a backup before the first write.
  4. `orpheus schede-csv` / `schede-risultati`: 10 accepted cards drawn with a fixed seed, one row per value, checked
     by hand (giusto S/N): precision per field and overall.

First lot (user's decision of 2026-10-11): the complete articles with an open-access text kept from Europe PMC (10),
then 20 complete articles drawn in the fixed sha256 order of SEED, original studies only (reviews, meta-analyses,
consensus statements, editorials, letters and corrections left out by PubMed publication type and by title).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath

import yaml

from . import audit, cards, layout
from .benchmark import excel_plain, excel_text, pick, read_filled
from .config import ROOT
from .europepmc_survey import is_cc
from .content import COMPLETE, classify_all
from .db import transaction
from .ids import new_id
from .quality import REPORTS_DIR
from .verify_doi import latest_checks

SEED = "orpheus-T-019-v1"
CONTROL_SEED = "orpheus-T-019-controllo-v1"
LOT_OPEN, LOT_SAMPLE, CONTROL_SIZE = 10, 20, 10
NEXT_LOT = 30                # articles of each lot after the first (user's request of 2026-10-16: "fai le schede")
PACKETS_DIR = "pacchetti"
CARDS_DIR = "schede"
TO_SEND, ACCEPTED = "da_inviare", "accettate"
AGENT_NAME = "C1 Knowledge Extraction"
GENERIC_MODEL = "claude-session"
TASK_KEY = "T-019:{version}:{article_id}"

EXCLUDED_TYPES = {"Review", "Systematic Review", "Meta-Analysis", "Consensus Statement", "Practice Guideline", "Guideline",
                  "Editorial", "Comment", "Letter", "Published Erratum", "Retraction of Publication", "Scoping Review"}
EXCLUSIONS_FILE = ROOT / "config" / "schede_esclusi.yaml"
_EXCLUDED_TITLE = re.compile(r"\b(review|meta-analys[ie]s|consensus|position stand|position statement|editorial|"
                             r"corrigendum|erratum|letter to the editor)\b", re.I)


def reports(home: Path) -> Path:
    return Path(home) / REPORTS_DIR


def packets_dir(home: Path) -> Path:
    return reports(home) / PACKETS_DIR


def to_send_dir(home: Path) -> Path:
    return reports(home) / CARDS_DIR / TO_SEND


def accepted_dir(home: Path) -> Path:
    return reports(home) / CARDS_DIR / ACCEPTED


# ---------------------------------------------------------------- lot and text

def _complete_documents(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    """{article_id: the complete-article PDF with most pages (then lowest id)}.

    Only documents whose pages are on this PC: a document received from a colleague (sharing, T-053) comes without its
    pages, so it is never used for packets, segments or claims until the PDF itself is read here."""
    classes = classify_all(conn)
    out: dict[str, sqlite3.Row] = {}
    for d in conn.execute("""SELECT id, article_id, page_count, original_path FROM document
                             WHERE kind = 'article' AND article_id IS NOT NULL
                               AND EXISTS (SELECT 1 FROM document_page p WHERE p.document_id = document.id)
                             ORDER BY page_count DESC, id"""):
        if (classes.get(d["id"]) or {}).get("classe") == COMPLETE and d["article_id"] not in out:
            out[d["article_id"]] = d
    return out


def jats_snapshot(conn: sqlite3.Connection, article_id: str) -> str | None:
    """The JATS snapshot whose sections are kept for the article (latest stored text), or None."""
    row = conn.execute(
        """SELECT f.fetch_snapshot_id FROM fulltext_check f
           WHERE f.article_id = ? AND f.text_stored = 1
             AND EXISTS (SELECT 1 FROM article_section s WHERE s.article_id = f.article_id
                         AND s.source_snapshot_id = f.fetch_snapshot_id)
           ORDER BY f.checked_at DESC, f.rowid DESC LIMIT 1""", (article_id,)).fetchone()
    return row[0] if row else None


def exclusions(path: Path = EXCLUSIONS_FILE) -> dict[str, str]:
    """{doi: reason} of the articles left out of the draw by hand (config/schede_esclusi.yaml)."""
    if not Path(path).exists():
        return {}
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return {str(e["doi"]).strip().lower(): str(e.get("motivo", "")) for e in data.get("esclusi") or []}


def lot(conn: sqlite3.Connection, *, n_open: int = LOT_OPEN, n_sample: int = LOT_SAMPLE, seed: str = SEED,
        excluded_dois: dict[str, str] | None = None) -> dict:
    by_hand = {k.strip().lower(): v for k, v in (exclusions() if excluded_dois is None else excluded_dois).items()}
    docs = _complete_documents(conn)
    full = {r[0]: r for r in conn.execute("SELECT id, title, doi FROM article WHERE access_level = 'full_text'")}
    eligible = [a for a in docs if a in full]
    open_ids = sorted(a for a in eligible if jats_snapshot(conn, a))[:n_open]
    types: dict[str, set[str]] = {}
    for r in conn.execute("SELECT article_id, type_name FROM article_publication_type"):
        types.setdefault(r[0], set()).add(r[1])
    pool, excluded = [], {"tipo_di_pubblicazione": 0, "titolo": 0, "a_mano": 0}
    for a in eligible:
        if a in open_ids:
            continue
        if (full[a]["doi"] or "").lower() in by_hand:
            excluded["a_mano"] += 1
        elif types.get(a, set()) & EXCLUDED_TYPES:
            excluded["tipo_di_pubblicazione"] += 1
        elif _EXCLUDED_TITLE.search(full[a]["title"] or ""):
            excluded["titolo"] += 1
        else:
            pool.append(a)
    drawn = pick(pool, n_sample, seed)
    return {"aperti": open_ids, "a_sorte": drawn, "seme": seed, "articoli_completi": len(eligible),
            "popolazione_a_sorte": len(pool), "esclusi": excluded}


def article_text(conn: sqlite3.Connection, document_id: str | None, snapshot_id: str | None,
                 article_id: str) -> tuple[dict[int, str], dict[int, dict]]:
    pages: dict[int, str] = {}
    if document_id:
        pages = {r[0]: r[1] for r in conn.execute(
            "SELECT page_no, text FROM document_page WHERE document_id = ? ORDER BY page_no", (document_id,))}
    sections: dict[int, dict] = {}
    if snapshot_id:
        for r in conn.execute("""SELECT ord, level, title, text FROM article_section
                                 WHERE article_id = ? AND source_snapshot_id = ? ORDER BY ord""", (article_id, snapshot_id)):
            sections[r[0]] = {"livello": r[1], "titolo": r[2], "testo": r[3]}
    return pages, sections


def text_sha256(pages: dict[int, str], sections: dict[int, dict]) -> str:
    material = json.dumps({"pagine": [[k, pages[k]] for k in sorted(pages)],
                           "sezioni": [[k, sections[k]["titolo"], sections[k]["testo"]] for k in sorted(sections)]},
                          ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _metadata(conn: sqlite3.Connection, article_id: str, document_id: str | None) -> dict:
    a = conn.execute("SELECT id, doi, pmid, pmcid, title, publication_year, licence FROM article WHERE id = ?",
                     (article_id,)).fetchone()
    journal = ""
    if document_id:
        check = latest_checks(conn).get(document_id)
        if check is not None and check["details"]:
            journal = ((json.loads(check["details"]).get("crossref") or {}).get("container_title") or [""])[0]
    authors = [r[0] for r in conn.execute("""SELECT au.display_name FROM article_author aa JOIN author au ON au.id = aa.author_id
                                             WHERE aa.article_id = ? ORDER BY aa.position""", (article_id,))]
    types = [r[0] for r in conn.execute("SELECT type_name FROM article_publication_type WHERE article_id = ? ORDER BY type_name",
                                        (article_id,))]
    mesh = [r[0] for r in conn.execute("""SELECT descriptor_name FROM article_mesh WHERE article_id = ?
                                          ORDER BY major_topic DESC, descriptor_name""", (article_id,))]
    return {"article_id": a["id"], "doi": a["doi"], "pmid": a["pmid"], "pmcid": a["pmcid"], "titolo": a["title"],
            "anno": a["publication_year"], "rivista": journal, "autori": authors, "tipi_di_pubblicazione": types,
            "mesh": mesh, "licenza": a["licence"]}


# ---------------------------------------------------------------- packets

INSTRUCTIONS = """Compila la scheda di questo articolo (contratto cards-v1, ADR-0008) e salvala come JSON in
rapporti\\schede\\da_inviare\\<article_id>.json. ORPHEUS la accetta solo se supera tutti i controlli.

Regole
1. Una riga in "valori" per ogni valore. Campi e significato: vedi "campi". Ogni campo compare almeno una volta: se
   l'articolo non lo riporta, una sola riga con "origin": "not_available" e valore, unita, pagina, sezione, citazione vuoti.
   Obbligatori con un valore: disegno_studio, partecipanti_numero, misura_principale, risultato_chiave, conclusione_autori.
2. Ogni valore ha UNA posizione: "pagina" (numero di pagina del PDF in "testo.pagine") oppure "sezione" (numero di
   sezione in "testo.sezioni", solo se presente). Mai tutte e due.
3. "citazione": un pezzo CONTINUO copiato da quella pagina o sezione (12-500 caratteri), senza puntini e senza
   parafrasi. Spazi, a capo e trattini di sillabazione possono essere diversi; tutto il resto deve essere uguale.
4. "valore": breve, nella lingua dell'articolo. Ogni numero del valore deve comparire nella citazione esattamente come
   scritto (stesso segno e decimali: "-0.81", ".01"). Niente numeri calcolati da te (origin "recalculated" non esiste in v1).
5. "unita": solo se c'è, e scritta nella citazione (es. "years", "m", "%", "°"). Per l'età è obbligatoria; per
   partecipanti_numero non si mette.
6. "origin": "reported" se il valore è scritto nell'articolo; "interpreted" se lo deduci dalla citazione (es. il disegno
   dello studio non nominato); "not_available" se l'articolo non lo riporta.
7. "gruppo": il gruppo, sesso o condizione a cui si riferisce il valore, se non è tutto il campione.
8. Copia "workflow_run_id" e "article_id" da questo pacchetto; "modello": il modello che compila (es. claude-opus-5-5).
9. Le tabelle del PDF hanno il testo spezzato su più righe: copia la citazione nell'ordine in cui appare nel testo della pagina.
"""

EXAMPLE = {
    "formato": cards.CARD_FORMAT, "versione": cards.CARD_VERSION, "workflow_run_id": "run_…", "article_id": "art_…",
    "modello": "claude-opus-5-5",
    "valori": [
        {"campo": "partecipanti_numero", "valore": "15", "pagina": 2, "citazione": "Fifteen children (9 male, 6 female) participated",
         "origin": "reported"},
        {"campo": "partecipanti_eta", "valore": "9.95 (1.35)", "unita": "years", "pagina": 3,
         "citazione": "Age (years) 9.95 (1.35)", "origin": "reported"},
        {"campo": "misura_principale", "nome": "stride length", "valore": "2.39 (0.32)", "unita": "m", "pagina": 3,
         "citazione": "Stride length (m) 2.39 (0.32)", "origin": "reported"},
        {"campo": "limite", "origin": "not_available"},
    ],
}


def _ensure_project(conn: sqlite3.Connection, name: str, version: str) -> str:
    row = conn.execute("SELECT id FROM project ORDER BY created_at, id LIMIT 1").fetchone()
    if row:
        return row[0]
    pid = new_id("project")
    conn.execute("INSERT INTO project (id, name, version, description) VALUES (?, ?, ?, ?)",
                 (pid, name, version, "created by `orpheus pacchetti` (task and workflow_run need a project)"))
    return pid


def _ensure_agent(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT id FROM agent WHERE name = ?", (AGENT_NAME,)).fetchone()
    if row:
        return row[0]
    aid = new_id("agent")
    conn.execute("""INSERT INTO agent (id, name, layer, role, contract_version, status, config)
                    VALUES (?, ?, 'C', ?, ?, 'implemented', ?)""",
                 (aid, AGENT_NAME, "Testo dell'articolo -> scheda con valori ancorati a pagina o sezione e citazione",
                  cards.CARD_VERSION, json.dumps({"executor": "claude_session", "modulo": "orpheus.cards"})))
    return aid


def _ensure_model(conn: sqlite3.Connection, model_ref: str, executor: str = "claude_session") -> str:
    row = conn.execute("SELECT id FROM model WHERE executor = ? AND model_ref = ?", (executor, model_ref)).fetchone()
    if row:
        return row[0]
    mid = new_id("model")
    conn.execute("INSERT INTO model (id, executor, model_ref, status) VALUES (?, ?, ?, 'candidate')",
                 (mid, executor, model_ref))
    return mid


def current_card(conn: sqlite3.Connection, article_id: str, version: str = cards.CARD_VERSION) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM article_card WHERE article_id = ? AND card_version = ? AND status = 'current'",
                        (article_id, version)).fetchone()


def _packet(meta: dict, task_id: str, run: dict, pages: dict[int, str], sections: dict[int, dict]) -> dict:
    return {
        "formato": cards.PACKET_FORMAT, "versione_scheda": cards.CARD_VERSION,
        "task_id": task_id, "workflow_run_id": run["id"], "article_id": meta["article_id"],
        "emesso_il": run["started_at"], "hash_testo": run["parameters"]["text_sha256"],
        "documento": run["parameters"]["document_id"], "testo_aperto_jats": run["parameters"]["jats_snapshot_id"],
        "articolo": meta,
        "istruzioni": INSTRUCTIONS,
        "campi": {k: {"obbligatorio": v["obbligatorio"], "descrizione": v["descrizione"]} for k, v in cards.FIELDS.items()},
        "esempio": EXAMPLE,
        "schema_json": cards.json_schema(),
        "testo": {"pagine": [{"pagina": k, "testo": pages[k]} for k in sorted(pages)],
                  "sezioni": [{"sezione": k, "livello": s["livello"], "titolo": s["titolo"], "testo": s["testo"]}
                              for k, s in sorted(sections.items())]},
    }


def _write_if_changed(path: Path, data: bytes) -> bool:
    if path.exists() and path.read_bytes() == data:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    return True


@dataclass
class PacketResult:
    issued: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    problems: list[dict] = field(default_factory=list)
    created_runs: int = 0


def has_open_packet(conn: sqlite3.Connection, article_id: str) -> bool:
    key = TASK_KEY.format(version=cards.CARD_VERSION, article_id=article_id)
    return conn.execute("""SELECT 1 FROM workflow_run w JOIN task t ON t.id = w.task_id
                           WHERE t.idempotency_key = ? AND w.status = 'running'""", (key,)).fetchone() is not None


def open_packet_articles(conn: sqlite3.Connection) -> list[str]:
    """Articles with an open card packet (a running claude_session run of T-019)."""
    prefix = TASK_KEY.format(version=cards.CARD_VERSION, article_id="")
    return [r[0][len(prefix):] for r in conn.execute(
        """SELECT DISTINCT t.idempotency_key FROM workflow_run w JOIN task t ON t.id = w.task_id
           WHERE w.status = 'running' AND t.idempotency_key LIKE ?""", (prefix + "%",))]


def next_lot(conn: sqlite3.Connection, size: int = NEXT_LOT, *, seed: str = SEED,
             excluded_dois: dict[str, str] | None = None) -> dict:
    """The lots after the first (user's request of 2026-10-16): the next `size` original studies without an accepted
    card and without an open packet, in the fixed order of the same draw (seed of T-019), open texts included. Same
    exclusions as the first lot (publication type, title, by hand). Read-only."""
    by_hand = {k.strip().lower(): v for k, v in (exclusions() if excluded_dois is None else excluded_dois).items()}
    docs = _complete_documents(conn)
    full = {r[0]: r for r in conn.execute("SELECT id, title, doi FROM article WHERE access_level = 'full_text'")}
    types: dict[str, set[str]] = {}
    for r in conn.execute("SELECT article_id, type_name FROM article_publication_type"):
        types.setdefault(r[0], set()).add(r[1])
    pool = [a for a in docs if a in full and (full[a]["doi"] or "").lower() not in by_hand
            and not (types.get(a, set()) & EXCLUDED_TYPES) and not _EXCLUDED_TITLE.search(full[a]["title"] or "")]
    ranked = sorted(pool, key=lambda i: (hashlib.sha256(f"{seed}:{i}".encode()).hexdigest(), i))
    todo = [a for a in ranked if not current_card(conn, a) and not has_open_packet(conn, a)]
    return {"articoli": todo[:size], "seme": seed, "studi_originali": len(pool), "senza_scheda": len(todo),
            "restano_dopo": max(0, len(todo) - size)}


PRIORITY_MODES = ("casuale", "rivista", "equo")


def priority_lot(conn: sqlite3.Connection, journal_list: list[dict] | None, mode: str = "casuale", *,
                 sigla: str | None = None, size: int = 10, seed: str = SEED,
                 excluded_dois: dict[str, str] | None = None) -> dict:
    """Which articles get the next packets (user's request of 2026-10-17 19:13). Same pool and order as next_lot (the
    draw of T-019); then:
    - casuale: as before, the next ones of the draw;
    - rivista: only the articles of one journal of the list (sigla), in the order of the draw;
    - equo: one per journal in turn (journals in the order of the list), in the order of the draw; a journal with
      nothing left is skipped, so the others fill its place (not too rigid). Read-only."""
    mode = mode if mode in PRIORITY_MODES else "casuale"
    base = next_lot(conn, size=10 ** 6, seed=seed, excluded_dois=excluded_dois)
    todo = base["articoli"]
    from . import policy_versions
    by_sigla = policy_versions.journal_by_sigla(conn, journal_list or [])      # {sigla (lower): journal_id}
    sigla_of = {jid: sg for sg, jid in by_sigla.items()}
    jour = {r[0]: r[1] for r in conn.execute("SELECT id, journal_id FROM article")}
    order = [sg for sg in ((r.get("sigla") or "").lower() for r in (journal_list or [])) if sg in by_sigla]
    per_journal: dict[str, list[str]] = {}
    for a in todo:
        per_journal.setdefault(sigla_of.get(jour.get(a), ""), []).append(a)
    available = {sg.upper(): len(per_journal.get(sg, [])) for sg in order}
    if per_journal.get(""):
        available["altre"] = len(per_journal[""])
    if mode == "rivista":
        sg = (sigla or "").lower()
        if sg not in by_sigla:
            return {"modo": mode, "rivista": sigla, "articoli": [], "senza_scheda": len(todo),
                    "per_rivista": available, "problema": f"rivista {sigla!r} non trovata nell'elenco delle riviste"}
        chosen = per_journal.get(sg, [])[:size]
    elif mode == "equo":
        queues = [list(per_journal.get(sg, [])) for sg in order] + [list(per_journal.get("", []))]
        chosen = []
        while len(chosen) < size and any(queues):
            for q in queues:
                if q and len(chosen) < size:
                    chosen.append(q.pop(0))
    else:
        chosen = todo[:size]
    count: dict[str, int] = {}
    for a in chosen:
        sg_a = sigla_of.get(jour.get(a))
        k = sg_a.upper() if sg_a else "altre"
        count[k] = count.get(k, 0) + 1
    return {"modo": mode, "rivista": (sigla or None) if mode == "rivista" else None, "articoli": chosen,
            "scelti_per_rivista": count, "per_rivista": available, "senza_scheda": len(todo),
            "restano_dopo": max(0, len(todo) - len(chosen)), "seme": seed}


def issue_packets(conn: sqlite3.Connection, home: Path, article_ids: list[str], *, project_name: str = "ORPHEUS",
                  project_version: str = "", actor: str = "orpheus pacchetti") -> PacketResult:
    """Tasks, runs and packet files for the articles without an accepted card. Idempotent."""
    res = PacketResult()
    docs = _complete_documents(conn)
    plan = []
    for aid in article_ids:
        if not conn.execute("SELECT 1 FROM article WHERE id = ?", (aid,)).fetchone():
            res.problems.append({"article_id": aid, "problema": "articolo non trovato nel database"})
            continue
        if current_card(conn, aid):
            res.skipped.append({"article_id": aid, "motivo": "scheda già accettata"})
            continue
        doc = docs.get(aid)
        snap = jats_snapshot(conn, aid)
        if doc is None and snap is None:
            res.problems.append({"article_id": aid, "problema": "nessun PDF di articolo completo e nessun testo aperto"})
            continue
        pages, sections = article_text(conn, doc["id"] if doc else None, snap, aid)
        plan.append((aid, doc["id"] if doc else None, snap, pages, sections, text_sha256(pages, sections)))
    written = []
    with transaction(conn):
        if plan:
            project_id = _ensure_project(conn, project_name, project_version)
            agent_id = _ensure_agent(conn)
            model_id = _ensure_model(conn, GENERIC_MODEL)
        for aid, doc_id, snap, pages, sections, sha in plan:
            key = TASK_KEY.format(version=cards.CARD_VERSION, article_id=aid)
            task = conn.execute("SELECT id FROM task WHERE idempotency_key = ?", (key,)).fetchone()
            if task is None:
                task_id = new_id("task")
                conn.execute("""INSERT INTO task (id, project_id, title, priority, status, owner_agent_id, expected_output,
                                                  acceptance_criteria, idempotency_key)
                                VALUES (?, ?, ?, 1, 'ready', ?, ?, ?, ?)""",
                             (task_id, project_id, f"Scheda {cards.CARD_VERSION} dell'articolo {aid}", agent_id,
                              f"rapporti/schede/da_inviare/{aid}.json (formato {cards.CARD_FORMAT})",
                              "supera tutti i controlli di cards-v1 (ADR-0008) con orpheus submit", key))
            else:
                task_id = task[0]
                conn.execute("UPDATE task SET status = 'ready' WHERE id = ? AND status NOT IN ('done', 'running')", (task_id,))
            run = None
            for r in conn.execute("""SELECT * FROM workflow_run WHERE task_id = ? AND status = 'running'
                                     ORDER BY started_at DESC, rowid DESC""", (task_id,)):
                params = json.loads(r["parameters"])
                if params.get("text_sha256") == sha and params.get("document_id") == doc_id \
                        and params.get("jats_snapshot_id") == snap and run is None:
                    run = {"id": r["id"], "started_at": r["started_at"], "parameters": params}
                else:                                  # the text changed since: that packet is no longer valid
                    conn.execute("""UPDATE workflow_run SET status = 'cancelled', finished_at = ?,
                                    error = 'testo cambiato: pacchetto riemesso' WHERE id = ?""", (audit.utc_now(), r["id"]))
            if run is None:
                params = {"card_version": cards.CARD_VERSION, "article_id": aid, "document_id": doc_id,
                          "jats_snapshot_id": snap, "text_sha256": sha, "pagine": len(pages), "sezioni": len(sections),
                          "packet_file": f"{REPORTS_DIR}/{PACKETS_DIR}/{aid}.json"}
                run = {"id": new_id("workflow_run"), "started_at": audit.utc_now(), "parameters": params}
                conn.execute("""INSERT INTO workflow_run (id, task_id, agent_id, executor, model_id, started_at, status,
                                                          parameters, validation_status)
                                VALUES (?, ?, ?, 'claude_session', ?, ?, 'running', ?, 'pending')""",
                             (run["id"], task_id, agent_id, model_id, run["started_at"],
                              json.dumps(params, ensure_ascii=False, sort_keys=True)))
                res.created_runs += 1
            written.append((aid, task_id, run, doc_id, pages, sections))
        if plan:
            audit.record(conn, actor=actor, action="card_packets_issued", payload={
                "card_version": cards.CARD_VERSION, "articles": [w[0] for w in written], "new_runs": res.created_runs},
                in_transaction=True)
    for aid, task_id, run, doc_id, pages, sections in written:
        meta = _metadata(conn, aid, doc_id)
        data = json.dumps(_packet(meta, task_id, run, pages, sections), ensure_ascii=False, indent=1).encode("utf-8")
        path = packets_dir(home) / f"{aid}.json"
        changed = _write_if_changed(path, data)
        res.issued.append({"article_id": aid, "workflow_run_id": run["id"], "file": str(path), "pagine": len(pages),
                           "sezioni": len(sections), "nuovo_file": changed, "titolo": meta["titolo"],
                           "licenza": meta["licenza"], "testo_aperto_cc": open_cc(sections, meta["licenza"])})
    return res


def status(conn: sqlite3.Connection) -> dict:
    runs = {r[0]: r[1] for r in conn.execute(
        """SELECT validation_status, count(*) FROM workflow_run w JOIN task t ON t.id = w.task_id
           WHERE w.executor = 'claude_session' AND w.status = 'running' AND t.idempotency_key LIKE 'T-019:%'
           GROUP BY 1""")}
    return {"schede_accettate": conn.execute("SELECT count(*) FROM article_card WHERE status = 'current'").fetchone()[0],
            "valori": conn.execute("""SELECT count(*) FROM card_value v JOIN article_card c ON c.id = v.card_id
                                      WHERE c.status = 'current'""").fetchone()[0],
            "pacchetti_aperti": sum(runs.values()), "pacchetti_con_invio_rifiutato": runs.get("fail", 0)}


def open_cc(sections: dict, licence: str | None) -> bool:
    """The packet may leave the PC (branch pacchetti-cc on GitHub, decision of the user of 2026-10-12): only when the
    article's open-access JATS text is kept (stored only with a Creative Commons licence written in the JATS, 0006)
    and the article's licence is Creative Commons."""
    return bool(sections) and is_cc(licence)


# ---------------------------------------------------------------- cards written by the hourly loop (cloud)

CLOUD_DIR = "schede_cloud"           # folder in the repository (branch main) where the loop writes the cards


def cloud_cards_from_git(root: Path = ROOT, run=None) -> tuple[dict[str, bytes], str | None]:
    """{file name: content} of the cards the loop wrote in origin/main:schede_cloud/, read with the git of this PC
    (fetch + ls-tree + show: the working tree of the code is not touched). (cards, problem): on any git or network
    problem no cards and the reason (the local cards are sent anyway)."""
    import subprocess
    run = run or (lambda args: subprocess.run(["git", "-C", str(root), *args], capture_output=True, timeout=120))
    try:
        if run(["fetch", "-q", "origin", "main"]).returncode != 0:
            return {}, "GitHub non raggiungibile: schede del loop non scaricate"
        ls = run(["ls-tree", "--name-only", "origin/main", f"{CLOUD_DIR}/"])
        if ls.returncode != 0:
            return {}, "cartella delle schede del loop non trovata"
        out = {}
        for name in ls.stdout.decode("utf-8", "replace").split():
            if re.fullmatch(rf"{CLOUD_DIR}/art_[0-9A-HJKMNP-TV-Z]{{26}}\.json", name):
                show = run(["show", f"origin/main:{name}"])
                if show.returncode == 0:
                    out[name.split("/", 1)[1]] = show.stdout
        return out, None
    except (OSError, ValueError) as exc:                  # git missing, timeout
        return {}, f"git non disponibile: {exc}"


def import_cloud_cards(home: Path, found: dict[str, bytes]) -> dict:
    """Copy the loop's cards into schede/da_inviare: a card already accepted is skipped; a card waiting there is
    replaced only when it was refused (<name>.motivi.txt) and the loop's version is different."""
    dest = to_send_dir(home)
    dest.mkdir(parents=True, exist_ok=True)
    copied, skipped = [], []
    for name, data in sorted(found.items()):
        target, reasons = dest / name, dest / (Path(name).stem + ".motivi.txt")
        if (accepted_dir(home) / name).exists():
            skipped.append({"file": name, "motivo": "già accettata"})
        elif target.exists() and (not reasons.exists() or target.read_bytes() == data):
            skipped.append({"file": name, "motivo": "già in da_inviare"})
        else:
            _write_if_changed(target, data)
            copied.append(name)
    return {"copiate": copied, "saltate": skipped}


# ---------------------------------------------------------------- submit

@dataclass
class Submission:
    path: Path
    raw: bytes = b""
    sha256: str = ""
    data: object = None
    run: sqlite3.Row | None = None
    params: dict = field(default_factory=dict)
    stop: str | None = None          # refused before any check can be recorded (no database write)

    @property
    def reaches_db(self) -> bool:
        return self.stop is None


def prepare(conn: sqlite3.Connection, path: Path) -> Submission:
    s = Submission(Path(path))
    try:
        s.raw = s.path.read_bytes()
    except OSError as exc:
        s.stop = f"file non leggibile: {exc}"
        return s
    s.sha256 = hashlib.sha256(s.raw).hexdigest()
    try:
        s.data = json.loads(s.raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        s.stop = f"non è un JSON valido: {exc}"
        return s
    run_id = s.data.get("workflow_run_id") if isinstance(s.data, dict) else None
    if not isinstance(run_id, str):
        s.stop = "manca workflow_run_id (copialo dal pacchetto)"
        return s
    s.run = conn.execute("SELECT * FROM workflow_run WHERE id = ?", (run_id,)).fetchone()
    if s.run is None:
        s.stop = f"esecuzione {run_id} non trovata in questo database"
        return s
    s.params = json.loads(s.run["parameters"] or "{}")
    if s.run["executor"] != "claude_session" or s.params.get("card_version") != cards.CARD_VERSION:
        s.stop = f"l'esecuzione {run_id} non è un pacchetto di schede {cards.CARD_VERSION}"
    elif s.run["status"] == "succeeded":
        card = conn.execute("SELECT file_sha256 FROM article_card WHERE workflow_run_id = ?", (run_id,)).fetchone()
        s.stop = ("scheda già accettata (stesso file)" if card and card[0] == s.sha256
                  else "per questo pacchetto c'è già una scheda accettata")
    elif s.run["status"] != "running":
        s.stop = f"il pacchetto non è più valido (esecuzione {s.run['status']}): rilancia pacchetti.cmd e usa il pacchetto nuovo"
    return s


def _packet_check(conn: sqlite3.Connection, s: Submission) -> cards.CheckResult:
    problems = []
    if not isinstance(s.data, dict) or s.data.get("article_id") != s.params.get("article_id"):
        problems.append(f"article_id diverso da quello del pacchetto ({s.params.get('article_id')})")
    pages, sections = article_text(conn, s.params.get("document_id"), s.params.get("jats_snapshot_id"),
                                   s.params.get("article_id"))
    if text_sha256(pages, sections) != s.params.get("text_sha256"):
        problems.append("il testo dell'articolo nel database è cambiato dopo il pacchetto: rilancia pacchetti.cmd")
    return cards.CheckResult("pacchetto", not problems, problems)


def record(conn: sqlite3.Connection, s: Submission, *, actor: str = "orpheus submit",
           executor: str = "claude_session") -> dict:
    """Check one card and write the outcome in one transaction. Returns a summary for the user."""
    assert s.reaches_db and s.run is not None
    aid = s.params["article_id"]
    pages, sections = article_text(conn, s.params.get("document_id"), s.params.get("jats_snapshot_id"), aid)
    outcome = cards.validate(s.data, pages, {k: v["testo"] for k, v in sections.items()},
                             packet_check=_packet_check(conn, s))
    attempt = 1 + conn.execute("""SELECT count(*) FROM validation_result WHERE workflow_run_id = ? AND check_name = 'formato'""",
                               (s.run["id"],)).fetchone()[0]
    names = {c.name for c in outcome.checks}
    checks = outcome.checks + [cards.CheckResult(n, False, ["non eseguito: il formato non è valido"])
                               for n in cards.CHECKS if n not in names]
    now = audit.utc_now()
    out = {"file": s.path.name, "article_id": aid, "workflow_run_id": s.run["id"], "tentativo": attempt,
           "esito": "accettata" if outcome.accepted else "rifiutata",
           "controlli": {c.name: ("ok" if c.ok else "non superato") for c in checks}}
    with transaction(conn):
        for c in checks:
            conn.execute("""INSERT INTO validation_result (id, workflow_run_id, check_name, outcome, validator, details)
                            VALUES (?, ?, ?, ?, 'deterministic', ?)""",
                         (new_id("validation_result"), s.run["id"], c.name, "pass" if c.ok else "fail",
                          json.dumps({"file": s.path.name, "file_sha256": s.sha256, "tentativo": attempt,
                                      "regole": cards.CARD_VERSION, "problemi": c.problems[:50],
                                      "problemi_totali": len(c.problems)}, ensure_ascii=False)))
        if outcome.accepted:
            card = outcome.card
            conn.execute("UPDATE article_card SET status = 'superseded' WHERE article_id = ? AND card_version = ? "
                         "AND status = 'current'", (aid, cards.CARD_VERSION))
            card_id = new_id("article_card")
            conn.execute("""INSERT INTO article_card (id, article_id, workflow_run_id, card_version, document_id,
                                                      jats_snapshot_id, text_sha256, file_sha256, model_ref)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                         (card_id, aid, s.run["id"], cards.CARD_VERSION, s.params.get("document_id"),
                          s.params.get("jats_snapshot_id"), s.params["text_sha256"], s.sha256, card.modello))
            for i, v in enumerate(card.valori, 1):
                na = v.origin == "not_available"
                conn.execute("""INSERT INTO card_value (card_id, ord, field, name, value, unit, group_label, page_no,
                                                        section_ord, quote, origin, note)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                             (card_id, i, v.campo, v.nome, None if na else v.valore, None if na else v.unita, v.gruppo,
                              None if na else v.pagina, None if na else v.sezione, None if na else v.citazione,
                              v.origin, v.nota))
            model_id = _ensure_model(conn, card.modello, executor)
            # the packet run was opened for Claude; a card from a free chat (ADR-0011) records who really wrote it
            conn.execute("""UPDATE workflow_run SET status = 'succeeded', finished_at = ?, validation_status = 'pass',
                            model_id = ?, executor = ? WHERE id = ?""",
                         (max(now, s.run["started_at"]), model_id, executor, s.run["id"]))
            conn.execute("UPDATE task SET status = 'done' WHERE id = ?", (s.run["task_id"],))
            audit.record(conn, actor=actor, action="card_accepted", entity_type="article_card", entity_id=card_id,
                         payload={"article_id": aid, "workflow_run_id": s.run["id"], "values": len(card.valori),
                                  "file_sha256": s.sha256, "card_version": cards.CARD_VERSION, "model": card.modello,
                                  "executor": executor},
                         in_transaction=True)
            out["id_scheda"] = card_id
            out["valori"] = len(card.valori)
        else:
            conn.execute("UPDATE workflow_run SET validation_status = 'fail' WHERE id = ?", (s.run["id"],))
            audit.record(conn, actor=actor, action="card_rejected", entity_type="workflow_run", entity_id=s.run["id"],
                         payload={"article_id": aid, "file_sha256": s.sha256, "attempt": attempt,
                                  "failed": [c.name for c in checks if not c.ok]}, in_transaction=True)
            problems = [f"[{c.name}] {p}" for c in checks if not c.ok for p in c.problems]
            out["motivi"] = problems[:40]
            out["motivi_totali"] = len(problems)
    return out


def reasons_text(summary: dict, stop: str | None = None) -> str:
    lines = [f"ORPHEUS - scheda rifiutata - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
             f"file: {summary.get('file')}", ""]
    if stop:
        lines.append(f"- {stop}")
    for m in summary.get("motivi", []):
        lines.append(f"- {m}")
    if summary.get("motivi_totali", 0) > len(summary.get("motivi", [])):
        lines.append(f"... e altri {summary['motivi_totali'] - len(summary['motivi'])} (vedi validation_result nel database)")
    lines += ["", "Correggi la scheda e rilancia schede.cmd."]
    return "\r\n".join(lines) + "\r\n"


def file_after(s: Submission, summary: dict, home: Path) -> str | None:
    """Accepted -> moved to schede/accettate (never overwriting); refused -> <name>.motivi.txt beside it."""
    motivi = s.path.with_name(s.path.stem + ".motivi.txt")
    if summary.get("esito") == "accettata" or (s.stop or "").startswith("scheda già accettata"):
        dest_dir = accepted_dir(home)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / s.path.name
        if dest.exists():
            dest = dest_dir / f"{s.path.stem}_{s.sha256[:8]}{s.path.suffix}"
        if not dest.exists():
            s.path.replace(dest)
        elif s.path.exists() and dest.read_bytes() == s.raw:
            s.path.unlink()
        if motivi.exists():
            motivi.unlink()
        return str(dest)
    motivi.write_text(reasons_text(summary, s.stop), encoding="utf-8")
    return str(motivi)


def check_with_packet(card_data, packet: dict) -> cards.Outcome:
    """Same content checks on the text of the packet file, without a database (for Claude before saving)."""
    pages = {p["pagina"]: p["testo"] for p in packet["testo"]["pagine"]}
    sections = {s["sezione"]: s["testo"] for s in packet["testo"]["sezioni"]}
    problems = []
    if not isinstance(card_data, dict) or card_data.get("workflow_run_id") != packet.get("workflow_run_id"):
        problems.append("workflow_run_id diverso da quello del pacchetto")
    if not isinstance(card_data, dict) or card_data.get("article_id") != packet.get("article_id"):
        problems.append("article_id diverso da quello del pacchetto")
    return cards.validate(card_data, pages, sections, packet_check=cards.CheckResult("pacchetto", not problems, problems))


# ---------------------------------------------------------------- hand check (sample of accepted cards)

CONTROL_PREFIX = "schede_controllo"
CONTROL_FILL = ["giusto", "note"]
CONTROL_COLUMNS = ["n", "id_scheda", "ord", "articolo", "doi", "file", "cartella", "pagina", "sezione", "campo", "nome",
                   "valore", "unita", "gruppo", "origin", "citazione", "cosa_controllare", *CONTROL_FILL]
_CONTROL_ORIGINAL = re.compile(rf"^{CONTROL_PREFIX}_(\d+)_\d{{4}}-\d{{2}}-\d{{2}}(_\d{{6}})?$")


def control_rows(conn: sqlite3.Connection, size: int = CONTROL_SIZE, seed: str = CONTROL_SEED,
                 locator=None) -> tuple[list[dict], int]:
    current = {r["article_id"]: r for r in conn.execute("SELECT * FROM article_card WHERE status = 'current'")}
    chosen = pick(list(current), size, seed)
    rows: list[dict] = []
    for aid in chosen:
        c = current[aid]
        art = conn.execute("SELECT title, doi FROM article WHERE id = ?", (aid,)).fetchone()
        doc = conn.execute("SELECT original_path, file_sha256 FROM document WHERE id = ?", (c["document_id"],)).fetchone() \
            if c["document_id"] else None
        where = PurePosixPath(doc["original_path"].replace("\\", "/")) if doc else None
        if doc and locator is not None:              # T-054: where the PDF is now
            now = locator.find(doc["original_path"], doc["file_sha256"])
            if now is not None:
                where = PurePosixPath(layout.shown_path(locator.home, now))
        for v in conn.execute("SELECT * FROM card_value WHERE card_id = ? ORDER BY ord", (c["id"],)):
            todo = ("l'articolo davvero non lo riporta" if v["origin"] == "not_available"
                    else "il valore è quello dell'articolo per questo campo (e il gruppo è giusto)")
            rows.append({"id_scheda": c["id"], "ord": v["ord"], "articolo": art["title"], "doi": art["doi"],
                         "file": where.name if where else "", "cartella": "\\".join(where.parent.parts) if where else "",
                         "pagina": v["page_no"] or "", "sezione": "" if v["section_ord"] is None else v["section_ord"],
                         "campo": v["field"], "nome": v["name"] or "", "valore": excel_text(v["value"]), "unita": v["unit"] or "",
                         "gruppo": v["group_label"] or "", "origin": v["origin"], "citazione": v["quote"] or "",
                         "cosa_controllare": todo, "giusto": "", "note": ""})
    for n, r in enumerate(rows, 1):
        r["n"] = n
    return rows, len(chosen)


def write_control_csv(rows: list[dict], n_cards: int, home: Path, now: datetime | None = None) -> tuple[Path, bool]:
    now = now or datetime.now()
    buf = io.StringIO(newline="")
    w = csv.DictWriter(buf, fieldnames=CONTROL_COLUMNS, delimiter=";", lineterminator="\r\n")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    data = buf.getvalue().encode("utf-8-sig")
    folder = reports(home)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{CONTROL_PREFIX}_{n_cards}_{now.strftime('%Y-%m-%d')}.csv"
    if path.exists():
        if path.read_bytes() == data:
            return path, False
        path = path.with_name(f"{path.stem}_{now.strftime('%H%M%S')}.csv")
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    return path, True


def latest_filled_control(home: Path) -> Path | None:
    folder = reports(home)
    if not folder.is_dir():
        return None
    found = [p for p in folder.glob(f"{CONTROL_PREFIX}_*.csv") if not _CONTROL_ORIGINAL.match(p.stem)]
    return max(found, key=lambda p: (p.stat().st_mtime, p.name)) if found else None


def _verdict(cell) -> str:
    v = (cell or "").strip().rstrip(".").strip().lower()
    return "" if not v else "S" if v in {"s", "si", "sì", "sí"} else "N" if v in {"n", "no"} else "?"


def _precision(verdicts: list[str]) -> dict:
    s, n = verdicts.count("S"), verdicts.count("N")
    return {"giusti": s, "sbagliati": n, "non_compilati": verdicts.count(""), "non_validi": verdicts.count("?"),
            "precisione": round(s / (s + n), 4) if s + n else None}


def score_control(path: Path) -> dict:
    rows, info = read_filled(path, need={"id_scheda", "ord", "giusto"})
    verdicts = [_verdict(r.get("giusto")) for r in rows]
    by = lambda key: {k: _precision([v for v, r in zip(verdicts, rows) if r.get(key) == k])  # noqa: E731
                      for k in dict.fromkeys(r.get(key, "") for r in rows)}
    cards_ok = {}
    for v, r in zip(verdicts, rows):
        cards_ok.setdefault(r.get("id_scheda", ""), []).append(v)
    whole = [m for m in cards_ok.values() if all(x in ("S", "N") for x in m)]
    return {"file": str(path), **info, "righe": len(rows), "schede": len(cards_ok),
            "precisione_complessiva": _precision(verdicts), "per_campo": by("campo"), "per_origin": by("origin"),
            "schede_tutte_giuste": {"compilate": len(whole), "tutte_giuste": sum(1 for m in whole if set(m) == {"S"})},
            "sbagliati": [{"n": r.get("n"), "id_scheda": r.get("id_scheda"), "campo": r.get("campo"),
                           "valore": excel_plain(r.get("valore")), "note": (r.get("note") or "").strip()}
                          for v, r in zip(verdicts, rows) if v == "N"],
            "non_validi": [{"n": r.get("n"), "giusto": r.get("giusto")} for v, r in zip(verdicts, rows) if v == "?"]}
