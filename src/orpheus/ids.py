"""Persistent, time-sortable, prefixed identifiers for ORPHEUS entities.

Format: ``<prefix>_<ULID>`` — e.g. ``art_01JA2B3C4D5E6F7G8H9JKMNPQR``.
A ULID is 48 bits of millisecond timestamp + 80 random bits, encoded in
Crockford base32 (26 chars), so IDs sort by creation time and never collide
in practice. IDs are generated once and never reused or re-derived.
"""

from __future__ import annotations

import os
import re
import time

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

# One prefix per entity; adding an entity means adding a prefix here.
PREFIXES: dict[str, str] = {
    "project": "prj",
    "research_domain": "dom",
    "source_snapshot": "snp",
    "document": "doc",
    "doi_check": "dck",
    "pubmed_check": "pck",
    "fulltext_check": "ftc",
    "article_card": "crd",
    "journal_card": "jcd",
    "journal_card_manual": "jcm",
    "journal": "jrn",
    "journal_policy_version": "jpv",
    "journal_metric": "jmt",
    "article": "art",
    "article_version": "arv",
    "author": "aut",
    "citation": "cit",
    "search_strategy": "sst",
    "search_run": "srn",
    "scientific_claim": "clm",
    "evidence_record": "evd",
    "agent": "agt",
    "model": "mdl",
    "task": "tsk",
    "workflow_run": "run",
    "validation_result": "val",
    "alert": "alr",
    "audit_event": "aev",
    "segment": "seg",
    "text_method_item": "tmi",  # T-057 (migration 0016)
    "my_manuscript": "msr",  # T-056 (migration 0017)
    "submission": "sub",
    "submission_letter": "sle",
    "submission_event": "sev",
    "reviewer_comment": "rvc",
    "comment_label": "cml",
    # EURYDIKE (migration 0014)
    "card_item": "cdi",
    "card_outcome_row": "cor",
    "review": "rev",
    "review_criterion": "rcr",
    "review_reviewer": "rvw",
    "review_log": "rlg",
    "search_import": "imp",
    "review_record": "rrc",
    "screening_decision": "sdc",
    "review_study": "rst",
    "ft_proposal": "ftp",
    "extraction_form": "frm",
    "extraction_value": "exv",
    "appraisal_tool_choice": "atc",
    "appraisal": "apr",
    "ma_analysis": "mas",
}

_ID_RE = re.compile(r"^([a-z]{3})_([0-9A-HJKMNP-TV-Z]{26})$")


def _encode(value: int, length: int) -> str:
    out = []
    for _ in range(length):
        out.append(_CROCKFORD[value & 31])
        value >>= 5
    return "".join(reversed(out))


def new_ulid(timestamp_ms: int | None = None) -> str:
    ts = int(time.time() * 1000) if timestamp_ms is None else timestamp_ms
    if not 0 <= ts < 2**48:
        raise ValueError("timestamp out of ULID range")
    rand = int.from_bytes(os.urandom(10), "big")
    return _encode(ts, 10) + _encode(rand, 16)


def new_id(entity: str, timestamp_ms: int | None = None) -> str:
    try:
        prefix = PREFIXES[entity]
    except KeyError:
        raise ValueError(f"unknown entity type: {entity!r}") from None
    return f"{prefix}_{new_ulid(timestamp_ms)}"


def entity_of(identifier: str) -> str:
    """Return the entity type encoded in an ID, or raise ValueError."""
    m = _ID_RE.match(identifier or "")
    if not m:
        raise ValueError(f"malformed ORPHEUS id: {identifier!r}")
    for entity, prefix in PREFIXES.items():
        if prefix == m.group(1):
            return entity
    raise ValueError(f"unknown id prefix: {m.group(1)!r}")


def timestamp_ms_of(identifier: str) -> int:
    m = _ID_RE.match(identifier or "")
    if not m:
        raise ValueError(f"malformed ORPHEUS id: {identifier!r}")
    value = 0
    for ch in m.group(2)[:10]:
        value = value * 32 + _CROCKFORD.index(ch)
    return value
