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

CONTRACT = "obiettivi-v2"          # 2026-11-01: taxonomy v2 approved by the user on 2026-10-31 23:07 (ADR-0015)
CONTRACTS = ("obiettivi-v1", "obiettivi-v2")   # packets of both are accepted; the cards are always checked with v2
PACKET_FORMAT = "orpheus-pacchetto-obiettivi-v3"
PACKETS_DIR = "pacchetti_obiettivi"
CARDS_DIR = "schede_obiettivi"
TASK_KEY = "T-057c:obiettivi-v1:{article_id}"      # stable identity of the task of an article (not the contract)
COMPLETE_KEY = "T-057c:obiettivi-v2-completa:{article_id}"
# completion of the cards written with obiettivi-v1 (2026-11-01): the two new fields and the objective type again
COMPLETE_FIELDS = ("tipo_obiettivo", "natura_studio", "replicazione")
SEED = "orpheus-T-057c-v1"
PER_SESSION = 10
MAX_OPEN = 50          # 2026-10-28: the user may open 30-50 packets at a time
FIELDS = cards_v2.LEVEL2_FIELDS
CHOICE_FIELDS = tuple(k for k in FIELDS if cards_v2.NEW_FIELDS[k]["tipo"] == "scelta")
DESIGN_FIELDS = ("disegno_tempi", "disegno_gruppi", "stessi_soggetti", "crossover")

OBJECTIVE_IT = {"confronto_gruppi": "Confronto tra gruppi",
                "confronto_condizioni": "Confronto tra condizioni o arti negli stessi soggetti",
                "effetto_intervento": "Effetto di un intervento (pre-post, con o senza controllo)",
                "andamento_nel_tempo": "Andamento nel tempo (misure ripetute senza intervento)",
                "associazione_predizione": "Associazione o predizione",
                "affidabilita": "Affidabilità",
                "validita": "Validità",
                "descrittivo": "Descrittivo",
                "affidabilita_validita": "Affidabilità o validità (obiettivi-v1, da riclassificare)"}
NATURE_IT = {"rct": "RCT a gruppi paralleli", "crossover": "Crossover", "non_randomizzato": "Intervento non randomizzato",
             "coorte": "Coorte", "caso_controllo": "Caso-controllo", "trasversale": "Trasversale",
             "serie_di_casi": "Serie di casi o caso singolo", "indagine": "Indagine o questionario",
             "qualitativo": "Qualitativo", "altro": "Altro"}
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
# 2026-11-01 (point 3 of the user's list): the rule "no Methods section and no statistical method" left out true studies
# whose Methods heading the section rules did not recognise (e.g. «Methods and Procedures», «Material y métodos») or
# whose statistics have no known name (e.g. «Comparison of nerve conduction velocities… between runners and controls»).
# Such an article stays in when its text shows a study: a Methods heading on a line of its own, or a p value together
# with a sample ("14 male runners", "A survey was performed"). Case reports and narrative texts have neither.
METHODS_HEADING = re.compile(
    r"^[ \t]*(?:\d+\.?\s*)?(?:Materials?,? and [Mm]ethods?|MATERIALS? AND METHODS?|Methods? and [Pp]rocedures?|"
    r"(?:Subjects|Participants|Patients) and [Mm]ethods?|Methods?|METHODS?|Methodology|"
    r"Material(?:es)? y m[ée]todos?|Pacientes y m[ée]todos?)[ \t]*$", re.M)
P_VALUE = re.compile(r"\b[pP]\s?[<=≤]\s?0?\.\d")
SAMPLE = re.compile(
    r"\b(?:\d+|[Tt]wo|[Tt]hree|[Ff]our|[Ff]ive|[Ss]ix|[Ss]even|[Ee]ight|[Nn]ine|[Tt]en|[Ee]leven|[Tt]welve|"
    r"[A-Z][a-z]+teen|[A-Z][a-z]+ty(?:-[a-z]+)?)\s+(?:[\w-]+\s+){0,4}?"
    r"(?:participants|subjects|patients|players|athletes|runners|volunteers|respondents|footballers|surgeons|children|"
    r"adults|women|men)\s+(?:were|was|participated|took part|completed|returned|responded|aged|with|from)\b"
    r"|\b(?:[Aa] survey was (?:performed|conducted|carried out)|[Aa] questionnaire was (?:distributed|sent|administered)|"
    r"surveys were returned|were surveyed)\b")


def study_signals(text: str) -> str | None:
    """Why the text looks like a study although the section rules found no Methods (or None)."""
    m = METHODS_HEADING.search(text)
    if m:
        return f"titolo dei Metodi «{m.group(0).strip()}»"
    s = SAMPLE.search(text)
    if s and P_VALUE.search(text):
        return f"valore p e campione «{' '.join(s.group(0).split())}»"
    return None


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
    "Rule for conditions and interventions (obiettivi-v2): acute conditions compared in the same subjects in one session "
    "or a short series (shoes, insoles, braces worn for the test, limbs, speeds, surfaces, fatigue protocols) -> "
    "confronto_condizioni; a before-after with an intervention lasting beyond the test session (training, treatment, "
    "surgery, a programme) -> effetto_intervento. Repeated measures of the same subjects without an intervention "
    "(during a race, across the phases of a cycle, over a season) -> andamento_nel_tempo. affidabilita = repeatability "
    "or agreement of a measure; validita = agreement with a criterion, construct or content (both: classify the main "
    "one, the other as an 'obiettivo' row).",
    "natura_studio = the nature of the study (one key of 'valori'); 'altro' only with the reason in 'nota'. "
    "replicazione = 'si' only when the article says it replicates an earlier study (quote that sentence); otherwise "
    "'no' with the aim sentence as quote, origin 'interpreted'.",
    "Coherence checked by ORPHEUS: crossover 'si' <-> natura_studio 'crossover' and -> stessi_soggetti 'si'; natura_studio "
    "'rct' -> two or more groups; effetto_intervento -> two or more time points, or two or more groups, or crossover; "
    "confronto_gruppi -> two or more groups or crossover 'si'; confronto_condizioni -> stessi_soggetti 'si'; "
    "andamento_nel_tempo -> two or more time points, one group, stessi_soggetti 'si'.",
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


def open_packet_articles(conn: sqlite3.Connection, *, complete: bool = False) -> list[str]:
    prefix = (COMPLETE_KEY if complete else TASK_KEY).format(article_id="")
    return [r[0][len(prefix):] for r in conn.execute(
        """SELECT DISTINCT t.idempotency_key FROM workflow_run w JOIN task t ON t.id = w.task_id
           WHERE w.status = 'running' AND t.idempotency_key LIKE ? ORDER BY t.idempotency_key""", (prefix + "%",))]


def completion_candidates(conn: sqlite3.Connection) -> list[str]:
    """Articles with the objective in the card but without the new fields of obiettivi-v2 (natura_studio), no open
    completion packet, in a fixed order (sha256 of SEED). Read-only."""
    have = {r[0] for r in conn.execute("SELECT DISTINCT article_id FROM card_item WHERE field_key = 'natura_studio' "
                                       "AND superseded_by IS NULL")}
    skip = have | set(open_packet_articles(conn, complete=True))
    return sorted((a for a in done_articles(conn) if a not in skip),
                  key=lambda a: hashlib.sha256(f"{SEED}:{a}".encode()).hexdigest())


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
            text = "\n".join(r[0] or "" for r in conn.execute(
                "SELECT text FROM document_page WHERE document_id = ? ORDER BY page_no", (document_id,)))
            if study_signals(text) is None:
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


def fields_spec(fields: tuple[str, ...] = FIELDS) -> dict:
    return {k: {kk: (list(vv) if isinstance(vv, tuple) else vv) for kk, vv in cards_v2.NEW_FIELDS[k].items()}
            for k in fields}


COMPLETE_INSTRUCTION = ("COMPLETION of a card written with obiettivi-v1: write ONLY the fields of 'campi' (the others are "
                        "already in the card, see 'valori_attuali'; they are used for the coherence checks). "
                        "tipo_obiettivo again with the types of obiettivi-v2 (e.g. 'affidabilita_validita' -> "
                        "'affidabilita' or 'validita'; repeated measures without intervention -> 'andamento_nel_tempo').")


def packet(meta: dict, task_id: str, run: dict, pages: dict[int, str], items: list[dict], *,
           current: dict | None = None) -> dict:
    params = run["parameters"]
    kept, trimmed = card_store.trim_packet_pages(pages)
    out = {"formato": PACKET_FORMAT, "contratto": CONTRACT, "versione_scheda": cards_v2.VERSION,
           "task_id": task_id, "workflow_run_id": run["id"], "article_id": meta["article_id"],
           "emesso_il": run["started_at"], "hash_testo": params["text_sha256"],
           "documento": params["document_id"], "articolo": meta,
           "comune": card_store.COMMON_FILE, "testo_tagliato": trimmed, "voci_livello_1": items,
           "testo": {"pagine": [{"pagina": k, "testo": kept[k]} for k in sorted(kept)]}}
    if params.get("completamento"):                 # its own instruction and fields, on top of the common file
        out |= {"istruzioni": [COMPLETE_INSTRUCTION], "campi": fields_spec(tuple(params["campi"])),
                "completamento": True, "valori_attuali": current or {}}
    return out


def _text(conn: sqlite3.Connection, doc: str | None, aid: str) -> tuple[dict[int, str], str]:
    pages, _sections = card_store.article_text(conn, doc, None, aid)
    return pages, card_store.text_sha256(pages, {})


def issue_packets(conn: sqlite3.Connection, home: Path, article_ids: list[str], *, project_name: str = "ORPHEUS",
                  project_version: str = "", actor: str = "orpheus obiettivi",
                  complete: bool = False) -> card_store.PacketResult:
    """Tasks, runs and packet files (idempotent: an open packet with the same text is written again, not duplicated).
    `complete`: completion packets of cards already written with obiettivi-v1 (only COMPLETE_FIELDS not yet decided)."""
    res = card_store.PacketResult()
    done = done_articles(conn)
    plan = []
    for aid in article_ids:
        if not conn.execute("SELECT 1 FROM article WHERE id = ?", (aid,)).fetchone():
            res.problems.append({"article_id": aid, "problema": "articolo non trovato nel database"})
            continue
        if not complete and aid in done:
            res.skipped.append({"article_id": aid, "motivo": "obiettivo già nella scheda"})
            continue
        if complete and aid not in done:
            res.skipped.append({"article_id": aid, "motivo": "obiettivo non ancora nella scheda (pacchetto normale)"})
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
            key = (COMPLETE_KEY if complete else TASK_KEY).format(article_id=aid)
            decided = cards_v2.decided_keys(conn, aid) if complete else set()
            wanted = [f for f in COMPLETE_FIELDS if (f, 1) not in decided] if complete else None
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
                if params.get("text_sha256") == sha and params.get("document_id") == doc and run is None \
                        and params.get("campi", wanted) == wanted:
                    run = {"id": r["id"], "started_at": r["started_at"], "parameters": params}
                else:
                    conn.execute("""UPDATE workflow_run SET status = 'cancelled', finished_at = ?,
                                    error = 'testo cambiato: pacchetto riemesso' WHERE id = ?""", (audit.utc_now(), r["id"]))
            if run is None:
                params = {"card_version": cards_v2.VERSION, "contratto": CONTRACT, "article_id": aid, "document_id": doc,
                          "text_sha256": sha, "pagine": len(pages),
                          "packet_file": f"{REPORTS_DIR}/{PACKETS_DIR}/{aid}.json"}
                if complete:
                    params |= {"completamento": True, "campi": wanted}
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
                "contract": CONTRACT, "articles": [w[0] for w in written], "new_runs": res.created_runs,
                "completion": complete},
                in_transaction=True)
    if written:
        res.common_file = card_store.write_common(packets_dir(home), {
            "formato": PACKET_FORMAT, "contratto": CONTRACT, "versione_scheda": cards_v2.VERSION,
            "nota": card_store.TRIM_NOTE, "istruzioni": INSTRUCTIONS, "campi": fields_spec(), "esempio": EXAMPLE})
    for aid, task_id, run, doc, pages in written:
        meta = card_store._metadata(conn, aid, doc)
        data = json.dumps(packet(meta, task_id, run, pages, level1_items(conn, aid),
                                 current=current_choices(conn, aid) if complete else None), ensure_ascii=False,
                          indent=1).encode("utf-8")
        path = packets_dir(home) / f"{aid}.json"
        changed = card_store._write_if_changed(path, data)
        res.issued.append({"article_id": aid, "workflow_run_id": run["id"], "file": str(path), "pagine": len(pages),
                           "nuovo_file": changed, "titolo": meta["titolo"]})
    return res


# ---------------------------------------------------------------- checks
def check_design(card, current: dict[str, str] | None = None) -> CheckResult:
    """Coherence of the choice fields between them (only when both sides are written). `current`: the values already
    in the card of the article (a completion card writes only some fields; the others come from there)."""
    val = dict(current or {})
    val.update({v.campo: v.valore for v in card.valori if v.campo in CHOICE_FIELDS and v.origin != "not_available"})
    for v in card.valori:
        if v.campo in CHOICE_FIELDS and v.origin == "not_available":
            val.pop(v.campo, None)
    probs = []
    if val.get("crossover") == "si" and val.get("stessi_soggetti") == "no":
        probs.append("crossover 'si' ma stessi_soggetti 'no': in un crossover gli stessi soggetti ricevono ogni condizione")
    nat = val.get("natura_studio")
    if nat == "crossover" and val.get("crossover") == "no" or nat not in (None, "crossover") and val.get("crossover") == "si":
        probs.append("natura_studio e crossover non concordano: natura 'crossover' se e solo se crossover 'si'")
    if nat == "rct" and val.get("disegno_gruppi") == "uno":
        probs.append("natura_studio 'rct' con un solo gruppo: un RCT a gruppi paralleli ha due o più gruppi "
                     "(un crossover randomizzato è natura 'crossover')")
    for v in card.valori:
        if v.campo == "natura_studio" and v.valore == "altro" and not v.nota:
            probs.append("natura_studio 'altro': scrivi il motivo in 'nota'")
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
    if tipo == "andamento_nel_tempo":
        if val.get("disegno_tempi") == "uno":
            probs.append("andamento_nel_tempo con un solo tempo: servono due o più tempi di misura")
        if val.get("disegno_gruppi") in ("due", "piu_di_due"):
            probs.append("andamento_nel_tempo con più gruppi: è un solo gruppo seguito nel tempo (con più gruppi "
                         "confronto_gruppi)")
        if val.get("stessi_soggetti") == "no":
            probs.append("andamento_nel_tempo con stessi_soggetti 'no': si misurano gli stessi soggetti nel tempo")
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
             packet_check: CheckResult | None = None, wanted: tuple[str, ...] = FIELDS,
             current: dict[str, str] | None = None) -> Outcome:
    out = cards_v2.validate(data, pages, {}, wanted=wanted, decided=decided, packet_check=packet_check)
    if out.card is None:
        return out
    return Outcome(out.checks + _strict(out.card, pages) + [check_design(out.card, current)], out.card)


def current_choices(conn: sqlite3.Connection, aid: str) -> dict[str, str]:
    """The choice values already in the card of the article (current, not 'not reported')."""
    return {r["field_key"]: r["value"] for r in v2store.current_items(conn, aid)
            if r["field_key"] in CHOICE_FIELDS and r["item_ord"] == 1 and r["value"] not in (None, "not reported")}


def _wanted(params: dict) -> tuple[str, ...]:
    return tuple(params["campi"]) if params.get("completamento") and params.get("campi") else FIELDS


def check_with_packet(card_data, packet_data: dict) -> Outcome:
    """The content checks on the text of the packet, no database (for Claude before saving the card)."""
    pages = {p["pagina"]: p["testo"] for p in packet_data["testo"]["pagine"]}
    probs = []
    for k in ("workflow_run_id", "article_id"):
        if not isinstance(card_data, dict) or card_data.get(k) != packet_data.get(k):
            probs.append(f"{k} diverso da quello del pacchetto")
    return validate(card_data, pages, packet_check=CheckResult("pacchetto", not probs, probs),
                    wanted=_wanted(packet_data), current=packet_data.get("valori_attuali"))


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
    if s.params.get("contratto") not in CONTRACTS:
        s.stop = f"l'esecuzione {run_id} non è un pacchetto di obiettivi ({', '.join(CONTRACTS)})"
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
                       packet_check=CheckResult("pacchetto", not probs, probs), wanted=_wanted(s.params),
                       current=current_choices(conn, aid))
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


# ---------------------------------------------------------------- quick confirmation in the dashboard (2026-11-01)
# Point 2 of the user's list: one row per article, «Conferma tutto l'articolo» (only the values of this contract still
# proposed whose quote was found exactly in the text, like the bulk confirmation of EURYDIKE) and «Correggi» per field.
# No migration: the decisions are card_item rows written by store.set_item (supersede, never DELETE).
ROW_FIELDS = ("obiettivo", "tipo_obiettivo", "natura_studio", "disegno_tempi", "disegno_gruppi", "stessi_soggetti",
              "crossover", "replicazione", "esito_primario", "analisi_principale", "effetto_principale")
FIELD_IT = {"obiettivo": "Obiettivo", "tipo_obiettivo": "Tipo di obiettivo", "natura_studio": "Natura dello studio",
            "disegno_tempi": "Tempi", "disegno_gruppi": "Gruppi", "stessi_soggetti": "Stessi soggetti",
            "crossover": "Crossover", "replicazione": "Replicazione", "esito_primario": "Esito primario",
            "analisi_principale": "Analisi principale", "effetto_principale": "Effetto principale"}


def choice_labels() -> dict[str, list[list[str]]]:
    """Allowed values of the choice fields with the Italian labels (the old v1 type is not offered)."""
    it = {"tipo_obiettivo": OBJECTIVE_IT, "natura_studio": NATURE_IT, "disegno_tempi": COUNT_IT,
          "disegno_gruppi": COUNT_IT, "stessi_soggetti": YES_NO_IT, "crossover": YES_NO_IT, "replicazione": YES_NO_IT}
    return {k: [[v, it[k].get(v, v)] for v in cards_v2.NEW_FIELDS[k]["valori"]] for k in CHOICE_FIELDS}


def review_rows(conn: sqlite3.Connection, journal_id: str | None = None) -> dict:
    """Every article with an objective in the card, with the values of this contract (current), their state and
    quote, and how many are still to check / confirmable in bulk. Read-only."""
    labels = {k: dict(v) for k, v in choice_labels().items()}
    labels["tipo_obiettivo"].update(OBJECTIVE_IT)
    done = done_articles(conn)
    meta = {r["id"]: r for r in conn.execute(
        """SELECT a.id, a.title, a.publication_year, a.doi, a.journal_id, j.title AS journal
           FROM article a LEFT JOIN journal j ON j.id = a.journal_id""") if r["id"] in done}
    marks = ",".join("?" * len(ROW_FIELDS))
    items: dict[str, dict[str, list[dict]]] = {}
    for r in conn.execute(f"""SELECT article_id, field_key, item_ord, value, state, quote_check, quote, page_no, note
                              FROM card_item WHERE superseded_by IS NULL AND field_key IN ({marks})
                              ORDER BY article_id, field_key, item_ord""", ROW_FIELDS):
        if r["article_id"] in meta:
            items.setdefault(r["article_id"], {}).setdefault(r["field_key"], []).append({
                "ordine": r["item_ord"], "valore": r["value"], "etichetta": labels.get(r["field_key"], {}).get(r["value"]),
                "stato": r["state"], "controllo_citazione": r["quote_check"], "citazione": r["quote"],
                "pagina": r["page_no"], "nota": r["note"]})
    rows = []
    for aid, a in meta.items():
        if journal_id and a["journal_id"] != journal_id:
            continue
        f = items.get(aid, {})
        flat = [x for xs in f.values() for x in xs]
        rows.append({"id": aid, "titolo": a["title"], "anno": a["publication_year"], "doi": a["doi"],
                     "rivista": a["journal"], "journal_id": a["journal_id"], "campi": f,
                     "da_controllare": sum(x["stato"] == "ai" for x in flat),
                     "esatte": sum(x["stato"] == "ai" and x["controllo_citazione"] == "exact" for x in flat),
                     "v1": any(x["valore"] == "affidabilita_validita" for x in f.get("tipo_obiettivo", []))
                           or "natura_studio" not in f})
    rows.sort(key=lambda r: (-r["da_controllare"], (r["rivista"] or ""), (r["titolo"] or "").lower()))
    journals = sorted({(r["journal_id"], r["rivista"]) for r in rows if r["journal_id"]}, key=lambda x: x[1] or "")
    return {"articoli": rows, "campi": [[k, FIELD_IT[k]] for k in ROW_FIELDS], "scelte": choice_labels(),
            "riviste": [{"id": j, "titolo": t} for j, t in journals], "contratto": CONTRACT,
            "da_controllare": sum(r["da_controllare"] for r in rows), "esatte": sum(r["esatte"] for r in rows),
            "da_completare": sum(r["v1"] for r in rows)}


def confirm_article(conn: sqlite3.Connection, article_id: str, *, actor: str = "user") -> dict:
    """«Conferma tutto l'articolo»: confirm the proposed values of this contract whose quote was found exactly. The
    others (similar or missing quote, proposed 'not reported') stay to be decided one by one."""
    todo = [r for r in v2store.current_items(conn, article_id)
            if r["field_key"] in ROW_FIELDS and r["state"] == "ai" and r["quote_check"] == "exact"]
    for r in todo:
        v2store.set_item(conn, article_id, r["field_key"], state="confirmed", item_ord=r["item_ord"], actor=actor)
    left = sum(r["state"] == "ai" for r in v2store.current_items(conn, article_id) if r["field_key"] in ROW_FIELDS)
    return {"confermati": len(todo), "restano_da_controllare": left}


def check_correction(field: str, value: str | None) -> None:
    """A correction of a choice field takes one of the allowed values (raises StoreError)."""
    spec = cards_v2.NEW_FIELDS.get(field, {})
    if spec.get("tipo") == "scelta" and value is not None and str(value).strip() not in spec["valori"]:
        raise v2store.StoreError(f"valore non ammesso per {field}: scegli uno di {', '.join(spec['valori'])}")
