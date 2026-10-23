"""T-029 — Fase 2: warnings when the author guidelines change, and what changed (rules policy-diff-v1). Read-only.

User's decision of 2026-10-15: "Scarichi tu il PDF". The user saves the new guidelines as PDF in
inbox/riviste/<SIGLA>/linee_guida; leggi_pdf reads it; `linee-guida` (inside riviste.cmd) writes a new
journal_policy_version only when the text changes (policy-v1: fingerprint without the browser's print lines). Nothing is
ever downloaded, so unchanged pages are never fetched again.

A warning ("avviso") is due for a journal when
  - its current journal card was written on a version that is no longer the latest ("scheda da rifare"), or
  - it has no current card and its latest version replaces an older one and is still to be read (needs_human_review).
For each warning: the two versions (date, file), the sentences removed and added between the two texts (the content text
of policy-v1 split into sentences, so a different line wrapping of the print changes nothing; each sentence with the
page where it starts), and — with a card — the values whose quote is no longer in the new text ("da ricontrollare":
they come from a passage that changed). The new card is written by Claude in a session, as for T-027.
"""

from __future__ import annotations

import difflib
import re
import sqlite3

from . import cards
from .policy_versions import POLICY_TYPE, browser_lines, version_documents

RULES_VERSION = "policy-diff-v1"
MAX_EXAMPLES = 12
_SENTENCE_END = re.compile(r"(?<=[.!?;:])\s+(?=\S)")


def _pages_of_version(conn: sqlite3.Connection, version_id: str) -> tuple[list[str], dict | None]:
    """The pages of all the PDFs of a version, one after the other (policy-v2: more PDFs per version)."""
    docs = version_documents(conn, version_id)
    if not docs:
        return [], None
    pages: list[str] = []
    for d in docs:
        own = [r["text"] or "" for r in conn.execute(
            "SELECT text FROM document_page WHERE document_id = ? ORDER BY page_no", (d["document_id"],))]
        drop, _, _ = browser_lines(own)        # each PDF has its own print lines ("<address> 3/8")
        pages += ["\n".join(ln for ln in p.splitlines() if ln.rstrip() not in drop) for p in own]
    return pages, {"document_id": docs[0]["document_id"], "file": " + ".join(d["file_name"] for d in docs)}


def sentences(pages: list[str]) -> list[tuple[int, str]]:
    """(page, sentence) of the content text: browser print lines left out, whitespace collapsed, split after . ! ? ; :"""
    drop, _, _ = browser_lines(pages)
    out = []
    for no, page in enumerate(pages, 1):
        text = " ".join(ln for ln in (page or "").splitlines() if ln.rstrip() not in drop)
        text = cards.normalise(text)
        out += [(no, s.strip()) for s in _SENTENCE_END.split(text) if s.strip()]
    # a sentence cut by a page break is joined to the next one (it starts on the first page)
    merged: list[tuple[int, str]] = []
    for no, s in out:
        if merged and not re.search(r"[.!?;:]$", merged[-1][1]) and merged[-1][0] != no:
            merged[-1] = (merged[-1][0], merged[-1][1] + " " + s)
        else:
            merged.append((no, s))
    return merged


def compare(old_pages: list[str], new_pages: list[str]) -> dict:
    """Sentences removed and added from the old text to the new one (difflib on the sentences)."""
    a, b = sentences(old_pages), sentences(new_pages)
    sm = difflib.SequenceMatcher(a=[s for _, s in a], b=[s for _, s in b], autojunk=False)
    removed, added = [], []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op in ("delete", "replace"):
            removed += [{"pagina": p, "frase": s} for p, s in a[i1:i2]]
        if op in ("insert", "replace"):
            added += [{"pagina": p, "frase": s} for p, s in b[j1:j2]]
    return {"frasi_prima": len(a), "frasi_dopo": len(b), "tolte": removed, "aggiunte": added,
            "uguali": round(sm.ratio(), 3)}


def day(ts: str | None) -> str | None:
    """'2026-10-14T07:05:00Z' -> '14/10/2026' (a plain string: PowerShell 7 would turn ISO dates into DateTime)."""
    return f"{ts[8:10]}/{ts[5:7]}/{ts[:4]}" if ts and len(ts) >= 10 else None


def _versions(conn: sqlite3.Connection, journal_id: str) -> list[sqlite3.Row]:
    return conn.execute("""SELECT id, detected_at, supersedes_id, needs_human_review FROM journal_policy_version
                           WHERE journal_id = ? AND policy_type = ? ORDER BY id""",
                        (journal_id, POLICY_TYPE)).fetchall()


def values_to_recheck(conn: sqlite3.Connection, card_id: str, new_pages: list[str]) -> list[dict]:
    forms = cards.text_forms("\n".join(new_pages))
    out = []
    for v in conn.execute("""SELECT ord, field, article_type, value, page_no, quote FROM journal_card_value
                             WHERE card_id = ? AND quote IS NOT NULL ORDER BY ord""", (card_id,)):
        if not cards.quote_in_text(v["quote"], forms):
            out.append({"ord": v["ord"], "campo": v["field"], "tipo_articolo": v["article_type"], "valore": v["value"],
                        "pagina_vecchia": v["page_no"], "citazione": v["quote"][:160]})
    return out


def alerts(conn: sqlite3.Connection, sigla_of: dict[str, str] | None = None) -> list[dict]:
    """One warning per journal whose guidelines changed after its card (or that changed and are still to read)."""
    sigla_of = sigla_of or {}
    out = []
    for j in conn.execute("SELECT id, title FROM journal ORDER BY title"):
        vs = _versions(conn, j["id"])
        if not vs:
            continue
        latest = vs[-1]
        card = conn.execute("""SELECT id, policy_version_id, created_at FROM journal_card
                               WHERE journal_id = ? AND status = 'current' ORDER BY created_at DESC LIMIT 1""",
                            (j["id"],)).fetchone()
        if card is not None and card["policy_version_id"] != latest["id"]:
            base_id, kind = card["policy_version_id"], "scheda da rifare"
        elif card is None and latest["supersedes_id"] and latest["needs_human_review"]:
            base_id, kind = latest["supersedes_id"], "linee guida cambiate (nessuna scheda)"
        else:
            continue
        base = next((v for v in vs if v["id"] == base_id), None)
        old_pages, old_doc = _pages_of_version(conn, base_id)
        new_pages, new_doc = _pages_of_version(conn, latest["id"])
        diff = compare(old_pages, new_pages)
        item = {"rivista": sigla_of.get(j["id"]) or j["title"], "titolo": j["title"], "avviso": kind,
                "versione_precedente": {"data": base["detected_at"] if base else None,
                                        "giorno": day(base["detected_at"]) if base else None,
                                        "file": old_doc["file"] if old_doc else None},
                "versione_nuova": {"data": latest["detected_at"], "giorno": day(latest["detected_at"]),
                                   "file": new_doc["file"] if new_doc else None},
                "versioni_dopo_la_scheda": sum(1 for v in vs if base and v["id"] > base["id"]),
                "frasi_tolte": len(diff["tolte"]), "frasi_aggiunte": len(diff["aggiunte"]),
                "testo_uguale": diff["uguali"],
                "esempi": ([{"tipo": "-", **x} for x in diff["tolte"]] + [{"tipo": "+", **x} for x in diff["aggiunte"]])[:MAX_EXAMPLES]}
        if card is not None:
            recheck = values_to_recheck(conn, card["id"], new_pages)
            total = conn.execute("SELECT count(*) FROM journal_card_value WHERE card_id = ?", (card["id"],)).fetchone()[0]
            item |= {"valori_della_scheda": total, "valori_da_ricontrollare": recheck}
        out.append(item)
    return out


def summary_line(a: dict) -> str:
    extra = (f"; valori della scheda da ricontrollare {len(a['valori_da_ricontrollare'])} su {a['valori_della_scheda']}"
             if "valori_da_ricontrollare" in a else "")
    return (f"{a['rivista']}: {a['avviso']} — linee guida del {a['versione_nuova']['giorno']} "
            f"({a['versione_nuova']['file']}) invece di quelle del {a['versione_precedente']['giorno']}: "
            f"frasi tolte {a['frasi_tolte']}, aggiunte {a['frasi_aggiunte']}{extra}")
