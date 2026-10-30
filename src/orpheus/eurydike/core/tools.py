"""Risk-of-bias and quality appraisal tools, equal to Tabula v19 (decision of the user of 2026-10-20: "Uguali a
Tabula v19"), and their derived scores.

The definitions (names, item ids and texts, scales with points, modes, bands, items without score) are read from
`tabula_v19_tools.json`, copied from the published source of Tabula v19 (objects ROB_TOOLS, QSC, QUAL_TOOLS and the
custom tools of `robTool` / `qualTool`). `qual_score` and `rob_overall` follow Tabula's `qualScore` and `robOverall`:

- quality, mode sum: points and maximum on the answered items other than `na` (each item with its own scale), items
  without score (PEDro p1) left out also from `total`; pct = points / max with one decimal, bands on the percentage
  (QualSyst 75/55, Downs & Black 75/50/25, PEDro 60/40); ratio with two decimals;
- quality, mode worst (COSMIN): the worst of vg < ad < db < in, `na` ignored;
- quality, mode count (AXIS, QAREL, MMAT, custom list): yes over the answered items other than `na`;
- risk of bias: the worst judgement in the tool's order (`ni` and other codes outside the order do not count);
  RoB 2 with three or more domains "some" → "high" with a note; NOS counts the stars; not every domain judged →
  the worst so far with `partial`.

Deliberate differences, kept from the specification (principle: a score is never computed from invalid data):
a judgement outside the item's scale counts as blank (Tabula would count it as 0 points); a risk of bias whose
judged domains are all `ni` gives no suggestion (Tabula would suggest "low"). The overall risk of bias is only a
SUGGESTION: the user writes the final one. Labels are Italian (shown to the user)."""
from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

DEFINITIONS = Path(__file__).with_name("tabula_v19_tools.json")
COSMIN_ORDER = ("vg", "ad", "db", "in")
ROB_ORDER = {"robinsi": ("low", "moderate", "serious", "critical"), "quadas2": ("low", "unclear", "high")}
ROB_DEFAULT_ORDER = ("low", "some", "high")
ROB2_SOME_TO_HIGH = 3
ROB2_NOTE = "≥3 domini con alcune preoccupazioni: valuta se il giudizio complessivo è alto"


@dataclass(frozen=True)
class Item:
    id: str
    text: str
    scale: Mapping[str, int | None]          # code -> points (None for na or for risk-of-bias codes)
    noscore: bool = False
    hint: str = ""
    section: str = ""

    def codes(self) -> tuple[str, ...]:
        return tuple(self.scale)


@dataclass(frozen=True)
class Tool:
    key: str
    name: str
    kind: str                      # "rob" or "qual"
    mode: str                      # rob: "worst" / "stars"; qual: "sum" / "worst" / "count"
    items: tuple[Item, ...]
    ref: str = ""
    use: str = ""
    show: str = ""                 # sum: "ratio" or "points" (how Tabula shows the score)
    bands: tuple[tuple[float, str], ...] = ()     # (lower bound in percent, label), highest first
    band_note: str = ""
    order: tuple[str, ...] = ()    # best first, for mode worst
    labels: Mapping[str, str] = field(default_factory=dict)

    def item(self, iid: str) -> Item | None:
        return next((i for i in self.items if i.id == iid), None)

    @property
    def scored(self) -> tuple[Item, ...]:
        return tuple(i for i in self.items if not i.noscore)


@cache
def _definitions() -> dict:
    return json.loads(DEFINITIONS.read_text(encoding="utf-8"))


def _scale(pairs) -> dict[str, int | None]:
    return {p[0]: (p[2] if len(p) > 2 else None) for p in pairs}


def _rob_tool(key: str, t: dict, domains: list[str] | None = None) -> Tool:
    scale = _scale(t["scale"])
    doms = ([{"id": f"{t['prefix']}{i}", "t": d, "h": ""} for i, d in enumerate(domains or [], 1)]
            if t.get("custom") else t["domains"])
    items = tuple(Item(d["id"], d["t"], scale, hint=d.get("h", "")) for d in doms)
    return Tool(key, t["name"], "rob", "stars" if key == "nos" else "worst", items,
                order=ROB_ORDER.get(key, ROB_DEFAULT_ORDER), labels={p[0]: p[1] for p in t["scale"]})


def _qual_tool(key: str, t: dict, custom: list[str] | None = None) -> Tool:
    base = _scale(t["scale"])
    raw = ([{"id": f"{t['prefix']}{i}", "t": x} for i, x in enumerate(custom or [], 1)] if t.get("custom")
           else t["items"])
    items = tuple(Item(x["id"], x["t"], _scale(x["scale"]) if x.get("scale") else base, bool(x.get("noscore")),
                       x.get("h", ""), x.get("s", "")) for x in raw)
    return Tool(key, t["name"], "qual", t["mode"], items, t.get("ref", ""), t.get("use", ""), t.get("show", ""),
                tuple((float(b[0]), b[1]) for b in t.get("bands", [])), t.get("bandNote", ""),
                COSMIN_ORDER if t["mode"] == "worst" else (), {p[0]: p[1] for p in t["scale"]})


ROB_KEYS = tuple(k for k in _definitions()["rob"] if k != "custom")
QUAL_KEYS = tuple(k for k in _definitions()["qual"] if k != "qcustom")
ALL_KEYS = ROB_KEYS + ("custom",) + QUAL_KEYS + ("qcustom",)
ROB_TOOLS = {k: _rob_tool(k, _definitions()["rob"][k]) for k in ROB_KEYS}
QUAL_TOOLS = {k: _qual_tool(k, _definitions()["qual"][k]) for k in QUAL_KEYS}


def custom_rob(domains: list[str]) -> Tool:
    """Risk-of-bias tool with domains written by the user (C1…; low / some ("Incerto") / high, worst judgement)."""
    return _rob_tool("custom", _definitions()["rob"]["custom"], domains)


def custom_qual(items: list[str]) -> Tool:
    """Quality list with items written by the user (x1…; y / n / u, count of yes)."""
    return _qual_tool("qcustom", _definitions()["qual"]["qcustom"], items)


def get_tool(key: str, custom: list[str] | None = None) -> Tool:
    if key == "custom":
        return custom_rob(custom or [])
    if key == "qcustom":
        return custom_qual(custom or [])
    if key in ROB_TOOLS:
        return ROB_TOOLS[key]
    if key in QUAL_TOOLS:
        return QUAL_TOOLS[key]
    raise KeyError(f"strumento sconosciuto: {key}")


# ---------------------------------------------------------------- scores

def _j(v) -> str | None:
    if v is None:
        return None
    return v.get("j") if isinstance(v, Mapping) else str(v)


def _judgements(items, d: Mapping) -> dict[str, str]:
    """Item id -> judgement, only for the given items with a judgement in the item's scale."""
    out = {}
    for it in items:
        j = _j(d.get(it.id))
        if j in it.codes():
            out[it.id] = j
    return out


def _round(x: float, digits: int) -> float:
    """Math.round of JavaScript (halves up), as Tabula: Math.round(x * 10^d) / 10^d."""
    f = 10 ** digits
    return math.floor(x * f + 0.5) / f


def qual_score(tool: Tool | str, d: Mapping, custom: list[str] | None = None) -> dict:
    """Score of a quality appraisal (Tabula `qualScore`). `d`: item id -> judgement code (or {"j": code}).

    Common keys: n_answered (scored items with a judgement, na included), total (scored items), partial.
    - sum: kind "points", pts, max, pct (1 decimal), band (on pct), ratio (2 decimals);
    - worst: kind "worst", worst, label;
    - count: kind "count", yes, applicable (answered items other than na).
    When no answered item is other than na: {n_answered, total, partial, empty: True} ("non valutato")."""
    if isinstance(tool, str):
        tool = get_tool(tool, custom)
    items = tool.scored
    js = _judgements(items, d)
    base = {"n_answered": len(js), "total": len(items), "partial": len(js) < len(items)}
    real = {k: v for k, v in js.items() if v != "na"}
    if not real:
        return {**base, "empty": True}
    if tool.mode == "worst":
        worst = max(real.values(), key=lambda j: tool.order.index(j) if j in tool.order else 0)
        return {**base, "kind": "worst", "worst": worst, "label": tool.labels.get(worst, worst)}
    if tool.mode == "count":
        return {**base, "kind": "count", "yes": sum(1 for v in real.values() if v == "y"), "applicable": len(real)}
    pts = mx = 0
    for iid, j in real.items():
        scale = tool.item(iid).scale
        pts += scale.get(j) or 0
        mx += max((v or 0) for v in scale.values())
    pct = _round(pts / mx * 100, 1) if mx else 0.0
    band = next((label for lo, label in tool.bands if pct >= lo), "")
    return {**base, "kind": "points", "pts": pts, "max": mx, "pct": pct, "band": band,
            "ratio": _round(pts / mx, 2) if mx else 0.0}


def rob_overall(d: Mapping, tool: Tool | str, custom: list[str] | None = None) -> dict | None:
    """Suggested overall risk of bias (Tabula `robOverall`); the user writes the final one.

    None with no domain judged. NOS: {"stars": domains with a star, "max": domains} (+ partial). Other tools: the worst
    judgement in the tool's order; not every domain judged → {"j": worst, "partial": True}; RoB 2 with every domain
    judged and three or more "some" → "high" with a note. Judgements outside the order (`ni`) do not count; when
    every judged domain is `ni` → {"j": None, "ni": True} (Tabula would say "low")."""
    if isinstance(tool, str):
        tool = get_tool(tool, custom)
    js = _judgements(tool.items, d)
    if not js:
        return None
    partial = len(js) < len(tool.items)
    if tool.mode == "stars":
        out = {"stars": sum(1 for v in js.values() if v == "low"), "max": len(tool.items)}
        return {**out, "partial": True} if partial else out
    ranked = [tool.order.index(v) for v in js.values() if v in tool.order]
    if not ranked:
        return {"j": None, "ni": True, **({"partial": True} if partial else {})}
    worst = tool.order[max(ranked)]
    if partial:
        return {"j": worst, "partial": True}
    if tool.key == "rob2" and sum(1 for v in js.values() if v == "some") >= ROB2_SOME_TO_HIGH:
        return {"j": "high", "note": ROB2_NOTE}
    return {"j": worst}
