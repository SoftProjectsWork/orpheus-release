"""Verify the DOI candidates of the PDFs already read (T-008), deterministically.

For every article PDF with a DOI candidate:
  1. ask Crossref for the record (crossref.py) — or, with `rivaluta`, reuse the stored answer;
  2. keep the raw answer as a source_snapshot (sha256, file under the archive), also for 404;
  3. compare the record with what the PDF itself contains: title, authors, year, journal/ISSN, pages, type;
  4. write one doi_check row (append-only) and an audit event; create or link an `article` only when
     the outcome is 'confirmed'.

Outcomes: confirmed (confermato) · not_found (non trovato: Crossref does not know the DOI) ·
mismatch (non corrisponde: the record contradicts the PDF, or the evidence is not enough).
A network failure is not an outcome: the document stays "da verificare" and is retried on the next run.

Rules (RULES_VERSION), derived from the 135 real JSAMS PDFs of 2026-10-10:
  - title: Crossref's title must be found in the text of the first pages (fuzzy on letters, so line breaks,
    hyphenation, dashes and OCR noise do not matter): >= 90% "uguale", >= 80% "simile";
  - authors: family names searched NEAR the title (conference-abstract pages hold other abstracts and their
    authors), at least half of them, otherwise "autori non trovati";
    verify-v2 (2026-10-10, the 4 real mismatches): when the title's page belongs to ONE item — the candidate
    DOI is printed on it and no other DOI of the same journal is (abstract pages print one per abstract) —
    the authors are searched in the whole PDF, because letters and editorials sign at the END of the page and
    long or letter-spaced author lists push names out of the window; and if the text extraction still lost
    most names, the first author found + DOI printed on the page + title "uguale" + three other positive
    checks are enough ("autori incompleti nel testo del PDF", with a note);
    verify-v3 (2026-10-11, human benchmark): when the title is "uguale", a family name of 5+ letters also counts
    when the text has it with ONE letter lost or changed by OCR — accented capitals read as punctuation or digits
    ("Çolak" → "(:;olak", "Gönener" → "G0nener"); these are listed apart ("con tolleranza OCR");
    verify-v4 (2026-10-14, FAS 2000 scanned): the authors are searched near EVERY place of the first pages where the
    title is found at >= 80% (one per page, best first), and the place with most of them counts: an old scanned
    journal repeats the title as running head of page 3, where it reads better than on page 1 (title lines out of
    order) but where no author is printed;
  - year: a year in the file name (ScienceDirect names files "<title>_<year>_<journal>.pdf") must be one of
    Crossref's years (issued, print, online); without it, a Crossref year printed on the page counts;
  - journal: an ISSN printed as such in the PDF (after "ISSN", before "/©", inside the PII) must be one of
    Crossref's ISSNs, or the journal title must be printed; only a labelled ISSN can contradict;
  - pages: Crossref's page range against the PDF's page count (a 1-page abstract is not a 16-page article);
  - file name: ScienceDirect names a download after the start of the title; on abstract pages it tells which
    of the abstracts on the page is the article (a ScienceDirect-style name that differs contradicts);
  - type: a DOI of a component, issue, journal or dataset is not an article.
  Confirmed = nothing contradicts AND the title is "uguale" plus at least one other positive check
  (or "simile" plus authors and two more). Otherwise mismatch, with every reason.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path, PurePosixPath

from . import audit, crossref
from .db import transaction
from .identifiers import is_valid_issn, normalize_issn
from .ids import new_id
from .ingest_pdf import choose_doi_candidate, find_dois, portable

RULES_VERSION = "verify-v4"
# A DOI the PDF does not print, found by doi_search.py (2026-10-14): its doi_check details carry a "ricerca" entry.
SEARCH_NOTE = "trovato con la ricerca Crossref"
OUTCOME_IT = {"confirmed": "confermato", "not_found": "non trovato", "mismatch": "non corrisponde"}
SNAPSHOT_SUBDIR = "crossref"
_TITLE_STRONG, _TITLE_SIMILAR = 0.90, 0.80
_PAGES_SEARCHED = 3
_NOT_ARTICLES = {"component", "journal-issue", "journal-volume", "journal", "dataset", "database", "peer-review",
                 "grant", "standard", "report-component"}
_STOP = {"a", "an", "the", "of", "in", "on", "and", "or", "for", "to", "with", "by", "at", "from", "is", "are",
         "as", "vs", "versus", "into", "during", "its", "their", "do", "does", "not", "between", "within"}


# ---------------------------------------------------------------- text helpers

def fold_tokens(text: str) -> list[str]:
    """Lower-case ASCII word tokens: accents removed, punctuation and dashes ignored."""
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(ch for ch in t if not unicodedata.combining(ch)).lower()
    t = t.replace("ß", "ss").replace("æ", "ae").replace("ø", "o").replace("ł", "l").replace("đ", "d")
    return re.findall(r"[a-z0-9]+", t)


def find_title(title: str, tokens: list[str]) -> tuple[float, int | None]:
    """Best match of `title` inside a token list: (share of the title's letters found in order, start index).

    Windows start near a significant word of the title; letters are compared without spaces, so
    "veloc-ity", "signifi cant" or an OCR space inside a word still match.
    """
    words = fold_tokens(title)
    target = "".join(words)
    if not target or not tokens:
        return 0.0, None
    anchors = {w for w in words[:6] if w not in _STOP and len(w) >= 3} or set(words[:3])
    n = len(words)
    best, where = 0.0, None
    for i in range(len(tokens)):
        if not any(t in anchors for t in tokens[i:i + 3]):
            continue
        window = "".join(tokens[i:i + n + 6])
        sm = SequenceMatcher(None, target, window, autojunk=False)
        if sm.quick_ratio() * (len(target) + len(window)) / (2 * len(target)) <= best:
            continue
        score = sum(b.size for b in sm.get_matching_blocks()) / len(target)
        if score > best:
            best, where = score, i
            if best >= 0.999:
                break
    return round(best, 3), where


def _family_found(family: str, region: list[str], region_compact: str) -> bool:
    toks = fold_tokens(family)
    key = [t for t in toks if len(t) >= 3] or toks
    if not key:
        return False
    if all(t in region for t in key):
        return True
    compact = "".join(toks)
    return len(compact) >= 5 and compact in region_compact


_OCR_DIGITS = str.maketrans("015", "ols")       # digits OCR reads instead of letters: G0nener, Ko1b, 5mith


def _one_edit(a: str, b: str) -> bool:
    """True when a and b differ by at most one letter added, lost or changed."""
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    i = 0
    while i < min(len(a), len(b)) and a[i] == b[i]:
        i += 1
    if len(a) == len(b):
        return a[i + 1:] == b[i + 1:]
    return a[i + 1:] == b[i:] if len(a) > len(b) else a[i:] == b[i + 1:]


def _family_found_ocr(family: str, region: set[str]) -> bool:
    """verify-v3: a family name of 5+ letters found with one letter lost or changed by OCR."""
    compact = "".join(fold_tokens(family))
    if len(compact) < 5 or not compact.isalpha():
        return False
    return any(len(t) >= 4 and _one_edit(compact, t.translate(_OCR_DIGITS)) for t in region)


_ISSN_LABELLED = [
    re.compile(r"\bISSN\b[^0-9]{0,15}(\d{4}-\d{3}[\dXx])"),
    re.compile(r"(?<![\d-])(\d{4}-\d{3}[\dXx])\s*/\s*(?:©|\(c\)|\$)", re.IGNORECASE),
    re.compile(r"\bS(\d{4}-\d{3}[\dXx])\(\d{2}\)"),                      # inside a PII: S1440-2440(04)…
]
_ISSN_ANY = re.compile(r"(?<![\dA-Za-z-])(\d{4}-\d{3}[\dXx])(?![\dXx])")


def _issns(text: str, patterns) -> set[str]:
    out = set()
    for p in patterns:
        for m in p.finditer(text or ""):
            v = m.group(1).upper()
            a, b = v[:4], v[5:]
            if re.fullmatch(r"(19|20)\d\d", a) and re.fullmatch(r"(19|20)\d\d", b):
                continue                                                  # a range of years, not an ISSN
            if is_valid_issn(v):
                out.add(normalize_issn(v))
    return out


def _page_span(page: str | None) -> int | None:
    nums = [int(n) for n in re.findall(r"\d+", page or "")]
    if not nums:
        return None
    if len(nums) == 1:
        return 1
    span = nums[-1] - nums[0] + 1
    return span if 1 <= span <= 500 else None


_YEAR_IN_NAME = re.compile(r"_((?:19|20)\d\d)_")


# ---------------------------------------------------------------- evidence from the PDF

@dataclass
class PdfEvidence:
    document_id: str
    file: str
    page_count: int
    pages: list[str]
    pdf_title: str | None
    pdf_author: str | None
    doi: str
    doi_printed: bool
    doi_note: str | None = None
    page_dois: dict[int, set[str]] = field(default_factory=dict)      # page number (1-based) -> DOIs printed


def load_evidence(conn: sqlite3.Connection, doc: sqlite3.Row, *, doi: str | None = None,
                  note: str | None = None) -> PdfEvidence:
    """What the PDF says, for comparing it with the record of `doi` (default: the document's DOI candidate).
    `note` replaces the note of the candidate (e.g. SEARCH_NOTE for a DOI found by doi_search)."""
    doi = doi or doc["doi_candidate"]
    pages = [r["text"] for r in conn.execute(
        "SELECT text FROM document_page WHERE document_id = ? ORDER BY page_no", (doc["id"],))]
    printed = conn.execute("SELECT 1 FROM document_doi WHERE document_id = ? AND doi = ? LIMIT 1",
                           (doc["id"], doi)).fetchone() is not None
    if note is None:
        ev = conn.execute("SELECT payload FROM audit_event WHERE action IN ('ingest_pdf', 'refresh_doi_candidate') "
                          "AND entity_id = ? ORDER BY seq DESC LIMIT 1", (doc["id"],)).fetchone()
        if ev:
            note = json.loads(ev["payload"]).get("doi_candidate_note")
    page_dois: dict[int, set[str]] = {}
    for r in conn.execute("SELECT page_no, doi FROM document_doi WHERE document_id = ?", (doc["id"],)):
        page_dois.setdefault(r["page_no"], set()).add(r["doi"])
    return PdfEvidence(doc["id"], doc["original_path"], doc["page_count"], pages, doc["pdf_title"], doc["pdf_author"],
                       doi, printed, note, page_dois)


def _doi_stem(doi: str) -> str:
    """The journal part of a DOI: everything before the first digit after the slash ("10.1016/j.jsams.")."""
    prefix, _, suffix = doi.partition("/")
    m = re.match(r"[^0-9]*", suffix)
    return f"{prefix}/{m.group(0) if m else ''}"


def single_item_page(ev: PdfEvidence, page: int | None) -> bool:
    """True when `page` prints the candidate DOI and no other DOI of the same journal (not an abstract page)."""
    if page is None:
        return False
    dois = ev.page_dois.get(page, set())
    stem = _doi_stem(ev.doi)
    return ev.doi in dois and not any(d != ev.doi and _doi_stem(d) == stem for d in dois)


# ---------------------------------------------------------------- comparison

@dataclass
class Verdict:
    outcome: str                       # confirmed | mismatch | not_found
    reasons: list[str]
    details: dict = field(default_factory=dict)


def _doi_in_pdf(ev: PdfEvidence) -> str:
    if ev.doi_printed:
        return "stampato nel PDF"
    if ev.doi_note and ev.doi_note.startswith(SEARCH_NOTE):
        return SEARCH_NOTE
    if ev.doi_note and "PII" in ev.doi_note:
        return "derivato dal PII"
    return "solo nei metadati del PDF"


def not_found(ev: PdfEvidence) -> Verdict:
    how = _doi_in_pdf(ev)
    reason = "Crossref non conosce questo DOI (404)"
    if how == "derivato dal PII":
        reason += ": il DOI era stato ricavato dal PII, quindi può essere sbagliato"
    return Verdict("not_found", [reason], {"doi_nel_pdf": how, "regole": RULES_VERSION})


def compare(work: dict, ev: PdfEvidence) -> Verdict:
    rec = crossref.summary(work)
    searched = ev.pages[:_PAGES_SEARCHED]
    page_tokens = [fold_tokens(p) for p in searched]
    head_text = "\n".join(searched)
    checks: dict = {}
    blocking: list[str] = []
    positive: list[str] = []
    notes: list[str] = []

    # --- title
    titles = [t for t in dict.fromkeys([rec["title"], f"{rec['title']} {rec['subtitle']}".strip()]) if t]
    best, where_page, where_idx = 0.0, None, None
    per_page: dict[int, tuple[float, int | None]] = {}          # verify-v4: best place of the title on each page
    for t in titles:
        for p, toks in enumerate(page_tokens, 1):
            score, idx = find_title(t, toks)
            if score > per_page.get(p, (0.0, None))[0]:
                per_page[p] = (score, idx)
            if score > best:
                best, where_page, where_idx = score, p, idx
    meta_score = 0.0
    if ev.pdf_title and not re.search(r"10\.\d{4,9}/|\bPII\b", ev.pdf_title):
        meta_score = max((find_title(t, fold_tokens(ev.pdf_title))[0] for t in titles), default=0.0)
    if not rec["title"]:
        title_state = "assente in Crossref"
        blocking.append("il record Crossref non ha un titolo")
    elif best >= _TITLE_STRONG:
        title_state = "uguale"
    elif best >= _TITLE_SIMILAR:
        title_state = "simile"
        notes.append(f"titolo solo simile nel testo del PDF ({best:.0%})")
    elif meta_score >= _TITLE_STRONG:
        title_state = "solo nei metadati"
        blocking.append("titolo di Crossref uguale a quello nei dati del PDF ma non trovato nel testo")
    else:
        title_state = "non trovato"
        blocking.append(f"titolo di Crossref non trovato nel PDF (somiglianza massima {best:.0%})")
    checks["titolo"] = {"esito": title_state, "somiglianza": best, "pagina": where_page,
                        "somiglianza_metadati": round(meta_score, 3), "crossref": rec["title"]}

    # --- authors (near the title when it was found: abstract pages hold other abstracts and authors)
    families = [a["family"] for a in rec["authors"] if a.get("family")]
    ocr_tolerant = title_state == "uguale"

    def search(region: list[str]) -> tuple[list[str], list[str]]:
        region_set, region_compact = set(region), "".join(region)
        exact = [f for f in families if _family_found(f, region_set, region_compact)]
        approx = [f for f in families if ocr_tolerant and f not in exact and _family_found_ocr(f, region_set)]
        return [f for f in families if f in exact or f in approx], approx

    if where_page is not None and title_state in ("uguale", "simile"):
        n = len(fold_tokens(rec["title"]))
        places = [(where_page, where_idx)] + [(p, i) for p, (sc, i) in sorted(per_page.items(), key=lambda kv: -kv[1][0])
                                             if p != where_page and sc >= _TITLE_SIMILAR and i is not None]
        found, approx, scope = None, [], ""
        for p, i in places:                              # the best place first; another only if it finds more
            toks = page_tokens[p - 1]
            f_here, a_here = search(toks[max(0, i - 40): i + n + 150])
            if found is None or len(f_here) > len(found):
                found, approx, scope = f_here, a_here, f"vicino al titolo (pagina {p})"
    else:
        region = [t for toks in page_tokens for t in toks] + fold_tokens(ev.pdf_author or "")
        scope = "prime pagine e dati del PDF"
        found, approx = search(region)
    single = title_state in ("uguale", "simile") and single_item_page(ev, where_page)
    if families and len(found) / len(families) < 0.75 and single:
        region = [t for p in ev.pages for t in fold_tokens(p)] + fold_tokens(ev.pdf_author or "")
        found, approx = search(region)
        scope = "in tutto il PDF (pagina di un solo articolo)"
    missing = [f for f in families if f not in found]
    if approx:
        notes.append(f"autori trovati con tolleranza OCR (una lettera persa o cambiata): {', '.join(approx[:10])}")
    authors_incomplete = False
    if not families:
        a_state = "assenti in Crossref"
    elif len(found) / len(families) >= 0.75 and families[0] in found:
        a_state = "trovati"
        positive.append("autori")
    elif len(found) / len(families) >= 0.5:
        a_state = "in parte"
        positive.append("autori")
        notes.append(f"autori trovati {len(found)} su {len(families)}")
    elif single and title_state == "uguale" and ev.doi_printed and families[0] in found:
        a_state = "incompleti nel testo del PDF"
        authors_incomplete = True
        notes.append(f"autori trovati solo {len(found)} su {len(families)} (primo autore trovato): "
                     "il testo estratto dal PDF sembra incompleto")
    else:
        a_state = "non trovati"
        blocking.append(f"autori di Crossref non trovati {scope} ({len(found)} su {len(families)})")
    checks["autori"] = {"esito": a_state, "trovati": len(found), "totale": len(families),
                        "mancanti": missing[:10], "dove": scope}
    if approx:
        checks["autori"]["con_tolleranza_ocr"] = approx[:10]

    # --- year
    cr_years = rec["years"]
    name_year = None
    m = _YEAR_IN_NAME.search(PurePosixPath(ev.file.replace("\\", "/")).name)
    if m:
        name_year = int(m.group(1))
    page_for_year = page_tokens[(where_page or 1) - 1] if page_tokens else []
    page_years = sorted({int(t) for t in page_for_year if re.fullmatch(r"(19|20)\d\d", t)} & set(cr_years))
    if not cr_years:
        y_state, y_proof = "non verificabile", "anno assente in Crossref"
    elif name_year is not None:
        if name_year in cr_years:
            y_state, y_proof = "uguale", f"{name_year} nel nome del file"
        else:
            y_state, y_proof = "diverso", f"{name_year} nel nome del file, Crossref {', '.join(map(str, cr_years))}"
            blocking.append(f"anno diverso: {y_proof}")
    elif page_years:
        y_state, y_proof = "uguale", f"{page_years[0]} nella pagina"
    else:
        y_state, y_proof = "non verificabile", "nessun anno di Crossref nel PDF"
    if y_state == "uguale":
        positive.append("anno")
    checks["anno"] = {"esito": y_state, "prova": y_proof, "crossref": cr_years}

    # --- journal / ISSN
    cr_issn = set(rec["issn"])
    meta_text = " ".join(x for x in (ev.pdf_title or "",) if x)
    labelled = _issns(head_text + "\n" + meta_text, _ISSN_LABELLED)
    anywhere = _issns(head_text + "\n" + meta_text, [_ISSN_ANY]) | labelled
    compact_head = "".join(fold_tokens(head_text))
    titles_j = [t for t in rec["container_title"] + rec["short_container_title"] if len("".join(fold_tokens(t))) >= 8]
    title_hit = next((t for t in titles_j if "".join(fold_tokens(t)) in compact_head), None)
    if anywhere & cr_issn:
        j_state, j_proof = "uguale", f"ISSN {', '.join(sorted(anywhere & cr_issn))} nel PDF"
    elif title_hit:
        j_state, j_proof = "uguale", f"titolo della rivista nel PDF ({title_hit})"
    elif labelled:
        j_state, j_proof = "diverso", f"ISSN {', '.join(sorted(labelled))} nel PDF, Crossref {', '.join(sorted(cr_issn)) or 'nessuno'}"
        blocking.append(f"rivista diversa: {j_proof}")
    else:
        j_state, j_proof = "non verificabile", "né ISSN né titolo della rivista nel PDF"
    if j_state == "uguale":
        positive.append("rivista")
    checks["rivista"] = {"esito": j_state, "prova": j_proof,
                         "crossref": (rec["container_title"] or [""])[0], "issn_crossref": sorted(cr_issn)}

    # --- pages
    span = _page_span(rec["page"])
    if span is None:
        p_state = "non verificabile"
    elif (span >= 4 and ev.page_count <= 2) or (span <= 2 and ev.page_count >= 5):
        p_state = "diverso"
        blocking.append(f"lunghezza diversa: il PDF ha {ev.page_count} pagine, Crossref indica {span} "
                        f"(pagine {rec['page']}): ad esempio abstract invece di articolo completo")
    else:
        p_state = "compatibile"
    checks["pagine"] = {"esito": p_state, "pdf": ev.page_count, "crossref": rec["page"], "pagine_crossref": span}

    # --- file name: ScienceDirect names downloads "<start-of-title>_<year>_<journal>.pdf". On conference-abstract
    # pages the title found in the text may be a NEIGHBOURING abstract's; the file name is the article's own.
    stem = re.sub(r"\.pdf$", "", PurePosixPath(ev.file.replace("\\", "/")).name, flags=re.IGNORECASE)
    name_part = re.match(r"^(.*?)(?:_(?:19|20)\d\d_.*)?$", stem).group(1)
    name_c = "".join(fold_tokens(name_part))
    sd_like = bool(re.fullmatch(r"[A-Za-z0-9,;'()\-]+", name_part)) and name_part.count("-") >= 3
    cr_c = "".join(fold_tokens(rec["title"]))
    if len(name_c) >= 15 and cr_c:
        ratio = round(max(SequenceMatcher(None, name_c, "".join(fold_tokens(t))[:len(name_c)], autojunk=False).ratio()
                          for t in titles), 3)
        if ratio >= 0.85:
            n_state = "uguale"
            positive.append("nome del file")
        elif ratio < 0.6 and sd_like:
            n_state = "diverso"
            blocking.append(f"il nome del file indica un altro titolo («{name_part}»)")
        else:
            n_state = "non verificabile"
    else:
        ratio, n_state = None, "non verificabile"
    checks["nome_file"] = {"esito": n_state, "somiglianza": ratio, "nome": name_part}

    # --- type, DOI, notices
    if rec["type"] in _NOT_ARTICLES:
        blocking.append(f"il DOI non è di un articolo (tipo Crossref: {rec['type']})")
    if rec["doi"] and rec["doi"] != ev.doi:
        notes.append(f"Crossref risponde con il DOI {rec['doi']} (alias di {ev.doi})")
    for u in rec["update_to"]:
        notes.append(f"questo DOI è un avviso ({u['type']}) che riguarda {u['doi']}")
    for u in rec["updated_by"]:
        notes.append(f"Crossref segnala un aggiornamento ({u['type']}): {u['doi']}")
    doi_how = _doi_in_pdf(ev)
    if doi_how != "stampato nel PDF":
        notes.append(f"DOI {doi_how}: confermato solo dal confronto con il record")

    if authors_incomplete and len(positive) < 3:
        blocking.append(f"autori di Crossref non trovati {scope} ({len(found)} su {len(families)}) "
                        f"e solo {len(positive)} altri controlli positivi")
    if blocking:
        outcome, reasons = "mismatch", blocking
    elif title_state == "uguale" and positive:
        outcome, reasons = "confirmed", [f"titolo uguale; {', '.join(positive)} coincidono"]
    elif title_state == "simile" and "autori" in positive and len(positive) >= 3:
        outcome, reasons = "confirmed", [f"titolo simile ({best:.0%}); {', '.join(positive)} coincidono"]
    else:
        outcome = "mismatch"
        reasons = ["prove insufficienti: " + (f"titolo {title_state}" + (f", {', '.join(positive)}" if positive else "")
                                              + " — nient'altro da confrontare")]
    details = {"regole": RULES_VERSION, "doi_nel_pdf": doi_how, "controlli": checks, "note": notes,
               "crossref": {k: rec[k] for k in ("doi", "title", "container_title", "issn", "years", "type", "page",
                                                 "volume", "issue", "publisher")}
               | {"autori": [a.get("family") or a.get("name") for a in rec["authors"]][:30]}}
    return Verdict(outcome, reasons, details)


# ---------------------------------------------------------------- storage

def store_snapshot(conn: sqlite3.Connection, resp: crossref.Response, *, home: Path, archive_dir: Path,
                   source_id: str = crossref.SOURCE_ID, subdir: str = SNAPSHOT_SUBDIR, licence: str = crossref.LICENCE) -> str:
    """Write the raw answer under <archive>/<subdir>/aa/bb/<sha>.<ext> and record it; reuse an identical one.
    Defaults: Crossref; verify_pubmed passes PubMed's source, folder and licence."""
    sha = hashlib.sha256(resp.body).hexdigest()
    existing = conn.execute("SELECT id FROM source_snapshot WHERE source_id = ? AND request_uri = ? AND content_sha256 = ?",
                            (source_id, resp.public_url, sha)).fetchone()
    if existing:
        return existing["id"]
    ext = "json" if "json" in resp.content_type else ("xml" if "xml" in resp.content_type else "txt")
    path = Path(archive_dir) / subdir / sha[:2] / sha[2:4] / f"{sha}.{ext}"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".partial")
        tmp.write_bytes(resp.body)
        tmp.replace(path)
    snap_id = new_id("source_snapshot")
    conn.execute(
        """INSERT INTO source_snapshot (id, source_id, request_uri, retrieved_at, http_status, content_sha256,
               content_type, storage_path, size_bytes, etag, last_modified, licence, storage_allowed)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
        (snap_id, source_id, resp.public_url, resp.retrieved_at or audit.utc_now(), resp.status, sha,
         resp.content_type, portable(path, home), len(resp.body), resp.headers.get("etag"),
         resp.headers.get("last-modified"), licence))
    return snap_id


def read_snapshot(conn: sqlite3.Connection, snapshot_id: str, home: Path) -> tuple[int, bytes]:
    """(http_status, body) of a stored answer, after checking its sha256."""
    row = conn.execute("SELECT http_status, content_sha256, storage_path FROM source_snapshot WHERE id = ?",
                       (snapshot_id,)).fetchone()
    if row is None or not row["storage_path"]:
        raise FileNotFoundError(f"risposta salvata {snapshot_id} non trovata")
    path = Path(row["storage_path"])
    path = path if path.is_absolute() else Path(home) / path
    body = path.read_bytes()
    if hashlib.sha256(body).hexdigest() != row["content_sha256"]:
        raise ValueError(f"risposta salvata danneggiata (impronta diversa): {row['storage_path']}")
    return row["http_status"], body


def _article_for(conn: sqlite3.Connection, work: dict, fallback_doi: str) -> tuple[str, bool]:
    rec = crossref.summary(work)
    doi = rec["doi"] or fallback_doi
    row = conn.execute("SELECT id FROM article WHERE doi = ?", (doi,)).fetchone()
    if row:
        return row["id"], False
    art_id = new_id("article")
    conn.execute(
        """INSERT INTO article (id, doi, title, publication_date, publication_year, article_type, language,
               access_level, licence, analysis_level)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'full_text', ?, 'metadata')""",
        (art_id, doi, rec["title"], crossref.iso_date(work), (rec["issued"] or [None])[0] or (rec["years"] or [None])[0],
         rec["type"], rec["language"], "; ".join(rec["licence"]) or None))
    return art_id, True


def record(conn: sqlite3.Connection, ev: PdfEvidence, verdict: Verdict, work: dict | None, *, actor: str, mode: str,
           snapshot_id: str | None = None, response: crossref.Response | None = None,
           home: Path | None = None, archive_dir: Path | None = None) -> dict:
    """One transaction: the raw answer (if new), the doi_check row, the article (only if confirmed),
    the link on the document and the audit event — all or nothing."""
    check_id = new_id("doi_check")
    with transaction(conn):
        if response is not None:
            snapshot_id = store_snapshot(conn, response, home=home, archive_dir=archive_dir)
        article_id, created, unlinked = None, False, None
        if verdict.outcome == "confirmed":
            article_id, created = _article_for(conn, work, ev.doi)
            conn.execute("UPDATE document SET article_id = ? WHERE id = ?", (article_id, ev.document_id))
        else:
            old = conn.execute("SELECT article_id FROM document WHERE id = ?", (ev.document_id,)).fetchone()
            if old and old["article_id"]:
                unlinked = old["article_id"]                 # was confirmed with older rules: link removed
                conn.execute("UPDATE document SET article_id = NULL WHERE id = ?", (ev.document_id,))
        conn.execute(
            """INSERT INTO doi_check (id, document_id, doi, source_id, source_snapshot_id, outcome, reasons, details,
                   rules_version, article_id, checked_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (check_id, ev.document_id, ev.doi, crossref.SOURCE_ID, snapshot_id, verdict.outcome,
             json.dumps(verdict.reasons, ensure_ascii=False), json.dumps(verdict.details, ensure_ascii=False),
             RULES_VERSION, article_id, audit.utc_now()))
        audit.record(conn, actor=actor, action="verify_doi", entity_type="document", entity_id=ev.document_id,
                     payload={"doi": ev.doi, "outcome": verdict.outcome, "reasons": verdict.reasons,
                              "doi_check_id": check_id, "source_snapshot_id": snapshot_id, "article_id": article_id,
                              "article_created": created, "article_unlinked": unlinked, "rules": RULES_VERSION,
                              "mode": mode},
                     in_transaction=True)
    return {"article_id": article_id, "article_created": created, "article_unlinked": unlinked, "doi_check_id": check_id}


# ---------------------------------------------------------------- the run

def latest_checks(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    rows = conn.execute(
        """SELECT c.* FROM doi_check c
           WHERE c.rowid = (SELECT c2.rowid FROM doi_check c2 WHERE c2.document_id = c.document_id
                            ORDER BY c2.checked_at DESC, c2.rowid DESC LIMIT 1)""").fetchall()
    return {r["document_id"]: r for r in rows}


def candidates(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT * FROM document WHERE kind = 'article' AND doi_candidate IS NOT NULL
           ORDER BY original_path""").fetchall()


def found_by_search(check: sqlite3.Row | None) -> bool:
    """True when this doi_check is about a DOI that the PDF does not print, found by doi_search."""
    if check is None or not check["details"]:
        return False
    try:
        return bool((json.loads(check["details"]) or {}).get("ricerca"))
    except ValueError:
        return False


def searched_documents(conn: sqlite3.Connection, latest: dict | None = None) -> list[tuple[sqlite3.Row, sqlite3.Row]]:
    """(document, latest doi_check) for the article PDFs whose DOI came from doi_search: no printed DOI, or a printed
    DOI that Crossref does not know (search-v2)."""
    latest = latest_checks(conn) if latest is None else latest
    out = []
    for doc in conn.execute("SELECT * FROM document WHERE kind = 'article' ORDER BY original_path"):
        last = latest.get(doc["id"])
        if found_by_search(last):
            out.append((doc, last))
    return out


def refresh_candidates(conn: sqlite3.Connection, *, actor: str = "orpheus verify-dois") -> list[dict]:
    """Read again, with the current rules, the DOIs of the article PDFs not yet confirmed — from their STORED page
    text, the PDFs are not opened (2026-10-14: Elsevier DOIs whose last digits pypdf split off, see
    ingest_pdf.join_split_dois). Only candidates that came from the text (first_page / ambiguous / none): a
    candidate from the PDF metadata stays (the metadata are not stored). The DOIs printed on each page
    (document_doi) are aligned too. One transaction and one audit event (before/after) per changed document;
    running it again changes nothing."""
    latest = latest_checks(conn)
    changed = []
    docs = conn.execute(
        """SELECT id, original_path, doi_candidate, doi_candidate_source FROM document WHERE kind = 'article'
           AND doi_candidate_source IN ('first_page', 'ambiguous', 'none') ORDER BY original_path""").fetchall()
    for d in docs:
        last = latest.get(d["id"])
        if last is not None and last["outcome"] == "confirmed":
            continue
        pages = [r["text"] for r in conn.execute(
            "SELECT text FROM document_page WHERE document_id = ? ORDER BY page_no", (d["id"],))]
        old_rows = {(r["page_no"], r["doi"]) for r in conn.execute(
            "SELECT page_no, doi FROM document_doi WHERE document_id = ?", (d["id"],))}
        new_rows = {(i + 1, x) for i, t in enumerate(pages) for x in find_dois(t)}
        cand, source, note = choose_doi_candidate({}, find_dois(pages[0]) if pages else [], pages[0] if pages else "")
        if cand == d["doi_candidate"] and source == d["doi_candidate_source"] and new_rows == old_rows:
            continue
        with transaction(conn):
            conn.execute("UPDATE document SET doi_candidate = ?, doi_candidate_source = ? WHERE id = ?",
                         (cand, source, d["id"]))
            conn.executemany("DELETE FROM document_doi WHERE document_id = ? AND page_no = ? AND doi = ?",
                             [(d["id"], p, x) for p, x in sorted(old_rows - new_rows)])
            conn.executemany("INSERT INTO document_doi (document_id, page_no, doi) VALUES (?, ?, ?)",
                             [(d["id"], p, x) for p, x in sorted(new_rows - old_rows)])
            audit.record(conn, actor=actor, action="refresh_doi_candidate", entity_type="document", entity_id=d["id"],
                         payload={"prima": {"doi_candidate": d["doi_candidate"], "doi_candidate_source": d["doi_candidate_source"],
                                            "doi_pagine_tolti": [list(x) for x in sorted(old_rows - new_rows)]},
                                  "dopo": {"doi_candidate": cand, "doi_candidate_source": source,
                                           "doi_pagine_aggiunti": [list(x) for x in sorted(new_rows - old_rows)]},
                                  "doi_candidate_note": note,
                                  "regola": "ingest_pdf.join_split_dois (DOI Elsevier con le ultime cifre staccate), "
                                            "applicata al testo salvato"},
                         in_transaction=True)
        if cand != d["doi_candidate"]:
            changed.append({"file": _short(d["original_path"]), "prima": d["doi_candidate"], "dopo": cand})
    return changed


@dataclass
class RunResult:
    processed: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    skipped: int = 0
    unchanged: int = 0
    stopped: str | None = None
    requests: int = 0


def _short(path: str) -> str:
    return PurePosixPath(path.replace("\\", "/")).name


def run(conn: sqlite3.Connection, *, home: Path, archive_dir: Path, client: crossref.CrossrefClient | None,
        mode: str = "nuovi", limit: int | None = None, actor: str = "cli", max_consecutive_failures: int = 3) -> RunResult:
    """mode: 'nuovi' (never verified, or DOI candidate changed) · 'ricontrolla' (also re-ask Crossref for
    not_found / mismatch) · 'rivaluta' (no network: apply the current rules to the stored answers)."""
    out = RunResult()
    latest = latest_checks(conn)
    todo = []
    for doc in candidates(conn):
        last = latest.get(doc["id"])
        if found_by_search(last):       # the DOI found by doi_search is kept: searched_documents below
            continue
        same = last is not None and last["doi"] == doc["doi_candidate"]
        if mode == "rivaluta":
            if same:
                todo.append((doc, last))
            continue
        if same and (last["outcome"] == "confirmed" or mode == "nuovi"):
            out.skipped += 1
            continue
        todo.append((doc, last))
    for doc, last in searched_documents(conn, latest):        # DOIs not printed in the PDF (doi_search)
        if mode == "rivaluta" or (mode == "ricontrolla" and last["outcome"] != "confirmed"):
            todo.append((doc, last))
        else:
            out.skipped += 1
    if limit is not None:
        out.skipped += max(0, len(todo) - limit)
        todo = todo[:limit]

    failures = 0
    for doc, last in todo:
        searched = found_by_search(last)
        ev = load_evidence(conn, doc, doi=last["doi"], note=SEARCH_NOTE) if searched else load_evidence(conn, doc)
        item = {"file": _short(ev.file), "doi": ev.doi}
        try:
            resp, snapshot_id = None, None
            if mode == "rivaluta":
                snapshot_id = last["source_snapshot_id"]
                status, body = read_snapshot(conn, snapshot_id, home)
            else:
                resp = client.get_work(ev.doi)
                status, body = resp.status, resp.body
            work = None
            if status == 404:
                verdict = not_found(ev)
            else:
                try:
                    work = crossref.parse_work(body)
                except (ValueError, UnicodeDecodeError) as exc:
                    raise crossref.CrossrefError(f"risposta non leggibile: {exc}") from exc
                verdict = compare(work, ev)
            if searched:
                verdict.details["ricerca"] = json.loads(last["details"]).get("ricerca")
        except crossref.CrossrefBlocked as exc:
            out.stopped = str(exc)
            out.errors.append({**item, "errore": str(exc)})
            break
        except (crossref.CrossrefError, FileNotFoundError, ValueError) as exc:
            out.errors.append({**item, "errore": str(exc)})
            failures += 1
            if mode != "rivaluta" and failures >= max_consecutive_failures:
                out.stopped = (f"{failures} errori di rete di fila: verifica interrotta. Controlla la connessione "
                               "e rilancia (i DOI già verificati non vengono richiesti di nuovo).")
                break
            continue
        failures = 0
        if mode == "rivaluta" and last is not None and last["outcome"] == verdict.outcome \
                and json.loads(last["reasons"]) == verdict.reasons and last["rules_version"] == RULES_VERSION:
            out.unchanged += 1
            continue
        saved = record(conn, ev, verdict, work, actor=actor, mode=mode, snapshot_id=snapshot_id, response=resp,
                       home=home, archive_dir=archive_dir)
        out.processed.append({**item, "esito": OUTCOME_IT[verdict.outcome], "motivo": "; ".join(verdict.reasons),
                              **({"note": verdict.details.get("note")} if verdict.details.get("note") else {}),
                              **({"articolo": saved["article_id"]} if saved["article_id"] else {}),
                              **({"articolo_creato": True} if saved["article_created"] else {}),
                              **({"collegamento_rimosso": saved["article_unlinked"]} if saved["article_unlinked"] else {})})
    if client is not None:
        out.requests = client.requests_made
    if out.errors or out.stopped:
        audit.record(conn, actor=actor, action="verify_dois_run",
                     payload={"mode": mode, "processed": len(out.processed), "errors": out.errors,
                              "stopped": out.stopped, "requests": out.requests, "rules": RULES_VERSION})
    return out


def status_by_document(conn: sqlite3.Connection) -> dict[str, dict]:
    """Current verification state of every document that has one (for the quality report)."""
    out = {}
    for doc_id, r in latest_checks(conn).items():
        out[doc_id] = {"outcome": r["outcome"], "esito": OUTCOME_IT[r["outcome"]], "doi": r["doi"],
                       "motivo": "; ".join(json.loads(r["reasons"])), "article_id": r["article_id"],
                       "checked_at": r["checked_at"], "ricerca": found_by_search(r)}
    return out
