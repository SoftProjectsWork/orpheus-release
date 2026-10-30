"""EURYDIKE store (E2, T-051c): the unified article card cards-v2 on the tables of migration 0014.

One card per article, shared by ORPHEUS (search, claims, writing) and EURYDIKE (extraction, meta-analysis) — decision
D16 of 2026-10-19. Rules:
- nothing is deleted or rewritten: a new value supersedes the current one (`superseded_by`), in one transaction;
- `confirmed`, `edited` and `nr` are written only by the user (actor 'human', also enforced by a CHECK);
- a quote given with a correction is looked for in the article's text (ORPHEUS normalisation): exact / fuzzy / not_found;
- the cards-v1 cards are never changed: `convert_v1` copies their values (from_card_id, from_ord). A value the user
  checked as right becomes `confirmed`; one checked as wrong stays `ai` with a note, to be corrected; values Claude
  declared not reported are not copied (cards-v2 keeps 'not reported' as a decision of the user) and are counted;
- the card exported (JSON + CSV) marks every value corrected by hand.
"""
from __future__ import annotations

import csv
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .. import audit
from ..db import transaction
from ..ids import new_id
from .core.text import find_quote

CARD_VERSION = "cards-v2"
OUTCOME_KEY = "righe_esito"
STATS = ("n", "mean", "sd", "median", "q1", "q3", "min", "max", "se", "ci_low", "ci_high")
HUMAN_STATES = ("confirmed", "edited", "nr")
WRONG_NOTE = "checked as wrong in the manual control of cards-v1: to be corrected"


class StoreError(ValueError):
    """A request the store refuses (message in Italian, for the user)."""


@contextmanager
def _write(conn: sqlite3.Connection):
    """One write transaction with foreign keys checked at commit (a row points to its successor before it exists)."""
    with transaction(conn):
        conn.execute("PRAGMA defer_foreign_keys = ON")
        yield conn


# ------------------------------------------------------------------ text of the article (for quotes)
def _document_of(conn: sqlite3.Connection, article_id: str) -> str | None:
    """The PDF the card refers to: the one of the current cards-v1 card, else the article's PDF with most pages."""
    r = conn.execute("SELECT document_id FROM article_card WHERE article_id = ? AND status = 'current' "
                     "AND document_id IS NOT NULL ORDER BY created_at DESC LIMIT 1", (article_id,)).fetchone()
    if r:
        return r[0]
    r = conn.execute("SELECT id FROM document WHERE kind = 'article' AND article_id = ? ORDER BY page_count DESC, id LIMIT 1",
                     (article_id,)).fetchone()
    return r[0] if r else None


def _pages(conn: sqlite3.Connection, document_id: str | None) -> list[str]:
    if not document_id:
        return []
    rows = {r[0]: r[1] or "" for r in conn.execute(
        "SELECT page_no, text FROM document_page WHERE document_id = ?", (document_id,))}
    return [rows.get(p, "") for p in range(1, max(rows, default=0) + 1)]


def check_quote(conn: sqlite3.Connection, document_id: str | None, quote: str | None, page: int | None) -> tuple[str, int | None]:
    """(quote_check, page): exact / fuzzy / not_found, with the page where it was found; 'none' without a quote."""
    if not quote:
        return "none", None
    pages = _pages(conn, document_id)
    hit = find_quote(pages, quote, page) if pages else None
    if not hit:
        return "not_found", page
    return ("exact" if hit["exact"] else "fuzzy"), hit["page"]


# ------------------------------------------------------------------ reading
def _row(r: sqlite3.Row) -> dict:
    return {k: r[k] for k in r.keys()}


def current_items(conn: sqlite3.Connection, article_id: str) -> list[dict]:
    return [_row(r) for r in conn.execute(
        "SELECT * FROM card_item WHERE article_id = ? AND superseded_by IS NULL ORDER BY field_key, item_ord",
        (article_id,))]


def current_outcome_rows(conn: sqlite3.Connection, article_id: str) -> list[dict]:
    return [_row(r) for r in conn.execute(
        "SELECT * FROM card_outcome_row WHERE article_id = ? AND superseded_by IS NULL "
        "ORDER BY outcome, group_label, timepoint", (article_id,))]


def history(conn: sqlite3.Connection, article_id: str, field_key: str, item_ord: int = 1) -> list[dict]:
    """Every value a field has had, oldest first (the chain of superseded rows)."""
    return [_row(r) for r in conn.execute(
        "SELECT * FROM card_item WHERE article_id = ? AND field_key = ? AND item_ord = ? ORDER BY created_at, rowid",
        (article_id, field_key, item_ord))]


def _current(conn: sqlite3.Connection, article_id: str, field_key: str, item_ord: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM card_item WHERE article_id = ? AND field_key = ? AND item_ord = ? "
                        "AND superseded_by IS NULL", (article_id, field_key, item_ord)).fetchone()


# ------------------------------------------------------------------ writing one value
_ITEM_COLS = ("id", "article_id", "card_version", "field_key", "item_ord", "name", "value", "unit", "group_label",
              "document_id", "jats_snapshot_id", "quote", "page_no", "section_ord", "quote_check", "confidence", "note",
              "state", "actor", "workflow_run_id", "from_card_id", "from_ord")


def _insert_item(conn: sqlite3.Connection, row: dict, replaces: str | None) -> str:
    row = {**row, "id": new_id("card_item"), "card_version": row.get("card_version") or CARD_VERSION,
           "quote_check": row.get("quote_check") or "none", "item_ord": row.get("item_ord") or 1}
    if replaces:
        conn.execute("UPDATE card_item SET superseded_by = ? WHERE id = ?", (row["id"], replaces))
    conn.execute(f"INSERT INTO card_item ({', '.join(_ITEM_COLS)}) VALUES ({', '.join('?' * len(_ITEM_COLS))})",
                 tuple(row.get(c) for c in _ITEM_COLS))
    return row["id"]


def set_item(conn: sqlite3.Connection, article_id: str, field_key: str, *, state: str, item_ord: int = 1,
             value: str | None = None, unit: str | None = None, name: str | None = None, group: str | None = None,
             quote: str | None = None, page: int | None = None, note: str | None = None,
             actor: str = "user") -> dict:
    """The user's decision on one value of the card: confirm it, correct (or add) it, or mark it not reported.

    Returns {"id", "stato", "citazione": exact|fuzzy|not_found|none, "sostituisce": old id or None}."""
    if state not in HUMAN_STATES:
        raise StoreError(f"stato non ammesso per una tua decisione: {state} (confirmed, edited o nr)")
    old = _current(conn, article_id, field_key, item_ord)
    if state == "confirmed":
        if old is None or old["value"] is None:
            raise StoreError("niente da confermare: questo campo non ha un valore (usa Correggi)")
        if old["state"] == "confirmed":
            return {"id": old["id"], "stato": "confirmed", "citazione": old["quote_check"], "sostituisce": None}
        row = {k: old[k] for k in _ITEM_COLS if k not in ("id",)}
        if note is not None:
            row["note"] = note
    elif state == "nr":
        row = {"article_id": article_id, "field_key": field_key, "item_ord": item_ord, "name": name or (old["name"] if old else None),
               "note": note, "document_id": old["document_id"] if old else None}
    else:
        if value is None or not str(value).strip():
            raise StoreError("scrivi il valore corretto (o usa Non riportato)")
        doc = (old["document_id"] if old and old["document_id"] else None) or _document_of(conn, article_id)
        qcheck, found_page = check_quote(conn, doc, quote.strip() if quote else None, page)
        row = {"article_id": article_id, "field_key": field_key, "item_ord": item_ord,
               "name": name if name is not None else (old["name"] if old else None), "value": str(value).strip(),
               "unit": unit if unit is not None else (old["unit"] if old else None),
               "group_label": group if group is not None else (old["group_label"] if old else None),
               "document_id": doc if quote else (old["document_id"] if old else None),
               "quote": quote.strip() if quote else None, "page_no": found_page if quote else None,
               "quote_check": qcheck, "note": note}
        if row["page_no"] is None and row["quote"]:
            raise StoreError("con la frase scrivi anche la pagina (la frase non è stata trovata nel testo)")
        if row["page_no"] is not None and not row["document_id"]:
            raise StoreError("l'articolo non ha un PDF letto: scrivi il valore senza frase e pagina")
    row.update(state=state, actor="human", workflow_run_id=old["workflow_run_id"] if old and state == "confirmed" else None)
    if state != "confirmed":
        row.update(from_card_id=None, from_ord=None, confidence=None, jats_snapshot_id=None, section_ord=None)
    with _write(conn):
        new = _insert_item(conn, row, old["id"] if old else None)
        audit.record(conn, actor=actor, action=f"card_item_{state}", entity_type="card_item", entity_id=new,
                     payload={"article_id": article_id, "field_key": field_key, "item_ord": item_ord,
                              "replaces": old["id"] if old else None, "quote_check": row.get("quote_check", "none")},
                     in_transaction=True)
    return {"id": new, "stato": state, "citazione": row.get("quote_check", "none"),
            "sostituisce": old["id"] if old else None}


# ------------------------------------------------------------------ outcome rows
_ROW_COLS = ("id", "article_id", "outcome", "group_label", "timepoint", "unit") + STATS + (
    "document_id", "jats_snapshot_id", "quote", "page_no", "section_ord", "table_label", "quote_check", "note", "state",
    "actor", "workflow_run_id")


def set_outcome_row(conn: sqlite3.Connection, article_id: str, outcome: str, group: str, timepoint: str, *,
                    state: str = "edited", unit: str | None = None, stats: dict | None = None,
                    quote: str | None = None, page: int | None = None, table_label: str | None = None,
                    note: str | None = None, actor: str = "user") -> dict:
    """The user confirms, corrects or adds one outcome row (numbers copied as written; nothing converted)."""
    if state not in ("confirmed", "edited"):
        raise StoreError("per una riga di esito: confirmed o edited")
    stats = {k: (str(v).strip() if v not in (None, "") else None) for k, v in (stats or {}).items()}
    unknown = set(stats) - set(STATS)
    if unknown:
        raise StoreError(f"statistiche sconosciute: {', '.join(sorted(unknown))}")
    old = conn.execute("SELECT * FROM card_outcome_row WHERE article_id = ? AND outcome = ? AND group_label = ? "
                       "AND timepoint = ? AND superseded_by IS NULL", (article_id, outcome, group, timepoint)).fetchone()
    if state == "confirmed":
        if old is None:
            raise StoreError("niente da confermare: la riga non esiste (usa Correggi)")
        if old["state"] == "confirmed":
            return {"id": old["id"], "stato": "confirmed", "sostituisce": None}
        row = {k: old[k] for k in _ROW_COLS if k != "id"}
    else:
        merged = {k: (old[k] if old else None) for k in STATS}
        merged.update(stats)
        if not any(merged.values()):
            raise StoreError("la riga non ha nessun numero")
        doc = (old["document_id"] if old and old["document_id"] else None) or _document_of(conn, article_id)
        qcheck, found_page = check_quote(conn, doc, quote.strip() if quote else None, page)
        row = {"article_id": article_id, "outcome": outcome, "group_label": group, "timepoint": timepoint,
               "unit": unit if unit is not None else (old["unit"] if old else None), **merged,
               "document_id": doc if quote else None, "quote": quote.strip() if quote else None,
               "page_no": found_page if quote else None, "table_label": table_label, "quote_check": qcheck,
               "note": note}
        if row["quote"] and row["page_no"] is None:
            raise StoreError("con la frase scrivi anche la pagina (la frase non è stata trovata nel testo)")
    row.update(state=state, actor="human", workflow_run_id=old["workflow_run_id"] if old and state == "confirmed" else None)
    with _write(conn):
        new = new_id("card_outcome_row")
        if old:
            conn.execute("UPDATE card_outcome_row SET superseded_by = ? WHERE id = ?", (new, old["id"]))
        row["id"] = new
        row["quote_check"] = row.get("quote_check") or "none"
        conn.execute(f"INSERT INTO card_outcome_row ({', '.join(_ROW_COLS)}) VALUES ({', '.join('?' * len(_ROW_COLS))})",
                     tuple(row.get(c) for c in _ROW_COLS))
        audit.record(conn, actor=actor, action=f"card_outcome_row_{state}", entity_type="card_outcome_row", entity_id=new,
                     payload={"article_id": article_id, "replaces": old["id"] if old else None}, in_transaction=True)
    return {"id": new, "stato": state, "sostituisce": old["id"] if old else None}


# ------------------------------------------------------------------ cards-v1 -> cards-v2
def convert_v1(conn: sqlite3.Connection, *, actor: str = "orpheus scheda-unica") -> dict:
    """Copy the current cards-v1 cards into card_item. Idempotent; never overrides a decision of the user."""
    out = {"schede_v1": 0, "gia_convertite": 0, "convertite": 0, "valori_copiati": 0, "confermati_dal_controllo": 0,
           "segnati_sbagliati": 0, "non_riportati_non_copiati": 0, "tenute_tue_decisioni": 0}
    cards = conn.execute("""SELECT c.*, w.executor FROM article_card c JOIN workflow_run w ON w.id = c.workflow_run_id
                            WHERE c.card_version = 'cards-v1' AND c.status = 'current' ORDER BY c.created_at""").fetchall()
    out["schede_v1"] = len(cards)
    with _write(conn):
        for card in cards:
            if conn.execute("SELECT 1 FROM card_item WHERE from_card_id = ? LIMIT 1", (card["id"],)).fetchone():
                out["gia_convertite"] += 1
                continue
            ords: dict[str, int] = {}
            copied = 0
            for v in conn.execute("SELECT * FROM card_value WHERE card_id = ? ORDER BY ord", (card["id"],)):
                ords[v["field"]] = ords.get(v["field"], 0) + 1
                if v["origin"] == "not_available":
                    out["non_riportati_non_copiati"] += 1
                    continue
                k = ords[v["field"]]
                old = _current(conn, card["article_id"], v["field"], k)
                if old is not None and old["actor"] == "human":
                    out["tenute_tue_decisioni"] += 1
                    continue
                right = v["verified_by_human"] == 1 and v["human_verdict"] == "right"
                wrong = v["verified_by_human"] == 1 and v["human_verdict"] == "wrong"
                note = WRONG_NOTE if wrong else v["note"]
                if v["origin"] == "interpreted":
                    note = "; ".join(x for x in ("interpreted (cards-v1)", note) if x)
                _insert_item(conn, {
                    "article_id": card["article_id"], "field_key": v["field"], "item_ord": k, "name": v["name"],
                    "value": v["value"], "unit": v["unit"], "group_label": v["group_label"],
                    "document_id": card["document_id"] if v["page_no"] is not None else None,
                    "jats_snapshot_id": card["jats_snapshot_id"] if v["section_ord"] is not None else None,
                    "quote": v["quote"], "page_no": v["page_no"], "section_ord": v["section_ord"],
                    "quote_check": "exact" if v["quote"] else "none", "note": note,
                    "state": "confirmed" if right else "ai",
                    "actor": "human" if right else (card["executor"] if card["executor"] in ("claude_session", "chat_manuale")
                                                    else "claude_session"),
                    "workflow_run_id": card["workflow_run_id"], "from_card_id": card["id"], "from_ord": v["ord"]},
                    old["id"] if old else None)
                copied += 1
                out["confermati_dal_controllo"] += right
                out["segnati_sbagliati"] += wrong
            out["convertite"] += 1
            out["valori_copiati"] += copied
        if out["convertite"]:
            audit.record(conn, actor=actor, action="cards_v1_converted", entity_type="card_item",
                         payload={k: v for k, v in out.items()}, in_transaction=True)
    out["nel_database"] = {
        "valori_correnti": conn.execute("SELECT count(*) FROM card_item WHERE superseded_by IS NULL").fetchone()[0],
        "articoli": conn.execute("SELECT count(DISTINCT article_id) FROM card_item").fetchone()[0]}
    return out


# ------------------------------------------------------------------ export (the same card, corrected values marked)
def card(conn: sqlite3.Connection, article_id: str) -> dict:
    a = conn.execute("SELECT id, doi, title FROM article WHERE id = ?", (article_id,)).fetchone()
    if a is None:
        raise StoreError(f"articolo sconosciuto: {article_id}")
    items = current_items(conn, article_id)
    rows = current_outcome_rows(conn, article_id)

    def item(r: dict) -> dict:
        return {"campo": r["field_key"], "ordine": r["item_ord"], "nome": r["name"], "valore": r["value"],
                "unita": r["unit"], "gruppo": r["group_label"], "pagina": r["page_no"], "sezione": r["section_ord"],
                "citazione": r["quote"], "controllo_citazione": r["quote_check"], "stato": r["state"],
                "corretto_a_mano": r["state"] == "edited", "da_controllare": r["state"] == "ai", "nota": r["note"]}

    def outcome(r: dict) -> dict:
        return {"esito": r["outcome"], "gruppo": r["group_label"], "tempo": r["timepoint"], "unita": r["unit"],
                **{k: r[k] for k in STATS}, "pagina": r["page_no"], "tabella": r["table_label"],
                "citazione": r["quote"], "controllo_citazione": r["quote_check"], "stato": r["state"],
                "corretto_a_mano": r["state"] == "edited", "da_controllare": r["state"] == "ai", "nota": r["note"]}

    vals = [item(r) for r in items]
    outs = [outcome(r) for r in rows]
    every = vals + outs
    return {"versione": CARD_VERSION, "articolo": {"id": a["id"], "doi": a["doi"], "titolo": a["title"]},
            "valori": vals, OUTCOME_KEY: outs,
            "riepilogo": {"valori": len(vals), "righe_esito": len(outs),
                          "corretti_a_mano": sum(x["corretto_a_mano"] for x in every),
                          "da_controllare": sum(x["da_controllare"] for x in every),
                          "confermati": sum(x["stato"] == "confirmed" for x in every),
                          "non_riportati": sum(x["stato"] == "nr" for x in every)}}


def export_dir(home: Path) -> Path:
    return home / "rapporti" / "schede_v2"


def export_card(conn: sqlite3.Connection, article_id: str, home: Path) -> dict:
    """Write <article_id>.json and <article_id>.csv in rapporti\\schede_v2 (rewritten with the current values)."""
    data = card(conn, article_id)
    d = export_dir(home)
    d.mkdir(parents=True, exist_ok=True)
    jpath, cpath = d / f"{article_id}.json", d / f"{article_id}.csv"
    jpath.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    cols = ["tipo", "campo", "ordine", "nome", "valore", "unita", "gruppo", "tempo", *STATS, "pagina", "citazione",
            "controllo_citazione", "stato", "corretto_a_mano", "da_controllare", "nota"]
    with cpath.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, cols, delimiter=";", extrasaction="ignore")
        w.writeheader()
        for v in data["valori"]:
            w.writerow({"tipo": "valore", **v, "corretto_a_mano": "sì" if v["corretto_a_mano"] else "",
                        "da_controllare": "sì" if v["da_controllare"] else ""})
        for r in data[OUTCOME_KEY]:
            w.writerow({"tipo": "riga di esito", "campo": r["esito"], **r, "corretto_a_mano": "sì" if r["corretto_a_mano"] else "",
                        "da_controllare": "sì" if r["da_controllare"] else ""})
    return {"json": str(jpath), "csv": str(cpath), **data["riepilogo"]}


def articles_with_card(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT article_id FROM card_item UNION SELECT DISTINCT article_id FROM card_outcome_row ORDER BY 1")]


def confirm_exact(conn: sqlite3.Connection, article_id: str, *, actor: str = "user") -> dict:
    """Confirm in one go every value and outcome row still to check ('ai') whose quote was found exactly in the text.
    The others (fuzzy, not found, without a quote) stay to be checked one by one."""
    items = [r for r in current_items(conn, article_id) if r["state"] == "ai" and r["quote_check"] == "exact"]
    rows = [r for r in current_outcome_rows(conn, article_id) if r["state"] == "ai" and r["quote_check"] == "exact"]
    for r in items:
        set_item(conn, article_id, r["field_key"], state="confirmed", item_ord=r["item_ord"], actor=actor)
    for r in rows:
        set_outcome_row(conn, article_id, r["outcome"], r["group_label"], r["timepoint"], state="confirmed", actor=actor)
    left = sum(r["state"] == "ai" for r in current_items(conn, article_id) + current_outcome_rows(conn, article_id))
    return {"confermati": len(items) + len(rows), "restano_da_controllare": left}


def outcome_history(conn: sqlite3.Connection, article_id: str, outcome: str, group: str, timepoint: str) -> list[dict]:
    return [_row(r) for r in conn.execute(
        "SELECT * FROM card_outcome_row WHERE article_id = ? AND outcome = ? AND group_label = ? AND timepoint = ? "
        "ORDER BY created_at, rowid", (article_id, outcome, group, timepoint))]
