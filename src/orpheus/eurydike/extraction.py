"""EURYDIKE E6 — data extraction (T-051g, contract `eurydike-extract-v1`; decisions of the user of 2026-11-03 15:3x:
«Scheda unica + campi», «Claude, tu confermi», «Accanto, alla pagina»).

1. The extraction form of a review (`extraction_form`, `form_field`, versions frozen like Tabula's): the fields of the
   unified article card (cards-v2, `card_key` = the field) that the review wants, plus fields of the review alone
   (text, long text, number, choice, multiple choice, yes/no). Every change of the form is a new version; a frozen form
   never changes (trigger). Packets for Claude need a frozen form.
2. Claude extracts first, in a session: one packet per study included at the full text (`eurydike-pacchetto-estrazione-v1`:
   the form, the protocol and the text with [[p. N]] markers). His answer (`eurydike-extract-v1`) has two parts:
   `scheda`, a cards-v2 card with only the card fields still empty for that article (the card is one per article,
   shared with ORPHEUS: a value already there is never asked again), and `valori`, the fields of the review alone.
   Every value with origin reported/interpreted has a verbatim sentence and its page, found in the text, else the answer
   is refused. Accepted values are proposals (`ai`).
3. The user decides from the dashboard, with the PDF beside the table at the page of the sentence: Conferma, Correggi,
   Non riportato (card fields with the card's own actions, `store.set_item`; review fields here, `decide_value`, as a
   human reviewer). Confirmation in bulk only for sentences found exactly in the text.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
from pathlib import Path
from types import SimpleNamespace

from .. import audit, card_store, cards
from ..db import transaction
from ..ids import new_id
from . import cards_v2, store
from . import review as rv
from . import screening as sc
from .core.text import chunk_pages, trim_references

CONTRACT = "eurydike-extract-v1"
PACKET_FORMAT = "eurydike-pacchetto-estrazione-v1"
EX_DIR, PACKETS, TO_SEND, ACCEPTED = "estrazione", "pacchetti", "da_inviare", "accettate"
TASK_PREFIX = "T-051g:ex:"
BUDGET = 60_000
MAX_OPEN = 40
OUTCOME_KEY = store.OUTCOME_KEY                     # "righe_esito": the outcome rows of the card
TYPES = {"text": "testo breve", "long": "testo lungo", "number": "numero", "choice": "una scelta",
         "multi": "più scelte", "bool": "sì / no"}
CARD_DEFAULT = cards.FIELD_NAMES + (OUTCOME_KEY,)
NA_NOTE = "proposed by Claude as not reported: decide (Non riportato) or correct"
YES, NO = "si", "no"

INSTRUCTIONS = [
    "You extract data for a systematic review from the FULL TEXT of one study. Use only the text of this packet "
    "(pages are marked [[p. N]]). Never compute, convert or round: copy numbers as written.",
    "Write ONE JSON file: estrazione/da_inviare/<packet file name>, format 'eurydike-extract-v1', with packet_id and "
    "study_id copied from this packet and 'model' = your model name.",
    "'scheda': the unified article card (format 'orpheus-scheda-v2', version 'cards-v2', workflow_run_id = packet_id, "
    "article_id = the packet's article_id) with ONLY the fields listed in campi_scheda (rules of cards-v2: one row per "
    "value with 'ordine', origin reported / interpreted / not_available, a verbatim sentence in 'citazione' and its "
    "page in 'pagina' — pages only, never 'sezione'). Outcome rows in 'righe_esito' only if campi_scheda lists "
    "'righe_esito'. If campi_scheda is empty, 'scheda' is null.",
    "'valori': one object per field of campi_revisione: campo (the key), valore, citazione, pagina, origin, and "
    "optionally confidenza (high/medium/low) and nota. valore: text for text/long; the number as written for number; "
    "one of the options for choice; a list of options for multi; 'si' or 'no' for bool. origin 'reported' when the "
    "text states it, 'interpreted' when you deduce it (the sentence it rests on in citazione), 'not_available' when "
    "the study does not report it (then valore, citazione and pagina are left out).",
    "citazione: one verbatim sentence (12 to 500 characters) copied from the cited page, without changes.",
]


class ExtractionError(ValueError):
    pass


def ex_dir(conn: sqlite3.Connection, home: Path, review_id: str) -> Path:
    from .protocol import review_dir
    r = conn.execute("SELECT data_dir FROM review WHERE id = ?", (review_id,)).fetchone()
    if r is None:
        raise ExtractionError("revisione non trovata")
    return review_dir(home, r[0]) / EX_DIR


# ------------------------------------------------------------------ the form
def card_fields_catalogue() -> list[dict]:
    """The fields of the unified card a review can take (cards-v1 fields, the new cards-v2 fields, the outcome rows)."""
    out = []
    for k in cards.FIELD_NAMES:
        out.append({"key": k, "gruppo": "scheda (cards-v1)", "descrizione": cards.FIELDS[k]["descrizione"]})
    for k, spec in cards_v2.NEW_FIELDS.items():
        out.append({"key": k, "gruppo": "obiettivo e disegno" if k in cards_v2.LEVEL2_FIELDS else "scheda (cards-v2)",
                    "descrizione": spec["descrizione"]})
    out.append({"key": OUTCOME_KEY, "gruppo": "righe di esito",
                "descrizione": "Outcome rows for the meta-analysis: n, mean, SD (or median and IQR …) per group and time, as written."})
    return out


CARD_KEYS = tuple(x["key"] for x in card_fields_catalogue())


def slug(label: str) -> str:
    t = unicodedata.normalize("NFKD", label or "").encode("ascii", "ignore").decode().lower()
    t = re.sub(r"[^a-z0-9]+", "_", t).strip("_")
    if t and not t[0].isalpha():
        t = "c_" + t
    return t[:40].rstrip("_")


def _form_row(conn: sqlite3.Connection, review_id: str, frozen_only: bool = False) -> sqlite3.Row | None:
    sql = "SELECT * FROM extraction_form WHERE review_id = ?" + (" AND status = 'frozen'" if frozen_only else "")
    return conn.execute(sql + " ORDER BY version DESC LIMIT 1", (review_id,)).fetchone()


def _fields(conn: sqlite3.Connection, form_id: str) -> list[dict]:
    out = []
    for r in conn.execute("SELECT * FROM form_field WHERE form_id = ? ORDER BY ord", (form_id,)):
        out.append({"key": r["key"], "ordine": r["ord"], "sezione": r["section"], "etichetta": r["label"], "tipo": r["type"],
                    "unita": r["unit"], "istruzione": r["hint"], "opzioni": json.loads(r["options"]) if r["options"] else None,
                    "obbligatorio": bool(r["required"]), "dalla_scheda": r["card_key"] is not None})
    return out


def form(conn: sqlite3.Connection, review_id: str) -> dict:
    """The current form (last version) and the frozen one used for the packets."""
    if conn.execute("SELECT 1 FROM review WHERE id = ?", (review_id,)).fetchone() is None:
        raise ExtractionError("revisione non trovata")
    cur, frozen = _form_row(conn, review_id), _form_row(conn, review_id, True)
    fields = _fields(conn, cur["id"]) if cur else []
    return {"id": cur["id"] if cur else None, "versione": cur["version"] if cur else 0,
            "stato": cur["status"] if cur else None, "bloccato_il": cur["frozen_at"] if cur else None,
            "campi_scheda": [f["key"] for f in fields if f["dalla_scheda"]] if cur else list(CARD_DEFAULT),
            "campi_revisione": [f for f in fields if not f["dalla_scheda"]],
            "versione_bloccata": frozen["version"] if frozen else None, "catalogo_scheda": card_fields_catalogue(),
            "tipi": TYPES}


def _clean_field(f: dict, i: int, used: set[str]) -> dict:
    if not isinstance(f, dict):
        raise ExtractionError(f"campo {i}: non leggibile")
    label = str(f.get("etichetta") or "").strip()
    if not label:
        raise ExtractionError(f"campo {i}: manca il nome")
    key = slug(str(f.get("key") or "") or label)
    if not key:
        raise ExtractionError(f"campo {i} ({label}): il nome deve avere almeno una lettera")
    if key in CARD_KEYS:
        raise ExtractionError(f"campo «{label}»: è già un campo della scheda ({key}): spuntalo fra i campi della scheda")
    if key in used:
        raise ExtractionError(f"campo «{label}»: nome ripetuto ({key})")
    used.add(key)
    typ = f.get("tipo") or "text"
    if typ not in TYPES:
        raise ExtractionError(f"campo «{label}»: tipo non ammesso ({typ})")
    opts = None
    if typ in ("choice", "multi"):
        raw = f.get("opzioni")
        raw = raw.split("\n") if isinstance(raw, str) else (raw or [])
        opts = [str(o).strip() for o in raw if str(o).strip()]
        if len(opts) < 2 or len(set(opts)) != len(opts):
            raise ExtractionError(f"campo «{label}»: servono almeno due opzioni diverse")
    txt = lambda k: (str(f.get(k)).strip() or None) if f.get(k) not in (None, "") else None  # noqa: E731
    return {"key": key, "label": label[:200], "type": typ, "unit": txt("unita"), "hint": txt("istruzione"),
            "section": txt("sezione"), "options": opts, "required": 1 if f.get("obbligatorio") else 0}


def save_form(conn: sqlite3.Connection, review_id: str, card_keys: list[str], fields: list[dict], *,
              actor: str = "user") -> dict:
    """A new version (draft) of the form. Nothing is written if it is equal to the current draft."""
    if conn.execute("SELECT 1 FROM review WHERE id = ?", (review_id,)).fetchone() is None:
        raise ExtractionError("revisione non trovata")
    bad = [k for k in card_keys if k not in CARD_KEYS]
    if bad:
        raise ExtractionError("campi della scheda sconosciuti: " + ", ".join(bad))
    keys = [k for k in CARD_KEYS if k in set(card_keys)]
    used: set[str] = set()
    clean = [_clean_field(f, i, used) for i, f in enumerate(fields or [], 1)]
    if not keys and not clean:
        raise ExtractionError("il modulo è vuoto: scegli almeno un campo")
    cur = _form_row(conn, review_id)
    rows = [{"key": k, "label": k, "type": "outcome" if k == OUTCOME_KEY else "text", "unit": None, "hint": None,
             "section": "scheda", "options": None, "required": 0, "card_key": k} for k in keys] + \
           [{**c, "card_key": None} for c in clean]
    if cur is not None:
        old = [(f["key"], f["etichetta"], f["tipo"], f["unita"], f["istruzione"], f["sezione"], f["opzioni"],
                int(f["obbligatorio"]), f["dalla_scheda"]) for f in _fields(conn, cur["id"])]
        new = [(r["key"], r["label"], r["type"], r["unit"], r["hint"], r["section"], r["options"], r["required"],
                r["card_key"] is not None) for r in rows]
        if old == new:
            return {"id": cur["id"], "versione": cur["version"], "stato": cur["status"], "nuova": False}
    version = (cur["version"] if cur else 0) + 1
    fid = new_id("extraction_form")
    with rv._write(conn):
        conn.execute("INSERT INTO extraction_form (id, review_id, version, contract) VALUES (?, ?, ?, ?)",
                     (fid, review_id, version, CONTRACT))
        for i, r in enumerate(rows, 1):
            conn.execute("""INSERT INTO form_field (form_id, key, ord, section, label, type, unit, hint, options, required,
                                                    card_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                         (fid, r["key"], i, r["section"], r["label"], r["type"], r["unit"], r["hint"],
                          json.dumps(r["options"], ensure_ascii=False) if r["options"] else None, r["required"],
                          r["card_key"]))
        audit.record(conn, actor=actor, action="extraction_form_saved", entity_type="review", entity_id=review_id,
                     payload={"form_id": fid, "version": version, "card_fields": keys,
                              "review_fields": [c["key"] for c in clean]}, in_transaction=True)
    return {"id": fid, "versione": version, "stato": "draft", "nuova": True}


def freeze(conn: sqlite3.Connection, review_id: str, *, actor: str = "user") -> dict:
    cur = _form_row(conn, review_id)
    if cur is None:
        raise ExtractionError("non c'è ancora un modulo: salvalo prima")
    if cur["status"] == "frozen":
        return {"id": cur["id"], "versione": cur["version"], "stato": "frozen"}
    with rv._write(conn):
        conn.execute("UPDATE extraction_form SET status = 'frozen', frozen_at = ? WHERE id = ?", (audit.utc_now(), cur["id"]))
        audit.record(conn, actor=actor, action="extraction_form_frozen", entity_type="review", entity_id=review_id,
                     payload={"form_id": cur["id"], "version": cur["version"]}, in_transaction=True)
    return {"id": cur["id"], "versione": cur["version"], "stato": "frozen"}


def _frozen(conn: sqlite3.Connection, review_id: str) -> tuple[sqlite3.Row, list[dict]]:
    f = _form_row(conn, review_id, True)
    if f is None:
        raise ExtractionError("il modulo di estrazione non è ancora bloccato: blocca il modulo prima dei pacchetti")
    return f, _fields(conn, f["id"])


# ------------------------------------------------------------------ studies
def link_articles(conn: sqlite3.Connection, review_id: str, *, actor: str = "user") -> dict:
    """Studies without an ORPHEUS article: linked when their PDF is now verified (article of the document) or an article
    with the record's DOI exists. The card fields need the article (the card is one per article)."""
    n = 0
    for s in conn.execute("""SELECT s.id, d.article_id AS doc_art, r.doi FROM review_study s
                             LEFT JOIN document d ON d.id = s.document_id LEFT JOIN review_record r ON r.id = s.record_id
                             WHERE s.review_id = ? AND s.article_id IS NULL""", (review_id,)).fetchall():
        aid = s["doc_art"]
        if aid is None and s["doi"]:
            a = conn.execute("SELECT id FROM article WHERE lower(doi) = lower(?)", (s["doi"],)).fetchone()
            aid = a[0] if a else None
        if aid:
            with rv._write(conn):
                conn.execute("UPDATE review_study SET article_id = ? WHERE id = ?", (aid, s["id"]))
                audit.record(conn, actor=actor, action="review_study_article_linked", entity_type="review_study",
                             entity_id=s["id"], payload={"article_id": aid}, in_transaction=True)
            n += 1
    return {"collegati": n}


def included(conn: sqlite3.Connection, review_id: str) -> list[sqlite3.Row]:
    return conn.execute("""SELECT s.*, r.title, r.year, r.doi, r.authors, r.journal, d.page_count FROM review_study s
                           LEFT JOIN review_record r ON r.id = s.record_id LEFT JOIN document d ON d.id = s.document_id
                           WHERE s.review_id = ? AND s.ft_decision = 'include' ORDER BY s.created_at, s.rowid""",
                        (review_id,)).fetchall()


def _card_todo(conn: sqlite3.Connection, aid: str | None, card_keys: list[str]) -> list[str]:
    """Card fields of the form still empty for this article (a value already there, proposed or decided, is not asked)."""
    if not aid:
        return []
    have = {r["field_key"] for r in store.current_items(conn, aid)}
    rows = bool(store.current_outcome_rows(conn, aid))
    return [k for k in card_keys if (k == OUTCOME_KEY and not rows) or (k != OUTCOME_KEY and k not in have)]


# ------------------------------------------------------------------ packets for Claude
def open_packets(conn: sqlite3.Connection, review_id: str) -> list[sqlite3.Row]:
    return conn.execute("""SELECT w.* FROM workflow_run w JOIN task t ON t.id = w.task_id
                           WHERE t.idempotency_key LIKE ? AND w.status = 'running'
                           AND json_extract(w.parameters, '$.review_id') = ? ORDER BY w.started_at, w.rowid""",
                        (TASK_PREFIX + "%", review_id)).fetchall()


def _done_studies(conn: sqlite3.Connection, review_id: str, form_id: str) -> set[str]:
    return {r[0] for r in conn.execute("""SELECT json_extract(w.parameters, '$.study_id') FROM workflow_run w
                                          JOIN task t ON t.id = w.task_id WHERE t.idempotency_key LIKE ?
                                          AND w.status = 'succeeded' AND json_extract(w.parameters, '$.review_id') = ?
                                          AND json_extract(w.parameters, '$.form_id') = ?""",
                                       (TASK_PREFIX + "%", review_id, form_id))}


def to_extract(conn: sqlite3.Connection, review_id: str) -> list[str]:
    """Included studies with a PDF, not yet extracted by Claude with the frozen form, without an open packet."""
    f = _form_row(conn, review_id, True)
    if f is None:
        return []
    busy = {json.loads(r["parameters"])["study_id"] for r in open_packets(conn, review_id)}
    done = _done_studies(conn, review_id, f["id"])
    return [s["id"] for s in included(conn, review_id) if s["document_id"] and s["id"] not in busy | done]


def _pages(conn: sqlite3.Connection, document_id: str) -> list[str]:
    return [r[0] or "" for r in conn.execute("SELECT text FROM document_page WHERE document_id = ? ORDER BY page_no",
                                             (document_id,))]


def _spec_card(k: str) -> dict:
    if k == OUTCOME_KEY:
        return {"campo": k, "descrizione": "Outcome rows (righe_esito): outcome, group, timepoint, unit, n and the "
                "statistics as written (mean+sd, or median+q1+q3, or median+min+max, or se, or ci_low+ci_high), the "
                "verbatim sentence or table row in 'quote', its 'page', and 'table' (the table caption) when from a table."}
    if k in cards.FIELDS:
        spec = cards.FIELDS[k]
        return {"campo": k, "descrizione": spec["descrizione"], "obbligatorio": spec["obbligatorio"]}
    spec = cards_v2.NEW_FIELDS[k]
    return {"campo": k, "descrizione": spec["descrizione"], **({"valori": list(spec["valori"])} if "valori" in spec else {})}


def _spec_review(f: dict) -> dict:
    return {"campo": f["key"], "etichetta": f["etichetta"], "tipo": f["tipo"],
            **{k: f[k] for k in ("unita", "istruzione", "opzioni", "sezione") if f.get(k)}}


def packet(conn: sqlite3.Connection, review_id: str, run: dict) -> dict:
    p = run["parameters"]
    r = conn.execute("SELECT * FROM review WHERE id = ?", (review_id,)).fetchone()
    st = conn.execute("SELECT * FROM review_study WHERE id = ?", (p["study_id"],)).fetchone()
    fields = _fields(conn, p["form_id"])
    pages, cut = trim_references(_pages(conn, st["document_id"]))
    card_todo = _card_todo(conn, st["article_id"], [f["key"] for f in fields if f["dalla_scheda"]])
    return {"formato": PACKET_FORMAT, "contratto": CONTRACT, "packet_id": run["id"], "review_id": review_id,
            "study_id": st["id"], "article_id": st["article_id"], "studio": p["numero"], "emesso_il": run["started_at"],
            "modulo": {"id": p["form_id"], "versione": p["versione"]}, "istruzioni": INSTRUCTIONS,
            "protocollo": {"titolo": r["title"], "domanda": r["question"], "picos": json.loads(r["pico"] or "{}")},
            "titolo": st["label"], "campi_scheda": [_spec_card(k) for k in card_todo],
            "scheda_saltata": None if st["article_id"] else
            "the study has no ORPHEUS article yet: the card fields are skipped ('scheda': null)",
            "campi_revisione": [_spec_review(f) for f in fields if not f["dalla_scheda"]],
            "pagine": len(pages), "bibliografia_tolta_da_pagina": cut, "testo": chunk_pages(pages, BUDGET),
            "esempio": {"format": CONTRACT, "packet_id": "run_<copy>", "study_id": "rst_<copy>", "model": "claude-opus-5-5",
                        "scheda": {"formato": cards_v2.FORMAT, "versione": cards_v2.VERSION, "workflow_run_id": "run_<copy>",
                                   "article_id": "art_<copy>", "modello": "claude-opus-5-5",
                                   "valori": [{"campo": "paese", "ordine": 1, "valore": "Italy",
                                               "citazione": "<verbatim sentence>", "pagina": 2, "origin": "reported"}],
                                   "righe_esito": []},
                        "valori": [{"campo": "<key>", "valore": "<value>", "citazione": "<verbatim sentence>", "pagina": 3,
                                    "origin": "reported"}, {"campo": "<key>", "origin": "not_available"}]}}


def issue_packets(conn: sqlite3.Connection, home: Path, review_id: str, n: int, *, project_name: str = "ORPHEUS",
                  project_version: str = "", actor: str = "orpheus eurydike-estrazione") -> dict:
    """Open packets (one study each) until `n` are open; the packets of the open ones are written again."""
    if n > MAX_OPEN:
        raise ExtractionError(f"al massimo {MAX_OPEN} pacchetti aperti alla volta")
    f, _fields_ = _frozen(conn, review_id)
    link_articles(conn, review_id)
    sc.claude_reviewer(conn, review_id)
    opened = open_packets(conn, review_id)
    todo = to_extract(conn, review_id)[:max(0, n - len(opened))]
    folder = ex_dir(conn, home, review_id)
    new_runs = []
    if todo:
        with transaction(conn):
            project_id = card_store._ensure_project(conn, project_name, project_version)
            agent_id = card_store._ensure_agent(conn)
            model_id = card_store._ensure_model(conn, card_store.GENERIC_MODEL)
            n0 = conn.execute("SELECT count(*) FROM task WHERE idempotency_key LIKE ? AND title LIKE ?",
                              (TASK_PREFIX + "%", f"%{review_id}%")).fetchone()[0]
            for i, sid in enumerate(todo, 1):
                num = n0 + i
                task_id = new_id("task")
                conn.execute("""INSERT INTO task (id, project_id, title, priority, status, owner_agent_id, expected_output,
                                                  acceptance_criteria, idempotency_key)
                                VALUES (?, ?, ?, 2, 'ready', ?, ?, ?, ?)""",
                             (task_id, project_id, f"Estrazione ({CONTRACT}) studio {num} della revisione {review_id}",
                              agent_id, f"{EX_DIR}/{TO_SEND}/studio_{num:03d}.json (formato {CONTRACT})",
                              f"supera i controlli di {CONTRACT}", f"{TASK_PREFIX}{sid}:{num}"))
                params = {"contratto": CONTRACT, "review_id": review_id, "study_id": sid, "numero": num,
                          "form_id": f["id"], "versione": f["version"]}
                run_id = new_id("workflow_run")
                conn.execute("""INSERT INTO workflow_run (id, task_id, agent_id, executor, model_id, started_at, status,
                                                          parameters, validation_status)
                                VALUES (?, ?, ?, 'claude_session', ?, ?, 'running', ?, 'pending')""",
                             (run_id, task_id, agent_id, model_id, audit.utc_now(),
                              json.dumps(params, ensure_ascii=False, sort_keys=True)))
                new_runs.append(run_id)
            audit.record(conn, actor=actor, action="extraction_packets_issued", entity_type="review", entity_id=review_id,
                         payload={"contract": CONTRACT, "form_id": f["id"], "new_runs": new_runs}, in_transaction=True)
    written = []
    for run in open_packets(conn, review_id):
        params = json.loads(run["parameters"])
        pk = packet(conn, review_id, {"id": run["id"], "started_at": run["started_at"], "parameters": params})
        path = folder / PACKETS / f"studio_{params['numero']:03d}.json"
        card_store._write_if_changed(path, json.dumps(pk, ensure_ascii=False, indent=1).encode("utf-8"))
        written.append({"studio": params["numero"], "file": str(path), "nuovo": run["id"] in new_runs})
    (folder / TO_SEND).mkdir(parents=True, exist_ok=True)
    return {"aperti": written, "nuovi": len(new_runs), "da_estrarre": len(to_extract(conn, review_id)),
            "cartella": str(folder)}


# ------------------------------------------------------------------ checks of Claude's answer
def _ws(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text or "")).strip()


def page_texts(pk: dict) -> dict[int, str]:
    out: dict[int, str] = {}
    for block in pk.get("testo") or []:
        for m in re.finditer(r"\[\[p\. (\d+)\]\]\n(.*?)(?=\n?\[\[p\. \d+\]\]|\Z)", block, re.S):
            out[int(m.group(1))] = out.get(int(m.group(1)), "") + m.group(2)
    return out


def quote_check(quote: str, page_text: str) -> str:
    """exact (same text but for spaces), fuzzy (found with the tolerant forms of cards-v1), not_found."""
    if _ws(quote) in _ws(page_text):
        return "exact"
    return "fuzzy" if cards.quote_in_text(quote, cards.text_forms(page_text)) else "not_found"


def _check_value(i: int, v, spec: dict | None, texts: dict[int, str]) -> list[str]:
    if not isinstance(v, dict):
        return [f"valore {i}: deve essere un oggetto"]
    lab = f"valore {i} ({v.get('campo')})"
    if spec is None:
        return [f"{lab}: campo non nel modulo della revisione (campi_revisione)"]
    probs = []
    extra = set(v) - {"campo", "valore", "citazione", "pagina", "origin", "confidenza", "nota"}
    if extra:
        probs.append(f"{lab}: campi non previsti: {', '.join(sorted(extra))}")
    origin = v.get("origin")
    if origin not in cards.ORIGINS:
        return probs + [f"{lab}: origin reported, interpreted o not_available"]
    if v.get("confidenza") not in (None, "high", "medium", "low"):
        probs.append(f"{lab}: confidenza high, medium o low")
    if v.get("nota") is not None and (not isinstance(v["nota"], str) or len(v["nota"]) > 500):
        probs.append(f"{lab}: nota di al massimo 500 caratteri")
    if origin == "not_available":
        left = [k for k in ("valore", "citazione", "pagina") if v.get(k) not in (None, "", [])]
        return probs + ([f"{lab}: con 'not_available' lascia vuoti {', '.join(left)}"] if left else [])
    val, quote, page = v.get("valore"), v.get("citazione"), v.get("pagina")
    typ = spec["tipo"]
    if typ == "multi":
        if not isinstance(val, list) or not val or not all(isinstance(x, str) for x in val):
            probs.append(f"{lab}: per «più scelte» il valore è una lista di opzioni")
        elif any(x not in spec["opzioni"] for x in val) or len(set(val)) != len(val):
            probs.append(f"{lab}: opzioni ammesse: {', '.join(spec['opzioni'])}")
    elif not isinstance(val, str) or not val.strip():
        probs.append(f"{lab}: manca il valore")
    elif len(val) > cards.VALUE_MAX * 4:
        probs.append(f"{lab}: valore troppo lungo")
    elif typ == "choice" and val not in spec["opzioni"]:
        probs.append(f"{lab}: valore '{val}' non ammesso (ammessi: {', '.join(spec['opzioni'])})")
    elif typ == "bool" and val not in (YES, NO):
        probs.append(f"{lab}: valore 'si' o 'no'")
    elif typ == "number" and not (cards.numbers(val) or cards.number_words(val)):
        probs.append(f"{lab}: il valore '{val}' non contiene un numero")
    if not isinstance(quote, str) or not quote.strip():
        return probs + [f"{lab}: manca la citazione (la frase del testo)"]
    if len(quote) > cards.QUOTE_MAX:
        probs.append(f"{lab}: citazione di al massimo {cards.QUOTE_MAX} caratteri")
    if not isinstance(page, int) or isinstance(page, bool) or page not in texts:
        return probs + [f"{lab}: pagina: il numero di una pagina del pacchetto"]
    if quote_check(quote, texts[page]) == "not_found":
        probs.append(f"{lab}: la frase non è a pagina {page} (copiala come è scritta)")
    if typ == "number" and origin == "reported" and isinstance(val, str) and cards.missing_numbers(val, quote):
        probs.append(f"{lab}: il numero '{val}' non è scritto nella frase (niente valori calcolati)")
    return probs


def check(data, pk: dict) -> tuple[list[str], object]:
    """Problems of one answer against its packet (empty = acceptable) and the cards-v2 card (or None)."""
    if not isinstance(data, dict):
        return ["la risposta deve essere un oggetto JSON"], None
    probs = []
    if data.get("format") != CONTRACT:
        probs.append(f"format deve essere «{CONTRACT}»")
    if data.get("packet_id") != pk["packet_id"]:
        probs.append("packet_id diverso da quello del pacchetto")
    if data.get("study_id") != pk["study_id"]:
        probs.append("study_id diverso da quello del pacchetto")
    if not isinstance(data.get("model"), str) or not data["model"].strip():
        probs.append("model mancante")
    extra = set(data) - {"format", "packet_id", "study_id", "model", "scheda", "valori", "note"}
    if extra:
        probs.append("campi non previsti: " + ", ".join(sorted(extra)))
    texts = page_texts(pk)
    # the card part
    wanted = [c["campo"] for c in pk.get("campi_scheda") or []]
    card = None
    scheda = data.get("scheda")
    if not wanted:
        if scheda not in (None, {}):
            probs.append("scheda: il pacchetto non chiede campi della scheda (scheda: null)")
    elif not isinstance(scheda, dict):
        probs.append("scheda: serve la scheda cards-v2 con i campi di campi_scheda")
    else:
        if scheda.get("workflow_run_id") != pk["packet_id"]:
            probs.append("scheda.workflow_run_id deve essere il packet_id")
        if scheda.get("article_id") != pk.get("article_id"):
            probs.append("scheda.article_id deve essere l'article_id del pacchetto")
        if scheda.get("righe_esito") and OUTCOME_KEY not in wanted:
            probs.append("scheda.righe_esito: il pacchetto non chiede righe di esito")
        fields = tuple(k for k in wanted if k != OUTCOME_KEY)
        out = cards_v2.validate(scheda, texts, {}, wanted=fields)
        probs += ["scheda " + p for p in out.problems()]
        card = out.card
        if card is not None:
            for i, v in enumerate(card.valori, 1):
                if v.origin != "not_available" and v.pagina is None:
                    probs.append(f"scheda {cards_v2._label(i, v)}: serve la pagina del PDF (non la sezione)")
            found = cards.check_quotes(SimpleNamespace(valori=[v for v in card.valori if v.pagina is not None]), texts, {})
            probs += ["scheda " + p for p in found.problems]
    # the fields of the review
    specs = {c["campo"]: c for c in pk.get("campi_revisione") or []}
    vals = data.get("valori")
    if vals is None:
        vals = []
    if not isinstance(vals, list):
        probs.append("valori: una lista")
        vals = []
    seen: dict[str, int] = {}
    for i, v in enumerate(vals, 1):
        key = v.get("campo") if isinstance(v, dict) else None
        probs += _check_value(i, v, specs.get(key), texts)
        if isinstance(key, str):
            seen[key] = seen.get(key, 0) + 1
    for k in specs:
        if seen.get(k, 0) == 0:
            probs.append(f"manca il campo {k} (se lo studio non lo riporta: origin 'not_available')")
        elif seen[k] > 1:
            probs.append(f"il campo {k} è ripetuto: una riga per campo")
    return probs, card


def check_with_packet_files(answer: Path, pk_path: Path) -> list[str]:
    try:
        data = json.loads(Path(answer).read_text(encoding="utf-8-sig"))
        pk = json.loads(Path(pk_path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        return [f"file non leggibile: {exc}"]
    return check(data, pk)[0]


def _record_values(conn: sqlite3.Connection, pk: dict, data: dict, run_id: str, claude_id: str) -> int:
    texts = page_texts(pk)
    form_id = pk["modulo"]["id"]
    n = 0
    for v in data.get("valori") or []:
        na = v["origin"] == "not_available"
        qc = "none" if na else ("exact" if quote_check(v["citazione"], texts[v["pagina"]]) == "exact" else "fuzzy")
        note = NA_NOTE if na else (v.get("nota") or ("interpreted" if v["origin"] == "interpreted" else None))
        old = conn.execute("SELECT id FROM extraction_value WHERE study_id = ? AND reviewer_id = ? AND field_key = ? "
                           "AND superseded_by IS NULL", (pk["study_id"], claude_id, v["campo"])).fetchone()
        vid = new_id("extraction_value")
        if old:
            conn.execute("UPDATE extraction_value SET superseded_by = ? WHERE id = ?", (vid, old[0]))
        conn.execute("""INSERT INTO extraction_value (id, study_id, reviewer_id, form_id, field_key, value, quote, page_no,
                                                      quote_check, confidence, note, state, workflow_run_id)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ai', ?)""",
                     (vid, pk["study_id"], claude_id, form_id, v["campo"],
                      None if na else json.dumps(v["valore"], ensure_ascii=False), None if na else v["citazione"],
                      None if na else v["pagina"], qc, v.get("confidenza"), note, run_id))
        n += 1
    return n


def submit(conn: sqlite3.Connection, home: Path, review_id: str, *, actor: str = "claude_session") -> dict:
    """The answers in estrazione/da_inviare checked again against the database; accepted ones become proposals."""
    folder = ex_dir(conn, home, review_id)
    results = []
    for path in sorted((folder / TO_SEND).glob("*.json")) if (folder / TO_SEND).is_dir() else []:
        raw = path.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        res = {"file": path.name}
        run, params, stop, card, pk, data = None, {}, None, None, None, None
        try:
            data = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            stop = f"file non leggibile o non JSON: {exc}"
        if stop is None:
            pid = data.get("packet_id") if isinstance(data, dict) else None
            run = conn.execute("SELECT * FROM workflow_run WHERE id = ?", (pid,)).fetchone() if isinstance(pid, str) else None
            params = json.loads(run["parameters"]) if run else {}
            if run is None or params.get("contratto") != CONTRACT:
                stop = "packet_id mancante o non è un pacchetto di estrazione di questo database"
            elif params.get("review_id") != review_id:
                stop = "il pacchetto è di un'altra revisione"
            elif run["status"] != "running":
                stop = "pacchetto già accettato o non più valido"
        if stop:
            probs = [stop]
        else:
            pk = packet(conn, review_id, {"id": run["id"], "started_at": run["started_at"], "parameters": params})
            probs, card = check(data, pk)
        n_vals, card_out = 0, None
        if not probs:
            claude_id = sc.claude_reviewer(conn, review_id)
            if card is not None:
                card_out = cards_v2.record(conn, card, actor=actor)
            with rv._write(conn):
                n_vals = _record_values(conn, pk, data, run["id"], claude_id)
        with transaction(conn):
            if run is not None:
                conn.execute("""INSERT INTO validation_result (id, workflow_run_id, check_name, outcome, validator, details)
                                VALUES (?, ?, ?, ?, 'deterministic', ?)""",
                             (new_id("validation_result"), run["id"], CONTRACT, "fail" if probs else "pass",
                              json.dumps({"file": path.name, "file_sha256": sha, "problemi": probs[:50]}, ensure_ascii=False)))
                if not probs:
                    conn.execute("""UPDATE workflow_run SET status = 'succeeded', finished_at = ?, validation_status = 'pass',
                                    model_id = ? WHERE id = ?""",
                                 (max(audit.utc_now(), run["started_at"]), card_store._ensure_model(conn, data["model"]),
                                  run["id"]))
                    conn.execute("UPDATE task SET status = 'done' WHERE id = ?", (run["task_id"],))
            audit.record(conn, actor="orpheus eurydike-estrazione",
                         action="extraction_answer_" + ("refused" if probs else "accepted"), entity_type="review",
                         entity_id=review_id, payload={"file_sha256": sha, "workflow_run_id": run["id"] if run else None,
                                                       "values": n_vals, "card": card_out}, in_transaction=True)
        motivi = path.with_name(path.stem + ".motivi.txt")
        if probs:
            motivi.write_text(f"Risposta rifiutata ({CONTRACT}):\n- " + "\n- ".join(probs[:60]) + "\n", encoding="utf-8")
            res |= {"esito": "rifiutata", "motivi": probs[:20]}
        else:
            dest = folder / ACCEPTED / path.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                dest = dest.with_name(f"{path.stem}_{sha[:8]}{path.suffix}")
            path.replace(dest)
            if motivi.exists():
                motivi.unlink()
            res |= {"esito": "accettata", "valori": n_vals, "scheda": card_out}
        results.append(res)
    return {"inviate": len(results), "accettate": sum(r["esito"] == "accettata" for r in results),
            "rifiutate": sum(r["esito"] != "accettata" for r in results), "risposte": results}


# ------------------------------------------------------------------ the user's decisions on the review fields
def _current_value(conn: sqlite3.Connection, study_id: str, reviewer_id: str, key: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM extraction_value WHERE study_id = ? AND reviewer_id = ? AND field_key = ? "
                        "AND superseded_by IS NULL", (study_id, reviewer_id, key)).fetchone()


def _study(conn: sqlite3.Connection, review_id: str, study_id: str) -> sqlite3.Row:
    st = conn.execute("SELECT * FROM review_study WHERE id = ?", (study_id,)).fetchone()
    if st is None or st["review_id"] != review_id:
        raise ExtractionError("studio non trovato in questa revisione")
    return st


def _parse_user_value(spec: dict, value) -> str:
    typ = spec["tipo"]
    if typ == "multi":
        items = value if isinstance(value, list) else [x.strip() for x in str(value or "").split("\n")]
        items = [x for x in items if x]
        if not items or any(x not in spec["opzioni"] for x in items):
            raise ExtractionError(f"scegli fra: {', '.join(spec['opzioni'])}")
        return json.dumps(items, ensure_ascii=False)
    val = str(value if value is not None else "").strip()
    if not val:
        raise ExtractionError("scrivi il valore corretto (o usa Non riportato)")
    if typ == "choice" and val not in spec["opzioni"]:
        raise ExtractionError(f"scegli fra: {', '.join(spec['opzioni'])}")
    if typ == "bool" and val not in (YES, NO):
        raise ExtractionError("scrivi «si» o «no»")
    if typ == "number" and not (cards.numbers(val) or cards.number_words(val)):
        raise ExtractionError("il valore deve contenere un numero")
    return json.dumps(val, ensure_ascii=False)


def decide_value(conn: sqlite3.Connection, review_id: str, study_id: str, key: str, state: str, *, value=None,
                 quote: str | None = None, page: int | None = None, note: str | None = None,
                 label: str | None = None, actor: str = "user") -> dict:
    """The user's decision on one field of the review: confirm Claude's value, correct it, or mark it not reported."""
    if state not in store.HUMAN_STATES:
        raise ExtractionError("stato non ammesso (confirmed, edited o nr)")
    st = _study(conn, review_id, study_id)
    f, fields = _frozen(conn, review_id)
    spec = next((x for x in fields if x["key"] == key and not x["dalla_scheda"]), None)
    if spec is None:
        raise ExtractionError("campo non nel modulo della revisione (i campi della scheda si decidono nella scheda)")
    me = sc.human_reviewer(conn, review_id, label)
    claude = _current_value(conn, study_id, sc.claude_reviewer(conn, review_id), key)
    mine = _current_value(conn, study_id, me["id"], key)
    row = {"value": None, "quote": None, "page_no": None, "quote_check": "none", "adopted_from": None, "note": note}
    if state == "confirmed":
        if claude is None or claude["value"] is None:
            raise ExtractionError("niente da confermare: Claude non ha un valore per questo campo (usa Correggi)")
        row.update(value=claude["value"], quote=claude["quote"], page_no=claude["page_no"],
                   quote_check=claude["quote_check"], adopted_from=claude["reviewer_id"], note=note or claude["note"])
    elif state == "edited":
        row["value"] = _parse_user_value(spec, value)
        if quote and quote.strip():
            if not page:
                raise ExtractionError("con la frase scrivi anche la pagina")
            texts = {i: t for i, t in enumerate(_pages(conn, st["document_id"]), 1)} if st["document_id"] else {}
            if page not in texts:
                raise ExtractionError("la pagina non è nel PDF dello studio")
            row.update(quote=quote.strip(), page_no=int(page), quote_check=quote_check(quote, texts[int(page)]))
    vid = new_id("extraction_value")
    with rv._write(conn):
        if mine is not None:
            conn.execute("UPDATE extraction_value SET superseded_by = ? WHERE id = ?", (vid, mine["id"]))
        conn.execute("""INSERT INTO extraction_value (id, study_id, reviewer_id, form_id, field_key, value, quote, page_no,
                                                      quote_check, note, state, adopted_from)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                     (vid, study_id, me["id"], f["id"], key, row["value"], row["quote"], row["page_no"], row["quote_check"],
                      row["note"], state, row["adopted_from"]))
        audit.record(conn, actor=actor, action=f"extraction_value_{state}", entity_type="extraction_value", entity_id=vid,
                     payload={"study_id": study_id, "field_key": key, "replaces": mine["id"] if mine else None,
                              "quote_check": row["quote_check"]}, in_transaction=True)
    return {"id": vid, "stato": state, "citazione": row["quote_check"]}


def confirm_exact(conn: sqlite3.Connection, review_id: str, study_id: str, *, label: str | None = None,
                  actor: str = "user") -> dict:
    """In one go: every value still to check whose sentence was found exactly — the review fields here and the card
    values of the article (`store.confirm_exact`). The others stay to be checked one by one."""
    st = _study(conn, review_id, study_id)
    view = study(conn, review_id, study_id, label=label)
    n = 0
    for x in view["valori_revisione"]:
        if x["stato"] == "ai" and x["controllo_citazione"] == "exact":
            decide_value(conn, review_id, study_id, x["campo"], "confirmed", label=label, actor=actor)
            n += 1
    card = store.confirm_exact(conn, st["article_id"], actor=actor) if st["article_id"] else {"confermati": 0}
    return {"confermati": n + card["confermati"], "campi_revisione": n, "scheda": card["confermati"]}


# ------------------------------------------------------------------ the pages of the dashboard
def _shown(raw: str | None):
    return None if raw is None else json.loads(raw)


def study(conn: sqlite3.Connection, review_id: str, study_id: str, *, label: str | None = None) -> dict:
    """One study for the extraction page: the card values of the form's card fields and the review fields."""
    st = _study(conn, review_id, study_id)
    f = _form_row(conn, review_id, True)
    fields = _fields(conn, f["id"]) if f else []
    card_keys = [x["key"] for x in fields if x["dalla_scheda"]]
    rec = conn.execute("SELECT title, year, doi, authors, journal FROM review_record WHERE id = ?",
                       (st["record_id"],)).fetchone() if st["record_id"] else None
    pages = conn.execute("SELECT page_count FROM document WHERE id = ?", (st["document_id"],)).fetchone() if st["document_id"] else None
    card = None
    if st["article_id"] and card_keys:
        try:
            c = store.card(conn, st["article_id"])
        except store.StoreError:
            c = None
        if c is not None:
            keep = [x for x in c["valori"] if x["campo"] in card_keys]
            rows = c[OUTCOME_KEY] if OUTCOME_KEY in card_keys else []
            card = {"valori": keep, OUTCOME_KEY: rows,
                    "mancanti": [k for k in card_keys if k != OUTCOME_KEY and not any(x["campo"] == k for x in keep)],
                    "da_controllare": sum(1 for x in keep + rows if x["da_controllare"]),
                    "esatte_da_confermare": sum(1 for x in keep + rows if x["da_controllare"] and x["controllo_citazione"] == "exact")}
    claude_id = sc._claude_id(conn, review_id)
    try:
        me = sc.human_reviewer(conn, review_id, label)["id"]
    except sc.ScreeningError:
        me = None
    out_vals = []
    for x in (y for y in fields if not y["dalla_scheda"]):
        cv = _current_value(conn, study_id, claude_id, x["key"]) if claude_id else None
        mv = _current_value(conn, study_id, me, x["key"]) if me else None
        use = mv or cv
        out_vals.append({"campo": x["key"], "etichetta": x["etichetta"], "tipo": x["tipo"], "opzioni": x["opzioni"],
                         "unita": x["unita"], "sezione": x["sezione"],
                         "stato": use["state"] if use else "vuoto", "valore": _shown(use["value"]) if use else None,
                         "citazione": use["quote"] if use else None, "pagina": use["page_no"] if use else None,
                         "controllo_citazione": use["quote_check"] if use else "none", "nota": use["note"] if use else None,
                         "claude": ({"valore": _shown(cv["value"]), "citazione": cv["quote"], "pagina": cv["page_no"],
                                     "controllo_citazione": cv["quote_check"], "nota": cv["note"]}
                                    if cv and mv and mv["state"] in ("edited", "nr") else None)})
    return {"studio": {"id": st["id"], "etichetta": st["label"], "titolo": rec["title"] if rec else st["label"],
                       "anno": rec["year"] if rec else None, "doi": rec["doi"] if rec else None,
                       "autori": rec["authors"] if rec else None, "rivista": rec["journal"] if rec else None,
                       "articolo": st["article_id"], "documento": st["document_id"], "pagine": pages[0] if pages else None},
            "modulo": {"versione": f["version"] if f else None}, "scheda": card, "valori_revisione": out_vals,
            "esatte_da_confermare": sum(1 for v in out_vals if v["stato"] == "ai" and v["controllo_citazione"] == "exact")
            + (card["esatte_da_confermare"] if card else 0)}


def board(conn: sqlite3.Connection, home: Path, review_id: str, *, label: str | None = None) -> dict:
    """The included studies with what is left to check, the form and Claude's packets."""
    fm = form(conn, review_id)
    frozen = _form_row(conn, review_id, True)
    rows = []
    for s in included(conn, review_id):
        v = study(conn, review_id, s["id"], label=label) if frozen else None
        todo_rev = sum(1 for x in v["valori_revisione"] if x["stato"] in ("ai", "vuoto")) if v else None
        rows.append({"id": s["id"], "titolo": s["title"] or s["label"], "anno": s["year"], "pdf": bool(s["document_id"]),
                     "pagine": s["page_count"], "articolo": s["article_id"],
                     "scheda_da_controllare": v["scheda"]["da_controllare"] if v and v["scheda"] else 0,
                     "revisione_da_controllare": todo_rev, "estratto_da_claude": bool(
                         frozen and s["id"] in _done_studies(conn, review_id, frozen["id"]))})
    return {"modulo": fm, "studi": rows, "cartella": str(ex_dir(conn, home, review_id)),
            "conteggi": {"inclusi": len(rows), "senza_pdf": sum(not r["pdf"] for r in rows),
                         "senza_articolo": sum(not r["articolo"] for r in rows),
                         "estratti": sum(r["estratto_da_claude"] for r in rows),
                         "da_controllare": sum((r["scheda_da_controllare"] or 0) + (r["revisione_da_controllare"] or 0)
                                               for r in rows)},
            "claude": {"pacchetti_aperti": len(open_packets(conn, review_id)), "da_estrarre": len(to_extract(conn, review_id))}}
