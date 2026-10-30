"""T-056 — diary of the user's submissions and lessons from editors and reviewers (migration 0017, contract
feedback-v1, ADR-0016).

Decisions of the user (2026-10-27 17:1x): submissions and decisions are entered in the dashboard (windows "Nuova
sottomissione" and "Aggiungi decisione" with the letter pasted); Claude in a session classifies the comments and the
user confirms or corrects them in the dashboard; whole letters and comments are kept on the PC only (R10); past
submissions can be entered too. Migration 0017 approved at 17:20.

R10, everywhere in this module: the text of letters and comments, and the labels of the user's papers, never go into
the output of a command (it is logged and the logs reach GitHub), the audit payloads, the sharing package (sharing.py
never reads these tables) or a chat. Commands print counts and ids only; the text is read in the dashboard (local) or by
Claude in a session on the PC (packets in rapporti/riscontri, which stays on the PC).

Flow of the classification (like the cards, ADR-0008):
  1. `orpheus riscontri` (riscontri.cmd): one task and one workflow_run per letter with comments still without a label,
     and the packet rapporti/riscontri/pacchetti/<letter_id>.json (journal, article type, events, comments, categories).
  2. Claude writes rapporti/riscontri/da_inviare/<letter_id>.json and checks it with `orpheus riscontri --controlla`.
  3. `orpheus riscontri` again: every comment classified exactly once, values from the lists, the quote an exact piece of
     the comment; accepted labels enter as proposals (`ai`); the user confirms or corrects them in the dashboard.
Lessons are counts by journal and category, always with N (comments, submissions); never a prediction.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import statistics
from datetime import date
from pathlib import Path

from . import audit, card_store
from .db import transaction
from .ids import new_id
from .quality import REPORTS_DIR

CONTRACT = "feedback-v1"
PACKET_FORMAT, CARD_FORMAT = "orpheus-pacchetto-riscontri-v1", "orpheus-riscontri-v1"
FOLDER = "riscontri"
TASK_KEY = "T-056:" + CONTRACT + ":{letter_id}"

EVENTS = {"inviato": "Inviato", "rifiutato_senza_revisione": "Rifiutato senza revisione", "in_revisione": "In revisione",
          "revisione_maggiore": "Revisione maggiore", "revisione_minore": "Revisione minore",
          "revisione_inviata": "Revisione inviata", "accettato": "Accettato", "rifiutato": "Rifiutato",
          "ritirato": "Ritirato", "trasferito": "Trasferito ad altra rivista"}
DECISIONS = ("rifiutato_senza_revisione", "revisione_maggiore", "revisione_minore", "accettato", "rifiutato")
CLOSED = ("accettato", "rifiutato", "rifiutato_senza_revisione", "ritirato", "trasferito")
KINDS = ("originale", "revisione", "protocollo", "altro")
CATEGORIES = {
    "novita_rilevanza": ("Novità e rilevanza", "Novelty, relevance, contribution, interest for the readers"),
    "disegno": ("Disegno dello studio", "Study design, control group, randomisation, blinding, protocol, procedures"),
    "campione_potenza": ("Campione e potenza", "Sample size, power calculation, recruitment, inclusion/exclusion"),
    "statistica": ("Analisi statistica", "Statistical tests or models, assumptions, effect sizes, CIs, multiplicity"),
    "presentazione_risultati": ("Presentazione dei risultati", "Results reporting, tables, figures, numbers"),
    "discussione_limiti": ("Discussione e limiti", "Interpretation, overstatement, limitations, practical applications"),
    "scrittura_lingua": ("Scrittura e lingua", "Clarity, English, structure, length, terminology"),
    "riferimenti": ("Riferimenti", "Missing, outdated or wrong references"),
    "linee_guida_reporting": ("Linee guida di reporting", "CONSORT, STROBE, PRISMA, checklists, flow diagram"),
    "etica_registrazione": ("Etica e registrazione", "Ethics approval, consent, trial or protocol registration, data"),
    "fuori_ambito": ("Fuori ambito", "Out of the journal's scope, better suited elsewhere"),
    "formato_rivista": ("Formato della rivista", "Journal format, word limits, sections, style, files"),
    "altro": ("Altro", "None of the above"),
}
SEVERITIES = {"bloccante": "Bloccante", "importante": "Importante", "minore": "Minore"}
FOLLOWED = {"si": "Sì", "in_parte": "In parte", "no": "No", "non_ancora": "Non ancora"}
QUOTE_MIN = 10
_DATE = re.compile(r"^[12]\d{3}-[01]\d-[0-3]\d$")

INSTRUCTIONS = [
    "Write ONE JSON file: rapporti/riscontri/da_inviare/<letter_id>.json, format 'orpheus-riscontri-v1', versione "
    "'feedback-v1', with workflow_run_id and letter_id copied from this packet and 'modello' = your model name.",
    "Every comment of 'commenti' exactly once, by its key ('R1.3', 'ED.1'): categoria and gravita from the lists, "
    "'citazione' = an exact, continuous piece of that comment (at least 10 characters) that shows the category.",
    "One category per comment: the main request. A comment that asks several things: the one that weighs most.",
    "gravita: 'bloccante' = the editor or reviewer makes acceptance depend on it or it led to the rejection; "
    "'importante' = a substantive change is asked; 'minore' = wording, typos, small additions.",
    "seguito (optional): only when the comment has 'azione_nella_risposta' (the accepted response to the reviewers): "
    "modificato -> si, chiarito -> in_parte, non modificato -> no; otherwise leave it out.",
    "Never write the text of the comments anywhere else: R10 (the letter stays on the PC).",
]


class SubmissionError(ValueError):
    pass


def _d(x, what: str, *, required: bool = True) -> str | None:
    x = (str(x).strip() if x is not None else "") or None
    if x is None:
        if required:
            raise SubmissionError(f"manca {what} (AAAA-MM-GG)")
        return None
    if not _DATE.match(x):
        raise SubmissionError(f"{what}: data non valida '{x}' (AAAA-MM-GG)")
    try:
        date.fromisoformat(x)
    except ValueError as exc:
        raise SubmissionError(f"{what}: data non valida '{x}'") from exc
    return x


def _t(x) -> str | None:
    return (str(x).strip() or None) if x is not None else None


# ---------------------------------------------------------------- writing (dashboard)
def add_manuscript(conn: sqlite3.Connection, label: str, *, title: str | None = None, kind: str | None = None,
                   note: str | None = None, actor: str = "dashboard") -> str:
    label = _t(label)
    if not label:
        raise SubmissionError("manca il nome del lavoro")
    if kind and kind not in KINDS:
        raise SubmissionError(f"tipo di lavoro non ammesso: {kind}")
    if conn.execute("SELECT 1 FROM my_manuscript WHERE label = ?", (label,)).fetchone():
        raise SubmissionError("esiste già un lavoro con questo nome")
    mid = new_id("my_manuscript")
    with transaction(conn):
        conn.execute("INSERT INTO my_manuscript (id, label, title, article_kind, note) VALUES (?, ?, ?, ?, ?)",
                     (mid, label, _t(title), kind or None, _t(note)))
        audit.record(conn, actor=actor, action="my_manuscript_added", entity_type="my_manuscript", entity_id=mid,
                     payload={}, in_transaction=True)
    return mid


def add_submission(conn: sqlite3.Connection, manuscript_id: str, journal_title: str, submitted_on: str, *,
                   journal_id: str | None = None, article_type: str | None = None, number: str | None = None,
                   previous_id: str | None = None, note: str | None = None, actor: str = "dashboard") -> str:
    if not conn.execute("SELECT 1 FROM my_manuscript WHERE id = ?", (manuscript_id,)).fetchone():
        raise SubmissionError("lavoro non trovato")
    if journal_id:
        row = conn.execute("SELECT title FROM journal WHERE id = ?", (journal_id,)).fetchone()
        if row is None:
            raise SubmissionError("rivista non trovata")
        journal_title = journal_title or row[0]
    if not _t(journal_title):
        raise SubmissionError("manca la rivista")
    if previous_id and not conn.execute("SELECT 1 FROM submission WHERE id = ?", (previous_id,)).fetchone():
        raise SubmissionError("sottomissione precedente non trovata")
    day = _d(submitted_on, "data di invio")
    sid = new_id("submission")
    with transaction(conn):
        conn.execute("""INSERT INTO submission (id, manuscript_id, journal_id, journal_title, article_type,
                        manuscript_number, submitted_on, previous_id, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                     (sid, manuscript_id, journal_id or None, _t(journal_title), _t(article_type), _t(number), day,
                      previous_id or None, _t(note)))
        conn.execute("INSERT INTO submission_event (id, submission_id, event, event_on) VALUES (?, ?, 'inviato', ?)",
                     (new_id("submission_event"), sid, day))
        audit.record(conn, actor=actor, action="submission_added", entity_type="submission", entity_id=sid,
                     payload={"journal_id": journal_id}, in_transaction=True)
    return sid


def add_event(conn: sqlite3.Connection, submission_id: str, event: str, event_on: str, *, round_no: int = 1,
              letter: str | None = None, due_on: str | None = None, note: str | None = None,
              actor: str = "dashboard") -> dict:
    """One step of a submission, with the decision letter pasted (kept whole, never changed). The comments are written
    by the user one at a time (add_comment) or the letter is marked "no comments" (migration 0018, user's request of
    2026-10-27 18:44: not every line of the letter is a comment)."""
    if not current_submission(conn, submission_id):
        raise SubmissionError("sottomissione non trovata")
    if event not in EVENTS:
        raise SubmissionError(f"evento non ammesso: {event}")
    day = _d(event_on, "data")
    due = _d(due_on, "scadenza", required=False)
    try:
        round_no = int(round_no or 1)
    except (TypeError, ValueError) as exc:
        raise SubmissionError("giro di revisione non valido") from exc
    if round_no < 1:
        raise SubmissionError("giro di revisione non valido")
    text = (letter or "").replace("\r\n", "\n").strip()
    out = {"lettera": None, "commenti": 0}
    with transaction(conn):
        letter_id = None
        if text:
            sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
            old = conn.execute("SELECT id FROM submission_letter WHERE submission_id = ? AND text_sha256 = ?",
                               (submission_id, sha)).fetchone()
            if old:
                letter_id = old[0]
            else:
                letter_id = new_id("submission_letter")
                conn.execute("""INSERT INTO submission_letter (id, submission_id, received_on, text, text_sha256)
                                VALUES (?, ?, ?, ?, ?)""", (letter_id, submission_id, day, text, sha))
            out["lettera"] = letter_id
        eid = new_id("submission_event")
        conn.execute("""INSERT INTO submission_event (id, submission_id, event, event_on, round, letter_id, due_on, note)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", (eid, submission_id, event, day, round_no, letter_id, due,
                                                             _t(note)))
        audit.record(conn, actor=actor, action="submission_event_added", entity_type="submission",
                     entity_id=submission_id, payload={"event": event, "letter_id": letter_id,
                                                       "comments": out["commenti"]}, in_transaction=True)
    out["evento"] = eid
    out["suggerimento"] = suggest_event(event, text)
    return out


DESK = re.compile(r"decided against publishing|(?:does|did) not reach (?:a )?sufficient priority|without (?:external |peer[- ])?review|"
                  r"not (?:to )?(?:be )?sen[dt] (?:it |your manuscript )?(?:out )?for (?:external |peer[- ])?review|"
                  r"(?:outside|beyond) the scope of|not suitable for (?:publication in|the scope of)|editor(?:ial)? (?:triage|screening)",
                  re.I)


def suggest_event(event: str, letter: str | None) -> str | None:
    """'rifiutato_senza_revisione' when a rejection letter reads like a desk rejection; the user decides."""
    if event == "rifiutato" and letter and DESK.search(letter):
        return "rifiutato_senza_revisione"
    return None


def remove_event(conn: sqlite3.Connection, event_id: str, *, actor: str = "dashboard") -> None:
    """A step entered by mistake: kept with the time it was removed, no longer shown or counted."""
    old = conn.execute("SELECT * FROM submission_event WHERE id = ? AND superseded_by IS NULL AND removed_at IS NULL",
                       (event_id,)).fetchone()
    if old is None:
        raise SubmissionError("passo non trovato (o già tolto)")
    with transaction(conn):
        conn.execute("UPDATE submission_event SET removed_at = ? WHERE id = ?", (audit.utc_now(), event_id))
        audit.record(conn, actor=actor, action="submission_event_removed", entity_type="submission",
                     entity_id=old["submission_id"], payload={"event_id": event_id}, in_transaction=True)


REVIEWERS = ("ED",) + tuple(f"R{i}" for i in range(1, 10))


def add_comment(conn: sqlite3.Connection, letter_id: str, reviewer: str, text: str, *, actor: str = "dashboard") -> dict:
    """One reason or comment written by the user (Editor or Reviewer n) under a letter; the "+" of the page."""
    letter = conn.execute("SELECT * FROM submission_letter WHERE id = ?", (letter_id,)).fetchone()
    if letter is None:
        raise SubmissionError("lettera non trovata")
    if reviewer not in REVIEWERS:
        raise SubmissionError("chi: Editor o Revisore 1-9")
    text = (text or "").replace("\r\n", "\n").strip()
    if len(squash(text)) < QUOTE_MIN:
        raise SubmissionError(f"scrivi la motivazione o il commento (almeno {QUOTE_MIN} caratteri)")
    ord_ = 1 + conn.execute("SELECT coalesce(max(ord), 0) FROM reviewer_comment WHERE letter_id = ?", (letter_id,)).fetchone()[0]
    n = 1 + conn.execute("SELECT count(*) FROM reviewer_comment WHERE letter_id = ? AND reviewer = ? AND source = 'user'",
                         (letter_id, reviewer)).fetchone()[0]
    cid = new_id("reviewer_comment")
    with transaction(conn):
        conn.execute("""INSERT INTO reviewer_comment (id, letter_id, ord, reviewer, comment_key, text, source)
                        VALUES (?, ?, ?, ?, ?, ?, 'user')""", (cid, letter_id, ord_, reviewer, f"{reviewer}.{n}", text))
        conn.execute("UPDATE submission_letter SET no_comments = 0 WHERE id = ?", (letter_id,))
        audit.record(conn, actor=actor, action="reviewer_comment_added", entity_type="submission_letter",
                     entity_id=letter_id, payload={"comment_id": cid}, in_transaction=True)
    return {"id": cid, "chiave": f"{reviewer}.{n}", "nella_lettera": quote_in(text, letter["text"])}


def remove_comment(conn: sqlite3.Connection, comment_id: str, *, actor: str = "dashboard") -> None:
    c = conn.execute("SELECT * FROM reviewer_comment WHERE id = ? AND removed_at IS NULL", (comment_id,)).fetchone()
    if c is None:
        raise SubmissionError("commento non trovato (o già tolto)")
    with transaction(conn):
        conn.execute("UPDATE reviewer_comment SET removed_at = ? WHERE id = ?", (audit.utc_now(), comment_id))
        audit.record(conn, actor=actor, action="reviewer_comment_removed", entity_type="submission_letter",
                     entity_id=c["letter_id"], payload={"comment_id": comment_id}, in_transaction=True)


def set_no_comments(conn: sqlite3.Connection, letter_id: str, flag: bool = True, *, actor: str = "dashboard") -> None:
    """The "-" of the page: the journal made no comment in this letter (only when no comment is written)."""
    if conn.execute("SELECT 1 FROM submission_letter WHERE id = ?", (letter_id,)).fetchone() is None:
        raise SubmissionError("lettera non trovata")
    if flag and conn.execute("SELECT 1 FROM reviewer_comment WHERE letter_id = ? AND removed_at IS NULL",
                             (letter_id,)).fetchone():
        raise SubmissionError("ci sono commenti: togli prima quelli")
    with transaction(conn):
        conn.execute("UPDATE submission_letter SET no_comments = ? WHERE id = ?", (int(bool(flag)), letter_id))
        audit.record(conn, actor=actor, action="letter_no_comments", entity_type="submission_letter",
                     entity_id=letter_id, payload={"no_comments": bool(flag)}, in_transaction=True)


def correct_event(conn: sqlite3.Connection, event_id: str, *, event: str | None = None, event_on: str | None = None,
                  due_on: str | None = None, note: str | None = None, round_no: int | None = None,
                  actor: str = "dashboard") -> str:
    """A correction is a new row that replaces the old one (the history stays)."""
    old = conn.execute("SELECT * FROM submission_event WHERE id = ? AND superseded_by IS NULL AND removed_at IS NULL",
                       (event_id,)).fetchone()
    if old is None:
        raise SubmissionError("passo non trovato (o già corretto o tolto)")
    try:
        rnd = int(round_no) if round_no not in (None, "") else old["round"]
    except (TypeError, ValueError) as exc:
        raise SubmissionError("giro di revisione non valido") from exc
    if rnd < 1:
        raise SubmissionError("giro di revisione non valido")
    ev = event or old["event"]
    if ev not in EVENTS:
        raise SubmissionError(f"evento non ammesso: {ev}")
    new = new_id("submission_event")
    with transaction(conn):
        conn.execute("PRAGMA defer_foreign_keys = ON")
        conn.execute("UPDATE submission_event SET superseded_by = ? WHERE id = ?", (new, event_id))
        conn.execute("""INSERT INTO submission_event (id, submission_id, event, event_on, round, letter_id, due_on, note)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                     (new, old["submission_id"], ev, _d(event_on, "data") if event_on else old["event_on"],
                      rnd, old["letter_id"], _d(due_on, "scadenza", required=False) if due_on else old["due_on"],
                      _t(note) if note is not None else old["note"]))
        audit.record(conn, actor=actor, action="submission_event_corrected", entity_type="submission",
                     entity_id=old["submission_id"], payload={"old": event_id, "new": new}, in_transaction=True)
    return new


def set_label(conn: sqlite3.Connection, comment_id: str, *, category: str | None = None, severity: str | None = None,
              followed: str | None = None, quote: str | None = None, actor: str = "dashboard") -> str:
    """The user's decision on the classification of a comment: confirmed (same values as the proposal) or edited."""
    c = conn.execute("SELECT * FROM reviewer_comment WHERE id = ?", (comment_id,)).fetchone()
    if c is None:
        raise SubmissionError("commento non trovato")
    old = current_label(conn, comment_id)
    category = category or (old["category"] if old else None)
    severity = severity or (old["severity"] if old else None)
    followed = followed if followed is not None else (old["followed"] if old else None)
    quote = _t(quote) or (old["quote"] if old else None) or c["text"][:400]
    if category not in CATEGORIES or severity not in SEVERITIES or (followed and followed not in FOLLOWED):
        raise SubmissionError("categoria, gravità o seguito non ammessi")
    if not quote_in(quote, c["text"]):
        raise SubmissionError("la frase non è un pezzo esatto del commento")
    same = old is not None and (old["category"], old["severity"], old["followed"] or None, old["quote"]) == \
        (category, severity, followed or None, quote)
    lid = new_id("comment_label")
    with transaction(conn):
        conn.execute("PRAGMA defer_foreign_keys = ON")
        if old:
            conn.execute("UPDATE comment_label SET superseded_by = ? WHERE id = ?", (lid, old["id"]))
        conn.execute("""INSERT INTO comment_label (id, comment_id, category, severity, followed, quote, state, actor)
                        VALUES (?, ?, ?, ?, ?, ?, ?, 'human')""",
                     (lid, comment_id, category, severity, followed or None, quote, "confirmed" if same else "edited"))
        audit.record(conn, actor=actor, action="comment_label_" + ("confirmed" if same else "edited"),
                     entity_type="reviewer_comment", entity_id=comment_id, payload={"label": lid}, in_transaction=True)
    return lid


# ---------------------------------------------------------------- reading
def current_submission(conn: sqlite3.Connection, sid: str):
    return conn.execute("SELECT * FROM submission WHERE id = ? AND superseded_by IS NULL", (sid,)).fetchone()


def current_label(conn: sqlite3.Connection, comment_id: str):
    return conn.execute("SELECT * FROM comment_label WHERE comment_id = ? AND superseded_by IS NULL",
                        (comment_id,)).fetchone()


def _days(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


# ---------------------------------------------------------------- point 8 (2026-10-28): «seguito» from the response
# The response to the reviewers written with risposta.cmd (response-v1, accepted) says for every comment what was done:
# modificato -> seguito «si», chiarito -> «in_parte», non modificato -> «no». ORPHEUS matches the comments of the diary
# with those of the response by their text (same text, or one inside the other when long) and PROPOSES the value: the
# user confirms it in the dashboard; Claude sees it in the packet. Everything stays on the PC (R10).
FOLLOWED_FROM_ACTION = {"modificato": "si", "chiarito": "in_parte", "non modificato": "no"}
MATCH_MIN = 40


def response_actions(home: Path) -> list[dict]:
    """[{testo, azione, cartella}] of every comment answered in an accepted response (response-v1) on this PC."""
    from . import submission_letters as sl
    base = sl.compliance.reports_dir(home) / sl.RESPONSE["folder"]
    out = []
    for d in sorted(base.glob("*")) if base.is_dir() else []:
        acc, pk = d / sl.RESPONSE["accepted"], d / sl.PACKET
        if not acc.exists() or not pk.exists():
            continue
        try:
            data = json.loads(acc.read_text(encoding="utf-8-sig"))
            packet = json.loads(pk.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        texts = {c.get("id"): c.get("testo") or "" for c in packet.get("commenti") or []}
        for a in data.get("risposte") or []:
            if a.get("azione") in FOLLOWED_FROM_ACTION and texts.get(a.get("commento")):
                out.append({"testo": texts[a["commento"]], "azione": a["azione"], "cartella": d.name})
    return out


def followed_proposals(conn: sqlite3.Connection, home: Path | None) -> dict[str, dict]:
    """{reviewer_comment id: {seguito, azione, cartella}} for the comments of the diary found in an accepted response."""
    if home is None:
        return {}
    answered = [(squash(a["testo"]).lower(), a) for a in response_actions(home)]
    if not answered:
        return {}
    out = {}
    for r in conn.execute("SELECT id, text FROM reviewer_comment WHERE removed_at IS NULL"):
        mine = squash(r["text"]).lower()
        for other, a in answered:
            same = mine == other or (min(len(mine), len(other)) >= MATCH_MIN and (mine in other or other in mine))
            if same:
                out[r["id"]] = {"seguito": FOLLOWED_FROM_ACTION[a["azione"]], "azione": a["azione"],
                                "cartella": a["cartella"]}
                break
    return out


def diary(conn: sqlite3.Connection, today: date | None = None, *, home: Path | None = None) -> dict:
    """Every paper with its submissions, current events (oldest first), letters with their comments and labels; status,
    days to the first decision and open revision deadlines. For the dashboard only (it has the texts: never logged)."""
    today = today or date.today()
    events: dict[str, list[dict]] = {}
    labels = {r["comment_id"]: dict(r) for r in conn.execute("SELECT * FROM comment_label WHERE superseded_by IS NULL")}
    texts = {r["id"]: r["text"] for r in conn.execute("SELECT id, text FROM submission_letter")}
    comments: dict[str, list[dict]] = {}
    proposals = followed_proposals(conn, home)
    for r in conn.execute("SELECT * FROM reviewer_comment WHERE removed_at IS NULL ORDER BY letter_id, ord"):
        comments.setdefault(r["letter_id"], []).append({**dict(r), "etichetta": labels.get(r["id"]),
                                                        "nella_lettera": quote_in(r["text"], texts.get(r["letter_id"], "")),
                                                        "seguito_dalla_risposta": proposals.get(r["id"])})
    letters = {r["id"]: {**dict(r), "commenti": comments.get(r["id"], [])} for r in conn.execute(
        "SELECT id, submission_id, received_on, text, no_comments FROM submission_letter")}
    for r in conn.execute("""SELECT * FROM submission_event WHERE superseded_by IS NULL AND removed_at IS NULL
                             ORDER BY event_on, created_at, rowid"""):
        lt = letters.get(r["letter_id"]) if r["letter_id"] else None
        events.setdefault(r["submission_id"], []).append({
            **dict(r), "lettera": lt, "suggerimento": suggest_event(r["event"], lt["text"] if lt else None)})
    subs: dict[str, list[dict]] = {}
    for r in conn.execute("SELECT * FROM submission WHERE superseded_by IS NULL ORDER BY submitted_on, created_at"):
        ev = events.get(r["id"], [])
        first = next((e for e in ev if e["event"] in DECISIONS), None)
        last = ev[-1]["event"] if ev else "inviato"
        due = [e["due_on"] for e in ev if e["due_on"] and e["event"] in ("revisione_maggiore", "revisione_minore")]
        open_due = due[-1] if due and last in ("revisione_maggiore", "revisione_minore") else None
        subs.setdefault(r["manuscript_id"], []).append({
            **dict(r), "eventi": ev, "stato": last,
            "stato_testo": EVENTS.get(last, last),
            "giorni_alla_prima_decisione": _days(r["submitted_on"], first["event_on"]) if first else None,
            "giorni_in_attesa": _days(r["submitted_on"], today.isoformat()) if not first and last not in CLOSED else None,
            "scadenza_revisione": open_due,
            "giorni_alla_scadenza": _days(today.isoformat(), open_due) if open_due else None})
    papers = [{**dict(m), "sottomissioni": subs.get(m["id"], [])}
              for m in conn.execute("SELECT * FROM my_manuscript ORDER BY created_at")]
    return {"lavori": papers, "eventi": EVENTS, "categorie": {k: v[0] for k, v in CATEGORIES.items()},
            "gravita": SEVERITIES, "seguito": FOLLOWED, "tipi": KINDS,
            "chi": {"ED": "Editor", **{f"R{i}": f"Revisore {i}" for i in range(1, 10)}}}


def _current_events(conn: sqlite3.Connection, submission_id: str) -> list[sqlite3.Row]:
    return list(conn.execute("""SELECT * FROM submission_event WHERE submission_id = ? AND superseded_by IS NULL
                                AND removed_at IS NULL ORDER BY event_on, created_at, rowid""", (submission_id,)))


def lessons(conn: sqlite3.Connection, *, journal_id: str | None = None, article_type: str | None = None) -> dict:
    """By journal: submissions and decisions (desk rejections apart), classified comments by category (with severity),
    days to the first decision (median with N). Always with N; never a prediction. Only current steps and comments."""
    by_j: dict[str, dict] = {}
    subs = list(conn.execute("SELECT * FROM submission WHERE superseded_by IS NULL ORDER BY submitted_on"))
    for r in subs:
        if journal_id and r["journal_id"] != journal_id:
            continue
        if article_type and (r["article_type"] or "").lower() != article_type.lower():
            continue
        j = by_j.setdefault(r["journal_id"] or r["journal_title"], {
            "journal_id": r["journal_id"], "rivista": r["journal_title"], "sottomissioni": 0, "decisioni": {},
            "commenti": 0, "confermati": 0, "lettere_senza_commenti": 0, "categorie": {}, "_giorni": [],
            "_sub_ids": set()})
        j["sottomissioni"] += 1
        j["_sub_ids"].add(r["id"])
        ev = _current_events(conn, r["id"])
        for e in ev:
            if e["event"] in DECISIONS:
                j["decisioni"][e["event"]] = j["decisioni"].get(e["event"], 0) + 1
        first = next((e for e in ev if e["event"] in DECISIONS), None)
        if first:
            j["_giorni"].append(_days(r["submitted_on"], first["event_on"]))
        letters = {e["letter_id"] for e in ev if e["letter_id"]}
        for lid in letters:
            lt = conn.execute("SELECT no_comments FROM submission_letter WHERE id = ?", (lid,)).fetchone()
            j["lettere_senza_commenti"] += bool(lt and lt[0])
            for c in conn.execute("""SELECT l.category, l.severity, l.state FROM reviewer_comment c
                                     JOIN comment_label l ON l.comment_id = c.id AND l.superseded_by IS NULL
                                     WHERE c.letter_id = ? AND c.removed_at IS NULL""", (lid,)):
                j["commenti"] += 1
                j["confermati"] += c["state"] != "ai"
                cat = j["categorie"].setdefault(c["category"], {"categoria": c["category"],
                                                                "nome": CATEGORIES[c["category"]][0], "commenti": 0,
                                                                "sottomissioni": set(), **{k: 0 for k in SEVERITIES}})
                cat["commenti"] += 1
                cat["sottomissioni"].add(r["id"])
                cat[c["severity"]] += 1
    out = []
    for j in sorted(by_j.values(), key=lambda j: (-j["commenti"], -j["sottomissioni"], j["rivista"])):
        cats = sorted(j["categorie"].values(), key=lambda c: (-c["commenti"], c["categoria"]))
        for c in cats:
            c["sottomissioni"] = len(c["sottomissioni"])
        t = j.pop("_giorni")
        j.pop("_sub_ids")
        out.append({**j, "categorie": cats,
                    "rifiuti_senza_revisione": j["decisioni"].get("rifiutato_senza_revisione", 0),
                    "giorni_alla_prima_decisione": {"n": len(t), **({"mediana": statistics.median(t), "min": min(t),
                                                                     "max": max(t)} if t else {})}})
    return {"riviste": out, "commenti": sum(j["commenti"] for j in out), "eventi": EVENTS,
            "nota": "Conteggi dalle tue sottomissioni (decisioni e commenti classificati: «confermati» sono quelli "
                    "controllati da te). Pochi casi: nessuna previsione, guarda sempre N."}


def status(conn: sqlite3.Connection) -> dict:
    n = lambda q: conn.execute(q).fetchone()[0]  # noqa: E731
    return {"lavori": n("SELECT count(*) FROM my_manuscript"),
            "sottomissioni": n("SELECT count(*) FROM submission WHERE superseded_by IS NULL"),
            "lettere": n("SELECT count(*) FROM submission_letter"),
            "commenti": n("SELECT count(*) FROM reviewer_comment WHERE removed_at IS NULL"),
            "commenti_classificati": n("SELECT count(*) FROM comment_label l JOIN reviewer_comment c ON c.id = "
                                       "l.comment_id WHERE l.superseded_by IS NULL AND c.removed_at IS NULL"),
            "classificazioni_confermate": n("SELECT count(*) FROM comment_label l JOIN reviewer_comment c ON c.id = "
                                            "l.comment_id WHERE l.superseded_by IS NULL AND c.removed_at IS NULL "
                                            "AND l.state <> 'ai'"),
            "pacchetti_aperti": len(open_packets(conn))}


# ---------------------------------------------------------------- classification by Claude (feedback-v1)
def folder(home: Path) -> Path:
    return home / REPORTS_DIR / FOLDER


def packets_dir(home: Path) -> Path:
    return folder(home) / "pacchetti"


def to_send_dir(home: Path) -> Path:
    return folder(home) / card_store.TO_SEND


def accepted_dir(home: Path) -> Path:
    return folder(home) / card_store.ACCEPTED


def squash(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def quote_in(quote: str, text: str) -> bool:
    q = squash(quote)
    return len(q) >= QUOTE_MIN and q in squash(text)


def letters_to_classify(conn: sqlite3.Connection) -> list[str]:
    """Letters with at least one comment without a label decided by the user and without any current label."""
    return [r[0] for r in conn.execute(
        """SELECT DISTINCT c.letter_id FROM reviewer_comment c
           JOIN submission_letter t ON t.id = c.letter_id
           WHERE c.removed_at IS NULL AND t.no_comments = 0
             AND EXISTS (SELECT 1 FROM submission_event e WHERE e.letter_id = t.id AND e.superseded_by IS NULL
                         AND e.removed_at IS NULL)
             AND NOT EXISTS (SELECT 1 FROM comment_label l WHERE l.comment_id = c.id AND l.superseded_by IS NULL)
           ORDER BY t.received_on, c.letter_id""")]


def open_packets(conn: sqlite3.Connection) -> list[str]:
    prefix = TASK_KEY.format(letter_id="")
    return [r[0][len(prefix):] for r in conn.execute(
        """SELECT DISTINCT t.idempotency_key FROM workflow_run w JOIN task t ON t.id = w.task_id
           WHERE w.status = 'running' AND t.idempotency_key LIKE ?""", (prefix + "%",))]


def _todo_comments(conn: sqlite3.Connection, letter_id: str) -> list[sqlite3.Row]:
    return list(conn.execute(
        """SELECT c.* FROM reviewer_comment c WHERE c.letter_id = ? AND c.removed_at IS NULL AND NOT EXISTS
           (SELECT 1 FROM comment_label l WHERE l.comment_id = c.id AND l.superseded_by IS NULL AND l.state <> 'ai')
           ORDER BY c.ord""", (letter_id,)))


def _sha(comments) -> str:
    return hashlib.sha256(json.dumps([[c["id"], c["text"]] for c in comments]).encode("utf-8")).hexdigest()


def packet(conn: sqlite3.Connection, letter_id: str, task_id: str, run: dict, proposals: dict | None = None) -> dict:
    t = conn.execute("SELECT * FROM submission_letter WHERE id = ?", (letter_id,)).fetchone()
    s = conn.execute("SELECT * FROM submission WHERE id = ?", (t["submission_id"],)).fetchone()
    m = conn.execute("SELECT article_kind FROM my_manuscript WHERE id = ?", (s["manuscript_id"],)).fetchone()
    ev = [{"evento": e["event"], "data": e["event_on"], "giro": e["round"]} for e in conn.execute(
        "SELECT * FROM submission_event WHERE submission_id = ? AND superseded_by IS NULL AND removed_at IS NULL "
        "ORDER BY event_on",
        (s["id"],))]
    return {"formato": PACKET_FORMAT, "contratto": CONTRACT, "task_id": task_id, "workflow_run_id": run["id"],
            "letter_id": letter_id, "emesso_il": run["started_at"], "rivista": s["journal_title"],
            "tipo_di_articolo": s["article_type"], "tipo_di_lavoro": m["article_kind"] if m else None, "eventi": ev,
            "istruzioni": INSTRUCTIONS,
            "categorie": {k: v[1] for k, v in CATEGORIES.items()}, "gravita": list(SEVERITIES),
            "seguito": list(FOLLOWED),
            "commenti": [{"commento": c["comment_key"], "chi": c["reviewer"], "testo": c["text"],
                          **({"azione_nella_risposta": proposals[c["id"]]["azione"]}
                             if proposals and c["id"] in proposals else {})}
                         for c in _todo_comments(conn, letter_id)],
            "esempio": {"formato": CARD_FORMAT, "versione": CONTRACT, "workflow_run_id": "run_<copy>",
                        "letter_id": "sle_<copy>", "modello": "claude-opus-5-5",
                        "commenti": [{"commento": "R1.1", "categoria": "campione_potenza", "gravita": "importante",
                                      "citazione": "<an exact piece of the comment>"}]}}


def issue_packets(conn: sqlite3.Connection, home: Path, *, project_name: str = "ORPHEUS", project_version: str = "",
                  actor: str = "orpheus riscontri") -> dict:
    letters = letters_to_classify(conn)
    plan = [(lid, _sha(_todo_comments(conn, lid))) for lid in letters if _todo_comments(conn, lid)]
    written, new_runs = [], 0
    with transaction(conn):
        if plan:
            project_id = card_store._ensure_project(conn, project_name, project_version)
            agent_id = card_store._ensure_agent(conn)
            model_id = card_store._ensure_model(conn, card_store.GENERIC_MODEL)
        for lid, sha in plan:
            key = TASK_KEY.format(letter_id=lid)
            task = conn.execute("SELECT id FROM task WHERE idempotency_key = ?", (key,)).fetchone()
            if task is None:
                task_id = new_id("task")
                conn.execute("""INSERT INTO task (id, project_id, title, priority, status, owner_agent_id, expected_output,
                                                  acceptance_criteria, idempotency_key)
                                VALUES (?, ?, ?, 2, 'ready', ?, ?, ?, ?)""",
                             (task_id, project_id, f"Classificazione dei commenti ({CONTRACT}) della lettera {lid}",
                              agent_id, f"{REPORTS_DIR}/{FOLDER}/{card_store.TO_SEND}/{lid}.json",
                              f"supera i controlli di {CONTRACT} con orpheus riscontri", key))
            else:
                task_id = task[0]
                conn.execute("UPDATE task SET status = 'ready' WHERE id = ?", (task_id,))
            run = None
            for r in conn.execute("SELECT * FROM workflow_run WHERE task_id = ? AND status = 'running'", (task_id,)):
                params = json.loads(r["parameters"])
                if params.get("comments_sha256") == sha and run is None:
                    run = {"id": r["id"], "started_at": r["started_at"], "parameters": params}
                else:
                    conn.execute("""UPDATE workflow_run SET status = 'cancelled', finished_at = ?,
                                    error = 'commenti cambiati: pacchetto riemesso' WHERE id = ?""",
                                 (audit.utc_now(), r["id"]))
            if run is None:
                params = {"contratto": CONTRACT, "letter_id": lid, "comments_sha256": sha}
                run = {"id": new_id("workflow_run"), "started_at": audit.utc_now(), "parameters": params}
                conn.execute("""INSERT INTO workflow_run (id, task_id, agent_id, executor, model_id, started_at, status,
                                                          parameters, validation_status)
                                VALUES (?, ?, ?, 'claude_session', ?, ?, 'running', ?, 'pending')""",
                             (run["id"], task_id, agent_id, model_id, run["started_at"], json.dumps(params, sort_keys=True)))
                new_runs += 1
            written.append((lid, task_id, run))
    proposals = followed_proposals(conn, home)
    for lid, task_id, run in written:
        data = json.dumps(packet(conn, lid, task_id, run, proposals), ensure_ascii=False, indent=1).encode("utf-8")
        card_store._write_if_changed(packets_dir(home) / f"{lid}.json", data)
    to_send_dir(home).mkdir(parents=True, exist_ok=True)
    return {"pacchetti_aperti": len(written), "esecuzioni_nuove": new_runs}


def validate(data, comments: list[dict]) -> list[str]:
    """Problems of a classification file (empty = accepted). `comments`: [{commento, testo}] still to classify. The
    messages name the comments by key only (R10)."""
    probs = []
    if not isinstance(data, dict):
        return ["non è un oggetto JSON"]
    if data.get("formato") != CARD_FORMAT or data.get("versione") != CONTRACT:
        probs.append(f"formato o versione diversi da {CARD_FORMAT} / {CONTRACT}")
    if not isinstance(data.get("modello"), str) or len(data["modello"]) < 3:
        probs.append("manca il modello")
    extra = set(data) - {"formato", "versione", "workflow_run_id", "letter_id", "modello", "commenti", "note_generali"}
    if extra:
        probs.append(f"chiavi non previste: {', '.join(sorted(extra))}")
    items = data.get("commenti")
    if not isinstance(items, list):
        return probs + ["manca la lista commenti"]
    text = {c["commento"]: c["testo"] for c in comments}
    seen = set()
    for i, it in enumerate(items, 1):
        if not isinstance(it, dict):
            probs.append(f"voce {i}: non è un oggetto")
            continue
        key = it.get("commento")
        lab = f"voce {i} ({key})"
        if set(it) - {"commento", "categoria", "gravita", "seguito", "citazione"}:
            probs.append(f"{lab}: chiavi non previste")
        if key not in text:
            probs.append(f"{lab}: commento non nel pacchetto (o già deciso da te)")
            continue
        if key in seen:
            probs.append(f"{lab}: commento classificato due volte")
        seen.add(key)
        if it.get("categoria") not in CATEGORIES:
            probs.append(f"{lab}: categoria non ammessa")
        if it.get("gravita") not in SEVERITIES:
            probs.append(f"{lab}: gravità non ammessa")
        if it.get("seguito") is not None and it.get("seguito") not in FOLLOWED:
            probs.append(f"{lab}: seguito non ammesso")
        q = it.get("citazione")
        if not isinstance(q, str) or "..." in q or "…" in q or not quote_in(q, text[key]):
            probs.append(f"{lab}: la citazione non è un pezzo esatto e continuo del commento (almeno {QUOTE_MIN} caratteri)")
    missing = [k for k in text if k not in seen]
    if missing:
        probs.append(f"commenti senza classificazione: {', '.join(missing)}")
    return probs


def check_with_packet(data, pk: dict) -> list[str]:
    probs = []
    for k in ("workflow_run_id", "letter_id"):
        if not isinstance(data, dict) or data.get(k) != pk.get(k):
            probs.append(f"{k} diverso da quello del pacchetto")
    return probs + validate(data, pk["commenti"])


def submit(conn: sqlite3.Connection, home: Path, *, actor: str = "orpheus riscontri") -> dict:
    files = sorted(to_send_dir(home).glob("*.json")) if to_send_dir(home).is_dir() else []
    results = []
    for path in files:
        out = {"file": path.name}
        try:
            raw = path.read_bytes()
            data = json.loads(raw.decode("utf-8-sig"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            data, raw = None, b""
        run = conn.execute("SELECT * FROM workflow_run WHERE id = ?", (data.get("workflow_run_id"),)).fetchone() \
            if isinstance(data, dict) and isinstance(data.get("workflow_run_id"), str) else None
        params = json.loads(run["parameters"] or "{}") if run else {}
        if data is None:
            probs = ["file non leggibile o non JSON"]
        elif run is None or params.get("contratto") != CONTRACT:
            probs = ["workflow_run_id mancante o non di un pacchetto feedback-v1"]
        elif run["status"] != "running":
            probs = [f"il pacchetto non è più valido ({run['status']}): rilancia riscontri.cmd"]
        else:
            lid = params["letter_id"]
            todo = _todo_comments(conn, lid)
            probs = ([] if data.get("letter_id") == lid else ["letter_id diverso da quello del pacchetto"])
            if _sha(todo) != params.get("comments_sha256"):
                probs.append("i commenti sono cambiati dopo il pacchetto (o li hai decisi tu): rilancia riscontri.cmd")
            probs += validate(data, [{"commento": c["comment_key"], "testo": c["text"]} for c in todo])
        if not probs:
            by_key = {c["comment_key"]: c for c in todo}
            with transaction(conn):
                conn.execute("PRAGMA defer_foreign_keys = ON")
                for it in data["commenti"]:
                    c = by_key[it["commento"]]
                    old = current_label(conn, c["id"])
                    lid_new = new_id("comment_label")
                    if old is not None:
                        conn.execute("UPDATE comment_label SET superseded_by = ? WHERE id = ?", (lid_new, old["id"]))
                    conn.execute("""INSERT INTO comment_label (id, comment_id, category, severity, followed, quote, state,
                                    actor, workflow_run_id) VALUES (?, ?, ?, ?, ?, ?, 'ai', 'claude_session', ?)""",
                                 (lid_new, c["id"], it["categoria"], it["gravita"], it.get("seguito"),
                                  squash(it["citazione"]), run["id"]))
                model_id = card_store._ensure_model(conn, data["modello"])
                conn.execute("""UPDATE workflow_run SET status = 'succeeded', finished_at = ?, validation_status = 'pass',
                                model_id = ? WHERE id = ?""", (max(audit.utc_now(), run["started_at"]), model_id, run["id"]))
                conn.execute("UPDATE task SET status = 'done' WHERE id = ?", (run["task_id"],))
                audit.record(conn, actor=actor, action="comment_labels_accepted", entity_type="submission_letter",
                             entity_id=params["letter_id"], payload={"workflow_run_id": run["id"],
                                                                    "labels": len(data["commenti"])}, in_transaction=True)
            dest = accepted_dir(home)
            dest.mkdir(parents=True, exist_ok=True)
            target = dest / path.name if not (dest / path.name).exists() else \
                dest / f"{path.stem}_{hashlib.sha256(raw).hexdigest()[:8]}{path.suffix}"
            path.replace(target)
            motivi = path.with_name(path.stem + ".motivi.txt")
            if motivi.exists():
                motivi.unlink()
            out |= {"esito": "accettata", "etichette": len(data["commenti"])}
        else:
            path.with_name(path.stem + ".motivi.txt").write_text(
                "ORPHEUS - classificazione rifiutata\r\n" + "".join(f"- {p}\r\n" for p in probs)
                + "\r\nCorreggi il file e rilancia riscontri.cmd.\r\n", encoding="utf-8")
            out |= {"esito": "rifiutata", "motivi": probs[:20]}
        results.append(out)
    return {"inviate": len(results), "accettate": sum(r["esito"] == "accettata" for r in results),
            "rifiutate": sum(r["esito"] != "accettata" for r in results), "file": results}


# ---------------------------------------------------------------- use: warnings when preparing a manuscript
GROUP = "Lezioni dalle tue sottomissioni"
MIN_FOR_WARNING = 2


def compare(conn: sqlite3.Connection, jr, article_type: str | None = None) -> list:
    """Checks (group GROUP) for one journal: the categories that editors and reviewers of that journal asked about in
    the user's past submissions (at least MIN_FOR_WARNING comments) → "guarda"; never "ko"."""
    from .compliance import Check
    src = {"rivista": jr.sigla or jr.title, "regole": CONTRACT}
    note = ("Dalle tue sottomissioni passate a questa rivista (commenti classificati, «confermati» da te). Pochi casi: "
            "è un promemoria, mai un motivo di correzione da solo.")
    if not getattr(jr, "journal_id", None):
        return []
    les = lessons(conn, journal_id=jr.journal_id)
    j = les["riviste"][0] if les["riviste"] else None
    if j is None or (not j["commenti"] and not j["rifiuti_senza_revisione"]):
        return [Check("lezioni_rivista", GROUP, "Commenti di revisori ed editor di questa rivista", "na",
                      "nessuna tua sottomissione con commenti classificati o rifiuti per questa rivista", None, src, [note])]
    out = [Check("lezioni_rivista", GROUP, "Commenti di revisori ed editor di questa rivista", "ok",
                 f"{j['commenti']} commenti classificati in {j['sottomissioni']} sottomissioni "
                 f"({j['confermati']} confermati da te)", None, src, [note])]
    if j["rifiuti_senza_revisione"]:
        out.append(Check("lezioni_rifiuto_editor", GROUP, "Rifiuti senza revisione", "guarda",
                         f"{j['rifiuti_senza_revisione']} rifiuti senza revisione su {j['sottomissioni']} sottomissioni: "
                         "guarda le motivazioni dell'editor (spesso rilevanza e disegno)", None, src))
    for c in j["categorie"]:
        if c["commenti"] < MIN_FOR_WARNING:
            continue
        out.append(Check(f"lezioni_{c['categoria']}", GROUP, c["nome"], "guarda",
                         f"in passato {c['commenti']} commenti su questo in {c['sottomissioni']} sottomissioni "
                         f"(bloccanti {c['bloccante']}, importanti {c['importante']}, minori {c['minore']})", None, src))
    return out
