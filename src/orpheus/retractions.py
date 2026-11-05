"""An article retracted by its journal leaves what ORPHEUS shows: searches, journal profiles, the next lots.

Nothing is deleted (the article, its text and the old values stay readable in the history):
- `article.integrity_status` becomes 'retracted';
- the current article cards become 'superseded';
- the current values of the unified card are closed with `superseded_by = id` (withdrawn without a replacement, the same
  convention of `review_criterion`);
- an open packet is closed with the reason;
- one audit event says who, why and how many values.

The reading side filters on `integrity_status`: `semantic.by_article`, `journal_profile`, the lots of `card_store` and
`objectives`. A review that already uses the article is only named: what to do with it is the reviewer's decision.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from . import audit, card_store
from .db import transaction

RETRACTED = "retracted"


def _article(conn: sqlite3.Connection, doi: str) -> sqlite3.Row:
    r = conn.execute("SELECT id, doi, title, publication_year, integrity_status FROM article WHERE lower(doi) = ?",
                     (doi.strip().lower(),)).fetchone()
    if r is None:
        raise ValueError(f"DOI non trovato nel database: {doi}")
    return r


def _counts(conn: sqlite3.Connection, aid: str) -> dict:
    n = lambda sql: conn.execute(sql, (aid,)).fetchone()[0]  # noqa: E731
    return {"schede_correnti": n("SELECT count(*) FROM article_card WHERE article_id = ? AND status = 'current'"),
            "valori_scheda": n("SELECT count(*) FROM card_item WHERE article_id = ? AND superseded_by IS NULL"),
            "righe_esiti": n("SELECT count(*) FROM card_outcome_row WHERE article_id = ? AND superseded_by IS NULL")}


def plan(conn: sqlite3.Connection, doi: str) -> dict:
    """What `retract` would do. Read-only."""
    a = _article(conn, doi)
    reviews = [dict(r) for r in conn.execute(
        """SELECT s.id AS studio, s.label AS etichetta, r.id AS revisione FROM review_study s
           JOIN review r ON r.id = s.review_id WHERE s.article_id = ?""", (a["id"],))]
    return {"article_id": a["id"], "doi": a["doi"], "titolo": a["title"], "anno": a["publication_year"],
            "gia_ritirato": a["integrity_status"] == RETRACTED, **_counts(conn, a["id"]), "studi_in_revisioni": reviews}


def retract(conn: sqlite3.Connection, home: Path, doi: str, *, reason: str, actor: str = "orpheus ritira-articolo") -> dict:
    """Idempotent: a second call finds nothing left to close and writes no event."""
    if not (reason or "").strip():
        raise ValueError("serve il motivo")
    out = plan(conn, doi)
    aid = out["article_id"]
    if out["gia_ritirato"] and not (out["schede_correnti"] or out["valori_scheda"] or out["righe_esiti"]):
        return out
    with transaction(conn):
        conn.execute("UPDATE article SET integrity_status = ? WHERE id = ?", (RETRACTED, aid))
        conn.execute("UPDATE article_card SET status = 'superseded' WHERE article_id = ? AND status = 'current'", (aid,))
        conn.execute("UPDATE card_item SET superseded_by = id WHERE article_id = ? AND superseded_by IS NULL", (aid,))
        conn.execute("UPDATE card_outcome_row SET superseded_by = id WHERE article_id = ? AND superseded_by IS NULL", (aid,))
        audit.record(conn, actor=actor, action="article_retracted", entity_type="article", entity_id=aid,
                     payload={"doi": out["doi"], "motivo": reason.strip(), "schede": out["schede_correnti"],
                              "valori": out["valori_scheda"], "righe_esiti": out["righe_esiti"]},
                     in_transaction=True)
    out["pacchetti_chiusi"] = card_store.close_excluded_packets(conn, home, excluded_dois={out["doi"]: reason.strip()},
                                                                actor=actor)
    return out
