"""T-041 — Fase 6 (A): a new article from the user's own data, for a journal, section by section (writing-v1).

User's decisions of 2026-10-16: the article starts from the user's data (original study) and is written section by
section (piano, metodi, risultati, introduzione, discussione, abstract), each approved by the user before the next.
R10: everything stays on the PC and in sessions with Claude (folder `nuovi_articoli\\<name>` of the data disk).

Flow (as for the article cards, ADR-0008):
  1. the user fills `studio.csv` (the study: question, design, participants, methods, ethics...) and `risultati.csv`
     (the results, one row per value) — in English, as the article;
  2. `orpheus nuovo-articolo` checks them and writes `pacchetto.json`: the study, the results, the rules of the journal
     (journal card), the evidence from ORPHEUS (articles with a verified DOI, their English claims and card values with
     page and quote), the allowed references, instructions and an example;
  3. Claude, in a session, writes `sezioni/da_inviare/<sezione>.json` (format orpheus-sezione-v1), citing only with
     `[@<article_id>]`;
  4. `--invia` checks every section with deterministic validators (no unverified citation, no untraced number, English,
     abstract labels and limits of the journal) and moves it to `sezioni/accettate` (or writes `<sezione>.motivi.txt`);
     `anteprima.html` shows the accepted sections with numbered references;
  5. `--approva <sezione>`: the user approves; only then the next section can be sent.
A number in the text is traced when it is in studio.csv or risultati.csv, in the quotes of the evidence cited in the
same paragraph, or declared in "derivati" with a formula on traced numbers (recomputed here). Nothing is written in the
database. The output for the log has states and counts only.
"""
from __future__ import annotations

import ast
import csv
import hashlib
import html
import io
import json
import math
import re
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import compliance, semantic
from .claims import language_of

WRITING_VERSION = "writing-v1"
PACKET_FORMAT = "orpheus-pacchetto-scrittura-v1"
SECTION_FORMAT = "orpheus-sezione-v1"
FOLDER = "nuovi_articoli"
SECTIONS = ("piano", "metodi", "risultati", "introduzione", "discussione", "abstract")
# Types of article (R11; 2026-10-17: also protocols). A protocol has no Results and no Discussion: its sections are
# Methods and analysis, Introduction, Ethics and dissemination.
KINDS = ("originale", "revisione", "protocollo")
SECTIONS_BY_KIND = {"originale": SECTIONS, "revisione": SECTIONS,
                    "protocollo": ("piano", "metodi", "introduzione", "etica_diffusione", "abstract")}
ALL_SECTIONS = tuple(dict.fromkeys(SECTIONS + SECTIONS_BY_KIND["protocollo"]))
SECTION_TITLES = {"piano": "Piano dell'articolo", "metodi": "Methods", "risultati": "Results",
                  "introduzione": "Introduction", "discussione": "Discussion", "abstract": "Abstract",
                  "etica_diffusione": "Ethics and dissemination"}
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,59}$")
MAX_EVIDENCE = 25
CITE = re.compile(r"\[@([^\]]+)\]")
OTHER_CITES = re.compile(r"\[\d+(?:[,–-]\s*\d+)*\]|\([A-Z][A-Za-z'’\-]+(?: et al\.?)?,? (?:19|20)\d{2}[a-z]?\)")

# (campo, required, what to write — Italian, for the user); "required" is for an original study, see REQUIRED_BY_KIND
STUDY_FIELDS = [
    ("tipo", False, "Tipo di articolo: originale, revisione (revisione sistematica o meta-analisi) o protocollo; "
                    "vuoto = originale"),
    ("titolo_provvisorio", False, "Titolo di lavoro dell'articolo (in inglese)"),
    ("rivista", True, "Sigla della rivista (come in riviste_prioritarie.csv, es. JSAMS)"),
    ("domanda_di_ricerca", True, "Domanda di ricerca o obiettivo, in una o due frasi"),
    ("ipotesi", False, "Ipotesi, se c'è"),
    ("disegno", True, "Disegno dello studio (es. cross-sectional, randomised controlled trial, cohort)"),
    ("contesto", False, "Dove e quando sono stati raccolti i dati"),
    ("partecipanti_numero", True, "Numero di partecipanti analizzati (e reclutati, se diverso)"),
    ("partecipanti_sesso", False, "Maschi e femmine"),
    ("partecipanti_eta", False, "Età (media ± DS o intervallo)"),
    ("partecipanti_livello", False, "Livello (es. elite, sub-elite, recreational)"),
    ("sport", False, "Sport o attività"),
    ("criteri_inclusione", False, "Criteri di inclusione"),
    ("criteri_esclusione", False, "Criteri di esclusione"),
    ("procedure", True, "Procedure e protocollo (cosa hanno fatto i partecipanti, in ordine)"),
    ("misure", True, "Misure e strumenti (modello, frequenza di campionamento, affidabilità se nota)"),
    ("variabili_principali", False, "Variabili principali (outcome)"),
    ("analisi_statistica", True, "Analisi statistica (test, livello di significatività, effect size, software)"),
    ("etica_comitato", True, "Comitato etico che ha approvato lo studio (o 'not applicable')"),
    ("etica_numero", False, "Numero e data dell'approvazione"),
    ("consenso", False, "Consenso informato (e dei genitori per i minori)"),
    ("registrazione", False, "Registro e numero (es. ClinicalTrials.gov NCT...), se c'è"),
    ("finanziamenti", False, "Finanziamenti (o 'none')"),
    ("conflitti", False, "Conflitti di interesse (o 'none')"),
    ("disponibilita_dati", False, "Disponibilità dei dati"),
    ("uso_ia", False, "Uso di strumenti di IA nella scrittura (ORPHEUS e Claude): come dichiararlo"),
    ("parole_chiave", False, "Parole chiave, separate da ;"),
    ("riferimenti_chiave", False, "DOI dei lavori che vuoi citare di sicuro, separati da ; (devono essere in ORPHEUS)"),
]
# Fields only for a systematic review or meta-analysis (PRISMA 2020) and only for a protocol (SPIRIT / PRISMA-P)
REVIEW_FIELDS = [
    ("registrazione_revisione", False, "Registro e numero del protocollo della revisione (es. PROSPERO CRD...), o "
                                       "'not registered'"),
    ("banche_dati", False, "Banche dati e altre fonti consultate (es. PubMed; Embase; SPORTDiscus)"),
    ("strategia_ricerca", False, "Stringa di ricerca completa di almeno una banca dati"),
    ("date_ricerca", False, "Date della ricerca (dall'inizio al giorno dell'ultima ricerca)"),
    ("selezione_studi", False, "Come sono stati selezionati gli studi (quanti revisori, indipendenti, programma)"),
    ("estrazione_dati", False, "Come sono stati raccolti i dati dagli studi"),
    ("rischio_bias", False, "Strumento per il rischio di bias (es. RoB 2, ROBINS-I, PEDro)"),
    ("sintesi", False, "Metodo di sintesi (es. meta-analisi a effetti casuali, SMD, eterogeneità I2) o sintesi narrativa"),
    ("certezza_prove", False, "Certezza delle prove (es. GRADE), se valutata"),
    ("prisma_identificati", False, "Diagramma PRISMA: record identificati"),
    ("prisma_doppioni", False, "Diagramma PRISMA: doppioni rimossi"),
    ("prisma_esaminati", False, "Diagramma PRISMA: record esaminati (titolo e abstract)"),
    ("prisma_esclusi_esame", False, "Diagramma PRISMA: record esclusi a titolo e abstract"),
    ("prisma_testo_completo", False, "Diagramma PRISMA: articoli letti per intero"),
    ("prisma_esclusi_testo", False, "Diagramma PRISMA: articoli esclusi dopo la lettura (con i motivi in 'note')"),
    ("prisma_inclusi", False, "Diagramma PRISMA: studi inclusi"),
    ("prisma_meta_analisi", False, "Studi inclusi nella meta-analisi, se c'è"),
]
PROTOCOL_FIELDS = [
    ("stato_reclutamento", False, "Stato: reclutamento non ancora iniziato, in corso (da quando), concluso"),
    ("date_previste", False, "Date previste: inizio e fine del reclutamento, fine dello studio"),
    ("numerosita_prevista", False, "Numerosità prevista e come è calcolata (effetto atteso, potenza, alfa, abbandoni)"),
    ("interventi", False, "Interventi di ogni gruppo (cosa, quanto, chi, dove)"),
    ("esito_primario", False, "Esito primario, con il momento della misura"),
    ("esiti_secondari", False, "Esiti secondari"),
    ("allocazione", False, "Sequenza casuale e occultamento dell'allocazione (se randomizzato)"),
    ("cecita", False, "Chi sarà in cieco"),
    ("raccolta_dati", False, "Raccolta e gestione dei dati"),
    ("monitoraggio", False, "Monitoraggio dei dati, analisi intermedie, eventi avversi"),
    ("diffusione", False, "Come saranno diffusi i risultati"),
]
ALL_FIELDS = STUDY_FIELDS + REVIEW_FIELDS + PROTOCOL_FIELDS
REQUIRED_BY_KIND = {
    "originale": {c for c, req, _ in STUDY_FIELDS if req},
    "revisione": {"rivista", "domanda_di_ricerca", "criteri_inclusione", "registrazione_revisione", "banche_dati",
                  "strategia_ricerca", "date_ricerca", "selezione_studi", "rischio_bias", "sintesi",
                  "prisma_identificati", "prisma_esaminati", "prisma_testo_completo", "prisma_inclusi"},
    "protocollo": {"rivista", "domanda_di_ricerca", "disegno", "criteri_inclusione", "interventi", "esito_primario",
                   "numerosita_prevista", "analisi_statistica", "etica_comitato", "stato_reclutamento", "date_previste"},
}
FIELDS_BY_KIND = {"originale": STUDY_FIELDS, "revisione": STUDY_FIELDS + REVIEW_FIELDS,
                  "protocollo": STUDY_FIELDS + PROTOCOL_FIELDS}
INCLUDED_COLUMNS = ["studio", "doi", "disegno", "n", "partecipanti", "intervento", "confronto", "esiti", "rischio_bias",
                    "note"]
INCLUDED_FILE = "studi_inclusi.csv"
RESULT_COLUMNS = ["tabella", "variabile", "gruppo", "n", "media", "ds", "mediana", "iqr", "min", "max", "differenza",
                  "ic95_basso", "ic95_alto", "p", "effetto", "tipo_effetto", "unita", "note"]
NUMERIC_COLUMNS = ["n", "media", "ds", "mediana", "min", "max", "differenza", "ic95_basso", "ic95_alto", "effetto"]

README = """ORPHEUS — nuovo articolo dai tuoi dati (T-041)
==============================================

1. Compila studio.csv (una riga per voce: colonna "valore") e risultati.csv (una riga per ogni risultato).
   Scrivi in INGLESE, come l'articolo. Si aprono con Excel; salvali sempre come CSV (separatore ;).
2. Doppio clic su nuovo_articolo.cmd: ORPHEUS controlla i file e prepara pacchetto.json per Claude.
3. In una sessione con Claude (PC collegato): Claude scrive una sezione alla volta in sezioni\\da_inviare.
4. Doppio clic su nuovo_articolo.cmd: ORPHEUS controlla la sezione (citazioni verificate, numeri tracciati) e apre
   anteprima.html; se va bene la approvi e Claude passa alla sezione successiva.
Ordine: piano, metodi, risultati, introduzione, discussione, abstract.
Revisione sistematica o meta-analisi: anche studi_inclusi.csv (una riga per studio, con il DOI: deve essere in
ORPHEUS) e i numeri del diagramma PRISMA in studio.csv; risultati.csv con le sintesi (n = numero di studi).
Protocollo: niente risultati.csv; ordine piano, metodi (Methods and analysis), introduzione, etica_diffusione
(Ethics and dissemination), abstract; tutto al futuro, nessun risultato.

Regole: i numeri del testo vengono solo da questi due file o dagli articoli citati; si citano solo articoli presenti in
ORPHEUS con DOI verificato. Questa cartella resta sul PC: non va mai su GitHub.
"""


class WritingError(Exception):
    """A problem for the user (message in Italian)."""


def folder(home: Path) -> Path:
    return Path(home) / FOLDER


def project_dir(home: Path, name: str) -> Path:
    if not NAME_RE.match(name or ""):
        raise WritingError("nome del progetto non valido: solo lettere, numeri, - e _ (senza spazi), al massimo 60")
    return folder(home) / name


def list_projects(home: Path) -> list[str]:
    root = folder(home)
    return sorted(p.name for p in root.iterdir() if p.is_dir() and NAME_RE.match(p.name)) if root.is_dir() else []


# ---------------------------------------------------------------------------------------------- the user's files
def _write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    w.writerow(header)
    w.writerows(rows)
    path.write_text(buf.getvalue(), encoding="utf-8-sig")


def create(home: Path, name: str, journal: str | None = None, kind: str = "originale") -> Path:
    d = project_dir(home, name)
    if kind not in KINDS:
        raise WritingError(f"tipo di articolo non valido: uno di {', '.join(KINDS)}")
    if d.exists():
        raise WritingError("il progetto esiste già")
    (d / "sezioni" / "da_inviare").mkdir(parents=True)
    (d / "sezioni" / "accettate").mkdir(parents=True)
    need = REQUIRED_BY_KIND[kind]
    preset = {"rivista": journal or "", "tipo": kind}
    _write_csv(d / "studio.csv", ["campo", "valore", "descrizione"],
               [[c, preset.get(c, ""), ("OBBLIGATORIO. " if c in need else "") + desc]
                for c, _req, desc in FIELDS_BY_KIND[kind]])
    if kind == "revisione":
        _write_csv(d / "risultati.csv", RESULT_COLUMNS,
                   [["Table 2", "Sprint time", "Training vs control", "12", "", "", "", "", "", "", "-0.42", "-0.61",
                     "-0.23", "<0.001", "0.42", "SMD", "", "ESEMPIO: risultato della meta-analisi (n = numero di "
                     "studi); cancella questa riga"]])
        _write_csv(d / INCLUDED_FILE, INCLUDED_COLUMNS,
                   [["Rossi 2020", "10.xxxx/esempio", "RCT", "40", "youth football players", "Nordic hamstring",
                     "usual training", "sprint time", "low", "ESEMPIO: cancella questa riga"]])
    elif kind == "originale":
        _write_csv(d / "risultati.csv", RESULT_COLUMNS,
                   [["Table 1", "Age", "All", "130", "15.2", "1.9", "", "", "12", "18", "", "", "", "", "", "", "years",
                     "ESEMPIO: cancella questa riga"]])
    (d / "LEGGIMI.txt").write_text(README.replace("\n", "\r\n"), encoding="utf-8-sig")
    return d


def _read_csv(path: Path) -> list[dict]:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1252"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    first = text.splitlines()[0] if text.strip() else ""
    delim = ";" if first.count(";") >= first.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    reader.fieldnames = [(f or "").strip().lower() for f in reader.fieldnames or []]
    return [{k: (v or "").strip() for k, v in r.items() if k} for r in reader]


def read_study(d: Path) -> tuple[dict, list[str]]:
    path = d / "studio.csv"
    if not path.exists():
        return {}, ["manca studio.csv"]
    try:
        rows = _read_csv(path)
    except OSError as exc:
        return {}, [f"studio.csv non leggibile ({exc.__class__.__name__}): se è aperto in Excel, chiudilo"]
    known = {c for c, _, _ in ALL_FIELDS}
    study = {r.get("campo", ""): r.get("valore", "") for r in rows if r.get("campo") in known}
    kind = kind_of_study(study)
    if kind is None:
        return study, [f"studio.csv: 'tipo' deve essere uno di: {', '.join(KINDS)} (vuoto = originale)"]
    problems = [f"studio.csv: manca il valore di '{c}'" for c, _r, _d in FIELDS_BY_KIND[kind]
                if c in REQUIRED_BY_KIND[kind] and not study.get(c)]
    if kind == "revisione":
        problems += prisma_problems(study)
    italian = [c for c, v in study.items() if c not in ("rivista", "riferimenti_chiave") and len(v.split()) >= 6
               and language_of(v)[0] == "it"]
    problems += [f"studio.csv: '{c}' sembra in italiano: l'articolo è in inglese (R11)" for c in italian]
    return study, problems


def kind_of_study(study: dict) -> str | None:
    k = (study.get("tipo") or "originale").strip().lower()
    k = {"original": "originale", "studio originale": "originale", "review": "revisione", "systematic review": "revisione",
         "meta-analisi": "revisione", "meta-analysis": "revisione", "protocol": "protocollo"}.get(k, k)
    return k if k in KINDS else None


def kind_of(d: Path) -> str:
    """The type of the project (from studio.csv; originale when it cannot be read)."""
    try:
        rows = _read_csv(d / "studio.csv") if (d / "studio.csv").exists() else []
    except OSError:
        rows = []
    study = {r.get("campo", ""): r.get("valore", "") for r in rows}
    return kind_of_study(study) or "originale"


def sections_of(kind: str) -> tuple[str, ...]:
    return SECTIONS_BY_KIND.get(kind, SECTIONS)


def prisma_problems(study: dict) -> list[str]:
    """The numbers of the PRISMA flow diagram must add up (no number in the message: R10)."""
    n = {k: _num(study.get(f"prisma_{k}", "")) for k in ("identificati", "doppioni", "esaminati", "esclusi_esame",
                                                          "testo_completo", "esclusi_testo", "inclusi", "meta_analisi")}
    out = []
    if None not in (n["identificati"], n["doppioni"], n["esaminati"]) and n["identificati"] - n["doppioni"] != n["esaminati"]:
        out.append("studio.csv: PRISMA, identificati meno doppioni non dà gli esaminati")
    if None not in (n["esaminati"], n["esclusi_esame"], n["testo_completo"]) and \
            n["esaminati"] - n["esclusi_esame"] != n["testo_completo"]:
        out.append("studio.csv: PRISMA, esaminati meno esclusi non dà gli articoli letti per intero")
    if None not in (n["testo_completo"], n["esclusi_testo"], n["inclusi"]) and \
            n["testo_completo"] - n["esclusi_testo"] != n["inclusi"]:
        out.append("studio.csv: PRISMA, letti per intero meno esclusi non dà gli inclusi")
    if None not in (n["meta_analisi"], n["inclusi"]) and n["meta_analisi"] > n["inclusi"]:
        out.append("studio.csv: PRISMA, gli studi della meta-analisi sono più degli inclusi")
    return out


def read_included(d: Path) -> tuple[list[dict], list[str]]:
    """studi_inclusi.csv of a review: one row per included study, with its DOI (it must be in ORPHEUS)."""
    path = d / INCLUDED_FILE
    if not path.exists():
        return [], [f"manca {INCLUDED_FILE}"]
    try:
        rows = _read_csv(path)
    except OSError as exc:
        return [], [f"{INCLUDED_FILE} non leggibile ({exc.__class__.__name__}): se è aperto in Excel, chiudilo"]
    out, problems = [], []
    for i, r in enumerate(rows, 2):
        if not any(r.values()) or "esempio" in (r.get("note") or "").lower():
            continue
        if not r.get("studio") or not r.get("doi"):
            problems.append(f"{INCLUDED_FILE} riga {i}: servono 'studio' e 'doi'")
        if r.get("n") and _num(r["n"]) is None:
            problems.append(f"{INCLUDED_FILE} riga {i}: 'n' non è un numero")
        out.append({c: r.get(c, "") for c in INCLUDED_COLUMNS})
    if not out:
        problems.append(f"{INCLUDED_FILE}: nessuno studio incluso (la riga di esempio non conta)")
    return out, problems


def _num(text: str) -> float | None:
    t = (text or "").strip().replace("−", "-").replace(",", ".")
    t = re.sub(r"^[<>≤≥=]\s*", "", t)
    try:
        return float(t)
    except ValueError:
        return None


def read_results(d: Path) -> tuple[list[dict], list[str]]:
    path = d / "risultati.csv"
    if not path.exists():
        return [], ["manca risultati.csv"]
    try:
        rows = _read_csv(path)
    except OSError as exc:
        return [], [f"risultati.csv non leggibile ({exc.__class__.__name__}): se è aperto in Excel, chiudilo"]
    problems, out = [], []
    for i, r in enumerate(rows, 2):
        if not any(r.values()) or "esempio" in (r.get("note") or "").lower():
            continue
        if not r.get("variabile"):
            problems.append(f"risultati.csv riga {i}: manca 'variabile'")
        for c in NUMERIC_COLUMNS + ["p"]:
            if r.get(c) and _num(r[c]) is None:
                problems.append(f"risultati.csv riga {i}: '{c}' non è un numero")   # no value: it goes in the log (R10)
        out.append({c: r.get(c, "") for c in RESULT_COLUMNS})
    if not out:
        problems.append("risultati.csv: nessun risultato (la riga di esempio non conta)")
    return out, problems


# -------------------------------------------------------------------------------------------- numbers and traces
NUMBER = re.compile(r"(?<![A-Za-z0-9_.])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?!(?:st|nd|rd|th)\b)(?!\.\d)"
                    r"|(?<=[\s=<>(])(\.\d+)\b")
LABELLED = re.compile(r"\b(?:Table|Figure|Fig\.|Supplementary (?:Table|Figure))\s*S?\d+[a-z]?"
                      r"|\b9[05]\s*%\s*(?=CI\b|confidence)", re.I)       # "95% CI": a convention, not a datum


def numbers_in(text: str) -> list[str]:
    """Numbers written in a text, as written ('0.08', '1,234'); not inside citations, names like VO2, ordinals,
    'Table 1' / 'Figure 2'."""
    text = CITE.sub(" ", text or "")
    text = LABELLED.sub(" ", text)
    return [m.group(1) or "0" + m.group(2) for m in NUMBER.finditer(text)]


def value(s: str) -> float:
    return float(s.replace(",", ""))


def decimals(s: str) -> int:
    return len(s.split(".", 1)[1]) if "." in s else 0


def traced_values(study: dict, results: list[dict]) -> set[float]:
    out = set()
    for v in study.values():
        out.update(value(n) for n in numbers_in(v))
    for r in results:
        for c, v in r.items():
            if c in ("tabella",):
                continue
            out.update(value(n) for n in numbers_in(v))
    return out


_ALLOWED_FUNCS = {"round": round, "abs": abs, "min": min, "max": max, "sqrt": math.sqrt}


def eval_formula(formula: str, allowed: set[float]) -> tuple[float | None, str | None]:
    """Arithmetic on traced numbers only (+ - * / ** and round, abs, min, max, sqrt). Returns (value, problem)."""
    try:
        tree = ast.parse(formula.replace("×", "*").replace("−", "-"), mode="eval")
    except SyntaxError:
        return None, f"formula non valida: {formula!r}"

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            if float(n.value) not in allowed and not (isinstance(n.value, int) and n.value in (0, 1, 2, 100)):
                raise ValueError(f"il numero {n.value} della formula non viene dai dati")
            return float(n.value)
        if isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)):
            a, b = ev(n.left), ev(n.right)
            return {ast.Add: a + b, ast.Sub: a - b, ast.Mult: a * b, ast.Pow: a ** b,
                    ast.Div: a / b if b else math.nan}[type(n.op)]
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.USub):
            return -ev(n.operand)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _ALLOWED_FUNCS and not n.keywords:
            args = [ev(a) for a in n.args]
            if n.func.id == "round" and len(args) == 2:
                return round(args[0], int(args[1]))
            return float(_ALLOWED_FUNCS[n.func.id](*args))
        raise ValueError(f"operazione non ammessa nella formula: {ast.dump(n)[:40]}")
    try:
        return ev(tree), None
    except (ValueError, OverflowError, ZeroDivisionError, TypeError) as exc:
        return None, str(exc)


# ---------------------------------------------------------------------------------------------- the packet
def _rules(jr) -> dict:
    keep = ("parole_testo_max", "abstract_parole_max", "abstract_struttura", "parole_chiave", "riferimenti_max",
            "figure_tabelle_max", "altri_elementi", "stile_citazioni", "politica_ia", "etica_consenso", "registrazione",
            "linee_guida_reporting", "conflitti_finanziamenti", "dati")
    return {f: [{"valore": r.value, "pagina": r.page} for r in jr.get(f)] for f in keep if jr.get(f)}


def _authors(conn, aid: str) -> list[str]:
    return [r[0] for r in conn.execute("""SELECT au.display_name FROM article_author aa JOIN author au ON au.id = aa.author_id
                                         WHERE aa.article_id = ? ORDER BY aa.position""", (aid,))]


def _evidence(conn, aid: str) -> dict | None:
    a = conn.execute("""SELECT a.id, a.doi, a.title, a.publication_year, a.integrity_status, j.title AS journal
                        FROM article a LEFT JOIN journal j ON j.id = a.journal_id WHERE a.id = ?""", (aid,)).fetchone()
    if a is None or not a["doi"] or a["integrity_status"] == "retracted":
        return None
    claims = []
    try:
        for c in conn.execute("""SELECT id, statement FROM scientific_claim WHERE article_id = ? AND status = 'current'
                                 AND language = 'en' ORDER BY ord""", (aid,)):
            ev = conn.execute("SELECT page_no, quote, appraisal_level FROM evidence_record WHERE claim_id = ? "
                              "ORDER BY rowid LIMIT 1", (c["id"],)).fetchone()
            claims.append({"affermazione": c["statement"], "citazione": ev["quote"] if ev else None,
                           "pagina": ev["page_no"] if ev else None, "livello": ev["appraisal_level"] if ev else None})
    except sqlite3.OperationalError:
        pass
    card = []
    try:
        cid = conn.execute("SELECT id FROM article_card WHERE article_id = ? AND status = 'current' "
                           "ORDER BY created_at DESC LIMIT 1", (aid,)).fetchone()
        if cid:
            card = [{"campo": v["field"], "valore": v["value"], "unita": v["unit"], "citazione": v["quote"],
                     "pagina": v["page_no"]}
                    for v in conn.execute("""SELECT field, value, unit, quote, page_no FROM card_value WHERE card_id = ?
                                             AND origin <> 'not_available' ORDER BY ord""", (cid[0],))]
    except sqlite3.OperationalError:
        pass
    return {"article_id": aid, "doi": a["doi"], "titolo": a["title"], "anno": a["publication_year"],
            "rivista": a["journal"], "autori": _authors(conn, aid), "affermazioni": claims, "scheda": card}


def find_evidence(conn, study: dict, embedder=None, index=None) -> tuple[list[dict], str]:
    query = " ".join(filter(None, [study.get("domanda_di_ricerca"), study.get("titolo_provvisorio"),
                                   study.get("parole_chiave", "").replace(";", " ")]))
    mode = "ibrida" if embedder is not None and index is not None and index.ids else "parole"
    ranked = semantic.ranked_segments(conn, query, mode, embedder=embedder, index=index)
    found = semantic.by_article(conn, ranked, limit=MAX_EVIDENCE * 2)
    ids = []
    for doi in [x.strip().lower() for x in (study.get("riferimenti_chiave") or "").split(";") if x.strip()]:
        r = conn.execute("SELECT id FROM article WHERE doi = ?", (doi.removeprefix("https://doi.org/"),)).fetchone()
        if r:
            ids.append(r[0])
    ids += [f["article_id"] for f in found if f["article_id"]]
    out = []
    for aid in dict.fromkeys(ids):
        ev = _evidence(conn, aid)
        if ev:
            out.append(ev)
        if len(out) >= MAX_EVIDENCE:
            break
    return out, mode


def missing_key_refs(conn, study: dict) -> list[str]:
    out = []
    for doi in [x.strip().lower() for x in (study.get("riferimenti_chiave") or "").split(";") if x.strip()]:
        if not conn.execute("SELECT 1 FROM article WHERE doi = ?", (doi.removeprefix("https://doi.org/"),)).fetchone():
            out.append(doi)
    return out


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""


def _prints(d: Path) -> dict:
    out = {"studio.csv": _sha(d / "studio.csv"), "risultati.csv": _sha(d / "risultati.csv")}
    if (d / INCLUDED_FILE).exists():
        out[INCLUDED_FILE] = _sha(d / INCLUDED_FILE)
    return out


INSTRUCTIONS = [
    "Scrivi in INGLESE, nello stile della rivista indicata, UNA sezione per file: sezioni/da_inviare/<sezione>.json.",
    "Ordine: piano, metodi, risultati, introduzione, discussione, abstract; una sezione si invia solo dopo "
    "l'approvazione della precedente (vedi 'sezioni').",
    "Cita solo gli articoli di 'evidenze' con [@<article_id>] (anche più insieme: [@art_1][@art_2]); nessun altro stile "
    "di citazione.",
    "Ogni numero del testo deve venire da 'studio' o 'risultati', dalle citazioni degli articoli citati nello stesso "
    "paragrafo, oppure stare in 'derivati' con la formula fatta solo di numeri dei dati (es. 66 / 130 * 100).",
    "Non inventare dati, risultati o riferimenti: se manca qualcosa, scrivilo in 'note' e chiedi all'utente.",
    "piano: 'messaggi_chiave' (3-6 frasi), 'parole' (budget per sezione, somma entro il limite della rivista), "
    "'titoli' (sottotitoli previsti per sezione) e paragrafi brevi con la scaletta.",
    "abstract: paragrafi con 'titolo' uguale ai titoletti richiesti dalla rivista (se strutturato), più "
    "'titolo_articolo' e 'parole_chiave'.",
    "discussione: comprende anche le sezioni finali richieste dalla rivista (Limitations, Conclusion, Practical "
    "Implications o Perspective), come paragrafi con 'titolo'.",
]
KIND_INSTRUCTIONS = {
    "revisione": [
        "Revisione sistematica o meta-analisi: segui PRISMA 2020. Metodi: registrazione, criteri, fonti, strategia e date "
        "della ricerca, selezione, raccolta dei dati, rischio di bias, sintesi, certezza delle prove (da 'studio').",
        "Risultati: il flusso con i numeri PRISMA di 'studio' (prisma_*), le caratteristiche degli studi da "
        "'studi_inclusi' (cita ogni studio con [@article_id]) e le sintesi da 'risultati' (n = numero di studi).",
    ],
    "protocollo": [
        "Protocollo: lo studio NON è ancora fatto. Scrivi al futuro ('will be recruited'); nessun risultato, nessun p.",
        "Sezioni: piano, metodi ('Methods and analysis': disegno, contesto, partecipanti, interventi, esiti, calendario, "
        "numerosità, reclutamento, allocazione e cecità, raccolta dei dati, analisi statistica, monitoraggio), "
        "introduzione, etica_diffusione ('Ethics and dissemination': approvazione, consenso, riservatezza, diffusione), "
        "abstract. Segui SPIRIT (protocollo di uno studio) o PRISMA-P (protocollo di una revisione).",
    ],
}
EXAMPLE = {"formato": SECTION_FORMAT, "progetto": "<nome>", "sezione": "metodi", "modello": "claude-…",
           "paragrafi": [{"titolo": "Participants", "testo": "Forty youth football players (age 16.1 ± 0.7 years) "
                          "took part [@art_…]."}],
           "derivati": [{"valore": "51", "formula": "66 / 130 * 100"}], "note": ""}


def build_packet(conn, home: Path, name: str, journal_list: list[dict], *, embedder=None, index=None,
                 now: datetime | None = None) -> tuple[dict | None, list[str]]:
    d = project_dir(home, name)
    if not d.is_dir():
        raise WritingError("progetto non trovato")
    study, p1 = read_study(d)
    kind = kind_of_study(study) or "originale"
    results, p2 = read_results(d) if kind != "protocollo" or (d / "risultati.csv").exists() else ([], [])
    if kind == "protocollo":
        p2 = [x for x in p2 if "nessun risultato" not in x]          # a protocol has no results yet
    included, p3 = read_included(d) if kind == "revisione" else ([], [])
    problems = p1 + p2 + p3
    if included:
        dois = [r["doi"] for r in included if r.get("doi")]
        study = {**study, "riferimenti_chiave": "; ".join(dict.fromkeys(
            [x.strip() for x in (study.get("riferimenti_chiave") or "").split(";") if x.strip()] + dois))}
    journals, unknown = compliance.pick_journals(conn, journal_list, [study.get("rivista")] if study.get("rivista") else [])
    if study.get("rivista") and (unknown or not journals):
        problems.append(f"rivista '{study.get('rivista')}' senza scheda in ORPHEUS")
    missing = missing_key_refs(conn, study)
    if missing:
        problems.append(f"{len(missing)} riferimenti chiave o studi inclusi non sono in ORPHEUS: aggiungi i PDF in inbox "
                        "e lancia lavora")
    if problems:
        return None, problems
    jr = compliance.load_rules(conn, journals[0], kind)
    evidence, mode = find_evidence(conn, study, embedder, index)
    packet = {
        "formato": PACKET_FORMAT, "versione": WRITING_VERSION, "progetto": name,
        "creato": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        "rivista": {"sigla": journals[0]["sigla"], "titolo": jr.title, "tipo": jr.type_name,
                    "scheda": jr.card_date[:10], "linee_guida": jr.guidelines_date, "regole": _rules(jr)},
        "tipo": kind, "tipo_testo": compliance.KINDS[kind], "tipo_assente_nella_rivista": jr.type_name is None,
        "ordine_sezioni": list(sections_of(kind)), "studi_inclusi": included,
        "studio": study, "risultati": results,
        "evidenze": evidence, "ricerca_evidenze": mode,
        "riferimenti_ammessi": [e["article_id"] for e in evidence],
        "sezioni": state(d)["sezioni"],
        "istruzioni": INSTRUCTIONS + KIND_INSTRUCTIONS.get(kind, []), "esempio": EXAMPLE,
        "impronte": _prints(d),
    }
    (d / "pacchetto.json").write_text(json.dumps(packet, ensure_ascii=False, indent=1), encoding="utf-8")
    return packet, []


def current_packet(conn, home: Path, name: str, journal_list: list[dict], matcher_factory=None) -> tuple[dict | None, list[str]]:
    """The packet stays the same while studio.csv and risultati.csv do not change (the allowed references of the
    sections already written must not move); otherwise it is built again."""
    d = project_dir(home, name)
    path = d / "pacchetto.json"
    if path.exists():
        try:
            packet = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            packet = None
        if packet and packet.get("formato") == PACKET_FORMAT and packet.get("impronte") == _prints(d):
            packet["sezioni"] = state(d)["sezioni"]
            return packet, []
    m = matcher_factory() if matcher_factory else None
    return build_packet(conn, home, name, journal_list, embedder=getattr(m, "embedder", None),
                        index=getattr(m, "index", None))


# ---------------------------------------------------------------------------------------------- sections
@dataclass
class Check:
    name: str
    ok: bool
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _paragraph_text(p: dict) -> str:
    return " ".join(str(p.get(k) or "") for k in ("titolo", "testo"))


def _evidence_numbers(packet: dict) -> dict[str, set[float]]:
    out = {}
    for e in packet.get("evidenze", []):
        vals = set()
        for c in e.get("affermazioni", []):
            for t in (c.get("affermazione"), c.get("citazione")):
                vals.update(value(n) for n in numbers_in(t or ""))
        for v in e.get("scheda", []):
            for t in (v.get("valore"), v.get("citazione")):
                vals.update(value(n) for n in numbers_in(t or ""))
        out[e["article_id"]] = vals
    return out


def _limit(rules: dict, name: str) -> float | None:
    """The upper limit of a journal rule (as compliance reads the card values)."""
    for r in rules.get(name, []):
        nums = numbers_in(r.get("valore") or "")
        lim = compliance.limits(compliance.Rule(name, None, r.get("valore"), value(nums[0]) if nums else None, None,
                                                r.get("pagina"), "reported"))
        if lim.hi is not None:
            return lim.hi
    return None


def check_section(packet: dict, data: dict) -> list[Check]:
    checks = []
    fmt = []
    if not isinstance(data, dict) or data.get("formato") != SECTION_FORMAT:
        fmt.append(f"'formato' deve essere {SECTION_FORMAT}")
        return [Check("formato", False, fmt)]
    if data.get("progetto") != packet.get("progetto"):
        fmt.append("'progetto' non è quello del pacchetto")
    kind = packet.get("tipo", "originale")
    if data.get("sezione") not in sections_of(kind):
        fmt.append(f"'sezione' deve essere una di: {', '.join(sections_of(kind))}")
    paras = data.get("paragrafi")
    if not isinstance(paras, list) or not paras or not all(isinstance(p, dict) and str(p.get("testo") or "").strip()
                                                          for p in paras):
        fmt.append("'paragrafi' deve essere una lista di paragrafi con 'testo'")
    if not data.get("modello"):
        fmt.append("manca 'modello'")
    checks.append(Check("formato", not fmt, fmt))
    if fmt:
        return checks
    section = data["sezione"]
    text_all = "\n".join(_paragraph_text(p) for p in paras)

    lang, _ = language_of(text_all)
    checks.append(Check("lingua", lang != "it", ["il testo sembra in italiano: l'articolo è in inglese (R11)"]
                        if lang == "it" else []))

    allowed_refs = set(packet.get("riferimenti_ammessi", []))
    rules = packet.get("rivista", {}).get("regole", {})
    max_group = None
    for r in rules.get("altri_elementi", []):
        m = re.search(r"al massimo (\d+) riferimenti per sostenere", r["valore"] or "")
        if m:
            max_group = int(m.group(1))
    cite_problems = []
    for i, p in enumerate(paras, 1):
        t = p.get("testo", "")
        if OTHER_CITES.search(t):
            cite_problems.append(f"paragrafo {i}: citazione in un altro stile: usa [@article_id]")
        for group in re.finditer(r"(?:\[@[^\]]+\]\s*)+", t):
            keys = [k.strip().lstrip("@") for m in CITE.finditer(group.group(0)) for k in re.split(r"[,;]", m.group(1))
                    if k.strip()]
            bad = [k for k in keys if k not in allowed_refs]
            if bad:
                cite_problems.append(f"paragrafo {i}: riferimenti non verificati o fuori dal pacchetto: {', '.join(bad)}")
            if max_group and len(keys) > max_group:
                cite_problems.append(f"paragrafo {i}: {len(keys)} riferimenti insieme, la rivista ne vuole al massimo "
                                     f"{max_group}")
    checks.append(Check("citazioni", not cite_problems, cite_problems))

    traced = traced_values(packet.get("studio", {}), packet.get("risultati", []) + packet.get("studi_inclusi", []))
    ev_numbers = _evidence_numbers(packet)
    derived, der_problems = set(), []
    for dv in data.get("derivati") or []:
        if not isinstance(dv, dict) or not dv.get("valore") or not dv.get("formula"):
            der_problems.append("ogni 'derivati' vuole 'valore' e 'formula'")
            continue
        got, problem = eval_formula(str(dv["formula"]), traced)
        if problem:
            der_problems.append(f"derivato {dv['valore']}: {problem}")
            continue
        nums = numbers_in(str(dv["valore"]))
        if not nums:
            der_problems.append(f"derivato {dv['valore']!r}: nessun numero")
            continue
        want = value(nums[0])
        if round(got, decimals(nums[0])) != want:
            der_problems.append(f"derivato {dv['valore']}: la formula dà {got:.4g}")
            continue
        derived.add(want)
    number_problems = list(der_problems)
    for i, p in enumerate(paras, 1):
        cited = {k.strip().lstrip("@") for m in CITE.finditer(p.get("testo", "")) for k in re.split(r"[,;]", m.group(1))}
        ok_here = traced | derived | set().union(*[ev_numbers.get(c, set()) for c in cited] or [set()])
        loose = [n for n in numbers_in(_paragraph_text(p)) if value(n) not in ok_here]
        if loose:
            number_problems.append(f"paragrafo {i}: valori non tracciati: {', '.join(dict.fromkeys(loose))} (vengono "
                                   "da studio.csv, risultati.csv, dagli articoli citati nel paragrafo o da 'derivati')")
    checks.append(Check("numeri", not number_problems, number_problems))

    words = sum(len(p.get("testo", "").split()) for p in paras)
    limit_problems, warnings = [], []
    if section == "abstract":
        lim = _limit(rules, "abstract_parole_max")
        if lim and words > lim:
            limit_problems.append(f"abstract di {words} parole, la rivista: al massimo {int(lim)}")
        struct = next((r["valore"] for r in rules.get("abstract_struttura", [])), "") or ""
        if struct.lower().startswith("strutturato") and ":" in struct:
            wanted = [w.strip(" .") for w in re.split(r"[,;]", struct.split(":", 1)[1].split(";")[0]) if w.strip(" .")]
            have = [str(p.get("titolo") or "").strip().rstrip(":") for p in paras]
            missing = [w for w in wanted if w.lower() not in (h.lower() for h in have)]
            if missing:
                limit_problems.append(f"titoletti dell'abstract mancanti: {', '.join(missing)}")
        if not data.get("titolo_articolo"):
            limit_problems.append("manca 'titolo_articolo'")
        if not data.get("parole_chiave"):
            limit_problems.append("mancano 'parole_chiave'")
    if section == "piano":
        budget = data.get("parole") or {}
        if not isinstance(budget, dict) or not budget:
            limit_problems.append("il piano vuole 'parole' (budget per sezione)")
        else:
            total = sum(int(v) for k, v in budget.items() if k in ("introduzione", "metodi", "risultati", "discussione",
                                                                  "etica_diffusione") and str(v).isdigit())
            lim = _limit(rules, "parole_testo_max")
            if lim and total > lim:
                limit_problems.append(f"budget del testo {total} parole, la rivista: al massimo {int(lim)}")
        if not data.get("messaggi_chiave"):
            limit_problems.append("il piano vuole 'messaggi_chiave'")
    checks.append(Check("regole_rivista", not limit_problems, limit_problems, warnings))
    if kind == "protocollo" and section != "piano":
        found = [i for i, p in enumerate(paras, 1) if re.search(r"\bp\s*[<=>≤]\s*0?\.\d|\b95\s*%\s*CI\b|\bwere "
                                                                  r"(?:significantly|found)\b", _paragraph_text(p), re.I)]
        checks.append(Check("protocollo", not found, [f"paragrafo {i}: un protocollo non ha risultati (niente p, IC, "
                                                      "'were found')" for i in found]))
    return checks


def verdict(checks: list[Check]) -> str:
    return "accettabile" if all(c.ok for c in checks) else "da correggere"


# ---------------------------------------------------------------------------------------------- state and flow
def _approvals(d: Path) -> dict:
    p = d / "approvazioni.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def state(d: Path) -> dict:
    approvals = _approvals(d)
    out = {}
    order = sections_of(kind_of(d))
    for s in order:
        acc = d / "sezioni" / "accettate" / f"{s}.json"
        sent = d / "sezioni" / "da_inviare" / f"{s}.json"
        if acc.exists() and approvals.get(s, {}).get("sha256") == _sha(acc):
            st = "approvata"
        elif acc.exists():
            st = "da approvare"
        elif sent.exists():
            st = "da controllare"
        else:
            st = "da scrivere"
        out[s] = st
    nxt = next((s for s in order if out[s] != "approvata"), None)
    return {"sezioni": out, "prossima": nxt}


def submit(d: Path, packet: dict, now: datetime | None = None) -> list[dict]:
    """Check every section in da_inviare: accepted ones go to accettate, refused ones get <sezione>.motivi.txt."""
    results = []
    st = state(d)["sezioni"]
    for path in sorted((d / "sezioni" / "da_inviare").glob("*.json")):
        name = path.stem
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            checks = [Check("formato", False, [f"JSON non leggibile: {exc}"])]
            data = {}
        else:
            checks = check_section(packet, data)
        order = sections_of(packet.get("tipo", "originale"))
        before = list(order[:order.index(name)]) if name in order else []
        not_approved = [s for s in before if st.get(s) != "approvata"]
        if not_approved:
            checks.append(Check("ordine", False, [f"prima vanno approvate: {', '.join(not_approved)}"]))
        v = verdict(checks)
        motivi = path.with_suffix(".motivi.txt")
        if v == "accettabile":
            shutil.move(str(path), str(d / "sezioni" / "accettate" / path.name))
            if motivi.exists():
                motivi.unlink()
        else:
            lines = [f"{c.name}: {p}" for c in checks for p in c.problems]
            motivi.write_text("\n".join(lines) + "\n", encoding="utf-8")
        results.append({"sezione": name, "esito": v, "problemi": sum(len(c.problems) for c in checks)})
    write_preview(d, packet)
    return results


def approve(d: Path, section: str, now: datetime | None = None) -> dict:
    acc = d / "sezioni" / "accettate" / f"{section}.json"
    if section not in sections_of(kind_of(d)) or not acc.exists():
        raise WritingError(f"la sezione '{section}' non è tra quelle accettate")
    approvals = _approvals(d)
    approvals[section] = {"sha256": _sha(acc), "quando": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")}
    (d / "approvazioni.json").write_text(json.dumps(approvals, ensure_ascii=False, indent=1), encoding="utf-8")
    return state(d)


def _reference(e: dict) -> str:
    au = e.get("autori") or []
    names = ", ".join(au[:3]) + (", et al." if len(au) > 3 else "") if au else "Anonymous"
    return f"{names}. {e.get('titolo')}. {e.get('rivista') or ''} {e.get('anno') or ''}. doi:{e.get('doi')}"


def write_preview(d: Path, packet: dict) -> Path:
    """anteprima.html: the accepted sections in order, citations as numbers in order of first citation."""
    ev = {e["article_id"]: e for e in packet.get("evidenze", [])}
    st = state(d)["sezioni"]
    order: list[str] = []
    parts = []
    for s in sections_of(packet.get("tipo", "originale")):
        acc = d / "sezioni" / "accettate" / f"{s}.json"
        if not acc.exists():
            continue
        data = json.loads(acc.read_text(encoding="utf-8"))
        title = "Methods and analysis" if s == "metodi" and packet.get("tipo") == "protocollo" else SECTION_TITLES[s]
        parts.append(f"<h2>{html.escape(title)} <span class='b {'ok' if st[s] == 'approvata' else 'g'}'>"
                     f"{html.escape(st[s])}</span></h2>")
        if s == "abstract" and data.get("titolo_articolo"):
            parts.append(f"<p class='t'>{html.escape(data['titolo_articolo'])}</p>")
        for p in data.get("paragrafi", []):
            def number(m):
                keys = [k.strip().lstrip("@") for k in re.split(r"[,;]", m.group(1)) if k.strip()]
                nums = []
                for k in keys:
                    if k not in order:
                        order.append(k)
                    nums.append(str(order.index(k) + 1))
                return "\x00" + ",".join(nums) + "\x01"
            body = html.escape(CITE.sub(number, p.get("testo", ""))).replace("\x00", "<sup>").replace("\x01", "</sup>")
            if p.get("titolo"):
                parts.append(f"<h3>{html.escape(str(p['titolo']))}</h3>")
            parts.append(f"<p>{body}</p>")
        if s == "abstract" and data.get("parole_chiave"):
            kw = data["parole_chiave"]
            parts.append(f"<p><b>Keywords:</b> {html.escape('; '.join(kw) if isinstance(kw, list) else str(kw))}</p>")
    refs = "".join(f"<li>{html.escape(_reference(ev[k]))}</li>" for k in order if k in ev)
    page = ("<!doctype html><html lang='en'><head><meta charset='utf-8'><title>Anteprima</title><style>"
            "body{font-family:Georgia,serif;max-width:820px;margin:30px auto;padding:0 16px;line-height:1.55;color:#222}"
            "h2{font-family:Segoe UI,Arial,sans-serif;font-size:1.15rem;margin-top:30px}.b{font-size:.75rem;padding:1px 8px;"
            "border-radius:9px;margin-left:6px}.ok{background:#dcefe0}.g{background:#fdf1cf}.t{font-weight:bold}"
            "ol{font-size:.9rem}</style></head><body>"
            f"<p style='font-family:Segoe UI,Arial;color:#666'>Anteprima del nuovo articolo per {html.escape(packet.get('rivista', {}).get('titolo') or '')}"
            " (resta su questo PC)</p>" + "".join(parts) + (f"<h2>References</h2><ol>{refs}</ol>" if refs else "")
            + "</body></html>")
    path = d / "anteprima.html"
    path.write_text(page, encoding="utf-8")
    return path


def public_state(d: Path, packet: dict | None) -> dict:
    """For the log: states and counts, no text."""
    st = state(d)
    out = {"sezioni": st["sezioni"], "prossima": st["prossima"]}
    if packet:
        out |= {"rivista": packet["rivista"]["sigla"], "tipo": packet.get("tipo", "originale"),
                "tipo_assente_nella_rivista": packet.get("tipo_assente_nella_rivista", False),
                "studi_inclusi": len(packet.get("studi_inclusi", [])), "evidenze": len(packet["evidenze"]),
                "ricerca_evidenze": packet["ricerca_evidenze"], "risultati": len(packet["risultati"])}
    return out
