"""EURYDIKE E3 (T-051d): a new review from the form of the dashboard, and the import of the search files.

User's decisions of 2026-10-23: the review is created from a form in the dashboard ("Nuova revisione"), with PICOS,
registration, search strategy and default risk-of-bias / quality tools; its folder is
`ORPHEUS_DATI\\revisioni\\<nome>\\` with `protocollo.yaml` (a copy of what is in the database, for the user) and
`ricerca\\`, where the user puts the exported files (RIS, PubMed .nbib, Web of Science, CSV/TSV). The import reads the
files of `ricerca\\` not imported yet (same SHA-256 = same file) with `core.records` and stores them with
`review.import_records`: every record is kept, duplicates point to the first record.

Writes go through `review.py` (one transaction each, audit events). The folder and the YAML file are written only
after the review is in the database."""
from __future__ import annotations

import hashlib
import re
import sqlite3
import unicodedata
from pathlib import Path

import yaml

from .. import audit
from . import review as rv
from .core import records as core_records

FOLDER = "revisioni"
SEARCH = "ricerca"
PROTOCOL_FILE = "protocollo.yaml"
EXTENSIONS = (".ris", ".nbib", ".txt", ".csv", ".tsv")
PICO_KEYS = ("P", "I", "C", "O", "S")
DATABASES = (("pubmed", "PubMed"), ("medline", "MEDLINE"), ("scopus", "Scopus"), ("embase", "Embase"),
             ("sportdiscus", "SPORTDiscus"), ("cinahl", "CINAHL"), ("cochrane", "Cochrane Library"),
             ("wos", "Web of Science"), ("webofscience", "Web of Science"), ("web_of_science", "Web of Science"),
             ("psycinfo", "PsycINFO"), ("ebsco", "EBSCO"), ("scholar", "Google Scholar"))
FORMAT_DATABASE = {"nbib": "PubMed", "wos": "Web of Science"}


def slug(title: str) -> str:
    """Folder name from the title: lower case ASCII, words joined by '_', at most 60 characters."""
    t = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode().lower()
    t = re.sub(r"[^a-z0-9]+", "_", t).strip("_")
    return (t[:60].rstrip("_")) or "revisione"


def _lines(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = value.splitlines()
    return [str(x).strip().lstrip("-•* ").strip() for x in value if str(x).strip()]


def review_dir(home: Path, data_dir: str) -> Path:
    return Path(home) / Path(data_dir.replace("\\", "/"))


def new_review(conn: sqlite3.Connection, home: Path, form: dict, *, actor: str = "user") -> dict:
    """Create the review from the form: review, reviewers (you and Claude, declared), criteria; then the folder with
    `ricerca\\` and `protocollo.yaml`. Returns {"id", "cartella", "criteri"}."""
    title = str(form.get("titolo") or "").strip()
    question = str(form.get("domanda") or "").strip()
    inc, exc = _lines(form.get("inclusione")), _lines(form.get("esclusione"))
    if not title or not question:
        raise rv.ReviewError("scrivi titolo e domanda della revisione")
    if not inc or not exc:
        raise rv.ReviewError("scrivi almeno un criterio di inclusione e uno di esclusione (uno per riga)")
    me = str(form.get("revisore") or "").strip() or "Tu"
    if me.lower() == "claude":
        raise rv.ReviewError("il tuo nome non può essere 'Claude'")
    pico = {k: str(form.get(k) or form.get(k.lower()) or "").strip() for k in PICO_KEYS}
    pico = {k: v for k, v in pico.items() if v}
    base = slug(title)
    root = Path(home) / FOLDER
    name, n = base, 2
    while (root / name).exists() or conn.execute("SELECT 1 FROM review WHERE data_dir = ?",
                                                 (f"{FOLDER}/{name}",)).fetchone():
        name, n = f"{base}_{n}", n + 1
    data_dir = f"{FOLDER}/{name}"
    kind = form.get("tipo") or "systematic_review"
    try:
        need = int(form.get("revisori_screening") or 2)
    except ValueError as exc_:
        raise rv.ReviewError("i revisori allo screening sono un numero") from exc_
    rid = rv.create_review(conn, title=title, question=question, data_dir=data_dir, kind=kind, pico=pico,
                           registration=(str(form.get("registrazione") or "").strip() or None), screen_reviewers=need,
                           search_strategy=(str(form.get("strategia") or "").strip() or None),
                           rob_tool=form.get("rob") or None, qual_tool=form.get("qualita") or None, actor=actor)
    rv.add_reviewer(conn, rid, kind="human", label=me, actor=actor)
    rv.add_reviewer(conn, rid, kind="claude_session", label="Claude", model_ref="claude (sessione)", actor=actor)
    for text in inc:
        rv.add_criterion(conn, rid, kind="inclusion", text=text, actor=actor)
    for text in exc:
        rv.add_criterion(conn, rid, kind="exclusion", text=text, actor=actor)
    folder = review_dir(home, data_dir)
    (folder / SEARCH).mkdir(parents=True, exist_ok=True)
    write_protocol(conn, home, rid)
    return {"id": rid, "cartella": str(folder), "criteri": {"inclusione": len(inc), "esclusione": len(exc)}}


FORM_FIELDS = {"titolo": "title", "domanda": "question", "tipo": "kind", "registrazione": "registration",
               "strategia": "search_strategy", "revisori_screening": "screen_reviewers", "cieco": "blind_reviewers",
               "cieco_ai": "blind_ai", "rob": "rob_tool_default", "qualita": "qual_tool_default", "fase": "status"}


def edit_review(conn: sqlite3.Connection, home: Path, review_id: str, form: dict, *, actor: str = "user") -> dict:
    """The pencil of the dashboard (2026-11-03): change the parameters given in `form` (names of the creation form,
    PICOS as P, I, C, O, S), then rewrite `protocollo.yaml`. The folder of the review keeps its name."""
    changes = {FORM_FIELDS[k]: v for k, v in form.items() if k in FORM_FIELDS}
    if any(k in form for k in PICO_KEYS):
        changes["pico"] = {k: form.get(k) or "" for k in PICO_KEYS}
    out = rv.update_review(conn, review_id, changes, actor=actor)
    write_protocol(conn, home, review_id)
    return out


def edit_criterion(conn: sqlite3.Connection, home: Path, review_id: str, form: dict, *, actor: str = "user") -> dict:
    """Criteria from the dashboard: azione "aggiungi" (tipo, testo), "modifica" (id, testo: a new criterion replaces
    the old one, same number) or "ritira" (id). The old one stays in the history."""
    action = form.get("azione")
    kinds = {"inclusione": "inclusion", "esclusione": "exclusion", "inclusion": "inclusion", "exclusion": "exclusion"}
    if action == "aggiungi":
        kind = kinds.get(str(form.get("tipo") or ""))
        if not kind:
            raise rv.ReviewError("tipo di criterio: inclusione o esclusione")
        cid = rv.add_criterion(conn, review_id, kind=kind, text=str(form.get("testo") or ""), actor=actor)
    elif action == "modifica":
        old = conn.execute("SELECT kind FROM review_criterion WHERE id = ? AND review_id = ?",
                           (str(form.get("id") or ""), review_id)).fetchone()
        if old is None:
            raise rv.ReviewError("criterio non trovato")
        cid = rv.add_criterion(conn, review_id, kind=old[0], text=str(form.get("testo") or ""),
                               replaces=str(form["id"]), actor=actor)
    elif action == "ritira":
        rv.withdraw_criterion(conn, review_id, str(form.get("id") or ""), actor=actor)
        cid = None
    else:
        raise rv.ReviewError("azione non ammessa: aggiungi, modifica o ritira")
    write_protocol(conn, home, review_id)
    return {"id": cid}


def write_protocol(conn: sqlite3.Connection, home: Path, review_id: str) -> Path:
    """`protocollo.yaml` in the folder of the review: what the database holds now (current criteria)."""
    r = conn.execute("SELECT * FROM review WHERE id = ?", (review_id,)).fetchone()
    if r is None:
        raise rv.ReviewError("revisione non trovata")
    import json
    crit = rv.current_criteria(conn, review_id)
    doc = {
        "id": r["id"], "titolo": r["title"], "domanda": r["question"], "tipo": r["kind"],
        "pico": json.loads(r["pico"] or "{}"), "registrazione": r["registration"], "strategia": r["search_strategy"],
        "revisori_screening": r["screen_reviewers"], "rischio_di_bias": r["rob_tool_default"],
        "qualita": r["qual_tool_default"],
        "criteri_inclusione": [c["text"] for c in crit if c["kind"] == "inclusion"],
        "criteri_esclusione": [c["text"] for c in crit if c["kind"] == "exclusion"],
        "revisori": [{"label": x["label"], "tipo": x["kind"]} for x in conn.execute(
            "SELECT label, kind FROM review_reviewer WHERE review_id = ? ORDER BY created_at, rowid", (review_id,))],
        "creata": r["created_at"],
    }
    path = review_dir(home, r["data_dir"]) / PROTOCOL_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    head = ("# Protocollo della revisione (copia di quanto è nel database di ORPHEUS).\n"
            "# Le modifiche si fanno dal cruscotto: questo file non viene riletto.\n")
    path.write_text(head + yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=100), encoding="utf-8")
    return path


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def database_name(filename: str, fmt: str) -> str:
    low = re.sub(r"[\s\-]+", "_", filename.lower())
    for key, name in DATABASES:
        if key in low:
            return name
    return FORMAT_DATABASE.get(fmt, fmt.upper())


def default_search_dir(conn: sqlite3.Connection, home: Path, review_id: str) -> Path:
    r = conn.execute("SELECT data_dir FROM review WHERE id = ?", (review_id,)).fetchone()
    if r is None:
        raise rv.ReviewError("revisione non trovata")
    return review_dir(home, r["data_dir"]) / SEARCH


def search_files(conn: sqlite3.Connection, home: Path, review_id: str, folder: Path | None = None) -> dict:
    """The files of the search folder with what the import would do: format, records, database, already imported or
    not. `folder`: the folder chosen by the user for this review on this PC (2026-11-03), else `ricerca\\`."""
    default = default_search_dir(conn, home, review_id)
    folder = Path(folder) if folder else default
    done = {x["file_sha256"]: dict(x) for x in conn.execute(
        "SELECT file_sha256, source_name, database_name, format, imported_at, raw_count, dupes_count FROM search_import "
        "WHERE review_id = ?",
        (review_id,))}
    out = []
    if folder.is_dir():
        for f in sorted(folder.iterdir(), key=lambda x: x.name.lower()):
            if not f.is_file() or f.name.startswith((".", "~$")) or f.suffix.lower() not in EXTENSIONS:
                continue
            sha = hashlib.sha256(f.read_bytes()).hexdigest()
            item = {"file": f.name, "sha256": sha, "byte": f.stat().st_size}
            if sha in done:
                d = done[sha]
                item.update(stato="importato", importato_il=d["imported_at"], record=d["raw_count"],
                            doppioni=d["dupes_count"], banca_dati=d["database_name"], formato=d["format"])
            else:
                try:
                    fmt, recs = core_records.parse_records(_read_text(f), f.name)
                    if not any((x.get("title") or "").strip() for x in recs):
                        item.update(stato="vuoto", formato=fmt, record=0,
                                    errore="nessun record con titolo: controlla il file esportato")
                        out.append(item)
                        continue
                    item.update(stato="nuovo", formato=fmt, record=len(recs), banca_dati=database_name(f.name, fmt),
                                senza_titolo=sum(1 for x in recs if not (x.get("title") or "").strip()))
                except ValueError as exc:
                    item.update(stato="non leggibile", errore=str(exc))
            out.append(item)
    return {"cartella": str(folder), "predefinita": str(default), "scelta": folder != default, "file": out}


def import_new(conn: sqlite3.Connection, home: Path, review_id: str, *, folder: Path | None = None,
               actor: str = "user") -> dict:
    """Import every new readable file of the search folder; one transaction per file. Returns the report of every file."""
    listing = search_files(conn, home, review_id, folder)
    folder = Path(listing["cartella"])
    done = []
    for item in listing["file"]:
        if item["stato"] != "nuovo":
            continue
        path = folder / item["file"]
        fmt, recs = core_records.parse_records(_read_text(path), path.name)
        if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise rv.ReviewError(f"il file {path.name} è cambiato durante l'importazione: riprova")
        out = rv.import_records(conn, review_id, recs, source_name=path.name, database_name=item["banca_dati"],
                                fmt=fmt, file_sha256=item["sha256"], actor=actor)
        done.append({"file": path.name, "banca_dati": item["banca_dati"], "formato": fmt, **out})
    if done and conn.execute("SELECT status FROM review WHERE id = ?", (review_id,)).fetchone()[0] == "protocol":
        with rv._write(conn):
            conn.execute("UPDATE review SET status = 'screening' WHERE id = ?", (review_id,))
            audit.record(conn, actor=actor, action="review_status_changed", entity_type="review", entity_id=review_id,
                         payload={"from": "protocol", "to": "screening"}, in_transaction=True)
    return {"importati": done, "saltati": [x for x in listing["file"] if x["stato"] != "nuovo"]}
