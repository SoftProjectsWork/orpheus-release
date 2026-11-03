"""EURYDIKE E4 · bulk rules of the screening (migration 0021; decisions of the user of 2026-11-03: words in title or
abstract, year, publication type; a rule is ONLY A PROPOSAL, confirmed or rejected record by record; the records it
proposes to exclude are not sent to Claude's lots, unless a reviewer rejected the proposal).

- `add_rule` / `remove_rule`: a rule proposes «exclude» with an exclusion criterion; every rule has its row in the
  register of the review (review_log, kind 'rule'). A rule taken away keeps its row (removed_at).
- `answer`: the reviewer confirms (= his decision «exclude», origin 'rule', linked to the rule's register row) or
  rejects; a new answer supersedes the old one. Never a decision without the reviewer's click.
- `fill_pub_types`: the publication types of records imported before 0.70.0, read again from the search files
  (only with a button of the user).
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path

from .. import audit
from ..ids import new_id
from . import review as rv
from . import screening as sc
from .core import records as core_records

KINDS = ("words", "year", "pubtype")
WHERE = ("titolo", "titolo_abstract")
CONSENSUS = "consensus"


class RuleError(ValueError):
    pass


def _pattern(word: str) -> re.Pattern:
    return re.compile(r"(?<![0-9a-z])" + re.escape(word.lower().strip()), re.I)


def _types(raw: str | None) -> list[str]:
    try:
        v = json.loads(raw) if raw else []
    except ValueError:
        return []
    return [str(x) for x in v] if isinstance(v, list) else []


def clean_params(kind: str, params: dict) -> dict:
    """Checked parameters of a rule (Italian messages)."""
    if kind == "words":
        words = params.get("parole")
        if isinstance(words, str):
            words = re.split(r"[\n,;]+", words)
        words = [w.strip() for w in (words or []) if isinstance(w, str) and w.strip()]
        if not words:
            raise RuleError("scrivi almeno una parola o frase")
        if any(len(w) < 3 or len(w) > 80 for w in words):
            raise RuleError("ogni parola o frase: da 3 a 80 caratteri")
        where = params.get("dove") or "titolo"
        if where not in WHERE:
            raise RuleError("dove: titolo oppure titolo_abstract")
        return {"parole": list(dict.fromkeys(words))[:30], "dove": where}
    if kind == "year":
        out = {}
        for k in ("prima_di", "dopo"):
            v = params.get(k)
            if v in (None, ""):
                continue
            try:
                n = int(v)
            except (TypeError, ValueError) as exc:
                raise RuleError("l'anno è un numero (es. 2000)") from exc
            if not 1800 <= n <= 2100:
                raise RuleError("anno tra 1800 e 2100")
            out[k] = n
        if not out:
            raise RuleError("indica «prima del» o «dopo il» (anno)")
        if "prima_di" in out and "dopo" in out and out["prima_di"] > out["dopo"]:
            raise RuleError("«prima del» deve essere minore di «dopo il» (altrimenti esclude tutto)")
        return out
    if kind == "pubtype":
        types = params.get("tipi")
        if isinstance(types, str):
            types = re.split(r"[\n;]+", types)
        types = [t.strip() for t in (types or []) if isinstance(t, str) and t.strip()]
        if not types:
            raise RuleError("scegli almeno un tipo di pubblicazione")
        return {"tipi": list(dict.fromkeys(types))[:20]}
    raise RuleError("tipo di regola: words, year o pubtype")


def describe(kind: str, p: dict) -> str:
    if kind == "words":
        where = "nel titolo" if p["dove"] == "titolo" else "nel titolo o nell'abstract"
        return f"{where}: " + ", ".join(f"«{w}»" for w in p["parole"])
    if kind == "year":
        parts = ([f"prima del {p['prima_di']}"] if "prima_di" in p else []) + ([f"dopo il {p['dopo']}"] if "dopo" in p else [])
        return "anno " + " o ".join(parts)
    return "tipo di pubblicazione: " + ", ".join(p["tipi"])


def matcher(kind: str, p: dict):
    if kind == "words":
        pats = [_pattern(w) for w in p["parole"]]
        return lambda r: any(x.search(r["title"] + (" " + (r["abstract"] or "") if p["dove"] == "titolo_abstract" else ""))
                             for x in pats)
    if kind == "year":
        def by_year(r):
            m = re.match(r"\d{4}", r["year"] or "")
            if not m:
                return False
            y = int(m.group(0))
            return ("prima_di" in p and y < p["prima_di"]) or ("dopo" in p and y > p["dopo"])
        return by_year
    wanted = {t.lower() for t in p["tipi"]}
    return lambda r: any(t.lower() in wanted for t in _types(r["pub_types"]))


def _records(conn: sqlite3.Connection, review_id: str) -> list[sqlite3.Row]:
    return conn.execute("SELECT id, title, abstract, year, pub_types FROM review_record WHERE review_id = ? "
                        "AND duplicate_of IS NULL ORDER BY created_at, rowid", (review_id,)).fetchall()


def matches(conn: sqlite3.Connection, rule: sqlite3.Row, recs: list[sqlite3.Row] | None = None) -> list[str]:
    f = matcher(rule["kind"], json.loads(rule["params"]))
    return [r["id"] for r in (recs if recs is not None else _records(conn, rule["review_id"])) if f(r)]


def preview(conn: sqlite3.Connection, review_id: str, kind: str, params: dict, n: int = 8) -> dict:
    """How many records a rule would propose, with a few titles (nothing is written)."""
    p = clean_params(kind, params)
    f = matcher(kind, p)
    hit = [r for r in _records(conn, review_id) if f(r)]
    return {"descrizione": describe(kind, p), "trovati": len(hit),
            "esempi": [{"id": r["id"], "titolo": r["title"], "anno": r["year"]} for r in hit[:n]]}


def active_rules(conn: sqlite3.Connection, review_id: str) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM screening_rule WHERE review_id = ? AND removed_at IS NULL ORDER BY created_at, rowid",
                        (review_id,)).fetchall()


def add_rule(conn: sqlite3.Connection, review_id: str, kind: str, params: dict, criterion_id: str, *,
             label: str | None = None, why: str | None = None, actor: str = "user") -> dict:
    if conn.execute("SELECT 1 FROM review WHERE id = ?", (review_id,)).fetchone() is None:
        raise RuleError("revisione non trovata")
    p = clean_params(kind, params)
    crit = conn.execute("SELECT kind, review_id FROM review_criterion WHERE id = ? AND superseded_by IS NULL",
                        (criterion_id or "",)).fetchone()
    if crit is None or crit["review_id"] != review_id or crit["kind"] != "exclusion":
        raise RuleError("scegli un criterio di esclusione del protocollo")
    text = describe(kind, p)
    for r in active_rules(conn, review_id):
        if r["kind"] == kind and json.loads(r["params"]) == p:
            raise RuleError("c'è già una regola uguale")
    me = sc.human_reviewer(conn, review_id, label)
    n = len(matches(conn, {"kind": kind, "params": json.dumps(p), "review_id": review_id}))
    log = rv.add_log(conn, review_id, me["id"], kind="rule", title=f"Regola (proposta, escludi): {text}",
                     why=(why or "").strip() or None, n_records=n, actor=actor)
    sid = new_id("screening_rule")
    with rv._write(conn):
        conn.execute("INSERT INTO screening_rule (id, review_id, kind, params, criterion_id, log_id) VALUES (?, ?, ?, ?, ?, ?)",
                     (sid, review_id, kind, json.dumps(p, ensure_ascii=False, sort_keys=True), criterion_id, log))
        audit.record(conn, actor=actor, action="screening_rule_added", entity_type="screening_rule", entity_id=sid,
                     payload={"review_id": review_id, "kind": kind, "params": p, "criterion_id": criterion_id,
                              "records": n}, in_transaction=True)
    return {"id": sid, "descrizione": text, "trovati": n}


def remove_rule(conn: sqlite3.Connection, review_id: str, rule_id: str, *, actor: str = "user") -> None:
    r = conn.execute("SELECT * FROM screening_rule WHERE id = ? AND review_id = ?", (rule_id, review_id)).fetchone()
    if r is None:
        raise RuleError("regola non trovata in questa revisione")
    if r["removed_at"]:
        return
    with rv._write(conn):
        conn.execute("UPDATE screening_rule SET removed_at = ? WHERE id = ?", (audit.utc_now(), rule_id))
        audit.record(conn, actor=actor, action="screening_rule_removed", entity_type="screening_rule", entity_id=rule_id,
                     payload={"review_id": review_id}, in_transaction=True)


def _answers(conn: sqlite3.Connection, review_id: str) -> dict[tuple[str, str], dict[str, str]]:
    """{(rule, record): {reviewer_id: answer}} — current answers."""
    out: dict[tuple[str, str], dict[str, str]] = {}
    for a in conn.execute("""SELECT a.rule_id, a.record_id, a.reviewer_id, a.answer FROM screening_rule_answer a
                             JOIN screening_rule s ON s.id = a.rule_id WHERE s.review_id = ? AND a.superseded_by IS NULL""",
                          (review_id,)):
        out.setdefault((a[0], a[1]), {})[a[2]] = a[3]
    return out


def _current_decisions(conn: sqlite3.Connection, reviewer_id: str) -> dict[str, sqlite3.Row]:
    return {r["record_id"]: r for r in conn.execute(
        "SELECT record_id, decision, rule_log_id FROM screening_decision WHERE reviewer_id = ? AND superseded_by IS NULL",
        (reviewer_id,))}


def proposals(conn: sqlite3.Connection, review_id: str, *, label: str | None = None) -> dict:
    """For one reviewer: {record_id: [proposals]} of the active rules, with his answer (proposta / confermata /
    rifiutata). A record he has already decided by hand shows the proposal as «già decisa»."""
    me = sc.human_reviewer(conn, review_id, label)
    recs = _records(conn, review_id)
    ans = _answers(conn, review_id)
    mine = _current_decisions(conn, me["id"])
    crit = {r[0]: {"ord": r[1], "testo": r[2]} for r in conn.execute(
        "SELECT id, ord, text FROM review_criterion WHERE review_id = ?", (review_id,))}
    out: dict[str, list[dict]] = {}
    for rule in active_rules(conn, review_id):
        p = json.loads(rule["params"])
        for rid in matches(conn, rule, recs):
            a = ans.get((rule["id"], rid), {}).get(me["id"])
            d = mine.get(rid)
            state = {"confirmed": "confermata", "rejected": "rifiutata"}.get(a) or ("già decisa" if d is not None else "proposta")
            out.setdefault(rid, []).append({"regola": rule["id"], "descrizione": describe(rule["kind"], p),
                                            "criterio_id": rule["criterion_id"], "criterio": crit.get(rule["criterion_id"]),
                                            "stato": state})
    return {"revisore": me["label"], "proposte": out}


def answer(conn: sqlite3.Connection, review_id: str, rule_id: str, record_id: str, what: str, *,
           label: str | None = None, actor: str = "user") -> dict:
    """The reviewer's answer to one proposal: «confirmed» writes his decision «exclude» (origin 'rule'), «rejected»
    only records the refusal (his decision, if any, stays as it is)."""
    if what not in ("confirmed", "rejected"):
        raise RuleError("risposta: confirmed o rejected")
    rule = conn.execute("SELECT * FROM screening_rule WHERE id = ? AND review_id = ?", (rule_id, review_id)).fetchone()
    if rule is None or rule["removed_at"]:
        raise RuleError("regola non trovata o tolta")
    if record_id not in matches(conn, rule):
        raise RuleError("la regola non propone questo record")
    me = sc.human_reviewer(conn, review_id, label)
    dec_id = None
    if what == "confirmed":
        dec_id = rv.decide(conn, record_id, me["id"], "exclude", criterion_id=rule["criterion_id"], origin="rule",
                           rule_log_id=rule["log_id"], rationale=f"Regola: {describe(rule['kind'], json.loads(rule['params']))}",
                           actor=actor)["id"]
    old = conn.execute("SELECT id FROM screening_rule_answer WHERE rule_id = ? AND record_id = ? AND reviewer_id = ? "
                       "AND superseded_by IS NULL", (rule_id, record_id, me["id"])).fetchone()
    aid = new_id("screening_rule_answer")
    with rv._write(conn):
        if old:
            conn.execute("UPDATE screening_rule_answer SET superseded_by = ? WHERE id = ?", (aid, old[0]))
        conn.execute("INSERT INTO screening_rule_answer (id, rule_id, record_id, reviewer_id, answer, decision_id) "
                     "VALUES (?, ?, ?, ?, ?, ?)", (aid, rule_id, record_id, me["id"], what, dec_id))
        audit.record(conn, actor=actor, action="screening_rule_answered", entity_type="screening_rule_answer",
                     entity_id=aid, payload={"rule_id": rule_id, "record_id": record_id, "answer": what,
                                             "decision_id": dec_id, "replaces": old[0] if old else None},
                     in_transaction=True)
    return {"id": aid, "decisione": dec_id}


def rules(conn: sqlite3.Connection, review_id: str, *, label: str | None = None) -> list[dict]:
    """The active rules with their counts for the reviewer (found, still to answer, confirmed, rejected)."""
    me = sc.human_reviewer(conn, review_id, label)
    recs = _records(conn, review_id)
    ans = _answers(conn, review_id)
    mine = _current_decisions(conn, me["id"])
    crit = {r[0]: {"ord": r[1], "testo": r[2]} for r in conn.execute(
        "SELECT id, ord, text FROM review_criterion WHERE review_id = ?", (review_id,))}
    out = []
    for rule in active_rules(conn, review_id):
        hit = matches(conn, rule, recs)
        a = [ans.get((rule["id"], x), {}).get(me["id"]) for x in hit]
        open_ = sum(1 for x, y in zip(hit, a) if y is None and x not in mine)
        out.append({"id": rule["id"], "tipo": rule["kind"], "descrizione": describe(rule["kind"], json.loads(rule["params"])),
                    "criterio": crit.get(rule["criterion_id"]), "trovati": len(hit), "da_rispondere": open_,
                    "confermati": a.count("confirmed"), "rifiutati": a.count("rejected"),
                    "gia_decisi": sum(1 for x, y in zip(hit, a) if y is None and x in mine), "creata_il": rule["created_at"]})
    return out


def skipped_for_claude(conn: sqlite3.Connection, review_id: str) -> set[str]:
    """Records proposed for exclusion by an active rule and not rejected by any reviewer: not sent to Claude
    (pre-filter, decision of the user of 2026-11-03)."""
    rs = active_rules(conn, review_id)
    if not rs:
        return set()
    recs = _records(conn, review_id)
    ans = _answers(conn, review_id)
    out = set()
    for rule in rs:
        for rid in matches(conn, rule, recs):
            if "rejected" not in ans.get((rule["id"], rid), {}).values():
                out.add(rid)
    return out


def fill_pub_types(conn: sqlite3.Connection, home: Path, review_id: str, *, folder: Path | None = None,
                   actor: str = "user") -> dict:
    """Publication types of records imported before 0.70.0: the search files still in `ricerca\\` are read again
    (same SHA-256 as the import); records are matched in import order and only when the title is the same."""
    from .protocol import SEARCH, _read_text, review_dir
    r = conn.execute("SELECT data_dir FROM review WHERE id = ?", (review_id,)).fetchone()
    if r is None:
        raise RuleError("revisione non trovata")
    folder = Path(folder) if folder else review_dir(home, r["data_dir"]) / SEARCH
    files = {}
    if folder.is_dir():
        for f in folder.iterdir():
            if f.is_file():
                files[hashlib.sha256(f.read_bytes()).hexdigest()] = f
    filled, missing, changed = 0, [], []
    for imp in conn.execute("SELECT id, file_sha256, source_name FROM search_import WHERE review_id = ? ORDER BY imported_at, rowid",
                            (review_id,)).fetchall():
        rows = conn.execute("SELECT id, title, pub_types FROM review_record WHERE import_id = ? ORDER BY created_at, rowid",
                            (imp["id"],)).fetchall()
        if all(x["pub_types"] is not None for x in rows):
            continue
        f = files.get(imp["file_sha256"])
        if f is None:
            missing.append(imp["source_name"])
            continue
        _fmt, recs = core_records.parse_records(_read_text(f), f.name)
        recs = [x for x in recs if (x.get("title") or "").strip()]
        if len(recs) != len(rows):
            changed.append(imp["source_name"])
            continue
        upd = [(json.dumps(x.get("pub_types") or [], ensure_ascii=False), row["id"]) for row, x in zip(rows, recs)
               if row["pub_types"] is None and row["title"] == x["title"].strip()]
        if upd:
            with rv._write(conn):
                conn.executemany("UPDATE review_record SET pub_types = ? WHERE id = ? AND pub_types IS NULL", upd)
                audit.record(conn, actor=actor, action="review_record_pub_types_filled", entity_type="search_import",
                             entity_id=imp["id"], payload={"records": len(upd)}, in_transaction=True)
            filled += len(upd)
    return {"riempiti": filled, "file_mancanti": missing, "file_diversi": changed}


def pub_type_counts(conn: sqlite3.Connection, review_id: str) -> dict:
    """The publication types of the review with how many records (to choose a rule), and how many are not read yet."""
    counts: dict[str, int] = {}
    unread = 0
    for r in _records(conn, review_id):
        if r["pub_types"] is None:
            unread += 1
        for t in _types(r["pub_types"]):
            counts[t] = counts.get(t, 0) + 1
    return {"tipi": [{"tipo": k, "n": v} for k, v in sorted(counts.items(), key=lambda x: (-x[1], x[0].lower()))],
            "non_letti": unread}
