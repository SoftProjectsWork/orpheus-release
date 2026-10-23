"""Q9 — Fase 2, first step: the `journal` / `journal_issn` rows of the journals of riviste_prioritarie.csv, and the
link of the confirmed articles (and their PDFs) to their journal. User's decision of 2026-10-14: "Sì, con la tabella".

Sources, deterministic (rules journals-v1):
  - the user's list riviste_prioritarie.csv: sigla, titolo, issn_print, issn_online, sito_ufficiale;
  - the Crossref answers already saved for the confirmed DOIs (latest confirmed doi_check per article): container
    title, publisher, ISSNs and — from the raw answer kept on disk — their type (issn-type: print / electronic).
A row of the list matches the Crossref journal that shares one of its ISSNs (Crossref records are grouped by shared
ISSN). For each journal:
  - title and publisher: the ones Crossref gives most often ("&amp;" decoded); without Crossref, the list's title;
  - ISSNs: every ISSN of the list and of Crossref. The type is Crossref's when Crossref gives one (fonte "crossref",
    verified_at and source_snapshot_id written); otherwise the list's column (fonte "elenco", not verified). When the
    list gives an ISSN the type that Crossref gives to ANOTHER ISSN of the journal (real case: FAS, columns swapped
    in the list), its type is "unknown" and the problem is reported;
  - homepage: the list's sito_ufficiale without tracking parameters (utm_*);
  - status: 'active' when a confirmed article was issued this year or last year, otherwise 'unknown'.
Not written ("non scritta"), with the reason: a row whose ISSNs belong to more than one Crossref journal, or whose
ISSN is already in journal_issn for another journal. A journal already in the table ("già presente") is not changed,
except (journals-v2, 2026-10-14) its ISSN rows of type "unknown" whose type the plan now knows without conflict
(the user corrected the swapped FAS columns of the list): only 'unknown' becomes print / electronic, never the other
way, verified only when the type comes from Crossref; one audit event per journal.
Links: article.journal_id for the confirmed articles of the journal's Crossref group still without a journal, and
document.journal_id for their PDFs. Never overwritten, nothing deleted. `write` = one transaction, one audit event per
journal written and one for the links; `plan` writes nothing.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from . import audit, crossref
from .db import transaction
from .identifiers import is_valid_issn, normalize_issn
from .ids import new_id
from .verify_doi import read_snapshot

RULES_VERSION = "journals-v2"
_TYPES = {"print": "print", "electronic": "electronic"}


def clean_url(url: str | None) -> str | None:
    """The URL without tracking parameters (utm_*, fbclid, gclid)."""
    if not url or not url.strip():
        return None
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in ("fbclid", "gclid")]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _confirmed(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Latest confirmed check per article (among the latest check of each document)."""
    return conn.execute(
        """SELECT article_id, details, source_snapshot_id, checked_at FROM (
               SELECT c.*, ROW_NUMBER() OVER (PARTITION BY c.article_id ORDER BY c.checked_at DESC, c.id DESC) AS rn
               FROM doi_check c
               WHERE c.outcome = 'confirmed' AND c.article_id IS NOT NULL
                 AND c.rowid = (SELECT c2.rowid FROM doi_check c2 WHERE c2.document_id = c.document_id
                                ORDER BY c2.checked_at DESC, c2.rowid DESC LIMIT 1)) WHERE rn = 1""").fetchall()


def crossref_groups(conn: sqlite3.Connection, home: Path) -> list[dict]:
    """Confirmed articles grouped into journals by shared ISSN, with titles, publishers, years and ISSN types."""
    recs = []
    for r in _confirmed(conn):
        try:
            cr = (json.loads(r["details"] or "{}") or {}).get("crossref") or {}
        except ValueError:
            cr = {}
        issns = {normalize_issn(i) for i in cr.get("issn") or [] if isinstance(i, str) and is_valid_issn(i)}
        types: dict[str, str] = {}
        try:                                            # the raw answer has issn-type; the details only the ISSNs
            status, body = read_snapshot(conn, r["source_snapshot_id"], home)
            msg = json.loads(body.decode("utf-8")).get("message") or {}
            for t in msg.get("issn-type") or []:
                if isinstance(t, dict) and is_valid_issn(t.get("value") or "") and t.get("type") in _TYPES:
                    types[normalize_issn(t["value"])] = _TYPES[t["type"]]
                    issns.add(normalize_issn(t["value"]))
        except (FileNotFoundError, OSError, ValueError, UnicodeDecodeError):
            pass
        if not issns:
            continue
        titles = [crossref.strip_markup(t) for t in cr.get("container_title") or [] if isinstance(t, str) and t.strip()]
        pub = cr.get("publisher")
        recs.append({"article_id": r["article_id"], "issn": issns, "types": types, "snapshot": r["source_snapshot_id"],
                     "title": titles[0] if titles else None,
                     "publisher": crossref.strip_markup(pub) if isinstance(pub, str) and pub.strip() else None,
                     "years": [y for y in cr.get("years") or [] if isinstance(y, int)]})
    groups: list[dict] = []
    for rec in recs:                                    # merge every group sharing an ISSN with this record
        hits = [g for g in groups if g["issn"] & rec["issn"]]
        g = {"issn": set(rec["issn"]), "types": {}, "snapshot": {}, "titles": Counter(), "publishers": Counter(),
             "years": set(), "articles": []}
        for h in hits:
            groups.remove(h)
            g["issn"] |= h["issn"]
            for k, c in h["types"].items():
                g["types"].setdefault(k, Counter()).update(c)
            g["snapshot"].update(h["snapshot"])
            g["titles"].update(h["titles"])
            g["publishers"].update(h["publishers"])
            g["years"] |= h["years"]
            g["articles"] += h["articles"]
        for k, t in rec["types"].items():
            g["types"].setdefault(k, Counter())[t] += 1
            g["snapshot"].setdefault(k, rec["snapshot"])
        if rec["title"]:
            g["titles"][rec["title"]] += 1
        if rec["publisher"]:
            g["publishers"][rec["publisher"]] += 1
        g["years"] |= set(rec["years"])
        g["articles"].append(rec["article_id"])
        groups.append(g)
    return groups


def plan(conn: sqlite3.Connection, home: Path, journal_list: list[dict], *, year: int | None = None) -> dict:
    """What `write` would do, row by row of the list. Read-only."""
    year = year or datetime.now(timezone.utc).year
    groups = crossref_groups(conn, home)
    known = {r["issn"]: r["journal_id"] for r in conn.execute("SELECT issn, journal_id FROM journal_issn")}
    unknown_type = {r["issn"] for r in conn.execute("SELECT issn FROM journal_issn WHERE issn_type = 'unknown'")}
    linked = {r[0] for r in conn.execute("SELECT id FROM article WHERE journal_id IS NOT NULL")}
    rows, matched_groups = [], []
    for row in journal_list:
        listed: dict[str, str] = {}
        problems: list[str] = []
        for col, typ in (("issn_print", "print"), ("issn_online", "electronic")):
            value = (row.get(col) or "").strip()
            if value and is_valid_issn(value):
                listed.setdefault(normalize_issn(value), typ)
            elif value:
                problems.append(f"{col} {value!r} non valido: non usato")
        hits = [g for g in groups if g["issn"] & set(listed)]
        item = {"sigla": row.get("sigla"), "titolo_elenco": row.get("titolo"), "problemi": problems, "note": []}
        if len(hits) > 1:
            problems.append("gli ISSN dell'elenco sono di più riviste diverse in Crossref: controlla la riga")
            rows.append({**item, "azione": "non scritta", "issn": [], "articoli": 0, "_group": None})
            continue
        g = hits[0] if hits else None
        if g is not None:
            matched_groups.append(id(g))
        issns = []
        cr_types = {i: c.most_common(1)[0][0] for i, c in (g["types"].items() if g else [])}
        for issn in sorted((g["issn"] if g else set()) | set(listed)):
            if issn in cr_types:
                issns.append({"issn": issn, "tipo": cr_types[issn], "fonte": "crossref"})
                if issn in listed and listed[issn] != cr_types[issn]:
                    problems.append(f"ISSN {issn}: nell'elenco è {listed[issn]}, Crossref dice {cr_types[issn]}")
            elif issn in listed:
                tipo = listed[issn]
                if tipo in cr_types.values():
                    tipo = "unknown"
                    problems.append(f"ISSN {issn}: nell'elenco è {listed[issn]}, ma Crossref dà quel tipo a un altro "
                                    "ISSN della rivista (colonne issn_print/issn_online scambiate?): tipo sconosciuto")
                issns.append({"issn": issn, "tipo": tipo, "fonte": "elenco"})
            else:
                issns.append({"issn": issn, "tipo": "unknown", "fonte": "crossref"})
                item["note"].append(f"ISSN {issn} di Crossref non presente nell'elenco")
        in_table = {known[i["issn"]] for i in issns if i["issn"] in known}
        if len(in_table) > 1:
            problems.append("gli ISSN sono già in journal_issn per più riviste diverse")
            action = "non scritta"
        else:
            action = "già presente" if in_table else "nuova"
        title = g["titles"].most_common(1)[0][0] if g and g["titles"] else (row.get("titolo") or "").strip()
        if not title:
            problems.append("titolo assente nell'elenco e in Crossref")
            action = "non scritta"
        if row.get("titolo") and title and _fold(row["titolo"]) != _fold(title):
            item["note"].append(f"titolo nell'elenco «{row['titolo']}», in Crossref «{title}» (si usa Crossref)")
        jid = next(iter(in_table), None)
        complete = [i for i in issns if action == "già presente" and i["issn"] in unknown_type
                    and known.get(i["issn"]) == jid and i["tipo"] in _TYPES]        # journals-v2
        rows.append({**item, "azione": action, "journal_id": jid, "tipi_da_completare": complete,
                     "titolo": title, "editore": g["publishers"].most_common(1)[0][0] if g and g["publishers"] else None,
                     "sito": clean_url(row.get("sito_ufficiale")),
                     "stato": "active" if g and g["years"] and max(g["years"]) >= year - 1 else "unknown",
                     "issn": issns, "articoli": len(g["articles"]) if g else 0,
                     "da_collegare": sorted(a for a in (g["articles"] if g else []) if a not in linked),
                     "_group": g})
    outside = [g for g in groups if id(g) not in matched_groups]
    return {"regole": RULES_VERSION, "riviste": rows,
            "articoli_da_collegare": sum(len(r.get("da_collegare", [])) for r in rows if r["azione"] != "non scritta"),
            "articoli_senza_rivista_in_elenco": sum(len(g["articles"]) for g in outside)}


def _fold(text: str) -> str:
    return " ".join("".join(ch if ch.isalnum() else " " for ch in crossref.strip_markup(text).casefold()).split())


def write(conn: sqlite3.Connection, the_plan: dict, *, actor: str = "orpheus scrivi-riviste") -> dict:
    """Apply a plan: one transaction. Returns how many journals, articles and documents were written/linked."""
    now = audit.utc_now()
    written, linked_articles, linked_docs, links, completed = 0, 0, 0, [], 0
    with transaction(conn):
        for r in the_plan["riviste"]:
            if r["azione"] == "non scritta":
                continue
            jid = r.get("journal_id")
            if r["azione"] == "nuova":
                jid = new_id("journal")
                conn.execute("INSERT INTO journal (id, title, publisher, homepage_url, status) VALUES (?, ?, ?, ?, ?)",
                             (jid, r["titolo"], r["editore"], r["sito"], r["stato"]))
                g = r["_group"] or {"snapshot": {}}
                for i in r["issn"]:
                    verified = i["fonte"] == "crossref" and i["tipo"] != "unknown"
                    conn.execute("""INSERT INTO journal_issn (issn, journal_id, issn_type, source_snapshot_id, verified_at)
                                    VALUES (?, ?, ?, ?, ?)""",
                                 (i["issn"], jid, i["tipo"], g["snapshot"].get(i["issn"]) if verified else None,
                                  now if verified else None))
                audit.record(conn, actor=actor, action="write_journal", entity_type="journal", entity_id=jid,
                             payload={"regole": RULES_VERSION, "sigla": r["sigla"], "titolo": r["titolo"],
                                      "titolo_elenco": r["titolo_elenco"], "editore": r["editore"], "sito": r["sito"],
                                      "stato": r["stato"], "issn": r["issn"], "problemi": r["problemi"],
                                      "note": r["note"]}, in_transaction=True)
                written += 1
            done = []
            for i in r.get("tipi_da_completare") or []:                       # journals-v2: 'unknown' only
                verified = i["fonte"] == "crossref"
                g = r["_group"] or {"snapshot": {}}
                n = conn.execute("""UPDATE journal_issn SET issn_type = ?, source_snapshot_id = ?, verified_at = ?
                                    WHERE issn = ? AND journal_id = ? AND issn_type = 'unknown'""",
                                 (i["tipo"], g["snapshot"].get(i["issn"]) if verified else None, now if verified else None,
                                  i["issn"], jid)).rowcount
                if n:
                    done.append(i)
            if done:
                audit.record(conn, actor=actor, action="complete_journal_issn_types", entity_type="journal",
                             entity_id=jid, payload={"regole": RULES_VERSION, "sigla": r["sigla"], "issn": done},
                             in_transaction=True)
                completed += len(done)
            todo = r.get("da_collegare") or []
            if todo:
                marks = ",".join("?" * len(todo))
                n = conn.execute(f"""UPDATE article SET journal_id = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
                                     WHERE journal_id IS NULL AND id IN ({marks})""", (jid, *todo)).rowcount
                d = conn.execute(f"""UPDATE document SET journal_id = ? WHERE journal_id IS NULL
                                     AND article_id IN ({marks})""", (jid, *todo)).rowcount
                linked_articles += n
                linked_docs += d
                links.append({"sigla": r["sigla"], "journal_id": jid, "articoli": n, "documenti": d})
        if links:
            audit.record(conn, actor=actor, action="link_articles_to_journals",
                         payload={"regole": RULES_VERSION, "collegamenti": links}, in_transaction=True)
    return {"riviste_scritte": written, "articoli_collegati": linked_articles, "documenti_collegati": linked_docs,
            "tipi_issn_completati": completed}


def public(the_plan: dict) -> list[dict]:
    """The plan rows without internal fields and without the full list of article ids (for JSON output)."""
    out = []
    for r in the_plan["riviste"]:
        row = {k: v for k, v in r.items() if not k.startswith("_") and k != "da_collegare"}
        row["da_collegare"] = len(r.get("da_collegare") or [])
        out.append(row)
    return out
