"""Bibliographic records for a systematic review (Tabula §4.1–4.2): parsers for RIS, PubMed .nbib, Web of Science
plain text and CSV/TSV exports (standard library only), the deduplication key and deduplication.

The format is recognised from the content, not from the file extension (except CSV/TSV, which have no marker).
A parsed record is a dict with exactly: title, abstract, authors, year, journal, doi, pmid (strings, "" if absent),
truncated as in Tabula (title 600, abstract 5000, authors 600, journal 200 characters)."""
from __future__ import annotations

import csv
import io
import re
import unicodedata
from collections.abc import Iterable

FIELDS = ("title", "abstract", "authors", "year", "journal", "doi", "pmid")
LIMITS = {"title": 600, "abstract": 5000, "authors": 600, "journal": 200}

# ---------------------------------------------------------------- deduplication key (§4.1)

_DOI_PREFIX = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.I)
_DASHES = str.maketrans({c: "-" for c in "‐‑‒–—―−﹣－"})
_NOT_KEY = re.compile(r"[^a-z0-9.%+]+")


def clean_doi(doi: str | None) -> str:
    """DOI in lower case without the resolver prefix (https://doi.org/, http://dx.doi.org/, doi:) and spaces."""
    d = (doi or "").strip()
    d = _DOI_PREFIX.sub("", d).strip().lower()
    return d.rstrip(".,;")


def norm_title(text: str | None) -> str:
    """Aggressive normalisation of a title for the key: lower case, accents removed (NFD), unicode dashes unified,
    every character other than [a-z0-9.%+] (hyphen included) -> one space, trimmed. "In-shoe" -> "in shoe"
    (value of Tabula §8.2, obtained by running the published code)."""
    t = unicodedata.normalize("NFD", (text or "").lower())
    t = "".join(ch for ch in t if not unicodedata.combining(ch)).translate(_DASHES)
    return _NOT_KEY.sub(" ", t).strip()


def rec_key(r: dict) -> str:
    """Deduplication key: "doi:<doi>" when the record has a DOI, else "t:<first 140 chars of the normalised
    title>|<year>". Same title with different years -> different keys (not a duplicate)."""
    doi = clean_doi(r.get("doi"))
    if doi:
        return "doi:" + doi
    return "t:" + norm_title(r.get("title"))[:140] + "|" + str(r.get("year") or "")


def match_title(text: str | None) -> str:
    """Title used to match records across databases (EURYDIKE, decision of the user of 2026-11-03): `norm_title`
    without the final full stop. PubMed ends titles with "." and puts translated titles in [ ] ("[Title]."), Scopus
    does not: with `norm_title` alone the same article had two keys (3 duplicates missed in a real import)."""
    return norm_title(text).rstrip(" .")


def match_keys(r: dict) -> list[tuple[str, str]]:
    """Keys of a record for duplicate matching, as (reason, key), strongest first: DOI, PMID, title and year.
    The title key exists for every record with a title, also when it has a DOI."""
    keys = []
    doi = clean_doi(r.get("doi"))
    if doi:
        keys.append(("doi", "doi:" + doi))
    pmid = re.sub(r"\D", "", str(r.get("pmid") or ""))
    if pmid:
        keys.append(("pmid", "pmid:" + pmid))
    title = match_title(r.get("title"))
    if title:
        keys.append(("titolo", "t:" + title[:140] + "|" + str(r.get("year") or "")))
    return keys


class DedupIndex:
    """Duplicates by DOI, PMID or title and year (EURYDIKE, 2026-11-03). A record is a duplicate of the first record
    that shares one of its keys. The same DOI is always a duplicate; a match by PMID or title is not when the two
    have different DOIs or different PMIDs (different articles with the same title: an erratum, a reprint, a chapter
    with a common title)."""

    def __init__(self) -> None:
        self._first: dict[str, str] = {}
        self._ids: dict[str, tuple[str, str]] = {}

    @staticmethod
    def _ids_of(r: dict) -> tuple[str, str]:
        return clean_doi(r.get("doi")), re.sub(r"\D", "", str(r.get("pmid") or ""))

    def find(self, r: dict) -> tuple[str | None, str | None]:
        """(id of the record r duplicates, reason) or (None, None)."""
        doi, pmid = self._ids_of(r)
        for reason, key in match_keys(r):
            rid = self._first.get(key)
            if rid is None:
                continue
            odoi, opmid = self._ids[rid]
            if reason != "doi" and ((doi and odoi and doi != odoi) or (pmid and opmid and pmid != opmid)):
                continue
            return rid, reason
        return None, None

    def add(self, rid: str, r: dict) -> None:
        """Register a record kept (not a duplicate); the first record with a key keeps it."""
        self._ids[rid] = self._ids_of(r)
        for _, key in match_keys(r):
            self._first.setdefault(key, rid)


def dedupe(records: Iterable[dict], seen: set[str] | None = None) -> tuple[list[dict], int]:
    """Records not already present (by `rec_key`) among `seen` (keys of every record already imported, in any
    batch) nor earlier in the same file. Returns (kept, number of duplicates); `seen` is updated in place."""
    seen = set() if seen is None else seen
    kept, dupes = [], 0
    for r in records:
        k = rec_key(r)
        if k in seen:
            dupes += 1
            continue
        seen.add(k)
        kept.append(r)
    return kept, dupes


# ---------------------------------------------------------------- tagged formats (§4.2)

_RIS_LINE = re.compile(r"^([A-Z][A-Z0-9])  - ?(.*)$")
_NBIB_LINE = re.compile(r"^([A-Z]{2,4})\s*- (.*)$")
_WOS_LINE = re.compile(r"^([A-Z][A-Z0-9]) (.*)$")
_CONT = re.compile(r"^ {2,}(\S.*)$")
_WOS_LIST_TAGS = {"AU", "AF", "DE"}


def detect_format(text: str, filename: str | None = None) -> str | None:
    """'ris', 'nbib', 'wos', 'csv', 'tsv' or None. Tagged formats from the content; CSV/TSV from the extension or,
    without one, from the first line (tabs -> tsv, commas or semicolons -> csv)."""
    if re.search(r"^\s*TY  -", text, re.M):
        return "ris"
    if re.search(r"^PMID- ", text, re.M):
        return "nbib"
    if re.search(r"^(?:FN |PT )", text, re.M):
        return "wos"
    ext = (filename or "").lower().rsplit(".", 1)[-1] if filename and "." in filename else ""
    if ext in ("csv", "tsv", "txt"):
        if ext == "tsv":
            return "tsv"
        first = text.lstrip("﻿").splitlines()[0] if text.strip() else ""
        return "tsv" if "\t" in first else "csv"
    if ext in ("xlsx", "xls"):
        return None
    first = text.lstrip("﻿").splitlines()[0] if text.strip() else ""
    if "\t" in first:
        return "tsv"
    if "," in first or ";" in first:
        return "csv"
    return None


def parse_tagged(text: str, fmt: str) -> list[dict]:
    """Records of a RIS ('ris'), PubMed ('nbib') or Web of Science ('wos') export, mapped to FIELDS.

    Separators: RIS `TY` opens and `ER` closes; nbib `PMID` opens; WoS `PT` opens and `ER`/`EF` close.
    A line starting with at least two spaces continues the previous tag (joined with a space); in WoS the tags
    AU, AF and DE start a new element instead (one author or keyword per line)."""
    line_re = {"ris": _RIS_LINE, "nbib": _NBIB_LINE, "wos": _WOS_LINE}[fmt]
    records: list[dict[str, list[str]]] = []
    cur: dict[str, list[str]] | None = None
    last: str | None = None

    def close():
        nonlocal cur, last
        if cur:
            records.append(cur)
        cur, last = None, None

    for raw in text.lstrip("﻿").splitlines():
        line = raw.rstrip("\r\n")
        m = line_re.match(line)
        if m and not (fmt == "wos" and line.startswith(" ")):
            tag, val = m.group(1), m.group(2).strip()
            if fmt == "ris" and tag == "TY":
                close()
                cur = {}
            elif fmt == "nbib" and tag == "PMID":
                close()
                cur = {}
            elif fmt == "wos" and tag == "PT":
                close()
                cur = {}
            elif (fmt == "ris" and tag == "ER") or (fmt == "wos" and tag in ("ER", "EF")):
                close()
                continue
            if fmt == "wos" and tag in ("FN", "VR"):
                continue
            if cur is None:
                cur = {}
            cur.setdefault(tag, []).append(val)
            last = tag
            continue
        if fmt == "wos" and line in ("ER", "EF"):
            close()
            continue
        c = _CONT.match(line)
        if c and cur is not None and last is not None:
            val = c.group(1).strip()
            if fmt == "wos" and last in _WOS_LIST_TAGS:
                cur[last].append(val)
            else:
                cur[last][-1] = (cur[last][-1] + " " + val).strip()
    close()
    return [_map(r) for r in records]


def _first(r: dict[str, list[str]], tags: tuple[str, ...]) -> list[str]:
    for t in tags:
        vals = [v for v in r.get(t, []) if v.strip()]
        if vals:
            return vals
    return []


_DOI_IN = re.compile(r"10\.\d{4,9}/\S+")


def _map(r: dict[str, list[str]]) -> dict:
    title = " ".join(_first(r, ("TI", "T1", "CT")))
    abstract = " ".join(v for t in ("AB", "N2") for v in r.get(t, []) if v.strip())
    authors = "; ".join(_first(r, ("AU", "A1", "FAU", "AF")))
    year = ""
    for v in _first(r, ("PY", "Y1", "DA", "DP")):
        m = re.search(r"\d{4}", v)
        if m:
            year = m.group(0)
            break
    journal = " ".join(_first(r, ("JO", "JF", "T2", "JT", "SO", "TA", "JA"))[:1])
    doi = (_first(r, ("DO", "DI")) or [""])[0]
    if not doi:
        for v in r.get("LID", []) + r.get("AID", []):
            if "[doi]" in v.lower():
                m = _DOI_IN.search(v)
                doi = m.group(0) if m else v.split("[")[0]
                break
    pmid = (_first(r, ("PMID", "PM")) or [""])[0]
    out = _limit({"title": title, "abstract": abstract, "authors": authors, "year": year, "journal": journal,
                  "doi": clean_doi(doi), "pmid": pmid.strip()})
    out["pub_types"] = pub_types(r)
    return out


RIS_TYPES = {"CONF": "Conference Paper", "CPAPER": "Conference Paper", "BOOK": "Book", "CHAP": "Book Chapter",
             "THES": "Thesis", "RPRT": "Report", "EDBOOK": "Book", "ABST": "Abstract", "NEWS": "News"}


def split_types(values) -> list[str]:
    """Publication types without repeats (order kept); "Review; Editorial" counts as two."""
    out: list[str] = []
    for v in values or []:
        for t in re.split(r"[;|]", v or ""):
            t = re.sub(r"\s+", " ", t).strip()[:80]
            if t and t.lower() not in [x.lower() for x in out]:
                out.append(t)
    return out[:12]


def pub_types(r: dict[str, list[str]]) -> list[str]:
    """Publication types of a tagged record: PubMed PT, RIS M3 (Scopus document type) or the RIS TY code when it is
    not a journal article, Web of Science DT."""
    if r.get("PT") and r.get("PMID"):
        return split_types(r["PT"])
    vals = list(r.get("M3", [])) + list(r.get("DT", []))
    if not vals:
        ty = (r.get("TY") or [""])[0].strip().upper()
        vals = [RIS_TYPES[ty]] if ty in RIS_TYPES else []
    return split_types(vals)


def _limit(rec: dict) -> dict:
    out = {k: (rec.get(k) or "").strip() for k in FIELDS}
    for k, n in LIMITS.items():
        out[k] = out[k][:n]
    return out


# ---------------------------------------------------------------- CSV / TSV (Italian and English headers)

HEADERS = {
    "title": ("title", "article title", "document title", "ti", "titolo", "titolo dell'articolo"),
    "abstract": ("abstract", "ab", "riassunto", "sommario"),
    "authors": ("authors", "author", "author full names", "au", "autori", "autore"),
    "year": ("year", "publication year", "py", "anno", "anno di pubblicazione", "pubyear"),
    "journal": ("journal", "source title", "source", "publication title", "journal/book", "so", "rivista", "fonte"),
    "doi": ("doi", "di"),
    "pmid": ("pmid", "pubmed id", "pm"),
    "pub_types": ("document type", "publication type", "publication types", "dt", "pt", "tipo di documento",
                  "tipo di pubblicazione", "tipo"),
}


def parse_table(text: str, fmt: str = "csv") -> list[dict]:
    """Records of a CSV (comma or semicolon, detected) or TSV export with a header row; columns recognised by name
    without case distinction among Italian and English alternatives (HEADERS). Rows without a title are skipped."""
    text = text.lstrip("﻿")
    if fmt == "tsv":
        delim = "\t"
    else:
        first = text.splitlines()[0] if text.strip() else ""
        delim = ";" if first.count(";") > first.count(",") else ","
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    rows = list(reader)
    if not rows:
        return []
    names = [h.strip().lower() for h in rows[0]]
    col = {}
    for field, alts in HEADERS.items():
        for i, h in enumerate(names):
            if h in alts:
                col[field] = i
                break
    out = []
    for row in rows[1:]:
        rec = {f: (row[i] if i < len(row) else "") for f, i in col.items()}
        if not (rec.get("title") or "").strip():
            continue
        m = re.search(r"\d{4}", rec.get("year") or "")
        rec["year"] = m.group(0) if m else ""
        rec["doi"] = clean_doi(rec.get("doi"))
        types = split_types([rec.get("pub_types") or ""])
        rec = _limit(rec)
        rec["pub_types"] = types
        out.append(rec)
    return out


def parse_records(text: str, filename: str | None = None) -> tuple[str, list[dict]]:
    """(format, records) of an exported file. Raises ValueError (Italian message) when the format is unknown."""
    fmt = detect_format(text, filename)
    if fmt in ("ris", "nbib", "wos"):
        return fmt, parse_tagged(text, fmt)
    if fmt in ("csv", "tsv"):
        return fmt, parse_table(text, fmt)
    raise ValueError("formato non riconosciuto: servono RIS, PubMed (.nbib), Web of Science (testo) o CSV/TSV; "
                     "un file Excel va salvato come CSV")
