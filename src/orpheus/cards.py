"""Article cards, contract `cards-v1` (T-019, ADR-0008): the JSON Claude fills in during a session from a work packet,
and the deterministic validators that decide whether ORPHEUS accepts it (ADR-0003: a task is complete only when its
output passes the validators).

This module has no database access: the text to check against is given as two dicts, {page number: text} and
{section ord: text}. card_store.py takes it from the database (submit) and controlla-scheda from the packet file.

Text matching (`citazione_nel_testo`): a quote must be a continuous piece of the page or section it names. Both are
compared after the same normalisation — Unicode NFKC (ligatures, "µ"), dashes and minus signs → "-", "◦"/"º" → "°",
typographic quotes → plain ones, soft hyphens removed, case folded — and with ALL whitespace removed (PDF text breaks
lines and table cells anywhere). A hyphen at the end of a line in the page is tried both ways (word split by
hyphenation joined, or kept). Numbers (`numeri_nella_citazione`) are compared as written, with their sign: "-0.81"
in the value needs "-0.81" (or "−0.81") in the quote; "0.81" does not match "−0.81".
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

CARD_VERSION = "cards-v1"
CARD_FORMAT = "orpheus-scheda-v1"
PACKET_FORMAT = "orpheus-pacchetto-v1"

# field -> (description for the instructions, required with a value, numeric kind)
FIELDS: dict[str, dict] = {
    "disegno_studio": {"obbligatorio": True, "tipo": "testo",
                       "descrizione": "Disegno dello studio (es. randomised controlled trial, cross-sectional, prospective cohort, "
                                      "case-control, crossover, laboratory/biomechanical study). Se l'articolo non lo nomina, "
                                      "origin 'interpreted' con la frase da cui si deduce."},
    "partecipanti_numero": {"obbligatorio": True, "tipo": "intero",
                            "descrizione": "Numero di partecipanti analizzati o reclutati, come scritto (uno per riga se ci sono "
                                           "gruppi: usa 'gruppo'). Solo il numero nel valore, es. '15'."},
    "partecipanti_sesso": {"obbligatorio": False, "tipo": "testo",
                           "descrizione": "Composizione per sesso, es. '9 male, 6 female' o 'female only'."},
    "partecipanti_eta": {"obbligatorio": False, "tipo": "eta",
                         "descrizione": "Età come riportata (media (DS) o intervallo), con unità, es. valore '9.95 (1.35)', unita 'years'."},
    "partecipanti_livello": {"obbligatorio": False, "tipo": "testo",
                             "descrizione": "Livello sportivo o di attività (es. elite, professional, recreational, active children)."},
    "partecipanti_sport": {"obbligatorio": False, "tipo": "testo",
                           "descrizione": "Sport o attività dei partecipanti (es. soccer, running)."},
    "intervento_esposizione": {"obbligatorio": False, "tipo": "testo",
                               "descrizione": "Intervento, condizione o esposizione studiata."},
    "confronto": {"obbligatorio": False, "tipo": "testo",
                  "descrizione": "Gruppo o condizione di confronto (controllo, placebo, altra condizione)."},
    "misura_principale": {"obbligatorio": True, "tipo": "misura",
                          "descrizione": "Misure principali (outcome): 'nome' della misura; 'valore' con i numeri riportati, "
                                         "'unita' se c'è, 'gruppo' se riguarda un gruppo. Una riga per misura e gruppo."},
    "risultato_chiave": {"obbligatorio": True, "tipo": "testo",
                         "descrizione": "Risultati principali, brevi, con i numeri come scritti (es. 'r = -0.81, p < .01')."},
    "limite": {"obbligatorio": False, "tipo": "testo",
               "descrizione": "Limiti dichiarati dagli autori (una riga per limite)."},
    "conclusione_autori": {"obbligatorio": True, "tipo": "testo",
                           "descrizione": "Conclusione degli autori, breve."},
}
FIELD_NAMES = tuple(FIELDS)
ORIGINS = ("reported", "interpreted", "not_available")

QUOTE_MIN, QUOTE_MAX = 12, 500
VALUE_MAX = 300

CHECKS = ("formato", "pacchetto", "campi_completi", "posizione", "citazione_nel_testo", "numeri_nella_citazione",
          "unita", "valori_plausibili")


# ---------------------------------------------------------------- contract (Pydantic)

def _blank_to_none(v):
    if isinstance(v, str) and not v.strip():
        return None
    return v


class CardValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    campo: Literal[FIELD_NAMES]  # type: ignore[valid-type]
    nome: str | None = Field(default=None, max_length=200)
    valore: str | None = Field(default=None, max_length=VALUE_MAX)
    unita: str | None = Field(default=None, max_length=40)
    gruppo: str | None = Field(default=None, max_length=200)
    pagina: int | None = Field(default=None, ge=1)
    sezione: int | None = Field(default=None, ge=0)
    citazione: str | None = Field(default=None, max_length=QUOTE_MAX)
    origin: Literal["reported", "interpreted", "not_available"]
    nota: str | None = Field(default=None, max_length=500)

    @field_validator("nome", "valore", "unita", "gruppo", "citazione", "nota", mode="before")
    @classmethod
    def _blanks(cls, v):
        return _blank_to_none(v)


class Card(BaseModel):
    model_config = ConfigDict(extra="forbid")

    formato: Literal["orpheus-scheda-v1"]
    versione: Literal["cards-v1"]
    workflow_run_id: str = Field(pattern=r"^run_[0-9A-HJKMNP-TV-Z]{26}$")
    article_id: str = Field(pattern=r"^art_[0-9A-HJKMNP-TV-Z]{26}$")
    modello: str = Field(min_length=3, max_length=80)
    valori: list[CardValue] = Field(min_length=1, max_length=200)
    note_generali: str | None = Field(default=None, max_length=2000)


def json_schema() -> dict:
    return Card.model_json_schema()


# ---------------------------------------------------------------- text normalisation

_TRANSLATE = str.maketrans({
    "−": "-", "–": "-", "—": "-", "‐": "-", "‑": "-", "‒": "-", "―": "-", "﹣": "-",
    "－": "-", "◦": "°", "º": "°", "˚": "°", "∘": "°",
    "‘": "'", "’": "'", "‚": "'", "′": "'", "“": '"', "”": '"', "„": '"', "″": '"',
    "­": None, "​": None, "‌": None, "‍": None, "﻿": None,
    "·": ".", "⋅": ".", "‧": ".",
})
_WS = re.compile(r"\s+")
_LINE_HYPHEN = re.compile(r"-[ \t]*\r?\n\s*")


def normalise(text: str) -> str:
    """NFKC + the character map above; whitespace kept (collapsed to single spaces)."""
    t = unicodedata.normalize("NFKC", text or "").translate(_TRANSLATE)
    return _WS.sub(" ", t).strip()


def squash(text: str) -> str:
    """normalise + case folded + no whitespace at all: the form quotes and pages are compared in."""
    t = unicodedata.normalize("NFKC", text or "").translate(_TRANSLATE).casefold()
    return _WS.sub("", t)


def text_forms(text: str) -> tuple[str, ...]:
    """The squashed forms of a page or section: hyphens at line ends joined, and kept."""
    t = unicodedata.normalize("NFKC", text or "").translate(_TRANSLATE)
    joined = _LINE_HYPHEN.sub("", t)
    forms = {squash(joined), squash(t)}
    return tuple(sorted(forms))


def quote_in_text(quote: str, forms: tuple[str, ...]) -> bool:
    q = squash(quote)
    return bool(q) and any(q in f for f in forms)


_NUMBER = re.compile(r"(?<![\w.)\]])-?(?:\d+(?:[.,]\d+)*|[.,]\d+)")
_WORD_NUMBERS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90, "hundred": 100,
}
_WORD_RE = re.compile(r"[a-z]+")


def numbers(text: str) -> list[str]:
    """Number tokens as written (after normalise), with a leading '-' when it is a sign (not a range '18-25')."""
    return _NUMBER.findall(normalise(text))


def _plain(token: str) -> str:
    return token.replace(",", "")


_COMPOUND = re.compile(r"\b(twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)[\s-]+(one|two|three|four|five|six|seven|"
                       r"eight|nine)\b")


def number_words(text: str) -> set[str]:
    """Numbers written in English words in the text: 'fifteen' -> '15', 'Twenty-seven' -> '27'."""
    t = normalise(text).casefold()
    out = {str(_WORD_NUMBERS[w]) for w in _WORD_RE.findall(t) if w in _WORD_NUMBERS}
    out |= {str(_WORD_NUMBERS[a] + _WORD_NUMBERS[b]) for a, b in _COMPOUND.findall(t)}
    return out


def missing_numbers(value: str, quote: str) -> list[str]:
    """Numbers of the value that are not in the quote (same token, or same digits without thousands commas;
    small integers may be written as words in the quote: 'fifteen')."""
    have = numbers(quote)
    plain = {_plain(t) for t in have}
    words = number_words(quote)
    out = []
    for tok in numbers(value):
        if tok in have or _plain(tok) in plain:
            continue
        if not tok.startswith("-") and tok.isdigit() and str(int(tok)) in words:
            continue
        out.append(tok)
    return out


# ---------------------------------------------------------------- units

# Canonical unit -> spellings accepted in the quote. A unit not listed must be written in the quote as given.
UNIT_ALIASES: dict[str, tuple[str, ...]] = {
    "years": ("years", "year", "yrs", "yr", "y"),
    "months": ("months", "month", "mo"),
    "°": ("°", "deg", "degrees", "degree"),
    "%": ("%", "percent", "per cent"),
    "m/s": ("m/s", "m.s-1", "ms-1", "m s-1", "m·s-1", "m.s−1"),
    "km/h": ("km/h", "km.h-1", "kmh-1", "km h-1"),
    "kg": ("kg",), "g": ("g",), "cm": ("cm",), "mm": ("mm",), "m": ("m",), "km": ("km",),
    "s": ("s", "sec", "seconds"), "ms": ("ms", "milliseconds"), "min": ("min", "minutes"), "h": ("h", "hours"),
    "n": ("n",), "kn": ("kn",), "bw": ("bw", "body weight", "bodyweight"), "n/kg": ("n/kg", "n.kg-1", "nkg-1", "n kg-1"),
    "nm": ("nm", "n.m", "n·m", "n m"), "nm/kg": ("nm/kg", "n.m/kg", "n·m/kg", "nm.kg-1", "n.m.kg-1", "nmkg-1"),
    "w": ("w",), "w/kg": ("w/kg", "w.kg-1", "wkg-1", "w kg-1"),
    "hz": ("hz",), "bpm": ("bpm", "beats/min", "beats.min-1", "beatsmin-1"),
    "ml/kg/min": ("ml/kg/min", "ml.kg-1.min-1", "mlkg-1min-1", "ml kg-1 min-1", "ml·kg-1·min-1"),
    "l/min": ("l/min", "l.min-1", "lmin-1", "l min-1"),
    "kg/m2": ("kg/m2", "kg.m-2", "kgm-2", "kg m-2", "kg/m^2"),
    "mmol/l": ("mmol/l", "mmol.l-1", "mmoll-1", "mmol l-1"),
    "au": ("au", "a.u.", "arbitrary units"), "rpe": ("rpe",), "°/s": ("°/s", "deg/s", "°.s-1", "°s-1", "deg.s-1"),
}
_UNIT_INDEX = {squash(a): canon for canon, aliases in UNIT_ALIASES.items() for a in (canon, *aliases)}


def unit_in_quote(unit: str, quote: str) -> bool:
    """The unit (or one of its accepted spellings) is written in the quote. Short units must stand alone
    ('m' does not count inside 'mass')."""
    q = normalise(quote).casefold()
    canon = _UNIT_INDEX.get(squash(unit))
    spellings = {unit} | (set(UNIT_ALIASES[canon]) | {canon} if canon else set())
    for s in spellings:
        s = normalise(s).casefold()
        if not s:
            continue
        if s[0].isalnum() and s[-1].isalnum():
            if re.search(rf"(?<![a-z]){re.escape(s)}(?![a-z])", q):
                return True
            # compound units ("nm.kg-1", "m/s"): PDF text may put spaces inside them ("Nm·kg −1")
            if not squash(s).isalnum() and squash(s) in squash(q):
                return True
        elif s in q or squash(s) in squash(q):
            return True
    return False


# ---------------------------------------------------------------- checks

@dataclass
class CheckResult:
    name: str
    ok: bool
    problems: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"controllo": self.name, "esito": "ok" if self.ok else "non superato", "problemi": self.problems}


@dataclass
class Outcome:
    checks: list[CheckResult]
    card: Card | None = None

    @property
    def accepted(self) -> bool:
        return self.card is not None and all(c.ok for c in self.checks)

    def problems(self) -> list[str]:
        return [f"[{c.name}] {p}" for c in self.checks for p in c.problems]


def parse(data) -> tuple[Card | None, CheckResult]:
    """The `formato` check: the JSON matches the contract (no unknown keys, right types and lengths)."""
    try:
        card = Card.model_validate(data)
    except ValidationError as exc:
        problems = []
        for e in exc.errors()[:30]:
            where = ".".join(str(p) for p in e["loc"])
            problems.append(f"{where}: {e['msg']}")
        return None, CheckResult("formato", False, problems)
    return card, CheckResult("formato", True)


def _label(i: int, v: CardValue) -> str:
    extra = f", {v.nome}" if v.nome else ""
    return f"valore {i} ({v.campo}{extra})"


def check_fields(card: Card) -> CheckResult:
    problems = []
    by_field: dict[str, list[CardValue]] = {f: [] for f in FIELD_NAMES}
    for v in card.valori:
        by_field[v.campo].append(v)
    for name, spec in FIELDS.items():
        vals = by_field[name]
        real = [v for v in vals if v.origin != "not_available"]
        na = [v for v in vals if v.origin == "not_available"]
        if not vals:
            problems.append(f"manca il campo {name} (se l'articolo non lo riporta: una riga con origin 'not_available')")
        elif spec["obbligatorio"] and not real:
            problems.append(f"il campo {name} è obbligatorio: serve almeno un valore con citazione")
        if na and real:
            problems.append(f"il campo {name} ha valori e anche 'not_available': tieni solo i valori")
        if len(na) > 1:
            problems.append(f"il campo {name} ha più righe 'not_available': ne basta una")
    for i, v in enumerate(card.valori, 1):
        if v.origin == "not_available":
            extra = [k for k in ("valore", "unita", "pagina", "sezione", "citazione") if getattr(v, k) is not None]
            if extra:
                problems.append(f"{_label(i, v)}: con 'not_available' lascia vuoti {', '.join(extra)}")
            continue
        if v.valore is None:
            problems.append(f"{_label(i, v)}: manca il valore")
        if v.citazione is None:
            problems.append(f"{_label(i, v)}: manca la citazione")
        if v.campo == "misura_principale" and not v.nome:
            problems.append(f"{_label(i, v)}: manca il nome della misura")
    return CheckResult("campi_completi", not problems, problems)


def check_positions(card: Card, pages: dict[int, str], sections: dict[int, str]) -> CheckResult:
    problems = []
    for i, v in enumerate(card.valori, 1):
        if v.origin == "not_available":
            continue
        if (v.pagina is None) == (v.sezione is None):
            problems.append(f"{_label(i, v)}: indica la pagina oppure la sezione (una sola delle due)")
        elif v.pagina is not None and v.pagina not in pages:
            problems.append(f"{_label(i, v)}: la pagina {v.pagina} non esiste (pagine 1-{max(pages) if pages else 0})")
        elif v.sezione is not None and v.sezione not in sections:
            problems.append(f"{_label(i, v)}: la sezione {v.sezione} non esiste nel testo aperto di questo articolo")
    return CheckResult("posizione", not problems, problems)


def check_quotes(card: Card, pages: dict[int, str], sections: dict[int, str]) -> CheckResult:
    problems = []
    forms: dict[tuple[str, int], tuple[str, ...]] = {}
    for i, v in enumerate(card.valori, 1):
        if v.origin == "not_available" or v.citazione is None:
            continue
        q = v.citazione
        if "..." in q or "…" in q or "[...]" in q:
            problems.append(f"{_label(i, v)}: la citazione deve essere un pezzo continuo, senza puntini")
            continue
        if len(squash(q)) < QUOTE_MIN:
            problems.append(f"{_label(i, v)}: citazione troppo corta (almeno {QUOTE_MIN} caratteri senza spazi)")
            continue
        key = ("p", v.pagina) if v.pagina is not None else ("s", v.sezione)
        text = pages.get(v.pagina) if key[0] == "p" else sections.get(v.sezione)
        if text is None:
            continue                                   # reported by check_positions
        if key not in forms:
            forms[key] = text_forms(text)
        if not quote_in_text(q, forms[key]):
            where = f"pagina {v.pagina}" if key[0] == "p" else f"sezione {v.sezione}"
            problems.append(f"{_label(i, v)}: la citazione non si trova nella {where}: \"{q[:120]}\"")
    return CheckResult("citazione_nel_testo", not problems, problems)


def check_numbers(card: Card) -> CheckResult:
    problems = []
    for i, v in enumerate(card.valori, 1):
        if v.origin == "not_available" or not v.valore or not v.citazione:
            continue
        miss = missing_numbers(v.valore, v.citazione)
        if miss:
            problems.append(f"{_label(i, v)}: numeri del valore che non sono nella citazione: {', '.join(miss)} "
                            "(copiali come sono scritti, con segno e decimali)")
        if v.campo == "partecipanti_numero" and not numbers(v.valore):
            problems.append(f"{_label(i, v)}: il valore deve essere un numero")
    return CheckResult("numeri_nella_citazione", not problems, problems)


def check_units(card: Card) -> CheckResult:
    problems = []
    for i, v in enumerate(card.valori, 1):
        if v.origin == "not_available":
            continue
        kind = FIELDS[v.campo]["tipo"]
        if v.unita and v.citazione and not unit_in_quote(v.unita, v.citazione):
            problems.append(f"{_label(i, v)}: l'unità '{v.unita}' non è scritta nella citazione")
        if kind == "eta" and not v.unita:
            problems.append(f"{_label(i, v)}: per l'età serve l'unità (years o months)")
        if kind == "intero" and v.unita:
            problems.append(f"{_label(i, v)}: il numero dei partecipanti non ha unità")
    return CheckResult("unita", not problems, problems)


_THOUSANDS = re.compile(r"-?\d{1,3}(?:,\d{3})+")


def _as_float(token: str) -> float | None:
    """'1,257' (thousands) -> 1257; '0,81' (decimal comma) -> 0.81; '.01' -> 0.01."""
    t = token
    if _THOUSANDS.fullmatch(t):
        t = t.replace(",", "")
    elif "," in t and "." not in t:
        t = t.replace(",", ".")
    else:
        t = t.replace(",", "")
    try:
        return float(t)
    except ValueError:
        return None


def check_plausible(card: Card) -> CheckResult:
    problems = []
    for i, v in enumerate(card.valori, 1):
        if v.origin == "not_available" or not v.valore:
            continue
        nums = [x for x in (_as_float(t) for t in numbers(v.valore)) if x is not None]
        kind = FIELDS[v.campo]["tipo"]
        if kind == "intero" and nums:
            n = nums[0]
            if len(nums) != 1 or n != int(n) or not 1 <= n <= 1_000_000:
                problems.append(f"{_label(i, v)}: il numero dei partecipanti deve essere un solo intero tra 1 e 1000000 "
                                f"(trovato '{v.valore}'; per i gruppi usa più righe con 'gruppo')")
        if kind == "eta" and nums:
            top = 1440 if v.unita and squash(v.unita).startswith("mo") else 120
            if any(x < 0 or x > top for x in nums):
                problems.append(f"{_label(i, v)}: età fuori dall'intervallo possibile (0-{top}): '{v.valore}'")
        if v.unita and v.unita.strip() == "%" and any(x < -100 or x > 100 for x in nums) and kind != "misura":
            problems.append(f"{_label(i, v)}: percentuale fuori da -100..100: '{v.valore}'")
    return CheckResult("valori_plausibili", not problems, problems)


def check_card(card: Card, pages: dict[int, str], sections: dict[int, str]) -> list[CheckResult]:
    """Every content check of cards-v1 (all but `formato` and `pacchetto`, which need the raw JSON and the run)."""
    return [check_fields(card), check_positions(card, pages, sections), check_quotes(card, pages, sections),
            check_numbers(card), check_units(card), check_plausible(card)]


def validate(data, pages: dict[int, str], sections: dict[int, str], *, packet_check: CheckResult | None = None) -> Outcome:
    card, fmt = parse(data)
    checks = [fmt]
    if packet_check is not None:
        checks.append(packet_check)
    if card is None:
        return Outcome(checks)
    return Outcome(checks + check_card(card, pages, sections), card)
