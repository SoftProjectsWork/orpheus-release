"""Deterministic normalisation and validation of scholarly identifiers.

These checks never call an LLM: DOIs, ISSNs, ORCIDs and PubMed IDs are
validated by syntax and, where the standard defines one, by check digit.
A value that fails validation is rejected, never "repaired" by guessing.
"""

from __future__ import annotations

import re

_DOI_PREFIXES = (
    "https://doi.org/",
    "http://doi.org/",
    "https://dx.doi.org/",
    "http://dx.doi.org/",
    "doi.org/",
    "doi:",
)
_DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")
_ISSN_RE = re.compile(r"^(\d{4})-?(\d{3}[\dX])$")
_ORCID_RE = re.compile(r"^(\d{4})-?(\d{4})-?(\d{4})-?(\d{3}[\dX])$")
_PMID_RE = re.compile(r"^[1-9]\d{0,8}$")
_PMCID_RE = re.compile(r"^PMC[1-9]\d{0,9}$")


class IdentifierError(ValueError):
    """Raised when an identifier is syntactically invalid."""


def normalize_doi(raw: str) -> str:
    """Return the canonical (lower-case, prefix-free) form of a DOI.

    DOIs are case-insensitive, so lower case is the canonical form used as a key.
    """
    if raw is None:
        raise IdentifierError("DOI is None")
    value = raw.strip()
    lowered = value.lower()
    for prefix in _DOI_PREFIXES:
        if lowered.startswith(prefix):
            value = value[len(prefix):]
            break
    value = value.strip().lower()
    if not _DOI_RE.match(value):
        raise IdentifierError(f"not a valid DOI: {raw!r}")
    return value


def _issn_check_char(first7: str) -> str:
    total = sum(int(d) * w for d, w in zip(first7, range(8, 1, -1)))
    check = (11 - total % 11) % 11
    return "X" if check == 10 else str(check)


def normalize_issn(raw: str) -> str:
    """Return an ISSN as NNNN-NNNC after verifying its mod-11 check digit."""
    value = (raw or "").strip().upper()
    m = _ISSN_RE.match(value)
    if not m:
        raise IdentifierError(f"not a valid ISSN format: {raw!r}")
    digits = m.group(1) + m.group(2)
    if _issn_check_char(digits[:7]) != digits[7]:
        raise IdentifierError(f"ISSN check digit mismatch: {raw!r}")
    return f"{digits[:4]}-{digits[4:]}"


def is_valid_issn(raw: str) -> bool:
    try:
        normalize_issn(raw)
        return True
    except IdentifierError:
        return False


def _orcid_check_char(first15: str) -> str:
    total = 0
    for d in first15:
        total = (total + int(d)) * 2
    check = (12 - total % 11) % 11
    return "X" if check == 10 else str(check)


def normalize_orcid(raw: str) -> str:
    """Return an ORCID iD as NNNN-NNNN-NNNN-NNNC after ISO 7064 11,2 check."""
    value = (raw or "").strip().upper()
    for prefix in ("HTTPS://ORCID.ORG/", "HTTP://ORCID.ORG/", "ORCID.ORG/"):
        if value.startswith(prefix):
            value = value[len(prefix):]
    m = _ORCID_RE.match(value)
    if not m:
        raise IdentifierError(f"not a valid ORCID format: {raw!r}")
    digits = "".join(m.groups())
    if _orcid_check_char(digits[:15]) != digits[15]:
        raise IdentifierError(f"ORCID checksum mismatch: {raw!r}")
    return "-".join(digits[i:i + 4] for i in range(0, 16, 4))


def normalize_pmid(raw: str | int) -> str:
    value = str(raw).strip()
    if not _PMID_RE.match(value):
        raise IdentifierError(f"not a valid PMID: {raw!r}")
    return value


def normalize_pmcid(raw: str) -> str:
    value = (raw or "").strip().upper()
    if value.isdigit():
        value = "PMC" + value
    if not _PMCID_RE.match(value):
        raise IdentifierError(f"not a valid PMCID: {raw!r}")
    return value
