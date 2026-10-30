"""T-057 livello 1 — what the published articles report about statistics, transparency and structure: rules
stats-terms-v1. Deterministic: no model, no network; same pages → same result.

Decisions of the user (2026-10-27): start from part B level 1; the dictionary below approved as proposed (groups
confronto, modello, approccio, affidabilita, riporto, software, trasparenza, plus the structure counts); the tests are
also read by objective, crossing these items with the objective type written by Claude in cards-v2 (journal_profile);
migration 0016 approved at 16:17 ("Approvo"): text_method_item, text_structure, text_method_check.

Source: the pages (document_page) of the complete-article PDF of every article (card_store._complete_documents), the
same text the segments come from, with the same clean-up: words split by a line break or a ligature rejoined
(ingest_pdf.rejoin_hyphenated), running heads and page numbers left out (segments.repeated_lines).

Where a term is looked for (precision first: the bibliography would count every method named in a cited title):
  - never in the bibliography (from "References" in the second half of the text) nor in captions of figures;
  - the body starts at the first Introduction/Background heading followed by at least BODY_MIN_WORDS words (a
    structured abstract has its own "Background", "Methods", "Results" lines): before it is the front matter (title,
    abstract); a heading of the usual sections switches the section; "Participants", "Design"… switch to Methods only
    before the Results;
  - methods, models, tests and software: only in Methods and Results (what the article itself did); the threshold of p
    only in Methods (in the Results "p < 0.05" is a result, not the threshold);
  - transparency (guidelines, registration, data, ethics): front matter, Methods, Results and the back matter
    (Data availability, Ethics…), never Introduction or Discussion (where other studies or future work are named);
  - an article without a recognised Introduction keeps its items with section "?" only for transparency; the other
    groups are left out (no section, no certainty) and the structure counts are empty with a note.
Every item is one term in one sentence: the page where the sentence starts and the sentence as on the page (whitespace
collapsed; a sentence longer than QUOTE_MAX is cut around the term, still an exact piece of the page).

Structure (text_structure): words of Abstract (only with an "Abstract"/"Summary" heading, up to "Keywords" or the
body), Introduction, Methods, Results, Discussion (with Limitations, Practical applications, Conclusions) and the
whole body; figures and tables from the captions at the start of a line ("Fig. 3.", "Table 2"), counted only when
numbers start at 1 (else empty); references from the numbered list after the References heading (empty when not
numbered). The word counts include the text of tables printed inside a section (the PDF does not tell them apart).
"""
from __future__ import annotations

import bisect
import hashlib
import re
import sqlite3
from dataclasses import dataclass, field

from . import audit, card_store
from .cards import normalise
from .db import transaction
from .ids import new_id
from .ingest_pdf import rejoin_hyphenated
from .segments import _PAGE_NUMBER, _shape, repeated_lines

RULES_VERSION = "stats-terms-v1"
QUOTE_MAX = 600
BODY_MIN_WORDS = 150
I = re.I

# ---------------------------------------------------------------- dictionary (approved 2026-10-27)

M, R, F, B = "methods", "results", "front", "back"
SCOPE_STATS = frozenset({M, R})
SCOPE_ALPHA = frozenset({M})
SCOPE_OPEN = frozenset({F, M, R, B})

GROUPS = {
    "confronto": "Test di confronto", "modello": "Modelli", "approccio": "Altri approcci",
    "affidabilita": "Affidabilità e validità", "riporto": "Come si riporta", "software": "Software",
    "trasparenza": "Trasparenza",
}
GROUPS_EN = {
    "confronto": "Comparison tests", "modello": "Models", "approccio": "Other approaches",
    "affidabilita": "Reliability and validity", "riporto": "Reporting", "software": "Software",
    "trasparenza": "Transparency",
}


@dataclass(frozen=True)
class Term:
    key: str
    grp: str
    label: str
    patterns: tuple[re.Pattern, ...]
    scope: frozenset = SCOPE_STATS
    detail: str | None = None          # name of the detail reader (see _DETAILS)


def _t(key, grp, label, *pats, scope=SCOPE_STATS, detail=None, flags=I) -> Term:
    compiled = tuple(p if isinstance(p, re.Pattern) else re.compile(p, flags) for p in pats)
    return Term(key, grp, label, compiled, scope, detail)


def _cs(p: str) -> re.Pattern:
    """A case-sensitive pattern (acronyms that are ordinary words in lower case: SAS, GEE, ICC, R…)."""
    return re.compile(p)


TERMS: tuple[Term, ...] = (
    # confronto
    _t("t_test", "confronto", "t test", r"\bt[- ]?tests?\b", r"\bstudent'?s? t\b", r"\b(?:paired|independent|unpaired|"
       r"one|two)[- ]samples?\s+t\b", r"\bwelch'?s?\s+t\b", detail="t_test"),
    _t("anova", "confronto", "ANOVA", r"\banovas?\b", r"\banalys[ie]s of variance\b", detail="anova"),
    _t("anova_rm", "confronto", "ANOVA a misure ripetute", r"\brepeated[- ]measures?\s+(?:\w+[- ]way\s+)?"
       r"(?:anovas?|analys[ie]s of variance)\b", r"\brm[- ]?anovas?\b"),
    _t("ancova", "confronto", "ANCOVA", r"\bancovas?\b", r"\banalys[ie]s of covariance\b"),
    _t("manova", "confronto", "MANOVA", r"\bmanovas?\b", r"\bmultivariate analys[ie]s of variance\b"),
    _t("wilcoxon", "confronto", "Wilcoxon", r"\bwilcoxon\b"),
    _t("mann_whitney", "confronto", "Mann-Whitney", r"\bmann[- –]whitney\b"),
    _t("kruskal_wallis", "confronto", "Kruskal-Wallis", r"\bkruskal[- –]wallis\b"),
    _t("friedman", "confronto", "Friedman", r"\bfriedman'?s?\s+(?:test|anova|two[- ]way|analysis|rank)"),
    _t("chi_square", "confronto", "Chi quadrato", r"\bchi[- ]?squared?\b", r"χ ?2", r"χ²"),
    _t("fisher_exact", "confronto", "Fisher (esatto)", r"\bfisher'?s?\s+exact\b"),
    # modello
    _t("mixed_model", "modello", "Modelli misti / multilivello",
       r"(?<!way )(?<!way-)\b(?:linear\s+|generali[sz]ed\s+linear\s+)?mixed[- ](?:effects?[- ])?(?:linear[- ])?(?:regression\s+)?models?\b"
       r"(?!\s*(?:anova|analys[ie]s of variance|repeated))", r"\blinear mixed\b", r"\bmultilevel (?:models?|regression|"
       r"analys[ie]s|modell?ing)\b", r"\bhierarchical linear models?\b", r"\brandom[- ](?:intercepts?|slopes?)\b",
       _cs(r"\bG?LMMs?\b")),
    _t("gee", "modello", "GEE", r"\bgenerali[sz]ed estimating equations?\b", _cs(r"\bGEEs?\b")),
    _t("linear_regression", "modello", "Regressione lineare", r"\blinear regressions?\b", r"\bmultiple regressions?\b",
       r"\b(?:stepwise|hierarchical|multivariable) regressions?\b"),
    _t("logistic_regression", "modello", "Regressione logistica", r"\blogistic regressions?\b"),
    _t("survival", "modello", "Cox / sopravvivenza", r"\bcox (?:proportional|regression|models?|hazards?)\b",
       r"\bproportional[- ]hazards?\b", r"\bkaplan[- –]meier\b"),
    _t("poisson_nb", "modello", "Poisson / binomiale negativa", r"\bpoisson\b", r"\bnegative binomial\b"),
    # approccio
    _t("bayesian", "approccio", "Bayesiano", r"\bbayesian\b", r"\bbayes factors?\b", r"\bcredible intervals?\b",
       _cs(r"\bBF ?(?:10|01)\b")),
    _t("spm", "approccio", "SPM", r"\bstatistical parametric mapping\b", r"\bspm1d\b", _cs(r"\bSPM\b")),
    _t("mbi", "approccio", "Magnitude-based inference", r"\bmagnitude[- ]based (?:inferences?|decisions?)\b",
       _cs(r"\bMB[ID]\b"), r"\bsmallest worthwhile (?:change|effect|difference)\b"),
    _t("meta_analysis", "approccio", "Meta-analisi", r"\bmeta[- ]?analys[ie]s\b", r"\bmeta[- ]?analytic(?:al)?\b"),
    # affidabilita
    _t("icc", "affidabilita", "ICC", r"\bintra[- ]?class correlations?\b", _cs(r"\bICCs?\b")),
    _t("bland_altman", "affidabilita", "Bland-Altman", r"\bbland[- –]altman\b", r"\blimits? of agreement\b"),
    _t("sem_mdc", "affidabilita", "SEM / MDC", r"\bstandard errors? of (?:the )?measurements?\b",
       r"\bminimal(?:ly)? detectable (?:change|difference)\b", r"\bsmallest (?:detectable|real) (?:change|difference)\b",
       _cs(r"\bMDC(?:90|95)?\b")),
    _t("cv", "affidabilita", "Coefficiente di variazione", r"\bcoefficients? of variation\b",
       _cs(r"\bCV ?(?:%|\(%\)|=)")),
    _t("pearson", "affidabilita", "Correlazione di Pearson", r"\bpearson'?s?\s+(?:product[- ]moment\s+)?"
       r"(?:correlations?|coefficients?|r\b)", r"\bpearson correlation"),
    _t("spearman", "affidabilita", "Correlazione di Spearman", r"\bspearman'?s?\b"),
    _t("roc", "affidabilita", "ROC / AUC", r"\breceiver[- ]operating", _cs(r"\bROC\b")),
    # riporto
    _t("effect_size", "riporto", "Effect size (qualsiasi)", r"\beffect sizes?\b", r"\bcohen'?s?\s+d\b",
       r"\bhedges'?s?\s+g\b", r"\b(?:partial\s+)?eta[- ]squared\b", r"η ?p? ?2", r"η ?p? ?²", r"\bpartial eta\b"),
    _t("cohen_d", "riporto", "d di Cohen", r"\bcohen'?s?\s+d\b"),
    _t("hedges_g", "riporto", "g di Hedges", r"\bhedges'?s?\s+g\b"),
    _t("eta_squared", "riporto", "Eta quadrato", r"\b(?:partial\s+)?eta[- ]squared\b", r"η ?p? ?2", r"η ?p? ?²",
       r"\bpartial eta\b"),
    _t("ci", "riporto", "Intervalli di confidenza", r"\bconfidence intervals?\b", _cs(r"\b9[05] ?% ?CIs?\b"),
       _cs(r"\bCI ?9[05]")),
    _t("multiple_comparisons", "riporto", "Correzione per confronti multipli", r"\bbonferroni\b", r"\bholm\b",
       r"\btukey'?s?\b", r"\bbenjamini\b", r"\bfalse discovery rate\b", _cs(r"\bFDR\b"), r"\b(?:š|s)id(?:á|a)k\b",
       r"\bscheff(?:é|e)\b", r"\bpost[- ]hoc (?:tests?|comparisons?|analys[ie]s|pairwise)\b", r"\bdunn'?s?\b",
       r"\bmultiple comparisons?\b", detail="multiple_comparisons"),
    _t("power_analysis", "riporto", "Calcolo di potenza / campione",
       r"\b(?:a priori |post[- ]hoc |statistical )?power (?:analys[ie]s|calculations?|was (?:calculated|set|computed))\b",
       r"\bsample[- ]size (?:calculations?|estimations?|was (?:calculated|determined|estimated|computed))\b",
       r"\bg\*\s?power\b", r"\bgpower\b", r"\b\d{2} ?% (?:statistical )?power\b(?! ?(?:frequenc|spectr|output|density|band))", r"\bpower of (?:0?\.\d+|\d{2} ?%)",
       r"\b1 ?[-–] ?β\b", detail="power"),
    _t("normality", "riporto", "Test di normalità", r"\bshapiro[- –]wilks?\b", r"\bkolmogorov[- –]smirnov\b",
       r"\bnormality\b", r"\bnormally distributed\b", r"\bq[- –]q plots?\b", detail="normality"),
    _t("alpha", "riporto", "Soglia di significatività",
       r"\b(?:p|α|alpha)(?:[- ]values?)?\s*(?:<|≤|<=|=|was set (?:at|to)|set (?:at|to)|level (?:of|at))\s*0?\.(?:0[15]|10?)\b",
       r"\bsignificance (?:level|threshold)?\s*(?:was\s+)?(?:set\s+)?(?:at|to|of)\s+(?:p\s*[<≤=]\s*)?0?\.(?:0[15]|10?)\b",
       r"\bstatistical(?:ly)? significan(?:ce|t) (?:was )?(?:set|accepted|defined) (?:at|as) (?:p\s*[<≤=]\s*)?0?\.(?:0[15]|10?)\b",
       scope=SCOPE_ALPHA, detail="alpha"),
    # software
    _t("spss", "software", "SPSS", r"\bspss\b", detail="version"),
    _t("r", "software", "R", _cs(r"\bR (?:software|version|statistical|[Ff]oundation|[Cc]ore [Tt]eam|packages?|project|"
       r"environment|\((?:version|v)|v?\d+\.\d+\.\d+)"), _cs(r"\bR[- ]?[Ss]tudio\b"), r"\blme4\b", r"\bnlme\b",
       detail="version"),
    _t("stata", "software", "Stata", r"\bstata\b", detail="version"),
    _t("sas", "software", "SAS", _cs(r"\bSAS\b"), detail="version"),
    _t("graphpad", "software", "GraphPad Prism", r"\bgraphpad\b", _cs(r"\bPrism (?:\d|\(|version|software)"),
       detail="version"),
    _t("python", "software", "Python", r"\bpython\b", r"\bscipy\b", r"\bstatsmodels\b", r"\bpingouin\b",
       detail="version"),
    _t("matlab", "software", "MATLAB", r"\bmatlab\b", detail="version"),
    _t("jasp", "software", "JASP", _cs(r"\bJASP\b"), detail="version"),
    _t("jamovi", "software", "jamovi", r"\bjamovi\b", detail="version"),
    _t("excel", "software", "Excel", _cs(r"\bExcel\b"), detail="version"),
    _t("gpower", "software", "G*Power", r"\bg\*\s?power\b", r"\bgpower\b", detail="version"),
    # trasparenza
    _t("consort", "trasparenza", "CONSORT", _cs(r"\bCONSORT\b"), scope=SCOPE_OPEN),
    _t("strobe", "trasparenza", "STROBE", _cs(r"\bSTROBE\b"), scope=SCOPE_OPEN),
    _t("prisma", "trasparenza", "PRISMA", _cs(r"\bPRISMA\b"), scope=SCOPE_OPEN),
    _t("stard", "trasparenza", "STARD", _cs(r"\bSTARD\b"), scope=SCOPE_OPEN),
    _t("tidier", "trasparenza", "TIDieR", _cs(r"\bTIDieR\b"), scope=SCOPE_OPEN),
    _t("cosmin", "trasparenza", "COSMIN", _cs(r"\bCOSMIN\b"), scope=SCOPE_OPEN),
    _t("spirit", "trasparenza", "SPIRIT", _cs(r"\bSPIRIT\b"), scope=SCOPE_OPEN),
    _t("cert", "trasparenza", "CERT", _cs(r"\bCERT\b"), scope=SCOPE_OPEN),
    _t("grras", "trasparenza", "GRRAS", _cs(r"\bGRRAS\b"), scope=SCOPE_OPEN),
    _t("champ", "trasparenza", "CHAMP", _cs(r"\bCHAMP\b"), scope=SCOPE_OPEN),
    _t("registration", "trasparenza", "Registrazione", _cs(r"\bNCT ?\d{8}\b"), _cs(r"\bCRD ?\d{8,14}\b"),
       _cs(r"\bACTRN ?\d{14}\b"), _cs(r"\bISRCTN ?\d{8}\b"), _cs(r"\bDRKS ?\d{8}\b"), _cs(r"\bChiCTR[- ]?[A-Z0-9-]{6,}"),
       r"\bosf\.io/[a-z0-9]{4,}", r"\bclinicaltrials\.gov\b", _cs(r"\bPROSPERO\b"), r"\bpre-?regist(?:ered|ration)\b",
       r"\btrial registration\b", r"\bregistered (?:at|with|in|on) (?:the )?(?:clinicaltrials|prospero|osf|open science|"
       r"australian|german|chinese|international|isrctn|brazilian|japan|iranian|pan african)", scope=SCOPE_OPEN,
       detail="registration"),
    _t("data_availability", "trasparenza", "Dati disponibili",
       r"\bdata (?:that support[^.]{0,80} )?(?:are|is|will be|have been|were) (?:made )?(?:openly |publicly |freely )?"
       r"(?:available|deposited|shared)\b", r"\bavailable (?:from|upon|on) (?:the )?(?:corresponding author|"
       r"(?:reasonable )?requests?)\b", r"\bdata (?:are|is) not (?:publicly |openly )?(?:available|shared)\b",
       r"\b(?:osf\.io|zenodo|figshare|github\.com|datadryad|dryad)\b", scope=SCOPE_OPEN, detail="data"),
    _t("ethics", "trasparenza", "Approvazione etica", r"\bethics? committees?\b", r"\bethical (?:approval|committee|"
       r"review|board)\b", r"\binstitutional review boards?\b", _cs(r"\bIRB\b"), r"\bdeclaration of helsinki\b",
       r"\bresearch ethics (?:board|committee)\b", r"\bethics approval\b", scope=SCOPE_OPEN),
)
TERMS_BY_KEY = {t.key: t for t in TERMS}


# ---------------------------------------------------------------- details read next to a term

_VERSION = re.compile(r"[^.;:]{0,30}?\b(?:version|v\.?|ver\.?|release|statistics)?\s*\(?\s*v?(R ?20\d\d[ab]|20\d\d[ab]|\d{1,2}"
                      r"(?:\.\d{1,2}){0,3})\b", I)


def _detail(term: Term, sentence: str, match: re.Match) -> str | None:
    s = sentence
    low = s.casefold()
    if term.detail == "t_test":
        kinds = []
        if re.search(r"\bpaired\b|\bdependent\b|\brelated[- ]samples?\b", low):
            kinds.append("paired")
        if re.search(r"\bindependent\b|\bunpaired\b|\btwo[- ]samples?\b|\bwelch", low):
            kinds.append("independent")
        if re.search(r"\bone[- ]sample\b", low):
            kinds.append("one_sample")
        return ",".join(kinds) or None
    if term.detail == "anova":
        kinds = [k for k, p in (("one_way", r"\bone[- ]way\b"), ("two_way", r"\btwo[- ]way\b"),
                                ("three_way", r"\bthree[- ]way\b"), ("mixed", r"\bmixed[- ](?:design|model|factorial)?\s*"
                                                                              r"(?:anova|analys[ie]s of variance)|\bsplit[- ]plot\b"),
                                ("repeated", r"\brepeated[- ]measures?\b"), ("factorial", r"\bfactorial\b"))
                 if re.search(p, low)]
        return ",".join(kinds) or None
    if term.detail == "multiple_comparisons":
        kinds = [k for k, p in (("bonferroni", r"bonferroni"), ("holm", r"\bholm\b"), ("tukey", r"\btukey"),
                                ("fdr", r"benjamini|false discovery|\bfdr\b"), ("sidak", r"(?:š|s)id(?:á|a)k"),
                                ("scheffe", r"scheff"), ("dunn", r"\bdunn"), ("lsd", r"\blsd\b|least significant"))
                 if re.search(p, low)]
        return ",".join(kinds) or None
    if term.detail == "normality":
        kinds = [k for k, p in (("shapiro_wilk", r"shapiro"), ("kolmogorov_smirnov", r"kolmogorov"), ("qq_plot", r"q[- –]q"))
                 if re.search(p, low)]
        return ",".join(kinds) or None
    if term.detail == "power":
        if re.search(r"\b(?:no|not|without)\b[^.]{0,40}\b(?:power|sample[- ]size)\b|\b(?:power|sample[- ]size)"
                     r"[^.]{0,40}\b(?:was|were) not\b", low):
            return "not_done"
        return None
    if term.detail == "alpha":
        m = re.search(r"0?\.(0[15]|10?)\b", match.group(0))
        return f"0.{m.group(1)}" if m else None
    if term.detail == "version":
        m = _VERSION.match(s, match.end())
        return m.group(1).replace(" ", "") if m else None
    if term.detail == "registration":
        m = re.search(r"\b(NCT ?\d{8}|CRD ?\d{8,14}|ACTRN ?\d{14}|ISRCTN ?\d{8}|DRKS ?\d{8}|ChiCTR[- ]?[A-Z0-9-]{6,}|"
                      r"osf\.io/[a-z0-9]{4,})", s, I)
        return m.group(1).replace(" ", "") if m else None
    if term.detail == "data":
        if re.search(r"\bnot (?:publicly |openly )?(?:available|shared)\b|cannot be (?:made available|shared)", low):
            return "not_shared"
        if re.search(r"osf\.io|zenodo|figshare|github\.com|dryad|repository|openly available|publicly available", low):
            return "repository"
        if re.search(r"request|corresponding author", low):
            return "on_request"
        return None
    return None


# ---------------------------------------------------------------- reading the pages

MAJOR_ORDER = {"front": 0, "abstract": 0, "introduction": 1, "methods": 2, "results": 3, "discussion": 4, "back": 9}
_INTRO = re.compile(r"^(?:introduction|background)$")
_EXPLICIT_METHODS = re.compile(r"^(?:methods?|materials?andmethods?|methodology|statisticalanalys[ie]s|dataanalys[ie]s)$")
_DISCUSSION = re.compile(r"^(?:conclusions?.*|(?:general)?discussion|limitations?|strengthsandlimitations|perspectives?|"
                         r"implications?|futureresearch|futuredirections|practicalapplications?)$")
_KEYWORDS = re.compile(r"^\s*(?:key\s*words?|keywords)\b", I)
_CAPTION = re.compile(r"^\s*(fig(?:ure)?s?\.?|table)\s*(\d{1,2})(?![\d.]\d)(?:\s*[.:|\-–—]|\s+[A-Z(])", I)
_REF_NUM = re.compile(r"^\s*(?:\[(\d{1,3})\]|(\d{1,3})\.)\s+\S")
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\[])")

# Headings read on the "squashed" line (no spaces, no "|", lower case): "1 | Introduction" (Wiley), "1. Introductio n"
# and "a b s t r a c t" (letters spaced by the PDF) are the same heading. Same names as the segments' rules.
_SQ_NUMBER = re.compile(r"^(?:\d{1,2}(?:\.\d{1,2}){0,3}\.?|[ivx]{1,4}\.)")


def _squashed_rule(alternation: str) -> re.Pattern:
    return re.compile("^(?:" + re.sub(r"\\s[+*]", "", alternation) + ")$")


from .segments import _BACK as _BACK_NAMES, _SECTIONS as _SECTION_NAMES   # noqa: E402

_SQ_SECTION = _squashed_rule(_SECTION_NAMES)
_SQ_BACK_ONLY = _squashed_rule(_BACK_NAMES)
_SQ_REFERENCES = re.compile(r"^(?:references|referencelist|bibliography|literaturecited)$")
_SQ_ABSTRACT = re.compile(r"^(?:abstract|summary|abstractarticleinfo|articleinfoabstract)$")


def classify(line: str) -> tuple[str, str] | None:
    """("section" | "back" | "references" | "abstract", squashed name) for a heading line, else None."""
    if len(line) > 60:
        return None
    sq = re.sub(r"[\s|]+", "", line.casefold())
    sq = _SQ_NUMBER.sub("", sq).rstrip(":.")
    if not sq or not sq[0].isalpha():
        return None
    if _SQ_ABSTRACT.match(sq):
        return "abstract", "abstract"
    if _SQ_REFERENCES.match(sq):
        return "references", sq
    if _SQ_SECTION.match(sq):
        return "section", sq
    if _SQ_BACK_ONLY.match(sq) or _SQ_BACK_ONLY.match(re.sub(r"statements?$", "", sq)):
        return "back", sq
    return None


_LABELS = {"methods": "Methods", "method": "Methods", "materialsandmethods": "Materials and methods",
           "materialandmethods": "Materials and methods", "methodology": "Methodology",
           "statisticalanalysis": "Statistical analysis", "statisticalanalyses": "Statistical analysis",
           "dataanalysis": "Data analysis", "dataanalyses": "Data analysis", "results": "Results", "result": "Results",
           "discussion": "Discussion", "introduction": "Introduction", "background": "Background",
           "participants": "Participants", "subjects": "Subjects", "procedures": "Procedures", "procedure": "Procedures",
           "conclusions": "Conclusions", "conclusion": "Conclusions", "dataavailability": "Data availability",
           "dataavailabilitystatement": "Data availability", "funding": "Funding", "ethics": "Ethics",
           "studydesign": "Study design", "design": "Design", "limitations": "Limitations"}


def _printed(line: str) -> str:
    sq = _SQ_NUMBER.sub("", re.sub(r"[\s|]+", "", line.casefold())).rstrip(":.")
    if sq in _LABELS:
        return _LABELS[sq]
    t = " ".join(line.replace("|", " ").split())
    return re.sub(r"^(?:\d{1,2}(?:\.\d{1,2}){0,3}\.?|[IVX]{1,4}\.)\s+", "", t)


def _major(sq: str, current: str) -> str:
    if _INTRO.match(sq):
        return "introduction"
    if sq.startswith("result"):
        return "results"
    if _DISCUSSION.match(sq):
        return "discussion"
    if _EXPLICIT_METHODS.match(sq):
        return "methods"
    return "methods" if MAJOR_ORDER.get(current, 0) < MAJOR_ORDER["results"] else current


@dataclass
class Line:
    page: int
    text: str
    section: str = "front"          # front | abstract | introduction | methods | results | discussion | back
    heading: str | None = None      # the heading this line belongs to (as printed)
    caption: bool = False


@dataclass
class Reading:
    lines: list[Line]
    body_found: bool
    abstract_found: bool
    references: list[tuple[int, str]] = field(default_factory=list)   # (page, line) after the References heading
    notes: list[str] = field(default_factory=list)


def read_pages(pages: list[str]) -> Reading:
    pages, _ = rejoin_hyphenated([p or "" for p in pages])
    drop = repeated_lines(pages)
    raw: list[tuple[int, str]] = []
    for no, p in enumerate(pages, 1):
        for ln in p.splitlines():
            if not ln.strip() or _PAGE_NUMBER.match(ln) or _shape(ln) in drop:
                continue
            raw.append((no, ln.strip()))
    total = sum(len(t) for _, t in raw) or 1
    kinds = [classify(t) for _, t in raw]
    # 1) cut the bibliography (second half of the text)
    seen, cut = 0, len(raw)
    for i, (_, t) in enumerate(raw):
        if kinds[i] and kinds[i][0] == "references" and seen / total >= 0.5:
            cut = i
            break
        seen += len(t)
    body, refs, kinds = raw[:cut], raw[cut + 1:], kinds[:cut]
    # 2) start of the body: the first Introduction/Background heading followed by BODY_MIN_WORDS words before the next
    #    heading (a structured abstract has short "Background", "Methods" … parts)
    start = None
    heads = [i for i, k in enumerate(kinds) if k]
    for n, i in enumerate(heads):
        if kinds[i][0] != "section" or not _INTRO.match(kinds[i][1]):
            continue
        nxt = heads[n + 1] if n + 1 < len(heads) else len(body)
        if sum(len(t.split()) for _, t in body[i + 1:nxt]) >= BODY_MIN_WORDS:
            start = i
            break
    out: list[Line] = []
    section, heading, abstract_found = "front", None, False
    for i, (page, t) in enumerate(body):
        k = kinds[i]
        before_body = start is None or i < start
        if k and k[0] == "abstract" or (k and k[0] == "section" and k[1] == "summary" and before_body):
            if before_body and not abstract_found:
                section, heading, abstract_found = "abstract", _printed(t), True
            continue
        if k and k[0] == "back" and not (start is not None and i < start):
            section, heading = "back", _printed(t)
            continue
        if k and k[0] == "section":
            if start is not None and i < start:          # structured abstract: its headings are not the body's
                continue
            section, heading = _major(k[1], section if section != "back" else "discussion"), _printed(t)
            continue
        if section == "abstract" and _KEYWORDS.match(t):
            section, heading = "front", None
        out.append(Line(page, t, section, heading, bool(_CAPTION.match(t))))
    notes = []
    if start is None:
        notes.append("Introduzione non riconosciuta: sezioni e conteggi delle parole non sicuri")
        for ln in out:
            if ln.section != "back":
                ln.section = "?"
    if cut == len(raw):
        notes.append("bibliografia non riconosciuta")
    return Reading(out, start is not None, abstract_found, refs, notes)


def _sentences(lines: list[Line]) -> list[tuple[int, str, str, str | None]]:
    """Sentences of consecutive lines of the same section (captions apart): (page of start, section, sentence,
    heading)."""
    out: list[tuple[int, str, str, str | None]] = []
    block: list[Line] = []

    def flush():
        if not block:
            return
        starts, parts, pos = [], [], 0
        for ln in block:
            starts.append(pos)
            parts.append(ln.text)
            pos += len(ln.text) + 1
        raw = " ".join(parts)
        offset = 0
        for piece in _SENTENCE.split(raw):
            begin = raw.find(piece, offset)
            begin = offset if begin < 0 else begin
            offset = begin + len(piece)
            text = normalise(piece)
            if text:
                page = block[max(0, bisect.bisect_right(starts, begin) - 1)].page
                out.append((page, block[0].section, text, block[0].heading))
        block.clear()

    for ln in lines:
        if ln.caption or (block and (ln.section != block[0].section or ln.heading != block[0].heading)):
            flush()
        if ln.caption:
            continue
        block.append(ln)
    flush()
    return out


def _quote(sentence: str, match: re.Match) -> str:
    if len(sentence) <= QUOTE_MAX:
        return sentence
    a = max(0, match.start() - QUOTE_MAX // 2)
    b = min(len(sentence), a + QUOTE_MAX)
    a = max(0, b - QUOTE_MAX)
    if a > 0:
        a = sentence.find(" ", a) + 1 or a
    if b < len(sentence):
        b = sentence.rfind(" ", a, b) if sentence.rfind(" ", a, b) > match.end() else b
    return sentence[a:b].strip()


@dataclass
class Item:
    term: str
    grp: str
    detail: str | None
    section: str | None
    page: int
    quote: str

    @property
    def quote_sha256(self) -> str:
        return hashlib.sha256(self.quote.encode("utf-8")).hexdigest()


def _scope_ok(term: Term, section: str) -> bool:
    if section == "?":
        return term.scope is SCOPE_OPEN
    if section == "abstract":
        section = F
    return section in term.scope


def extract(reading: Reading) -> list[Item]:
    items: dict[tuple, Item] = {}
    for page, section, sentence, heading in _sentences(reading.lines):
        for term in TERMS:
            if not _scope_ok(term, section):
                continue
            for pat in term.patterns:
                m = pat.search(sentence)
                if m:
                    q = _quote(sentence, m)
                    key = (term.key, page, q)
                    if key not in items:
                        items[key] = Item(term.key, term.grp, _detail(term, sentence, m),
                                          heading if section != "?" else None, page, q)
                    break
    return list(items.values())


# ---------------------------------------------------------------- structure

@dataclass
class Structure:
    pages: int
    words: dict[str, int | None]
    figures: int | None
    tables: int | None
    references: int | None
    notes: list[str]


def _numbered(numbers: list[int]) -> int | None:
    """The last number of a list starting at 1 in which at least 80% of 1..max are present."""
    s = set(numbers)
    if not s or 1 not in s:
        return None
    top = max(s)
    while top > 1 and len(s & set(range(1, top + 1))) < 0.8 * top:
        top = max(n for n in s if n < top)
    return top if len(s & set(range(1, top + 1))) >= 0.8 * top else None


def structure(reading: Reading, pages: int) -> Structure:
    notes = list(reading.notes)
    words: dict[str, int | None] = {"abstract": None, "introduction": None, "methods": None, "results": None,
                                    "discussion": None, "body": None}
    if reading.body_found:
        for key in ("introduction", "methods", "results", "discussion"):
            words[key] = sum(len(ln.text.split()) for ln in reading.lines if ln.section == key and not ln.caption)
        words["body"] = sum(words[k] or 0 for k in ("introduction", "methods", "results", "discussion"))
        for key in ("methods", "results", "discussion"):
            if not words[key]:
                words[key] = None
                notes.append(f"sezione non riconosciuta: {key}")
    if reading.abstract_found:
        n = sum(len(ln.text.split()) for ln in reading.lines if ln.section == "abstract")
        words["abstract"] = n or None
    else:
        notes.append("titolo Abstract non trovato")
    figs, tabs = [], []
    for ln in reading.lines:
        m = _CAPTION.match(ln.text)
        if m:
            (tabs if m.group(1).casefold().startswith("table") else figs).append(int(m.group(2)))
    figures, tables = _numbered(figs), _numbered(tabs)
    if figs and figures is None:
        notes.append("figure non numerate da 1")
    if tabs and tables is None:
        notes.append("tabelle non numerate da 1")
    figures = figures if figs else 0
    tables = tables if tabs else 0
    refs = []
    for _, t in reading.references:
        if (classify(t) or ('',))[0] == "back" and len(refs) >= 3:
            break
        m = _REF_NUM.match(t)
        if m:
            refs.append(int(m.group(1) or m.group(2)))
    references = _numbered(refs)
    if references is None:
        notes.append("riferimenti non numerati" if reading.references else "bibliografia non trovata")
    return Structure(pages, words, figures, tables, references, notes)


# ---------------------------------------------------------------- database

def _pages(conn: sqlite3.Connection, document_id: str) -> list[str]:
    return [r["text"] or "" for r in conn.execute(
        "SELECT text FROM document_page WHERE document_id = ? ORDER BY page_no", (document_id,))]


def analyse(pages: list[str]) -> tuple[list[Item], Structure]:
    reading = read_pages(pages)
    return extract(reading), structure(reading, len(pages))


def _fingerprint(items: list[Item]) -> list[tuple]:
    return sorted((i.term, i.page, i.quote_sha256, i.detail or "", i.section or "") for i in items)


def plan(conn: sqlite3.Connection) -> dict:
    """For every complete article: its items and structure, and whether the database already has exactly them."""
    out = []
    for article_id, d in sorted(card_store._complete_documents(conn).items()):
        items, st = analyse(_pages(conn, d["id"]))
        old = [(r["term"], r["page_no"], hashlib.sha256(r["quote"].encode("utf-8")).hexdigest(), r["detail"] or "",
                r["section"] or "") for r in conn.execute(
            "SELECT term, page_no, quote, detail, section FROM text_method_item WHERE document_id = ? AND rules = ?",
            (d["id"], RULES_VERSION))]
        has_structure = conn.execute("SELECT 1 FROM text_structure WHERE document_id = ? AND rules = ?",
                                     (d["id"], RULES_VERSION)).fetchone() is not None
        other = conn.execute("""SELECT (SELECT count(*) FROM text_method_item WHERE document_id = ? AND rules <> ?)
                                     + (SELECT count(*) FROM text_structure WHERE document_id = ? AND rules <> ?)""",
                             (d["id"], RULES_VERSION, d["id"], RULES_VERSION)).fetchone()[0]
        same = sorted(old) == _fingerprint(items) and has_structure and not other
        out.append({"article_id": article_id, "document_id": d["id"], "items": items, "structure": st,
                    "azione": "uguali" if same else ("da rifare" if old or has_structure or other else "nuovi")})
    return {"regole": RULES_VERSION, "articoli": out}


def write(conn: sqlite3.Connection, the_plan: dict, *, actor: str = "orpheus metodi") -> dict:
    written = replaced = 0
    changed = [it for it in the_plan["articoli"] if it["azione"] != "uguali"]
    with transaction(conn):
        for it in changed:
            replaced += conn.execute("DELETE FROM text_method_item WHERE document_id = ?", (it["document_id"],)).rowcount
            conn.execute("DELETE FROM text_structure WHERE document_id = ?", (it["document_id"],))
            for i in it["items"]:
                conn.execute("""INSERT INTO text_method_item (id, article_id, document_id, rules, grp, term, detail,
                                    section, page_no, quote) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                             (new_id("text_method_item"), it["article_id"], it["document_id"], RULES_VERSION, i.grp,
                              i.term, i.detail, i.section, i.page, i.quote))
                written += 1
            st: Structure = it["structure"]
            conn.execute("""INSERT INTO text_structure (document_id, article_id, rules, pages, words_abstract,
                                words_introduction, words_methods, words_results, words_discussion, words_body, figures,
                                tables, references_n, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                         (it["document_id"], it["article_id"], RULES_VERSION, st.pages, st.words["abstract"],
                          st.words["introduction"], st.words["methods"], st.words["results"], st.words["discussion"],
                          st.words["body"], st.figures, st.tables, st.references, "; ".join(st.notes) or None))
        if changed:
            audit.record(conn, actor=actor, action="write_text_methods",
                         payload={"regole": RULES_VERSION, "articoli": len(changed), "voci_scritte": written,
                                  "voci_sostituite": replaced}, in_transaction=True)
    return {"articoli_cambiati": len(changed), "voci_scritte": written, "voci_sostituite": replaced}


def summary(the_plan: dict) -> dict:
    arts = the_plan["articoli"]
    per_term: dict[str, int] = {}
    for it in arts:
        for key in {i.term for i in it["items"]}:
            per_term[key] = per_term.get(key, 0) + 1
    return {"articoli": len(arts),
            "azioni": {a: sum(1 for it in arts if it["azione"] == a) for a in ("nuovi", "da rifare", "uguali")},
            "voci": sum(len(it["items"]) for it in arts),
            "articoli_con_introduzione": sum(1 for it in arts if not any("Introduzione" in n for n in it["structure"].notes)),
            "articoli_per_voce": {k: per_term[k] for k in sorted(per_term, key=lambda k: -per_term[k])}}


def status(conn: sqlite3.Connection) -> dict:
    q = lambda sql: conn.execute(sql, (RULES_VERSION,)).fetchone()[0]   # noqa: E731
    return {"voci": q("SELECT count(*) FROM text_method_item WHERE rules = ?"),
            "articoli_con_voci": q("SELECT count(DISTINCT article_id) FROM text_method_item WHERE rules = ?"),
            "articoli_con_struttura": q("SELECT count(*) FROM text_structure WHERE rules = ?")}


# ---------------------------------------------------------------- hand check of a sample (like T-027)

REPORTS_DIR = "rapporti"
CONTROL_PREFIX = "metodi_controllo"
PER_TERM = 5
CONTROL_COLUMNS = ["n", "articolo", "doi", "file_pdf", "pagina", "sezione", "gruppo", "voce", "nome_voce", "dettaglio",
                   "frase", "cosa_controllare", "giusto", "note", "id_documento", "codice_voce", "impronta_frase"]
_CONTROL_ORIGINAL = re.compile(rf"^{CONTROL_PREFIX}_(\d+)_\d{{4}}-\d{{2}}-\d{{2}}(_\d{{6}})?$")
TODO = ("la frase alla pagina indicata dice che l'articolo stesso usa (o riporta, o dichiara) la voce; il dettaglio, se "
        "c'è, è giusto. N se la voce non c'entra (es. citata da un altro studio, o un'altra cosa con lo stesso nome)")


def control_rows(conn: sqlite3.Connection, *, rules: str = RULES_VERSION) -> list[dict]:
    """Reproducible sample: for every term up to PER_TERM items, drawn with a fixed seed among the items not yet
    checked (one per article at most for each term)."""
    import random
    checked = {(r[0], r[1], r[2], r[3]) for r in conn.execute(
        "SELECT document_id, term, page_no, quote_sha256 FROM text_method_check WHERE rules = ?", (rules,))}
    by_term: dict[str, list[sqlite3.Row]] = {}
    for r in conn.execute("""SELECT i.*, a.title, a.doi, d.original_path FROM text_method_item i
                             JOIN article a ON a.id = i.article_id JOIN document d ON d.id = i.document_id
                             WHERE i.rules = ? ORDER BY i.term, i.article_id, i.page_no, i.quote""", (rules,)):
        sha = hashlib.sha256(r["quote"].encode("utf-8")).hexdigest()
        if (r["document_id"], r["term"], r["page_no"], sha) not in checked:
            by_term.setdefault(r["term"], []).append(r)
    rows = []
    for t in TERMS:
        pool = by_term.get(t.key, [])
        rng = random.Random(f"{rules}:{t.key}")
        rng.shuffle(pool)
        seen_articles, picked = set(), []
        for r in pool:
            if r["article_id"] in seen_articles:
                continue
            seen_articles.add(r["article_id"])
            picked.append(r)
            if len(picked) == PER_TERM:
                break
        for r in picked:
            rows.append({"articolo": r["title"], "doi": r["doi"] or "",
                         "file_pdf": (r["original_path"] or "").replace("\\", "/").rsplit("/", 1)[-1],
                         "pagina": r["page_no"], "sezione": r["section"] or "", "gruppo": GROUPS[t.grp], "voce": t.label,
                         "nome_voce": t.key, "dettaglio": r["detail"] or "", "frase": r["quote"],
                         "cosa_controllare": TODO, "giusto": "", "note": "", "id_documento": r["document_id"],
                         "codice_voce": t.key, "impronta_frase": hashlib.sha256(r["quote"].encode("utf-8")).hexdigest()})
    for n, r in enumerate(rows, 1):
        r["n"] = n
    return rows


def _control_bytes(rows: list[dict]) -> bytes:
    import csv
    import io
    buf = io.StringIO(newline="")
    w = csv.DictWriter(buf, fieldnames=CONTROL_COLUMNS, delimiter=";", lineterminator="\r\n")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return buf.getvalue().encode("utf-8-sig")


def _write_control(rows: list[dict], home, now=None):
    from datetime import datetime
    from pathlib import Path
    now = now or datetime.now()
    data = _control_bytes(rows)
    folder = Path(home) / REPORTS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    for old in sorted(folder.glob(f"{CONTROL_PREFIX}_{len(rows)}_*.csv")):
        if _CONTROL_ORIGINAL.match(old.stem) and old.read_bytes() == data:
            return old, False
    path = folder / f"{CONTROL_PREFIX}_{len(rows)}_{now.strftime('%Y-%m-%d')}.csv"
    if path.exists():
        path = path.with_name(f"{path.stem}_{now.strftime('%H%M%S')}.csv")
    path.write_bytes(data)
    return path, True


def _latest_filled(home):
    from pathlib import Path
    folder = Path(home) / REPORTS_DIR
    found = [p for p in folder.glob(f"{CONTROL_PREFIX}_*.csv") if not _CONTROL_ORIGINAL.match(p.stem)] \
        if folder.is_dir() else []
    return max(found, key=lambda p: (p.stat().st_mtime, p.name)) if found else None


def _verdict(cell) -> str:
    v = (cell or "").strip().upper()[:1]
    return v if v in ("S", "N") else ("" if not v else "?")


def score_control(path) -> dict:
    from .benchmark import read_filled
    rows, info = read_filled(path, need={"codice_voce", "id_documento", "impronta_frase", "giusto"})
    verdicts = [_verdict(r.get("giusto")) for r in rows]

    def prec(vs):
        g, b = vs.count("S"), vs.count("N")
        return {"giusti": g, "sbagliati": b, "non_compilati": vs.count(""), "non_validi": vs.count("?"),
                "precisione": round(g / (g + b), 3) if g + b else None}
    per_term = {k: prec([v for v, r in zip(verdicts, rows) if r.get("codice_voce") == k])
                for k in dict.fromkeys(r.get("codice_voce", "") for r in rows)}
    per_group = {g: prec([v for v, r in zip(verdicts, rows)
                          if TERMS_BY_KEY.get((r.get("codice_voce") or "").strip(), TERMS[0]).grp == g]) for g in GROUPS}
    return {"file": str(path), **info, "righe": len(rows), "precisione_complessiva": prec(verdicts),
            "per_gruppo": per_group, "per_voce": per_term,
            "sbagliate": [{"n": r.get("n"), "voce": r.get("codice_voce"), "note": (r.get("note") or "").strip()}
                          for v, r in zip(verdicts, rows) if v == "N"]}


def record_verdicts(conn: sqlite3.Connection, path, *, rules: str = RULES_VERSION, actor: str = "orpheus metodi") -> int:
    from pathlib import Path

    from .benchmark import read_filled
    rows, _ = read_filled(path, need={"codice_voce", "id_documento", "impronta_frase", "giusto"})
    n = 0
    with transaction(conn):
        for r in rows:
            v = _verdict(r.get("giusto"))
            sha = (r.get("impronta_frase") or "").strip()
            if v not in ("S", "N") or not re.fullmatch(r"[0-9a-f]{64}", sha):
                continue
            try:
                page = int(str(r.get("pagina") or "").strip().lstrip("'"))
            except ValueError:
                continue
            key = ((r.get("id_documento") or "").strip(), rules, (r.get("codice_voce") or "").strip(), page, sha)
            old = conn.execute("""SELECT verdict FROM text_method_check WHERE document_id = ? AND rules = ? AND term = ?
                                  AND page_no = ? AND quote_sha256 = ?""", key).fetchone()
            if old is not None and old[0] == v:
                continue
            if conn.execute("SELECT 1 FROM document WHERE id = ?", (key[0],)).fetchone() is None:
                continue
            conn.execute("""INSERT INTO text_method_check (document_id, rules, term, page_no, quote_sha256, verdict,
                                source_file) VALUES (?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT (document_id, rules, term, page_no, quote_sha256)
                            DO UPDATE SET verdict = excluded.verdict, source_file = excluded.source_file,
                                          checked_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')""",
                         (*key, v, Path(path).name))
            n += 1
        if n:
            audit.record(conn, actor=actor, action="text_methods_hand_check",
                         payload={"file": Path(path).name, "giudizi": n}, in_transaction=True)
    return n


def check_cycle(conn: sqlite3.Connection, home, now=None) -> dict:
    """What metodi-controllo does now: record a filled file (and give its precision); otherwise write the CSV of a
    sample not yet checked; otherwise the precision recorded so far."""
    filled = _latest_filled(home)
    if filled is not None:
        res = score_control(filled)
        res["giudizi_scritti"] = record_verdicts(conn, filled)
        return {"modo": "risultati", **res, "nel_database": checked_precision(conn)}
    rows = control_rows(conn)
    if rows:
        path, written = _write_control(rows, home, now)
        return {"modo": "file da compilare", "file_csv": str(path), "nuovo_file": written, "righe": len(rows),
                "file_da_salvare": str(path.with_name(f"{path.stem}_compilato.csv"))}
    return {"modo": "niente da controllare", "nel_database": checked_precision(conn)}


def checked_precision(conn: sqlite3.Connection, rules: str = RULES_VERSION) -> dict:
    """Precision from the S/N recorded so far (overall and per group); None with no verdict."""
    rows = list(conn.execute("SELECT term, verdict FROM text_method_check WHERE rules = ?", (rules,)))
    def prec(vs):
        g, b = vs.count("S"), vs.count("N")
        return {"giusti": g, "sbagliati": b, "precisione": round(g / (g + b), 3) if g + b else None}
    return {"complessiva": prec([r[1] for r in rows]),
            "per_gruppo": {g: prec([r[1] for r in rows if TERMS_BY_KEY.get(r[0], TERMS[0]).grp == g]) for g in GROUPS}}
