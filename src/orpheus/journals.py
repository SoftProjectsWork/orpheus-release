"""Q5 — journal catalogue from the Crossref answers already saved (read-only on the database).

For every article the most recent *confirmed* DOI check is taken (doi_check.details.crossref: the
Crossref record compared with the PDF). Records are grouped into journals by shared ISSN (two records
sharing any ISSN are the same journal); records without a valid ISSN are grouped by folded title.
For each journal: the title Crossref gives most often (other spellings listed), the ISSNs, the
publishers, the number of articles and the journal folders (document.journal_hint) they came from.

Nothing is written: the `journal` / `journal_issn` tables stay as they are (filling them is a decision
of the user). `in_tabella_journal` only says whether an ISSN of the group is already there.
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter

from .identifiers import is_valid_issn, normalize_issn

NO_FOLDER = "(senza cartella di rivista)"


def sigla_by_issn(journal_list: list[dict] | None) -> dict[str, str]:
    """ISSN -> sigla from riviste_prioritarie.csv (rows as read by layout.read_journal_list)."""
    out: dict[str, str] = {}
    for row in journal_list or []:
        for col in ("issn_print", "issn_online"):
            value = (row.get(col) or "").strip()
            if value and is_valid_issn(value) and row.get("sigla"):
                out.setdefault(normalize_issn(value), row["sigla"])
    return out


def records_by_article(conn: sqlite3.Connection) -> dict[str, dict]:
    """Latest confirmed Crossref record per article: title, ISSNs, publisher, folders."""
    return {r["article_id"]: r for r in _records(conn)}


def _records(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT article_id, doi, details, checked_at FROM (
               SELECT c.*, ROW_NUMBER() OVER (PARTITION BY c.article_id ORDER BY c.checked_at DESC, c.id DESC) AS rn
               FROM doi_check c WHERE c.outcome = 'confirmed' AND c.article_id IS NOT NULL) WHERE rn = 1""").fetchall()
    folders: dict[str, set] = {}
    for aid, hint in conn.execute("SELECT article_id, journal_hint FROM document WHERE article_id IS NOT NULL"):
        folders.setdefault(aid, set()).add(hint or NO_FOLDER)
    out = []
    for aid, doi, details, checked_at in rows:
        try:
            cr = (json.loads(details or "{}") or {}).get("crossref") or {}
        except ValueError:
            cr = {}
        titles = [t.strip() for t in (cr.get("container_title") or []) if isinstance(t, str) and t.strip()]
        issns = sorted({normalize_issn(i) for i in (cr.get("issn") or []) if isinstance(i, str) and is_valid_issn(i)})
        pub = cr.get("publisher")
        out.append({"article_id": aid, "doi": doi, "data": checked_at, "titolo": titles[0] if titles else None,
                    "issn": issns, "editore": pub.strip() if isinstance(pub, str) and pub.strip() else None,
                    "cartelle": folders.get(aid, {NO_FOLDER})})
    return out


def _fold(title: str | None) -> str:
    return " ".join("".join(ch if ch.isalnum() else " " for ch in (title or "").casefold()).split())


def build(conn: sqlite3.Connection, journal_list: list[dict] | None = None) -> dict:
    """The catalogue. Read-only (SELECT only). `journal_list`: rows of riviste_prioritarie.csv (sigla by ISSN)."""
    recs = _records(conn)
    listed = sigla_by_issn(journal_list)
    # union-find over records: same ISSN, or (no ISSN and) same folded title
    parent = list(range(len(recs)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    first_by_key: dict[str, int] = {}
    for i, r in enumerate(recs):
        keys = [f"issn:{x}" for x in r["issn"]] or ([f"titolo:{_fold(r['titolo'])}"] if r["titolo"] else [f"doi:{r['doi']}"])
        for k in keys:
            if k in first_by_key:
                parent[find(i)] = find(first_by_key[k])
            else:
                first_by_key[k] = i
    groups: dict[int, list[dict]] = {}
    for i, r in enumerate(recs):
        groups.setdefault(find(i), []).append(r)
    known = {row[0] for row in conn.execute("SELECT issn FROM journal_issn")}
    journals = []
    for members in groups.values():
        titles = Counter(r["titolo"] for r in members if r["titolo"])
        pubs = Counter(r["editore"] for r in members if r["editore"])
        issns = sorted({x for r in members for x in r["issn"]})
        folders = Counter(f for r in members for f in r["cartelle"])
        main = titles.most_common(1)[0][0] if titles else None
        journals.append({"titolo": main or "(titolo assente in Crossref)",
                         "altri_titoli": sorted(t for t in titles if t != main),
                         "issn": issns, "editori": [p for p, _ in pubs.most_common()],
                         "articoli": len(members), "cartelle": dict(sorted(folders.items())),
                         "sigla_elenco": sorted({listed[x] for x in issns if x in listed}),
                         "in_tabella_journal": bool(known & set(issns))})
    journals.sort(key=lambda j: (-j["articoli"], j["titolo"].casefold()))
    return {"fonte": "doi_check.details.crossref (ultimo controllo confermato per articolo), document.journal_hint",
            "data": max((r["data"] for r in recs), default=None),
            "riepilogo": {"riviste": len(journals), "articoli": len(recs),
                          "senza_issn": sum(1 for j in journals if not j["issn"]),
                          "con_piu_titoli": sum(1 for j in journals if j["altri_titoli"]),
                          "con_piu_editori": sum(1 for j in journals if len(j["editori"]) > 1),
                          "gia_in_tabella_journal": sum(1 for j in journals if j["in_tabella_journal"])},
            "riviste": journals}
