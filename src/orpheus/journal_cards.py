"""Journal cards (T-027, ADR-0009): contract journal-cards-v1, deterministic validators, submit and hand check.

Decisions of the user (2026-10-14, manual session): fields = "proposta completa", migration 0008, hand check of all
the cards. Claude writes one card per journal, in a session with the PC linked, from the CURRENT version of the
journal's author guidelines (journal_policy_version, policy-v1) — the guideline texts are not Creative Commons: they
stay on the PC and so do the cards (rapporti/schede_riviste/da_inviare, never GitHub). ORPHEUS accepts a card only if
every check passes:
  formato             the JSON follows the contract (Pydantic, no unknown key);
  versione            journal, guideline version and its fingerprint exist, belong together and are the latest
                      version of that journal (a newer version = the guidelines changed: redo the card);
  campi_completi      every journal field at least once; at least one article type; every article-type field for
                      every type (or for "tutti i tipi"); values of unknown types refused; origin rules (as cards-v1);
  posizione           the PDF ("documento", file name; needed when the version has more PDFs, policy-v2/T-050) is one
                      of the version's PDFs and the page exists in it;
  citazione_nel_testo the quote is on that page of that PDF (spaces, line breaks, hyphenation, ligatures and case
                      ignored);
  numeri_nella_citazione  every number of the value is in the quote;
  unita               the unit, when given, is written in the quote;
  valori_plausibili   numeric fields: positive whole numbers in a possible range; keywords min <= max.
Accepted cards: one transaction (the older current card of the journal becomes 'superseded'), audit event
'journal_card_accepted' with every check; refused ones: audit event 'journal_card_rejected' and <name>.motivi.txt.
Hand check: a CSV with one row per value of every current card (column giusto S/N), then the precision.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from . import audit, cards, policy_versions
from .benchmark import excel_plain, excel_text, read_filled
from .card_store import _precision, _verdict
from .db import transaction
from .ids import new_id
from .quality import REPORTS_DIR

CARD_VERSION = "journal-cards-v1"
CARD_FORMAT = "orpheus-scheda-rivista-v1"
CARDS_DIR, TO_SEND, ACCEPTED = "schede_riviste", "da_inviare", "accettate"
ALL_TYPES = "tutti i tipi"

TYPE_FIELDS: dict[str, dict] = {
    "tipo_articolo": {"descrizione": "Un tipo di articolo accettato, come lo chiama la rivista (una riga per tipo; "
                                     "'tipo_articolo' = nome breve usato nelle altre righe)."},
    "parole_testo_max": {"numero": True, "intervallo": (100, 20000),
                         "descrizione": "Limite di parole del testo; nella nota cosa è incluso (es. abstract, riferimenti)."},
    "abstract_parole_max": {"numero": True, "intervallo": (50, 1000), "descrizione": "Limite di parole dell'abstract."},
    "abstract_struttura": {"descrizione": "Abstract strutturato o no, con i titoletti richiesti; o nessun abstract."},
    "parole_chiave": {"numero": True, "intervallo": (1, 20), "descrizione": "Numero di parole chiave (es. '3-8', 'fino a 6')."},
    "riferimenti_max": {"numero": True, "intervallo": (1, 500), "descrizione": "Numero massimo di riferimenti."},
    "figure_tabelle_max": {"numero": True, "intervallo": (0, 50), "descrizione": "Numero massimo di figure e tabelle "
                                                                               "(nella nota se separate o insieme)."},
    "altri_elementi": {"descrizione": "Altri elementi richiesti per quel tipo (highlights, key points, applicazioni "
                                      "pratiche, struttura del testo…)."},
}
JOURNAL_FIELDS: dict[str, dict] = {
    "ambito": {"descrizione": "Ambito della rivista (aims and scope), breve."},
    "stile_citazioni": {"descrizione": "Stile dei riferimenti (es. Vancouver/AMA numerato, APA)."},
    "revisione": {"descrizione": "Tipo di revisione (doppio cieco, singolo cieco…)."},
    "preprint": {"descrizione": "Se i preprint sono accettati."},
    "politica_ia": {"descrizione": "Regole sull'uso dell'IA generativa (dichiarazione, divieti)."},
    "dati": {"descrizione": "Dichiarazione di disponibilità dei dati: obbligatoria, incoraggiata…"},
    "registrazione": {"descrizione": "Registrazione obbligatoria (studi clinici, revisioni sistematiche…)."},
    "linee_guida_reporting": {"descrizione": "Linee guida di reporting (CONSORT, PRISMA, STROBE…)."},
    "etica_consenso": {"descrizione": "Approvazione etica e consenso informato."},
    "conflitti_finanziamenti": {"descrizione": "Dichiarazioni di conflitti di interesse e finanziamenti."},
    "open_access_costi": {"descrizione": "Open access, APC, costi per pagina o per i colori."},
    "orcid": {"descrizione": "ORCID richiesto o no."},
    "documenti_invio": {"descrizione": "Documenti per l'invio (lettera di presentazione, pagina del titolo separata…)."},
}
FIELDS = {**TYPE_FIELDS, **JOURNAL_FIELDS}
FIELD_NAMES = tuple(FIELDS)
CHECKS = ("formato", "versione", "campi_completi", "posizione", "citazione_nel_testo", "numeri_nella_citazione",
          "unita", "valori_plausibili")


class JournalCardValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    campo: Literal[FIELD_NAMES]  # type: ignore[valid-type]
    tipo_articolo: str | None = Field(default=None, max_length=120)
    valore: str | None = Field(default=None, max_length=cards.VALUE_MAX)
    unita: str | None = Field(default=None, max_length=40)
    pagina: int | None = Field(default=None, ge=1)
    citazione: str | None = Field(default=None, max_length=cards.QUOTE_MAX)
    origin: Literal["reported", "interpreted", "not_available"]
    nota: str | None = Field(default=None, max_length=500)
    documento: str | None = Field(default=None, max_length=255)

    @field_validator("tipo_articolo", "valore", "unita", "citazione", "nota", "documento", mode="before")
    @classmethod
    def _blanks(cls, v):
        return None if isinstance(v, str) and not v.strip() else v


class JournalCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    formato: Literal["orpheus-scheda-rivista-v1"]
    versione: Literal["journal-cards-v1"]
    sigla: str = Field(min_length=1, max_length=20)
    journal_id: str = Field(pattern=r"^jrn_[0-9A-HJKMNP-TV-Z]{26}$")
    policy_version_id: str = Field(pattern=r"^jpv_[0-9A-HJKMNP-TV-Z]{26}$")
    impronta: str = Field(pattern=r"^[0-9a-f]{64}$")
    modello: str = Field(min_length=3, max_length=80)
    valori: list[JournalCardValue] = Field(min_length=1, max_length=400)
    note_generali: str | None = Field(default=None, max_length=2000)


# ---------------------------------------------------------------- guideline text

def latest_version(conn: sqlite3.Connection, journal_id: str) -> sqlite3.Row | None:
    return policy_versions.latest_version(conn, journal_id)


def version_documents(conn: sqlite3.Connection, version_id: str) -> list[dict]:
    """The guideline PDFs of a version, in order (policy-v2): [{document_id, file_name, ord, page_key}]."""
    return policy_versions.version_documents(conn, version_id)


def version_document(conn: sqlite3.Connection, version_id: str) -> sqlite3.Row | None:
    """The first PDF of a version (the instructions page)."""
    docs = version_documents(conn, version_id)
    return conn.execute("SELECT * FROM document WHERE id = ?", (docs[0]["document_id"],)).fetchone() if docs else None


def pages_of(conn: sqlite3.Connection, document_id: str) -> dict[int, str]:
    return {r["page_no"]: r["text"] or "" for r in conn.execute(
        "SELECT page_no, text FROM document_page WHERE document_id = ? ORDER BY page_no", (document_id,))}


# ---------------------------------------------------------------- checks

def parse(data) -> tuple[JournalCard | None, cards.CheckResult]:
    try:
        return JournalCard.model_validate(data), cards.CheckResult("formato", True)
    except ValidationError as exc:
        problems = [f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors()[:30]]
        return None, cards.CheckResult("formato", False, problems)


def _label(i: int, v: JournalCardValue) -> str:
    return f"valore {i} ({v.campo}{', ' + v.tipo_articolo if v.tipo_articolo else ''})"


def check_version(conn: sqlite3.Connection, card: JournalCard) -> tuple[cards.CheckResult, sqlite3.Row | None]:
    problems = []
    v = conn.execute("SELECT * FROM journal_policy_version WHERE id = ?", (card.policy_version_id,)).fetchone()
    doc = None
    if v is None:
        problems.append(f"versione delle linee guida {card.policy_version_id} non trovata in questo database")
    else:
        if v["journal_id"] != card.journal_id:
            problems.append("la versione delle linee guida è di un'altra rivista")
        if v["content_sha256"] != card.impronta:
            problems.append("impronta diversa da quella della versione delle linee guida")
        last = latest_version(conn, v["journal_id"])
        if last is not None and last["id"] != v["id"]:
            problems.append("le linee guida sono cambiate (c'è una versione più recente): rifare la scheda su quella")
        doc = version_document(conn, v["id"])
        if doc is None:
            problems.append("il PDF di questa versione delle linee guida non è nel database")
    return cards.CheckResult("versione", not problems, problems), doc


def check_fields(card: JournalCard) -> cards.CheckResult:
    problems = []
    for i, v in enumerate(card.valori, 1):
        if v.origin == "not_available":
            if v.valore or v.pagina or v.citazione or v.unita:
                problems.append(f"{_label(i, v)}: 'not_available' non ha valore, unità, pagina né citazione")
        else:
            if not v.valore:
                problems.append(f"{_label(i, v)}: manca il valore")
            if v.pagina is None or not v.citazione:
                problems.append(f"{_label(i, v)}: servono pagina e citazione")
            elif len(v.citazione.strip()) < cards.QUOTE_MIN:
                problems.append(f"{_label(i, v)}: citazione troppo corta (almeno {cards.QUOTE_MIN} caratteri)")
        if v.campo in JOURNAL_FIELDS and v.tipo_articolo:
            problems.append(f"{_label(i, v)}: è un campo della rivista, senza tipo di articolo")
        if v.campo in TYPE_FIELDS and not v.tipo_articolo:
            problems.append(f"{_label(i, v)}: serve il tipo di articolo (o '{ALL_TYPES}')")
    types = {v.tipo_articolo for v in card.valori if v.campo == "tipo_articolo" and v.origin != "not_available"
             and v.tipo_articolo}
    if not types:
        problems.append("nessun tipo di articolo (campo tipo_articolo)")
    for i, v in enumerate(card.valori, 1):
        if v.campo in TYPE_FIELDS and v.campo != "tipo_articolo" and v.tipo_articolo \
                and v.tipo_articolo not in types | {ALL_TYPES}:
            problems.append(f"{_label(i, v)}: tipo di articolo non dichiarato con una riga tipo_articolo")
    present = {(v.campo, v.tipo_articolo) for v in card.valori}
    for f in JOURNAL_FIELDS:
        if (f, None) not in present:
            problems.append(f"manca il campo della rivista {f} (con un valore o not_available)")
    for t in sorted(types):
        for f in TYPE_FIELDS:
            if f != "tipo_articolo" and (f, t) not in present and (f, ALL_TYPES) not in present:
                problems.append(f"tipo {t}: manca il campo {f} (con un valore o not_available)")
    return cards.CheckResult("campi_completi", not problems, problems)


@dataclass
class VersionText:
    """The PDFs of a version with their pages: file name -> (document_id, {page: text}), in order."""
    docs: dict[str, tuple[str, dict[int, str]]] = field(default_factory=dict)

    @classmethod
    def load(cls, conn: sqlite3.Connection, version_id: str | None) -> "VersionText":
        vt = cls()
        for d in version_documents(conn, version_id) if version_id else []:
            vt.docs[d["file_name"]] = (d["document_id"], pages_of(conn, d["document_id"]))
        return vt

    def find(self, name: str | None) -> tuple[str | None, str | None]:
        """(file name, problem) of the PDF a value refers to: its "documento" (file name, case and the .pdf ending
        ignored); without it, the only PDF of the version."""
        if not self.docs:
            return None, None
        if not name:
            if len(self.docs) == 1:
                return next(iter(self.docs)), None
            return None, ("la versione delle linee guida ha più PDF: indica il file in 'documento' (" +
                          "; ".join(self.docs) + ")")
        want = name.strip().lower().removesuffix(".pdf")
        for f in self.docs:
            if f.lower().removesuffix(".pdf") == want:
                return f, None
        return None, f"il documento '{name}' non è tra i PDF di questa versione (" + "; ".join(self.docs) + ")"

    def pages(self, name: str | None) -> dict[int, str]:
        return self.docs[name][1] if name in self.docs else {}

    def document_id(self, name: str | None) -> str | None:
        return self.docs[name][0] if name in self.docs else None


def check_positions(card: JournalCard, vt: VersionText) -> cards.CheckResult:
    problems = []
    for i, v in enumerate(card.valori, 1):
        if v.origin == "not_available":
            if v.documento:
                problems.append(f"{_label(i, v)}: 'not_available' non ha documento")
            continue
        name, problem = vt.find(v.documento)
        if problem:
            problems.append(f"{_label(i, v)}: {problem}")
        elif name is not None and v.pagina is not None and v.pagina not in vt.pages(name):
            problems.append(f"{_label(i, v)}: la pagina {v.pagina} non esiste ({name} ha {len(vt.pages(name))} pagine)")
    return cards.CheckResult("posizione", not problems, problems)


def check_quotes(card: JournalCard, vt: VersionText) -> cards.CheckResult:
    problems = []
    cache: dict[str, dict[int, object]] = {}
    for i, v in enumerate(card.valori, 1):
        name, problem = vt.find(v.documento)
        if not v.citazione or problem or name is None:
            continue
        pages = vt.pages(name)
        if v.pagina not in pages:
            continue
        forms = cache.setdefault(name, {p: cards.text_forms(t) for p, t in pages.items()})
        if not cards.quote_in_text(v.citazione, forms[v.pagina]):
            elsewhere = [(f, p) for f in vt.docs for p, fm in cache.setdefault(
                f, {p: cards.text_forms(t) for p, t in vt.pages(f).items()}).items() if cards.quote_in_text(v.citazione, fm)]
            hint = (f" (è a pagina {elsewhere[0][1]}" + (f" di {elsewhere[0][0]}" if len(vt.docs) > 1 else "") + ")"
                    if elsewhere else "")
            problems.append(f"{_label(i, v)}: citazione non trovata a pagina {v.pagina}"
                            + (f" di {name}" if len(vt.docs) > 1 else "") + hint)
    return cards.CheckResult("citazione_nel_testo", not problems, problems)


def check_numbers(card: JournalCard) -> cards.CheckResult:
    problems = []
    for i, v in enumerate(card.valori, 1):
        if v.valore and v.citazione:
            missing = cards.missing_numbers(v.valore, v.citazione)
            if missing:
                problems.append(f"{_label(i, v)}: numeri del valore non presenti nella citazione: {', '.join(missing)}")
    return cards.CheckResult("numeri_nella_citazione", not problems, problems)


def check_units(card: JournalCard) -> cards.CheckResult:
    problems = [f"{_label(i, v)}: l'unità '{v.unita}' non è scritta nella citazione"
                for i, v in enumerate(card.valori, 1) if v.unita and v.citazione and not cards.unit_in_quote(v.unita, v.citazione)]
    return cards.CheckResult("unita", not problems, problems)


def first_number(value: str | None) -> float | None:
    for tok in cards.numbers(value or ""):
        try:
            return float(tok.replace(",", ""))
        except ValueError:
            continue
    return None


def check_plausible(card: JournalCard) -> cards.CheckResult:
    problems = []
    for i, v in enumerate(card.valori, 1):
        spec = FIELDS[v.campo]
        if not spec.get("numero") or not v.valore:
            continue
        nums = [float(t.replace(",", "")) for t in cards.numbers(v.valore) if t.replace(",", "").lstrip("-").replace(".", "", 1).isdigit()]
        lo, hi = spec["intervallo"]
        for n in nums:
            if n != int(n) or not lo <= n <= hi:
                problems.append(f"{_label(i, v)}: {n:g} non è un numero intero tra {lo} e {hi}")
        if v.campo == "parole_chiave" and len(nums) >= 2 and nums[0] > nums[1]:
            problems.append(f"{_label(i, v)}: minimo maggiore del massimo")
    return cards.CheckResult("valori_plausibili", not problems, problems)


def validate(conn: sqlite3.Connection, data) -> tuple[cards.Outcome, sqlite3.Row | None]:
    card, fmt = parse(data)
    if card is None:
        return cards.Outcome([fmt]), None
    ver, doc = check_version(conn, card)
    vt = VersionText.load(conn, card.policy_version_id) if doc is not None else VersionText()
    checks = [fmt, ver, check_fields(card), check_positions(card, vt), check_quotes(card, vt),
              check_numbers(card), check_units(card), check_plausible(card)]
    return cards.Outcome(checks, card), doc


# ---------------------------------------------------------------- submit

def to_send_dir(home: Path) -> Path:
    return Path(home) / REPORTS_DIR / CARDS_DIR / TO_SEND


def accepted_dir(home: Path) -> Path:
    return Path(home) / REPORTS_DIR / CARDS_DIR / ACCEPTED


@dataclass
class Submission:
    path: Path
    raw: bytes = b""
    sha256: str = ""
    data: object = None
    stop: str | None = None
    summary: dict = field(default_factory=dict)


def prepare(conn: sqlite3.Connection, path: Path) -> Submission:
    s = Submission(Path(path))
    try:
        s.raw = s.path.read_bytes()
    except OSError as exc:
        s.stop = f"file non leggibile: {exc}"
        return s
    s.sha256 = hashlib.sha256(s.raw).hexdigest()
    try:
        s.data = json.loads(s.raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        s.stop = f"non è un JSON valido: {exc}"
        return s
    if conn.execute("SELECT 1 FROM journal_card WHERE file_sha256 = ?", (s.sha256,)).fetchone():
        s.stop = "scheda già accettata (stesso file)"
    return s


def record(conn: sqlite3.Connection, s: Submission, *, actor: str = "orpheus schede-riviste") -> dict:
    """Check one card and write the outcome in one transaction."""
    outcome, doc = validate(conn, s.data)
    vt = VersionText.load(conn, outcome.card.policy_version_id) if outcome.accepted else VersionText()
    names = {c.name for c in outcome.checks}
    checks = outcome.checks + [cards.CheckResult(n, False, ["non eseguito: il formato non è valido"])
                               for n in CHECKS if n not in names]
    out = {"file": s.path.name, "esito": "accettata" if outcome.accepted else "rifiutata",
           "controlli": {c.name: ("ok" if c.ok else "non superato") for c in checks}}
    card = outcome.card
    if card is not None:
        out["sigla"] = card.sigla
    with transaction(conn):
        if outcome.accepted:
            conn.execute("UPDATE journal_card SET status = 'superseded' WHERE journal_id = ? AND card_version = ? "
                         "AND status = 'current'", (card.journal_id, CARD_VERSION))
            card_id = new_id("journal_card")
            conn.execute("""INSERT INTO journal_card (id, journal_id, policy_version_id, document_id, card_version,
                                                      file_sha256, model_ref, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                         (card_id, card.journal_id, card.policy_version_id, doc["id"], CARD_VERSION, s.sha256,
                          card.modello, card.note_generali))
            for i, v in enumerate(card.valori, 1):
                na = v.origin == "not_available"
                number = first_number(v.valore) if FIELDS[v.campo].get("numero") and not na else None
                value_doc = None if na else vt.document_id(vt.find(v.documento)[0])
                conn.execute("""INSERT INTO journal_card_value (card_id, ord, field, article_type, value, number, unit,
                                                                page_no, quote, origin, note, document_id)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                             (card_id, i, v.campo, v.tipo_articolo, None if na else v.valore, number,
                              None if na else v.unita, None if na else v.pagina, None if na else v.citazione,
                              v.origin, v.nota, value_doc))
            audit.record(conn, actor=actor, action="journal_card_accepted", entity_type="journal_card",
                         entity_id=card_id,
                         payload={"sigla": card.sigla, "journal_id": card.journal_id,
                                  "policy_version_id": card.policy_version_id, "values": len(card.valori),
                                  "file_sha256": s.sha256, "card_version": CARD_VERSION, "model": card.modello,
                                  "checks": [c.as_dict() for c in checks]}, in_transaction=True)
            out["id_scheda"], out["valori"] = card_id, len(card.valori)
        else:
            problems = [f"[{c.name}] {p}" for c in checks if not c.ok for p in c.problems]
            audit.record(conn, actor=actor, action="journal_card_rejected", entity_type="journal",
                         entity_id=card.journal_id if card else None,
                         payload={"file": s.path.name, "file_sha256": s.sha256, "card_version": CARD_VERSION,
                                  "failed": [c.name for c in checks if not c.ok], "problems": problems[:50]},
                         in_transaction=True)
            out["motivi"], out["motivi_totali"] = problems[:40], len(problems)
    return out


def file_after(s: Submission, summary: dict, home: Path) -> str | None:
    """Accepted -> moved to schede_riviste/accettate (never overwriting); refused -> <name>.motivi.txt beside it."""
    motivi = s.path.with_name(s.path.stem + ".motivi.txt")
    if summary.get("esito") == "accettata" or (s.stop or "").startswith("scheda già accettata"):
        dest_dir = accepted_dir(home)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / s.path.name
        if dest.exists() and dest.read_bytes() != s.raw:
            dest = dest_dir / f"{s.path.stem}_{s.sha256[:8]}{s.path.suffix}"
        if not dest.exists():
            s.path.replace(dest)
        elif s.path.exists():
            s.path.unlink()
        if motivi.exists():
            motivi.unlink()
        return str(dest)
    lines = [f"ORPHEUS - scheda della rivista rifiutata - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
             f"file: {s.path.name}", ""] + ([f"- {s.stop}"] if s.stop else []) + \
            [f"- {m}" for m in summary.get("motivi", [])] + ["", "Correggi la scheda e rilancia schede_riviste.cmd."]
    motivi.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    return str(motivi)


def submit_folder(conn: sqlite3.Connection, home: Path) -> list[dict]:
    folder = to_send_dir(home)
    out = []
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        s = prepare(conn, path)
        summary = {"file": path.name, "esito": "non inviata", "motivo": s.stop} if s.stop else record(conn, s)
        summary["dopo"] = file_after(s, summary, home)
        out.append(summary)
    return out


def status(conn: sqlite3.Connection) -> dict:
    q = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    stale = conn.execute("""SELECT count(*) FROM journal_card c WHERE c.status = 'current' AND c.policy_version_id <>
                              (SELECT v.id FROM journal_policy_version v WHERE v.journal_id = c.journal_id
                               AND v.policy_type = 'author_guidelines' ORDER BY v.id DESC LIMIT 1)""").fetchone()[0]
    return {"schede": q("SELECT count(*) FROM journal_card WHERE status = 'current'"),
            "valori": q("SELECT count(*) FROM journal_card_value v JOIN journal_card c ON c.id = v.card_id "
                        "WHERE c.status = 'current'"),
            "riviste": q("SELECT count(*) FROM journal"), "schede_da_rifare": stale}


# ---------------------------------------------------------------- hand check (all current cards)

CONTROL_PREFIX = "schede_riviste_controllo"
CONTROL_COLUMNS = ["n", "id_scheda", "ord", "rivista", "file_linee_guida", "pagina", "campo", "tipo_articolo", "valore",
                   "unita", "origin", "citazione", "nota", "cosa_controllare", "giusto", "note"]
_CONTROL_ORIGINAL = re.compile(rf"^{CONTROL_PREFIX}_(\d+)_\d{{4}}-\d{{2}}-\d{{2}}(_\d{{6}})?$")


def control_rows(conn: sqlite3.Connection, *, only_unchecked: bool = True) -> tuple[list[dict], int]:
    """Rows of the hand check. By default only the values not yet checked by hand (2026-10-20: after the first check
    of 5 cards the new cards never got a file of their own). The count is the number of cards with rows."""
    rows: list[dict] = []
    with_rows: set[str] = set()
    current = conn.execute("""SELECT c.*, j.title FROM journal_card c JOIN journal j ON j.id = c.journal_id
                              WHERE c.status = 'current' ORDER BY j.title""").fetchall()
    for c in current:
        doc = conn.execute("SELECT original_path FROM document WHERE id = ?", (c["document_id"],)).fetchone()
        name = (doc["original_path"] or "").replace("\\", "/").rsplit("/", 1)[-1] if doc else ""
        names: dict[str, str] = {}
        for v in conn.execute("SELECT * FROM journal_card_value WHERE card_id = ? ORDER BY ord", (c["id"],)):
            if only_unchecked and v["verified_by_human"]:
                continue
            with_rows.add(c["id"])
            vdoc = v["document_id"] if "document_id" in v.keys() else None
            if vdoc and vdoc not in names:
                d = conn.execute("SELECT original_path FROM document WHERE id = ?", (vdoc,)).fetchone()
                names[vdoc] = (d["original_path"] or "").replace("\\", "/").rsplit("/", 1)[-1] if d else ""
            file_name = names.get(vdoc, name) if vdoc else name
            todo = ("le linee guida davvero non lo dicono" if v["origin"] == "not_available"
                    else "il valore è quello delle linee guida per questo campo e tipo di articolo")
            rows.append({"id_scheda": c["id"], "ord": v["ord"], "rivista": c["title"], "file_linee_guida": file_name,
                         "pagina": v["page_no"] or "", "campo": v["field"], "tipo_articolo": v["article_type"] or "",
                         "valore": excel_text(v["value"]), "unita": v["unit"] or "", "origin": v["origin"],
                         "citazione": v["quote"] or "", "nota": v["note"] or "", "cosa_controllare": todo,
                         "giusto": "", "note": ""})
    for n, r in enumerate(rows, 1):
        r["n"] = n
    return rows, len(with_rows)


def write_control_csv(rows: list[dict], n_cards: int, home: Path, now: datetime | None = None) -> tuple[Path, bool]:
    now = now or datetime.now()
    buf = io.StringIO(newline="")
    w = csv.DictWriter(buf, fieldnames=CONTROL_COLUMNS, delimiter=";", lineterminator="\r\n")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    data = buf.getvalue().encode("utf-8-sig")
    folder = Path(home) / REPORTS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob(f"{CONTROL_PREFIX}_{n_cards}_*.csv"):
        if _CONTROL_ORIGINAL.match(old.stem) and old.read_bytes() == data:
            return old, False
    path = folder / f"{CONTROL_PREFIX}_{n_cards}_{now.strftime('%Y-%m-%d')}.csv"
    if path.exists():
        path = path.with_name(f"{path.stem}_{now.strftime('%H%M%S')}.csv")
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    return path, True


def latest_filled_control(home: Path, conn: sqlite3.Connection | None = None) -> Path | None:
    """The newest filled copy. With the database: only a copy that still has S/N not written into journal_card_value
    (a copy already recorded must not hide the file of the cards added later — bug of 2026-10-20)."""
    folder = Path(home) / REPORTS_DIR
    found = [p for p in folder.glob(f"{CONTROL_PREFIX}_*.csv") if not _CONTROL_ORIGINAL.match(p.stem)] \
        if folder.is_dir() else []
    if conn is not None:
        found = [p for p in found if _has_unrecorded(conn, p)]
    return max(found, key=lambda p: (p.stat().st_mtime, p.name)) if found else None


def _has_unrecorded(conn: sqlite3.Connection, path: Path) -> bool:
    try:
        rows, _ = read_filled(path, need={"id_scheda", "ord", "giusto"})
    except Exception:  # noqa: BLE001 - an unreadable copy is shown to the user by score_control
        return True
    for r in rows:
        if _verdict(r.get("giusto")) not in ("S", "N"):
            continue
        try:
            ord_ = int(str(r.get("ord")).strip())
        except ValueError:
            continue
        hit = conn.execute("""SELECT v.verified_by_human FROM journal_card_value v JOIN journal_card c ON c.id = v.card_id
                              WHERE v.card_id = ? AND v.ord = ? AND c.status = 'current'""",
                           ((r.get("id_scheda") or "").strip(), ord_)).fetchone()
        if hit is not None and not hit[0]:
            return True
    return False


def score_control(path: Path) -> dict:
    rows, info = read_filled(path, need={"id_scheda", "ord", "giusto"})
    verdicts = [_verdict(r.get("giusto")) for r in rows]
    by = lambda key: {k: _precision([v for v, r in zip(verdicts, rows) if r.get(key) == k])  # noqa: E731
                      for k in dict.fromkeys(r.get(key, "") for r in rows)}
    return {"file": str(path), **info, "righe": len(rows), "precisione_complessiva": _precision(verdicts),
            "per_rivista": by("rivista"), "per_campo": by("campo"), "per_origin": by("origin"),
            "sbagliati": [{"n": r.get("n"), "rivista": r.get("rivista"), "campo": r.get("campo"),
                           "tipo_articolo": r.get("tipo_articolo"), "valore": excel_plain(r.get("valore")),
                           "note": (r.get("note") or "").strip()} for v, r in zip(verdicts, rows) if v == "N"],
            "non_validi": [{"n": r.get("n"), "giusto": r.get("giusto")} for v, r in zip(verdicts, rows) if v == "?"]}


def record_verdicts(conn: sqlite3.Connection, path: Path, *, actor: str = "orpheus schede-riviste") -> int:
    """Write the S/N of the filled control file into journal_card_value (verified_by_human, human_verdict)."""
    rows, _ = read_filled(path, need={"id_scheda", "ord", "giusto"})
    n = 0
    with transaction(conn):
        for r in rows:
            v = _verdict(r.get("giusto"))
            if v not in ("S", "N"):
                continue
            try:
                ord_ = int(str(r.get("ord")).strip())
            except ValueError:
                continue
            n += conn.execute("""UPDATE journal_card_value SET verified_by_human = 1, human_verdict = ?
                                 WHERE card_id = ? AND ord = ?""",
                              ("right" if v == "S" else "wrong", (r.get("id_scheda") or "").strip(), ord_)).rowcount
        if n:
            audit.record(conn, actor=actor, action="journal_cards_hand_check",
                         payload={"file": Path(path).name, "values": n}, in_transaction=True)
    return n


def instructions() -> dict:
    """The contract as Claude reads it before writing a card."""
    return {"formato": CARD_FORMAT, "versione": CARD_VERSION, "tutti_i_tipi": ALL_TYPES,
            "campi_per_tipo_di_articolo": {k: v["descrizione"] for k, v in TYPE_FIELDS.items()},
            "campi_della_rivista": {k: v["descrizione"] for k, v in JOURNAL_FIELDS.items()},
            "regole": ["ogni valore 'reported'/'interpreted' ha pagina e citazione copiata (12-500 caratteri)",
                       "'not_available' = le linee guida non lo dicono: niente valore, pagina, citazione",
                       "ogni numero del valore è nella citazione; l'unità (es. words) è scritta nella citazione"]}
