"""T-030 — Fase 3: pieces of text ("segments") of the complete articles, rules segment-v1. Deterministic, no model.

User's decisions of 2026-10-15 13:40: search by meaning with a local multilingual model, segments and vectors in the
database (migration 0009). Source: the text of the complete-article PDF of every article (card_store._complete_documents:
the PDF classified "articolo completo" with most pages), page by page from document_page. Rules:
  - words split by a line break ("veloc-\\nity") or by a ligature rendered as a space ("signifi cant") are joined with
    the same evidence rules as the reader (ingest_pdf.rejoin_hyphenated: only from the document's own text);
  - running headers and footers left out: a line repeated (digits ignored) on at least 40% of the pages, and at least
    2; lines that are only a page number; the publisher's lines of page 1 are kept (few words, harmless);
  - the bibliography is left out: from a heading "References" / "Bibliography" / "Literature cited" in the second half
    of the text; back matter (Acknowledgements, Funding, Conflict of interest, Declaration of competing interest, CRediT,
    Data availability, Supplementary material, Appendix) is left out up to the next real heading;
  - headings of the usual sections (Abstract, Introduction, Methods…, Results, Discussion, Conclusion(s), Practical
    applications, Limitations… numbered or not) start a new segment and become its `section`;
  - inside a section, sentences are put together up to ~160 words (at most 220; a single longer sentence is cut by
    words); a last piece under 60 words joins the previous one of the same section when the total stays within 280;
    a piece under 20 words (e.g. "Design: Cross-sectional study." of a structured abstract) goes, with its heading as a
    label, in front of the next segment.
Every segment keeps the first and last page of its sentences. Rebuilt when the rules change; unchanged documents are
left as they are (their vectors stay valid).
"""

from __future__ import annotations

import bisect
import hashlib
import re
import sqlite3
from dataclasses import dataclass

from . import audit, card_store
from .cards import normalise
from .db import transaction
from .ids import new_id
from .ingest_pdf import rejoin_hyphenated

RULES_VERSION = "segment-v1"
TARGET_WORDS, MAX_WORDS, MIN_WORDS, MERGE_LIMIT, TINY_WORDS = 160, 220, 60, 280, 20

_NUMBERING = r"(?:(?:\d{1,2}|[IVX]{1,4})(?:\.\d{1,2}){0,3}\.?\s+)?"
_SECTIONS = (r"abstract|summary|introduction|background|methods?|materials?\s+and\s+methods?|methodology|participants|"
             r"subjects|study\s+design|design|procedures?|experimental\s+(?:approach|design|protocol)(?:\s+to\s+the\s+problem)?|"
             r"statistical\s+analys[ie]s|data\s+analys[ie]s|results?|discussion|conclusions?|practical\s+applications?|"
             r"limitations?|strengths\s+and\s+limitations|perspectives?|outcome\s+measures|measurements?|interventions?|"
             r"results\s+and\s+discussion|general\s+discussion|implications?|future\s+(?:research|directions)")
_BACK = (r"acknowledg(?:e)?ments?|funding(?:\s+information|\s+sources?)?|conflicts?\s+of\s+interests?|"
         r"declarations?\s+of\s+(?:competing\s+)?interests?|competing\s+interests?|credit\s+authorship.*|"
         r"author\s+contributions?|data\s+availability(?:\s+statement)?|supplementary\s+(?:material|data|files?)|"
         r"appendix(?:\s+[a-z0-9])?|ethics(?:\s+(?:approval|declarations?))?|disclosures?|orcid|"
         r"declaration\s+of\s+generative\s+ai.*|patient\s+consent|informed\s+consent")
_HEADING = re.compile(rf"^\s*{_NUMBERING}({_SECTIONS})\s*:?\s*$", re.I)
_BACK_HEADING = re.compile(rf"^\s*{_NUMBERING}({_BACK})(?:\s+statements?)?\s*:?\s*$", re.I)
_REFERENCES = re.compile(rf"^\s*{_NUMBERING}(references|reference\s+list|bibliography|literature\s+cited)\s*:?\s*$", re.I)
_PAGE_NUMBER = re.compile(r"^\s*\d{1,4}\s*$")
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\[])")


@dataclass
class Segment:
    ord: int
    page_from: int
    page_to: int
    section: str | None
    text: str

    @property
    def words(self) -> int:
        return len(self.text.split())

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


def _shape(line: str) -> str:
    return re.sub(r"\d+", "#", " ".join(line.split()).casefold())


def repeated_lines(pages: list[str]) -> set[str]:
    """Shapes (digits as #) of the lines repeated on at least 40% of the pages and at least 2."""
    if len(pages) < 3:
        return set()
    count: dict[str, int] = {}
    for p in pages:
        for shape in {_shape(ln) for ln in (p or "").splitlines() if ln.strip()}:
            count[shape] = count.get(shape, 0) + 1
    need = max(2, int(0.4 * len(pages) + 0.999))
    return {s for s, n in count.items() if n >= need and len(s) <= 200
            and not (_HEADING.match(s) or _BACK_HEADING.match(s) or _REFERENCES.match(s))}   # headings are never running heads


def _heading(line: str) -> str | None:
    m = _HEADING.match(line)
    if not m:
        return None
    name = " ".join(m.group(1).split())
    return name[:1].upper() + name[1:].lower()


def segment_pages(pages: list[str]) -> list[Segment]:
    pages, _ = rejoin_hyphenated([p or "" for p in pages])
    drop = repeated_lines(pages)
    lines: list[tuple[int, str]] = []
    for no, p in enumerate(pages, 1):
        for ln in p.splitlines():
            if not ln.strip() or _PAGE_NUMBER.match(ln) or _shape(ln) in drop:
                continue
            lines.append((no, ln.strip()))
    total = sum(len(t) for _, t in lines) or 1
    out: list[Segment] = []
    buffer: list[tuple[int, str]] = []
    section: str | None = None
    skipping = False
    seen = 0

    def flush():
        nonlocal buffer
        if buffer and not skipping:
            _chunk(buffer, section, out)
        buffer = []

    for page, line in lines:
        position = seen / total
        seen += len(line)
        if _REFERENCES.match(line) and position >= 0.5:
            break
        if _BACK_HEADING.match(line):
            flush()
            skipping = True
            continue
        name = _heading(line)
        if name:
            flush()
            section, skipping = name, False
            continue
        buffer.append((page, line))
    flush()
    out = _join_tiny(out)
    for i, s in enumerate(out, 1):
        s.ord = i
    return out


def _join_tiny(segs: list[Segment]) -> list[Segment]:
    """A piece under TINY_WORDS (e.g. "Design: Cross-sectional study." of a structured abstract) goes in front of the
    next segment, with its own heading as a label; at the end it joins the previous one."""
    def labelled(s: Segment) -> Segment:
        if s.words < TINY_WORDS and s.section and not s.text.lower().startswith(s.section.lower()):
            return Segment(0, s.page_from, s.page_to, s.section, f"{s.section}: {s.text}")
        return s

    out: list[Segment] = []
    pending: Segment | None = None
    for s in segs:
        s = labelled(s)
        if pending is not None:
            s = Segment(0, min(pending.page_from, s.page_from), max(pending.page_to, s.page_to), s.section,
                        f"{pending.text} {s.text}")
            pending = None
        if s.words < TINY_WORDS:
            pending = s
            continue
        out.append(s)
    if pending is not None:
        if out:
            last = out[-1]
            out[-1] = Segment(0, last.page_from, max(last.page_to, pending.page_to), last.section,
                              f"{last.text} {pending.text}")
        else:
            out.append(pending)
    return out


def _chunk(buffer: list[tuple[int, str]], section: str | None, out: list[Segment]) -> None:
    starts, parts, pos = [], [], 0
    for page, line in buffer:
        starts.append(pos)
        parts.append(line)
        pos += len(line) + 1
    raw = " ".join(parts)
    page_at = [p for p, _ in buffer]

    def page_of(offset: int) -> int:
        return page_at[max(0, bisect.bisect_right(starts, offset) - 1)]

    sentences: list[tuple[int, int, str]] = []          # (first page, last page, normalised text)
    offset = 0
    for piece in _SENTENCE.split(raw):
        start = raw.find(piece, offset)
        start = offset if start < 0 else start
        end = start + max(0, len(piece) - 1)
        offset = start + len(piece)
        text = normalise(piece)
        if not text:
            continue
        words = text.split()
        while len(words) > MAX_WORDS:                   # one very long "sentence" (tables, lists): cut by words
            sentences.append((page_of(start), page_of(end), " ".join(words[:TARGET_WORDS])))
            words = words[TARGET_WORDS:]
        sentences.append((page_of(start), page_of(end), " ".join(words)))
    current: list[tuple[int, int, str]] = []
    pieces: list[list[tuple[int, int, str]]] = []
    count = 0
    for s in sentences:
        n = len(s[2].split())
        if current and count + n > MAX_WORDS or current and count >= TARGET_WORDS:
            pieces.append(current)
            current, count = [], 0
        current.append(s)
        count += n
    if current:
        last_words = sum(len(x[2].split()) for x in current)
        if pieces and last_words < MIN_WORDS and sum(len(x[2].split()) for x in pieces[-1]) + last_words <= MERGE_LIMIT:
            pieces[-1].extend(current)
        else:
            pieces.append(current)
    for p in pieces:
        out.append(Segment(0, p[0][0], max(x[1] for x in p), section, " ".join(x[2] for x in p)))


# ---------------------------------------------------------------- database

def _pages(conn: sqlite3.Connection, document_id: str) -> list[str]:
    return [r["text"] or "" for r in conn.execute(
        "SELECT text FROM document_page WHERE document_id = ? ORDER BY page_no", (document_id,))]


def plan(conn: sqlite3.Connection) -> dict:
    """For every complete article: its segments and whether the database already has exactly them. Read-only."""
    items = []
    for article_id, d in sorted(card_store._complete_documents(conn).items()):
        segs = segment_pages(_pages(conn, d["id"]))
        old = [r["text_sha256"] for r in conn.execute(
            "SELECT text_sha256 FROM segment WHERE document_id = ? AND rules = ? ORDER BY ord", (d["id"], RULES_VERSION))]
        other = conn.execute("SELECT count(*) FROM segment WHERE document_id = ? AND rules <> ?",
                             (d["id"], RULES_VERSION)).fetchone()[0]
        same = old == [s.sha256 for s in segs] and not other
        items.append({"article_id": article_id, "document_id": d["id"], "segments": segs,
                      "azione": "uguali" if same else ("da rifare" if old or other else "nuovi")})
    return {"regole": RULES_VERSION, "articoli": items}


def write(conn: sqlite3.Connection, the_plan: dict, *, actor: str = "orpheus segmenta") -> dict:
    written = replaced = 0
    with transaction(conn):
        for it in the_plan["articoli"]:
            if it["azione"] == "uguali":
                continue
            if it["azione"] == "da rifare":
                replaced += conn.execute("DELETE FROM segment WHERE document_id = ?", (it["document_id"],)).rowcount
            for s in it["segments"]:
                conn.execute("""INSERT INTO segment (id, document_id, article_id, rules, ord, page_from, page_to, section,
                                    text, words, text_sha256) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                             (new_id("segment"), it["document_id"], it["article_id"], RULES_VERSION, s.ord, s.page_from,
                              s.page_to, s.section, s.text, s.words, s.sha256))
                written += 1
        changed = sum(1 for it in the_plan["articoli"] if it["azione"] != "uguali")
        if changed:
            audit.record(conn, actor=actor, action="write_segments",
                         payload={"regole": RULES_VERSION, "articoli": changed, "segmenti_scritti": written,
                                  "segmenti_sostituiti": replaced}, in_transaction=True)
    return {"articoli_cambiati": changed, "segmenti_scritti": written, "segmenti_sostituiti": replaced}


def summary(the_plan: dict) -> dict:
    segs = [s for it in the_plan["articoli"] for s in it["segments"]]
    words = sorted(s.words for s in segs)
    sections: dict[str, int] = {}
    for s in segs:
        key = s.section or "(prima dei titoli)"
        sections[key] = sections.get(key, 0) + 1
    return {"articoli": len(the_plan["articoli"]), "segmenti": len(segs),
            "azioni": {a: sum(1 for it in the_plan["articoli"] if it["azione"] == a) for a in ("nuovi", "da rifare", "uguali")},
            "parole_mediana": words[len(words) // 2] if words else 0, "parole_min": words[0] if words else 0,
            "parole_max": words[-1] if words else 0,
            "articoli_senza_segmenti": sum(1 for it in the_plan["articoli"] if not it["segments"]),
            "sezioni_principali": dict(sorted(sections.items(), key=lambda kv: -kv[1])[:12])}


def status(conn: sqlite3.Connection) -> dict:
    q = lambda sql: conn.execute(sql).fetchone()[0]   # noqa: E731
    return {"segmenti": q(f"SELECT count(*) FROM segment WHERE rules = '{RULES_VERSION}'"),
            "articoli_con_segmenti": q(f"SELECT count(DISTINCT article_id) FROM segment WHERE rules = '{RULES_VERSION}'")}
