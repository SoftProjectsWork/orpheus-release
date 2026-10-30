"""Read PDFs from the user's working folder into ORPHEUS (free tools only).

What this module does — deterministically, without any language model:
  1. hashes the file (sha256) and skips files already ingested (same bytes = same document);
  2. copies the file into the content-addressed archive (optional);
  3. extracts the text page by page with pypdf, then normalises it for search (NORMALIZER below):
     typographic ligatures expanded (ﬁ → fi) and words broken across lines rejoined only when the
     same document proves the joined form (see `rejoin_hyphenated`);
  4. records a source_snapshot (provenance), a document, its pages and every DOI string found;
  5. proposes a DOI *candidate* for the document itself (PDF metadata first, then page 1);
  6. writes an audit event.

What it deliberately does NOT do: decide that the PDF *is* a given article. The candidate DOI
is verified later against Crossref/PubMed; only then is an `article` row linked.
Scanned PDFs (no text layer) are flagged `no_text` for OCR rather than guessed at.
"""

from __future__ import annotations

import hashlib
import logging
import json
import re
import shutil
import sqlite3
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import pypdf

from . import audit
from .db import transaction
from .identifiers import IdentifierError, normalize_doi
from .ids import new_id

# pypdf reports recoverable font/encoding problems as warnings on the console: keep the console for
# ORPHEUS's own report (text quality is measured by `orpheus quality-report` instead).
logging.getLogger("pypdf").setLevel(logging.ERROR)

NORMALIZER = "normalize-v1 (ligatures, line-break hyphens)"
EXTRACTOR = f"pypdf {pypdf.__version__}; {NORMALIZER}"
_DOI_IN_TEXT = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>]+)", re.IGNORECASE)
_TRAILING = ".,;:)]}'”’"
# Typographic ligatures (U+FB00–FB06). Elsevier PDFs use them on almost every page ("inﬂuence",
# "signiﬁcant"): left as they are, a search for "influence" would miss those pages.
_LIGATURES = str.maketrans({"\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi",
                            "\ufb04": "ffl", "\ufb05": "st", "\ufb06": "st"})
_LINE_BREAK_HYPHEN = re.compile(r"([A-Za-z]+)-\n([a-z]+)")
# Some PDFs render a ligature as "fi" plus a space: "signifi cant", "benefi ts".
_SPLIT_LIGATURE = re.compile(r"\b([A-Za-z]*(?:ffi|ffl|fi|fl|ff)) ([a-z]+)\b")
_WORD = re.compile(r"[A-Za-z]+(?:-[A-Za-z]+)*")
# Elsevier prints an article's own DOI right above the "ISSN/© year publisher" line.
_OWN_DOI_BEFORE_COPYRIGHT = re.compile(
    r"(10\.\d{4,9}/[^\s\"<>]+?)[.,;]?\s{0,3}\d{4}-\d{3}[\dXx]\s*/\s*(?:©|\(c\)|\$)", re.IGNORECASE)
# pypdf can read the last digits of an Elsevier DOI as a separate word: "…/j.fas.2021.05.01 1" for …05.011 (real FAS
# PDFs, 2026-10-14). Modern Elsevier DOIs end with a 3-digit article number, so the pieces are joined only when they
# complete it exactly; anything else is left as printed (and verify-dois still checks the DOI against Crossref).
_SPLIT_ELSEVIER_DOI = re.compile(r"(10\.1016/j\.[a-z]+\.\d{4}\.\d{2}\.)(\d{1,2}) (\d{1,2})(?=\s|$)", re.IGNORECASE)
# Old Elsevier PDFs carry only the PII (e.g. "PII: S1440-2440(04)80030-6"); its DOI is 10.1016/<PII>.
_PII = re.compile(r"\bPII:?\s*(S\d{4}-\d{3}[\dX]\(\d{2}\)\d{5}-[\dX])", re.IGNORECASE)


@dataclass
class IngestResult:
    path: Path
    status: str                      # ingested | duplicate | error
    document_id: str | None = None
    sha256: str | None = None
    pages: int = 0
    text_status: str | None = None
    doi_candidate: str | None = None
    doi_candidate_source: str | None = None
    doi_candidate_note: str | None = None
    other_dois: list[str] = field(default_factory=list)
    archive_restored: bool = False
    error: str | None = None


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def join_split_dois(text: str) -> str:
    """The text with Elsevier DOIs whose last digits were split off rejoined (see _SPLIT_ELSEVIER_DOI)."""
    def fix(m: re.Match) -> str:
        return m.group(1) + m.group(2) + m.group(3) if len(m.group(2)) + len(m.group(3)) == 3 else m.group(0)
    return _SPLIT_ELSEVIER_DOI.sub(fix, text or "")


def find_dois(text: str) -> list[str]:
    """All syntactically valid DOIs in the text, normalised, in order of first appearance."""
    seen: list[str] = []
    for m in _DOI_IN_TEXT.finditer(join_split_dois(text)):
        raw = m.group(1).rstrip(_TRAILING)
        try:
            doi = normalize_doi(raw)
        except IdentifierError:
            continue
        if doi not in seen:
            seen.append(doi)
    return seen


def _clean(text: str) -> str:
    return unicodedata.normalize("NFC", text or "").replace("\x00", "").translate(_LIGATURES)


def rejoin_hyphenated(pages: list[str]) -> tuple[list[str], dict]:
    """Rejoin words split across lines ("veloc-\\nity") using only evidence from the same document.

    - the joined form ("velocity") appears elsewhere in the document  -> join;
    - the hyphenated form ("three-dimensional") appears elsewhere     -> keep the hyphen, drop the break;
    - neither                                                        -> leave the text unchanged.
    Also "signifi cant" -> "significant" (ligature rendered with a space), only when the joined word
    appears elsewhere in the document and neither piece is a word on its own ("off and" stays).
    Deterministic: the result depends only on this document's own text, never on other documents.
    """
    full = "\n".join(pages)
    plain = set(re.findall(r"[A-Za-z]+", _LINE_BREAK_HYPHEN.sub(" ", full)))
    compounds = set(_WORD.findall(_LINE_BREAK_HYPHEN.sub(" ", full)))
    stats = {"joined": 0, "kept_hyphen": 0, "unresolved": 0, "ligature_spaces": 0}

    def fix(m: re.Match) -> str:
        left, right = m.group(1), m.group(2)
        if left + right in plain:
            stats["joined"] += 1
            return left + right
        if f"{left}-{right}" in compounds:
            stats["kept_hyphen"] += 1
            return f"{left}-{right}"
        stats["unresolved"] += 1
        return m.group(0)

    # vocabulary without the candidate pieces themselves ("signifi", "cant" must not count as evidence)
    rest = set(re.findall(r"[A-Za-z]+", _SPLIT_LIGATURE.sub(" ", _LINE_BREAK_HYPHEN.sub(" ", full))))

    def fix_ligature(m: re.Match) -> str:
        left, right = m.group(1), m.group(2)
        if left + right in rest and left not in rest and right not in rest:
            stats["ligature_spaces"] += 1
            return left + right
        return m.group(0)

    out = [_SPLIT_LIGATURE.sub(fix_ligature, _LINE_BREAK_HYPHEN.sub(fix, t)) for t in pages]
    return out, stats


def _metadata(reader: pypdf.PdfReader) -> dict[str, str]:
    meta = reader.metadata or {}
    out = {}
    for key in ("/Title", "/Author", "/Subject", "/doi", "/DOI", "/Keywords"):
        value = meta.get(key)
        if value:
            out[key] = str(value)
    return out


def choose_doi_candidate(meta: dict[str, str], first_page_dois: list[str],
                         first_page_text: str = "") -> tuple[str | None, str, str | None]:
    """(candidate, source, note). Order: explicit metadata, other metadata fields, the PII, then page 1.

    Metadata comes first because on conference-abstract pages the only DOI printed on page 1 can
    belong to the PREVIOUS abstract (seen on real JSAMS supplements).
    """
    for key in ("/doi", "/DOI", "/Subject", "/Keywords", "/Title"):
        found = find_dois(meta.get(key, ""))
        if len(found) == 1:
            return found[0], "pdf_metadata", None
    for key in ("/Title", "/Subject"):
        m = _PII.search(meta.get(key, ""))
        if m:
            return normalize_doi("10.1016/" + m.group(1)), "pdf_metadata", "derivato dal PII (da verificare)"
    if len(first_page_dois) == 1:
        return first_page_dois[0], "first_page", None
    if len(first_page_dois) > 1:
        own = {normalize_doi(m.group(1).rstrip(_TRAILING))
               for m in _OWN_DOI_BEFORE_COPYRIGHT.finditer(join_split_dois(first_page_text))}
        own &= set(first_page_dois)
        if len(own) == 1:
            return own.pop(), "first_page", "DOI stampato sopra la riga ISSN/©"
        return None, "ambiguous", None
    return None, "none", None


def archive_path(archive_dir: Path, sha: str) -> Path:
    return archive_dir / sha[:2] / sha[2:4] / f"{sha}.pdf"


def portable(path: Path, home: Path | None) -> str:
    """Path as stored in the database: POSIX and relative to ORPHEUS_HOME when inside it
    (valid on every PC and drive letter), otherwise absolute."""
    resolved = Path(path).resolve()
    if home is not None:
        try:
            return resolved.relative_to(Path(home).resolve()).as_posix()
        except ValueError:
            pass
    return str(resolved)


def _read_pages(path: Path) -> tuple[list[str], dict]:
    reader = pypdf.PdfReader(str(path))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as exc:  # password-protected: report, never bypass
            raise ValueError("encrypted PDF: provide an unprotected copy") from exc
    return rejoin_hyphenated([_clean(p.extract_text() or "") for p in reader.pages])


def _complete_shared(conn: sqlite3.Connection, path: Path, sha: str, doc_id: str, result: "IngestResult", *,
                     archive_dir: Path | None, home: Path | None, actor: str) -> "IngestResult":
    """Pages of a document received without them (sharing): same bytes, so only the pages and the archive copy are
    added; the document row (identity, article, checks) stays as it is."""
    pages, hyphen_stats = _read_pages(path)
    doc = conn.execute("SELECT page_count, source_snapshot_id FROM document WHERE id = ?", (doc_id,)).fetchone()
    if len(pages) != doc["page_count"]:
        raise ValueError(f"same file but {len(pages)} pages instead of {doc['page_count']}: not completed")
    stored = None
    if archive_dir is not None:
        stored = archive_path(Path(archive_dir), sha)
        stored.parent.mkdir(parents=True, exist_ok=True)
        if not stored.exists():
            shutil.copy2(path, stored)
    with transaction(conn):
        conn.executemany("INSERT INTO document_page (document_id, page_no, text, char_count) VALUES (?, ?, ?, ?)",
                         [(doc_id, i + 1, t, len(t)) for i, t in enumerate(pages)])
        conn.executemany("INSERT OR IGNORE INTO document_doi (document_id, page_no, doi) VALUES (?, ?, ?)",
                         [(doc_id, i + 1, d) for i, t in enumerate(pages) for d in find_dois(t)])
        if stored is not None:
            conn.execute("UPDATE source_snapshot SET storage_path = ? WHERE id = ? AND storage_path IS NULL",
                         (portable(stored, home), doc["source_snapshot_id"]))
        audit.record(conn, actor=actor, action="complete_shared_document", entity_type="document", entity_id=doc_id,
                     payload={"sha256": sha, "pages": len(pages), "hyphenation": hyphen_stats,
                              "path": portable(path, home)}, in_transaction=True)
    nonempty = sum(1 for t in pages if t.strip())
    result.status, result.document_id, result.pages = "completed", doc_id, len(pages)
    result.text_status = "no_text" if nonempty == 0 else ("ok" if nonempty == len(pages) else "partial")
    return result


def ingest_pdf(conn: sqlite3.Connection, path: Path, *, kind: str = "article",
               archive_dir: Path | None = None, home: Path | None = None,
               journal_hint: str | None = None, actor: str = "cli") -> IngestResult:
    path = Path(path)
    result = IngestResult(path=path, status="error")
    try:
        sha = sha256_file(path)
        result.sha256 = sha
        existing = conn.execute("SELECT id FROM document WHERE file_sha256 = ?", (sha,)).fetchone()
        if existing and not conn.execute("SELECT 1 FROM document_page WHERE document_id = ? LIMIT 1",
                                         (existing["id"],)).fetchone():
            # A document received from a colleague (sharing, T-053) has no pages: this PDF is the same file
            # (same SHA-256), so its pages are read now and the document becomes a normal one.
            return _complete_shared(conn, path, sha, existing["id"], result, archive_dir=archive_dir, home=home,
                                    actor=actor)
        if existing:
            result.status, result.document_id = "duplicate", existing["id"]
            if archive_dir is not None:
                # Same bytes as an archived copy that went missing (e.g. after a disk failure):
                # put the copy back. Content-addressed, so this cannot change what the database says.
                stored = archive_path(Path(archive_dir), sha)
                if not stored.exists():
                    stored.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, stored)
                    result.archive_restored = True
            return result

        reader = pypdf.PdfReader(str(path))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as exc:  # password-protected: report, never bypass
                raise ValueError("encrypted PDF: provide an unprotected copy") from exc
        pages, hyphen_stats = rejoin_hyphenated([_clean(p.extract_text() or "") for p in reader.pages])
        meta = _metadata(reader)

        nonempty = sum(1 for t in pages if t.strip())
        text_status = "no_text" if nonempty == 0 else ("ok" if nonempty == len(pages) else "partial")
        page_dois = [(i + 1, find_dois(t)) for i, t in enumerate(pages)]
        first_page = page_dois[0][1] if page_dois else []
        candidate, cand_source, cand_note = ((None, "none", None) if kind != "article"
                                             else choose_doi_candidate(meta, first_page, pages[0] if pages else ""))

        stored = None
        if archive_dir is not None:
            stored = archive_path(Path(archive_dir), sha)
            stored.parent.mkdir(parents=True, exist_ok=True)
            if not stored.exists():
                shutil.copy2(path, stored)

        snap_id, doc_id = new_id("source_snapshot"), new_id("document")
        original = portable(path, home)
        request_uri = ("home:" + original) if home is not None and not Path(original).is_absolute() else path.resolve().as_uri()
        with transaction(conn):
            conn.execute(
                """INSERT INTO source_snapshot (id, source_id, request_uri, retrieved_at, content_sha256,
                       content_type, storage_path, size_bytes, licence, storage_allowed)
                   VALUES (?, 'local_pdf', ?, ?, ?, 'application/pdf', ?, ?, ?, 1)""",
                (snap_id, request_uri, audit.utc_now(), sha,
                 portable(stored, home) if stored else None, path.stat().st_size, "user-provided copy (private use)"),
            )
            conn.execute(
                """INSERT INTO document (id, source_snapshot_id, kind, original_path, file_sha256, page_count,
                       extractor, text_status, pdf_title, pdf_author, doi_candidate, doi_candidate_source, journal_hint)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (doc_id, snap_id, kind, original, sha, len(pages), EXTRACTOR, text_status,
                 meta.get("/Title"), meta.get("/Author"), candidate, cand_source, journal_hint),
            )
            conn.executemany(
                "INSERT INTO document_page (document_id, page_no, text, char_count) VALUES (?, ?, ?, ?)",
                [(doc_id, i + 1, t, len(t)) for i, t in enumerate(pages)],
            )
            conn.executemany(
                "INSERT INTO document_doi (document_id, page_no, doi) VALUES (?, ?, ?)",
                [(doc_id, page_no, d) for page_no, dois in page_dois for d in dois],
            )
            audit.record(conn, actor=actor, action="ingest_pdf", entity_type="document", entity_id=doc_id,
                         payload={"sha256": sha, "pages": len(pages), "kind": kind, "text_status": text_status,
                                  "doi_candidate": candidate, "doi_candidate_source": cand_source,
                                  "doi_candidate_note": cand_note, "hyphenation": hyphen_stats,
                                  "extractor": EXTRACTOR, "path": original, "journal_hint": journal_hint},
                         in_transaction=True)

        all_dois = [d for _, dois in page_dois for d in dois]
        result.status, result.document_id = "ingested", doc_id
        result.pages, result.text_status = len(pages), text_status
        result.doi_candidate, result.doi_candidate_source = candidate, cand_source
        result.doi_candidate_note = cand_note
        result.other_dois = sorted(set(all_dois) - {candidate})
        return result
    except Exception as exc:
        result.error = f"{type(exc).__name__}: {exc}"
        return result


def ingest_paths(conn: sqlite3.Connection, paths: list[Path], **kwargs) -> list[IngestResult]:
    """Ingest files and every *.pdf found (recursively) in the given folders."""
    files: list[Path] = []
    for p in map(Path, paths):
        if p.is_dir():
            files += sorted(q for q in p.rglob("*") if q.suffix.lower() == ".pdf" and q.is_file())
        else:
            files.append(p)
    return [ingest_pdf(conn, f, **kwargs) for f in files]


def fts_query(text: str) -> str:
    """Turn free text into a safe FTS5 query: every word quoted, all words required, and every word
    also matched as a prefix ("marker" finds "markers", "basket" finds "basketball")."""
    words = re.findall(r"\w[\w\-]*", text, flags=re.UNICODE)
    return " ".join('"' + w.replace('"', '""') + '"*' for w in words)


def _excluded(exclude) -> str:
    return json.dumps(sorted(exclude or []))


def search_pages(conn: sqlite3.Connection, text: str, limit: int = 20, exclude=None) -> list[dict]:
    """`exclude`: document ids left out (by default the CLI leaves out what is not a complete article)."""
    q = fts_query(text)
    if not q:
        return []
    rows = conn.execute(
        """SELECT d.id AS document_id, d.kind, d.original_path, p.page_no, d.doi_candidate, d.pdf_title,
                  snippet(page_fts, 0, '[', ']', ' … ', 12) AS snippet
           FROM page_fts
           JOIN document_page p ON p.rowid = page_fts.rowid
           JOIN document d ON d.id = p.document_id
           WHERE page_fts MATCH ? AND d.id NOT IN (SELECT value FROM json_each(?))
           ORDER BY bm25(page_fts) LIMIT ?""",
        (q, _excluded(exclude), limit),
    )
    return [dict(r) for r in rows]


def search_documents(conn: sqlite3.Connection, text: str, limit: int = 15, exclude=None) -> list[dict]:
    """Same search, one line per document: best page first, plus every page that matches.

    Ranking: the document's best page (bm25), so one long article cannot fill the whole list.
    """
    q = fts_query(text)
    if not q:
        return []
    rows = conn.execute(
        """SELECT d.id AS document_id, d.kind, d.original_path, d.doi_candidate, d.pdf_title, p.page_no,
                  bm25(page_fts) AS score, snippet(page_fts, 0, '[', ']', ' … ', 12) AS snippet
           FROM page_fts
           JOIN document_page p ON p.rowid = page_fts.rowid
           JOIN document d ON d.id = p.document_id
           WHERE page_fts MATCH ? AND d.id NOT IN (SELECT value FROM json_each(?))
           ORDER BY bm25(page_fts)""",
        (q, _excluded(exclude)),
    ).fetchall()
    docs: dict[str, dict] = {}
    for r in rows:                       # rows arrive best-first: the first page seen is the best one
        d = docs.get(r["document_id"])
        if d is None:
            d = docs[r["document_id"]] = {
                "document_id": r["document_id"], "kind": r["kind"], "original_path": r["original_path"],
                "doi_candidate": r["doi_candidate"], "best_page": r["page_no"], "snippet": r["snippet"], "pages": []}
        d["pages"].append(r["page_no"])
    out = list(docs.values())[:limit]
    for d in out:
        d["pages"].sort()
    return out
