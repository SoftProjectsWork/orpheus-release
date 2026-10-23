"""DOIs that the PDF does not print: ask Crossref which record the PDF is, then verify it like any other DOI.

Real case (2026-10-14): the 13 JSCR PDFs (Wolters Kluwer) print no DOI at all; above the abstract they print the
citation "J Strength Cond Res 40(10): 1253–1258, 2026". For every COMPLETE article PDF without a DOI candidate,
in the folder of a journal of riviste_prioritarie.csv (folder sigla -> its ISSNs):

  1. one Crossref search, restricted to the journal's ISSNs:
     /works?query.bibliographic=<the self-citation, or the start of page 1>&filter=issn:A,issn:B; answer kept;
  2. rules search-v2 — a record matches when
       - its title is found in the first pages of the PDF (>= 90%, the measure of verify-dois), AND
       - if the PDF prints a citation "vol(issue): first–last, year": the same volume, issue and first page.
     Exactly one match -> its DOI. None -> "non trovato". More than one -> "ambiguo": no guess.
  3. the DOI found is verified exactly like a DOI read from the PDF (verify_doi.compare, rules of verify-dois): /works/{doi},
     title, authors, year, journal, pages. A doi_check row (with the search in details["ricerca"]) is written, and
     an article only if confirmed. The document's doi_candidate stays empty: the PDF does not print a DOI.
Every search is recorded (answer as source_snapshot, audit event 'search_doi'), also when nothing is found.
A PDF whose search found nothing is searched again on the next run (Crossref may have the record by then).

search-v2 (2026-10-14, Q11): FAS 1997-2002 PDFs print a DOI built from the PII (10.1016/S1268-7731(97)80009-4) that
Crossref does not know (404), and the citation "Foot and Ankle Surgery 1998 4:3-11" (year vol:first-last, no issue).
So the search also takes the complete article PDFs whose latest DOI check is "not_found" for the DOI they print
(never one found by a search), and reads the citation "<year> <vol>:<first>-<last>" too (issue not compared). The
document's doi_candidate stays the printed one; verify_doi.run keeps the check found by the search (found_by_search).

search-v3 (2026-10-14, the 2 FAS still not found with search-v2):
  - the citation may print a double hyphen ("Foot and Ankle Surgery 1997 3:41--48");
  - a citation printed at the very top of page 1 (FAS: "Foot and Ankle Surgery 2000 6:3-9") is sent to Crossref
    together with what follows it (title, authors), not alone: alone, the right record was not among the 10 answers;
  - scanned PDFs may give the title lines in another order ("Ultrasonographic / metatarsal arch / evaluation of the
    transverse"): when the PDF prints a citation and the record has the same volume, issue and first page, a title
    found at >= 80% (the "simile" of verify-dois) also matches if EVERY word of the title (4 words or more) is in one
    window of the first pages as long as the title plus 6 words. The DOI is then verified by verify-dois as usual.
"""

from __future__ import annotations

import re
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from . import audit, content, crossref, verify_doi
from .db import transaction
from .identifiers import is_valid_issn, normalize_issn

RULES_VERSION = "search-v3"
ROWS = 10
QUERY_CHARS = 350
QUERY_TOP = 100           # a citation starting within the first 100 characters is a running head
OUTCOME_IT = {**verify_doi.OUTCOME_IT, "none": "non trovato", "ambiguous": "ambiguo"}
_CITATION = re.compile(r"\b(\d{1,4}) ?\((\d{1,4}[A-Za-z]?)\) ?: ?([eE]?\d{1,6}) ?[–—-]{1,2} ?([eE]?\d{1,6}), ?((?:19|20)\d\d)\b")
_CITATION_YEAR_FIRST = re.compile(r"\b((?:19|20)\d\d) (\d{1,4}) ?: ?([eE]?\d{1,6}) ?[–—-]{1,2} ?([eE]?\d{1,6})\b")
_MIN_WORDS_ANY_ORDER = 4


def _citation_match(text: str):
    """(match, 'vip' | 'yvp') of the first printed citation: "vol(issue): first–last, year", otherwise
    "year vol:first-last" (Foot and Ankle Surgery 1997-2002)."""
    m = _CITATION.search(text)
    if m:
        return m, "vip"
    m = _CITATION_YEAR_FIRST.search(text)
    return (m, "yvp") if m else (None, None)


def printed_citation(text: str) -> dict | None:
    """The first "vol(issue): first–last, year" (or "year vol:first-last", issue None) printed in the text, or None."""
    m, kind = _citation_match(" ".join((text or "").split()))
    if not m:
        return None
    if kind == "yvp":
        return {"volume": m.group(2), "issue": None, "first_page": m.group(3).lower(),
                "last_page": m.group(4).lower(), "year": int(m.group(1))}
    return {"volume": m.group(1), "issue": m.group(2), "first_page": m.group(3).lower(),
            "last_page": m.group(4).lower(), "year": int(m.group(5))}


def first_page(page) -> str | None:
    """'1253-1258' -> '1253'; 'E1039–e1045' -> 'e1039'."""
    if not page:
        return None
    return re.split(r"\s*[–—-]\s*", str(page).strip())[0].lower() or None


def query_text(page1: str) -> str:
    """The text sent to Crossref: the self-citation ending with "vol(issue): pages, year" when the PDF prints one
    (authors, title, journal: a bibliographic reference), otherwise the start of page 1 (title and authors) — also
    when the citation is the running head at the top of page 1 (search-v3)."""
    text = " ".join((page1 or "").split())
    m, _ = _citation_match(text)
    if m is None or m.start() < QUERY_TOP:     # search-v3: a citation at the top is followed by title and authors
        return text[:QUERY_CHARS]
    return text[max(0, m.end() - QUERY_CHARS): m.end()]


def issns_by_sigla(journals: list[dict] | None) -> dict[str, list[str]]:
    """sigla (lower case) -> valid ISSNs from riviste_prioritarie.csv."""
    out: dict[str, list[str]] = {}
    for row in journals or []:
        values = [normalize_issn(v) for v in (row.get("issn_print"), row.get("issn_online")) if v and is_valid_issn(v)]
        if row.get("sigla") and values:
            out[row["sigla"].lower()] = list(dict.fromkeys(values))
    return out


def title_words_in_window(title: str, tokens: list[str]) -> float:
    """search-v3: share of the title's letters whose words are all in ONE window of the text (as many times as the
    title has them), whatever their order; 0 for titles shorter than 4 words. Windows start at a significant word of
    the title and are as long as the title plus 6 words (scanned PDFs: title lines out of order)."""
    words = verify_doi.fold_tokens(title)
    total = sum(len(w) for w in words)
    if len(words) < _MIN_WORDS_ANY_ORDER or not total or not tokens:
        return 0.0
    need = Counter(words)
    anchors = {w for w in words if w not in verify_doi._STOP and len(w) >= 3} or set(words)
    best = 0.0
    for i, tok in enumerate(tokens):
        if tok not in anchors:
            continue
        window = Counter(tokens[i:i + len(words) + 6])
        best = max(best, sum(len(w) * min(c, window[w]) for w, c in need.items()) / total)
        if best >= 0.999:
            break
    return round(best, 3)


def match(items: list[dict], pages: list[str], citation: dict | None) -> tuple[list[str], list[dict]]:
    """(DOIs that pass the rules, what was considered for each record)."""
    tokens = [verify_doi.fold_tokens(p) for p in pages[:3]]
    matches, considered = [], []
    for it in items:
        rec = crossref.summary(it)
        titles = [t for t in dict.fromkeys([rec["title"], f"{rec['title']} {rec['subtitle']}".strip()]) if t]
        score = max((verify_doi.find_title(t, toks)[0] for t in titles for toks in tokens), default=0.0)
        same = None
        if citation is not None:
            same = (str(rec["volume"] or "").strip() == citation["volume"]
                    and (citation["issue"] is None
                         or str(rec["issue"] or "").strip().lower() == citation["issue"].lower())
                    and first_page(rec["page"]) == citation["first_page"])
        any_order = None
        if same and verify_doi._TITLE_SIMILAR <= score < verify_doi._TITLE_STRONG:      # search-v3
            any_order = max((title_words_in_window(t, toks) for t in titles for toks in tokens), default=0.0)
        title_ok = score >= verify_doi._TITLE_STRONG or (any_order is not None and any_order >= 0.999)
        ok = bool(rec["doi"]) and title_ok and (citation is None or same)
        considered.append({"doi": rec["doi"], "titolo": rec["title"][:160], "somiglianza_titolo": round(score, 3),
                           **({"parole_del_titolo_in_altro_ordine": any_order} if any_order is not None else {}),
                           "volume": rec["volume"], "fascicolo": rec["issue"], "pagine": rec["page"],
                           "citazione_uguale": same, "scelto": ok})
        if ok and rec["doi"] not in matches:
            matches.append(rec["doi"])
    return matches, considered


@dataclass
class SearchResult:
    processed: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    not_searchable: list[dict] = field(default_factory=list)
    skipped: int = 0
    stopped: str | None = None
    requests: int = 0


def to_search(conn: sqlite3.Connection, journals: list[dict], mode: str = "nuovi"):
    """(documents to search with their ISSNs, documents that cannot be searched, number already with a DOI check).
    Searched: complete article PDFs without a printed DOI, and those whose printed DOI Crossref does not know."""
    issns = issns_by_sigla(journals)
    classes = content.classify_all(conn)
    latest = verify_doi.latest_checks(conn)
    todo, impossible, skipped = [], [], 0
    for d in conn.execute("SELECT * FROM document WHERE kind = 'article' ORDER BY original_path"):
        if (classes.get(d["id"]) or {}).get("classe") != content.COMPLETE:
            continue
        last = latest.get(d["id"])
        searched = verify_doi.found_by_search(last)
        unknown_printed = (d["doi_candidate"] is not None and last is not None and not searched
                           and last["outcome"] == "not_found" and last["doi"] == d["doi_candidate"])
        if d["doi_candidate"] is not None and not (unknown_printed or searched):
            continue                      # the printed DOI is verified by verify-dois
        if last is not None and not unknown_printed and (mode == "nuovi" or last["outcome"] == "confirmed"):
            skipped += 1
            continue
        sigla = d["journal_hint"]
        if not sigla:
            impossible.append({"file": verify_doi._short(d["original_path"]),
                               "motivo": "PDF fuori dalle cartelle delle riviste: rivista sconosciuta"})
        elif sigla.lower() not in issns:
            impossible.append({"file": verify_doi._short(d["original_path"]),
                               "motivo": f"rivista {sigla} senza ISSN in riviste_prioritarie.csv"})
        else:
            todo.append((d, issns[sigla.lower()]))
    return todo, impossible, skipped


def run(conn: sqlite3.Connection, *, home: Path, archive_dir: Path, client: crossref.CrossrefClient,
        journals: list[dict], mode: str = "nuovi", limit: int | None = None, actor: str = "cli",
        max_consecutive_failures: int = 3) -> SearchResult:
    """mode 'nuovi': PDFs never verified; 'ricontrolla': also those whose DOI found earlier was not confirmed."""
    out = SearchResult()
    todo, out.not_searchable, out.skipped = to_search(conn, journals, mode)
    if limit is not None:
        todo = todo[:limit]
    failures = 0
    for doc, issns in todo:
        pages = [r["text"] for r in conn.execute(
            "SELECT text FROM document_page WHERE document_id = ? ORDER BY page_no", (doc["id"],))]
        page1 = pages[0] if pages else ""
        citation, query = printed_citation(page1), query_text(page1)
        item = {"file": verify_doi._short(doc["original_path"])}
        try:
            if not query.strip():
                raise ValueError("prima pagina senza testo: niente da cercare")
            resp = client.search_works(query, issns, rows=ROWS)
            try:
                items = crossref.parse_work_list(resp.body)
            except (ValueError, UnicodeDecodeError) as exc:
                raise crossref.CrossrefError(f"risposta non leggibile: {exc}") from exc
            matches, considered = match(items, pages, citation)
            outcome = "found" if len(matches) == 1 else ("none" if not matches else "ambiguous")
            with transaction(conn):
                snap_id = verify_doi.store_snapshot(conn, resp, home=home, archive_dir=archive_dir)
                audit.record(conn, actor=actor, action="search_doi", entity_type="document", entity_id=doc["id"],
                             payload={"regole": RULES_VERSION, "esito": outcome, "doi": matches[0] if outcome == "found" else None,
                                      "query": query, "issn": issns, "citazione": citation, "record": considered,
                                      "source_snapshot_id": snap_id}, in_transaction=True)
            if outcome != "found":
                why = ("nessun record di Crossref con titolo" + (", volume, fascicolo e prima pagina" if citation else "")
                       + " uguali al PDF") if outcome == "none" else f"{len(matches)} record passano le regole: nessuna scelta"
                out.processed.append({**item, "esito": OUTCOME_IT[outcome], "motivo": why})
                failures = 0
                continue
            doi = matches[0]
            resp2 = client.get_work(doi)
            ev = verify_doi.load_evidence(conn, doc, doi=doi, note=f"{verify_doi.SEARCH_NOTE} ({RULES_VERSION})")
            work = None
            if resp2.status == 404:
                verdict = verify_doi.not_found(ev)
            else:
                try:
                    work = crossref.parse_work(resp2.body)
                except (ValueError, UnicodeDecodeError) as exc:
                    raise crossref.CrossrefError(f"risposta non leggibile: {exc}") from exc
                verdict = verify_doi.compare(work, ev)
            verdict.details["ricerca"] = {"regole": RULES_VERSION, "source_snapshot_id": snap_id, "query": query,
                                          "citazione": citation}
        except crossref.CrossrefBlocked as exc:
            out.stopped = str(exc)
            out.errors.append({**item, "errore": str(exc)})
            break
        except (crossref.CrossrefError, ValueError) as exc:
            out.errors.append({**item, "errore": str(exc)})
            failures += 1
            if failures >= max_consecutive_failures:
                out.stopped = (f"{failures} errori di rete di fila: ricerca interrotta. Controlla la connessione "
                               "e rilancia.")
                break
            continue
        failures = 0
        saved = verify_doi.record(conn, ev, verdict, work, actor=actor, mode=f"ricerca {mode}", response=resp2,
                                  home=home, archive_dir=archive_dir)
        out.processed.append({**item, "doi": doi, "esito": OUTCOME_IT[verdict.outcome],
                              "motivo": "; ".join(verdict.reasons),
                              **({"articolo": saved["article_id"]} if saved["article_id"] else {}),
                              **({"articolo_creato": True} if saved["article_created"] else {})})
    out.requests = client.requests_made
    if out.errors or out.stopped:
        audit.record(conn, actor=actor, action="search_dois_run",
                     payload={"mode": mode, "processed": len(out.processed), "errors": out.errors,
                              "stopped": out.stopped, "requests": out.requests, "rules": RULES_VERSION})
    return out


def summary(res: SearchResult) -> dict:
    counts = {"confermato": 0, "non_corrisponde": 0, "non_trovato": 0, "ambiguo": 0}
    for p in res.processed:
        counts[p["esito"].replace(" ", "_")] += 1
    return {"cercati": len(res.processed), **counts, "errori": len(res.errors), "gia_con_doi": res.skipped,
            "articoli_creati": sum(1 for p in res.processed if p.get("articolo_creato")),
            "richieste_a_crossref": res.requests}

