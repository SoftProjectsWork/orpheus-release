"""Contract `cards-v2` (EURYDIKE_CONTRATTI.md §7, approved by the user on 2026-10-20): the unified article card written
by Claude, its validators and its entry into `card_item` / `card_outcome_row` as proposals (`state='ai'`).

Decisions of the user (2026-10-20): the fields of cards-v1 plus the fields of Tabula's blocks; the new fields are asked
only for the articles included in a review (the packet lists the wanted fields: `campi_del_pacchetto`). Claude writes
proposals only: a value the user confirmed, corrected or marked not reported is never rewritten (`gia_deciso`), and
"not reported" is a decision of the user (a proposed `not_available` enters as `ai` with a note).

The checks of cards-v1 are reused as they are on the cards-v1 fields (same normalisation, numbers, units, plausible
values). A quote that is not found exactly does not refuse the card: the value enters with `quote_check` fuzzy or
not_found and can be confirmed only one by one (never in bulk). Outcome rows follow `eurydike-ma-v1` (§4): every
statistic as written in the quote, a sufficient set with n ≥ 2, coherent values, group and time, unit in the quote.
"""
from __future__ import annotations

import sqlite3
from types import SimpleNamespace
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .. import audit, cards
from ..cards import CheckResult, Outcome
from ..ids import new_id
from . import store

FORMAT, VERSION = "orpheus-scheda-v2", "cards-v2"
V1_FIELDS = cards.FIELD_NAMES

# new fields (Tabula's six blocks); all optional, asked only for the articles of a review
NEW_FIELDS: dict[str, dict] = {
    "paese": {"tipo": "testo", "descrizione": "Country where the study was done."},
    "contesto": {"tipo": "testo", "descrizione": "Setting: laboratory, field, clinic, school, club …"},
    "finanziamento": {"tipo": "testo", "descrizione": "Funding as declared (or 'no funding')."},
    "conflitti_di_interesse": {"tipo": "testo", "descrizione": "Conflicts of interest as declared."},
    "registrazione": {"tipo": "testo", "descrizione": "Trial or protocol registration (register and number)."},
    "criteri_inclusione": {"tipo": "testo", "descrizione": "Inclusion criteria (one row each, `ordine`)."},
    "criteri_esclusione": {"tipo": "testo", "descrizione": "Exclusion criteria (one row each, `ordine`)."},
    "altezza": {"tipo": "misura", "descrizione": "Height as reported, mean (SD) or range, with the unit."},
    "massa": {"tipo": "misura", "descrizione": "Body mass as reported, with the unit."},
    "bmi": {"tipo": "misura", "descrizione": "BMI as reported, with the unit if written."},
    "durata": {"tipo": "testo", "descrizione": "Duration of the intervention or exposure."},
    "frequenza": {"tipo": "testo", "descrizione": "Frequency of the sessions (e.g. 3 per week)."},
    "intensita": {"tipo": "testo", "descrizione": "Intensity of the intervention."},
    "tempi_di_misura": {"tipo": "testo", "descrizione": "Time points of the measurements (baseline, post, follow-up)."},
    "strumento": {"tipo": "testo", "descrizione": "Instrument: model and manufacturer (one row each)."},
    "frequenza_campionamento": {"tipo": "misura", "descrizione": "Sampling frequency, with the unit (Hz)."},
    "filtro": {"tipo": "testo", "descrizione": "Filter: type, order and cut-off frequency."},
    "protocollo_misura": {"tipo": "testo", "descrizione": "Measurement protocol: task, trials, surface, footwear."},
    "variabili_derivate": {"tipo": "testo", "descrizione": "Variables derived from the raw data and how."},
    "analisi_statistica": {"tipo": "testo", "descrizione": "Statistical analysis: tests, models, significance level."},
}
FIELD_NAMES = V1_FIELDS + tuple(NEW_FIELDS)
STATS = store.STATS
NA_NOTE = "proposed by Claude as not reported: decide (Non riportato) or correct"


def _blank(v):
    return None if isinstance(v, str) and not v.strip() else v


class Value(BaseModel):
    model_config = ConfigDict(extra="forbid")
    campo: Literal[FIELD_NAMES]  # type: ignore[valid-type]
    ordine: int = Field(default=1, ge=1, le=50)
    nome: str | None = Field(default=None, max_length=200)
    valore: str | None = Field(default=None, max_length=cards.VALUE_MAX)
    unita: str | None = Field(default=None, max_length=40)
    gruppo: str | None = Field(default=None, max_length=200)
    pagina: int | None = Field(default=None, ge=1)
    sezione: int | None = Field(default=None, ge=0)
    citazione: str | None = Field(default=None, max_length=cards.QUOTE_MAX)
    origin: Literal["reported", "interpreted", "not_available"]
    confidenza: Literal["high", "medium", "low"] | None = None
    nota: str | None = Field(default=None, max_length=500)

    @field_validator("nome", "valore", "unita", "gruppo", "citazione", "nota", mode="before")
    @classmethod
    def _blanks(cls, v):
        return _blank(v)


class Row(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: str = Field(min_length=1, max_length=200)
    group: str = Field(min_length=1, max_length=200)
    timepoint: str = Field(min_length=1, max_length=100)
    unit: str | None = Field(default=None, max_length=40)
    n: str | None = None
    mean: str | None = None
    sd: str | None = None
    median: str | None = None
    q1: str | None = None
    q3: str | None = None
    min: str | None = None
    max: str | None = None
    se: str | None = None
    ci_low: str | None = None
    ci_high: str | None = None
    quote: str = Field(min_length=1, max_length=cards.QUOTE_MAX)
    page: int = Field(ge=1)
    table: str | None = Field(default=None, max_length=100)

    @field_validator("unit", *STATS, "table", mode="before")
    @classmethod
    def _blanks(cls, v):
        return _blank(v)


class Card(BaseModel):
    model_config = ConfigDict(extra="forbid")
    formato: Literal["orpheus-scheda-v2"]
    versione: Literal["cards-v2"]
    workflow_run_id: str = Field(pattern=r"^run_[0-9A-HJKMNP-TV-Z]{26}$")
    article_id: str = Field(pattern=r"^art_[0-9A-HJKMNP-TV-Z]{26}$")
    modello: str = Field(min_length=3, max_length=80)
    valori: list[Value] = Field(default_factory=list, max_length=300)
    righe_esito: list[Row] = Field(default_factory=list, max_length=300)
    note_generali: str | None = Field(default=None, max_length=2000)


# ------------------------------------------------------------------ checks
def _label(i: int, v: Value) -> str:
    return f"valore {i} ({v.campo}{f' {v.ordine}' if v.ordine > 1 else ''}{f', {v.nome}' if v.nome else ''})"


def check_packet_fields(card: Card, wanted: tuple[str, ...], decided: set[tuple[str, int]]) -> list[CheckResult]:
    """`campi_del_pacchetto` (only the fields of the packet, each present), `gia_deciso` (never a value the user
    decided), and the completeness rules of cards-v1 extended to every wanted field."""
    probs, done = [], []
    present: dict[str, list[Value]] = {}
    seen: set[tuple[str, int]] = set()
    for i, v in enumerate(card.valori, 1):
        if v.campo not in wanted:
            probs.append(f"{_label(i, v)}: campo non richiesto da questo pacchetto")
            continue
        if (v.campo, v.ordine) in seen:
            probs.append(f"{_label(i, v)}: campo e ordine ripetuti")
        seen.add((v.campo, v.ordine))
        if (v.campo, v.ordine) in decided:
            done.append(f"{_label(i, v)}: già deciso dall'utente (confermato, corretto o non riportato): non si riscrive")
        present.setdefault(v.campo, []).append(v)
    for name in wanted:
        vals = present.get(name, [])
        real = [v for v in vals if v.origin != "not_available"]
        if not vals and not any(k[0] == name for k in decided):
            probs.append(f"manca il campo {name} (se l'articolo non lo riporta: una riga con origin 'not_available')")
        elif name in V1_FIELDS and cards.FIELDS[name]["obbligatorio"] and vals and not real:
            probs.append(f"il campo {name} è obbligatorio: serve almeno un valore con citazione")
        if real and len(real) != len(vals):
            probs.append(f"il campo {name} ha valori e anche 'not_available': tieni solo i valori")
    for i, v in enumerate(card.valori, 1):
        if v.origin == "not_available":
            extra = [k for k in ("valore", "unita", "pagina", "sezione", "citazione") if getattr(v, k) is not None]
            if extra:
                probs.append(f"{_label(i, v)}: con 'not_available' lascia vuoti {', '.join(extra)}")
            continue
        if v.valore is None:
            probs.append(f"{_label(i, v)}: manca il valore")
        if v.citazione is None:
            probs.append(f"{_label(i, v)}: manca la citazione")
        if v.campo == "misura_principale" and not v.nome:
            probs.append(f"{_label(i, v)}: manca il nome della misura")
        if v.campo in NEW_FIELDS and NEW_FIELDS[v.campo]["tipo"] == "misura" and v.unita and v.citazione \
                and not cards.unit_in_quote(v.unita, v.citazione):
            probs.append(f"{_label(i, v)}: l'unità '{v.unita}' non è scritta nella citazione")
    return [CheckResult("campi_del_pacchetto", not probs, probs), CheckResult("gia_deciso", not done, done)]


def _f(x: str | None) -> float | None:
    return None if x is None else cards._as_float(cards.numbers(x)[0]) if cards.numbers(x) else None


def check_rows(card: Card, pages: dict[int, str]) -> list[CheckResult]:
    written, enough, coherent, gt, units, pos = [], [], [], [], [], []
    keys: set[tuple[str, str, str]] = set()
    for i, r in enumerate(card.righe_esito, 1):
        lab = f"riga {i} ({r.outcome}, {r.group}, {r.timepoint})"
        if r.page not in pages:
            pos.append(f"{lab}: la pagina {r.page} non esiste")
        elif not cards.quote_in_text(r.quote, cards.text_forms(pages[r.page])):
            pos.append(f"{lab}: la frase o riga di tabella non si trova a pagina {r.page}")
        for k in STATS:
            v = getattr(r, k)
            if v is not None:
                if not cards.numbers(v):
                    written.append(f"{lab}: {k} '{v}' non è un numero")
                elif cards.missing_numbers(v, r.quote):
                    written.append(f"{lab}: {k} '{v}' non è scritto nella frase (niente valori calcolati)")
        s = {k: _f(getattr(r, k)) for k in STATS}
        sets = [("mean", "sd"), ("median", "q1", "q3"), ("median", "min", "max"), ("se",), ("ci_low", "ci_high")]
        if s["n"] is None or s["n"] != int(s["n"]) or s["n"] < 2:
            enough.append(f"{lab}: serve n intero ≥ 2")
        elif not any(all(s[k] is not None for k in grp) for grp in sets):
            enough.append(f"{lab}: servono media e DS, oppure mediana con Q1-Q3 o min-max, oppure ES, oppure IC")
        if s["q1"] is not None and s["q3"] is not None and s["median"] is not None and not s["q1"] <= s["median"] <= s["q3"]:
            coherent.append(f"{lab}: Q1 ≤ mediana ≤ Q3 non vale")
        if s["min"] is not None and s["max"] is not None and s["min"] > s["max"]:
            coherent.append(f"{lab}: min > max")
        for k in ("sd", "se"):
            if s[k] is not None and s[k] <= 0:
                coherent.append(f"{lab}: {k} deve essere > 0")
        if s["ci_low"] is not None and s["ci_high"] is not None and s["ci_low"] >= s["ci_high"]:
            coherent.append(f"{lab}: IC basso ≥ IC alto")
        if s["mean"] is not None and s["min"] is not None and s["max"] is not None and not s["min"] <= s["mean"] <= s["max"]:
            coherent.append(f"{lab}: la media non è fra min e max")
        key = (r.outcome.strip().lower(), r.group.strip().lower(), r.timepoint.strip().lower())
        if key in keys:
            gt.append(f"{lab}: esito, gruppo e tempo ripetuti")
        keys.add(key)
        if r.unit and not (cards.unit_in_quote(r.unit, r.quote) or (r.table and cards.unit_in_quote(r.unit, r.table))):
            units.append(f"{lab}: l'unità '{r.unit}' non è nella frase o nell'intestazione della tabella")
    return [CheckResult("righe_posizione", not pos, pos), CheckResult("come_scritto", not written, written),
            CheckResult("set_sufficiente", not enough, enough), CheckResult("coerenza", not coherent, coherent),
            CheckResult("gruppo_e_tempo", not gt, gt), CheckResult("unita_righe", not units, units)]


def decided_keys(conn: sqlite3.Connection, article_id: str) -> set[tuple[str, int]]:
    return {(r["field_key"], r["item_ord"]) for r in store.current_items(conn, article_id) if r["state"] != "ai"}


def validate(data, pages: dict[int, str], sections: dict[int, str], *, wanted: tuple[str, ...] = V1_FIELDS,
             decided: set[tuple[str, int]] = frozenset(), packet_check: CheckResult | None = None) -> Outcome:
    try:
        card = Card.model_validate(data)
    except ValidationError as exc:
        return Outcome([CheckResult("formato", False, [f"{'.'.join(map(str, e['loc']))}: {e['msg']}"
                                                       for e in exc.errors()[:30]])])
    checks = [CheckResult("formato", True)] + ([packet_check] if packet_check else [])
    checks += check_packet_fields(card, wanted, set(decided))
    v1 = SimpleNamespace(valori=[v for v in card.valori if v.campo in V1_FIELDS])
    allv = SimpleNamespace(valori=card.valori)
    checks += [cards.check_positions(allv, pages, sections), cards.check_numbers(allv),
               cards.check_units(v1), cards.check_plausible(v1)]
    checks += check_rows(card, pages)
    return Outcome(checks, card)


# ------------------------------------------------------------------ entry into the database
def record(conn: sqlite3.Connection, card: Card, *, actor: str = "claude_session") -> dict:
    """Write an accepted card as proposals (`ai`). A current proposal of the same field and order is superseded; a value
    the user decided is never touched (also checked here, not only by the validator)."""
    if actor not in ("claude_session", "chat_manuale"):
        raise store.StoreError("una scheda cards-v2 la scrive Claude o la chat")
    aid = card.article_id
    doc = store._document_of(conn, aid)
    decided = decided_keys(conn, aid)
    n_items = n_rows = 0
    with store._write(conn):
        for v in card.valori:
            if (v.campo, v.ordine) in decided:
                raise store.StoreError(f"{v.campo} {v.ordine}: già deciso dall'utente")
            old = store._current(conn, aid, v.campo, v.ordine)
            na = v.origin == "not_available"
            qc, page = store.check_quote(conn, doc, v.citazione, v.pagina) if not na else ("none", None)
            note = NA_NOTE if na else (v.nota or ("interpreted" if v.origin == "interpreted" else None))
            store._insert_item(conn, {
                "article_id": aid, "field_key": v.campo, "item_ord": v.ordine, "name": v.nome,
                "value": "not reported" if na else v.valore, "unit": v.unita, "group_label": v.gruppo,
                "document_id": doc if not na else None, "quote": v.citazione, "page_no": page if v.citazione else None,
                "section_ord": v.sezione, "quote_check": qc, "confidence": v.confidenza, "note": note,
                "state": "ai", "actor": actor, "workflow_run_id": card.workflow_run_id}, old["id"] if old else None)
            n_items += 1
        for r in card.righe_esito:
            old = conn.execute("SELECT * FROM card_outcome_row WHERE article_id = ? AND outcome = ? AND group_label = ? "
                               "AND timepoint = ? AND superseded_by IS NULL", (aid, r.outcome, r.group, r.timepoint)).fetchone()
            if old is not None and old["state"] != "ai":
                continue                                       # decided by the user: kept as it is
            qc, page = store.check_quote(conn, doc, r.quote, r.page)
            rid = new_id("card_outcome_row")
            if old is not None:
                conn.execute("UPDATE card_outcome_row SET superseded_by = ? WHERE id = ?", (rid, old["id"]))
            row = {"id": rid, "article_id": aid, "outcome": r.outcome, "group_label": r.group, "timepoint": r.timepoint,
                   "unit": r.unit, **{k: getattr(r, k) for k in STATS}, "document_id": doc, "quote": r.quote,
                   "page_no": page, "table_label": r.table, "quote_check": qc, "state": "ai", "actor": actor,
                   "workflow_run_id": card.workflow_run_id}
            conn.execute(f"INSERT INTO card_outcome_row ({', '.join(store._ROW_COLS)}) "
                         f"VALUES ({', '.join('?' * len(store._ROW_COLS))})", tuple(row.get(c) for c in store._ROW_COLS))
            n_rows += 1
        audit.record(conn, actor=actor, action="card_v2_recorded", entity_type="article", entity_id=aid,
                     payload={"workflow_run_id": card.workflow_run_id, "values": n_items, "outcome_rows": n_rows},
                     in_transaction=True)
    return {"valori": n_items, "righe_esito": n_rows}
