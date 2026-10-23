"""Agreement between two reviewers (Tabula §4.5): Cohen's kappa, binarisation of screening decisions, labels.

In EURYDIKE the second reviewer is Claude, declared as such (decision D6): the kappa is reported as user–Claude
agreement, never as agreement between two humans."""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping


def cohen_kappa(pairs: Iterable[tuple[str, str]]) -> dict | None:
    """Cohen's kappa on pairs (a, b) of any categories. Returns {n, po, pe, kappa} or None with no pairs.
    kappa = 1 when the expected agreement is 1 (both reviewers used one same category for everything)."""
    pairs = list(pairs)
    n = len(pairs)
    if not n:
        return None
    po = sum(1 for a, b in pairs if a == b) / n
    ca, cb = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    pe = sum((ca[c] / n) * (cb[c] / n) for c in set(ca) | set(cb))
    kappa = 1.0 if pe == 1 else (po - pe) / (1 - pe)
    return {"n": n, "po": po, "pe": pe, "kappa": kappa}


def binarise(decision: str | None) -> str | None:
    """Screening decision -> 'inc' (include, maybe) or 'exc' (exclude); anything else -> None."""
    if decision in ("include", "maybe"):
        return "inc"
    if decision == "exclude":
        return "exc"
    return None


def screening_pairs(a: Mapping[str, str], b: Mapping[str, str]) -> list[tuple[str, str]]:
    """Binarised pairs on the records decided by BOTH reviewers (maps record id -> decision), in record order."""
    out = []
    for rec in sorted(set(a) & set(b)):
        x, y = binarise(a[rec]), binarise(b[rec])
        if x is not None and y is not None:
            out.append((x, y))
    return out


def screening_kappa(a: Mapping[str, str], b: Mapping[str, str]) -> dict | None:
    """Cohen's kappa of two screeners, with its label added (None when no record is decided by both)."""
    k = cohen_kappa(screening_pairs(a, b))
    if k is not None:
        k["label"] = kappa_label(k["kappa"])
    return k


def kappa_label(kappa: float) -> str:
    """Landis & Koch bands as in Tabula (Italian, shown to the user)."""
    if kappa < 0:
        return "scarso"
    if kappa <= 0.2:
        return "lieve"
    if kappa <= 0.4:
        return "discreto"
    if kappa <= 0.6:
        return "moderato"
    if kappa <= 0.8:
        return "sostanziale"
    return "quasi perfetto"
