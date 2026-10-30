"""T-033 — Fase 3: claims and evidence from the articles (contract claims-v1) with a simple level of evidence
(rules appraisal-v1).

User's decisions of 2026-10-15 14:05: claims "Dalle 30 schede" (the articles with an accepted card), appraisal
"Livello semplice", migration 0010, hand check of "Tutte, con CSV".

Claude writes, in a session, one file per article and language (rapporti/affermazioni/da_inviare/<name>.json, never
GitHub: the quotes come from texts without a CC licence): 1-6 claims in Italian ("lingua": "it", the default) or in English
("lingua": "en", 0.23.0, user's request of 2026-10-15) on the article's own results, conclusions or limits, each with 1-3
pieces of evidence = page of the article's PDF + a verbatim quote of that page. ORPHEUS writes a file only if every check
passes:
  formato              the JSON matches the contract (no unknown keys, lengths);
  lingua               the statements look written in the file's language (common short words of Italian / English);
  articolo             the article exists, has a complete-article PDF and a current card (needed for the level);
  posizione            every page exists in the PDF;
  citazione_nel_testo  every quote is on its page (same comparison as the cards: spaces, line breaks, hyphenation,
                       ligatures and case do not count);
  numeri_nella_citazione  every number of the claim is in its quotes (in Italian claims the decimal comma "6,8" = "6.8");
Level of evidence of the study (appraisal-v1), from the article's card — deterministic, written with its reasons:
  1. design (disegno_studio): systematic review / meta-analysis → alto; randomised controlled trial (not crossover) →
     alto; randomised crossover, prospective cohort or longitudinal, case-control, non-randomised controlled → medio;
     anything else (cross-sectional, observational, laboratory, pre-post, case series, retrospective…) → basso;
  2. fewer than 20 participants (largest number of partecipanti_numero) → one level lower;
  3. no comparison group (card field "confronto" not available) → one level lower (basso stays basso).
A new file for the same article and language supersedes the current claims of that language (never deleted): the Italian
and the English claims of an article live side by side. Each evidence is linked to the segment (Fase 3) that contains its
quote.
"""

from __future__ import annotations

import hashlib
import io
import csv
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from . import audit, card_store, cards
from .benchmark import excel_plain, excel_text, read_filled
from .db import transaction
from .ids import new_id
from .segments import RULES_VERSION as SEGMENT_RULES

CLAIM_FORMAT, CLAIM_VERSION, APPRAISAL_RULES = "orpheus-affermazioni-v1", "claims-v1", "appraisal-v1"
REPORTS_DIR, CLAIMS_DIR, TO_SEND, ACCEPTED = "rapporti", "affermazioni", "da_inviare", "accettate"
CHECKS = ("formato", "lingua", "articolo", "posizione", "citazione_nel_testo", "numeri_nella_citazione")
LEVELS = ("basso", "medio", "alto")
LANGUAGES = {"it": "italiano", "en": "inglese"}
# Short words that are common in one language and not words of the other (no "a", "in", "per", "e", "al", "non", "si":
# English text has "per cent", "e.g.", "et al.", "non-randomised", "SI units").
LANGUAGE_WORDS = {
    "it": frozenset("il lo gli la le di del dello della dei degli delle con nel nello nella nei negli nelle tra fra che è sono più meno rispetto dopo un uno una dal dalla dai alla ai sul sulla era erano anche quando questo questa".split()),
    "en": frozenset("the and of with was were is are than to for that this these those between after before higher lower greater more less when which their from by during not while".split()),
}
MIN_PARTICIPANTS = 20


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pagina: int = Field(ge=1)
    citazione: str = Field(min_length=12, max_length=cards.QUOTE_MAX)
    sostegno: Literal["supports", "contradicts", "mentions", "unclear"] = "supports"
    origine: Literal["reported", "interpreted"] = "reported"


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    testo: str = Field(min_length=20, max_length=400)
    tipo: Literal["risultato", "conclusione", "limite"]
    evidenze: list[Evidence] = Field(min_length=1, max_length=3)

    @field_validator("testo", mode="before")
    @classmethod
    def _strip(cls, v):
        return v.strip() if isinstance(v, str) else v


class ClaimFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    formato: Literal["orpheus-affermazioni-v1"]
    versione: Literal["claims-v1"]
    article_id: str = Field(pattern=r"^art_[0-9A-HJKMNP-TV-Z]{26}$")
    doi: str | None = Field(default=None, max_length=200)
    modello: str = Field(min_length=3, max_length=80)
    lingua: Literal["it", "en"] = "it"
    affermazioni: list[Claim] = Field(min_length=1, max_length=6)
    note: str | None = Field(default=None, max_length=1000)


# ---------------------------------------------------------------- checks

def parse(data) -> tuple[ClaimFile | None, cards.CheckResult]:
    try:
        return ClaimFile.model_validate(data), cards.CheckResult("formato", True)
    except ValidationError as exc:
        problems = [f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors()][:30]
        return None, cards.CheckResult("formato", False, problems)


def complete_document(conn: sqlite3.Connection, article_id: str) -> sqlite3.Row | None:
    return card_store._complete_documents(conn).get(article_id)


def current_card(conn: sqlite3.Connection, article_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM article_card WHERE article_id = ? AND status = 'current' "
                        "ORDER BY created_at DESC LIMIT 1", (article_id,)).fetchone()


def pages_of(conn: sqlite3.Connection, document_id: str) -> dict[int, str]:
    return {r["page_no"]: r["text"] or "" for r in conn.execute(
        "SELECT page_no, text FROM document_page WHERE document_id = ? ORDER BY page_no", (document_id,))}


def italian_decimals(text: str) -> str:
    """'6,8%' -> '6.8%' (one or two digits after the comma; '1,200' stays a thousands separator)."""
    return re.sub(r"(?<=\d),(?=\d{1,2}(?!\d))", ".", text or "")


def language_of(text: str) -> tuple[str | None, dict[str, int]]:
    """The language whose common short words are more frequent in the text (None when even or fewer than 3)."""
    words = re.findall(r"[a-zà-ÿ]+", (text or "").lower())
    count = {lang: sum(w in markers for w in words) for lang, markers in LANGUAGE_WORDS.items()}
    best = max(count, key=count.get)
    if count[best] < 3 or list(count.values()).count(count[best]) > 1:
        return None, count
    return best, count


def check_language(f: ClaimFile) -> cards.CheckResult:
    problems = []
    for i, c in enumerate(f.affermazioni, 1):
        found, _ = language_of(c.testo)
        if found and found != f.lingua:
            problems.append(f"affermazione {i}: il testo sembra in {LANGUAGES[found]}, ma il file dice \"lingua\": "
                            f"\"{f.lingua}\" ({LANGUAGES[f.lingua]}); un file per lingua")
    return cards.CheckResult("lingua", not problems, problems)


def check_article(conn, f: ClaimFile) -> tuple[cards.CheckResult, sqlite3.Row | None]:
    problems = []
    art = conn.execute("SELECT id, doi FROM article WHERE id = ?", (f.article_id,)).fetchone()
    doc = None
    if art is None:
        problems.append(f"articolo {f.article_id} non nel database")
    else:
        if f.doi and art["doi"] and f.doi.strip().lower() != art["doi"]:
            problems.append(f"DOI del file {f.doi} diverso da quello dell'articolo {art['doi']}")
        doc = complete_document(conn, f.article_id)
        if doc is None:
            problems.append("l'articolo non ha un PDF di articolo completo")
        if current_card(conn, f.article_id) is None:
            problems.append("l'articolo non ha una scheda accettata: il livello della prova non si può calcolare")
    return cards.CheckResult("articolo", not problems, problems), doc


def _label(i: int, j: int) -> str:
    return f"affermazione {i}, evidenza {j}"


def check_positions(f: ClaimFile, pages: dict[int, str]) -> cards.CheckResult:
    problems = [f"{_label(i, j)}: pagina {e.pagina} non nel PDF ({len(pages)} pagine)"
                for i, c in enumerate(f.affermazioni, 1) for j, e in enumerate(c.evidenze, 1) if e.pagina not in pages]
    return cards.CheckResult("posizione", not problems, problems)


def check_quotes(f: ClaimFile, pages: dict[int, str]) -> cards.CheckResult:
    forms = {p: cards.text_forms(t) for p, t in pages.items()}
    problems = []
    for i, c in enumerate(f.affermazioni, 1):
        for j, e in enumerate(c.evidenze, 1):
            if e.pagina in forms and not cards.quote_in_text(e.citazione, forms[e.pagina]):
                elsewhere = [p for p, fm in forms.items() if cards.quote_in_text(e.citazione, fm)]
                hint = f" (è a pagina {elsewhere[0]})" if elsewhere else ""
                problems.append(f"{_label(i, j)}: citazione non trovata a pagina {e.pagina}{hint}")
    return cards.CheckResult("citazione_nel_testo", not problems, problems)


def check_numbers(f: ClaimFile) -> cards.CheckResult:
    problems = []
    for i, c in enumerate(f.affermazioni, 1):
        quotes = " ".join(e.citazione for e in c.evidenze)
        missing = cards.missing_numbers(italian_decimals(c.testo) if f.lingua == "it" else c.testo, quotes)
        if missing:
            problems.append(f"affermazione {i}: numeri non presenti nelle citazioni: {', '.join(missing)}")
    return cards.CheckResult("numeri_nella_citazione", not problems, problems)


def validate(conn: sqlite3.Connection, data) -> tuple[cards.Outcome, sqlite3.Row | None]:
    f, fmt = parse(data)
    if f is None:
        return cards.Outcome([fmt]), None
    art, doc = check_article(conn, f)
    pages = pages_of(conn, doc["id"]) if doc is not None else {}
    checks = [fmt, check_language(f), art, check_positions(f, pages), check_quotes(f, pages), check_numbers(f)]
    return cards.Outcome(checks, f), doc


# ---------------------------------------------------------------- level of evidence

def _design_class(design: str) -> tuple[str, str]:
    d = (design or "").lower()
    if any(k in d for k in ("systematic review", "meta-analys", "meta analys", "umbrella review")):
        return "alto", "revisione sistematica / meta-analisi"
    crossover = "crossover" in d or "cross-over" in d
    if "randomi" in d and not crossover and any(k in d for k in ("controlled", "trial", "rct")):
        return "alto", "studio randomizzato controllato"
    if "randomi" in d and crossover:
        return "medio", "crossover randomizzato"
    if ("prospective" in d and "cohort" in d) or "longitudinal" in d:
        return "medio", "coorte prospettica / longitudinale"
    if "case-control" in d or "case control" in d:
        return "medio", "caso-controllo"
    if "controlled" in d and "uncontrolled" not in d:
        return "medio", "studio controllato non randomizzato"
    return "basso", "trasversale, osservazionale, di laboratorio o pre-post"


def appraisal(conn: sqlite3.Connection, article_id: str) -> tuple[str | None, dict]:
    card = current_card(conn, article_id)
    if card is None:
        return None, {"motivo": "nessuna scheda"}
    vals = conn.execute("SELECT field, value, origin FROM card_value WHERE card_id = ? ORDER BY ord", (card["id"],)).fetchall()
    design = "; ".join(v["value"] for v in vals if v["field"] == "disegno_studio" and v["value"])
    numbers = []
    for v in vals:
        if v["field"] == "partecipanti_numero" and v["value"]:
            for tok in cards.numbers(v["value"]):
                try:
                    numbers.append(int(float(tok.replace(",", ""))))
                except ValueError:
                    continue
    n = max(numbers) if numbers else None
    control = any(v["field"] == "confronto" and v["origin"] != "not_available" for v in vals)
    level, klass = _design_class(design)
    steps = [f"disegno: {klass} → {level}"]
    if n is not None and n < MIN_PARTICIPANTS and level != "basso":
        level = LEVELS[LEVELS.index(level) - 1]
        steps.append(f"meno di {MIN_PARTICIPANTS} partecipanti ({n}) → {level}")
    if not control and level != "basso":
        level = LEVELS[LEVELS.index(level) - 1]
        steps.append(f"senza gruppo di confronto → {level}")
    return level, {"disegno": design[:300], "classe": klass, "partecipanti": n, "gruppo_di_confronto": control,
                   "passi": steps}


def segment_for(conn: sqlite3.Connection, document_id: str, page: int, quote: str) -> str | None:
    rows = conn.execute("""SELECT id, text FROM segment WHERE document_id = ? AND rules = ? AND page_from <= ?
                           AND page_to >= ? ORDER BY ord""", (document_id, SEGMENT_RULES, page, page)).fetchall()
    q = cards.squash(quote)[:60]
    for r in rows:
        if q and q in cards.squash(r["text"]):
            return r["id"]
    return rows[0]["id"] if rows else None


# ---------------------------------------------------------------- submit

def to_send_dir(home: Path) -> Path:
    return Path(home) / REPORTS_DIR / CLAIMS_DIR / TO_SEND


def accepted_dir(home: Path) -> Path:
    return Path(home) / REPORTS_DIR / CLAIMS_DIR / ACCEPTED


@dataclass
class Submission:
    path: Path
    raw: bytes = b""
    sha256: str = ""
    data: object = None
    stop: str | None = None


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
    if conn.execute("SELECT 1 FROM scientific_claim WHERE file_sha256 = ?", (s.sha256,)).fetchone():
        s.stop = "affermazioni già accettate (stesso file)"
    return s


def record(conn: sqlite3.Connection, s: Submission, *, actor: str = "orpheus affermazioni") -> dict:
    outcome, doc = validate(conn, s.data)
    names = {c.name for c in outcome.checks}
    checks = outcome.checks + [cards.CheckResult(n, False, ["non eseguito: il formato non è valido"])
                               for n in CHECKS if n not in names]
    out = {"file": s.path.name, "esito": "accettato" if outcome.accepted else "rifiutato",
           "controlli": {c.name: ("ok" if c.ok else "non superato") for c in checks}}
    f: ClaimFile | None = outcome.card
    with transaction(conn):
        if outcome.accepted:
            project = card_store._ensure_project(conn, "ORPHEUS", "claims")
            superseded = conn.execute("UPDATE scientific_claim SET status = 'superseded' WHERE article_id = ? AND "
                                      "status = 'current' AND coalesce(language, 'it') = ?",
                                      (f.article_id, f.lingua)).rowcount
            level, reasons = appraisal(conn, f.article_id)
            ids = []
            for i, c in enumerate(f.affermazioni, 1):
                cid = new_id("scientific_claim")
                ids.append(cid)
                conn.execute("""INSERT INTO scientific_claim (id, project_id, statement, claim_type, created_by, article_id,
                                    claim_version, language, ord, status, file_sha256, model_ref)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'current', ?, ?)""",
                             (cid, project, c.testo, c.tipo, f.modello, f.article_id, CLAIM_VERSION, f.lingua, i,
                              s.sha256, f.modello))
                for e in c.evidenze:
                    conn.execute("""INSERT INTO evidence_record (id, claim_id, article_id, location, support, origin,
                                        document_id, page_no, quote, segment_id, appraisal_level, appraisal_rules,
                                        appraisal_reasons) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                                 (new_id("evidence_record"), cid, f.article_id, json.dumps({"pagina": e.pagina}),
                                  e.sostegno, e.origine, doc["id"], e.pagina, e.citazione,
                                  segment_for(conn, doc["id"], e.pagina, e.citazione), level, APPRAISAL_RULES,
                                  json.dumps(reasons, ensure_ascii=False)))
            audit.record(conn, actor=actor, action="claims_accepted", entity_type="article", entity_id=f.article_id,
                         payload={"claims": len(ids), "superseded": superseded, "file_sha256": s.sha256,
                                  "claim_version": CLAIM_VERSION, "appraisal": level, "model": f.modello,
                                  "checks": [c.as_dict() for c in checks]}, in_transaction=True)
            out |= {"article_id": f.article_id, "lingua": f.lingua, "affermazioni": len(ids), "livello": level,
                    "sostituite": superseded}
        else:
            problems = [f"[{c.name}] {p}" for c in checks if not c.ok for p in c.problems]
            audit.record(conn, actor=actor, action="claims_rejected", entity_type="article",
                         entity_id=f.article_id if f else None,
                         payload={"file": s.path.name, "file_sha256": s.sha256, "claim_version": CLAIM_VERSION,
                                  "failed": [c.name for c in checks if not c.ok], "problems": problems[:50]},
                         in_transaction=True)
            out["motivi"], out["motivi_totali"] = problems[:40], len(problems)
    return out


def file_after(s: Submission, summary: dict, home: Path) -> str | None:
    motivi = s.path.with_name(s.path.stem + ".motivi.txt")
    if summary.get("esito") == "accettato" or (s.stop or "").startswith("affermazioni già accettate"):
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
    lines = [f"ORPHEUS - affermazioni rifiutate - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", f"file: {s.path.name}",
             ""] + ([f"- {s.stop}"] if s.stop else []) + [f"- {m}" for m in summary.get("motivi", [])] + \
            ["", "Claude corregge il file; poi rilancia affermazioni.cmd."]
    motivi.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    return str(motivi)


def submit_folder(conn: sqlite3.Connection, home: Path) -> list[dict]:
    folder = to_send_dir(home)
    out = []
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        s = prepare(conn, path)
        summary = {"file": path.name, "esito": "non inviato", "motivo": s.stop} if s.stop else record(conn, s)
        summary["dopo"] = file_after(s, summary, home)
        out.append(summary)
    return out


def status(conn: sqlite3.Connection) -> dict:
    q = lambda sql: conn.execute(sql).fetchone()[0]   # noqa: E731
    levels = dict(conn.execute("""SELECT e.appraisal_level, count(DISTINCT c.article_id) FROM scientific_claim c
                                  JOIN evidence_record e ON e.claim_id = c.id WHERE c.status = 'current'
                                  GROUP BY e.appraisal_level""").fetchall())
    return {"affermazioni": q("SELECT count(*) FROM scientific_claim WHERE status = 'current' AND article_id IS NOT NULL"),
            "evidenze": q("""SELECT count(*) FROM evidence_record e JOIN scientific_claim c ON c.id = e.claim_id
                             WHERE c.status = 'current'"""),
            "articoli": q("SELECT count(DISTINCT article_id) FROM scientific_claim WHERE status = 'current'"),
            "articoli_per_livello": {k or "senza livello": v for k, v in levels.items()},
            "per_lingua": dict(conn.execute("""SELECT coalesce(language, 'it'), count(*) FROM scientific_claim
                                               WHERE status = 'current' AND article_id IS NOT NULL GROUP BY 1
                                               ORDER BY 1 DESC""").fetchall()),
            "controllate_a_mano": q("SELECT count(*) FROM scientific_claim WHERE status = 'current' AND verified_by_human = 1")}


# ---------------------------------------------------------------- hand check of every claim

CONTROL_PREFIX = "affermazioni_controllo"
CONTROL_COLUMNS = ["n", "id_affermazione", "articolo", "doi", "file_pdf", "pagina", "affermazione", "lingua", "tipo", "citazione",
                   "sostegno", "livello_prova", "perche_livello", "cosa_controllare", "giusto", "note"]
_CONTROL_ORIGINAL = re.compile(rf"^{CONTROL_PREFIX}_(\d+)_\d{{4}}-\d{{2}}-\d{{2}}(_\d{{6}})?$")
TODO = ("l'affermazione dice quello che dice la frase alla pagina indicata (e il sostegno è giusto); il livello segue "
        "le regole (disegno, partecipanti, gruppo di confronto)")


def control_rows(conn: sqlite3.Connection, *, only_unchecked: bool = False) -> list[dict]:
    rows = []
    for c in conn.execute(f"""SELECT c.id, c.statement, c.claim_type, coalesce(c.language, 'it') AS language, a.title,
                                     a.doi FROM scientific_claim c
                              JOIN article a ON a.id = c.article_id WHERE c.status = 'current'
                              {"AND c.verified_by_human = 0" if only_unchecked else ""}
                              ORDER BY a.publication_year, a.title, coalesce(c.language, 'it') DESC, c.ord"""):
        ev = conn.execute("""SELECT e.page_no, e.quote, e.support, e.appraisal_level, e.appraisal_reasons, d.original_path
                             FROM evidence_record e LEFT JOIN document d ON d.id = e.document_id
                             WHERE e.claim_id = ? ORDER BY e.rowid""", (c["id"],)).fetchall()
        reasons = json.loads(ev[0]["appraisal_reasons"] or "{}") if ev else {}
        rows.append({"id_affermazione": c["id"], "articolo": c["title"], "doi": c["doi"] or "",
                     "file_pdf": ((ev[0]["original_path"] or "").replace("\\", "/").rsplit("/", 1)[-1]) if ev else "",
                     "pagina": excel_text(" | ".join(str(e["page_no"]) for e in ev)),
                     "affermazione": c["statement"], "lingua": c["language"], "tipo": c["claim_type"],
                     "citazione": " | ".join(e["quote"] or "" for e in ev),
                     "sostegno": " | ".join(e["support"] for e in ev),
                     "livello_prova": ev[0]["appraisal_level"] if ev else "",
                     "perche_livello": "; ".join(reasons.get("passi", [])), "cosa_controllare": TODO,
                     "giusto": "", "note": ""})
    for n, r in enumerate(rows, 1):
        r["n"] = n
    return rows


def _control_bytes(rows: list[dict]) -> bytes:
    buf = io.StringIO(newline="")
    w = csv.DictWriter(buf, fieldnames=CONTROL_COLUMNS, delimiter=";", lineterminator="\r\n")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return buf.getvalue().encode("utf-8-sig")


def existing_control(rows: list[dict], home: Path, data: bytes | None = None) -> Path | None:
    """The control file already written with exactly these rows (to fill in), if any."""
    data = _control_bytes(rows) if data is None else data
    folder = Path(home) / REPORTS_DIR
    for old in sorted(folder.glob(f"{CONTROL_PREFIX}_{len(rows)}_*.csv")) if folder.is_dir() else []:
        if _CONTROL_ORIGINAL.match(old.stem) and old.read_bytes() == data:
            return old
    return None


def write_control_csv(rows: list[dict], home: Path, now: datetime | None = None) -> tuple[Path, bool]:
    now = now or datetime.now()
    data = _control_bytes(rows)
    folder = Path(home) / REPORTS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    old = existing_control(rows, home, data)
    if old is not None:
        return old, False
    path = folder / f"{CONTROL_PREFIX}_{len(rows)}_{now.strftime('%Y-%m-%d')}.csv"
    if path.exists():
        path = path.with_name(f"{path.stem}_{now.strftime('%H%M%S')}.csv")
    path.write_bytes(data)
    return path, True


def latest_filled_control(home: Path) -> Path | None:
    folder = Path(home) / REPORTS_DIR
    found = [p for p in folder.glob(f"{CONTROL_PREFIX}_*.csv") if not _CONTROL_ORIGINAL.match(p.stem)] \
        if folder.is_dir() else []
    return max(found, key=lambda p: (p.stat().st_mtime, p.name)) if found else None


def _verdict(cell) -> str:
    v = (cell or "").strip().upper()[:1]
    return v if v in ("S", "N") else ("" if not v else "?")


def score_control(path: Path) -> dict:
    rows, info = read_filled(path, need={"id_affermazione", "giusto"})
    for r in rows:                                   # files of 0.22.0 have no "lingua" column: all Italian
        r["lingua"] = (r.get("lingua") or "it").strip() or "it"
    verdicts = [_verdict(r.get("giusto")) for r in rows]

    def prec(vs):
        g, s = vs.count("S"), vs.count("N")
        return {"giusti": g, "sbagliati": s, "non_compilati": vs.count(""), "non_validi": vs.count("?"),
                "precisione": round(g / (g + s), 3) if g + s else None}
    by = lambda key: {k: prec([v for v, r in zip(verdicts, rows) if r.get(key) == k])  # noqa: E731
                      for k in dict.fromkeys(r.get(key, "") for r in rows)}
    return {"file": str(path), **info, "righe": len(rows), "precisione_complessiva": prec(verdicts),
            "per_livello": by("livello_prova"), "per_tipo": by("tipo"), "per_lingua": by("lingua"),
            "sbagliate": [{"n": r.get("n"), "articolo": (r.get("articolo") or "")[:80],
                           "affermazione": excel_plain(r.get("affermazione"))[:200], "note": (r.get("note") or "").strip()}
                          for v, r in zip(verdicts, rows) if v == "N"]}


def new_verdicts(conn: sqlite3.Connection, path: Path) -> int:
    """S/N of the filled file that are not yet in the database (claim never checked, or a different verdict)."""
    rows, _ = read_filled(path, need={"id_affermazione", "giusto"})
    n = 0
    for r in rows:
        v = _verdict(r.get("giusto"))
        if v in ("S", "N"):
            db = conn.execute("SELECT human_verdict FROM scientific_claim WHERE id = ?",
                              ((r.get("id_affermazione") or "").strip(),)).fetchone()
            n += db is not None and db["human_verdict"] != ("right" if v == "S" else "wrong")
    return n


def control_pending(conn: sqlite3.Connection, home: Path) -> dict:
    """What affermazioni-controllo would do now, without writing (for `orpheus lavora`): new S/N to record, claims never
    checked, and the control file already waiting for them (None: it is still to be written)."""
    filled = latest_filled_control(home)
    fresh = new_verdicts(conn, filled) if filled is not None else 0
    todo = control_rows(conn, only_unchecked=True)
    waiting = existing_control(todo, home) if todo else None
    return {"giudizi_nuovi": fresh, "da_controllare": len(todo), "file_da_compilare": str(waiting) if waiting else None,
            "da_fare": fresh + (1 if todo and waiting is None else 0)}


def control_step(conn: sqlite3.Connection, home: Path, now: datetime | None = None) -> dict:
    """What affermazioni-controllo does now (0.23.0): record a filled file with new S/N; otherwise a CSV of the claims
    never checked (e.g. new English claims, or new claims that superseded checked ones); otherwise the latest results."""
    filled = latest_filled_control(home)
    todo = control_rows(conn, only_unchecked=True)
    fresh = new_verdicts(conn, filled) if filled is not None else 0
    if filled is not None and (fresh or not todo):
        res = score_control(filled)
        res["affermazioni_registrate"] = record_verdicts(conn, filled) if fresh else 0
        res["da_controllare"] = len(control_rows(conn, only_unchecked=True))
        return {"modo": "risultati", **res}
    if todo:
        path, written = write_control_csv(todo, home, now)
        return {"modo": "file da compilare", "file_csv": str(path), "nuovo_file": written, "righe": len(todo),
                "file_da_salvare": str(path.with_name(f"{path.stem}_compilato.csv"))}
    return {"modo": "niente da controllare", "motivo": "nessuna affermazione nel database"}


def record_verdicts(conn: sqlite3.Connection, path: Path, *, actor: str = "orpheus affermazioni") -> int:
    rows, _ = read_filled(path, need={"id_affermazione", "giusto"})
    n = 0
    with transaction(conn):
        for r in rows:
            v = _verdict(r.get("giusto"))
            if v not in ("S", "N"):
                continue
            verdict = "right" if v == "S" else "wrong"
            cid = (r.get("id_affermazione") or "").strip()
            n += conn.execute("UPDATE scientific_claim SET verified_by_human = 1, human_verdict = ? WHERE id = ?",
                              (verdict, cid)).rowcount
            conn.execute("UPDATE evidence_record SET verified_by_human = 1, human_verdict = ? WHERE claim_id = ?",
                         (verdict, cid))
        if n:
            audit.record(conn, actor=actor, action="claims_hand_check", payload={"file": Path(path).name, "claims": n},
                         in_transaction=True)
    return n


def instructions() -> dict:
    return {"formato": CLAIM_FORMAT, "versione": CLAIM_VERSION, "livello": APPRAISAL_RULES,
            "regole": ["1-6 affermazioni sui risultati, le conclusioni o i limiti dell'articolo stesso, in inglese "
                       "(\"lingua\": \"en\"; dal 2026-10-15 solo inglese, decisione dell'utente); un file per articolo",
                       "ogni affermazione con 1-3 evidenze: pagina del PDF e frase copiata da quella pagina (12-500 caratteri)",
                       "ogni numero dell'affermazione è nelle citazioni (in italiano 6,8 = 6.8)",
                       "un file nuovo dello stesso articolo e della stessa lingua sostituisce le affermazioni di prima"]}
