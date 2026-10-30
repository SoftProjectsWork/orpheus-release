"""EURYDIKE E1 (T-051b): check of a systematic review / meta-analysis written in Word (review-audit-v1).

Group "Revisione sistematica" of the manuscript checks, added to the existing ones for kind "revisione"
(`compliance.check`), and the standalone command `orpheus revisione-sistematica` (no journal needed), which also runs
the PRISMA 2020 checklist (`reporting_checklists`) and the numbers and statistics (`numbers_audit`, CIs included).

Checks (each `ok` conforme / `ko` da correggere / `guarda` da guardare, as compliance.py):
- PRISMA flow that adds up: identified − duplicates = screened (other sources and other removals accounted for);
  screened − excluded = sought (or assessed); sought − not retrieved = assessed; assessed − excluded = included;
  meta-analysis ≤ included. The numbers come from the Results, the figure legends and the text boxes of a PRISMA
  diagram drawn in Word; when the text and the diagram give two different numbers for the same box, that is reported.
- numbers of the abstract = numbers of the flow;
- rows of the table of study characteristics (distinct studies in the first column) = included;
- k and n of the pooled analyses: k ≥ 2, k ≤ studies in the meta-analysis, n ≥ k, n ≤ total participants;
- effect estimates with a 95% CI in square brackets (forest-plot style, "SMD 0.45 [0.12, 0.78]") contain the estimate
  (the other CI forms are checked by numbers_audit);
- I² between 0 and 100%, τ² not negative;
- registration (PROSPERO, OSF, INPLASY) present; risk-of-bias tool named;
- conversions declared when medians, IQR or ranges are reported and pooled.
R10: the details carry only numbers and the names of the boxes, never sentences of the manuscript."""
from __future__ import annotations

import itertools
import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from ..compliance import REVIEW_REGISTRIES, Check
from ..manuscript import Manuscript
from ..numbers_audit import _WORD_NUMBER, ci_problem, word_number
from ..reporting_checklists import PRISMA

REVIEW_AUDIT_VERSION = "review-audit-v1"
GROUP = "Revisione sistematica"

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

BOXES = {   # name in the details (Italian, shown to the user)
    "identified": "identificati", "other": "da altre fonti", "duplicates": "doppioni", "removed": "rimossi prima",
    "screened": "esaminati", "excluded": "esclusi (titolo/abstract)", "sought": "cercati per intero",
    "not_retrieved": "non recuperati", "assessed": "letti per intero", "ft_excluded": "esclusi al full text",
    "included": "inclusi", "ma": "nella meta-analisi", "added": "studi aggiunti (altre fonti)",
}

# ---------------------------------------------------------------- numbers as written ("1,234", "1 234", "twelve")

_N = rf"(?<![\w.,-])(\d{{1,3}}(?:[,   ]\d{{3}})+(?!\d)|\d+|{_WORD_NUMBER}|\b(?:two|three|four|five|six|seven|eight|nine)\b)"
_NOUN = (r"(?:records?|articles?|studies|study|citations?|references?|reports?|papers?|publications?|hits|trials?|"
         r"titles?(?:\s+and\s+abstracts?)?|abstracts?|documents?|manuscripts?|RCTs?)")
_ADJ = r"(?:[A-Za-z][A-Za-z-]*\s+){0,3}?"     # up to 3 words, no numbers ("eligible", "full-text")
_BOX = r"[^()\n.;]{0,70}?\(\s*n\s*=\s*" + _N


def to_int(s: str) -> int | None:
    s = s.strip()
    if re.fullmatch(r"[\d,   ]+", s):
        return int(re.sub(r"\D", "", s))
    return word_number(s)


def _rx(*parts: str) -> list[re.Pattern]:
    return [re.compile(p, re.I) for p in parts]


PATTERNS: dict[str, list[re.Pattern]] = {
    "identified": _rx(
        rf"{_N}\s+{_ADJ}{_NOUN}\s+(?:were\s+|was\s+)?(?:initially\s+|originally\s+)?(?:identified|retrieved|found|"
        rf"yielded|obtained|located|returned)",
        rf"(?:search(?:es)?|strategy|databases?)\s+{_ADJ}(?:yielded|retrieved|identified|returned|generated|produced|"
        rf"resulted\s+in)\s+(?:a\s+total\s+of\s+)?{_N}",
        rf"(?:records?|studies|articles|references)\s+identified{_BOX}",
        rf"identified\s+(?:a\s+total\s+of\s+)?{_N}\s+{_ADJ}{_NOUN}",
    ),
    "other": _rx(
        rf"{_N}\s+(?:additional|further|other)\s+{_ADJ}{_NOUN}",
        rf"(?:other\s+(?:sources|methods)|hand[- ]?search\w*|reference\s+lists?|citation\s+searching|registers?){_BOX}",
    ),
    "duplicates": _rx(
        rf"{_N}\s+(?:were\s+)?(?:duplicates?|duplicated)\b",
        rf"{_N}\s+duplicat\w*\s+{_ADJ}(?:were\s+)?(?:removed|excluded|eliminated|deleted)",
        rf"(?:removal|removing|removed|exclusion|excluding|eliminat\w+|delet\w+)\s+(?:of\s+)?{_N}\s+duplicat",
        rf"duplicat\w*(?:\s+records?)?\s+removed{_BOX}",
        rf"duplicat\w*{_BOX}",
    ),
    "removed": _rx(rf"records?\s+removed\s+before\s+screening{_BOX}"),
    "screened": _rx(
        rf"{_N}\s+{_ADJ}{_NOUN}\s+(?:were|was|remained|underwent)\s+(?:then\s+|subsequently\s+)?(?:screened|"
        rf"screening|eligible\s+for\s+screening)",
        rf"{_N}\s+{_ADJ}{_NOUN}\s+remained\s+for\s+(?:title\s+and\s+abstract\s+)?screening",
        rf"{_N}\s+{_ADJ}{_NOUN}\s+(?:were\s+)?(?:left|retained|remained|kept)\s+(?:for|to\s+undergo)\s+"
        rf"(?:the\s+)?(?:[\w/-]+\s+){{0,4}}?screening",
        rf"screened\s+(?:a\s+total\s+of\s+)?(?:the\s+)?{_N}\s+",
        rf"(?:records?|titles?)\s+(?:and\s+abstracts?\s+)?screened{_BOX}",
    ),
    "sought": _rx(rf"reports?\s+sought(?:\s+for\s+retrieval)?{_BOX}"),
    "not_retrieved": _rx(
        rf"(?:reports?\s+)?not\s+retrieved{_BOX}",
        rf"{_N}\s+{_ADJ}{_NOUN}?\s*(?:could\s+not\s+be|were\s+not)\s+(?:retrieved|obtained|accessed)",
    ),
    "assessed": _rx(
        rf"{_N}\s+{_ADJ}(?:full[- ]texts?|full[- ]text\s+(?:articles?|reports?|papers?|studies))\s+(?:were\s+)?"
        rf"(?:assessed|reviewed|retrieved|screened|examined|evaluated|read)",
        rf"{_N}\s+{_ADJ}{_NOUN}\s+(?:were\s+)?(?:assessed|reviewed|read|evaluated|examined|screened)\s+(?:in\s+full|"
        rf"for\s+(?:full[- ]text\s+)?eligibility|(?:at|by|in)\s+(?:the\s+)?full[- ]text)",
        rf"full[- ]texts?\s+of\s+(?:the\s+(?:remaining\s+)?)?{_N}\b",
        rf"leaving\s+{_N}\s+{_ADJ}(?:full[- ]text\s+)?{_NOUN}\s+for\s+(?:full[- ]text\s+)?(?:eligibility|assessment|"
        rf"review|evaluation)",
        rf"(?:assessed|evaluated)\s+for\s+eligibility{_BOX}",
        rf"full[- ]text\s+(?:articles?|reports?)\s+assessed{_BOX}",
    ),
    "included": _rx(
        rf"{_N}\s+{_ADJ}(?:studies|study|articles?|trials?|reports?|papers?|publications?|RCTs?)\s+(?:were\s+|was\s+)?"
        rf"(?:therefore\s+|finally\s+|ultimately\s+|eventually\s+)?(?:included|retained|used\s+in\s+the\s+final\s+analys\w+|"
        rf"met\s+(?:the\s+|all\s+(?:the\s+)?)?(?:inclusion|"
        rf"eligibility)\s+criteria|(?:were\s+)?eligible\s+for\s+inclusion)",
        rf"(?:review\s+)?(?:included|includes|including)\s+(?:a\s+total\s+of\s+)?{_N}\s+{_ADJ}(?:studies|trials|"
        rf"articles|RCTs|reports)",
        rf"(?:a\s+total\s+of\s+)?{_N}\s+{_ADJ}(?:studies|trials|articles|RCTs)\s+(?:involving|with|comprising|enrolling)",
        rf"{_N}\s+{_ADJ}(?:studies|trials|articles|RCTs)\s*\([^)]{{0,60}}\)\s*(?:were\s+|was\s+)?included",
        rf"studies\s+included\s+in\s+(?:the\s+)?(?:systematic\s+)?review{_BOX}",
    ),
    "added": _rx(
        rf"{_N}\s+(?:more|additional|further)\s+(?:eligible\s+|relevant\s+)?(?:studies|trials|articles|reports)\s+"
        rf"(?:were\s+|was\s+)?(?:identified|included|added|found)",
    ),
    "ma": _rx(
        rf"{_N}\s+(?:{_ADJ}(?:studies|trials|articles|RCTs)\s+)?(?:were\s+)?(?:included|pooled|entered|used|"
        rf"combined)\s+in\s+(?:the\s+|a\s+)?(?:meta-?analys[ie]s|quantitative\s+(?:synthesis|analysis)|pooled\s+analys)",
        rf"(?:meta-?analys[ie]s|quantitative\s+synthesis){_BOX}",
    ),
}
# "N ... excluded": the sentence decides which box (title/abstract or full text)
_EXCLUDED = re.compile(rf"{_N}\s+{_ADJ}{_NOUN}?\s*(?:were\s+|was\s+)?(?:then\s+|subsequently\s+|further\s+)?excluded",
                       re.I)
_EXCLUSION_OF = re.compile(rf"(?:resulted\s+in|led\s+to)\s+(?:the\s+)?exclusion\s+of\s+{_N}", re.I)
_EXCLUDED_BOX = {"excluded": re.compile(rf"records?\s+excluded{_BOX}", re.I),
                 "ft_excluded": re.compile(rf"(?:reports?|full[- ]text\s+articles?|studies)\s+excluded{_BOX}", re.I)}
_FT_WORDS = re.compile(r"full[- ]?text|eligibility|reasons?", re.I)
_TA_WORDS = re.compile(r"\btitles?\b|abstracts?|screening", re.I)
_SENT = re.compile(r"(?<=[.;!?])\s+(?=[A-Z0-9(])|\n+")
_NOT_FLOW = re.compile(r"\b(?:participants|subjects|patients|athletes|players|individuals|people|women|men|children|"
                       r"adults|volunteers)\b", re.I)


@dataclass
class Flow:
    """Numbers of the PRISMA boxes found in one area: box -> distinct values in order of appearance."""
    values: dict[str, list[int]] = field(default_factory=dict)

    def add(self, box: str, v: int | None):
        if v is None:
            return
        lst = self.values.setdefault(box, [])
        if v not in lst:
            lst.append(v)

    def get(self, box: str) -> int | None:
        lst = self.values.get(box)
        return lst[0] if lst else None


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT.split(text or "") if s and s.strip()]


def read_flow(text: str) -> Flow:
    """PRISMA numbers written in a text (Results, legend or the boxes of a diagram)."""
    f = Flow()
    for s in sentences(text):
        for box, pats in PATTERNS.items():
            for rx in pats:
                for m in rx.finditer(s):
                    tail = s[m.end(): m.end() + 60]
                    if box == "included" and re.match(r"\s*in\s+(?:the\s+|a\s+)?(?:meta-?analys|quantitative|pooled)",
                                                      tail, re.I):
                        continue                     # "12 studies were included in the meta-analysis" -> ma
                    if box in ("included", "identified", "screened") and _NOT_FLOW.search(re.sub(r"\([^)]*\)", "", m.group(0))):
                        continue
                    if re.search(r"(?:hundred|thousand)(?:\s+and)?\s*$", s[max(0, m.start(1) - 20):m.start(1)], re.I):
                        continue                     # "one thousand one hundred and thirty-two": not 32
                    if box in ("identified", "included") and re.search(r"\b(?:additional|further|other|more)\b",
                                                                      m.group(0), re.I):
                        continue                     # "12 additional records were found" -> other
                    f.add(box, to_int(m.group(1)))
        for box, rx in _EXCLUDED_BOX.items():
            for m in rx.finditer(s):
                f.add(box, to_int(m.group(1)))
        if any(rx.search(s) for rx in _EXCLUDED_BOX.values()):
            continue
        for m in list(_EXCLUDED.finditer(s)) + list(_EXCLUSION_OF.finditer(s)):
            ft, ta = bool(_FT_WORDS.search(s)), bool(_TA_WORDS.search(s))
            if "duplicat" in s[m.start(): m.end() + 30].lower():
                continue
            if ft and not ta:
                f.add("ft_excluded", to_int(m.group(1)))
            elif ta and not ft:
                f.add("excluded", to_int(m.group(1)))
    return f


# ---------------------------------------------------------------- what the .docx holds besides the paragraphs

def docx_extras(path) -> tuple[list[str], list[list[list[str]]]]:
    """(paragraphs of the text boxes, tables as rows of cells) of a .docx. The paragraphs of ORPHEUS do not include
    text boxes (where a PRISMA diagram drawn in Word keeps its numbers) and the tables keep no rows."""
    boxes: list[str] = []
    tables: list[list[list[str]]] = []
    try:
        with zipfile.ZipFile(path) as z:
            root = ET.fromstring(z.read("word/document.xml"))
    except (OSError, KeyError, zipfile.BadZipFile, ET.ParseError):
        return boxes, tables

    def ptext(p) -> str:
        return re.sub(r"\s+", " ", "".join(t.text or "" for t in p.iter(W + "t"))).strip()

    seen = set()
    for tb in root.iter(W + "txbxContent"):
        lines = [ptext(p) for p in tb.findall(W + "p")]
        block = " ".join(x for x in lines if x)
        if block and block not in seen:          # mc:AlternateContent repeats the same box (DrawingML and VML)
            seen.add(block)
            boxes.append(block)
    body = root.find(W + "body")
    for tbl in (body.findall(W + "tbl") if body is not None else []):
        rows = []
        for tr in tbl.findall(W + "tr"):
            rows.append([" ".join(ptext(p) for p in tc.iter(W + "p")).strip() for tc in tr.findall(W + "tc")])
        tables.append(rows)
    return boxes, tables


# ---------------------------------------------------------------- the checks

_STUDY_HEAD = re.compile(r"^(?:study|studies|author|authors|first\s+author|reference|ref\.?|trial|study\s*\(year\)|"
                         r"author\s*\(year\)|author,?\s+year)\b", re.I)
_CHAR_HEAD = re.compile(r"participants|sample|population|subjects|\bn\b|design|intervention|age|country|sport|"
                        r"outcome", re.I)
_K = re.compile(rf"\bk\s*=\s*(\d+)|\(\s*{_N}\s+(?:studies|trials|RCTs)\s*[,;]\s*{_N}\s+(?:participants|subjects|"
                rf"athletes|patients|individuals)", re.I)
_KN = re.compile(r"\bk\s*=\s*(\d+)\s*[,;]?\s*(?:studies\s*[,;]?\s*)?(?:n|N)\s*=\s*(\d[\d,]*)")
_TOTAL_PART = re.compile(rf"(?:a\s+total\s+of|total(?:ling|ing)?|comprising|involving|including)\s+{_N}\s+(?:[\w-]+\s+)"
                         rf"{{0,2}}?(?:participants|subjects|athletes|patients|individuals|players|people)", re.I)
_EFF = re.compile(r"\b(?:SMD|MD|WMD|Hedges'?\s*g|Cohen'?s\s*d|g|d|ES|RR|OR|HR|r)\s*[=:]?\s*(-?\d+(?:\.\d+)?)\s*"
                  r"\[\s*(-?\d+(?:\.\d+)?)\s*[,;]\s*(-?\d+(?:\.\d+)?)\s*\]")
_I2 = re.compile(r"\bI\s*(?:²|2|\^2)\s*(?:=|:|was|of)\s*(-?\d+(?:\.\d+)?)\s*(%)?")
_TAU2 = re.compile(r"(?:τ|tau)\s*(?:²|2|\^2)\s*(?:=|:)\s*(-?\d+(?:\.\d+)?)", re.I)
_MEDIANS = re.compile(r"\bmedians?\b|interquartile|\bIQR\b|\(range\b|\brange\s*[:=]", re.I)
_CONVERSION = re.compile(r"\bWan\b|\bLuo\b|\bHozo\b|\bShi\b(?:\s+et\s+al)?|McGrath|estimat\w+\s+(?:the\s+)?(?:sample\s+)?"
                         r"means?|convert\w*|transform\w*\s+(?:in)?to\s+means?|Cochrane\s+Handbook|imput\w+", re.I)
_MA = re.compile(r"meta-?analy|pooled|random[- ]effects?|fixed[- ]effects?|forest\s+plot", re.I)
_RISK = next(i.pattern for i in PRISMA if i.id == "rischio")


def _text_areas(m: Manuscript, boxes: list[str]) -> tuple[str, str, str]:
    """(abstract, results + legends + diagram boxes, the whole text)."""
    full = "\n".join(p.text for p in m.doc.paragraphs)
    res_idx = [i for s in m.sections if s.name in ("results", "legends") for i in s.paras]
    results = m.text_of(res_idx) if res_idx else ""
    caption_idx = [i for k in m.captions.values() for i in k.values() if i >= 0]
    captions = m.text_of(sorted(set(caption_idx) - set(res_idx))) if caption_idx else ""
    if not results:            # no Results heading recognised: everything but the abstract and the references
        refs = {i for s in m.sections if s.name == "references" for i in s.paras}
        results = "\n".join(p.text for p in m.doc.paragraphs if p.i not in refs and p.i not in set(m.abstract_paras))
    return m.abstract_text or "", "\n".join([results, captions]), full


def _merged(text_flow: Flow, box_flow: Flow) -> tuple[dict[str, int], dict[str, list[int]], list[str]]:
    """(the flow of the body: the diagram's number when there is one, else the first of the text; every candidate
    of each box, the diagram's first; the boxes where text and diagram disagree)."""
    out, cands, clash = {}, {}, []
    for box in BOXES:
        d, t = box_flow.get(box), text_flow.get(box)
        cands[box] = list(dict.fromkeys(box_flow.values.get(box, []) + text_flow.values.get(box, [])))
        if d is not None:
            out[box] = d
            if t is not None and t != d and d not in text_flow.values.get(box, []):
                clash.append(f"{BOXES[box]}: {t} nel testo, {d} nel diagramma")
        elif t is not None:
            out[box] = t
    return out, {k: v for k, v in cands.items() if v}, clash


def _try(label: str, boxes: tuple[str, ...], cands: dict[str, list[int]], fn, show) -> tuple[str, str] | None:
    """One equation over every combination of the candidate values (at most 64): ("ok", text) when one combination
    holds, ("bad", text) when none holds and every box has one value, ("unsure", text) when none holds but some box
    has more candidates (multi-stage screening, updated searches, numbers of other sections)."""
    lists = [cands.get(b) for b in boxes]
    if any(not x for x in lists):
        return None
    combos = list(itertools.islice(itertools.product(*lists), 64))
    for vals in combos:
        if fn(*vals):
            return "ok", f"{label}: {show(*vals, True)}"
    kind = "bad" if all(len(x) == 1 for x in lists) else "unsure"
    return kind, f"{label}: {show(*combos[0], False)}" + ("" if kind == "bad" else " (con più numeri candidati)")


def equations(c: dict, cands: dict[str, list[int]] | None = None) -> tuple[list[str], list[str], list[str]]:
    """(equations that hold, that do not, that do not with ambiguous numbers), with numbers only. `c`: box -> value;
    `cands`: box -> candidate values (default: the values of `c`)."""
    cands = cands or {k: [v] for k, v in c.items() if v is not None}
    res = {"ok": [], "bad": [], "unsure": []}

    def add(r):
        if r:
            res[r[0]].append(r[1])

    removed = "removed" if cands.get("removed") else "duplicates"
    other = cands.get("other") or [0]                 # the lambda tries both "with" and "without" other sources
    cc = {**cands, "_other": other}
    add(_try("identificati (+ altre fonti) − rimossi = esaminati", ("identified", "_other", removed, "screened"), cc,
             lambda i, o, r, s: i - r == s or i + o - r == s,
             lambda i, o, r, s, okk: f"{i} − {r} = {s}" if okk and i - r == s else
             (f"{i} + {o} − {r} = {s}" if okk else f"{i} − {r} = {i - r} ≠ {s}")))
    if cands.get("sought"):
        nxt = "sought"
    elif not any(cands.get("not_retrieved", [])):
        nxt = "assessed"
    else:
        nxt = None
    if nxt:
        add(_try(f"esaminati − esclusi = {BOXES[nxt]}", ("screened", "excluded", nxt), cands,
                 lambda s, e, n: s - e == n, lambda s, e, n, okk: f"{s} − {e} = {s - e} {'=' if okk else '≠'} {n}"))
    nr = {**cands, "not_retrieved": cands.get("not_retrieved") or [0]}
    add(_try("cercati − non recuperati = letti per intero", ("sought", "not_retrieved", "assessed"), nr,
             lambda s, n, a: s - n == a, lambda s, n, a, okk: f"{s} − {n} = {s - n} {'=' if okk else '≠'} {a}"))
    ad = {**cands, "added": cands.get("added") or [0]}
    add(_try("letti per intero − esclusi (+ aggiunti) = inclusi", ("assessed", "ft_excluded", "added", "included"), ad,
             lambda a, e, x, i: a - e == i or a - e + x == i,
             lambda a, e, x, i, okk: (f"{a} − {e} = {i}" if a - e == i else f"{a} − {e} + {x} = {i}") if okk else
             f"{a} − {e} = {a - e} ≠ {i}"))
    add(_try("nella meta-analisi ≤ inclusi", ("ma", "included"), cands,
             lambda m_, i: m_ <= i, lambda m_, i, okk: f"{m_} {'≤' if okk else '>'} {i}"))
    return res["ok"], res["bad"], res["unsure"]


def study_table_rows(tables: list[list[list[str]]]) -> tuple[int, int] | None:
    """(distinct studies in the first column, tables used) of the tables of study characteristics: header with a
    'Study'/'Author' first cell and a characteristics column. Continued tables with the same header are summed."""
    names: list[str] = []
    used = 0
    for rows in tables:
        if len(rows) < 2 or not rows[0]:
            continue
        head = rows[0]
        if not (_STUDY_HEAD.match(head[0] or "") and any(_CHAR_HEAD.search(h or "") for h in head[1:])):
            continue
        used += 1
        for r in rows[1:]:
            first = re.sub(r"\s+", " ", (r[0] if r else "") or "").strip()
            if first and not _STUDY_HEAD.match(first) and first not in names:
                names.append(first)
    return (len(names), used) if used else None


def audit(m: Manuscript, path=None) -> list[Check]:
    """The checks of group "Revisione sistematica" (review-audit-v1). `path`: the .docx, to read text boxes and table
    rows (without it those two sources are skipped)."""
    boxes, tables = docx_extras(path) if path else ([], [])
    abstract, body, full = _text_areas(m, boxes)
    a_flow = read_flow(abstract)
    flow, cands, clash = _merged(read_flow(body), read_flow("\n".join(boxes)))
    refs = {i for sec in m.sections if sec.name == "references" for i in sec.paras}
    main = "\n".join(p.text for p in m.doc.paragraphs if p.i not in refs)   # without the reference list
    out: list[Check] = []

    # 1. PRISMA flow
    ok_eq, bad_eq, unsure_eq = equations(flow, cands)
    found = ", ".join(f"{BOXES[b]} {v}" for b, v in flow.items()) or "nessun numero del flusso trovato"
    if bad_eq:
        esito, msg = "ko", f"il flusso PRISMA non torna ({len(bad_eq)} su {len(bad_eq) + len(ok_eq)} controlli)"
    elif clash or unsure_eq:
        esito, msg = "guarda", ("testo e diagramma danno numeri diversi" if clash else
                                "il flusso non torna con i numeri trovati, ma alcune caselle hanno più numeri candidati")
    elif ok_eq and flow.get("included") is not None:
        esito, msg = "ok", f"il flusso PRISMA torna ({len(ok_eq)} controlli)"
    else:
        esito, msg = "guarda", "numeri del flusso incompleti: controlla a mano il diagramma PRISMA"
    out.append(Check("rs_flusso", GROUP, "Flusso PRISMA (identificati → inclusi)", esito, f"{msg}; trovati: {found}",
                     None, None, bad_eq + clash + unsure_eq + ok_eq))

    # 2. abstract = flow
    common = [b for b in ("identified", "screened", "assessed", "included", "ma")
              if a_flow.get(b) is not None and flow.get(b) is not None]
    diff = [f"{BOXES[b]}: {a_flow.get(b)} nell'abstract, {flow[b]} nel flusso" for b in common
            if not set(a_flow.values.get(b, [])) & set(cands.get(b, []))]
    a_inc = a_flow.get("included")
    added = cands.get("added") or [0]
    odd = [v for v in cands.get("included", []) if a_inc is not None and v != a_inc
           and not any(v + x == a_inc for x in added)]
    if diff:
        out.append(Check("rs_abstract", GROUP, "Numeri dell'abstract = flusso", "ko",
                         f"{len(diff)} numeri diversi fra abstract e flusso", None, None, diff))
    elif odd:
        out.append(Check("rs_abstract", GROUP, "Numeri dell'abstract = flusso", "guarda",
                         f"inclusi {a_inc} nell'abstract e nel testo, ma il testo dice anche altri numeri di studi inclusi "
                         "(sottogruppi o un errore?)", None, None, [f"inclusi: {v} nel testo" for v in odd]))
    elif a_flow.get("included") is None:
        out.append(Check("rs_abstract", GROUP, "Numeri dell'abstract = flusso", "guarda",
                         "l'abstract non dice quanti studi sono stati inclusi (PRISMA 2020 per gli abstract)"))
    else:
        out.append(Check("rs_abstract", GROUP, "Numeri dell'abstract = flusso", "ok" if common else "guarda",
                         f"uguali: {', '.join(BOXES[b] for b in common)}" if common else
                         "inclusi nell'abstract, ma il flusso del testo non lo dice"))

    # 3. table of study characteristics
    st = study_table_rows(tables)
    inc = flow.get("included", a_flow.get("included"))
    if st is None:
        out.append(Check("rs_tabella", GROUP, "Tabella delle caratteristiche = inclusi", "guarda",
                         "tabella delle caratteristiche degli studi non trovata (prima colonna 'Study' o 'Author')"))
    elif inc is None:
        out.append(Check("rs_tabella", GROUP, "Tabella delle caratteristiche = inclusi", "guarda",
                         f"{st[0]} studi nella tabella; numero di inclusi non trovato"))
    else:
        same = st[0] == inc
        out.append(Check("rs_tabella", GROUP, "Tabella delle caratteristiche = inclusi", "ok" if same else "guarda",
                         f"{st[0]} studi nella tabella ({st[1]} tabelle), {inc} inclusi" +
                         ("" if same else ": controlla righe doppie o studi mancanti")))

    # 4. k and n of the pooled analyses
    ma = flow.get("ma") or a_flow.get("ma") or inc
    total = None
    for t in (abstract, body):
        mt = _TOTAL_PART.search(t)
        if mt:
            total = to_int(mt.group(1))
            break
    probs, seen_k = [], 0
    for mk in _KN.finditer(full):
        k, n = int(mk.group(1)), int(re.sub(r"\D", "", mk.group(2)))
        seen_k += 1
        probs += _kn_problems(k, n, ma, total)
    for mk in _K.finditer(full):
        if mk.group(1) is not None:
            if not re.match(r"\s*[,;]?\s*(?:studies\s*[,;]?\s*)?(?:n|N)\s*=", full[mk.end():mk.end() + 20]):
                seen_k += 1
                probs += _kn_problems(int(mk.group(1)), None, ma, total)
        else:
            seen_k += 1
            probs += _kn_problems(to_int(mk.group(2)), to_int(mk.group(3)), ma, total)
    probs = list(dict.fromkeys(probs))
    if seen_k:
        out.append(Check("rs_kn", GROUP, "k e n delle analisi", "ko" if probs else "ok",
                         f"{seen_k} analisi con k dichiarato" + (f", {len(probs)} incoerenze" if probs else ", coerenti"),
                         None, None, probs))

    # 5. effect estimates with CIs in square brackets, I², τ²
    bad_ci = []
    for me in _EFF.finditer(full):
        p = ci_problem(me.group(1), me.group(2), me.group(3))
        if p:
            bad_ci.append(f"{me.group(1)} [{me.group(2)}, {me.group(3)}]: {p}")
    if bad_ci:
        out.append(Check("rs_ic", GROUP, "Stime e IC 95% delle forest plot", "ko",
                         f"{len(bad_ci)} intervalli che non contengono la stima", None, None, bad_ci))
    i2 = [(float(x.group(1)), x.group(2)) for x in _I2.finditer(full)]
    bad_i2 = [f"I² = {v:g}{p or ''}" for v, p in i2 if v < 0 or v > 100]
    bad_tau = [f"τ² = {x.group(1)}" for x in _TAU2.finditer(full) if float(x.group(1)) < 0]
    if i2 or bad_tau:
        out.append(Check("rs_i2", GROUP, "I² fra 0 e 100%, τ² non negativo", "ko" if bad_i2 or bad_tau else "ok",
                         f"{len(i2)} valori di I²" + (f", {len(bad_i2) + len(bad_tau)} impossibili" if bad_i2 or bad_tau
                                                     else ", tutti possibili"), None, None, bad_i2 + bad_tau))

    # 6. registration
    regs = [r for r in m.registrations if r[0] in REVIEW_REGISTRIES]
    if not regs:          # forms the manuscript reader does not know: old PROSPERO ids, the OSF DOI (10.17605/OSF.IO/…)
        if re.search(r"PROSPERO[^.]{0,60}?\b(?:ID|number|no\.?|registration)\s*:?\s*(?:CRD\s?)?\d{5,}", main, re.I):
            regs = [("PROSPERO", "", False)]
        elif re.search(r"10\.\s?17605/\s?OSF\.\s?IO/\s?\w{4,}", main, re.I):
            regs = [("OSF", "", False)]
    if regs:
        out.append(Check("rs_registrazione", GROUP, "Registrazione (PROSPERO, OSF, INPLASY)", "ok",
                         ", ".join(dict.fromkeys(r[0] for r in regs)) + (" (anche nell'abstract)" if any(r[2] for r in regs)
                                                                         else "")))
    elif re.search(r"PROSPERO|\bOSF\b|INPLASY|Open Science Framework", main):
        out.append(Check("rs_registrazione", GROUP, "Registrazione (PROSPERO, OSF, INPLASY)", "guarda",
                         "registro nominato ma senza numero riconoscibile (CRD…, osf.io/…, INPLASY…)"))
    else:
        out.append(Check("rs_registrazione", GROUP, "Registrazione (PROSPERO, OSF, INPLASY)", "ko",
                         "nessuna registrazione del protocollo (PRISMA 2020, voce 24a: dirlo anche se non registrata)"))

    # 7. risk of bias tool
    out.append(Check("rs_rob", GROUP, "Strumento per il rischio di bias", "ok" if re.search(_RISK, main, re.I) else "ko",
                     "nominato" if re.search(_RISK, main, re.I) else
                     "nessuno strumento nominato (RoB 2, ROBINS-I, PEDro, NOS, QUADAS-2…)"))

    # 8. conversions declared
    table_text = "\n".join(" ".join(c for r in t for c in r) for t in tables)
    is_ma = bool(re.search(r"meta-?analy", f"{m.title or ''}\n{abstract}", re.I))   # a review that pools data
    if _MEDIANS.search(body + "\n" + table_text) and is_ma:
        conv = _CONVERSION.search(main)
        out.append(Check("rs_conversioni", GROUP, "Conversioni da mediana/IQR/range dichiarate", "ok" if conv else "guarda",
                         "metodo di conversione citato" if conv else
                         "mediane, IQR o range riportati in una revisione con meta-analisi, ma nessun metodo di "
                         "conversione citato (es. Wan 2014, Luo 2018)"))
    return out


def _kn_problems(k: int | None, n: int | None, ma: int | None, total: int | None) -> list[str]:
    out = []
    if k is None:
        return out
    if ma is not None and k > ma:
        out.append(f"k = {k} più degli studi nella meta-analisi ({ma})")
    if n is not None:
        if n < k:
            out.append(f"k = {k}, n = {n}: meno partecipanti che studi")
        if total is not None and n > total:
            out.append(f"k = {k}, n = {n}: più dei partecipanti totali ({total})")
    return out


# ---------------------------------------------------------------- the standalone command (revisione-sistematica)

def flow_numbers(m: Manuscript, path=None) -> dict:
    """{abstract: {box: value}, flusso: {box: value}} — numbers only (safe for the log)."""
    boxes, _tables = docx_extras(path) if path else ([], [])
    abstract, body, _full = _text_areas(m, boxes)
    flow, _cands, _clash = _merged(read_flow(body), read_flow("\n".join(boxes)))
    a = read_flow(abstract)
    return {"abstract": {b: a.get(b) for b in BOXES if a.get(b) is not None}, "flusso": flow,
            "diagramma_nel_word": bool(boxes)}


def run(path) -> dict:
    """The whole check of one review (.docx, or .pdf read through a temporary .docx deleted at the end): numbers and
    statistics (numbers_audit), PRISMA 2020 checklist (reporting_checklists) and the group "Revisione sistematica".
    Read-only, no database, no network."""
    import tempfile
    from dataclasses import asdict
    from pathlib import Path

    from .. import compliance
    from .. import manuscript as ms
    from ..numbers_audit import audit as numbers
    from ..reporting_checklists import audit as checklists
    path = Path(path)
    if path.suffix.lower() == ".pdf":
        with tempfile.TemporaryDirectory() as tmp:
            docx = Path(tmp) / "revisione.docx"
            pdf_to_docx(path, docx)
            result = run(docx)
        result["formato"] = "pdf"
        result["nota"] += (" Letta dal PDF: tabelle e diagrammi in immagine non si leggono, i loro numeri vanno "
                           "controllati a mano.")
        return result
    m = ms.read(path)
    checks = audit(m, path) + checklists(m, "revisione") + numbers(m)
    return {"versione": REVIEW_AUDIT_VERSION, "manoscritto": compliance.manuscript_summary(m), "titolo": m.title,
            "numeri": flow_numbers(m, path), "conteggi": compliance.counts(checks), "formato": "docx",
            "controlli": [asdict(c) for c in checks],
            "nota": "Controllo della coerenza interna della revisione: non giudica la qualità delle prove."}


def summary(result: dict, report=None) -> dict:
    """What the command prints and the log keeps (R10): numbers and outcomes only, never the text."""
    out = {"versione": result["versione"], "formato": result.get("formato", "docx"), "manoscritto": {k: result["manoscritto"][k] for k in
                                                          ("impronta", "parole_testo", "parole_abstract", "riferimenti",
                                                           "tabelle", "figure")},
           "numeri": result["numeri"], "conteggi": result["conteggi"],
           "da_correggere": [c["titolo"] for c in result["controlli"] if c["esito"] == "ko"],
           "da_guardare": [c["titolo"] for c in result["controlli"] if c["esito"] == "guarda"], "nota": result["nota"]}
    if report:
        out["rapporto"] = str(report)
    return out


def render_html(result: dict, file_name: str, now) -> str:
    from ..compliance import CSS, ORDER, OUTCOMES, _e
    s = result["manoscritto"]
    out = ["<!doctype html><html lang='it'><head><meta charset='utf-8'>",
           "<meta name='viewport' content='width=device-width,initial-scale=1'>",
           f"<title>Controllo della revisione sistematica</title><style>{CSS}</style></head><body>",
           "<h1>Controllo della revisione sistematica</h1>",
           f"<div class='sub'>{_e(file_name)} · impronta {_e(s['impronta'])} · {now:%Y-%m-%d %H:%M} · "
           f"{_e(result['versione'])}</div>"]
    if result.get("titolo"):
        out.append(f"<p><b>{_e(result['titolo'])}</b></p>")
    c = result["conteggi"]
    out.append(f"<p class='note'>{_e(result['nota'])} Il rapporto resta su questo PC. Da correggere: "
               f"<b>{c['da correggere']}</b> · da guardare: <b>{c['da guardare']}</b> · conformi: "
               f"<b>{c['conforme']}</b></p>")
    num = result["numeri"]
    out.append("<h2>Numeri del flusso PRISMA trovati</h2><table><tr><th>Casella</th><th>Nel testo e nel diagramma</th>"
               "<th>Nell'abstract</th></tr>")
    for b, label in BOXES.items():
        f, a = num["flusso"].get(b), num["abstract"].get(b)
        if f is None and a is None:
            continue
        out.append(f"<tr><td>{_e(label)}</td><td>{_e('—' if f is None else f)}</td><td>{_e('—' if a is None else a)}"
                   "</td></tr>")
    out.append("</table><div class='sub'>Diagramma disegnato in Word: " + ("sì" if num.get("diagramma_nel_word") else
               "no (se è un'immagine, i suoi numeri non si leggono: controllali a mano)") + "</div>")
    groups: dict[str, list[dict]] = {}
    for ch in result["controlli"]:
        groups.setdefault(ch["gruppo"], []).append(ch)
    for grp, rows in groups.items():
        rows.sort(key=lambda x: ORDER.index(x["esito"]))
        out.append(f"<h2>{_e(grp)}</h2><table><tr><th style='width:26%'>Controllo</th><th style='width:13%'>Esito</th>"
                   "<th>Nel manoscritto</th></tr>")
        for ch in rows:
            det = "".join(f"<div class='det'>• {_e(d)}</div>" for d in ch["dettagli"][:30])
            out.append(f"<tr><td>{_e(ch['titolo'])}</td><td><span class='b {ch['esito']}'>{OUTCOMES[ch['esito']]}"
                       f"</span></td><td>{_e(ch['trovato'])}{det}</td></tr>")
        out.append("</table>")
    out.append("<p class='src'>Regole: PRISMA 2020 (Page MJ et al., BMJ 2021;372:n71), aritmetica del flusso, "
               f"coerenza dei numeri ({_e(result['versione'])}). Nessun dato è uscito dal PC.</p></body></html>")
    return "\n".join(out)


def write_report(result: dict, home, file_name: str, now=None):
    import json
    from datetime import datetime

    from ..compliance import reports_dir
    now = now or datetime.now()
    folder = reports_dir(home)
    folder.mkdir(parents=True, exist_ok=True)
    base = folder / f"revisione_sistematica_{result['manoscritto']['impronta'][:8]}_{now:%Y-%m-%d_%H%M%S}"
    path = base.with_suffix(".html")
    path.write_text(render_html(result, file_name, now), encoding="utf-8")
    base.with_suffix(".json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


# ---------------------------------------------------------------- a review in PDF (published reviews for the gate)

_PDF_HEADING = re.compile(r"^\s*(?:\d{1,2}(?:\.\d{1,2})*\.?\s+)?(abstract|background|introduction|methods?|materials and "
                          r"methods|methodology|results|discussion|conclusions?|references|acknowledgements?|"
                          r"declarations)\s*$", re.I)


def pdf_blocks(path) -> list:
    """Paragraph blocks of a PDF for the manuscript reader: text per page (pypdf, ligatures and line-end hyphens
    fixed as in ingest_pdf), a heading for every isolated line that is a section name ("3 Results", "Methods"),
    the lines in between joined. Tables and figures drawn as images are not read."""
    import pypdf

    from ..article_docx import Block
    from ..ingest_pdf import _clean, rejoin_hyphenated
    reader = pypdf.PdfReader(str(path))
    pages = [_clean(p.extract_text() or "") for p in reader.pages]
    pages, _ = rejoin_hyphenated(pages)
    blocks, cur = [], []
    title = None

    def flush():
        if cur:
            text = re.sub(r"\s+", " ", " ".join(cur)).strip()
            if text:
                blocks.append(Block("p", [(text, {})]))
            cur.clear()

    for page in pages:
        for line in page.splitlines():
            s = line.strip()
            if not s:
                continue
            m = _PDF_HEADING.match(s)
            if m:
                flush()
                blocks.append(Block("h1", [(m.group(1).capitalize(), {})]))
                continue
            if title is None and len(s) > 20 and not re.search(r"doi|http|vol\.|\(\d{4}\)", s, re.I):
                title = s
            cur.append(s)
            if re.search(r"[.:]$", s) and len(" ".join(cur)) > 600:
                flush()
    flush()
    if title:
        blocks.insert(0, Block("title", [(title, {})]))
    return blocks


def pdf_to_docx(path, out) -> None:
    """A temporary .docx with the text of a PDF review (read by manuscript.read like any other review)."""
    from ..article_docx import DEFAULT_LAYOUT, write_docx
    write_docx(out, pdf_blocks(path), {**DEFAULT_LAYOUT, "numeri_di_riga": False}, "")
