"""T-057c — level 2 of "learning from the published articles", inside the shared card cards-v2: the objective and the
design of every article (contract obiettivi-v1), so that the statistics found by stats-terms-v1 can be read by
objective ("two time points, same subjects -> paired test").

Decisions of the user (2026-10-27 17:0x):
  a) objective types: comparison between groups, effect of an intervention (pre-post, with or without control),
     association/prediction, reliability/validity, descriptive (cards_v2.OBJECTIVE_TYPES);
  b) design: time points one / two / more than two, groups one / two / more than two, same subjects yes/no, plus
     crossover yes/no as a separate field;
  c) which articles first: those on sport, exercise and biomechanics (TOPIC), 10 packets per session.

Flow (same as the cards of T-019, ADR-0008; nothing reaches the real database without a command of the user):
  1. `orpheus obiettivi --pacchetti 10` (obiettivi.cmd): one task and one workflow_run (executor claude_session) per
     article and the packet rapporti/pacchetti_obiettivi/<article_id>.json with the PDF pages, the fields, the allowed
     values and the stats-terms-v1 items already found in the article (help, not answers).
  2. Claude, in a session, writes rapporti/schede_obiettivi/da_inviare/<article_id>.json (format orpheus-scheda-v2, only
     the fields of LEVEL2_FIELDS) and checks it with `orpheus obiettivi --controlla SCHEDA PACCHETTO` (no database).
  3. `orpheus obiettivi --invia` (obiettivi.cmd): every card checked again against the text IN THE DATABASE; accepted
     values enter card_item as proposals (`ai`), the user confirms or corrects them like every cards-v2 value.

Precision first: every value with page and exact sentence (here a sentence that is not found exactly refuses the card,
stricter than the extraction fields of EURYDIKE); choice values only from the list; a few rules of coherence between the
design fields (e.g. crossover -> same subjects). The model never counts: the crossing with the tests is done in code
(journal_profile.tests_by_objective).
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from . import audit, card_store, cards, stats_terms
from .cards import CheckResult, Outcome
from .db import transaction
from .eurydike import cards_v2
from .eurydike import store as v2store
from .ids import new_id
from .quality import REPORTS_DIR

CONTRACT = "obiettivi-v1"
PACKET_FORMAT = "orpheus-pacchetto-obiettivi-v1"
PACKETS_DIR = "pacchetti_obiettivi"
CARDS_DIR = "schede_obiettivi"
TASK_KEY = "T-057c:" + CONTRACT + ":{article_id}"
SEED = "orpheus-T-057c-v1"
PER_SESSION = 10
MAX_OPEN = 50          # 2026-10-28: the user may open 30-50 packets at a time
FIELDS = cards_v2.LEVEL2_FIELDS
CHOICE_FIELDS = tuple(k for k in FIELDS if cards_v2.NEW_FIELDS[k]["tipo"] == "scelta")
DESIGN_FIELDS = ("disegno_tempi", "disegno_gruppi", "stessi_soggetti", "crossover")

OBJECTIVE_IT = {"confronto_gruppi": "Confronto tra gruppi",
                "confronto_condizioni": "Confronto tra condizioni o arti negli stessi soggetti",
                "effetto_intervento": "Effetto di un intervento (pre-post, con o senza controllo)",
                "associazione_predizione": "Associazione o predizione",
                "affidabilita_validita": "Affidabilità o validità",
                "descrittivo": "Descrittivo"}
COUNT_IT = {"uno": "uno", "due": "due", "piu_di_due": "più di due"}
YES_NO_IT = {"si": "sì", "no": "no"}
METHOD_GROUPS = ("confronto", "modello", "approccio", "affidabilita")

# sport, exercise and biomechanics (user's choice of 2026-10-27): words of the title, abstract, MeSH or journal title
TOPIC = re.compile(
    r"\b(sports?|athlet\w*|exercis\w*|training|trained|physical activity|physical fitness|fitness|biomechanic\w*|"
    r"kinemati\w*|kinetic\w*|gait|walking|running|runners?|sprint\w*|jump\w*|landing|strength|resistance|"
    r"muscle\w*|muscular|neuromuscular|electromyograph\w*|EMG|plyometric\w*|warm-up|football\w*|soccer|rugby|"
    r"basketball|volleyball|handball|tennis|cycl(?:ing|ists?)|swim\w*|rowing|endurance|aerobic|anaerobic|VO2\w*|"
    r"players?|motor control|balance|postur\w*|ground reaction|locomotion|conditioning|kinesiology)\b", re.I)

# Not studies (point 2 of the user's list, 2026-10-28): left out of the packets, never written as cards. Two fixed rules:
# a label of the article category near the top of the first page (e.g. "Viewpoint" in BMJ Open SEM), or no Methods
# section and no statistical method found by stats-terms-v1 (e.g. a narrative proposal). Counted and listed by `lot`.
NON_STUDY_LABEL = re.compile(
    r"(?:^|\s)(Viewpoint|VIEWPOINT|Commentary|COMMENTARY|Editorial|EDITORIAL|Perspectives?|PERSPECTIVES?|Opinion|"
    r"OPINION|Current [Oo]pinion|CURRENT OPINION|Leading [Aa]rticle|LEADING ARTICLE|Narrative [Rr]eview|NARRATIVE REVIEW|"
    r"Infographic|INFOGRAPHIC|Point of [Vv]iew|POINT OF VIEW|Debate|DEBATE)(?=\s|$)")
LABEL_CHARS = 600


INSTRUCTIONS = [
    "Write ONE JSON file: rapporti/schede_obiettivi/da_inviare/<article_id>.json, format 'orpheus-scheda-v2', "
    "versione 'cards-v2', with workflow_run_id and article_id copied from this packet, 'modello' = your model name.",
    "Only the fields listed in 'campi', each at least once; nothing in 'righe_esito'. A field the article does not "
    "report: one row with origin 'not_available' and no value, page or quote.",
    "Every value: 'pagina' (the PDF page of 'testo') and 'citazione' = an exact, continuous piece of that page (no "
    "'...', at least 20 characters) that shows the value. Never 'sezione'. Numbers of the value must be in the quote.",
    "Choice fields (tipo 'scelta'): 'valore' is exactly one key of 'valori' (e.g. 'effetto_intervento', 'due', 'si'), "
    "one row (ordine 1). The quote is the sentence the choice rests on (aim, design, procedure); origin 'reported' when "
    "the article says it in so many words, 'interpreted' when you classify it from the sentence.",
    "tipo_obiettivo is the type of the MAIN objective (the primary aim or hypothesis). Several aims: classify the first "
    "or the one of the primary outcome, and write the other aims as more 'obiettivo' rows (ordine 2, 3).",
    "Design of the main outcome: disegno_tempi = measurement time points of that outcome (baseline+post = 'due'); "
    "disegno_gruppi = groups or arms between subjects (a single sample = 'uno'; a crossover study usually 'uno'); "
    "stessi_soggetti = the same subjects measured at every time point or condition; crossover = every subject gets "
    "every condition in sequence.",
    "Coherence checked by ORPHEUS: crossover 'si' -> stessi_soggetti 'si'; effetto_intervento -> two or more time "
    "points, or two or more groups, or crossover; confronto_gruppi -> two or more groups or crossover 'si'; "
    "confronto_condizioni (limbs, shoes, conditions compared in the same subjects, also in one session) -> "
    "stessi_soggetti 'si'.",
    "analisi_principale = the test or model used for the primary outcome, as written (e.g. 'paired t-test', "
    "'two-way repeated-measures ANOVA'). 'voci_livello_1' lists what the deterministic rules found in this article, "
    "with page and sentence: a help, never a substitute for reading the Methods.",
    "Never compute anything: no effect size, no difference, no CI that is not written in the article.",
]
EXAMPLE = {
    "formato": cards_v2.FORMAT, "versione": cards_v2.VERSION, "workflow_run_id": "run_<copy>", "article_id": "art_<copy>",
    "modello": "claude-opus-5-5",
    "valori": [
        {"campo": "obiettivo", "valore": "to examine the effects of 8 weeks of plyometric training on sprint and jump "
         "performance", "pagina": 2, "citazione": "The aim of this study was to examine the effects of 8 weeks of "
         "plyometric training on sprint and jump performance", "origin": "reported"},
        {"campo": "tipo_obiettivo", "valore": "effetto_intervento", "pagina": 2, "citazione": "The aim of this study "
         "was to examine the effects of 8 weeks of plyometric training on sprint and jump performance",
         "origin": "interpreted"},
        {"campo": "disegno_tempi", "valore": "due", "pagina": 3, "citazione": "Tests were performed before and after "
         "the 8-week intervention", "origin": "reported"},
        {"campo": "effetto_principale", "origin": "not_available"},
    ],
    "righe_esito": [],
}


def packets_dir(home: Path) -> Path:
    return home / REPORTS_DIR / PACKETS_DIR


def to_send_dir(home: Path) -> Path:
    return home / REPORTS_DIR / CARDS_DIR / card_store.TO_SEND


def accepted_dir(home: Path) -> Path:
    return home / REPORTS_DIR / CARDS_DIR / card_store.ACCEPTED


# ---------------------------------------------------------------- which articles (priority)
def done_articles(conn: sqlite3.Connection) -> set[str]:
    """Articles whose objective type is already in the card (any state: proposed, confirmed, corrected, not reported)."""
    return {r[0] for r in conn.execute("SELECT DISTINCT article_id FROM card_item WHERE field_key = 'tipo_obiettivo' "
                                       "AND superseded_by IS NULL")}


def open_packet_articles(conn: sqlite3.Connection) -> list[str]:
    prefix = TASK_KEY.format(article_id="")
    return [r[0][len(prefix):] for r in conn.execute(
        """SELECT DISTINCT t.idempotency_key FROM workflow_run w JOIN task t ON t.id = w.task_id
           WHERE w.status = 'running' AND t.idempotency_key LIKE ? ORDER BY t.idempotency_key""", (prefix + "%",))]


def topic_rank(title: str | None, abstract: str | None, mesh: list[str], journal: str | None) -> int:
    """0: the title is on sport, exercise or biomechanics; 1: the abstract, MeSH or journal title is; 2: none."""
    if TOPIC.search(title or ""):
        return 0
    if TOPIC.search(abstract or "") or any(TOPIC.search(m) for m in mesh) or TOPIC.search(journal or ""):
        return 1
    return 2


def non_study(conn: sqlite3.Connection, article_id: str, document_id: str) -> str | None:
    """Why the article is not a study (NON_STUDY_LABEL on the first page; or no Methods and no statistical method), or
    None. Read-only."""
    first = conn.execute("SELECT text FROM document_page WHERE document_id = ? ORDER BY page_no LIMIT 1",
                         (document_id,)).fetchone()
    m = NON_STUDY_LABEL.search((first[0] if first else "")[:LABEL_CHARS])
    if m:
        return f"etichetta «{m.group(1)}» in cima alla prima pagina"
    ts = conn.execute("SELECT words_methods FROM text_structure WHERE article_id = ? AND rules = ?",
                      (article_id, stats_terms.RULES_VERSION)).fetchone()
    if ts is not None and not ts[0]:
        n = conn.execute(f"""SELECT count(*) FROM text_method_item WHERE article_id = ? AND rules = ?
                             AND grp IN ({",".join("?" * len(METHOD_GROUPS))})""",
                         (article_id, stats_terms.RULES_VERSION, *METHOD_GROUPS)).fetchone()[0]
        if not n:
            return "senza sezione Metodi e senza metodi statistici"
    return None


def candidates(conn: sqlite3.Connection, *, seed: str = SEED, left_out: list | None = None) -> list[dict]:
    """Original studies with a complete PDF analysed by stats-terms-v1, without the objective in the card and without
    an open packet, in priority order: topic first (title, then abstract/MeSH/journal), then one journal at a time in
    turn (journals with more articles first), in the fixed sha256 order of SEED. Read-only."""
    docs = card_store._complete_documents(conn)
    analysed = {r[0] for r in conn.execute("SELECT article_id FROM text_structure WHERE rules = ?",
                                           (stats_terms.RULES_VERSION,))}
    skip = done_articles(conn) | set(open_packet_articles(conn))
    by_hand = {k.strip().lower() for k in card_store.exclusions()}
    types: dict[str, set[str]] = {}
    for r in conn.execute("SELECT article_id, type_name FROM article_publication_type"):
        types.setdefault(r[0], set()).add(r[1])
    mesh: dict[str, list[str]] = {}
    for r in conn.execute("SELECT article_id, descriptor_name FROM article_mesh"):
        mesh.setdefault(r[0], []).append(r[1])
    out = []
    for a in conn.execute("""SELECT a.id, a.title, a.abstract, a.doi, a.journal_id, j.title AS journal
                             FROM article a LEFT JOIN journal j ON j.id = a.journal_id"""):
        aid = a["id"]
        if aid not in docs or aid not in analysed or aid in skip or (a["doi"] or "").lower() in by_hand \
                or types.get(aid, set()) & card_store.EXCLUDED_TYPES or card_store._EXCLUDED_TITLE.search(a["title"] or ""):
            continue
        reason = non_study(conn, aid, docs[aid]["id"])
        if reason:
            if left_out is not None:
                left_out.append({"article_id": aid, "titolo": a["title"], "motivo": reason})
            continue
        out.append({"article_id": aid, "titolo": a["title"], "journal_id": a["journal_id"], "rivista": a["journal"],
                    "tema": topic_rank(a["title"], a["abstract"], mesh.get(aid, []), a["journal"]),
                    "ordine": hashlib.sha256(f"{seed}:{aid}".encode()).hexdigest()})
    size = {}
    for c in out:
        size[c["journal_id"]] = size.get(c["journal_id"], 0) + 1
    ranked = []
    for tier in (0, 1, 2):
        queues: dict = {}
        for c in sorted((c for c in out if c["tema"] == tier), key=lambda c: (c["ordine"], c["article_id"])):
            queues.setdefault(c["journal_id"], []).append(c)
        order = sorted(queues, key=lambda j: (-size[j], str(j)))
        while any(queues[j] for j in order):
            for j in order:
                if queues[j]:
                    ranked.append(queues[j].pop(0))
    return ranked


def lot(conn: sqlite3.Connection, size: int = PER_SESSION, *, journal_id: str | None = None) -> dict:
    """The next packets: up to `size` open packets in all (the open ones count), in the priority order. With
    `journal_id` (user's request of 2026-10-28: «prima i pacchetti di SJMSS») only the articles of that journal, and
    only its open packets count (the others stay open). Read-only."""
    opened = open_packet_articles(conn)
    left_out: list[dict] = []
    ranked = candidates(conn, left_out=left_out)
    if journal_id is not None:
        ranked = [c for c in ranked if c["journal_id"] == journal_id]
        mine = {r[0] for r in conn.execute("SELECT id FROM article WHERE journal_id = ?", (journal_id,))}
        counted = [a for a in opened if a in mine]
    else:
        counted = opened
    new = [c["article_id"] for c in ranked[:max(0, size - len(counted))]]
    return {"aperti": opened, "nuovi": new, "da_fare": len(ranked), "non_studi": left_out,
            "aperti_della_rivista": len(counted) if journal_id is not None else None,
            "sul_tema": sum(1 for c in ranked if c["tema"] < 2),
            "restano_dopo": max(0, len(ranked) - len(new))}


# ---------------------------------------------------------------- packets
def _document(conn: sqlite3.Connection, aid: str) -> str | None:
    """The PDF the card refers to (the same rule as cards-v2 record), only when its pages are on this PC."""
    doc = v2store._document_of(conn, aid)
    if doc and conn.execute("SELECT 1 FROM document_page WHERE document_id = ?", (doc,)).fetchone():
        return doc
    d = card_store._complete_documents(conn).get(aid)
    return d["id"] if d else None


def level1_items(conn: sqlite3.Connection, aid: str) -> list[dict]:
    return [{"voce": r["term"], "nome": stats_terms.TERMS_BY_KEY[r["term"]].label if r["term"] in stats_terms.TERMS_BY_KEY
             else r["term"], "gruppo": r["grp"], "dettaglio": r["detail"], "pagina": r["page_no"], "frase": r["quote"]}
            for r in conn.execute("""SELECT term, grp, detail, page_no, quote FROM text_method_item
                                     WHERE article_id = ? AND rules = ? ORDER BY page_no, id""",
                                  (aid, stats_terms.RULES_VERSION))
            if r["grp"] in METHOD_GROUPS]


def fields_spec() -> dict:
    return {k: {kk: (list(vv) if isinstance(vv, tuple) else vv) for kk, vv in cards_v2.NEW_FIELDS[k].items()}
            for k in FIELDS}


def packet(meta: dict, task_id: str, run: dict, pages: dict[int, str], items: list[dict]) -> dict:
    return {"formato": PACKET_FORMAT, "contratto": CONTRACT, "versione_scheda": cards_v2.VERSION,
            "task_id": task_id, "workflow_run_id": run["id"], "article_id": meta["article_id"],
            "emesso_il": run["started_at"], "hash_testo": run["parameters"]["text_sha256"],
            "documento": run["parameters"]["document_id"], "articolo": meta, "istruzioni": INSTRUCTIONS,
            "campi": fields_spec(), "esempio": EXAMPLE, "voci_livello_1": items,
            "testo": {"pagine": [{"pagina": k, "testo": pages[k]} for k in sorted(pages)]}}


def _text(conn: sqlite3.Connection, doc: str | None, aid: str) -> tuple[dict[int, str], str]:
    pages, _sections = card_store.article_text(conn, doc, None, aid)
    return pages, card_store.text_sha256(pages, {})


def issue_packets(conn: sqlite3.Connection, home: Path, article_ids: list[str], *, project_name: str = "ORPHEUS",
                  project_version: str = "", actor: str = "orpheus obiettivi") -> card_store.PacketResult:
    """Tasks, runs and packet files (idempotent: an open packet with the same text is written again, not duplicated)."""
    res = card_store.PacketResult()
    done = done_articles(conn)
    plan = []
    for aid in article_ids:
        if not conn.execute("SELECT 1 FROM article WHERE id = ?", (aid,)).fetchone():
            res.problems.append({"article_id": aid, "problema": "articolo non trovato nel database"})
            continue
        if aid in done:
            res.skipped.append({"article_id": aid, "motivo": "obiettivo già nella scheda"})
            continue
        doc = _document(conn, aid)
        if doc is None:
            res.problems.append({"article_id": aid, "problema": "nessun PDF di articolo completo con le pagine su questo PC"})
            continue
        pages, sha = _text(conn, doc, aid)
        plan.append((aid, doc, pages, sha))
    written = []
    with transaction(conn):
        if plan:
            project_id = card_store._ensure_project(conn, project_name, project_version)
            agent_id = card_store._ensure_agent(conn)
            model_id = card_store._ensure_model(conn, card_store.GENERIC_MODEL)
        for aid, doc, pages, sha in plan:
            key = TASK_KEY.format(article_id=aid)
            task = conn.execute("SELECT id FROM task WHERE idempotency_key = ?", (key,)).fetchone()
            if task is None:
                task_id = new_id("task")
                conn.execute("""INSERT INTO task (id, project_id, title, priority, status, owner_agent_id, expected_output,
                                                  acceptance_criteria, idempotency_key)
                                VALUES (?, ?, ?, 2, 'ready', ?, ?, ?, ?)""",
                             (task_id, project_id, f"Obiettivo e disegno ({CONTRACT}) dell'articolo {aid}", agent_id,
                              f"{REPORTS_DIR}/{CARDS_DIR}/{card_store.TO_SEND}/{aid}.json (formato {cards_v2.FORMAT})",
                              f"supera i controlli di {CONTRACT} con orpheus obiettivi --invia", key))
            else:
                task_id = task[0]
                conn.execute("UPDATE task SET status = 'ready' WHERE id = ? AND status NOT IN ('done', 'running')",
                             (task_id,))
            run = None
            for r in conn.execute("""SELECT * FROM workflow_run WHERE task_id = ? AND status = 'running'
                                     ORDER BY started_at DESC, rowid DESC""", (task_id,)):
                params = json.loads(r["parameters"])
                if params.get("text_sha256") == sha and params.get("document_id") == doc and run is None:
                    run = {"id": r["id"], "started_at": r["started_at"], "parameters": params}
                else:
                    conn.execute("""UPDATE workflow_run SET status = 'cancelled', finished_at = ?,
                                    error = 'testo cambiato: pacchetto riemesso' WHERE id = ?""", (audit.utc_now(), r["id"]))
            if run is None:
                params = {"card_version": cards_v2.VERSION, "contratto": CONTRACT, "article_id": aid, "document_id": doc,
                          "text_sha256": sha, "pagine": len(pages),
                          "packet_file": f"{REPORTS_DIR}/{PACKETS_DIR}/{aid}.json"}
                run = {"id": new_id("workflow_run"), "started_at": audit.utc_now(), "parameters": params}
                conn.execute("""INSERT INTO workflow_run (id, task_id, agent_id, executor, model_id, started_at, status,
                                                          parameters, validation_status)
                                VALUES (?, ?, ?, 'claude_session', ?, ?, 'running', ?, 'pending')""",
                             (run["id"], task_id, agent_id, model_id, run["started_at"],
                              json.dumps(params, ensure_ascii=False, sort_keys=True)))
                res.created_runs += 1
            written.append((aid, task_id, run, doc, pages))
        if plan:
            audit.record(conn, actor=actor, action="objective_packets_issued", payload={
                "contract": CONTRACT, "articles": [w[0] for w in written], "new_runs": res.created_runs},
                in_transaction=True)
    for aid, task_id, run, doc, pages in written:
        meta = card_store._metadata(conn, aid, doc)
        data = json.dumps(packet(meta, task_id, run, pages, level1_items(conn, aid)), ensure_ascii=False,
                          indent=1).encode("utf-8")
        path = packets_dir(home) / f"{aid}.json"
        changed = card_store._write_if_changed(path, data)
        res.issued.append({"article_id": aid, "workflow_run_id": run["id"], "file": str(path), "pagine": len(pages),
                           "nuovo_file": changed, "titolo": meta["titolo"]})
    return res


# ---------------------------------------------------------------- checks
def check_design(card) -> CheckResult:
    """Coherence of the choice fields between them (only when both sides are written)."""
    val = {v.campo: v.valore for v in card.valori if v.campo in CHOICE_FIELDS and v.origin != "not_available"}
    probs = []
    if val.get("crossover") == "si" and val.get("stessi_soggetti") == "no":
        probs.append("crossover 'si' ma stessi_soggetti 'no': in un crossover gli stessi soggetti ricevono ogni condizione")
    tipo = val.get("tipo_obiettivo")
    if tipo == "effetto_intervento" and val.get("disegno_tempi") == "uno" and val.get("disegno_gruppi") == "uno" \
            and val.get("crossover") != "si":
        probs.append("effetto_intervento con un tempo e un gruppo: servono due tempi, o due gruppi, o un crossover")
    if tipo == "confronto_condizioni" and val.get("stessi_soggetti") == "no":
        probs.append("confronto_condizioni con stessi_soggetti 'no': le condizioni o gli arti si confrontano negli stessi "
                     "soggetti (altrimenti confronto_gruppi)")
    if tipo == "confronto_gruppi" and val.get("disegno_gruppi") == "uno" and val.get("crossover") != "si":
        probs.append("confronto_gruppi con un solo gruppo: indica i gruppi (o confronto_condizioni se le condizioni sono "
                     "negli stessi soggetti)")
    return CheckResult("coerenza_disegno", not probs, probs)


def _strict(card, pages: dict[int, str]) -> list[CheckResult]:
    probs = []
    for i, v in enumerate(card.valori, 1):
        if v.origin != "not_available" and v.pagina is None:
            probs.append(f"{cards_v2._label(i, v)}: serve la pagina del PDF (non la sezione)")
    if card.righe_esito:
        probs.append("righe_esito: in questa scheda non si scrivono righe di esito")
    found = cards.check_quotes(SimpleNamespace(valori=[v for v in card.valori if v.pagina is not None]), pages, {})
    return [CheckResult("solo_pagine", not probs, probs), CheckResult("frase_esatta", found.ok, found.problems)]


def validate(data, pages: dict[int, str], *, decided: set[tuple[str, int]] = frozenset(),
             packet_check: CheckResult | None = None) -> Outcome:
    out = cards_v2.validate(data, pages, {}, wanted=FIELDS, decided=decided, packet_check=packet_check)
    if out.card is None:
        return out
    return Outcome(out.checks + _strict(out.card, pages) + [check_design(out.card)], out.card)


def check_with_packet(card_data, packet_data: dict) -> Outcome:
    """The content checks on the text of the packet, no database (for Claude before saving the card)."""
    pages = {p["pagina"]: p["testo"] for p in packet_data["testo"]["pagine"]}
    probs = []
    for k in ("workflow_run_id", "article_id"):
        if not isinstance(card_data, dict) or card_data.get(k) != packet_data.get(k):
            probs.append(f"{k} diverso da quello del pacchetto")
    return validate(card_data, pages, packet_check=CheckResult("pacchetto", not probs, probs))


# ---------------------------------------------------------------- submit
def _prepare(conn: sqlite3.Connection, path: Path) -> card_store.Submission:
    s = card_store.Submission(Path(path))
    try:
        s.raw = s.path.read_bytes()
        s.sha256 = hashlib.sha256(s.raw).hexdigest()
        s.data = json.loads(s.raw.decode("utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        s.stop = f"file non leggibile o non JSON: {exc}"
        return s
    run_id = s.data.get("workflow_run_id") if isinstance(s.data, dict) else None
    s.run = conn.execute("SELECT * FROM workflow_run WHERE id = ?", (run_id,)).fetchone() if isinstance(run_id, str) else None
    if s.run is None:
        s.stop = "workflow_run_id mancante o non trovato in questo database (copialo dal pacchetto)"
        return s
    s.params = json.loads(s.run["parameters"] or "{}")
    if s.params.get("contratto") != CONTRACT:
        s.stop = f"l'esecuzione {run_id} non è un pacchetto {CONTRACT}"
    elif s.run["status"] == "succeeded":
        s.stop = "scheda già accettata per questo pacchetto"
    elif s.run["status"] != "running":
        s.stop = f"il pacchetto non è più valido (esecuzione {s.run['status']}): rilancia obiettivi.cmd"
    return s


def submit_one(conn: sqlite3.Connection, s: card_store.Submission, *, actor: str = "orpheus obiettivi") -> dict:
    aid = s.params["article_id"]
    pages, sha = _text(conn, s.params.get("document_id"), aid)
    probs = []
    if not isinstance(s.data, dict) or s.data.get("article_id") != aid:
        probs.append(f"article_id diverso da quello del pacchetto ({aid})")
    if sha != s.params.get("text_sha256"):
        probs.append("il testo dell'articolo nel database è cambiato dopo il pacchetto: rilancia obiettivi.cmd")
    if v2store._document_of(conn, aid) != s.params.get("document_id"):
        probs.append("il PDF della scheda dell'articolo è cambiato dopo il pacchetto: rilancia obiettivi.cmd")
    outcome = validate(s.data, pages, decided=cards_v2.decided_keys(conn, aid),
                       packet_check=CheckResult("pacchetto", not probs, probs))
    out = {"file": s.path.name, "article_id": aid, "workflow_run_id": s.run["id"],
           "esito": "accettata" if outcome.accepted else "rifiutata",
           "controlli": {c.name: ("ok" if c.ok else "non superato") for c in outcome.checks}}
    if outcome.accepted:
        out["scritto"] = cards_v2.record(conn, outcome.card)
    with transaction(conn):
        for c in outcome.checks:
            conn.execute("""INSERT INTO validation_result (id, workflow_run_id, check_name, outcome, validator, details)
                            VALUES (?, ?, ?, ?, 'deterministic', ?)""",
                         (new_id("validation_result"), s.run["id"], c.name, "pass" if c.ok else "fail",
                          json.dumps({"file": s.path.name, "file_sha256": s.sha256, "regole": CONTRACT,
                                      "problemi": c.problems[:50], "problemi_totali": len(c.problems)},
                                     ensure_ascii=False)))
        if outcome.accepted:
            model_id = card_store._ensure_model(conn, outcome.card.modello)
            conn.execute("""UPDATE workflow_run SET status = 'succeeded', finished_at = ?, validation_status = 'pass',
                            model_id = ? WHERE id = ?""", (max(audit.utc_now(), s.run["started_at"]), model_id, s.run["id"]))
            conn.execute("UPDATE task SET status = 'done' WHERE id = ?", (s.run["task_id"],))
        else:
            conn.execute("UPDATE workflow_run SET validation_status = 'fail' WHERE id = ?", (s.run["id"],))
            out["motivi"] = outcome.problems()[:40]
            out["motivi_totali"] = len(outcome.problems())
        audit.record(conn, actor=actor, action="objective_card_" + ("accepted" if outcome.accepted else "rejected"),
                     entity_type="article", entity_id=aid,
                     payload={"workflow_run_id": s.run["id"], "file_sha256": s.sha256, "contract": CONTRACT},
                     in_transaction=True)
    return out


def _file_after(s: card_store.Submission, summary: dict, home: Path) -> str:
    motivi = s.path.with_name(s.path.stem + ".motivi.txt")
    if summary.get("esito") == "accettata":
        dest_dir = accepted_dir(home)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / s.path.name
        if dest.exists():
            dest = dest_dir / f"{s.path.stem}_{s.sha256[:8]}{s.path.suffix}"
        s.path.replace(dest)
        if motivi.exists():
            motivi.unlink()
        return str(dest)
    motivi.write_text(card_store.reasons_text(summary, s.stop).replace("schede.cmd", "obiettivi.cmd"), encoding="utf-8")
    return str(motivi)


def to_send(home: Path) -> list[Path]:
    d = to_send_dir(home)
    return sorted(p for p in d.glob("*.json")) if d.is_dir() else []


def submit(conn: sqlite3.Connection, home: Path, files: list[Path] | None = None) -> dict:
    results = []
    for path in (files if files is not None else to_send(home)):
        s = _prepare(conn, path)
        summary = ({"file": s.path.name, "esito": "rifiutata", "motivi": [s.stop]} if s.stop else submit_one(conn, s))
        summary["dopo"] = _file_after(s, summary, home)
        results.append(summary)
    return {"inviate": len(results), "accettate": sum(r["esito"] == "accettata" for r in results),
            "rifiutate": sum(r["esito"] != "accettata" for r in results), "schede": results}


def status(conn: sqlite3.Connection) -> dict:
    items = {}
    for r in conn.execute("""SELECT field_key, state, count(*) FROM card_item WHERE superseded_by IS NULL
                             AND field_key IN (%s) GROUP BY 1, 2""" % ",".join("?" * len(FIELDS)), FIELDS):
        items.setdefault(r[0], {})[r[1]] = r[2]
    return {"contratto": CONTRACT, "articoli_con_obiettivo": len(done_articles(conn)),
            "pacchetti_aperti": len(open_packet_articles(conn)), "valori": items}
