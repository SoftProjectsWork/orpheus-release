"""EURYDIKE — duplicates of a review checked by a person in the dashboard (requests of the user of 2026-11-03:
"chiedi anche una verifica manuale dei duplicati"; 11:30 "come in Rayyan"; 12:11 "Rimuovi la funzionalità csv").

A *pair* is (record, other record) with a kind:
- `trovato`: marked duplicate at import (same DOI, PMID or title and year);
- `proposto`: not marked at import, but a duplicate with today's rule (records imported before the rule of 2026-11-03);
- `possibile`: same title with a different year or different DOI/PMID: only a person can tell;
- `deciso`: a pair the person called different articles, kept in the list of the decided ones.
Every pair has a state: "" to decide, "S" confirmed duplicate, "N" different articles. In EURYDIKE → Doppioni the two
records are side by side with the differences marked: S makes one record the duplicate of the other (the one kept stays
in the screening), N makes both records stand on their own and the pair is never proposed again. The found pairs with
the same DOI in both records can be confirmed in one go. Records are never deleted: only `duplicate_of` changes; every
decision is in the audit (it is also the memory of the decisions)."""
from __future__ import annotations

import difflib
import json
import re
import sqlite3

from .. import audit
from . import review as rv
from .core import records as core_records

KINDS = ("trovato", "proposto", "possibile")
STATES = {"da_decidere": "", "confermati": "S", "scartati": "N"}
ACTIONS = ("duplicate_check_applied", "duplicate_pair_decided")


def _records(conn: sqlite3.Connection, review_id: str) -> list[sqlite3.Row]:
    cur = conn.cursor()
    cur.row_factory = sqlite3.Row
    return cur.execute("""SELECT r.id, r.title, r.abstract, r.year, r.authors, r.journal, r.doi, r.pmid, r.dedup_key,
                                  r.duplicate_of, i.source_name, i.database_name
                           FROM review_record r JOIN search_import i ON i.id = r.import_id
                           WHERE r.review_id = ? ORDER BY r.created_at, r.rowid""", (review_id,)).fetchall()


def _rec(r: sqlite3.Row) -> dict:
    return {"title": r["title"], "year": r["year"], "doi": r["doi"], "pmid": r["pmid"]}


def _reason(a: sqlite3.Row, b: sqlite3.Row) -> str:
    ka = {k for _, k in core_records.match_keys(_rec(a))}
    for reason, k in core_records.match_keys(_rec(b)):
        if k in ka:
            return reason
    if core_records.match_title(a["title"]) == core_records.match_title(b["title"]):
        return "titolo, anno diverso" if (a["year"] or "") != (b["year"] or "") else "titolo, DOI o PMID diversi"
    return "deciso a mano"


def decisions(conn: sqlite3.Connection, review_id: str) -> dict[frozenset, dict]:
    """The person's decision on every pair, the latest one wins: {frozenset(ids): {"v": "S"|"N", "tieni": id|None}}."""
    out: dict[frozenset, dict] = {}
    marks = ",".join("?" * len(ACTIONS))
    for (payload,) in conn.execute(f"SELECT payload FROM audit_event WHERE action IN ({marks}) AND entity_type = 'review' "
                                   "AND entity_id = ? ORDER BY seq", (*ACTIONS, review_id)):
        p = json.loads(payload)
        for a, b in p.get("not_duplicates", []):                     # first CSV files of 0.63.0
            out[frozenset((a, b))] = {"v": "N", "tieni": None}
        for d in p.get("decisions", []):
            out[frozenset((d["record"], d["altro"]))] = {"v": d["v"], "tieni": d.get("tieni")}
    return out


def similarity(a: str | None, b: str | None) -> int:
    """Similarity of two titles in % (difflib on the matching form of the titles)."""
    return round(100 * difflib.SequenceMatcher(None, core_records.match_title(a), core_records.match_title(b)).ratio())


def pairs(conn: sqlite3.Connection, review_id: str) -> list[dict]:
    """Every pair with its kind, reason and state, in the order of the import: trovati, proposti, possibili, decisi."""
    done = decisions(conn, review_id)
    no = {k for k, d in done.items() if d["v"] == "N"}
    rows = _records(conn, review_id)
    by_id = {r["id"]: r for r in rows}
    out = [{"tipo": "trovato", "motivo": _reason(r, by_id[r["duplicate_of"]]), "record": r, "originale": by_id[r["duplicate_of"]]}
           for r in rows if r["duplicate_of"] and r["duplicate_of"] in by_id]
    index, proposed = core_records.DedupIndex(), set()
    for r in rows:
        if r["duplicate_of"]:
            continue
        dup, reason = index.find(_rec(r))
        if dup and frozenset((r["id"], dup)) not in no:
            proposed.add(r["id"])
            out.append({"tipo": "proposto", "motivo": reason, "record": r, "originale": by_id[dup]})
        else:
            index.add(r["id"], _rec(r))
    first: dict[str, sqlite3.Row] = {}
    for r in rows:
        if r["duplicate_of"] or r["id"] in proposed:
            continue
        t = core_records.match_title(r["title"])
        if len(t) < 20:          # very short titles ("Editorial", "Successful aging") say nothing
            continue
        o = first.setdefault(t, r)
        if o is not r and frozenset((r["id"], o["id"])) not in no:
            why = "titolo, anno diverso" if (o["year"] or "") != (r["year"] or "") else "titolo, DOI o PMID diversi"
            out.append({"tipo": "possibile", "motivo": why, "record": r, "originale": o})
    shown = {frozenset((p["record"]["id"], p["originale"]["id"])) for p in out}
    for key, d in done.items():
        if d["v"] == "N" and key not in shown and all(x in by_id for x in key):
            a, b = sorted(key, key=lambda x: list(by_id).index(x))
            out.append({"tipo": "deciso", "motivo": "deciso a mano", "record": by_id[b], "originale": by_id[a]})
    for p in out:
        d = done.get(frozenset((p["record"]["id"], p["originale"]["id"])))
        # a decision S on a pair that is no longer a duplicate (or N on one that is) does not hold any more
        held = d and ((d["v"] == "S") == (p["tipo"] == "trovato"))
        p["stato"] = d["v"] if held else ""
    return out


def counts(found: list[dict]) -> dict:
    by_kind = {k: sum(1 for p in found if p["tipo"] == k) for k in KINDS}
    by_state = {k: sum(1 for p in found if p["stato"] == v) for k, v in STATES.items()}
    return {"per_tipo": by_kind, "per_stato": by_state,
            "da_decidere_non_trovati": sum(1 for p in found if not p["stato"] and p["tipo"] in ("proposto", "possibile"))}


def _view(r: sqlite3.Row) -> dict:
    return {"id": r["id"], "titolo": r["title"], "abstract": r["abstract"], "anno": r["year"], "autori": r["authors"],
            "rivista": r["journal"], "doi": r["doi"], "pmid": r["pmid"], "fonte": r["source_name"],
            "banca_dati": r["database_name"], "doppione_di": r["duplicate_of"]}


ORDER = {"proposto": 0, "possibile": 1, "trovato": 2, "deciso": 3}


def page(conn: sqlite3.Connection, review_id: str, *, stato: str = "da_decidere", tipo: str = "", da: int = 0,
         quanti: int = 20) -> dict:
    """Pairs for the dashboard: filtered by state and kind, the ones a person must look at first (proposti, possibili,
    then trovati by title, PMID, DOI), with both records in full and the similarity of the titles."""
    if stato not in STATES:
        raise ValueError(f"stato non ammesso: {stato}")
    found = pairs(conn, review_id)
    sel = [p for p in found if p["stato"] == STATES[stato] and (not tipo or p["tipo"] == tipo)]
    reason_rank = {"titolo": 0, "pmid": 1, "doi": 2}
    sel.sort(key=lambda p: (ORDER[p["tipo"]], reason_rank.get(p["motivo"], 0)))
    rows = [{"tipo": p["tipo"], "motivo": p["motivo"], "stato": p["stato"],
             "somiglianza": similarity(p["record"]["title"], p["originale"]["title"]),
             "record": _view(p["record"]), "originale": _view(p["originale"])} for p in sel[da:da + quanti]]
    return {"righe": rows, "totale": len(sel), **counts(found),
            "trovati_per_doi_da_decidere": sum(1 for p in found if p["tipo"] == "trovato" and p["motivo"] == "doi"
                                               and not p["stato"] and p["record"]["doi"] and p["originale"]["doi"])}


# ------------------------------------------------------------------ writes
def _free_key(conn: sqlite3.Connection, review_id: str, rid: str, key: str) -> str:
    taken = conn.execute("SELECT 1 FROM review_record WHERE review_id = ? AND dedup_key = ? AND duplicate_of IS NULL "
                         "AND id <> ?", (review_id, key, rid)).fetchone()
    return f"{key.split('#')[0]}#{rid}" if taken else key


def _row(conn: sqlite3.Connection, review_id: str, rid: str) -> sqlite3.Row:
    cur = conn.cursor()
    cur.row_factory = sqlite3.Row
    r = cur.execute("SELECT id, dedup_key, duplicate_of FROM review_record WHERE id = ? AND review_id = ?",
                     (rid, review_id)).fetchone()
    if r is None:
        raise rv.ReviewError(f"record {rid} non di questa revisione")
    return r


def _unmark(conn: sqlite3.Connection, review_id: str, rid: str) -> None:
    r = _row(conn, review_id, rid)
    conn.execute("UPDATE review_record SET duplicate_of = NULL, dedup_key = ? WHERE id = ?",
                 (_free_key(conn, review_id, rid, r["dedup_key"]), rid))


def _decide(conn: sqlite3.Connection, review_id: str, a: str, b: str, v: str, keep: str | None) -> list[dict]:
    """Apply one decision inside a transaction. Returns the changes (record, before, after)."""
    if a == b:
        raise rv.ReviewError("un record non è duplicato di sé stesso")
    ra, rb = _row(conn, review_id, a), _row(conn, review_id, b)
    changes: list[dict] = []
    if v == "N":
        for x, y in ((ra, b), (rb, a)):
            if x["duplicate_of"] == y:
                _unmark(conn, review_id, x["id"])
                changes.append({"record": x["id"], "prima": f"duplicato di {y}", "dopo": "non duplicato"})
        return changes
    if keep not in (None, a, b):
        raise rv.ReviewError("il record da tenere deve essere uno dei due")
    if keep is None:                       # keep the one already kept, else the first imported (b, the "originale")
        keep = a if rb["duplicate_of"] == a else b
    drop = b if keep == a else a
    if _row(conn, review_id, keep)["duplicate_of"] == drop:          # swap: the kept one stops being a duplicate
        _unmark(conn, review_id, keep)
        changes.append({"record": keep, "prima": f"duplicato di {drop}", "dopo": "tenuto"})
    root = _row(conn, review_id, keep)["duplicate_of"] or keep
    if root == drop:
        raise rv.ReviewError("i due record sono già legati al contrario: decidi prima l'altra coppia")
    before = _row(conn, review_id, drop)["duplicate_of"]
    if before != root:
        conn.execute("UPDATE review_record SET duplicate_of = ? WHERE duplicate_of = ?", (root, drop))
        conn.execute("UPDATE review_record SET duplicate_of = ? WHERE id = ?", (root, drop))
        decided = conn.execute("SELECT count(*) FROM screening_decision WHERE record_id = ? AND superseded_by IS NULL",
                               (drop,)).fetchone()[0]
        changes.append({"record": drop, "prima": f"duplicato di {before}" if before else "non duplicato",
                        "dopo": f"duplicato di {root}", "decisioni_che_non_contano_piu": decided})
    return changes


def decide_pair(conn: sqlite3.Connection, review_id: str, record: str, other: str, verdict: str, *,
                keep: str | None = None, actor: str = "user") -> dict:
    """One decision from the dashboard: verdict "S" (duplicates; `keep` = the record that stays, default the one
    already kept or the first imported) or "N" (different articles). Returns {"cambi", "conteggi"}."""
    v = {"s": "S", "n": "N"}.get(str(verdict).strip().lower())
    if v is None:
        raise rv.ReviewError("decisione non ammessa: S (duplicato) o N (articoli diversi)")
    with rv._write(conn):
        changes = _decide(conn, review_id, record, other, v, keep if v == "S" else None)
        audit.record(conn, actor=actor, action="duplicate_pair_decided", entity_type="review", entity_id=review_id,
                     payload={"review_id": review_id, "origin": "dashboard",
                              "decisions": [{"record": record, "altro": other, "v": v, "tieni": keep if v == "S" else None}],
                              "changes": changes}, in_transaction=True)
    return {"cambi": changes, **counts(pairs(conn, review_id))}


def confirm_found(conn: sqlite3.Connection, review_id: str, *, reason: str = "doi", actor: str = "user") -> dict:
    """Confirm in one go the duplicates found at import with the same `reason` (default the same DOI) not decided yet.
    With "doi" or "pmid" the identifier must be in both records (12:11: "solo se il doi è presente"); pairs without it
    stay to decide one by one. Nothing changes in the records: the decision S is recorded for each pair."""
    if reason not in ("doi", "pmid", "titolo"):
        raise rv.ReviewError(f"motivo non ammesso: {reason}")
    def both(p: dict, field: str) -> bool:        # the identifier is in both records, and it is the same
        x, y = p["record"][field], p["originale"][field]
        return bool(x and y and str(x).strip().lower() == str(y).strip().lower())

    sel = [p for p in pairs(conn, review_id) if p["tipo"] == "trovato" and p["motivo"] == reason and not p["stato"]
           and (reason == "titolo" or both(p, reason))]
    if sel:
        with rv._write(conn):
            audit.record(conn, actor=actor, action="duplicate_pair_decided", entity_type="review", entity_id=review_id,
                         payload={"review_id": review_id, "origin": f"dashboard: all found by {reason}",
                                  "decisions": [{"record": p["record"]["id"], "altro": p["originale"]["id"], "v": "S",
                                                 "tieni": p["originale"]["id"]} for p in sel], "changes": []},
                         in_transaction=True)
    return {"confermati": len(sel), **counts(pairs(conn, review_id))}


__all__ = ["pairs", "page", "decide_pair", "confirm_found", "decisions", "similarity"]
