"""Quality report on the PDFs already read (T-018) — read-only, deterministic, no network.

For every document it answers the questions a person would otherwise check by hand:
  - is there usable text on every page, or is it a scan (text from OCR) / are pages almost empty?
  - which DOI candidate was chosen, from where, and how many other DOIs were found?
  - is it a conference-abstract page that also contains OTHER abstracts (its text is mixed)?
  - which journal of riviste_prioritarie.csv does it belong to, and on what evidence (ISSN, journal title,
    or the DOI prefix shared with articles already recognised by ISSN/title)?
  - is another document the same work (same DOI candidate) or a related one (same title,
    e.g. a conference abstract and the full article)?
  - has the DOI candidate been verified against Crossref (`verify-dois`), with which outcome and why?

Nothing here changes the database. The CSV goes to ORPHEUS_HOME/rapporti/ (opens in Excel).
"""

from __future__ import annotations

import csv
import json
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pypdf

from .identifiers import is_valid_issn
from .verify_doi import SEARCH_NOTE, status_by_document

REPORTS_DIR = "rapporti"
_ISSN = re.compile(r"(?<![\dA-Za-z-])S?(\d{4}-\d{3}[\dXx])(?![\dXx])")  # also inside a PII: S1440-2440(04)...
_ABSTRACT_HEADER = re.compile(r"\bAbstracts?\b|\bConference\b|\bCongress\b|\bSymposium\b", re.IGNORECASE)
_LOW_TEXT_CHARS = 100          # a page with less text than this is "almost empty"
_SCAN_MIN_PIXELS = 1_000_000   # an image this big with the page's proportions = a scanned page
_SCAN_SHARE = 0.5              # at least half the pages scanned -> the document is a scan

CSV_COLUMNS = ["file", "tipo", "contenuto", "pagine", "testo", "caratteri_per_pagina", "doi_candidato", "origine_doi", "nota_doi",
               "altri_doi", "rivista", "prova_rivista", "abstract_congresso", "scansione_ocr", "pagine_quasi_vuote",
               "parole_spezzate_non_risolte", "stesso_doi_di", "stesso_titolo_di", "verifica_doi", "motivo_verifica",
               "articolo", "da_controllare", "note"]


def _norm_title(title: str | None) -> str | None:
    if not title or re.search(r"10\.\d{4,9}/|\bPII\b", title):
        return None
    key = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
    return key if len(key) >= 15 else None


def _doi_stem(doi: str) -> str:
    """'10.1016/j.jsams.2011.11.147' -> '10.1016/j.jsams.' (same journal series)."""
    m = re.match(r"(10\.\d+/[a-z]+\.[a-z]+\.)", doi)
    return m.group(1) if m else doi.split("/")[0] + "/"


def scanned_pages(pdf_path: Path) -> list[int] | None:
    """Page numbers whose content is one big image with the page's proportions (a scan)."""
    try:
        reader = pypdf.PdfReader(str(pdf_path))
    except Exception:
        return None
    pages = []
    for n, page in enumerate(reader.pages, 1):
        try:
            w, h = float(page.mediabox.width), float(page.mediabox.height)
            xobjects = (page.get("/Resources") or {}).get("/XObject") or {}
            for ref in xobjects.values():
                obj = ref.get_object()
                if obj.get("/Subtype") != "/Image":
                    continue
                iw, ih = int(obj.get("/Width", 0)), int(obj.get("/Height", 0))
                if iw * ih >= _SCAN_MIN_PIXELS and ih and h and abs(iw / ih - w / h) < 0.08:
                    pages.append(n)
                    break
        except Exception:
            continue
    return pages


def _journal_match(page1: str, journals: list[dict]) -> tuple[str | None, str | None]:
    issns = {m.upper() for m in _ISSN.findall(page1) if is_valid_issn(m)}
    flat = re.sub(r"\s+", " ", page1).lower()
    for j in journals:
        listed = {(j.get(k) or "").upper() for k in ("issn_print", "issn_online")} - {""}
        hit = sorted(issns & listed)
        if hit:
            return j["sigla"], f"ISSN {', '.join(hit)} in prima pagina"
    for j in journals:
        title = re.sub(r"\s+", " ", (j.get("titolo") or "")).lower().strip()
        if len(title) >= 8 and title in flat:
            return j["sigla"], "titolo della rivista in prima pagina"
    return None, None


def is_conference_abstract(kind: str, page_count: int, page1: str, dois, doi_candidate: str | None) -> bool:
    """A conference-abstract page (1-3 pages) that also holds OTHER abstracts: at least two DOIs of the same
    journal on the first two pages, or an "Abstracts/Conference/Congress/Symposium" heading and two DOIs.
    `dois`: rows with page_no and doi (document_doi). Also used by the benchmark sample (benchmark.py)."""
    stem_same = 0
    if doi_candidate:
        stem = _doi_stem(doi_candidate)
        stem_same = len({x["doi"] for x in dois if x["page_no"] <= 2 and x["doi"].startswith(stem)})
    return kind == "article" and page_count <= 3 and (
        stem_same >= 2 or (bool(_ABSTRACT_HEADER.search(page1[:300])) and len({x["doi"] for x in dois}) >= 2))


def build(conn: sqlite3.Connection, home: Path, journals: list[dict]) -> dict:
    docs = conn.execute(
        """SELECT d.id, d.kind, d.original_path, d.page_count, d.text_status, d.pdf_title, d.doi_candidate,
                  d.doi_candidate_source, d.journal_hint, s.storage_path
           FROM document d JOIN source_snapshot s ON s.id = d.source_snapshot_id
           ORDER BY d.original_path""").fetchall()
    verified = status_by_document(conn)
    events = {}
    for entity_id, action, payload in conn.execute(
            "SELECT entity_id, action, payload FROM audit_event WHERE action IN ('ingest_pdf', 'refresh_doi_candidate') "
            "ORDER BY seq"):
        if action == "ingest_pdf":
            events[entity_id] = json.loads(payload)
        else:                                       # DOI read again from the stored text (verify_doi.refresh_candidates)
            events.setdefault(entity_id, {})["doi_candidate_note"] = json.loads(payload).get("doi_candidate_note")

    rows, by_doi, by_title = [], defaultdict(list), defaultdict(list)
    for d in docs:
        pages = conn.execute("SELECT page_no, text, char_count FROM document_page WHERE document_id = ? ORDER BY page_no",
                             (d["id"],)).fetchall()
        dois = conn.execute("SELECT page_no, doi FROM document_doi WHERE document_id = ?", (d["id"],)).fetchall()
        page1 = pages[0]["text"] if pages else ""
        ev = events.get(d["id"], {})
        name = d["original_path"]
        chars = [p["char_count"] for p in pages]
        low = [p["page_no"] for p in pages if p["char_count"] < _LOW_TEXT_CHARS]

        abstract_page = is_conference_abstract(d["kind"], d["page_count"], page1, dois, d["doi_candidate"])

        scan = None
        if d["storage_path"]:
            archived = Path(d["storage_path"])
            archived = archived if archived.is_absolute() else home / archived
            if archived.exists():
                sp = scanned_pages(archived)
                if sp is not None and d["page_count"] and len(sp) / d["page_count"] >= _SCAN_SHARE:
                    scan = sp

        sigla, evidence = _journal_match(page1 + "\n" + (pages[1]["text"][:600] if len(pages) > 1 else "")
                                         + "\n" + (d["pdf_title"] or ""), journals)
        if not sigla and d["journal_hint"]:
            sigla, evidence = d["journal_hint"], "cartella (da confermare)"

        unresolved = (ev.get("hyphenation") or {}).get("unresolved")
        checks = []
        if d["text_status"] == "no_text":
            checks.append("nessun testo: il PDF è fatto solo di immagini. Se viene da una pagina web, salvalo di nuovo "
                          "dal browser con «Salva come PDF» (non «Microsoft Print to PDF»); altrimenti serve l'OCR")
        elif d["text_status"] == "partial" or (low and d["page_count"] > 1):
            checks.append(f"pagine quasi vuote: {', '.join(map(str, low))}")
        if scan:
            checks.append("scansione: testo da OCR, possibili errori di lettura")
        check = verified.get(d["id"])
        # a DOI the PDF does not print, found with the Crossref search (doi_search.py, 2026-10-14)
        searched = bool(d["kind"] == "article" and not d["doi_candidate"] and check and check["ricerca"])
        doi, doi_source = (check["doi"], "crossref_search") if searched else (d["doi_candidate"], d["doi_candidate_source"])
        if d["kind"] == "article" and not doi:
            checks.append("nessun DOI scelto" + (" (più DOI in prima pagina)" if d["doi_candidate_source"] == "ambiguous" else ""))
        note = SEARCH_NOTE if searched else (ev.get("doi_candidate_note") or "")
        if check and check["doi"] != doi:
            check = None                                  # verification of an older DOI candidate
        if d["kind"] == "article" and doi:
            if check is None:
                state, why = "da verificare", ""
            else:
                state, why = check["esito"], check["motivo"]
                if check["outcome"] != "confirmed":
                    checks.append(f"DOI {state} in Crossref: {why}")
        else:
            state, why = "", ""
        if "da verificare" in note and not (check and check["outcome"] == "confirmed"):
            checks.append(f"DOI {note}")
        notes = []
        if abstract_page:
            notes.append("abstract di congresso: la pagina contiene anche altri abstract (i risultati di ricerca "
                         "su questa pagina possono venire dagli abstract vicini)")

        row = {
            "id": d["id"], "file": name, "tipo": "linee guida" if d["kind"] == "author_guidelines" else "articolo",
            "pagine": d["page_count"], "testo": d["text_status"],
            "caratteri_per_pagina": round(sum(chars) / len(chars)) if chars else 0,
            "doi_candidato": doi or "", "origine_doi": doi_source,
            "nota_doi": note,
            "altri_doi": len({x["doi"] for x in dois} - {doi}),
            "rivista": sigla or "", "prova_rivista": evidence or "",
            "abstract_congresso": "sì" if abstract_page else "", "scansione_ocr": "sì" if scan else "",
            "pagine_quasi_vuote": ", ".join(map(str, low)) if d["page_count"] > 1 else "",
            "parole_spezzate_non_risolte": unresolved if unresolved is not None else "",
            "stesso_doi_di": "", "stesso_titolo_di": "", "verifica_doi": state, "motivo_verifica": why,
            "articolo": (check or {}).get("article_id") or "", "_checks": checks, "_notes": notes,
        }
        rows.append(row)
        if doi:
            by_doi[doi].append(row)
        key = _norm_title(d["pdf_title"])
        if key:
            by_title[key].append(row)

    # Journal from the DOI prefix: an article without ISSN or journal title (e.g. a recent conference
    # abstract) whose DOI starts like those of articles recognised by ISSN/title in the same database.
    stems = defaultdict(set)
    for r in rows:
        if r["rivista"] and r["doi_candidato"] and r["prova_rivista"] != "cartella (da confermare)":
            stems[_doi_stem(r["doi_candidato"])].add(r["rivista"])
    for r in rows:
        if r["tipo"] == "articolo" and not r["rivista"] and r["doi_candidato"]:
            stem = _doi_stem(r["doi_candidato"])
            if len(stems.get(stem, ())) == 1 and stem.count(".") >= 3:
                r["rivista"] = next(iter(stems[stem]))
                r["prova_rivista"] = f"prefisso DOI {stem} come altri articoli {r['rivista']} (da confermare)"
        if r["tipo"] == "articolo" and not r["rivista"]:
            r["_checks"].append("rivista non riconosciuta")

    for group in by_doi.values():
        if len(group) > 1:
            for r in group:
                r["stesso_doi_di"] = "; ".join(o["file"] for o in group if o is not r)
                r["_checks"].append("stesso DOI di un altro file: probabile doppione")
    # Same title + different DOI: typically a conference abstract and the full article. Groups made only of
    # abstract pages are skipped: their PDF title is often the title of a NEIGHBOURING abstract.
    def related(group):
        return (len(group) > 1 and len({r["doi_candidato"] for r in group}) > 1
                and any(not r["abstract_congresso"] for r in group))
    for group in by_title.values():
        if related(group):
            for r in group:
                r["stesso_titolo_di"] = "; ".join(o["file"] for o in group if o is not r)
                r["_checks"].append("stesso titolo di un altro file con DOI diverso (es. abstract e articolo completo)")
    from .content import classify_all          # here: content.py imports this module
    classes = classify_all(conn)
    for r in rows:
        r["contenuto"] = (classes.get(r["id"]) or {}).get("classe") or ""
        r["da_controllare"] = "; ".join(r["_checks"])
        r["note"] = "; ".join(r["_notes"])

    articles = [r for r in rows if r["tipo"] == "articolo"]
    summary = {
        "documenti": len(rows), "articoli": len(articles), "linee_guida": len(rows) - len(articles),
        "pagine": sum(r["pagine"] for r in rows),
        "testo": dict(Counter(r["testo"] for r in rows)),
        "doi": dict(Counter(r["origine_doi"] for r in articles)),
        "senza_doi": sum(1 for r in articles if not r["doi_candidato"]),
        "abstract_di_congresso": sum(1 for r in rows if r["abstract_congresso"]),
        "contenuto": dict(Counter(r["contenuto"] for r in articles)),
        "scansioni_ocr": sum(1 for r in rows if r["scansione_ocr"]),
        "riviste": dict(Counter(r["rivista"] or "non riconosciuta" for r in articles)),
        "possibili_doppioni": sum(1 for g in by_doi.values() if len(g) > 1),
        "stesso_titolo": sum(1 for g in by_title.values() if related(g)),
        "parole_spezzate_non_risolte": sum(r["parole_spezzate_non_risolte"] or 0 for r in rows),
        "verifica_doi": dict(Counter(r["verifica_doi"] for r in articles if r["verifica_doi"])),
        "da_controllare": sum(1 for r in rows if r["_checks"]),
    }
    return {"riepilogo": summary, "documenti": rows}


def write_csv(report: dict, home: Path, now: datetime | None = None) -> Path:
    now = now or datetime.now(timezone.utc)
    folder = home / REPORTS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"qualita_{now.strftime('%Y-%m-%d_%H%M%S')}.csv"
    tmp = path.with_suffix(".tmp")
    # Semicolon + UTF-8 BOM + CRLF: what Excel in Italian opens correctly with a double click.
    with tmp.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, delimiter=";", extrasaction="ignore", lineterminator="\r\n")
        writer.writeheader()
        for r in report["documenti"]:
            writer.writerow(r)
    tmp.replace(path)
    return path


def problems(report: dict) -> list[dict]:
    """Only the documents that need a look, one line each (for the console and the log)."""
    return [{"file": r["file"], "da_controllare": r["da_controllare"]} for r in report["documenti"] if r["_checks"]]


def public(report: dict) -> dict:
    """The report without internal fields (for JSON output)."""
    return {"riepilogo": report["riepilogo"],
            "documenti": [{k: v for k, v in r.items() if not k.startswith("_")} for r in report["documenti"]]}
