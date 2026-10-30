"""Reference lists of the complete articles, from the Crossref answers already stored (T-014 parte 1).

READ-ONLY and WITHOUT NETWORK: nothing is written in the database. For every complete article (access_level
'full_text') whose DOI Crossref confirmed, the newest confirmed Crossref answer is read from the archive and its
deposited reference list (`reference`) is summarised:

* how many references the publisher declares (`reference-count`) and how many are deposited and open;
* how many carry a DOI (and who asserted it: Crossref matching or the publisher), how many are text only;
* which cited DOIs are already articles of ORPHEUS: the citation network inside the corpus.

This is the base of T-014 (ADR-0004): a reference becomes `verified` only after a deterministic check against
Crossref/PubMed (title, first author, year), which is the next part and needs the network on the PC. Here nothing
is verified and no `citation` row is written. Rules ``refs-v1``.
"""
from __future__ import annotations

import csv
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import crossref
from .identifiers import IdentifierError, normalize_doi
from .verify_doi import read_snapshot

RULES_VERSION = "refs-v1"
CSV_FIELDS = ["articolo_doi", "posizione", "chiave", "doi_citato", "doi_indicato_da", "nel_corpus", "anno",
              "primo_autore", "titolo", "rivista", "testo"]


def _doi(value) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return normalize_doi(value)
    except IdentifierError:
        return None


def _year(value) -> int | None:
    m = re.match(r"\s*(\d{4})", str(value or ""))
    return int(m.group(1)) if m and 1500 <= int(m.group(1)) <= 2100 else None


def crossref_references(work: dict) -> dict:
    """{'declared': int|None, 'items': [...]} from a Crossref work. Each item: position (as deposited), key, doi,
    doi_asserted_by, year, first_author, title, journal, text (unstructured)."""
    declared = work.get("reference-count", work.get("references-count"))
    items = []
    for pos, ref in enumerate(work.get("reference") or [], start=1):
        if not isinstance(ref, dict):
            continue
        items.append({
            "position": pos, "key": str(ref.get("key") or ""),
            "doi": _doi(ref.get("DOI")), "doi_asserted_by": ref.get("doi-asserted-by") or "",
            "year": _year(ref.get("year")),
            "first_author": crossref.strip_markup(str(ref.get("author") or "")),
            "title": crossref.strip_markup(str(ref.get("article-title") or ref.get("volume-title") or "")),
            "journal": crossref.strip_markup(str(ref.get("journal-title") or ref.get("series-title") or "")),
            "text": crossref.strip_markup(str(ref.get("unstructured") or "")),
        })
    return {"declared": int(declared) if isinstance(declared, int) or str(declared or "").isdigit() else None,
            "items": items}


def confirmed_works(conn: sqlite3.Connection, home: Path) -> tuple[dict[str, dict], list[dict]]:
    """{article_id: {'doi', 'work'}} for the complete articles, from the newest confirmed Crossref answer."""
    rows = conn.execute(
        """SELECT dc.article_id, dc.source_snapshot_id, a.doi FROM doi_check dc JOIN article a ON a.id = dc.article_id
           WHERE dc.outcome = 'confirmed' AND a.access_level = 'full_text' AND dc.checked_at = (
               SELECT max(d2.checked_at) FROM doi_check d2 WHERE d2.article_id = dc.article_id AND d2.outcome = 'confirmed')
           ORDER BY a.doi""").fetchall()
    works, problems = {}, []
    for r in rows:
        if r["article_id"] in works:
            continue
        try:
            _, body = read_snapshot(conn, r["source_snapshot_id"], home)
            works[r["article_id"]] = {"doi": r["doi"], "work": crossref.parse_work(body)}
        except (OSError, ValueError, KeyError) as exc:
            problems.append({"articolo": r["doi"], "problema": str(exc)})
    return works, problems


def build(conn: sqlite3.Connection, home: Path) -> dict:
    works, problems = confirmed_works(conn, home)
    corpus = {r["doi"]: r["id"] for r in conn.execute("SELECT id, doi FROM article WHERE doi IS NOT NULL")}
    rows: list[dict] = []
    per_article: list[dict] = []
    cited_in_corpus: dict[str, int] = {}
    unique_dois: set[str] = set()
    edges: set[tuple[str, str]] = set()
    for art_id, w in sorted(works.items(), key=lambda kv: kv[1]["doi"]):
        refs = crossref_references(w["work"])
        with_doi = [i for i in refs["items"] if i["doi"]]
        inside = [i for i in with_doi if i["doi"] in corpus and i["doi"] != w["doi"]]
        per_article.append({"doi": w["doi"], "dichiarati": refs["declared"], "depositati": len(refs["items"]),
                            "con_doi": len(with_doi), "nel_corpus": len(inside)})
        for i in refs["items"]:
            in_corpus = bool(i["doi"] and i["doi"] in corpus and i["doi"] != w["doi"])
            rows.append({"articolo_doi": w["doi"], "posizione": i["position"], "chiave": i["key"],
                         "doi_citato": i["doi"] or "", "doi_indicato_da": i["doi_asserted_by"],
                         "nel_corpus": "S" if in_corpus else "N", "anno": i["year"] or "",
                         "primo_autore": i["first_author"], "titolo": i["title"], "rivista": i["journal"],
                         "testo": i["text"][:500]})
            if i["doi"]:
                unique_dois.add(i["doi"])
            if in_corpus and (w["doi"], i["doi"]) not in edges:
                edges.add((w["doi"], i["doi"]))
                cited_in_corpus[i["doi"]] = cited_in_corpus.get(i["doi"], 0) + 1
    deposited = sum(a["depositati"] for a in per_article)
    with_doi_n = sum(a["con_doi"] for a in per_article)
    asserted: dict[str, int] = {}
    for r in rows:
        if r["doi_citato"]:
            k = r["doi_indicato_da"] or "non indicato"
            asserted[k] = asserted.get(k, 0) + 1
    top = sorted(cited_in_corpus.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
    return {
        "regole": RULES_VERSION,
        "riepilogo": {
            "articoli_completi_letti": len(works),
            "articoli_con_elenco": sum(1 for a in per_article if a["depositati"]),
            "articoli_senza_elenco_ma_dichiarati": sum(1 for a in per_article if not a["depositati"] and (a["dichiarati"] or 0) > 0),
            "riferimenti_dichiarati": sum(a["dichiarati"] or 0 for a in per_article),
            "riferimenti_depositati": deposited, "con_doi": with_doi_n, "solo_testo": deposited - with_doi_n,
            "doi_citati_diversi": len(unique_dois),
            "doi_citati_gia_in_orpheus": len({d for d in unique_dois if d in corpus}),
            "citazioni_dentro_il_corpus": len(edges),
            "richieste_crossref_per_verificarli": len(unique_dois - set(corpus)),
        },
        "doi_indicato_da": dict(sorted(asserted.items())),
        "piu_citati_nel_corpus": [{"doi": d, "citato_da": n} for d, n in top],
        "articoli": per_article, "righe": rows, "problemi": problems,
    }


def write_csv(report: dict, home: Path, now: datetime | None = None) -> Path:
    now = now or datetime.now(timezone.utc)
    path = Path(home) / "rapporti" / f"riferimenti_{now.strftime('%Y-%m-%d_%H%M%S')}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_FIELDS, delimiter=";")
        w.writeheader()
        w.writerows(report["righe"])
    return path


def public(report: dict, path: Path) -> dict:
    no_list = [a["doi"] for a in report["articoli"] if not a["depositati"] and (a["dichiarati"] or 0) > 0]
    return {"regole": report["regole"], "riepilogo": report["riepilogo"], "doi_indicato_da": report["doi_indicato_da"],
            "piu_citati_nel_corpus": report["piu_citati_nel_corpus"],
            "senza_elenco_su_crossref": no_list[:30], "problemi": report["problemi"][:20], "file_csv": str(path),
            "scritto_nel_database": False,
            "nota": "Nessun riferimento è ancora verificato (ADR-0004): la verifica con Crossref è il passo successivo."}
