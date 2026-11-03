"""EURYDIKE — screening workspace (request of the user of 2026-11-03 14:14: records selectable to change a decision;
ideas taken from Rayyan and adapted: highlighted keywords for including and excluding, labels, filters with counts,
progress of every reviewer).

Blind rule (D6): another reviewer's decision and the final decision of a record are shown to a reviewer only after
his own decision on that record (or when the review is not blind). Keywords and labels are never decisions.
Migration 0020: review_keyword, record_label (rows are never deleted: removed_at)."""
from __future__ import annotations

import re
import sqlite3
from collections import Counter

from .. import audit
from ..ids import new_id
from . import review as rv
from . import screening as sc
from .overview import CONSENSUS, _bucket, _decisions, _finals

KINDS = ("include", "exclude")
DECISION_FILTERS = ("tutti", "da_decidere", "decisi", "inclusi", "esclusi", "forse", "conflitti", "proposte")
COUNT_FILTERS = ("qualsiasi", "nessuna", "una", "due_o_piu")
STOP = set("""a an and are as at be by for from in into is it its of on or that the their this to with without not no
than more less other others any all only who which were was have has study studies articles article individuals people
written english reported component review reviews protocol including include exclude""".split())


class WorkspaceError(ValueError):
    pass


def _review_need(conn: sqlite3.Connection, review_id: str) -> sqlite3.Row:
    r = conn.execute("SELECT id, screen_reviewers, blind_reviewers FROM review WHERE id = ?", (review_id,)).fetchone()
    if r is None:
        raise WorkspaceError("revisione non trovata")
    return r


# ------------------------------------------------------------------ keywords
def keywords(conn: sqlite3.Connection, review_id: str) -> dict:
    out: dict = {"include": [], "exclude": []}
    for r in conn.execute("SELECT id, kind, word FROM review_keyword WHERE review_id = ? AND removed_at IS NULL "
                          "ORDER BY created_at, rowid", (review_id,)):
        out[r[1]].append({"id": r[0], "parola": r[2]})
    return out


def _pattern(word: str) -> re.Pattern:
    """A word matches at the start of a word ("barrier" finds "barriers"); a phrase matches as written."""
    return re.compile(r"(?<![0-9a-z])" + re.escape(word.lower().strip()), re.I)


def add_keyword(conn: sqlite3.Connection, review_id: str, kind: str, word: str, *, actor: str = "user") -> str:
    _review_need(conn, review_id)
    if kind not in KINDS:
        raise WorkspaceError("tipo di parola: include o exclude")
    word = " ".join(str(word or "").split())
    if not word or len(word) > 80:
        raise WorkspaceError("scrivi una parola (al massimo 80 caratteri)")
    if conn.execute("SELECT 1 FROM review_keyword WHERE review_id = ? AND kind = ? AND lower(word) = lower(?) "
                    "AND removed_at IS NULL", (review_id, kind, word)).fetchone():
        raise WorkspaceError("questa parola c'è già")
    kid = new_id("review_keyword")
    with rv._write(conn):
        conn.execute("INSERT INTO review_keyword (id, review_id, kind, word) VALUES (?, ?, ?, ?)", (kid, review_id, kind, word))
        audit.record(conn, actor=actor, action="review_keyword_added", entity_type="review_keyword", entity_id=kid,
                     payload={"review_id": review_id, "kind": kind}, in_transaction=True)
    return kid


def remove_keyword(conn: sqlite3.Connection, review_id: str, keyword_id: str, *, actor: str = "user") -> None:
    r = conn.execute("SELECT id FROM review_keyword WHERE id = ? AND review_id = ? AND removed_at IS NULL",
                     (keyword_id, review_id)).fetchone()
    if r is None:
        raise WorkspaceError("parola non trovata")
    with rv._write(conn):
        conn.execute("UPDATE review_keyword SET removed_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id = ?", (keyword_id,))
        audit.record(conn, actor=actor, action="review_keyword_removed", entity_type="review_keyword", entity_id=keyword_id,
                     payload={"review_id": review_id}, in_transaction=True)


def suggested_keywords(conn: sqlite3.Connection, review_id: str) -> dict:
    """Words of the current criteria not yet in the lists: inclusion criteria -> include, exclusion -> exclude."""
    have = {(k, x["parola"].lower()) for k, xs in keywords(conn, review_id).items() for x in xs}
    out: dict = {"include": [], "exclude": []}
    for c in rv.current_criteria(conn, review_id):
        if c["kind"] not in ("inclusion", "exclusion"):
            continue
        kind = "include" if c["kind"] == "inclusion" else "exclude"
        if kind == "exclude" and re.match(r"\s*(no|not|without|lack of)\b", c["text"], re.I):
            kind = "include"                 # "No physical activity component": its words point to inclusion
        for w in re.findall(r"[a-zA-Z][a-zA-Z\-]{3,}", c["text"]):
            w = w.lower()
            if w not in STOP and (kind, w) not in have and w not in out[kind]:
                out[kind].append(w)
    return {k: v[:12] for k, v in out.items()}


# ------------------------------------------------------------------ labels
def add_label(conn: sqlite3.Connection, review_id: str, record_id: str, label: str, *, actor: str = "user") -> str:
    rec = conn.execute("SELECT review_id FROM review_record WHERE id = ?", (record_id,)).fetchone()
    if rec is None or rec[0] != review_id:
        raise WorkspaceError("record non trovato in questa revisione")
    label = " ".join(str(label or "").split())
    if not label or len(label) > 40:
        raise WorkspaceError("scrivi un'etichetta (al massimo 40 caratteri)")
    if conn.execute("SELECT 1 FROM record_label WHERE record_id = ? AND lower(label) = lower(?) AND removed_at IS NULL",
                    (record_id, label)).fetchone():
        raise WorkspaceError("il record ha già questa etichetta")
    lid = new_id("record_label")
    with rv._write(conn):
        conn.execute("INSERT INTO record_label (id, record_id, label) VALUES (?, ?, ?)", (lid, record_id, label))
        audit.record(conn, actor=actor, action="record_label_added", entity_type="record_label", entity_id=lid,
                     payload={"review_id": review_id, "record_id": record_id}, in_transaction=True)
    return lid


def remove_label(conn: sqlite3.Connection, review_id: str, record_id: str, label: str, *, actor: str = "user") -> None:
    r = conn.execute("""SELECT l.id FROM record_label l JOIN review_record r ON r.id = l.record_id
                        WHERE l.record_id = ? AND r.review_id = ? AND lower(l.label) = lower(?) AND l.removed_at IS NULL""",
                     (record_id, review_id, str(label or "").strip())).fetchone()
    if r is None:
        raise WorkspaceError("etichetta non trovata su questo record")
    with rv._write(conn):
        conn.execute("UPDATE record_label SET removed_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id = ?", (r[0],))
        audit.record(conn, actor=actor, action="record_label_removed", entity_type="record_label", entity_id=r[0],
                     payload={"review_id": review_id, "record_id": record_id}, in_transaction=True)


def _labels(conn: sqlite3.Connection, review_id: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for rid, lab in conn.execute("""SELECT l.record_id, l.label FROM record_label l JOIN review_record r ON r.id = l.record_id
                                    WHERE r.review_id = ? AND l.removed_at IS NULL ORDER BY l.created_at, l.rowid""",
                                 (review_id,)):
        out.setdefault(rid, []).append(lab)
    return out


# ------------------------------------------------------------------ list with filters and counts
def _n_bucket(n: int) -> str:
    return "nessuna" if n == 0 else "una" if n == 1 else "due_o_piu"


def _visible(mine: dict | None, blind: bool) -> bool:
    return bool(mine) or not blind


def board(conn: sqlite3.Connection, review_id: str, *, label: str | None = None, filtro: str = "da_decidere",
          q: str = "", parola: str = "", etichetta: str = "", motivo: str = "", n_decisioni: str = "qualsiasi",
          da: int = 0, quanti: int = 50) -> dict:
    """The records of the list for one reviewer, with the counts of every filter (computed on the records left by the
    other filters' choice of decision: so the numbers say what a click would show)."""
    rvw = _review_need(conn, review_id)
    blind = bool(rvw["blind_reviewers"])
    me = sc.human_reviewer(conn, review_id, label)
    decisions = _decisions(conn, review_id)
    finals = _finals(conn, review_id, rvw["screen_reviewers"], decisions)
    labels = _labels(conn, review_id)
    from .rules import proposals              # bulk rules (0.70.0): records with a proposal still to answer
    props = proposals(conn, review_id, label=label)["proposte"]
    kws = keywords(conn, review_id)
    pats = {(k, x["parola"]): _pattern(x["parola"]) for k in KINDS for x in kws[k]}
    rows = conn.execute("SELECT id, title, abstract, authors, year, journal FROM review_record WHERE review_id = ? "
                        "AND duplicate_of IS NULL ORDER BY created_at, rowid", (review_id,)).fetchall()
    crit = {c["id"]: c for c in rv.current_criteria(conn, review_id)} | {
        r[0]: {"id": r[0], "ord": r[1], "text": r[2], "kind": r[3]} for r in conn.execute(
            "SELECT id, ord, text, kind FROM review_criterion WHERE review_id = ?", (review_id,))}
    if filtro not in DECISION_FILTERS:
        filtro = "da_decidere"
    if n_decisioni not in COUNT_FILTERS:
        n_decisioni = "qualsiasi"
    q = (q or "").strip().lower()

    items = []
    for r in rows:
        ds = decisions.get(r["id"], {})
        mine = ds.get(me["label"])
        text = f"{r['title']} {r['abstract'] or ''}"
        hits = [key for key, p in pats.items() if p.search(text)]
        visible = _visible(mine, blind)
        bucket = _bucket(finals[r["id"]]) if visible else None
        n = sum(1 for k in ds if k != CONSENSUS)
        items.append({"r": r, "mine": mine, "hits": hits, "labels": labels.get(r["id"], []), "bucket": bucket,
                      "n": n, "visible": visible, "ds": ds,
                      "prop": any(x["stato"] == "proposta" for x in props.get(r["id"], []))})

    def keep_decision(it):
        if filtro == "tutti":
            return True
        if filtro == "da_decidere":
            return it["mine"] is None
        if filtro == "decisi":
            return it["mine"] is not None
        if filtro == "proposte":
            return it["prop"]
        return it["bucket"] == filtro

    def keep_others(it, skip=""):
        if q and q not in f"{it['r']['title']} {it['r']['authors'] or ''} {it['r']['journal'] or ''}".lower():
            return False
        if parola and skip != "parola" and not any(w.lower() == parola.lower() for _, w in it["hits"]):
            return False
        if etichetta and skip != "etichetta" and etichetta.lower() not in (x.lower() for x in it["labels"]):
            return False
        if motivo and skip != "motivo" and not (it["mine"] and it["mine"]["c"] == motivo):
            return False
        if n_decisioni != "qualsiasi" and skip != "n" and _n_bucket(it["n"]) != n_decisioni:
            return False                 # how many reviewers decided is shown also in blind mode (never what)
        return True

    base = [it for it in items if keep_decision(it)]
    chosen = [it for it in base if keep_others(it)]
    kw_count = Counter(w for it in base if keep_others(it, "parola") for _, w in set(it["hits"]))
    lab_count = Counter(x for it in base if keep_others(it, "etichetta") for x in it["labels"])
    why_count = Counter(it["mine"]["c"] for it in base if keep_others(it, "motivo") and it["mine"] and it["mine"]["c"])
    n_count = Counter(_n_bucket(it["n"]) for it in base if keep_others(it, "n"))
    dec_count = Counter()
    for it in items:
        if keep_others(it):
            dec_count["tutti"] += 1
            dec_count["da_decidere" if it["mine"] is None else "decisi"] += 1
            if it["bucket"] in ("inclusi", "esclusi", "forse", "conflitti"):
                dec_count[it["bucket"]] += 1
            if it["prop"]:
                dec_count["proposte"] += 1
    da, quanti = max(0, int(da or 0)), max(1, min(int(quanti or 50), 200))
    page = [{"id": it["r"]["id"], "titolo": it["r"]["title"], "anno": it["r"]["year"],
             "mia": it["mine"]["d"] if it["mine"] else None, "finale": it["bucket"], "etichette": it["labels"],
             "parole": sorted({(k, w) for k, w in it["hits"]}), "regola": it["prop"]} for it in chosen[da:da + quanti]]
    return {"revisore": me["label"], "righe": page, "totale": len(chosen), "da": da,
            "conteggi": {"decisione": dict(dec_count),
                         "parole": {k: [{**x, "n": kw_count.get(x["parola"], 0)} for x in kws[k]] for k in KINDS},
                         "etichette": [{"etichetta": k, "n": v} for k, v in sorted(lab_count.items(), key=lambda x: (-x[1], x[0].lower()))],
                         "motivi": [{"id": cid, "ord": crit[cid]["ord"], "testo": crit[cid]["text"], "n": n}
                                    for cid, n in sorted(why_count.items(), key=lambda x: crit.get(x[0], {}).get("ord", 99)) if cid in crit],
                         "numero": dict(n_count)},
            "suggerite": suggested_keywords(conn, review_id),
            "tutte_le_etichette": sorted({x for v in labels.values() for x in v}, key=str.lower)}


def record_detail(conn: sqlite3.Connection, review_id: str, record_id: str, *, label: str | None = None) -> dict:
    """One record for the reviewer: his decision (to change it), the others' only after his own (blind), labels."""
    rvw = _review_need(conn, review_id)
    me = sc.human_reviewer(conn, review_id, label)
    r = conn.execute("SELECT * FROM review_record WHERE id = ? AND review_id = ?", (record_id, review_id)).fetchone()
    if r is None:
        raise WorkspaceError("record non trovato in questa revisione")
    ds = _decisions(conn, review_id).get(record_id, {})
    mine = ds.get(me["label"])
    visible = _visible(mine, bool(rvw["blind_reviewers"]))
    others = [{"revisore": "Consenso" if k == CONSENSUS else k, "decisione": v["d"], "motivo": v["r"] or None,
               "criterio": v["c"]} for k, v in ds.items() if k != me["label"]] if visible else None
    fin = _finals(conn, review_id, rvw["screen_reviewers"], {record_id: ds}).get(record_id) if visible else None
    return {"id": r["id"], "titolo": r["title"], "abstract": r["abstract"], "autori": r["authors"], "anno": r["year"],
            "rivista": r["journal"], "doi": r["doi"], "pmid": r["pmid"], "doppione_di": r["duplicate_of"],
            "revisore": me["label"],
            "mia": {"decisione": mine["d"], "criterio": mine["c"], "motivo": mine["r"] or None} if mine else None,
            "altre": others, "finale": _bucket(fin) if fin else None, "nascoste": not visible and len(ds) > 0,
            "etichette": _labels(conn, review_id).get(record_id, []), "criteri": sc._criteria(conn, review_id),
            "parole": keywords(conn, review_id), "regole": _rule_props(conn, review_id, record_id, label)}


def _rule_props(conn: sqlite3.Connection, review_id: str, record_id: str, label: str | None) -> list[dict]:
    from .rules import proposals
    return proposals(conn, review_id, label=label)["proposte"].get(record_id, [])


# ------------------------------------------------------------------ progress (overview)
def progress(conn: sqlite3.Connection, review_id: str) -> dict:
    """Screening progress: final state of the records (included, maybe, excluded, conflicts, still open) and, for every
    reviewer, how many records he has decided and how (counts only, never another reviewer's choice on a record)."""
    rvw = _review_need(conn, review_id)
    decisions = _decisions(conn, review_id)
    finals = _finals(conn, review_id, rvw["screen_reviewers"], decisions)
    buckets = Counter(_bucket(f) for f in finals.values())
    total = len(finals)
    revs = []
    for v in conn.execute("SELECT label, kind FROM review_reviewer WHERE review_id = ? AND kind <> 'consensus' "
                          "ORDER BY created_at, rowid", (review_id,)):
        c = Counter(d[v["label"]]["d"] for rid, d in decisions.items() if v["label"] in d and rid in finals)
        done = sum(c.values())
        revs.append({"revisore": v["label"], "tipo": v["kind"], "fatti": done, "restano": total - done,
                     "inclusi": c.get("include", 0), "forse": c.get("maybe", 0), "esclusi": c.get("exclude", 0),
                     "percento": round(100 * done / total) if total else 0})
    return {"record": total, "stato": {k: buckets.get(k, 0) for k in ("inclusi", "forse", "esclusi", "conflitti",
                                                                      "parziali", "da_decidere")},
            "revisori": revs}
