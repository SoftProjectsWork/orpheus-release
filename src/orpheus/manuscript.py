"""T-037 — Fase 6: a manuscript in Word .docx, read with the Python standard library only (manuscript-v1).

R10 (decision of the user, 2026-10-15): manuscripts stay on the PC and in sessions with Claude, never on GitHub and never
in the logs. This module only reads the file and returns its structure in memory; it writes nothing.

A .docx is a zip of XML parts: word/document.xml (the text), word/styles.xml (styles and defaults), word/theme/theme1.xml
(theme fonts), word/comments.xml, word/footnotes.xml, word/header*.xml and word/footer*.xml (page numbers).

Structure (`analyse`): title, running title, abstract (with its labels), keywords, sections (Introduction, Methods,
Results, Discussion, Conclusion, ...), captions of figures and tables, statements (conflicts, funding, data, AI, ethics,
consent, registration), references and in-text citations (numeric in brackets, parentheses or superscript; or
author-year). Everything is deterministic; what cannot be decided is left to the checks as "da guardare".
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

MANUSCRIPT_VERSION = "manuscript-v1"
FOLDER = "manoscritti"                   # = layout.MANUSCRIPTS_DIR
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
MAX_PART_BYTES = 60 * 1024 * 1024        # uncompressed size of one XML part (guard against zip bombs)
TWIPS_PER_CM = 1440 / 2.54


class ManuscriptError(Exception):
    """The file cannot be read as a Word .docx (message in Italian, for the user)."""


# ----------------------------------------------------------------------------------------------- low level: the file
@dataclass
class Paragraph:
    i: int
    text: str
    style: str                    # style name, lower case ("normal", "heading 1", "title")
    level: int | None             # heading level (0 = title) from the style or the outline level
    bold: bool                    # all the text is bold
    sup: list[tuple[int, str]]    # superscript runs: (offset in raw, text)
    words: int
    raw: str = ""                 # the text before spaces are tidied (offsets of `sup`)


@dataclass
class Docx:
    paragraphs: list[Paragraph]
    tables: list[list[str]]       # the text of each top-level table, paragraph by paragraph
    table_after: list[int]        # index of the last body paragraph before each table (-1 = before all)
    images: int
    revisions: int                # tracked changes not yet accepted (insertions, deletions, moves, format changes)
    comments: int
    footnotes: int
    layout: dict
    sha256: str
    size: int


def words_of(text: str) -> int:
    """Words as Word counts them, roughly: pieces between spaces with at least one letter or digit."""
    return sum(1 for tok in (text or "").split() if any(ch.isalnum() for ch in tok))


def _on(el) -> bool:
    """A Word on/off property (<w:b/>, <w:b w:val="0"/>)."""
    if el is None:
        return False
    return el.get(W + "val", "true").lower() not in ("0", "false", "off", "none")


def _part(z: zipfile.ZipFile, name: str) -> ET.Element | None:
    try:
        info = z.getinfo(name)
    except KeyError:
        return None
    if info.file_size > MAX_PART_BYTES:
        raise ManuscriptError(f"il file contiene una parte troppo grande ({name}, {info.file_size // 1_000_000} MB)")
    raw = z.read(info)
    if b"<!DOCTYPE" in raw[:2000] or b"<!ENTITY" in raw[:5000]:
        raise ManuscriptError(f"il file contiene XML con dichiarazioni non ammesse ({name})")
    try:
        return ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ManuscriptError(f"il file è danneggiato ({name}: {exc})") from exc


class _Styles:
    """Styles and defaults: font, size, bold, line spacing and outline level, following basedOn."""

    def __init__(self, root: ET.Element | None, theme: dict[str, str]):
        self.by_id: dict[str, dict] = {}
        self.default_para: str | None = None
        self.doc_rpr: dict = {}
        self.doc_ppr: dict = {}
        self.theme = theme
        if root is None:
            return
        dd = root.find(W + "docDefaults")
        if dd is not None:
            self.doc_rpr = self._rpr(dd.find(f"{W}rPrDefault/{W}rPr"))
            self.doc_ppr = self._ppr(dd.find(f"{W}pPrDefault/{W}pPr"))
        for st in root.findall(W + "style"):
            sid = st.get(W + "styleId")
            if not sid:
                continue
            name_el, based = st.find(W + "name"), st.find(W + "basedOn")
            self.by_id[sid] = {"name": (name_el.get(W + "val") if name_el is not None else sid).lower(),
                               "based": based.get(W + "val") if based is not None else None,
                               "type": st.get(W + "type"), "rpr": self._rpr(st.find(W + "rPr")),
                               "ppr": self._ppr(st.find(W + "pPr"))}
            if st.get(W + "type") == "paragraph" and st.get(W + "default") in ("1", "true", "on"):
                self.default_para = sid

    def _font(self, rpr) -> str | None:
        f = rpr.find(W + "rFonts")
        if f is None:
            return None
        for attr in ("ascii", "hAnsi"):
            if f.get(W + attr):
                return f.get(W + attr)
        for attr in ("asciiTheme", "hAnsiTheme"):
            t = f.get(W + attr)
            if t:
                return self.theme.get("major" if t.startswith("major") else "minor")
        return None

    def _rpr(self, rpr) -> dict:
        if rpr is None:
            return {}
        out = {}
        font = self._font(rpr)
        if font:
            out["font"] = font
        sz = rpr.find(W + "sz")
        if sz is not None and (sz.get(W + "val") or "").isdigit():
            out["size"] = int(sz.get(W + "val")) / 2
        if rpr.find(W + "b") is not None:
            out["bold"] = _on(rpr.find(W + "b"))
        va = rpr.find(W + "vertAlign")
        if va is not None:
            out["sup"] = va.get(W + "val") == "superscript"
        if rpr.find(W + "vanish") is not None:
            out["hidden"] = _on(rpr.find(W + "vanish"))
        return out

    def _ppr(self, ppr) -> dict:
        if ppr is None:
            return {}
        out = {}
        sp = ppr.find(W + "spacing")
        if sp is not None and (sp.get(W + "line") or "").lstrip("-").isdigit():
            out["line"] = int(sp.get(W + "line"))
            out["rule"] = sp.get(W + "lineRule") or "auto"
        ol = ppr.find(W + "outlineLvl")
        if ol is not None and (ol.get(W + "val") or "").isdigit():
            out["outline"] = int(ol.get(W + "val"))
        return out

    def chain(self, sid: str | None):
        seen = set()
        while sid and sid in self.by_id and sid not in seen:
            seen.add(sid)
            yield self.by_id[sid]
            sid = self.by_id[sid]["based"]

    def para_value(self, sid: str | None, key: str, kind: str):
        for st in self.chain(sid or self.default_para):
            if key in st[kind]:
                return st[kind][key]
        return (self.doc_rpr if kind == "rpr" else self.doc_ppr).get(key)

    def name(self, sid: str | None) -> str:
        st = self.by_id.get(sid or self.default_para or "")
        return st["name"] if st else "normal"


def _theme_fonts(root: ET.Element | None) -> dict[str, str]:
    out = {}
    if root is None:
        return out
    for kind in ("major", "minor"):
        el = root.find(f".//{A}{kind}Font/{A}latin")
        if el is not None and el.get("typeface"):
            out[kind] = el.get("typeface")
    return out


def _spacing_label(line: int | None, rule: str | None) -> tuple[float | None, str]:
    if line is None:
        return 1.0, "singola"
    if (rule or "auto") == "auto":
        mult = round(line / 240, 2)
        names = {1.0: "singola", 1.15: "1,15", 1.5: "1,5", 2.0: "doppia"}
        return mult, names.get(mult, str(mult).replace(".", ","))
    return None, f"{'esatta' if rule == 'exact' else 'almeno'} {line / 20:g} pt"


def read_docx(path: Path) -> Docx:
    """Read a Word .docx. Raises ManuscriptError with a message for the user."""
    path = Path(path)
    if path.suffix.lower() == ".doc":
        raise ManuscriptError("è un file Word vecchio (.doc): aprilo in Word e salvalo come .docx")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ManuscriptError(f"impossibile leggere il file ({exc.__class__.__name__}): se è aperto in Word, "
                              "chiudilo e riprova") from exc
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise ManuscriptError("non è un file .docx (Word): salvalo da Word come 'Documento di Word (.docx)'") from exc
    with z:
        doc = _part(z, "word/document.xml")
        if doc is None:
            raise ManuscriptError("non è un documento di Word (.docx): manca word/document.xml")
        styles = _Styles(_part(z, "word/styles.xml"), _theme_fonts(_part(z, "word/theme/theme1.xml")))
        comments = _part(z, "word/comments.xml")
        notes = _part(z, "word/footnotes.xml")
        page_numbers = False
        for name in z.namelist():
            if re.fullmatch(r"word/(header|footer)\d*\.xml", name):
                part = _part(z, name)
                instr = " ".join((el.text or "") for el in part.iter(W + "instrText"))
                instr += " " + " ".join(el.get(W + "instr", "") for el in part.iter(W + "fldSimple"))
                if re.search(r"\b(NUM)?PAGES?\b", instr):
                    page_numbers = True
    body = doc.find(W + "body")
    if body is None:
        raise ManuscriptError("il documento è vuoto")

    paragraphs: list[Paragraph] = []
    tables: list[list[str]] = []
    table_after: list[int] = []
    fonts, sizes, spacing = Counter(), Counter(), Counter()
    sect_list = []

    def para(p: ET.Element) -> tuple[str, list, bool, int | None, str, dict]:
        ppr = p.find(W + "pPr")
        sid = None
        direct = {}
        if ppr is not None:
            ps = ppr.find(W + "pStyle")
            sid = ps.get(W + "val") if ps is not None else None
            direct = styles._ppr(ppr)
            if ppr.find(W + "sectPr") is not None:
                sect_list.append(ppr.find(W + "sectPr"))
        pieces, sup, bold_all, any_text = [], [], True, False
        runs_fmt = []

        def run(r: ET.Element):
            nonlocal bold_all, any_text
            rpr = r.find(W + "rPr")
            own = styles._rpr(rpr)
            rs = rpr.find(W + "rStyle") if rpr is not None else None
            cstyle = rs.get(W + "val") if rs is not None else None

            def value(key):
                if key in own:
                    return own[key]
                for st in styles.chain(cstyle):
                    if key in st["rpr"]:
                        return st["rpr"][key]
                return styles.para_value(sid, key, "rpr")
            if value("hidden"):
                return
            text = []
            for ch in r:
                tag = ch.tag
                if tag == W + "t":
                    text.append(ch.text or "")
                elif tag in (W + "tab", W + "ptab"):
                    text.append("\t")
                elif tag in (W + "br", W + "cr"):
                    text.append("\n")
                elif tag == W + "noBreakHyphen":
                    text.append("-")
            s = "".join(text)
            if not s:
                return
            start = sum(len(x) for x in pieces)
            pieces.append(s)
            if s.strip():
                any_text = True
                if not value("bold"):
                    bold_all = False
                if value("sup"):
                    sup.append((start, s))
                runs_fmt.append((len(s.strip()), value("font"), value("size")))

        def walk(el: ET.Element):
            for ch in el:
                tag = ch.tag
                if tag in (W + "del", W + "moveFrom", W + "pPr"):
                    continue
                if tag == W + "r":
                    run(ch)
                else:
                    walk(ch)          # hyperlink, ins, moveTo, smartTag, sdt, fldSimple, customXml...
        walk(p)
        text = "".join(pieces)
        name = styles.name(sid)
        level = None
        m = re.fullmatch(r"heading (\d)", name)
        if m:
            level = int(m.group(1))
        elif name in ("title", "subtitle"):
            level = 0
        else:
            outline = direct.get("outline", styles.para_value(sid, "outline", "ppr"))
            if outline is not None and outline < 9:
                level = outline + 1
        if "line" in direct:
            line, rule = direct["line"], direct.get("rule")
        else:
            line, rule = styles.para_value(sid, "line", "ppr"), styles.para_value(sid, "rule", "ppr")
        fmt = {"runs": runs_fmt, "line": line, "rule": rule}
        return text, sup, bold_all and any_text, level, name, fmt

    def body_items(container: ET.Element):
        for el in container:
            if el.tag in (W + "p", W + "tbl", W + "sectPr"):
                yield el
            elif el.tag in (W + "sdt", W + "customXml"):
                inner = el.find(W + "sdtContent") if el.tag == W + "sdt" else el
                if inner is not None:
                    yield from body_items(inner)

    for el in body_items(body):
        if el.tag == W + "sectPr":
            sect_list.append(el)
        elif el.tag == W + "tbl":
            cells = []
            for p in el.iter(W + "p"):
                t, *_ = para(p)
                if t.strip():
                    cells.append(re.sub(r"\s+", " ", t).strip())
            tables.append(cells)
            table_after.append(len(paragraphs) - 1)
        else:
            text, sup, bold, level, name, fmt = para(el)
            clean = re.sub(r"[ \t   ]+", " ", text).strip()
            paragraphs.append(Paragraph(len(paragraphs), clean, name, level, bold, sup, words_of(clean), text))
            for n, font, size in fmt["runs"]:
                if font:
                    fonts[font] += n
                if size:
                    sizes[size] += n
            if clean:
                spacing[_spacing_label(fmt["line"], fmt["rule"])[1]] += len(clean)

    revisions = sum(1 for tag in ("ins", "del", "moveFrom", "moveTo", "rPrChange", "pPrChange")
                    for _ in body.iter(W + tag))
    images = sum(1 for tag in ("drawing", "pict", "object") for _ in body.iter(W + tag))
    n_comments = len(comments.findall(W + "comment")) if comments is not None else 0
    n_notes = 0
    if notes is not None:
        n_notes = sum(1 for fn in notes.findall(W + "footnote")
                      if fn.get(W + "type") not in ("separator", "continuationSeparator", "continuationNotice"))
    margins, line_numbers = {}, False
    for sp in sect_list:
        if sp.find(W + "lnNumType") is not None:
            line_numbers = True
    if sect_list:
        pm = sect_list[-1].find(W + "pgMar")
        if pm is not None:
            for side in ("top", "bottom", "left", "right"):
                v = pm.get(W + side)
                if v and v.lstrip("-").isdigit():
                    margins[side] = round(abs(int(v)) / TWIPS_PER_CM, 2)
    total = sum(fonts.values()) or 1
    layout = {
        "carattere": fonts.most_common(1)[0][0] if fonts else styles.doc_rpr.get("font") or styles.theme.get("minor"),
        "caratteri": {k: round(v / total, 3) for k, v in fonts.most_common(4)},
        "corpo_pt": sizes.most_common(1)[0][0] if sizes else styles.doc_rpr.get("size"),
        "interlinea": spacing.most_common(1)[0][0] if spacing else "singola",
        "margini_cm": margins,
        "numeri_di_riga": line_numbers,
        "numeri_di_pagina": page_numbers,
    }
    return Docx(paragraphs, tables, table_after, images, revisions, n_comments, n_notes, layout,
                hashlib.sha256(raw).hexdigest(), len(raw))


# ---------------------------------------------------------------------------------------------- structure: the text
def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.replace("’", "'").replace("&", " and ").lower()
    return re.sub(r"\s+", " ", s).strip()


HEADINGS: dict[str, tuple[str, ...]] = {
    "abstract": ("abstract", "summary", "structured abstract"),
    "keywords": ("keywords", "key words", "key-words", "keyword", "key word"),
    "introduction": ("introduction", "background", "introduction and background"),
    "methods": ("methods", "method", "methods and analysis", "methods and design", "methods/design", "materials and methods", "material and methods", "methodology", "methods and materials",
                "patients and methods", "participants and methods", "subjects and methods"),
    "results": ("results", "findings"),
    "results_discussion": ("results and discussion",),
    "discussion": ("discussion", "general discussion"),
    "conclusion": ("conclusion", "conclusions", "summary and conclusions", "conclusions and future directions"),
    "practical": ("practical implications", "practical applications", "practical application", "clinical implications",
                  "perspective", "perspectives"),
    "limitations": ("limitations", "strengths and limitations", "study limitations", "limitations and strengths"),
    "acknowledgments": ("acknowledgments", "acknowledgements", "acknowledgment", "acknowledgement"),
    "funding": ("funding", "funding information", "funding sources", "financial support", "funding statement",
                "sources of funding", "source of funding"),
    "conflicts": ("conflict of interest", "conflicts of interest", "conflict of interest statement", "competing interests",
                  "competing interest", "declaration of interests", "declarations of interest", "declaration of interest",
                  "declaration of competing interest", "disclosure", "disclosures", "disclosure statement"),
    "data": ("data availability", "data availability statement", "data sharing", "data sharing statement",
             "availability of data", "availability of data and materials"),
    "ai": ("declaration of generative ai and ai-assisted technologies in the writing process", "declaration of generative ai",
           "use of generative ai", "use of artificial intelligence", "ai statement", "generative ai statement", "use of ai",
           "declaration of generative ai and ai-assisted technologies", "ai disclosure"),
    "ethics": ("ethics", "ethics statement", "ethical approval", "ethics approval", "ethical considerations",
               "ethics approval and consent to participate", "ethical statement", "ethics and dissemination"),
    "contributions": ("author contributions", "authors' contributions", "authors contributions", "contributors",
                      "credit authorship contribution statement", "author contribution"),
    "registration": ("trial registration", "registration", "study registration"),
    "highlights": ("highlights", "key points", "key messages"),
    "plain": ("plain language summary", "lay summary"),
    "references": ("references", "reference list", "bibliography", "literature cited", "works cited", "reference"),
    "legends": ("figure legends", "figure captions", "legends", "figures legends", "legends to figures", "figure legend",
                "figures", "captions"),
    "tables": ("tables",),
    "supplementary": ("supplementary material", "supplementary materials", "supplementary information", "appendix",
                      "appendices", "supporting information", "supplementary files"),
}
_HEADING_OF = {v: k for k, vs in HEADINGS.items() for v in vs}
MAIN = ("introduction", "methods", "results", "results_discussion", "discussion", "limitations", "conclusion", "practical")
BACK = ("acknowledgments", "funding", "conflicts", "data", "ai", "ethics", "contributions", "registration")
AFTER = ("references", "legends", "tables", "supplementary")
END_OF_TEXT = ("acknowledgments", "funding", "conflicts", "data", "ai", "contributions") + AFTER   # ethics and
# registration are often sub-headings of the Methods: they do not end the main text
SECTION_NAMES = {"front": "pagina iniziale", "abstract": "Abstract", "keywords": "Keywords", "introduction": "Introduction",
                 "methods": "Methods", "results": "Results", "results_discussion": "Results and Discussion",
                 "discussion": "Discussion", "limitations": "Limitations", "conclusion": "Conclusion",
                 "practical": "Practical implications", "acknowledgments": "Acknowledgments", "funding": "Funding",
                 "conflicts": "Conflicts of interest", "data": "Data availability", "ai": "Use of AI",
                 "ethics": "Ethics", "contributions": "Author contributions", "registration": "Registration",
                 "highlights": "Highlights", "plain": "Plain language summary", "references": "References",
                 "legends": "Figure legends", "tables": "Tables", "supplementary": "Supplementary material"}

ABSTRACT_LABELS: dict[str, tuple[str, ...]] = {
    "objectives": ("objective", "objectives", "aim", "aims", "purpose", "aims and objectives", "study objective"),
    "background": ("background", "introduction", "context", "rationale"),
    "design": ("design", "study design"),
    "methods": ("method", "methods", "materials and methods", "methodology"),
    "results": ("results", "findings"),
    "discussion": ("discussion",),
    "conclusions": ("conclusion", "conclusions"),
    "levels of evidence": ("level of evidence", "levels of evidence"),
    "setting": ("setting", "settings"),
    "participants": ("participants", "patients", "subjects"),
    "interventions": ("intervention", "interventions"),
    "outcome measures": ("main outcome measures", "outcome measures", "main outcomes"),
    "data sources": ("data sources", "data source"),
    "eligibility": ("eligibility criteria", "study eligibility criteria", "study selection"),
    "registration": ("registration", "trial registration", "prospero registration"),
}
_LABEL_OF = {v: k for k, vs in ABSTRACT_LABELS.items() for v in vs}
_NUMBERING = re.compile(r"^(?:\d+(?:\.\d+)*\.?|[IVX]+\.|[A-H]\.|[a-h]\))\s+")
_INLINE_LABEL = re.compile(r"^\s*([A-Za-z][A-Za-z ]{1,40}?)\s*[:.]\s+(\S.*)$", re.S)
_KEYWORDS = re.compile(r"^\s*(?:key\s*-?\s*words?|keywords)\b\s*[:.\-–—]?\s*(.*)$", re.I | re.S)
_RUNNING = re.compile(r"^\s*(?:short\s+running\s+title|running\s+(?:title|head)|short\s+title)\s*[:.\-–—]\s*(.+)$", re.I)
_TITLE_LABEL = re.compile(r"^\s*(?:full\s+)?title(?:\s+of\s+the\s+(?:article|manuscript|paper))?\s*[:.\-–—]\s*(.+)$", re.I)
CAPTION = re.compile(r"^\s*(?P<kind>Figure|Fig\.?|Table)\s*(?P<n>\d{1,2})\s*(?:[.:|—–-]|\s)", re.I)
MENTION = re.compile(r"\b(?P<kind>Fig(?:ure)?s?\.?|Tables?)\s*(?P<nums>\d{1,2}(?:\s*[a-z]\b)?(?:\s*(?:[-–—,]|and|&)\s*\d{1,2}"
                     r"(?:\s*[a-z]\b)?)*)", re.I)


def heading_of(text: str) -> str | None:
    """The canonical name of a section heading ('methods' for '2. Materials and Methods'), else None."""
    s = _NUMBERING.sub("", (text or "").strip())
    return _HEADING_OF.get(norm(s.rstrip(":.").strip()))


def label_of(text: str) -> str | None:
    return _LABEL_OF.get(norm(_NUMBERING.sub("", text or "").rstrip(":.").strip()))


def _looks_like_heading(p: Paragraph) -> bool:
    if not p.text or p.words > 12 or "\n" in p.text:
        return False
    if p.level is not None:
        return True
    return not re.search(r"[.!?;,]\s*$", p.text.rstrip(":")) or p.text.rstrip().endswith(":")


def _numbers(spec: str) -> list[int]:
    """'1, 3–5, 8' -> [1, 3, 4, 5, 8] (ranges of at most 200)."""
    out = []
    for part in re.split(r"\s*(?:,|;|\band\b|&)\s*", spec):
        m = re.fullmatch(r"(\d{1,4})\s*[a-z]?\s*(?:[-–—]\s*(\d{1,4})\s*[a-z]?)?", part.strip())
        if not m:
            continue
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2) else a
        if b < a or b - a > 200:
            out.append(a)
        else:
            out.extend(range(a, b + 1))
    return out


@dataclass
class Section:
    name: str
    heading: str | None
    start: int
    paras: list[int] = field(default_factory=list)


@dataclass
class Reference:
    n: int
    text: str
    label: int | None
    doi: str | None
    year: str | None
    first_author: str | None


@dataclass
class Citations:
    style: str                               # 'numerica' | 'autore-anno' | 'nessuna'
    form: str | None                         # 'parentesi quadre' | 'apici' | 'parentesi tonde' | None
    groups: list[tuple[int, str, list[int]]] = field(default_factory=list)   # (paragraph, label, numbers)
    author_year: list[tuple[int, str, str, str]] = field(default_factory=list)  # (paragraph, surname, year, label)


@dataclass
class Manuscript:
    doc: Docx
    title: str | None
    title_para: int | None
    running_title: str | None
    abstract_paras: list[int]
    abstract_text: str
    abstract_words: int
    abstract_labels: list[tuple[str, str]]   # (canonical, as written)
    keywords: list[str]
    sections: list[Section]
    main_paras: list[int]
    main_words: int
    captions: dict[str, dict[int, int]]      # kind -> number -> paragraph (or -1 for a caption inside a table)
    mentions: dict[str, list[int]]           # kind -> numbers in order of first mention
    references: list[Reference]
    citations: Citations
    statements: dict[str, list[int]]
    registrations: list[tuple[str, str, bool]]   # (registry, id, in the abstract)
    guidelines: list[str]
    language: str | None
    kind_guess: str
    kind_reason: str
    emails: int
    orcids: int
    total_words: int

    def section(self, name: str) -> Section | None:
        return next((s for s in self.sections if s.name == name), None)

    def text_of(self, idx) -> str:
        return "\n".join(self.doc.paragraphs[i].text for i in idx)


STATEMENTS = {
    "conflitti": re.compile(r"conflicts? of interests?|competing interests?|declarations? of (?:competing )?interests?|"
                            r"no (?:potential )?conflicts?|nothing to disclose|\bdisclosures?\b", re.I),
    "finanziamenti": re.compile(r"\bfunding\b|\bfunded by\b|\bgrants?\b|financial support|\bfinanced\b|"
                                r"received no (?:specific )?(?:external )?funding", re.I),
    "dati": re.compile(r"data availability|data (?:are|is|will be) (?:openly |publicly |freely )?(?:available|accessible)|"
                       r"data sharing|available (?:up)?on (?:reasonable )?request|data that support the findings", re.I),
    "ia": re.compile(r"generative (?:AI|artificial intelligence)|AI[- ]assisted|large language models?|ChatGPT|\bLLMs?\b|"
                     r"artificial intelligence|\bAI tools?\b", re.I),
    "etica": re.compile(r"ethic(?:s|al)\s+(?:committee|approval|board|review|commission)|institutional review board|"
                        r"\bIRB\b|research ethics|ethics approval", re.I),
    "helsinki": re.compile(r"declaration of helsinki|helsinki declaration", re.I),
    "consenso": re.compile(r"informed consent|consent (?:form|was obtained)|gave (?:their )?(?:written )?consent|"
                           r"written consent|provided consent", re.I),
    "contributi": re.compile(r"author contributions?|\bCRediT\b|contributed (?:equally )?to (?:the )?(?:study|work|"
                             r"conception|design)", re.I),
}
APPROVAL_NUMBER = re.compile(r"\b(?:ID|No\.?|Nr\.?|N\.|number|code|protocol|reference|ref\.?|approval)\s*[:#.]?\s*"
                             r"(?:no\.?\s*)?[A-Z]{0,10}[-/ ]?\d[\w./-]*\d", re.I)   # "(ID: 2020.193)", "approval no. 2025/114"
REGISTRIES = (
    ("ClinicalTrials.gov", re.compile(r"\bNCT\d{8}\b")),
    ("PROSPERO", re.compile(r"\bCRD\s?\d{11,12}\b")),   # 12 digits from 2025 (CRD420251234567)
    ("ISRCTN", re.compile(r"\bISRCTN\s?\d{8}\b")),
    ("ANZCTR", re.compile(r"\bACTRN\d{14}\b")),
    ("DRKS", re.compile(r"\bDRKS\d{8}\b")),
    ("ChiCTR", re.compile(r"\bChiCTR[-\w]{6,}\b")),
    ("UMIN", re.compile(r"\bUMIN\d{9}\b")),
    ("CTRI", re.compile(r"\bCTRI/\d{4}/\d{2,3}/\d{6}\b")),
    ("PACTR", re.compile(r"\bPACTR\d{15}\b")),
    ("EU CTR / EudraCT", re.compile(r"\b(?:EudraCT|EU CT|CTIS)[^\d]{0,20}\d{4}-\d{6}-\d{2}(?:-\d{2})?\b", re.I)),
    ("OSF", re.compile(r"\bosf\.io/[a-z0-9]{4,}\b", re.I)),
    ("INPLASY", re.compile(r"\bINPLASY\d{6,}\b")),
)
GUIDELINES = re.compile(r"\b(CONSORT|STROBE|PRISMA(?:[- ]?(?:P|ScR|NMA|IPD))?(?:\s?20(?:09|20))?|TIDieR|STARD|SPIRIT|COREQ|"
                        r"SRQR|ARRIVE|CHERRIES|TRIPOD|SAGER|MOOSE|PERSiST|SQUIRE|AGREE|CHEERS|CROSS|RECORD|GRRAS|"
                        r"CARE)\b")
REVIEW_WORDS = re.compile(r"systematic review|meta[- ]?analys[ie]s|scoping review|umbrella review|\bPRISMA\b", re.I)
# A protocol (2026-10-17: "Aggiungi ai tipi di articoli anche i protocol study"): words that name the article as a
# protocol, not a training or testing protocol used in a study ("a 6-week training protocol").
PROTOCOL_WORDS = re.compile(r"\b(?:study|trial|research|review|meta-analysis|evaluation)\s+protocol\b|\bprotocol\s+for\s+"
                            r"(?:a|an|the)\b[^.]{0,120}?\b(?:trial|study|review|meta-analysis)|:\s*(?:a|the)?\s*protocol\s*$|"
                            r"\bSPIRIT\b|PRISMA[- ]?P\b", re.I)
DOI = re.compile(r"10\.\d{4,9}/[^\s\"<>]+")
EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
ORCID = re.compile(r"\b\d{4}-\d{4}-\d{4}-\d{3}[\dX]\b")
UNIT_BEFORE_SUP = re.compile(r"(?:\b(?:m|cm|mm|km|kg|g|mg|s|ms|min|h|l|ml|mL|L|W|J|N|mol|mmol|µm|um|kcal|kJ|Hz|R|r|"
                             r"x|χ|η|ω|VO|VO2|m·s|kg·m|mL·kg|ml·kg)|[\d°·⋅])$")
SURNAME_PARTICLES = r"(?:van|de|der|den|von|da|di|del|della|la|le|du|dos|das|el|al|ten|ter|op)"
_SURNAME = re.compile(rf"\s*((?:{SURNAME_PARTICLES}\s+)*[A-ZÀ-ÝØÆŒ][\w'’\-À-ÿ]+(?:[ -](?:{SURNAME_PARTICLES}|"
                      rf"[A-ZÀ-Ý][a-zà-ÿ'’]+)(?=[ ,]))*)")
_AY_ITEM = re.compile(rf"^(?:e\.g\.,?|see|cf\.|for example,?|for a review,? see)?\s*((?:{SURNAME_PARTICLES}\s+)*[A-ZÀ-Ý]"
                      rf"[\w'’\-À-ÿ]+)(?:\s+et\s+al\.?|\s+(?:and|&)\s+(?:{SURNAME_PARTICLES}\s+)*[A-ZÀ-Ý][\w'’\-À-ÿ]+)?,?"
                      r"\s+((?:19|20)\d{2})[a-z]?(?:\s*,\s*(?:19|20)\d{2}[a-z]?)*$")
_AY_NARRATIVE = re.compile(rf"\b((?:{SURNAME_PARTICLES}\s+)*[A-ZÀ-Ý][\w'’\-À-ÿ]+)(?:\s+et\s+al\.?|\s+(?:and|&)\s+"
                           r"[A-ZÀ-Ý][\w'’\-À-ÿ]+)?\s+\(((?:19|20)\d{2})[a-z]?\)")


def surname_key(s: str | None) -> str:
    return re.sub(r"[^a-z]", "", norm(s or ""))


def parse_reference(n: int, text: str) -> Reference:
    label = None
    m = re.match(r"^\s*\[?(\d{1,4})[\].)]?\s+(?=\S)", text)
    body = text
    if m and (m.group(0).strip().endswith(("]", ".", ")")) or text.lstrip().startswith("[")
              or re.match(r"[A-ZÀ-Ý]", text[m.end():])):                    # "1 Sheppard JM, Young WB. ..." too
        label = int(m.group(1))
        body = text[m.end():]
    doi = DOI.search(body)
    doi_s = doi.group(0).rstrip(".,;)]}") if doi else None
    year = re.search(r"\(((?:19|20)\d{2})[a-z]?\)", body) or re.search(r"\b((?:19|20)\d{2})[a-z]?\b", body)
    sm = _SURNAME.match(body)
    return Reference(n, text, label, doi_s.lower() if doi_s else None, year.group(1) if year else None,
                     sm.group(1).strip() if sm else None)


def _abstract_labels(paras: list[Paragraph]) -> list[tuple[str, str]]:
    found = []
    for p in paras:
        if label_of(p.text) and p.words <= 6:
            found.append((label_of(p.text), p.text.rstrip(":. ")))
            continue
        for m in re.finditer(r"(?:^|(?<=[.\n])\s*)([A-Z][A-Za-z ]{1,40}?)\s*:", p.text):
            lab = label_of(m.group(1))
            if lab:
                found.append((lab, m.group(1).strip()))
    seen, out = set(), []
    for lab, written in found:
        if lab not in seen:
            seen.add(lab)
            out.append((lab, written))
    return out


def analyse(doc: Docx) -> Manuscript:
    ps = doc.paragraphs
    sections = [Section("front", None, 0)]
    in_abstract = False
    seen_labels: set[str] = set()
    keywords: list[str] = []
    keywords_next = False
    abstract_words_so_far = 0
    for p in ps:
        if not p.text:
            continue
        if keywords_next:
            keywords_next = False
            if p.words <= 40 and not heading_of(p.text):
                keywords = _split_keywords(p.text)
                sections[-1].paras.append(p.i)
                continue
        km = _KEYWORDS.match(p.text)
        if km and p.words <= 60 and (km.group(1).strip() or _looks_like_heading(p)):
            rest = km.group(1).strip()
            if rest:
                keywords = _split_keywords(rest)
            else:
                keywords_next = True
            if in_abstract or sections[-1].name in ("front", "abstract"):
                in_abstract = False
                sections.append(Section("keywords", p.text, p.i, [p.i]))
            else:
                sections[-1].paras.append(p.i)
            continue
        has_abstract = any(s.name == "abstract" for s in sections)
        am = re.match(r"^\s*(abstract|summary)\s*[:.\-–—]\s*(\S.*)$", p.text, re.I | re.S)
        if am and not has_abstract and words_of(am.group(2)) > 5:
            sections.append(Section("abstract", am.group(1), p.i, [p.i]))
            in_abstract, seen_labels, abstract_words_so_far = True, set(), p.words
            continue
        name = heading_of(p.text) if _looks_like_heading(p) else None
        if in_abstract:
            lab = label_of(p.text) if p.words <= 6 and _looks_like_heading(p) else None
            literal = norm(_NUMBERING.sub("", p.text).rstrip(":. "))
            structured = bool(seen_labels) or not sections[-1].paras
            if lab and lab not in seen_labels and structured and literal != "introduction" \
                    and name not in AFTER + BACK and abstract_words_so_far <= 600:
                seen_labels.add(lab)
                sections[-1].paras.append(p.i)
                continue
            if lab and lab in seen_labels:              # a label seen again: the main text begins
                name = name or "introduction"
            abstract_words_so_far += p.words
        if name == "abstract" and has_abstract:
            name = None
        if name:
            in_abstract = name == "abstract"
            if in_abstract:
                seen_labels, abstract_words_so_far = set(), 0
            sections.append(Section(name, p.text, p.i))
            continue
        sections[-1].paras.append(p.i)

    abstract = next((s for s in sections if s.name == "abstract"), None)
    abstract_paras = abstract.paras if abstract else []
    abs_ps = [ps[i] for i in abstract_paras]
    abstract_text = "\n".join(p.text for p in abs_ps)
    abstract_words = sum(p.words for p in abs_ps)
    if abstract and abstract_paras and abstract_paras[0] == abstract.start:      # "Abstract: text" in one paragraph
        abstract_words -= 1
        abstract_text = re.sub(r"^\s*(abstract|summary)\s*[:.\-–—]\s*", "", abstract_text, flags=re.I)

    # main text: from the first main section to the first back-matter or after-text section that follows it
    main_paras: list[int] = []
    started = False
    for s in sections:
        if s.name in MAIN:
            started = True
        elif started and s.name in END_OF_TEXT:
            break
        if started:
            main_paras.append(s.start)
            main_paras.extend(s.paras)
    captions: dict[str, dict[int, int]] = {"figure": {}, "table": {}}
    in_legends = {i for s in sections if s.name in ("legends", "tables") for i in s.paras}
    for p in ps:
        m = CAPTION.match(p.text)
        if not m or p.words < 2:
            continue
        rest = p.text[m.end("n"):].lstrip()
        if rest[:1] in tuple(".:|—–-") or p.words <= 3 or p.i in in_legends or "caption" in p.style:
            kind = "table" if m.group("kind").lower().startswith("tab") else "figure"
            captions[kind].setdefault(int(m.group("n")), p.i)
    for cells in doc.tables:
        for c in cells[:2]:
            m = CAPTION.match(c)
            if m and m.group("kind").lower().startswith("tab"):
                captions["table"].setdefault(int(m.group("n")), -1)
    caption_set = {i for d in captions.values() for i in d.values()}
    main_words = sum(ps[i].words for i in main_paras if i not in caption_set)

    mentions: dict[str, list[int]] = {"figure": [], "table": []}
    for i in main_paras:
        if i in caption_set:
            continue
        for m in MENTION.finditer(ps[i].text):
            kind = "table" if m.group("kind").lower().startswith("tab") else "figure"
            for n in _numbers(re.sub(r"(\d)\s*[a-z]\b", r"\1", m.group("nums"))):
                if n not in mentions[kind]:
                    mentions[kind].append(n)

    references: list[Reference] = []
    ref_sec = next((s for s in sections if s.name == "references"), None)
    if ref_sec:
        texts = []
        for i in ref_sec.paras:
            t = ps[i].text
            if not t:
                continue
            if CAPTION.match(t) and texts:
                break
            texts.extend(x.strip() for x in t.split("\n") if x.strip())
        references = [parse_reference(k, t) for k, t in enumerate(texts, 1)]

    cited_area = list(abstract_paras) + [i for i in main_paras]
    citations = _citations(ps, cited_area, len(references))

    statements: dict[str, list[int]] = {k: [] for k in STATEMENTS}
    for s in sections:
        key = {"conflicts": "conflitti", "funding": "finanziamenti", "data": "dati", "ai": "ia", "ethics": "etica",
               "contributions": "contributi"}.get(s.name)
        if key:
            statements[key].append(s.start)
    ref_idx = set(ref_sec.paras) if ref_sec else set()
    for p in ps:
        if not p.text or p.i in ref_idx:
            continue
        for key, rx in STATEMENTS.items():
            if rx.search(p.text) and p.i not in statements[key]:
                statements[key].append(p.i)
    full = "\n".join(p.text for p in ps if p.i not in ref_idx)
    registrations = []
    for name, rx in REGISTRIES:
        for m in rx.finditer(full):
            ident = m.group(0)
            if not any(r[1] == ident for r in registrations):
                registrations.append((name, ident, ident in abstract_text))
    guidelines = sorted({re.sub(r"\s+", " ", g) for g in GUIDELINES.findall(full)})

    title, title_para, running = None, None, None
    for p in ps:
        if p.text and p.level == 0 and p.style == "title":
            title, title_para = p.text, p.i
            break
    front = sections[0].paras
    for i in front:
        rm = _RUNNING.match(ps[i].text)
        if rm:
            running = rm.group(1).strip()
    if title is None:
        for i in front:
            t = ps[i].text
            if not t or _RUNNING.match(t) or _KEYWORDS.match(t) or re.match(r"^\s*(word count|manuscript|article type)",
                                                                             t, re.I):
                continue
            tm = _TITLE_LABEL.match(t)
            title, title_para = (tm.group(1).strip() if tm else t), i
            break

    from .claims import language_of          # local import: claims imports heavier modules
    lang, _ = language_of("\n".join(ps[i].text for i in (main_paras or [p.i for p in ps])[:400]))
    in_title = REVIEW_WORDS.search(title or "")
    in_abstract_all = REVIEW_WORDS.findall(abstract_text)
    protocol = PROTOCOL_WORDS.search(title or "") or (PROTOCOL_WORDS.search(abstract_text)
                                                      and re.search(r"\bthis protocol\b|\bwill be\b", abstract_text, re.I))
    if protocol:
        kind, reason = "protocollo", f"nel titolo o nell'abstract: '{protocol.group(0).strip()}'"
    elif in_title:
        kind, reason = "revisione", f"nel titolo: '{in_title.group(0)}'"
    elif len(in_abstract_all) >= 2 or any(w.upper() == "PRISMA" for w in in_abstract_all):
        kind, reason = "revisione", f"nell'abstract: {', '.join(sorted(set(in_abstract_all)))}"
    else:
        kind, reason = "originale", "nel titolo e nell'abstract non c'è 'systematic review', 'meta-analysis' o 'PRISMA'"
    front_text = "\n".join(ps[i].text for i in front)
    return Manuscript(
        doc=doc, title=title, title_para=title_para, running_title=running, abstract_paras=abstract_paras,
        abstract_text=abstract_text, abstract_words=max(abstract_words, 0), abstract_labels=_abstract_labels(abs_ps),
        keywords=keywords, sections=sections, main_paras=main_paras, main_words=main_words, captions=captions,
        mentions=mentions, references=references, citations=citations, statements=statements,
        registrations=registrations, guidelines=guidelines, language=lang, kind_guess=kind, kind_reason=reason,
        emails=len(EMAIL.findall(front_text)), orcids=len(ORCID.findall(full)),
        total_words=sum(p.words for p in ps))


def _split_keywords(text: str) -> list[str]:
    parts = re.split(r"\s*[;·•|]\s*|\s*,\s*(?![^()]*\))", text.strip().rstrip("."))
    if len(parts) == 1 and len(text.split()) > 6:
        parts = re.split(r"\s{2,}|\s*/\s*", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _citations(ps: list[Paragraph], area: list[int], n_refs: int) -> Citations:
    bracket, sup, paren, ay = [], [], [], []
    for i in area:
        p = ps[i]
        if not p.text or CAPTION.match(p.text):
            continue
        for m in re.finditer(r"\[(\s*\d{1,4}\s*[a-z]?(?:\s*(?:[-–—,;]|and)\s*\d{1,4}\s*[a-z]?)*\s*)\]", p.text):
            bracket.append((i, m.group(0), _numbers(m.group(1))))
        for off, s in p.sup:
            core = s.strip().strip(",")
            if not re.fullmatch(r"\d{1,4}(?:\s*[-–—,]\s*\d{1,4})*", core):
                continue
            if UNIT_BEFORE_SUP.search(p.raw[max(0, off - 8):off].rstrip()):
                continue
            sup.append((i, s.strip(), _numbers(core)))
        for m in re.finditer(r"\((\s*\d{1,3}(?:\s*[-–—,]\s*\d{1,3})*\s*)\)", p.text):
            nums = _numbers(m.group(1))
            before = p.text[max(0, m.start() - 12):m.start()]
            if nums and max(nums) <= max(n_refs, 1) and not re.search(r"(Table|Fig\.?|Figure|Eq\.?|Equation|=|\d)\s*$",
                                                                       before, re.I):
                paren.append((i, m.group(0), nums))
        for m in re.finditer(r"\(([^()]{4,300})\)", p.text):
            for item in re.split(r"\s*;\s*", m.group(1)):
                im = _AY_ITEM.match(item.strip())
                if im:
                    ay.append((i, im.group(1), im.group(2), item.strip()))
        for m in _AY_NARRATIVE.finditer(p.text):
            if m.group(1).lower() not in ("in", "the", "and", "of", "table", "figure"):
                ay.append((i, m.group(1), m.group(2), m.group(0)))
    if bracket:
        return Citations("numerica", "parentesi quadre", bracket, ay)
    if sup and len(sup) >= len(ay):
        return Citations("numerica", "apici", sup, ay)
    if paren and len(paren) > len(ay):
        return Citations("numerica", "parentesi tonde", paren, ay)
    if ay:
        return Citations("autore-anno", None, [], ay)
    return Citations("nessuna", None, [], [])


def read(path: Path) -> Manuscript:
    return analyse(read_docx(path))


def folder(home: Path) -> Path:
    return Path(home) / FOLDER


def list_files(home: Path) -> list[Path]:
    """The .docx files in ORPHEUS_HOME/manoscritti (not Word's temporary ~$ files), most recent first."""
    root = folder(home)
    if not root.is_dir():
        return []
    files = [p for p in root.iterdir() if p.is_file() and p.suffix.lower() == ".docx" and not p.name.startswith("~$")]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)
