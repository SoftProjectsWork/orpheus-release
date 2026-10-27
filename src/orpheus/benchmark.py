"""Human benchmark file (T-012): records to check by hand against the PDFs (gate of phase 1).

`orpheus benchmark-csv` writes ORPHEUS_HOME/rapporti/benchmark_articoli_completi_<n>_<date>.csv (Excel in Italian:
UTF-8 with BOM, ";" separator, CRLF). For each record: where to look in the PDF (folder relative to the data
folder, Windows style, the same on every PC), the data ORPHEUS holds from Crossref, the data read from the PDF,
and empty columns the user fills in (S/N) — titolo_ok, autori_ok, anno_ok, rivista_ok, doi_ok, note. The user
saves the filled file with "_compilato" added to the name.

Only COMPLETE articles (content.py; user's decision of 2026-10-10): conference abstracts and single pages are
left out and counted. Read-only on the database. Deterministic: the same database gives the same file, byte
for byte.

Sample (seed SEED, fixed):
  1. every complete article whose last DOI verification is not "confermato" (non trovato / non corrisponde);
  2. then confirmed complete articles, ordered by sha256("<SEED>:<document id>"), the first ones: the same order
     on every PC and with every Python version (random.sample may change between versions).
Documents never verified have no Crossref record to judge: they are left out and counted.
Rows: non confermati, then confermati; inside each group by document id.

`orpheus benchmark-risultati [FILE]` (score, check_sample) reads the filled file and measures the precision of
each field (S/(S+N)) per group, plus the share of records entirely right. Read-only on the file and on the
database. Excel may save with ";" or ",", with or without BOM: all are accepted; empty cells count as "not filled".
"""

from __future__ import annotations

import codecs
import csv
import hashlib
import io
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath

from . import layout
from .content import COMPLETE, classify_all
from .quality import REPORTS_DIR
from .verify_doi import OUTCOME_IT, _YEAR_IN_NAME, candidates, latest_checks

SIZE = 50
SEED = "orpheus-T-012-v2"
FILE_PREFIX = "benchmark_articoli_completi"
GROUPS = ("non confermato", "articolo completo")
FILL_COLUMNS = ["titolo_ok", "autori_ok", "anno_ok", "rivista_ok", "doi_ok", "note"]
COLUMNS = ["n", "gruppo", "id_documento", "file", "cartella", "pagina",
           "crossref_titolo", "crossref_autori", "crossref_anno", "crossref_rivista", "crossref_doi",
           "pdf_titolo", "pdf_autori", "pdf_anno_nome_file", "pdf_doi", "pdf_doi_dove", "pdf_rivista",
           *FILL_COLUMNS]


def pick(ids: list[str], k: int, seed: str = SEED) -> list[str]:
    """The first k ids in the fixed pseudo-random order given by the seed, returned sorted by id."""
    ranked = sorted(ids, key=lambda i: (hashlib.sha256(f"{seed}:{i}".encode()).hexdigest(), i))
    return sorted(ranked[:k])


@dataclass
class Sample:
    rows: list[dict] = field(default_factory=list)
    population: dict[str, int] = field(default_factory=dict)
    chosen: dict[str, int] = field(default_factory=dict)
    not_verified: int = 0
    not_complete: int = 0


def _clean(text) -> str:
    return re.sub(r"\s+", " ", str(text)).strip() if text is not None else ""


def _row(conn: sqlite3.Connection, doc: sqlite3.Row, check: sqlite3.Row, group: str, locator=None, home=None) -> dict:
    details = json.loads(check["details"] or "{}")
    cr = details.get("crossref") or {}
    checks = details.get("controlli") or {}
    art = conn.execute("SELECT title, publication_year, doi FROM article WHERE id = ?",
                       (check["article_id"],)).fetchone() if check["article_id"] else None
    authors = [a for a in cr.get("autori") or [] if a]
    total = (checks.get("autori") or {}).get("totale") or len(authors)
    authors_txt = "; ".join(_clean(a) for a in authors)
    if total > len(authors):
        authors_txt += f"; … ({total} in tutto)"
    year = art["publication_year"] if art and art["publication_year"] else (cr.get("years") or [""])[0]
    where = PurePosixPath(doc["original_path"].replace("\\", "/"))
    if locator is not None:                       # T-054: where the PDF is now (moved by the user or by ORPHEUS)
        now = locator.find(doc["original_path"], doc["file_sha256"])
        if now is not None:
            where = PurePosixPath(layout.shown_path(locator.home, now))
    name = where.name
    m = _YEAR_IN_NAME.search(name)
    return {
        "gruppo": f"{group} ({OUTCOME_IT[check['outcome']]})" if group == GROUPS[0] else group,
        "id_documento": doc["id"], "file": name,
        "cartella": "\\".join(where.parent.parts) if str(where.parent) != "." else "",
        "pagina": (checks.get("titolo") or {}).get("pagina") or "",
        "crossref_titolo": _clean(art["title"] if art else cr.get("title")),
        "crossref_autori": authors_txt, "crossref_anno": year,
        "crossref_rivista": _clean((cr.get("container_title") or [""])[0]),
        "crossref_doi": (art["doi"] if art else cr.get("doi")) or "",
        "pdf_titolo": _clean(doc["pdf_title"]), "pdf_autori": _clean(doc["pdf_author"]),
        "pdf_anno_nome_file": m.group(1) if m else "", "pdf_doi": doc["doi_candidate"],
        "pdf_doi_dove": details.get("doi_nel_pdf", ""),
        "pdf_rivista": _clean((checks.get("rivista") or {}).get("prova")),
        **{c: "" for c in FILL_COLUMNS},
    }


def build(conn: sqlite3.Connection, size: int = SIZE, seed: str = SEED, locator=None) -> Sample:
    latest = latest_checks(conn)
    classes = classify_all(conn)
    by_group: dict[str, dict[str, sqlite3.Row]] = {g: {} for g in GROUPS}
    out = Sample()
    for doc in candidates(conn):
        if (classes.get(doc["id"]) or {}).get("classe") != COMPLETE:
            out.not_complete += 1
            continue
        check = latest.get(doc["id"])
        if check is None or check["doi"] != doc["doi_candidate"]:
            out.not_verified += 1
            continue
        by_group[GROUPS[0] if check["outcome"] != "confirmed" else GROUPS[1]][doc["id"]] = doc
    out.population = {g: len(v) for g, v in by_group.items()}
    first = min(size, out.population[GROUPS[0]])
    out.chosen = {GROUPS[0]: first, GROUPS[1]: min(size - first, out.population[GROUPS[1]])}
    for g in GROUPS:
        for doc_id in pick(list(by_group[g]), out.chosen[g], seed):
            out.rows.append(_row(conn, by_group[g][doc_id], latest[doc_id], g, locator))
    for n, r in enumerate(out.rows, 1):
        r["n"] = n
    return out


def render(sample: Sample) -> bytes:
    """Semicolon + UTF-8 BOM + CRLF: what Excel in Italian opens correctly with a double click."""
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=COLUMNS, delimiter=";", lineterminator="\r\n")
    writer.writeheader()
    for r in sample.rows:
        writer.writerow(r)
    return buf.getvalue().encode("utf-8-sig")


def write(sample: Sample, home: Path, now: datetime | None = None) -> tuple[Path, bool]:
    """Write the file; never overwrite one that exists (the user may be filling it in).
    Returns (path, written): written=False when an identical file for the same day is already there."""
    now = now or datetime.now()
    folder = Path(home) / REPORTS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    data = render(sample)
    path = folder / f"{FILE_PREFIX}_{len(sample.rows)}_{now.strftime('%Y-%m-%d')}.csv"
    if path.exists():
        if path.read_bytes() == data:
            return path, False
        path = path.with_name(f"{path.stem}_{now.strftime('%H%M%S')}.csv")
        if path.exists():
            raise FileExistsError(f"esiste già {path}: riprova tra un secondo")
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    return path, True


# ---------------------------------------------------------------- results of the filled file (T-012 part 2)

FIELDS = {"titolo": "titolo_ok", "autori": "autori_ok", "anno": "anno_ok", "rivista": "rivista_ok", "doi": "doi_ok"}
CHECKED_COLUMNS = ["crossref_titolo", "crossref_autori", "crossref_anno", "crossref_rivista", "crossref_doi"]
SCORE_GROUPS = ("non confermati", "confermati")
YES = {"s", "si", "sì", "sí"}
NO = {"n", "no"}
_ORIGINAL_NAME = re.compile(rf"^{FILE_PREFIX}_(\d+)_\d{{4}}-\d{{2}}-\d{{2}}(_\d{{6}})?$")
_SIZE_IN_NAME = re.compile(rf"^{FILE_PREFIX}_(\d+)_")


class FilledFileError(ValueError):
    """The file cannot be read as a filled benchmark file."""


def latest_filled(home: Path) -> Path | None:
    """The newest benchmark file saved by the user under another name (…_compilato.csv, …_completato.csv, …):
    any file of the benchmark whose name is not exactly the one written by benchmark-csv."""
    folder = Path(home) / REPORTS_DIR
    if not folder.is_dir():
        return None
    found = [p for p in folder.glob(f"{FILE_PREFIX}_*.csv") if not _ORIGINAL_NAME.match(p.stem)]
    return max(found, key=lambda p: (p.stat().st_mtime, p.name)) if found else None


def _verdict(cell) -> str:
    """'S', 'N', '' (not filled) or '?' (not valid)."""
    v = (cell or "").strip().rstrip(".").strip().lower()
    if not v:
        return ""
    return "S" if v in YES else "N" if v in NO else "?"


def _group(cell) -> str:
    g = (cell or "").strip().lower()
    if g.startswith(GROUPS[0]):
        return SCORE_GROUPS[0]
    if g.startswith(GROUPS[1]):
        return SCORE_GROUPS[1]
    return "senza gruppo"


_EXCEL_RISKY = re.compile(r"^[\s\d.,:/+\-\u2013]*\d[\s\d.,:/+\-\u2013]*$")


def excel_text(value) -> str:
    """A value that Excel (Italian settings) would turn into a number or a date when it opens the CSV — "5,000" -> 5,
    "1.35" -> 135, "3-8" -> 3 August — written as a text formula ="5,000": Excel shows 5,000 and saves it as 5,000.
    Other values are left as they are. Only id, ord and giusto are read back, so this never changes a score."""
    v = "" if value is None else str(value)
    return '="' + v.replace('"', '""') + '"' if _EXCEL_RISKY.match(v) else v


def excel_plain(value) -> str:
    """The value without the ="…" wrapper (when the file was not saved by Excel)."""
    v = "" if value is None else str(value)
    if v.startswith('="') and v.endswith('"') and len(v) >= 3:
        return v[2:-1].replace('""', '"')
    return v


def read_filled(path: Path, need: set[str] | None = None) -> tuple[list[dict], dict]:
    """Rows of the filled file (header names normalised) and how it was saved. `need`: the columns that must be in
    the header (default: the benchmark's); also used for the hand check of the article cards (card_store)."""
    raw = Path(path).read_bytes()
    bom = raw.startswith(codecs.BOM_UTF8)
    try:
        text, encoding = raw.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:                       # Excel "CSV (delimitato)" saves in the Windows code page
        text, encoding = raw.decode("cp1252"), "cp1252"
    lines = text.splitlines()
    if not lines:
        raise FilledFileError(f"il file è vuoto: {path}")
    need = need or {"id_documento", *FIELDS.values()}
    delimiter = None
    for d in (";", ",", "\t"):
        names = {c.strip().lower() for c in next(csv.reader([lines[0]], delimiter=d))}
        if need <= names:
            delimiter = d
            break
    if delimiter is None:
        raise FilledFileError("intestazione non riconosciuta: servono le colonne " + ", ".join(sorted(need))
                              + f" (file {path})")
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    header = [c.strip().lower() for c in next(reader)]
    rows = []
    for cells in reader:
        if not any(c.strip() for c in cells):        # empty lines Excel may leave at the end
            continue
        rows.append({h: (cells[i] if i < len(cells) else "") for i, h in enumerate(header) if h})
    info = {"separatore": {";": ";", ",": ",", "\t": "tab"}[delimiter], "bom": bom, "codifica": encoding}
    return rows, info


def _counts(verdicts: list[str]) -> dict:
    s, n = verdicts.count("S"), verdicts.count("N")
    return {"giusti": s, "sbagliati": n, "non_compilati": verdicts.count(""), "non_validi": verdicts.count("?"),
            "precisione": round(s / (s + n), 4) if s + n else None}


def score(path: Path) -> dict:
    """Precision of each field and of whole records, per group, from the file filled in by hand. Read-only."""
    rows, info = read_filled(path)
    groups = [_group(r.get("gruppo")) for r in rows]
    names = list(dict.fromkeys([*SCORE_GROUPS, *groups]))
    per_field: dict = {}
    for name, col in FIELDS.items():
        verdicts = [_verdict(r.get(col)) for r in rows]
        per_field[name] = {"tutti": _counts(verdicts),
                           **{g: _counts([v for v, gg in zip(verdicts, groups) if gg == g]) for g in names}}
    whole: dict = {}
    for g in ["tutti", *names]:
        sel = [r for r, gg in zip(rows, groups) if g == "tutti" or gg == g]
        marks = [[_verdict(r.get(c)) for c in FIELDS.values()] for r in sel]
        done = [m for m in marks if all(v in ("S", "N") for v in m)]
        right = sum(1 for m in done if all(v == "S" for v in m))
        whole[g] = {"record": len(sel), "interamente_giusti": right, "con_almeno_una_N": len(done) - right,
                    "non_compilati_del_tutto": len(sel) - len(done),
                    "precisione": round(right / len(done), 4) if done else None}
    with_n, invalid, notes = [], [], []
    for r in rows:
        marks = {name: _verdict(r.get(col)) for name, col in FIELDS.items()}
        base = {"n": r.get("n", ""), "id_documento": r.get("id_documento", "").strip(), "file": r.get("file", "")}
        if "N" in marks.values():
            with_n.append({**base, "gruppo": r.get("gruppo", ""),
                           "campi_sbagliati": [k for k, v in marks.items() if v == "N"], "note": r.get("note", "").strip()})
        invalid += [{**base, "campo": FIELDS[k], "valore": r.get(FIELDS[k])} for k, v in marks.items() if v == "?"]
        if (r.get("note") or "").strip():
            notes.append({**base, "note": r["note"].strip()})
    ids = [r.get("id_documento", "").strip() for r in rows]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    return {"file": str(path), **info, "righe": len(rows),
            "per_gruppo": {g: groups.count(g) for g in names},
            "precisione_per_campo": per_field, "record_interamente_giusti": whole,
            "righe_con_N": with_n, "valori_non_validi": invalid, "note": notes, "id_ripetuti": dup}


def check_sample(conn: sqlite3.Connection, path: Path, seed: str = SEED) -> dict:
    """Are the documents of the file the sample this database gives today? Are the Crossref data the same
    as the ones the user judged? Read-only."""
    file_rows, _ = read_filled(path)
    rows_ids = [(r.get("id_documento") or "").strip() for r in file_rows]
    m = _SIZE_IN_NAME.match(Path(path).stem)
    size = int(m.group(1)) if m else len(rows_ids)
    sample = build(conn, size=size, seed=seed)
    expected = {r["id_documento"]: r for r in sample.rows}
    known = {r[0] for r in conn.execute("SELECT id FROM document")}
    out = {"campione_atteso": len(expected), "stesso_campione": set(rows_ids) == set(expected),
           "mancanti_nel_file": sorted(set(expected) - set(rows_ids)),
           "non_nel_campione_di_oggi": sorted(set(rows_ids) - set(expected)),
           "non_nel_database": sorted(i for i in rows_ids if i not in known), "dati_crossref_diversi": []}
    for r in file_rows:
        e = expected.get((r.get("id_documento") or "").strip())
        if e:
            diff = [c for c in CHECKED_COLUMNS if _clean(r.get(c)) != _clean(e[c])]
            if diff:
                out["dati_crossref_diversi"].append({"n": r.get("n", ""), "id_documento": e["id_documento"], "colonne": diff})
    return out
