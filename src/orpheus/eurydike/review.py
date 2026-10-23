"""EURYDIKE review store (E2, T-051c): writes of a systematic review on the tables of migrations 0014-0015.

What this module writes, always in one transaction with an audit event:
- the review, its reviewers and its criteria (a criterion is never changed: a new one supersedes it);
- an import of a search export (`search_import`) and its records (`review_record`): every record is kept, a duplicate
  (same `dedup_key` as a record already in the review, or earlier in the same file) points to the first one with
  `duplicate_of`; the same file (same SHA-256) is refused the second time;
- the screening decisions (`screening_decision`): one current decision per record and reviewer, a new one supersedes
  the old; duplicates are not screened; `exclude` needs an exclusion criterion of the review;
- the studies (`review_study`) for the full text, linked to the ORPHEUS article with the same DOI when there is one;
- the full-text decision (`ft_decision`, with `ft_note`): only a human reviewer or the consensus (also a trigger);
- the full-text proposals of Claude (`ft_proposal`): never a decision, a new one supersedes the old.

Nothing is deleted (triggers of 0014). The final screening decision is computed by `core.screening.final_decision`,
never stored. Messages for the user are in Italian; contents (criteria, reasons) are written as given.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from contextlib import contextmanager

from .. import audit
from ..db import transaction
from ..ids import new_id
from .core import records as core_records
from .core.screening import CONSENSUS, final_decision

KINDS = ("systematic_review", "meta_analysis", "scoping_review")
REVIEWER_KINDS = ("human", "claude_session", "chat_manuale", "consensus")
HUMAN_KINDS = ("human", "consensus")
CRITERION_KINDS = ("inclusion", "exclusion", "prefilter")
DECISIONS = ("include", "exclude", "maybe")
ORIGINS = ("manual", "bulk", "rule", "model")
FT_DECISIONS = ("include", "exclude", "not_retrieved")
FORMATS = ("ris", "nbib", "wos", "csv", "tsv")
LOG_KINDS = ("deviation", "bulk", "rule", "pdf", "note", "prefilter", "analysis")


class ReviewError(ValueError):
    """A request the review store refuses (message in Italian, for the user)."""


@contextmanager
def _write(conn: sqlite3.Connection):
    with transaction(conn):
        conn.execute("PRAGMA defer_foreign_keys = ON")
        yield conn


def _one(conn: sqlite3.Connection, sql: str, args: tuple, what: str) -> sqlite3.Row:
    r = conn.execute(sql, args).fetchone()
    if r is None:
        raise ReviewError(f"{what} non trovato")
    return r


def _reviewer(conn: sqlite3.Connection, reviewer_id: str, review_id: str) -> sqlite3.Row:
    r = _one(conn, "SELECT * FROM review_reviewer WHERE id = ?", (reviewer_id,), "revisore")
    if r["review_id"] != review_id:
        raise ReviewError("il revisore appartiene a un'altra revisione")
    return r


def _criterion(conn: sqlite3.Connection, criterion_id: str | None, review_id: str, kind: str = "exclusion") -> None:
    if criterion_id is None:
        return
    c = _one(conn, "SELECT * FROM review_criterion WHERE id = ?", (criterion_id,), "criterio")
    if c["review_id"] != review_id:
        raise ReviewError("il criterio appartiene a un'altra revisione")
    if c["kind"] != kind:
        raise ReviewError(f"serve un criterio di esclusione (questo è '{c['kind']}')")
    if c["superseded_by"] is not None:
        raise ReviewError("il criterio è stato sostituito: usa quello nuovo")


# ------------------------------------------------------------------ review, reviewers, criteria, log
def create_review(conn: sqlite3.Connection, *, title: str, question: str, data_dir: str,
                  kind: str = "systematic_review", pico: Mapping | None = None, registration: str | None = None,
                  screen_reviewers: int = 2, search_strategy: str | None = None, rob_tool: str | None = None,
                  qual_tool: str | None = None, actor: str = "user") -> str:
    if kind not in KINDS:
        raise ReviewError(f"tipo di revisione non ammesso: {kind}")
    if not title.strip() or not question.strip():
        raise ReviewError("scrivi titolo e domanda della revisione")
    from .core.tools import QUAL_TOOLS, ROB_TOOLS
    if rob_tool and rob_tool not in ROB_TOOLS:
        raise ReviewError(f"strumento del rischio di bias sconosciuto: {rob_tool}")
    if qual_tool and qual_tool not in QUAL_TOOLS:
        raise ReviewError(f"strumento della qualità sconosciuto: {qual_tool}")
    rid = new_id("review")
    with _write(conn):
        conn.execute("""INSERT INTO review (id, title, question, kind, pico, registration, screen_reviewers, data_dir,
                                            search_strategy, rob_tool_default, qual_tool_default)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                     (rid, title.strip(), question.strip(), kind, json.dumps(dict(pico or {}), ensure_ascii=False),
                      registration, screen_reviewers, data_dir, search_strategy or None, rob_tool or None,
                      qual_tool or None))
        audit.record(conn, actor=actor, action="review_created", entity_type="review", entity_id=rid,
                     payload={"kind": kind, "screen_reviewers": screen_reviewers}, in_transaction=True)
    return rid


def add_reviewer(conn: sqlite3.Connection, review_id: str, *, kind: str, label: str, model_ref: str | None = None,
                 actor: str = "user") -> str:
    if kind not in REVIEWER_KINDS:
        raise ReviewError(f"tipo di revisore non ammesso: {kind}")
    if kind not in HUMAN_KINDS and not model_ref:
        raise ReviewError("per un revisore non umano scrivi il modello (model_ref)")
    _one(conn, "SELECT id FROM review WHERE id = ?", (review_id,), "revisione")
    if conn.execute("SELECT 1 FROM review_reviewer WHERE review_id = ? AND label = ?", (review_id, label)).fetchone():
        raise ReviewError(f"esiste già un revisore '{label}' in questa revisione")
    vid = new_id("review_reviewer")
    with _write(conn):
        conn.execute("INSERT INTO review_reviewer (id, review_id, kind, label, model_ref) VALUES (?, ?, ?, ?, ?)",
                     (vid, review_id, kind, label, model_ref))
        audit.record(conn, actor=actor, action="review_reviewer_added", entity_type="review_reviewer", entity_id=vid,
                     payload={"review_id": review_id, "kind": kind}, in_transaction=True)
    return vid


def consensus_reviewer(conn: sqlite3.Connection, review_id: str) -> str:
    """The consensus 'reviewer' of the review, created the first time it is needed."""
    r = conn.execute("SELECT id FROM review_reviewer WHERE review_id = ? AND kind = 'consensus'", (review_id,)).fetchone()
    return r[0] if r else add_reviewer(conn, review_id, kind="consensus", label="Consenso", actor="orpheus")


def add_criterion(conn: sqlite3.Connection, review_id: str, *, kind: str, text: str, rule: Mapping | None = None,
                  replaces: str | None = None, actor: str = "user") -> str:
    """A new criterion (ord = next of its kind); with `replaces`, it supersedes that criterion and keeps its ord."""
    if kind not in CRITERION_KINDS:
        raise ReviewError(f"tipo di criterio non ammesso: {kind}")
    if not text.strip():
        raise ReviewError("scrivi il testo del criterio")
    if rule is not None and kind != "prefilter":
        raise ReviewError("solo un prefiltro ha una regola")
    _one(conn, "SELECT id FROM review WHERE id = ?", (review_id,), "revisione")
    old = None
    if replaces:
        old = _one(conn, "SELECT * FROM review_criterion WHERE id = ?", (replaces,), "criterio")
        if old["review_id"] != review_id or old["kind"] != kind:
            raise ReviewError("il criterio da sostituire è di un'altra revisione o di un altro tipo")
        if old["superseded_by"] is not None:
            raise ReviewError("il criterio è già stato sostituito")
    ord_ = old["ord"] if old else (conn.execute("SELECT coalesce(max(ord), 0) + 1 FROM review_criterion "
                                                "WHERE review_id = ? AND kind = ?", (review_id, kind)).fetchone()[0])
    cid = new_id("review_criterion")
    with _write(conn):
        if old:
            conn.execute("UPDATE review_criterion SET superseded_by = ? WHERE id = ?", (cid, old["id"]))
        conn.execute("INSERT INTO review_criterion (id, review_id, kind, ord, text, rule_json) VALUES (?, ?, ?, ?, ?, ?)",
                     (cid, review_id, kind, ord_, text.strip(),
                      json.dumps(dict(rule), ensure_ascii=False) if rule is not None else None))
        audit.record(conn, actor=actor, action="review_criterion_added", entity_type="review_criterion", entity_id=cid,
                     payload={"review_id": review_id, "kind": kind, "ord": ord_, "replaces": replaces},
                     in_transaction=True)
    return cid


def current_criteria(conn: sqlite3.Connection, review_id: str) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM review_criterion WHERE review_id = ? AND superseded_by IS NULL "
                                          "ORDER BY kind, ord", (review_id,))]


def add_log(conn: sqlite3.Connection, review_id: str, reviewer_id: str, *, kind: str, title: str,
            why: str | None = None, n_records: int | None = None, actor: str = "user") -> str:
    if kind not in LOG_KINDS:
        raise ReviewError(f"tipo di nota del registro non ammesso: {kind}")
    _reviewer(conn, reviewer_id, review_id)
    lid = new_id("review_log")
    with _write(conn):
        conn.execute("INSERT INTO review_log (id, review_id, reviewer_id, kind, title, why, n_records) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?)", (lid, review_id, reviewer_id, kind, title, why, n_records))
        audit.record(conn, actor=actor, action="review_log_added", entity_type="review_log", entity_id=lid,
                     payload={"review_id": review_id, "kind": kind, "n_records": n_records}, in_transaction=True)
    return lid


# ------------------------------------------------------------------ import of a search export
def _doi(value: str | None) -> str | None:
    d = core_records.clean_doi(value)
    return d if d and d.startswith("10.") and "/" in d else None


def _pmid(value: str | None) -> str | None:
    p = (value or "").strip()
    return p if p.isdigit() else None


def import_records(conn: sqlite3.Connection, review_id: str, records: Iterable[Mapping], *, source_name: str,
                   database_name: str, fmt: str, file_sha256: str, searched_on: str | None = None,
                   actor: str = "user") -> dict:
    """Store one search export. `records` are parsed records (core.records: title, abstract, authors, year, journal,
    doi, pmid). Every record is stored; a duplicate points to the first record with the same key (`duplicate_of`).
    Returns {"import_id", "record", "doppioni", "nuovi", "senza_titolo"}."""
    if fmt not in FORMATS:
        raise ReviewError(f"formato non ammesso: {fmt}")
    sha = (file_sha256 or "").lower()
    if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
        raise ReviewError("impronta SHA-256 del file non valida")
    _one(conn, "SELECT id FROM review WHERE id = ?", (review_id,), "revisione")
    if conn.execute("SELECT 1 FROM search_import WHERE review_id = ? AND file_sha256 = ?", (review_id, sha)).fetchone():
        raise ReviewError("questo file è già stato importato in questa revisione")
    first: dict[str, str] = {r[0]: r[1] for r in conn.execute(
        "SELECT dedup_key, id FROM review_record WHERE review_id = ? AND duplicate_of IS NULL ORDER BY created_at, rowid",
        (review_id,))}
    rows, dupes, untitled = [], 0, 0
    for rec in records:
        title = (rec.get("title") or "").strip()
        if not title:
            untitled += 1
            continue
        key = core_records.rec_key(dict(rec))
        rid = new_id("review_record")
        dup = first.get(key)
        if dup:
            dupes += 1
        else:
            first[key] = rid
        rows.append((rid, review_id, title, (rec.get("abstract") or None), (rec.get("authors") or None),
                     (str(rec.get("year")) if rec.get("year") else None), (rec.get("journal") or None),
                     _doi(rec.get("doi")), _pmid(rec.get("pmid")), key, dup))
    iid = new_id("search_import")
    with _write(conn):
        conn.execute("""INSERT INTO search_import (id, review_id, source_name, database_name, format, file_sha256,
                                                   searched_on, raw_count, dupes_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                     (iid, review_id, source_name, database_name, fmt, sha, searched_on, len(rows), dupes))
        conn.executemany("""INSERT INTO review_record (id, review_id, import_id, title, abstract, authors, year, journal,
                                                       doi, pmid, dedup_key, duplicate_of)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                         [r[:2] + (iid,) + r[2:] for r in rows])
        audit.record(conn, actor=actor, action="search_import_added", entity_type="search_import", entity_id=iid,
                     payload={"review_id": review_id, "records": len(rows), "duplicates": dupes,
                              "without_title": untitled, "sha256": sha}, in_transaction=True)
    return {"import_id": iid, "record": len(rows), "doppioni": dupes, "nuovi": len(rows) - dupes,
            "senza_titolo": untitled}


# ------------------------------------------------------------------ screening
def decide(conn: sqlite3.Connection, record_id: str, reviewer_id: str, decision: str, *,
           criterion_id: str | None = None, rationale: str | None = None, confidence: str | None = None,
           origin: str = "manual", rule_log_id: str | None = None, workflow_run_id: str | None = None,
           actor: str = "user") -> dict:
    """One reviewer's screening decision on one record; it supersedes the reviewer's current one.
    Returns {"id", "sostituisce", "finale"} where "finale" is the final decision of the record after this one."""
    if decision not in DECISIONS:
        raise ReviewError(f"decisione non ammessa: {decision} (include, exclude o maybe)")
    if origin not in ORIGINS:
        raise ReviewError(f"origine non ammessa: {origin}")
    rec = _one(conn, "SELECT * FROM review_record WHERE id = ?", (record_id,), "record")
    if rec["duplicate_of"] is not None:
        raise ReviewError("è un doppione: si decide sul record originale")
    rv = _reviewer(conn, reviewer_id, rec["review_id"])
    human = rv["kind"] in HUMAN_KINDS
    if human and origin == "model":
        raise ReviewError("una decisione di un revisore umano non ha origine 'model'")
    if not human and origin != "model":
        raise ReviewError("una decisione di Claude o della chat ha origine 'model' (con il suo workflow_run)")
    if origin == "model" and not workflow_run_id:
        raise ReviewError("una decisione del modello va legata al suo workflow_run")
    if origin in ("bulk", "rule") and not rule_log_id:
        raise ReviewError("una decisione di massa o da regola va legata alla sua riga del registro")
    if decision == "exclude":
        if not criterion_id:
            raise ReviewError("per escludere indica il criterio di esclusione")
        _criterion(conn, criterion_id, rec["review_id"])
    elif criterion_id is not None:
        raise ReviewError("il criterio si indica solo per escludere")
    old = conn.execute("SELECT id FROM screening_decision WHERE record_id = ? AND reviewer_id = ? "
                       "AND superseded_by IS NULL", (record_id, reviewer_id)).fetchone()
    did = new_id("screening_decision")
    with _write(conn):
        if old:
            conn.execute("UPDATE screening_decision SET superseded_by = ? WHERE id = ?", (did, old[0]))
        conn.execute("""INSERT INTO screening_decision (id, record_id, reviewer_id, decision, criterion_id, rationale,
                                                        confidence, origin, rule_log_id, workflow_run_id)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                     (did, record_id, reviewer_id, decision, criterion_id, rationale, confidence, origin, rule_log_id,
                      workflow_run_id))
        audit.record(conn, actor=actor, action="screening_decision_added", entity_type="screening_decision",
                     entity_id=did, payload={"record_id": record_id, "reviewer_id": reviewer_id, "decision": decision,
                                             "origin": origin, "replaces": old[0] if old else None},
                     in_transaction=True)
    return {"id": did, "sostituisce": old[0] if old else None, "finale": record_final(conn, record_id)}


def decide_consensus(conn: sqlite3.Connection, record_id: str, decision: str, *, criterion_id: str | None = None,
                     rationale: str | None = None, actor: str = "user") -> dict:
    """The consensus on a record (written by the user): it prevails on the reviewers' decisions."""
    rec = _one(conn, "SELECT review_id FROM review_record WHERE id = ?", (record_id,), "record")
    return decide(conn, record_id, consensus_reviewer(conn, rec["review_id"]), decision, criterion_id=criterion_id,
                  rationale=rationale, origin="manual", actor=actor)


def record_decisions(conn: sqlite3.Connection, record_id: str) -> dict[str, dict]:
    """Current decisions on a record, reviewer label -> {"d", "r"}, in the order they were made; the consensus under
    the key of core.screening (`_consenso`)."""
    out: dict[str, dict] = {}
    for r in conn.execute("""SELECT d.decision, d.rationale, v.kind, v.label FROM screening_decision d
                             JOIN review_reviewer v ON v.id = d.reviewer_id
                             WHERE d.record_id = ? AND d.superseded_by IS NULL ORDER BY d.created_at, d.rowid""",
                          (record_id,)):
        out[CONSENSUS if r["kind"] == "consensus" else r["label"]] = {"d": r["decision"], "r": r["rationale"] or ""}
    return out


def record_final(conn: sqlite3.Connection, record_id: str) -> dict:
    need = conn.execute("SELECT v.screen_reviewers FROM review_record r JOIN review v ON v.id = r.review_id "
                        "WHERE r.id = ?", (record_id,)).fetchone()[0]
    return final_decision(record_decisions(conn, record_id), need=need)


def screening_status(conn: sqlite3.Connection, review_id: str) -> dict:
    """Counts of the final decisions over the records that are not duplicates."""
    counts: dict[str, int] = {}
    for (rid,) in conn.execute("SELECT id FROM review_record WHERE review_id = ? AND duplicate_of IS NULL", (review_id,)):
        f = record_final(conn, rid)
        key = f["d"] if f["status"] in ("agreed", "resolved") else f["status"]
        counts[key] = counts.get(key, 0) + 1
    return counts


# ------------------------------------------------------------------ studies and full text
def add_study(conn: sqlite3.Connection, review_id: str, *, record_id: str | None = None, label: str | None = None,
              article_id: str | None = None, document_id: str | None = None, actor: str = "user") -> dict:
    """A study for the full text. From a record: the record must be included (agreed or by consensus); the ORPHEUS
    article with the same DOI is linked (and its longest PDF) when the caller does not give one."""
    rec = None
    if record_id:
        rec = _one(conn, "SELECT * FROM review_record WHERE id = ?", (record_id,), "record")
        if rec["review_id"] != review_id:
            raise ReviewError("il record appartiene a un'altra revisione")
        if rec["duplicate_of"] is not None:
            raise ReviewError("è un doppione: usa il record originale")
        f = record_final(conn, record_id)
        if f["d"] != "include" or f["status"] not in ("agreed", "resolved"):
            raise ReviewError("il record non è incluso allo screening (serve accordo o consenso su 'include')")
        if conn.execute("SELECT 1 FROM review_study WHERE review_id = ? AND record_id = ?", (review_id, record_id)).fetchone():
            raise ReviewError("lo studio di questo record esiste già")
        if article_id is None and rec["doi"]:
            a = conn.execute("SELECT id FROM article WHERE doi = ?", (rec["doi"],)).fetchone()
            article_id = a[0] if a else None
    else:
        _one(conn, "SELECT id FROM review WHERE id = ?", (review_id,), "revisione")
        if not label:
            raise ReviewError("uno studio senza record ha bisogno di un nome")
    if article_id and document_id is None:
        d = conn.execute("SELECT id FROM document WHERE kind = 'article' AND article_id = ? "
                         "ORDER BY page_count DESC, id LIMIT 1", (article_id,)).fetchone()
        document_id = d[0] if d else None
    if label is None:
        first = (rec["authors"] or "").split(";")[0].split(",")[0].strip() or rec["title"][:40]
        label = f"{first} {rec['year'] or ''}".strip()
    sid = new_id("review_study")
    with _write(conn):
        conn.execute("INSERT INTO review_study (id, review_id, record_id, article_id, document_id, label) "
                     "VALUES (?, ?, ?, ?, ?, ?)", (sid, review_id, record_id, article_id, document_id, label))
        audit.record(conn, actor=actor, action="review_study_added", entity_type="review_study", entity_id=sid,
                     payload={"review_id": review_id, "record_id": record_id, "article_id": article_id},
                     in_transaction=True)
    return {"id": sid, "label": label, "article_id": article_id, "document_id": document_id}


def decide_fulltext(conn: sqlite3.Connection, study_id: str, reviewer_id: str, decision: str, *,
                    criterion_id: str | None = None, note: str | None = None, actor: str = "user") -> dict:
    """The full-text decision, written by a human reviewer or the consensus only. The previous decision stays in the
    audit chain (the row of review_study is the current state)."""
    if decision not in FT_DECISIONS:
        raise ReviewError(f"decisione non ammessa: {decision} (include, exclude o not_retrieved)")
    st = _one(conn, "SELECT * FROM review_study WHERE id = ?", (study_id,), "studio")
    rv = _reviewer(conn, reviewer_id, st["review_id"])
    if rv["kind"] not in HUMAN_KINDS:
        raise ReviewError("la decisione sul full text la prendi tu (o il consenso), non Claude")
    if decision in ("exclude", "not_retrieved"):
        if not criterion_id:
            raise ReviewError("per escludere (o non reperito) indica il criterio")
        _criterion(conn, criterion_id, st["review_id"])
    elif criterion_id is not None:
        raise ReviewError("il criterio si indica solo per escludere")
    note = note.strip() if note and note.strip() else None
    now = audit.utc_now()
    with _write(conn):
        conn.execute("""UPDATE review_study SET ft_decision = ?, ft_criterion_id = ?, ft_decided_by = ?, ft_decided_at = ?,
                        ft_note = ? WHERE id = ?""", (decision, criterion_id, reviewer_id, now, note, study_id))
        audit.record(conn, actor=actor, action="review_study_ft_decision", entity_type="review_study", entity_id=study_id,
                     payload={"decision": decision, "criterion_id": criterion_id, "reviewer_id": reviewer_id,
                              "note": note, "previous": {"decision": st["ft_decision"],
                                                         "criterion_id": st["ft_criterion_id"],
                                                         "reviewer_id": st["ft_decided_by"], "note": st["ft_note"]}},
                     in_transaction=True)
    return {"id": study_id, "decisione": decision, "precedente": st["ft_decision"]}


def add_ft_proposal(conn: sqlite3.Connection, study_id: str, *, workflow_run_id: str, decision: str, summary: str,
                    rationale: str, criterion_id: str | None = None, quote: str | None = None,
                    page_no: int | None = None, section_ord: int | None = None, actor: str = "orpheus") -> str:
    """Claude's proposal on a full text (contract eurydike-ft-v1, already validated): never a decision."""
    if decision not in ("include", "exclude", "unsure"):
        raise ReviewError(f"proposta non ammessa: {decision}")
    if not summary.strip() or not rationale.strip() or summary.strip() == rationale.strip():
        raise ReviewError("sintesi e motivo devono esserci ed essere diversi")
    st = _one(conn, "SELECT * FROM review_study WHERE id = ?", (study_id,), "studio")
    if decision == "exclude":
        if not criterion_id:
            raise ReviewError("una proposta di esclusione indica il criterio")
        _criterion(conn, criterion_id, st["review_id"])
    if quote and (page_no is None) == (section_ord is None):
        raise ReviewError("la frase va con la pagina oppure con la sezione (una delle due)")
    old = conn.execute("SELECT id FROM ft_proposal WHERE study_id = ? AND superseded_by IS NULL", (study_id,)).fetchone()
    pid = new_id("ft_proposal")
    with _write(conn):
        if old:
            conn.execute("UPDATE ft_proposal SET superseded_by = ? WHERE id = ?", (pid, old[0]))
        conn.execute("""INSERT INTO ft_proposal (id, study_id, workflow_run_id, decision, summary, rationale, criterion_id,
                                                 quote, page_no, section_ord) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                     (pid, study_id, workflow_run_id, decision, summary.strip(), rationale.strip(), criterion_id,
                      quote, page_no if quote else None, section_ord if quote else None))
        audit.record(conn, actor=actor, action="ft_proposal_added", entity_type="ft_proposal", entity_id=pid,
                     payload={"study_id": study_id, "decision": decision, "replaces": old[0] if old else None},
                     in_transaction=True)
    return pid
