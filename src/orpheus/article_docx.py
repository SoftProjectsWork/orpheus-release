"""T-041 part 2 — Fase 6 (A): the Word draft of a new article from its approved sections (draft-v1).

When every section of `nuovi_articoli\\<name>` is approved (writing-v1, `new_article.py`), `build` writes `bozza.docx`
with the Python standard library only (zip of XML parts, as `manuscript.py` reads them):
  - the page format of the journal card ("altri_elementi": font, size, line spacing, margins, page and line numbers;
    defaults when the card says nothing: Times New Roman 12 pt, double spacing, 2.5 cm, page and line numbers);
  - the sections in the order of an article: title, abstract with the labels written in the approved abstract,
    keywords, Introduction, Methods, Results, Discussion (Conclusion and Practical implications as main headings),
    the statements from studio.csv (funding, conflicts, data, use of AI) before the references, References, Tables;
  - the citations `[@article_id]` as numbers in order of first citation (Vancouver, in brackets or superscript as the
    card says) or as author-year (APA) when the card asks for it; the references from the metadata verified in
    ORPHEUS (authors, title, journal, year, volume, issue and pages of the confirmed Crossref record, DOI);
  - the tables from risultati.csv (column "tabella"), only the columns with values.
Then the gate of the Fase 6 for what ORPHEUS writes (`gate`): every citation is an article of ORPHEUS with a DOI
confirmed by Crossref and not retracted, every reference is cited, the approved sections did not change after the
approval, and the draft passes the manuscript check (compliance-v1) against the journal card.
R10: the draft stays on the PC (project folder); the output for the log has counts only. Nothing is written in the
database.
"""
from __future__ import annotations

import json
import re
import sqlite3
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

from . import new_article as na

DRAFT_VERSION = "draft-v1"
DRAFT_NAME = "bozza.docx"
CHECK_NAME = "bozza_controllo.html"
FONTS = ("Times New Roman", "Arial", "Calibri", "Helvetica", "Cambria", "Georgia")
DEFAULT_LAYOUT = {"carattere": "Times New Roman", "corpo_pt": 12.0, "interlinea": "doppia", "margine_cm": 2.5,
                  "numeri_di_pagina": True, "numeri_di_riga": True}
LINE = {"singola": 240, "1,5": 360, "doppia": 480}
MAIN_HEADINGS = {"conclusion", "practical"}           # paragraph titles of the Discussion that become main headings
RESULT_HEADERS = [("variabile", "Variable"), ("gruppo", "Group"), ("n", "n"), ("media", "Mean"), ("ds", "SD"),
                  ("mediana", "Median"), ("iqr", "IQR"), ("min", "Min"), ("max", "Max"), ("differenza", "Difference"),
                  ("ic95", "95% CI"), ("p", "p"), ("effetto", "Effect size"), ("unita", "Unit")]
AI_DEFAULT = ("During the preparation of this work the authors used Claude (Anthropic) within the ORPHEUS system to draft "
              "the text from the authors' own data and from sources verified in ORPHEUS. The authors reviewed and edited "
              "the content and take full responsibility for the content of the publication.")


class DraftError(Exception):
    """A problem for the user (message in Italian)."""


# ------------------------------------------------------------------------------------------------------ the format
def layout_from_rules(rules: dict) -> dict:
    """The page format asked by the journal card (its "altri_elementi" values), with the defaults for the rest.
    'da_scheda' lists what came from the card."""
    out, from_card = dict(DEFAULT_LAYOUT), []
    for r in rules.get("altri_elementi", []):
        low = (r.get("valore") or "").lower()
        fonts = sorted((f for f in FONTS if f.lower() in low), key=lambda f: low.index(f.lower()))   # first named
        if fonts:
            out["carattere"] = fonts[0]
            from_card.append("carattere")
        size = re.search(r"(\d+(?:[.,]\d+)?)\s*pt\b", low)
        if size:
            out["corpo_pt"] = float(size.group(1).replace(",", "."))
            from_card.append("corpo")
        sp = re.search(r"interlinea\s+(doppia|singola|1[,.]5)", low)
        if sp:
            out["interlinea"] = sp.group(1).replace(".", ",")
            from_card.append("interlinea")
        mm = re.search(r"margin\w*\s+(?:di\s+)?(almeno\s+)?(\d+(?:[.,]\d+)?|un|1)\s*(cm|poll\w*|inch\w*)", low)
        if mm:
            amount = 1.0 if mm.group(2) == "un" else float(mm.group(2).replace(",", "."))
            out["margine_cm"] = round(amount * (1 if mm.group(3).startswith("cm") else 2.54), 2)
            from_card.append("margini")
        if re.search(r"numeri di pagina|pagine numerate|numerazione delle pagine", low):
            from_card.append("numeri di pagina")
        if re.search(r"numeri di riga|\bdi riga\b|numerazione delle righe", low):
            from_card.append("numeri di riga")
    out["da_scheda"] = list(dict.fromkeys(from_card))
    return out


@dataclass
class Style:
    kind: str = "numerica"          # numerica | autore-anno
    superscript: bool = False
    max_authors: int = 6            # authors written before "et al." (Vancouver)


def citation_style(rules: dict) -> Style:
    text = " ".join((r.get("valore") or "").lower() for r in rules.get("stile_citazioni", []))
    st = Style()
    if re.search(r"\bapa\b|harvard|autore[- ]anno", text) and not re.search(r"numeric|numerat|vancouver|\bama\b", text):
        st.kind = "autore-anno"
    st.superscript = bool(re.search(r"apice|superscript|superscritt", text))
    m = re.search(r"(?:autori:\s*i primi|primi)\s+(\d+)", text)
    if m:
        st.max_authors = max(1, int(m.group(1)))
    return st


# -------------------------------------------------------------------------------------------- references from ORPHEUS
@dataclass
class Ref:
    article_id: str
    doi: str
    title: str
    year: int | None
    journal: str
    authors: list[tuple[str, str]]          # (family, given)
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None
    confirmed: bool = False
    retracted: bool = False


def _split_name(display: str, families: set[str]) -> tuple[str, str]:
    display = (display or "").strip()
    for fam in sorted(families, key=len, reverse=True):
        if fam and display.lower().endswith(fam.lower()) and len(display) > len(fam):
            return display[-len(fam):], display[: -len(fam)].strip()
    parts = display.split()
    return (parts[-1], " ".join(parts[:-1])) if parts else ("Anonymous", "")


def reference_of(conn: sqlite3.Connection | None, ev: dict) -> Ref:
    """The bibliographic record of one cited article: the packet's evidence, completed with the confirmed Crossref
    record kept in ORPHEUS (doi_check details: journal, volume, issue, pages, family names)."""
    ref = Ref(ev["article_id"], ev.get("doi") or "", ev.get("titolo") or "", ev.get("anno"), ev.get("rivista") or "", [])
    families: list[str] = []
    if conn is not None:
        a = conn.execute("SELECT doi, title, publication_year, integrity_status FROM article WHERE id = ?",
                         (ev["article_id"],)).fetchone()
        if a is not None:
            ref.doi, ref.title = a["doi"] or ref.doi, a["title"] or ref.title
            ref.year = a["publication_year"] or ref.year
            ref.retracted = a["integrity_status"] == "retracted"
        row = conn.execute("""SELECT details FROM doi_check WHERE article_id = ? AND outcome = 'confirmed'
                              ORDER BY checked_at DESC LIMIT 1""", (ev["article_id"],)).fetchone()
        if row is not None:
            ref.confirmed = True
            try:
                cr = (json.loads(row["details"] or "{}") or {}).get("crossref") or {}
            except json.JSONDecodeError:
                cr = {}
            ref.volume, ref.issue, ref.pages = cr.get("volume"), cr.get("issue"), cr.get("page")
            if cr.get("container_title"):
                ref.journal = cr["container_title"][0] or ref.journal
            families = [f for f in cr.get("autori") or [] if f]
        names = [r[0] for r in conn.execute("""SELECT au.display_name FROM article_author aa JOIN author au
                                               ON au.id = aa.author_id WHERE aa.article_id = ? ORDER BY aa.position""",
                                            (ev["article_id"],))] or ev.get("autori") or []
    else:
        names = ev.get("autori") or []
    fams = set(families)
    ref.authors = [_split_name(n, fams) for n in names]
    if not ref.authors and families:
        ref.authors = [(f, "") for f in families]
    return ref


def _initials(given: str, dots: bool) -> str:
    parts = [p for p in re.split(r"[\s.]+", given) if p]
    out = []
    for p in parts:
        sub = [s for s in p.split("-") if s]
        out.append(("-" if dots else "").join(s[0].upper() + ("." if dots else "") for s in sub))
    return (" " if dots else "").join(out)


def _pages(p: str | None, compact: bool) -> str:
    if not p:
        return ""
    m = re.fullmatch(r"(\d+)\s*[-–]\s*(\d+)", p.strip())
    if not m:
        return p.strip()
    a, b = m.groups()
    if compact and len(a) == len(b):
        i = next((k for k in range(len(a)) if a[k] != b[k]), len(a))
        b = b[i:] or b[-1]
    return f"{a}{'-' if compact else '–'}{b}"


def vancouver(r: Ref, max_authors: int = 6) -> str:
    au = [f"{fam} {_initials(giv, False)}".strip() for fam, giv in r.authors]
    names = ", ".join(au[:max_authors]) + (", et al" if len(au) > max_authors else "") if au else "Anonymous"
    vol = (r.volume or "") + (f"({r.issue})" if r.issue else "")
    where = f"{r.year or 'n.d.'}" + (f";{vol}" if vol else "") + (f":{_pages(r.pages, True)}" if r.pages else "")
    return f"{names}. {r.title.rstrip('.')}. {r.journal}. {where}." + (f" doi:{r.doi}" if r.doi else "")


def apa(r: Ref) -> str:
    au = [f"{fam}, {_initials(giv, True)}".rstrip(", ") for fam, giv in r.authors]
    if not au:
        names = "Anonymous"
    elif len(au) == 1:
        names = au[0]
    elif len(au) <= 20:
        names = ", ".join(au[:-1]) + ", & " + au[-1]
    else:
        names = ", ".join(au[:19]) + ", ... " + au[-1]
    vol = (r.volume or "") + (f"({r.issue})" if r.issue else "")
    tail = ", ".join(x for x in (r.journal, vol, _pages(r.pages, False)) if x)
    return f"{names} ({r.year or 'n.d.'}). {r.title.rstrip('.')}. {tail}." + (f" https://doi.org/{r.doi}" if r.doi
                                                                                   else "")


def author_year(r: Ref) -> str:
    fams = [f for f, _ in r.authors] or ["Anonymous"]
    who = fams[0] if len(fams) == 1 else (f"{fams[0]} & {fams[1]}" if len(fams) == 2 else f"{fams[0]} et al.")
    return f"{who}, {r.year or 'n.d.'}"


def compress(nums: list[int]) -> str:
    """[1, 3, 4, 5, 8] -> '1,3–5,8'."""
    nums = sorted(dict.fromkeys(nums))
    out, i = [], 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        out.append(str(nums[i]) if j == i else (f"{nums[i]},{nums[j]}" if j == i + 1 else f"{nums[i]}–{nums[j]}"))
        i = j + 1
    return ",".join(out)


# ------------------------------------------------------------------------------------------------------- the content
@dataclass
class Block:
    kind: str                       # title | h1 | h2 | p | ref | table
    runs: list = field(default_factory=list)       # [(text, {"b": bool, "sup": bool, "i": bool})]
    rows: list = field(default_factory=list)       # table


def _load(d: Path, section: str) -> dict:
    return json.loads((d / "sezioni" / "accettate" / f"{section}.json").read_text(encoding="utf-8"))


SECTION_ORDER = ("introduzione", "metodi", "risultati", "discussione")
HEADINGS = {"introduzione": "Introduction", "metodi": "Methods", "risultati": "Results", "discussione": "Discussion",
            "etica_diffusione": "Ethics and dissemination"}
ORDER_BY_KIND = {"originale": SECTION_ORDER, "revisione": SECTION_ORDER,
                 "protocollo": ("introduzione", "metodi", "etica_diffusione")}
INCLUDED_HEADERS = [("studio", "Study"), ("disegno", "Design"), ("n", "n"), ("partecipanti", "Participants"),
                    ("intervento", "Intervention"), ("confronto", "Comparison"), ("esiti", "Outcomes"),
                    ("rischio_bias", "Risk of bias")]


def _heading(s: str, kind: str) -> str:
    return "Methods and analysis" if s == "metodi" and kind == "protocollo" else HEADINGS[s]


def _keys(group: str) -> list[str]:
    return [k.strip().lstrip("@") for m in na.CITE.finditer(group) for k in re.split(r"[,;]", m.group(1)) if k.strip()]


def _cited_runs(text: str, style: Style, order: list[str], refs: dict[str, Ref]) -> list:
    """The text with the citations as numbers or author-year: runs [(text, fmt)]."""
    runs, pos = [], 0
    for g in re.finditer(r"\s*(?:\[@[^\]]+\]\s*)+", text):
        keys = _keys(g.group(0))
        before = text[pos:g.start()]
        trailing = g.group(0)[len(g.group(0).rstrip()):]
        for k in keys:
            if k not in order:
                order.append(k)
        if style.kind == "autore-anno":
            runs.append((before + " (" + "; ".join(author_year(refs[k]) for k in keys if k in refs) + ")" + trailing, {}))
        else:
            nums = compress([order.index(k) + 1 for k in keys])
            if style.superscript:
                runs.append((before, {}))
                runs.append((nums, {"sup": True}))
                runs.append((trailing, {}))
            else:
                runs.append((before + " [" + nums + "]" + trailing, {}))
        pos = g.end()
    runs.append((text[pos:], {}))
    merged: list = []
    for t, f in runs:
        if not t:
            continue
        if merged and merged[-1][1] == f:
            merged[-1] = (merged[-1][0] + t, f)
        else:
            merged.append((t, f))
    return merged


def _statements(study: dict, methods_text: str = "") -> list[tuple[str, str]]:
    """The statements before the references, from the user's own words in studio.csv. The ethics statement only when
    the approved Methods do not have one (nothing is invented: no Helsinki, no number not given)."""
    out = []
    committee = (study.get("etica_comitato") or "").strip()
    if committee and not re.search(r"ethic", methods_text, re.I):
        if committee.lower() in ("not applicable", "n/a", "na"):
            text = "Ethics approval: not applicable."
        else:
            text = f"The study was approved by {committee.rstrip('.')}" + (
                f" ({study['etica_numero'].strip().rstrip('.')})" if study.get("etica_numero") else "") + "."
        if study.get("consenso"):
            text += " " + study["consenso"].strip().rstrip(".") + "."
        out.append(("Ethics approval", text))
    if study.get("finanziamenti"):
        fund = study["finanziamenti"].strip()
        out.append(("Funding", "This research received no external funding." if fund.lower() in ("none", "no", "-")
                    else fund))
    conf = (study.get("conflitti") or "").strip()
    out.append(("Declarations of interest", "Declarations of interest: none." if conf.lower() in ("", "none", "no", "-")
                else conf))
    if study.get("disponibilita_dati"):
        out.append(("Data availability", study["disponibilita_dati"].strip()))
    out.append(("Declaration of generative AI and AI-assisted technologies in the writing process",
                (study.get("uso_ia") or "").strip() or AI_DEFAULT))
    return out


def _tables(results: list[dict]) -> list[tuple[str, list[list[str]]]]:
    groups: dict[str, list[dict]] = {}
    for r in results:
        if r.get("tabella"):
            groups.setdefault(r["tabella"].strip(), []).append(r)

    def cell(r, key):
        if key == "ic95":
            lo, hi = r.get("ic95_basso"), r.get("ic95_alto")
            return f"{lo} to {hi}" if lo and hi else ""
        if key == "effetto" and r.get("effetto"):
            return r["effetto"] + (f" ({r['tipo_effetto']})" if r.get("tipo_effetto") else "")
        return r.get(key) or ""
    out = []
    for label, rows in groups.items():
        cols = [(k, h) for k, h in RESULT_HEADERS if any(cell(r, k) for r in rows)]
        body = [[h for _, h in cols]] + [[cell(r, k) for k, _ in cols] for r in rows]
        variables = list(dict.fromkeys(r["variabile"] for r in rows if r.get("variabile")))
        caption = f"{label}. " + (", ".join(variables[:6]) + (" and other variables" if len(variables) > 6 else "")
                                  if variables else "Results") + "."
        out.append((caption, body))
    return out


def compose(d: Path, packet: dict, conn: sqlite3.Connection | None = None) -> tuple[list[Block], dict]:
    """The blocks of the draft and what they contain (counts)."""
    st = na.state(d)
    missing = [s for s, v in st["sezioni"].items() if v != "approvata"]
    if missing:
        raise DraftError("la bozza si scrive quando tutte le sezioni sono approvate; mancano: " + ", ".join(missing))
    rules = packet.get("rivista", {}).get("regole", {})
    style = citation_style(rules)
    kind = packet.get("tipo", "originale")
    body_order = ORDER_BY_KIND.get(kind, SECTION_ORDER)
    evidence = {e["article_id"]: e for e in packet.get("evidenze", [])}
    secs = {s: _load(d, s) for s in na.sections_of(kind)}
    used = []
    for s in body_order + ("abstract",):
        for p in secs[s].get("paragrafi", []):
            used += [k for k in _keys(p.get("testo", "")) if k not in used]
    unknown = [k for k in used if k not in evidence]
    if unknown:
        raise DraftError(f"{len(unknown)} citazioni non sono tra le evidenze del pacchetto")
    refs = {k: reference_of(conn, evidence[k]) for k in used}
    order: list[str] = []
    blocks: list[Block] = []
    ab = secs["abstract"]
    blocks.append(Block("title", [(ab.get("titolo_articolo") or packet.get("studio", {}).get("titolo_provvisorio")
                                   or "Untitled", {})]))
    blocks.append(Block("h1", [("Abstract", {})]))
    for p in ab.get("paragrafi", []):
        runs = []
        if p.get("titolo"):
            runs.append((str(p["titolo"]).strip().rstrip(":") + ": ", {"b": True}))
        runs += _cited_runs(p.get("testo", ""), style, order, refs)
        blocks.append(Block("p", runs))
    kw = ab.get("parole_chiave") or []
    kw = kw if isinstance(kw, list) else [x.strip() for x in str(kw).split(";")]
    blocks.append(Block("p", [("Keywords: ", {"b": True}), ("; ".join(x for x in kw if x), {})]))
    for s in body_order:
        blocks.append(Block("h1", [(_heading(s, kind), {})]))
        for p in secs[s].get("paragrafi", []):
            title = str(p.get("titolo") or "").strip()
            if title:
                main = s == "discussione" and na.compliance.ms.heading_of(title) in MAIN_HEADINGS
                blocks.append(Block("h1" if main else "h2", [(title, {})]))
            blocks.append(Block("p", _cited_runs(p.get("testo", ""), style, order, refs)))
    methods_text = " ".join(na._paragraph_text(p) for s in ("metodi", "etica_diffusione") if s in secs
                            for p in secs[s].get("paragrafi", []))
    for title, text in _statements(packet.get("studio", {}), methods_text):
        blocks.append(Block("h1", [(title, {})]))
        blocks.append(Block("p", [(text, {})]))
    blocks.append(Block("h1", [("References", {})]))
    if style.kind == "autore-anno":
        listed = sorted(order, key=lambda k: (na.compliance.ms.surname_key(refs[k].authors[0][0] if refs[k].authors
                                                                            else "Anonymous"), refs[k].year or 0))
        for k in listed:
            blocks.append(Block("ref", [(apa(refs[k]), {})]))
    else:
        for i, k in enumerate(order, 1):
            blocks.append(Block("ref", [(f"{i}. {vancouver(refs[k], style.max_authors)}", {})]))
    tables = _tables(packet.get("risultati", []))
    included = packet.get("studi_inclusi") or []
    if included:
        cols = [(k, h) for k, h in INCLUDED_HEADERS if any(r.get(k) for r in included)]
        tables.insert(0, ("Table 1. Characteristics of the included studies.",
                          [[h for _, h in cols]] + [[r.get(k) or "" for k, _ in cols] for r in included]))
    if tables:
        blocks.append(Block("h1", [("Tables", {})]))
        for caption, rows in tables:
            blocks.append(Block("p", [(caption, {"b": True})]))
            blocks.append(Block("table", rows=rows))
    groups = sum(len(re.findall(r"(?:\[@[^\]]+\]\s*)+", p.get("testo", ""))) for s in body_order + ("abstract",)
                 for p in secs[s].get("paragrafi", []))
    info = {"riferimenti": len(order), "citazioni": groups, "stile": style.kind
            + (" (apice)" if style.superscript else ""), "tabelle": len(tables),
            "tipo": kind, "parole_testo": sum(len(p.get("testo", "").split()) for s in body_order
                                for p in secs[s].get("paragrafi", [])),
            "parole_abstract": sum(len(p.get("testo", "").split()) for p in ab.get("paragrafi", [])),
            "_refs": refs, "_order": order}
    return blocks, info


# ---------------------------------------------------------------------------------------------------- the .docx file
NS = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
      'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"')
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _run(text: str, fmt: dict) -> str:
    rpr = ("<w:b/>" if fmt.get("b") else "") + ("<w:i/>" if fmt.get("i") else "") \
        + ('<w:vertAlign w:val="superscript"/>' if fmt.get("sup") else "")
    return (f"<w:r>{'<w:rPr>' + rpr + '</w:rPr>' if rpr else ''}"
            f'<w:t xml:space="preserve">{escape(text)}</w:t></w:r>')


def _para(runs: list, style: str | None = None, extra_ppr: str = "") -> str:
    ppr = (f'<w:pStyle w:val="{style}"/>' if style else "") + extra_ppr
    return f"<w:p>{'<w:pPr>' + ppr + '</w:pPr>' if ppr else ''}{''.join(_run(t, f) for t, f in runs)}</w:p>"


def _table(rows: list[list[str]]) -> str:
    border = "".join(f'<w:{s} w:val="single" w:sz="4" w:space="0" w:color="000000"/>'
                     for s in ("top", "bottom"))
    out = []
    for i, row in enumerate(rows):
        cells = "".join(f'<w:tc><w:tcPr><w:tcW w:w="0" w:type="auto"/></w:tcPr>'
                        f'{_para([(c, {"b": i == 0})], extra_ppr="<w:spacing w:line=\"240\" w:lineRule=\"auto\"/>")}'
                        f'</w:tc>' for c in row)
        out.append(f"<w:tr>{'<w:trPr><w:tblHeader/></w:trPr>' if i == 0 else ''}{cells}</w:tr>")
    return (f'<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/><w:tblBorders>{border}</w:tblBorders></w:tblPr>'
            + "".join(out) + "</w:tbl>" + _para([]))


def write_docx(path: Path, blocks: list[Block], lay: dict, title: str = "") -> Path:
    body = []
    for b in blocks:
        if b.kind == "title":
            body.append(_para(b.runs, "Title"))
        elif b.kind == "h1":
            body.append(_para(b.runs, "Heading1"))
        elif b.kind == "h2":
            body.append(_para(b.runs, "Heading2"))
        elif b.kind == "ref":
            body.append(_para(b.runs, "Bibliography"))
        elif b.kind == "table":
            body.append(_table(b.rows))
        else:
            body.append(_para(b.runs))
    twips = round(lay["margine_cm"] * 1440 / 2.54)
    footer_ref = '<w:footerReference w:type="default" r:id="rId2"/>' if lay["numeri_di_pagina"] else ""
    numbering = '<w:lnNumType w:countBy="1" w:restart="continuous"/>' if lay["numeri_di_riga"] else ""
    sect = (f'<w:sectPr>{footer_ref}<w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="{twips}" w:right="{twips}" '
            f'w:bottom="{twips}" w:left="{twips}" w:header="708" w:footer="708" w:gutter="0"/>{numbering}</w:sectPr>')
    document = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {NS}><w:body>{"".join(body)}'
                f'{sect}</w:body></w:document>')
    font, half = lay["carattere"], int(round(lay["corpo_pt"] * 2))
    line = LINE.get(lay["interlinea"], 480)
    styles = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles {NS}><w:docDefaults><w:rPrDefault>'
              f'<w:rPr><w:rFonts w:ascii="{font}" w:hAnsi="{font}" w:cs="{font}" w:eastAsia="{font}"/>'
              f'<w:sz w:val="{half}"/><w:szCs w:val="{half}"/><w:lang w:val="en-GB"/></w:rPr></w:rPrDefault>'
              f'<w:pPrDefault><w:pPr><w:spacing w:after="0" w:line="{line}" w:lineRule="auto"/></w:pPr>'
              '</w:pPrDefault></w:docDefaults>'
              '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>'
              '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/>'
              '<w:pPr><w:jc w:val="center"/><w:spacing w:after="240"/><w:outlineLvl w:val="0"/></w:pPr>'
              f'<w:rPr><w:b/><w:sz w:val="{half + 4}"/></w:rPr></w:style>'
              '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/>'
              '<w:next w:val="Normal"/><w:pPr><w:keepNext/><w:spacing w:before="240"/><w:outlineLvl w:val="0"/></w:pPr>'
              '<w:rPr><w:b/></w:rPr></w:style>'
              '<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/>'
              '<w:next w:val="Normal"/><w:pPr><w:keepNext/><w:spacing w:before="120"/><w:outlineLvl w:val="1"/></w:pPr>'
              '<w:rPr><w:b/><w:i/></w:rPr></w:style>'
              '<w:style w:type="paragraph" w:styleId="Bibliography"><w:name w:val="Bibliography"/>'
              '<w:basedOn w:val="Normal"/><w:pPr><w:ind w:left="567" w:hanging="567"/></w:pPr></w:style></w:styles>')
    footer = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:ftr {NS}><w:p><w:pPr><w:jc w:val="center"/>'
              '</w:pPr><w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText xml:space="preserve"> PAGE '
              '</w:instrText></w:r><w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>1</w:t></w:r><w:r>'
              '<w:fldChar w:fldCharType="end"/></w:r></w:p></w:ftr>')
    overrides = ('<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.'
                 'wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/'
                 'vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/><Override PartName="/docProps/core.xml"'
                 ' ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>')
    rels = f'<Relationship Id="rId1" Type="{REL}/styles" Target="styles.xml"/>'
    if lay["numeri_di_pagina"]:
        overrides += ('<Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-'
                      'officedocument.wordprocessingml.footer+xml"/>')
        rels += f'<Relationship Id="rId2" Type="{REL}/footer" Target="footer1.xml"/>'
    content_types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.'
                     'openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/'
                     'vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/'
                     f'xml"/>{overrides}</Types>')
    core = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><cp:coreProperties xmlns:cp="http://schemas.'
            'openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/">'
            f'<dc:title>{escape(title)}</dc:title><dc:creator></dc:creator></cp:coreProperties>')
    path = Path(path)
    tmp = path.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://'
                   'schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.'
                   'openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
                   '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/'
                   'core-properties" Target="docProps/core.xml"/></Relationships>')
        z.writestr("word/_rels/document.xml.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships'
                   f' xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rels}</Relationships>')
        z.writestr("word/document.xml", document)
        z.writestr("word/styles.xml", styles)
        z.writestr("docProps/core.xml", core)
        if lay["numeri_di_pagina"]:
            z.writestr("word/footer1.xml", footer)
    try:
        tmp.replace(path)
    except PermissionError as exc:
        tmp.unlink(missing_ok=True)
        raise DraftError(f"{path.name} non si può scrivere: se è aperto in Word, chiudilo") from exc
    return path


# -------------------------------------------------------------------------------------------------- build and gate
def gate(d: Path, info: dict, kind: str = "originale") -> dict:
    """The gate of the Fase 6 for what ORPHEUS writes: citations verified, all references cited, approved sections
    unchanged."""
    refs: dict[str, Ref] = info["_refs"]
    not_verified = [k for k, r in refs.items() if not r.doi or not r.confirmed]
    retracted = [k for k, r in refs.items() if r.retracted]
    approvals = na._approvals(d)
    changed = [s for s in na.sections_of(kind) if approvals.get(s, {}).get("sha256")
               != na._sha(d / "sezioni" / "accettate" / f"{s}.json")]
    ok = not not_verified and not retracted and not changed
    return {"superato": ok, "citazioni_non_verificate": len(not_verified), "ritrattati": len(retracted),
            "riferimenti_non_citati": 0, "sezioni_cambiate_dopo_l_approvazione": changed}


def build(d: Path, packet: dict, conn: sqlite3.Connection | None = None, *, journal_list: list[dict] | None = None,
          now: datetime | None = None) -> dict:
    """Write bozza.docx (and bozza_controllo.html with the manuscript check). Returns counts only (for the log)."""
    blocks, info = compose(d, packet, conn)
    lay = layout_from_rules(packet.get("rivista", {}).get("regole", {}))
    title = next((t for b in blocks if b.kind == "title" for t, _ in b.runs), "")
    path = write_docx(d / DRAFT_NAME, blocks, lay, title)
    g = gate(d, info, packet.get("tipo", "originale"))
    out = {"versione": DRAFT_VERSION, "file": DRAFT_NAME,
           "formato": {k: lay[k] for k in ("carattere", "corpo_pt", "interlinea", "margine_cm", "numeri_di_pagina",
                                            "numeri_di_riga")} | {"da_scheda": lay["da_scheda"]},
           **{k: v for k, v in info.items() if not k.startswith("_")}, "gate": g}
    if conn is not None:
        from . import compliance
        sigla = packet.get("rivista", {}).get("sigla")
        try:
            result = compliance.run(conn, path, journal_list or [], [sigla] if sigla else None,
                                    packet.get("tipo", "originale"))
        except Exception as exc:                                # the draft is written anyway; the check is reported
            out["controllo"] = {"errore": exc.__class__.__name__}
            out["gate"]["superato"] = False
        else:
            if result["riviste"]:
                r = result["riviste"][0]
                (d / CHECK_NAME).write_text(compliance.render_html(result, DRAFT_NAME, now or datetime.now()),
                                            encoding="utf-8")
                out["controllo"] = {"rivista": r["rivista"], "conteggi": r["conteggi"], "rapporto": CHECK_NAME}
            else:
                out["controllo"] = {"errore": "rivista senza scheda"}
                out["gate"]["superato"] = False
    out["creato"] = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    return out
