"""Screening decisions and PRISMA counts (Tabula §2.15 and §8.2), from plain Python structures.

Two rules are deliberate and must not be "corrected" (they are tested):
- two concordant `maybe` are a `conflict`, not a final decision: `maybe` means "the full text decides";
- the reviewers counted are those who have decided something on the record: with need = 2, a record decided by
  one reviewer only is `partial`, not `conflict`.
The model's suggestions (Claude as declared second reviewer, D6) enter here only as one of the reviewers' decisions
once they are stored as such; a suggestion that is not a decision never enters `final_decision`."""
from __future__ import annotations

from collections.abc import Iterable, Mapping

DECISIONS = ("include", "exclude", "maybe")
FT_DECISIONS = ("include", "exclude", "na", "pending")
CONSENSUS = "_consenso"


def _d(x) -> tuple[str | None, str]:
    """(decision, reason) from a stored row {"d": .., "r": ..} or a bare decision string."""
    if x is None:
        return None, ""
    if isinstance(x, str):
        return x, ""
    return x.get("d"), x.get("r") or ""


def final_decision(decisions: Mapping[str, object], need: int = 2, consensus=None) -> dict:
    """Final screening decision of one record.

    `decisions`: reviewer -> {"d": include|exclude|maybe, "r": reason} (or the bare decision), in the order the
    reviewers decided (the reason of an agreement is the first reviewer's). A key "_consenso" in `decisions` is
    treated as `consensus`. Returns:
    - {"d": <consensus>, "status": "resolved", "r": reason} when a consensus exists;
    - {"d": None, "status": "pending"} with no decision;
    - {"d": None, "status": "partial", "n": k} with fewer than `need` reviewers;
    - {"d": <d>, "status": "agreed", "r": reason} when all decisions are equal and not `maybe`;
    - {"d": None, "status": "conflict"} otherwise (two `maybe` included)."""
    decisions = dict(decisions)
    if consensus is None:
        consensus = decisions.pop(CONSENSUS, None)
    else:
        decisions.pop(CONSENSUS, None)
    cd, cr = _d(consensus)
    if cd:
        return {"d": cd, "status": "resolved", "r": cr}
    ds = [_d(v) for v in decisions.values()]
    ds = [x for x in ds if x[0]]
    if not ds:
        return {"d": None, "status": "pending"}
    if len(ds) < need:
        return {"d": None, "status": "partial", "n": len(ds)}
    first = ds[0][0]
    if first != "maybe" and all(d == first for d, _ in ds):
        return {"d": first, "status": "agreed", "r": ds[0][1]}
    return {"d": None, "status": "conflict"}


def prisma_counts(imports: Iterable[Mapping], records: Iterable | int, finals: Mapping[str, Mapping] | Iterable,
                  studies: Iterable[Mapping]) -> dict:
    """PRISMA 2020 flow numbers, always computed, never typed (principle 3).

    - `imports`: one item per imported FILE with "raw" (records in the file) and "dupes" (dropped as duplicates);
    - `records`: the records kept after deduplication (an iterable of ids, or their number);
    - `finals`: the final screening decisions (record -> result of `final_decision`, or an iterable of them);
    - `studies`: the studies sought for the full text, each with "ft" (include|exclude|na|pending, missing =
      pending) and "pages" (pages of the PDF; 0 or missing = no PDF).

    Definitions (Tabula §8.2): identified = Σ raw; duplicates = Σ dupes; screened = records kept; excluded = records
    whose final decision is `exclude` (conflicts, partial and pending are not excluded); sought = studies;
    not_retrieved = studies sought WITHOUT a PDF (pages 0 or missing), whatever their full-text decision — this is
    Tabula's definition, kept as it is; assessed = studies with a full-text decision other than pending and na;
    ft_excluded = full-text decision exclude; included = full-text decision include. `na` (not applicable) counts
    as sought but neither assessed nor excluded: the check `flow_problems` reports it."""
    imports = list(imports)
    screened = records if isinstance(records, int) else len(list(records))
    finals_list = list(finals.values()) if isinstance(finals, Mapping) else list(finals)
    studies = list(studies)
    ft = [(s.get("ft") or "pending") for s in studies]
    return {
        "identified": sum(int(i.get("raw") or 0) for i in imports),
        "duplicates": sum(int(i.get("dupes") or 0) for i in imports),
        "screened": screened,
        "excluded": sum(1 for f in finals_list if (f or {}).get("d") == "exclude"),
        "sought": len(studies),
        "not_retrieved": sum(1 for s in studies if not s.get("pages")),
        "assessed": sum(1 for x in ft if x not in ("pending", "na")),
        "ft_excluded": ft.count("exclude"),
        "included": ft.count("include"),
    }


def flow_problems(c: Mapping[str, int]) -> list[str]:
    """Arithmetic of a PRISMA flow (messages in Italian): identified − duplicates = screened; included ≤ assessed ≤
    sought ≤ screened − excluded. An empty list means the flow adds up. Used also by the check of a published
    review (T-051b), where the same names are filled from the numbers read in the manuscript."""
    out = []
    g = lambda k: c.get(k)  # noqa: E731
    if None not in (g("identified"), g("duplicates"), g("screened")) and g("identified") - g("duplicates") != g("screened"):
        out.append(f"identificati − doppioni = {g('identified') - g('duplicates')}, ma esaminati = {g('screened')}")
    if None not in (g("screened"), g("excluded"), g("sought")) and g("sought") > g("screened") - g("excluded"):
        out.append(f"cercati per intero ({g('sought')}) più di esaminati − esclusi ({g('screened') - g('excluded')})")
    if None not in (g("sought"), g("assessed")) and g("assessed") > g("sought"):
        out.append(f"valutati per intero ({g('assessed')}) più dei cercati ({g('sought')})")
    if None not in (g("assessed"), g("ft_excluded"), g("included")) and g("assessed") - g("ft_excluded") != g("included"):
        out.append(f"valutati − esclusi al full text = {g('assessed') - g('ft_excluded')}, ma inclusi = {g('included')}")
    return out
