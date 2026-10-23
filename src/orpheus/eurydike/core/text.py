"""Page text of a full-text PDF (Tabula §4.3 and §4.8): cutting the reference list, blocks of pages within a byte
budget for the model, and finding a quote in the pages.

`find_quote` first checks the quote EXACTLY with the same normalisation as the ORPHEUS card validators
(`orpheus.cards.squash` / `text_forms`: NFKC, unified dashes and quotes, no case, no whitespace, line-end hyphens
joined), which is stricter than Tabula's; only if that fails it falls back to Tabula's approximate match
(shingles of 5 words, threshold 0.60), returned as `exact: False`. An approximate match is NEVER enough for a
bulk confirmation (principle 2 of the plan): only `found and exact` is."""
from __future__ import annotations

import re
import unicodedata

from ...cards import squash, text_forms

# ---------------------------------------------------------------- reference list

_REF_HEADING = re.compile(r"^[ \t]*(references|bibliography|bibliografia|literature cited|riferimenti bibliografici)"
                          r"[ \t]*:?[ \t]*$", re.I | re.M)
REF_THRESHOLD = 0.45


def trim_references(pages: list[str]) -> tuple[list[str], int | None]:
    """Pages without the reference list. The cut is at the first isolated heading (References, Bibliography,
    Bibliografia, Literature cited, Riferimenti bibliografici) that falls beyond 45% of the characters of the whole
    document, so that a "References" early in the article does not cut it. Returns (pages, page number of the cut,
    1-based) or (pages unchanged, None)."""
    total = sum(len(p) for p in pages)
    if not total:
        return list(pages), None
    before = 0
    for i, page in enumerate(pages):
        for m in _REF_HEADING.finditer(page):
            if before + m.start() > REF_THRESHOLD * total:
                return list(pages[:i]) + [page[:m.start()].rstrip()], i + 1
        before += len(page)
    return list(pages), None


# ---------------------------------------------------------------- blocks of pages

SEPARATOR = "\n\n"


def marker(page_number: int) -> str:
    """The page reference the model must cite: [[p. N]]."""
    return f"[[p. {page_number}]]"


def _bytes(s: str) -> int:
    return len(s.encode("utf-8"))


def chunk_pages(pages: list[str], budget: int, first_page: int = 1) -> list[str]:
    """Blocks of consecutive pages, each page preceded by its marker [[p. N]], within `budget` UTF-8 bytes (each
    page counted with its separator, as in Tabula). A single page larger than the budget is cut to 90% of the
    budget and goes in a block of its own."""
    if budget <= 0:
        raise ValueError("il budget deve essere positivo")
    blocks: list[str] = []
    cur: list[str] = []
    size = 0
    for i, text in enumerate(pages, first_page):
        piece = f"{marker(i)}\n{text}"
        need = _bytes(piece) + len(SEPARATOR)
        if need > budget:
            if cur:
                blocks.append(SEPARATOR.join(cur))
                cur, size = [], 0
            cut = piece.encode("utf-8")[: int(budget * 0.9)].decode("utf-8", errors="ignore")
            blocks.append(cut)
            continue
        if cur and size + need > budget:
            blocks.append(SEPARATOR.join(cur))
            cur, size = [], 0
        cur.append(piece)
        size += need
    if cur:
        blocks.append(SEPARATOR.join(cur))
    return blocks


# ---------------------------------------------------------------- finding a quote

MIN_CHARS = 4
MIN_WORDS_FUZZY = 6
SHINGLE = 5
FUZZY_THRESHOLD = 0.60
_NOT_WORD = re.compile(r"[^a-z0-9.%+-]+")


def words(text: str) -> list[str]:
    """Words after Tabula's aggressive normalisation (lower case, no accents, unified dashes, other characters ->
    space); used only by the approximate match."""
    t = unicodedata.normalize("NFD", squash_spaces(text).lower())
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    t = re.sub(r"[‐‑‒–—―−﹣－]", "-", t)
    return [w for w in _NOT_WORD.sub(" ", t).split() if w.strip(".-")]


def squash_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text or "")


def _shingles(ws: list[str]) -> set[tuple[str, ...]]:
    return {tuple(ws[i:i + SHINGLE]) for i in range(len(ws) - SHINGLE + 1)}


def find_quote(pages: list[str], quote: str, hint_page: int | None = None) -> dict | None:
    """Where a quote is in the pages (1-based page numbers), the suggested page first.

    1) exact (ORPHEUS normalisation): {"page": p, "exact": True, "found": True};
    2) otherwise, for quotes of at least 6 words, the page with the highest share of the quote's 5-word shingles,
       if >= 0.60: {"page": p, "exact": False, "found": True, "score": share} (never valid for a bulk confirmation);
    3) otherwise None. Quotes shorter than 4 normalised characters -> None."""
    q = squash(quote)
    if len(q) < MIN_CHARS:
        return None
    order = list(range(1, len(pages) + 1))
    if hint_page in order:
        order.remove(hint_page)
        order.insert(0, hint_page)
    for p in order:
        if any(q in f for f in text_forms(pages[p - 1])):
            return {"page": p, "exact": True, "found": True}
    qw = words(quote)
    if len(qw) < MIN_WORDS_FUZZY:
        return None
    qs = _shingles(qw)
    best, best_page = 0.0, None
    for p in order:
        ps = _shingles(words(pages[p - 1]))
        share = len(qs & ps) / len(qs)
        if share > best:
            best, best_page = share, p
    if best_page is not None and best >= FUZZY_THRESHOLD:
        return {"page": best_page, "exact": False, "found": True, "score": round(best, 3)}
    return None


def bulk_confirmable(result: dict | None) -> bool:
    """True only for a quote found identical (the condition for confirming a value in bulk)."""
    return bool(result and result.get("found") and result.get("exact") is True)
