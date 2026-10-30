"""T-039 (F2): numbers and statistics of a manuscript checked against each other (numbers-v2, deterministic).

Six checks, all "da guardare" at worst (a rule of thumb never proves an error):
- percentages that do not match n/N ("66/130 (51%)", or "66 males (51%)" with the total sample size);
- 95% confidence intervals that do not contain their estimate (or with the bounds swapped);
- p values in contrast with the words of the same sentence (p = 0.004 with "not significant", p = 0.20 with
  "significantly"), and impossible p values (above 1);
- numbers of the abstract (decimals and percentages) not found in the main text or in the tables;
- total sample size different between the abstract and the methods;
- (numbers-v2, 2026-10-17, from the first real manuscript) a percentage of the Discussion or Conclusion that is
  close to, but different from, a percentage of the Abstract, Results or tables about the same thing (same words in
  the sentence): "72.2%" in the Discussion and "72.1%" in the Results.
The details carry only the paragraph number and the values (R10: no sentences of the manuscript in the log)."""
from __future__ import annotations

import re

from .compliance import Check
from .manuscript import Manuscript

NUMBERS_VERSION = "numbers-v2"
GROUP = "Numeri e statistiche"
MAX_DETAILS = 8

_UNITS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
          "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
          "seventeen": 17, "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_WORD_NUMBER = (r"(?:(?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)(?:[-\s](?:one|two|three|four|five|six|"
                r"seven|eight|nine))?|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|ten)")
_PEOPLE = (r"(?:participants|athletes|players|patients|subjects|volunteers|individuals|people|men|women|males|females|"
           r"boys|girls|children|adolescents|adults|runners|swimmers|cyclists|soldiers|students|"
           r"(?:[a-z-]+\s+){1,2}(?:athletes|players|participants|patients))")
_SAMPLE = re.compile(rf"\b[nN]\s*=\s*(\d{{1,6}})\b|\b(\d{{1,6}}|{_WORD_NUMBER})\s+{_PEOPLE}\b", re.I)
_FRACTION = re.compile(r"\b(\d{1,6})\s*/\s*(\d{1,6})\s*\(\s*(\d{1,3}(?:\.\d+)?)\s*%\s*\)")
_COUNT_PCT = re.compile(r"\b(\d{1,6})\s+(?:[A-Za-z][A-Za-z-]*\s+){0,3}\(\s*(\d{1,3}(?:\.\d+)?)\s*%\s*\)")
_NUM = r"[-−–]?\d+(?:\.\d+)?"
_CI = re.compile(rf"({_NUM})\s*(?:%|[a-zA-Z]{{1,4}}(?:/[a-zA-Z]{{1,4}})?)?\s*[(\[,;]\s*95\s*%\s*"
                 rf"(?:CI|C\.I\.|confidence intervals?)\s*[:=,]?\s*({_NUM})\s*(?:%|[a-zA-Z]{{1,4}})?\s*"
                 rf"(?:to|,|;|–|—|-(?=\s*\d)|-(?=\s*-)|\s-\s)\s*({_NUM})", re.I)
_P = re.compile(r"(?<![A-Za-z])[pP]\s*(<|>|=|≤|≥|<=|>=)\s*(\d*\.\d+|\d+)")
_NEG = re.compile(r"\b(?:not|no|non-?)\s+(?:statistically\s+)?significant|\bnon-?significant|\binsignificant|"
                  r"\bdid not (?:differ|change|improve|increase|decrease)|\bno (?:significant |statistically significant )?"
                  r"(?:differences?|changes?|effects?|associations?)\b", re.I)
_POS = re.compile(r"\b(?:statistically )?significant(?:ly)?\b", re.I)
_SENTENCES = re.compile(r"(?<=[.;!?])\s+(?=[A-Z(])")
_PCT = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d+)?)\s*%(?!\s*(?:CI\b|C\.I\.|confidence))", re.I)
_WORD = re.compile(r"[A-Za-z][A-Za-z-]{4,}")
_GENERIC = set("""about above after again against among approximately around based being below between cases compared
compared could different during either every found further greater higher however indicate indicated lower might
observed occurring other overall percent percentage present proportion rather reported respectively results showed
shown similar since significant significantly study their there these those three total under value values which
while within would table tables figure""".split())
_ABSTRACT_NUM = re.compile(r"(?<![\d.])(\d+\.\d+|\d+(?=\s*%))(?![\d])")


def word_number(s: str) -> int | None:
    s = s.lower().replace("-", " ").split()
    if not s:
        return None
    if s[0].isdigit():
        return int(s[0])
    if s[0] in _UNITS:
        return _UNITS[s[0]]
    if s[0] in _TENS:
        return _TENS[s[0]] + (_UNITS.get(s[1], 0) if len(s) > 1 else 0)
    return None


def _float(s: str) -> float:
    return float(s.replace("−", "-").replace("–", "-"))


def _fmt(x: float) -> str:
    return f"{x:.1f}".rstrip("0").rstrip(".")


def _decimals(s: str) -> int:
    return len(s.split(".")[1]) if "." in s else 0


def sample_sizes(text: str) -> list[int]:
    out = []
    for m in _SAMPLE.finditer(text):
        if re.match(r"\s*\(\s*\d+(?:\.\d+)?\s*%", text[m.end():]):
            continue                                       # "66 males (51%)": a part of the sample, not the total
        n = int(m.group(1)) if m.group(1) else word_number(m.group(2))
        if n and n > 1:
            out.append(n)
    return out


def pct_matches(n: int, total: int, pct_text: str) -> bool:
    if total <= 0:
        return True
    exact = 100.0 * n / total
    tol = max(10 ** -_decimals(pct_text), 0.5) + 1e-9      # rounded or truncated, to the decimals written
    return abs(exact - float(pct_text)) <= tol


def ci_problem(est: str, lo: str, hi: str) -> str | None:
    e, a, b = _float(est), _float(lo), _float(hi)
    if a > b:
        return "estremi invertiti"
    if not (a <= e <= b):
        return "la stima è fuori dall'intervallo"
    return None


def p_values(sentence: str) -> list[tuple[str, float, str]]:
    out = []
    for m in _P.finditer(sentence):
        try:
            out.append((m.group(1), float(m.group(2)), m.group(0)))
        except ValueError:
            pass
    return out


def p_conflict(sentence: str) -> str | None:
    ps = [(op, v) for op, v, _ in p_values(sentence) if v <= 1]
    if not ps:
        return None
    small = [v for op, v in ps if (op in ("<", "≤", "<=") and v <= 0.05) or (op == "=" and v < 0.05)]
    large = [v for op, v in ps if op in ("=", ">", "≥", ">=") and v > 0.05]
    neg = bool(_NEG.search(sentence))
    pos = bool(_POS.search(_NEG.sub(" ", sentence)))
    if neg and small and not large and not pos:
        return "p sotto 0.05 ma il testo dice 'non significativo'"
    if pos and not neg and large and not small:
        return "p sopra 0.05 ma il testo dice 'significativo'"
    return None


def _norm_number(s: str) -> str:
    try:
        return repr(float(s))
    except ValueError:
        return s


def numbers_in(text: str) -> set[str]:
    return {_norm_number(x) for x in re.findall(r"(?<![\d.])\d+(?:\.\d+)?(?![\d])", text)}


class _Audit:
    def __init__(self, m: Manuscript):
        self.m = m
        self.out: list[Check] = []
        ps = m.doc.paragraphs
        self.body = [i for i in m.main_paras if ps[i].text]
        self.abstract = [i for i in m.abstract_paras if ps[i].text]
        self.tables = "\n".join("\n".join(t) for t in m.doc.tables)
        methods = m.section("methods")
        self.methods = [i for i in (methods.paras if methods else []) if ps[i].text]

    def text(self, i: int) -> str:
        return self.m.doc.paragraphs[i].text

    def add(self, id_, title, outcome, found, details=()):
        details = list(details)
        if len(details) > MAX_DETAILS:
            details = details[:MAX_DETAILS] + [f"... e altri {len(details) - MAX_DETAILS}"]
        self.out.append(Check(id_, GROUP, title, outcome, found, None, None, details))

    def total_n(self) -> int | None:
        sizes = [n for i in self.abstract + self.methods for n in sample_sizes(self.text(i))]
        return max(sizes) if sizes else None

    # ---- the five checks
    def sample(self):
        ab = [n for i in self.abstract for n in sample_sizes(self.text(i))]
        me = [n for i in self.methods for n in sample_sizes(self.text(i))]
        if not ab or not me:
            return
        if max(ab) == max(me):
            self.add("num_campione", "Numero di partecipanti", "ok", f"lo stesso nell'abstract e nei metodi (N = {max(ab)})")
        else:
            self.add("num_campione", "Numero di partecipanti", "guarda",
                     f"abstract N = {max(ab)}, metodi N = {max(me)}: controlla che il totale sia lo stesso")

    def percentages(self):
        total = self.total_n()
        seen, bad = 0, []
        for i in self.abstract + self.body:
            t = self.text(i)
            spans = []
            for m in _FRACTION.finditer(t):
                seen += 1
                spans.append(m.span())
                n, d, p = int(m.group(1)), int(m.group(2)), m.group(3)
                if not pct_matches(n, d, p):
                    bad.append(f"§{i}: {n}/{d} = {_fmt(100 * n / d)}%, scritto {p}%")
            if not total:
                continue
            for m in _COUNT_PCT.finditer(t):
                if any(a <= m.start() < b for a, b in spans):
                    continue
                n, p = int(m.group(1)), m.group(2)
                if n > total:
                    continue
                seen += 1
                if not pct_matches(n, total, p):
                    bad.append(f"§{i}: {n} su N = {total} = {_fmt(100 * n / total)}%, scritto {p}% (o un sottogruppo?)")
        if bad:
            self.add("num_percentuali", "Percentuali e conteggi", "guarda",
                     f"{len(bad)} su {seen} non tornano con n/N", bad)
        elif seen:
            self.add("num_percentuali", "Percentuali e conteggi", "ok", f"{seen} controllate, tornano tutte")

    def intervals(self):
        seen, bad = 0, []
        for i in self.abstract + self.body:
            for m in _CI.finditer(self.text(i)):
                seen += 1
                why = ci_problem(*m.groups())
                if why:
                    bad.append(f"§{i}: {m.group(1)} (IC 95% {m.group(2)}; {m.group(3)}): {why}")
        if bad:
            self.add("num_ic", "Intervalli di confidenza", "guarda", f"{len(bad)} su {seen} da controllare", bad)
        elif seen:
            self.add("num_ic", "Intervalli di confidenza", "ok", f"{seen} controllati: contengono la stima")

    def p_values(self):
        seen, bad, impossible = 0, [], []
        for i in self.abstract + self.body:
            for s in _SENTENCES.split(self.text(i)):
                ps = p_values(s)
                seen += len(ps)
                impossible += [f"§{i}: {raw}" for _, v, raw in ps if v > 1]
                why = p_conflict(s)
                if why:
                    bad.append(f"§{i}: {', '.join(raw for _, _, raw in ps)}: {why}")
        if impossible:
            self.add("num_p_impossibili", "Valori di p", "ko", f"{len(impossible)} valori di p sopra 1", impossible)
        if bad:
            self.add("num_p", "Valori di p e parole", "guarda", f"{len(bad)} frasi con p e parole in contrasto", bad)
        elif seen and not impossible:
            self.add("num_p", "Valori di p e parole", "ok", f"{seen} valori di p, coerenti con le parole vicine")

    def _pcts(self, paras_text: list[tuple[str, str]]) -> list[tuple[str, float, str, set[str]]]:
        out = []
        for where, t in paras_text:
            for sent in _SENTENCES.split(t):
                words = {w.lower() for w in _WORD.findall(sent)} - _GENERIC
                for m in _PCT.finditer(sent):
                    out.append((where, float(m.group(1)), m.group(1), words))
        return out

    def section_numbers(self):
        """numbers-v2: the same quantity written with two slightly different values in different sections."""
        def paras(names):
            got = []
            for name in names:
                s = self.m.section(name)
                got += [i for i in (s.paras if s else []) if self.text(i)]
            return got
        reported_idx = self.abstract + paras(("results", "results_discussion"))
        later_idx = paras(("discussion", "conclusion", "practical", "limitations"))
        if not reported_idx or not later_idx:
            return
        reported = self._pcts([(f"§{i}", self.text(i)) for i in reported_idx] + [("tabelle", self.tables)])
        exact = {v for _, v, _, _ in reported}
        seen, bad = 0, []
        for where, v, raw, words in self._pcts([(f"§{i}", self.text(i)) for i in later_idx]):
            seen += 1
            if v in exact:
                continue
            near = [(w2, r2) for w2, v2, r2, ws in reported if 0 < abs(v - v2) <= 1.0 and words & ws]
            if near:
                w2, r2 = near[0]
                bad.append(f"{where}: {raw}% (in {w2}: {r2}%)")
        if bad:
            self.add("num_sezioni", "Stessi numeri in sezioni diverse", "guarda",
                     f"{len(bad)} percentuali della discussione diverse da quelle dei risultati", bad)
        elif seen:
            self.add("num_sezioni", "Stessi numeri in sezioni diverse", "ok",
                     f"{seen} percentuali della discussione, nessuna in contrasto con i risultati")

    def abstract_numbers(self):
        if not self.abstract or not self.body:
            return
        found = numbers_in("\n".join(self.text(i) for i in self.body) + "\n" + self.tables)
        wanted, missing = [], []
        for i in self.abstract:
            t = self.text(i)
            for m in _ABSTRACT_NUM.finditer(t):
                if re.match(r"\s*%\s*(?:CI|C\.I\.|confidence)", t[m.end():], re.I) or m.group(1) in ("95",):
                    continue
                key = _norm_number(m.group(1))
                if key in {_norm_number(x) for x in wanted}:
                    continue
                wanted.append(m.group(1))
                if key not in found:
                    missing.append(f"§{i}: {m.group(1)}")
        if missing:
            self.add("num_abstract", "Numeri dell'abstract nel testo", "guarda",
                     f"{len(missing)} su {len(wanted)} non si trovano nel testo né nelle tabelle", missing)
        elif wanted:
            self.add("num_abstract", "Numeri dell'abstract nel testo", "ok",
                     f"{len(wanted)} numeri, tutti anche nel testo o nelle tabelle")


def audit(m: Manuscript) -> list[Check]:
    a = _Audit(m)
    for step in (a.sample, a.percentages, a.intervals, a.p_values, a.abstract_numbers, a.section_numbers):
        step()
    return a.out
