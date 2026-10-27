"""T-054 — journals recognised from the PDFs, journal folders, articles moved to their journal (rules journal-folders-v1).

User's request of 2026-10-27 10:45 and decisions of the same day:
  - "Automatica, con riepilogo": a confirmed article (DOI confirmed by Crossref) of a journal that is not in
    riviste_prioritarie.csv creates a new row: title, publisher and ISSNs from Crossref, site from OpenAlex (when it
    answers), sigla proposed from the initials of the title (changeable later in the dashboard);
  - "Sposta subito, con registro": the PDFs of articoli_vari whose article has a journal go to
    riviste/<sigla>/articoli; every move is in a registro (rapporti/spostamenti/*.json) that `--annulla` reverses;
  - "Elenco + scelta a mano": PDFs without a journal stay in articoli_vari; the dashboard lists them ("Da assegnare")
    with a menu of the journals; an ISSN of a known journal printed in the first pages makes it the "probabile" one,
    never assigned by itself;
  - "Rinomina": siglas that are not valid folder names (A&R, PM&R, "Lancet Rheu.") are renamed in the list and the
    folders (AR, PMR, LancetRheu); a folder without a row whose articles all belong to ONE journal of the list is merged
    into that journal's folder (PIIS → JP); tracking parameters are removed from the URLs of the list.
Files are never deleted or overwritten: a file with the same name already in place goes to inbox/doppioni when it is
the same file (same SHA-256), otherwise it gets a suffix _2, _3, …. `original_path` stays the provenance in the
database; layout.PdfLocator finds the PDFs where they are now.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from . import audit, content, journal_table, layout
from .identifiers import is_valid_issn, normalize_issn
from .journal_list_write import JournalList, ListError
from .policy_versions import journal_by_sigla

RULES_VERSION = "journal-folders-v1"
MOVES_DIR = "rapporti/spostamenti"
STOPWORDS = {"of", "the", "and", "in", "for", "on", "a", "an", "&", "de", "la", "le", "der", "die", "und", "di", "e",
             "y", "et", "to", "at", "with"}
ISSN_IN_TEXT = re.compile(r"\b(\d{4})\s?[-‐‑–]\s?(\d{3}[\dXx])\b")
FIRST_PAGES = 2


# ---------------------------------------------------------------- small helpers

def safe_sigla(text: str) -> str:
    """A valid folder name from a sigla: only letters, digits, - and _ (A&R → AR, 'Lancet Rheu.' → LancetRheu)."""
    s = re.sub(r"[^A-Za-z0-9_-]", "", text or "").lstrip("-_")
    return s[:40]


def unique_sigla(base: str, taken: set[str]) -> str:
    """base, or base2, base3… when taken (case-insensitive)."""
    taken = {t.lower() for t in taken}
    if base.lower() not in taken:
        return base
    n = 2
    while f"{base}{n}".lower() in taken:
        n += 1
    return f"{base}{n}"


def propose_sigla(title: str, taken: set[str]) -> str:
    """Initials of the significant words of the title (without of/the/and/&/in/for…), 2–10 characters; a title of one
    word gives its first 10 letters; a digit at the end when the sigla exists already."""
    words = [w for w in re.split(r"[^0-9A-Za-zÀ-ÿ]+", title or "") if w]
    significant = [w for w in words if w.lower() not in STOPWORDS]
    if len(significant) >= 2:
        base = "".join(w[0] for w in significant).upper()[:10]
    elif significant:
        base = significant[0][:10]
    else:
        base = "RIV"
    base = safe_sigla(base) or "RIV"
    if len(base) < 2:
        base = (base + "X")[:2]
    return unique_sigla(base, taken)


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _rel(home: Path, path: Path) -> str:
    return layout.shown_path(home, path)


def _inbox(home: Path, inbox_dir: str) -> Path:
    return Path(home) / inbox_dir


def _journals_dir(home: Path, inbox_dir: str) -> Path:
    return _inbox(home, inbox_dir) / layout.JOURNALS_DIR


# ---------------------------------------------------------------- moving files (never overwrite, never delete)

class Mover:
    """Moves files and remembers every move for the registro; `trial` = only say what would happen."""

    def __init__(self, home: Path, inbox_dir: str, locator: layout.PdfLocator, *, trial: bool = False):
        self.home, self.inbox_dir, self.locator, self.trial = Path(home), inbox_dir, locator, trial
        self.moves: list[dict] = []
        self.problems: list[dict] = []
        self._planned: set[str] = set()

    def _free(self, path: Path) -> bool:
        return not path.exists() and str(path).lower() not in self._planned

    def target(self, src: Path, dest_dir: Path) -> tuple[Path, str]:
        """Where src goes in dest_dir: (path, esito). Same name already there: same file → inbox/doppioni,
        another file → name_2.pdf."""
        dest = dest_dir / src.name
        if self._free(dest):
            return dest, "spostato"
        if dest.exists() and self.locator.sha_of(dest) == self.locator.sha_of(src):
            dup_dir = _inbox(self.home, self.inbox_dir) / layout.DUPLICATES_DIR
            dest = dup_dir / src.name
            n = 2
            while not self._free(dest):
                dest = dup_dir / f"{src.stem}_{n}{src.suffix}"
                n += 1
            return dest, "doppione"
        n = 2
        while not self._free(dest):
            dest = dest_dir / f"{src.stem}_{n}{src.suffix}"
            n += 1
        return dest, "spostato con un nome nuovo"

    def move(self, src: Path, dest_dir: Path, **info) -> dict | None:
        sha = self.locator.sha_of(src)
        dest, esito = self.target(src, dest_dir)
        item = {"da": _rel(self.home, src), "a": _rel(self.home, dest), "impronta": sha, "esito": esito, **info}
        if self.trial:
            self._planned.add(str(dest).lower())
            self.moves.append(item)
            return item
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():                                   # appeared meanwhile: never overwrite
                raise FileExistsError(str(dest))
            os.rename(src, dest)
        except OSError as exc:
            self.problems.append({**item, "problema": f"non spostato: {exc.__class__.__name__}: {exc}"})
            return None
        item["ora"] = now_utc()
        self.moves.append(item)
        return item


def _remove_empty_dirs(top: Path) -> None:
    """Remove the folders left empty under top (and top itself). Files are never removed."""
    if not top.is_dir():
        return
    for d in sorted((p for p in top.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        try:
            d.rmdir()
        except OSError:
            pass
    try:
        top.rmdir()
    except OSError:
        pass


def write_registro(home: Path, kind: str, payload: dict, *, now: datetime | None = None) -> Path:
    folder = Path(home) / MOVES_DIR
    folder.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y-%m-%d_%H%M%S")
    path = folder / f"{kind}_{stamp}.json"
    n = 2
    while path.exists():
        path = folder / f"{kind}_{stamp}_{n}.json"
        n += 1
    path.write_text(json.dumps({"regole": RULES_VERSION, "tipo": kind, "creato": now_utc(), **payload},
                               ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# ---------------------------------------------------------------- documents and their journal

def _sigla_of_journal(conn, rows: list[dict]) -> dict[str, str]:
    exact = {r["sigla"].lower(): r["sigla"] for r in rows if r.get("sigla")}
    return {jid: exact.get(sg, sg) for sg, jid in journal_by_sigla(conn, rows).items()}


def _documents(conn) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT d.id, d.kind, d.original_path, d.file_sha256, d.page_count, d.pdf_title, d.doi_candidate,
                  d.journal_hint, d.article_id, COALESCE(d.journal_id, a.journal_id) AS journal_id,
                  a.title AS article_title, a.doi AS article_doi
           FROM document d LEFT JOIN article a ON a.id = d.article_id
           ORDER BY d.original_path, d.id""").fetchall()


# ---------------------------------------------------------------- passo 3: sistema-riviste

def move_folder(home: Path, inbox_dir: str, old: str, new: str, mover: Mover, folder_moves: list[dict],
                motivo: str) -> None:
    """riviste/<old> → riviste/<new>: renamed when <new> does not exist, otherwise merged file by file (same file →
    inbox/doppioni, same name and another file → _2). Recorded in folder_moves / mover.moves."""
    jdir = _journals_dir(home, inbox_dir)
    src, dst = jdir / old, jdir / new
    if not src.is_dir() or old == new:
        return
    if not dst.exists() or (src.resolve() == dst.resolve()):                # new name, or only the case differs
        try:
            if dst.exists():
                tmp = jdir / (new + "__tmp_orpheus")
                os.rename(src, tmp)
                os.rename(tmp, dst)
            else:
                os.rename(src, dst)
            folder_moves.append({"tipo": "cartella", "da": _rel(home, src), "a": _rel(home, dst), "ora": now_utc(),
                                 "motivo": motivo})
        except OSError as exc:
            mover.problems.append({"da": _rel(home, src), "problema": f"cartella non rinominata: {exc}"})
        return
    for f in sorted(p for p in src.rglob("*") if p.is_file()):
        mover.move(f, dst / f.parent.relative_to(src), motivo=motivo)
    _remove_empty_dirs(src)


def plan_fix(conn, home: Path, inbox_dir: str, jl: JournalList, locator: layout.PdfLocator) -> dict:
    """What `sistema-riviste` would do: sigla renames in the list and the folders, folder merges, URLs to clean,
    folders missing for rows of the list, folders without a row. Read-only."""
    rows = jl.data_rows()
    siglas = {r["sigla"].strip() for r in rows}
    renames = []                                                   # list rows: old → new
    taken = {s for s in siglas if layout.SIGLA_RE.match(s)}
    for r in rows:
        old = r["sigla"].strip()
        if layout.SIGLA_RE.match(old):
            continue
        new = unique_sigla(safe_sigla(old) or "RIV", taken)
        taken.add(new)
        renames.append({"prima": old, "dopo": new})
    final = {s.lower(): s for s in taken}                          # the siglas after the renames
    by_old = {x["prima"].lower(): x["dopo"] for x in renames}

    jdir = _journals_dir(home, inbox_dir)
    folders = sorted(p.name for p in jdir.iterdir() if p.is_dir()) if jdir.is_dir() else []
    docs = _documents(conn)
    sigla_of = _sigla_of_journal(conn, rows)
    folder_ops, orphans = [], []
    for f in folders:
        if f.lower() in final and final[f.lower()] == f:
            continue
        target = by_old.get(f.lower()) or final.get(f.lower()) or final.get(safe_sigla(f).lower())
        reason = "sigla rinominata" if f.lower() in by_old or (safe_sigla(f) and safe_sigla(f) in by_old.values()) \
            else "nome della cartella non valido"
        if target is None:                                         # a folder without a row: its articles decide
            jids = set()
            for d in docs:
                if d["kind"] != "article" or not d["journal_id"]:
                    continue
                now = locator.find(d["original_path"], d["file_sha256"])
                w = locator.where(now) if now is not None else {"sigla": None}
                if (w["sigla"] == f) or (now is None and d["journal_hint"] == f):
                    jids.add(d["journal_id"])
            hits = {sigla_of.get(j) for j in jids}
            if len(hits) == 1 and None not in hits:
                target = hits.pop()
                reason = f"gli articoli della cartella sono della rivista {target}"
            else:
                orphans.append({"cartella": f, "motivo": "nessuna riga nell'elenco" + (
                    " e articoli di più riviste" if len(hits) > 1 else "")})
                continue
        if target == f:
            continue
        exists = (jdir / target).exists() and target.lower() != f.lower()
        folder_ops.append({"cartella": f, "verso": target, "modo": "unisci" if exists else "rinomina",
                           "motivo": reason})

    urls = []
    for r in rows:
        for col in ("sito_ufficiale", "url_istruzioni_autori"):
            v = (r.get(col) or "").strip()
            c = journal_table.clean_url(v) if v else None
            if v and c and c != v:
                urls.append({"sigla": r["sigla"].strip(), "colonna": col, "prima": v, "dopo": c})

    after = set(folders)
    for op in folder_ops:
        after.discard(op["cartella"])
        after.add(op["verso"])
    missing = sorted(s for s in taken if s not in after)
    return {"regole": RULES_VERSION, "sigle": renames, "cartelle": folder_ops, "url": urls,
            "cartelle_da_creare": missing, "cartelle_senza_riga": orphans,
            "da_fare": len(renames) + len(folder_ops) + len(urls) + len(missing)}


def apply_fix(conn, home: Path, inbox_dir: str, jl: JournalList, locator: layout.PdfLocator, the_plan: dict, *,
              archive_dir: str = "data/archive", actor: str = "orpheus sistema-riviste") -> dict:
    """Apply plan_fix: first the list (archive copy; nothing is done when the file is locked), then the folders."""
    for x in the_plan["sigle"]:
        jl.update(x["prima"], {"sigla": x["dopo"]}, check=False, why="sigla non valida come nome di cartella")
    for u in the_plan["url"]:
        jl.update(u["sigla"] if not any(x["prima"] == u["sigla"] for x in the_plan["sigle"])
                  else next(x["dopo"] for x in the_plan["sigle"] if x["prima"] == u["sigla"]),
                  {u["colonna"]: u["dopo"]}, check=False, why="parametri di tracciamento tolti dall'indirizzo")
    saved = jl.save(home=home, archive_dir=archive_dir, conn=conn, actor=actor)
    mover = Mover(home, inbox_dir, locator)
    folder_moves: list[dict] = []
    for op in the_plan["cartelle"]:
        move_folder(home, inbox_dir, op["cartella"], op["verso"], mover, folder_moves, op["motivo"])
    created = layout.ensure_journal_folders(home, inbox_dir, [r["sigla"] for r in jl.data_rows()])
    locator.refresh()
    reg = write_registro(home, "sistema_riviste", {
        "elenco": saved, "spostamenti": folder_moves + [{"tipo": "file", **m} for m in mover.moves],
        "problemi": mover.problems, "cartelle_create": created, "cartelle_senza_riga": the_plan["cartelle_senza_riga"]})
    audit.record(conn, actor=actor, action="fix_journal_folders", entity_type="journal_list",
                 payload={"regole": RULES_VERSION, "sigle": the_plan["sigle"], "cartelle": the_plan["cartelle"],
                          "url": len(the_plan["url"]), "file_spostati": len(mover.moves),
                          "cartelle_create": created, "registro": _rel(home, reg)})
    return {"elenco": saved, "cartelle_spostate": folder_moves, "file_spostati": mover.moves,
            "problemi": mover.problems, "cartelle_create": created, "registro": _rel(home, reg)}


# ---------------------------------------------------------------- passo 4a-b: riconosci-riviste

def _issn_columns(g: dict) -> tuple[str, str]:
    types = {i: c.most_common(1)[0][0] for i, c in g["types"].items()}
    prints = sorted(i for i in g["issn"] if types.get(i) == "print")
    onlines = sorted(i for i in g["issn"] if types.get(i) == "electronic")
    unknown = sorted(i for i in g["issn"] if i not in types)
    p = prints[0] if prints else ""
    o = onlines[0] if onlines else ""
    for i in unknown:
        if not p:
            p = i
        elif not o:
            o = i
    return p, o


def plan_recognise(conn, home: Path, jl: JournalList, *, inbox_dir: str = "inbox", openalex=None,
                   today: str | None = None) -> dict:
    """New rows of the list for the Crossref journals of confirmed articles that are not in the list. Read-only (the
    OpenAlex client, when given, is asked for the journal's home page)."""
    rows = jl.data_rows()
    base = journal_table.plan(conn, home, rows)
    known = {r[0]: r[1] for r in conn.execute("SELECT issn, journal_id FROM journal_issn")}
    listed = {}
    for r in rows:
        for c in ("issn_print", "issn_online"):
            v = (r.get(c) or "").strip()
            if v and is_valid_issn(v):
                listed[normalize_issn(v)] = r["sigla"].strip()
    taken = {r["sigla"].strip() for r in rows}
    jdir = _journals_dir(home, inbox_dir)
    if jdir.is_dir():
        taken |= {p.name for p in jdir.iterdir() if p.is_dir()}
    today = today or datetime.now().strftime("%Y-%m-%d")
    new, skipped = [], []
    for g in sorted(base["_fuori"], key=lambda g: (-len(g["articles"]), sorted(g["issn"]))):
        title = g["titles"].most_common(1)[0][0] if g["titles"] else ""
        dois = sorted(d for (d,) in conn.execute(
            f"SELECT doi FROM article WHERE id IN ({','.join('?' * len(g['articles']))}) AND doi IS NOT NULL",
            g["articles"]))
        item = {"titolo": title, "issn": sorted(g["issn"]), "articoli": len(g["articles"]), "doi": dois[:3]}
        if not dois:
            skipped.append({**item, "motivo": "nessun DOI confermato"})
            continue
        if not title:
            skipped.append({**item, "motivo": "Crossref non dà il titolo della rivista"})
            continue
        in_db = sorted({known[i] for i in g["issn"] if i in known})
        if in_db:
            skipped.append({**item, "motivo": "gli ISSN sono già di una rivista nel database (manca la sua riga "
                                              "nell'elenco?)"})
            continue
        in_list = sorted({listed[i] for i in g["issn"] if i in listed})
        if in_list:
            skipped.append({**item, "motivo": f"gli ISSN sono già nella riga {', '.join(in_list)} dell'elenco"})
            continue
        sigla = propose_sigla(title, taken)
        taken.add(sigla)
        p, o = _issn_columns(g)
        site, note_site = "", None
        if openalex is not None:
            site, note_site = _openalex_homepage(openalex, sorted(g["issn"]))
        publisher = g["publishers"].most_common(1)[0][0] if g["publishers"] else ""
        values = {"sigla": sigla, "titolo": title, "issn_print": p, "issn_online": o, "sito_ufficiale": site or "",
                  "url_istruzioni_autori": "", "priorita": "",
                  "note": f"aggiunta automatica {today} da {dois[0]}" + (f"; editore {publisher}" if publisher else "")}
        new.append({**item, "sigla": sigla, "editore": publisher, "valori": values,
                    **({"nota_sito": note_site} if note_site else {})})
    return {"regole": RULES_VERSION, "nuove": new, "non_create": skipped,
            "articoli_in_riviste_nuove": sum(n["articoli"] for n in new)}


def _openalex_homepage(client, issns: list[str]) -> tuple[str, str | None]:
    from . import journal_metrics
    for issn in issns:
        try:
            resp = client.by_issn(issn)
        except Exception as exc:                                          # noqa: BLE001 - the site is optional
            return "", f"OpenAlex non raggiungibile ({exc.__class__.__name__}): sito da aggiungere a mano"
        if resp.status != 200:
            continue
        try:
            src = journal_metrics.parse_source(resp.body)
        except ValueError:
            continue
        url = journal_table.clean_url(src.get("homepage_url")) or ""
        return url, None if url else "OpenAlex non ha il sito della rivista"
    return "", "rivista non trovata in OpenAlex"


def apply_recognise(conn, home: Path, inbox_dir: str, jl: JournalList, the_plan: dict, *,
                    archive_dir: str = "data/archive", actor: str = "orpheus riconosci-riviste") -> dict:
    """Rows in the list (archive copy first), then journal/journal_issn and the links (scrivi-riviste), then folders."""
    added, refused = [], []
    for n in the_plan["nuove"]:
        try:
            jl.add(n["valori"], conn=conn, why=f"rivista riconosciuta da {n['articoli']} articoli con DOI confermato")
            added.append(n)
        except ListError as exc:
            refused.append({**{k: n[k] for k in ("sigla", "titolo", "issn")}, "motivo": exc.message})
    saved = jl.save(home=home, archive_dir=archive_dir, conn=conn, actor=actor)
    rows = jl.data_rows()
    written = journal_table.write(conn, journal_table.plan(conn, home, rows), actor=actor) if added else None
    created = layout.ensure_journal_folders(home, inbox_dir, [r["sigla"] for r in rows])
    if added:
        audit.record(conn, actor=actor, action="recognise_journals", entity_type="journal_list",
                     payload={"regole": RULES_VERSION, "riviste": [{k: n[k] for k in ("sigla", "titolo", "issn",
                                                                                       "articoli", "doi")}
                                                                    for n in added], "rifiutate": refused,
                              "copia_elenco": saved.get("copia")})
    return {"aggiunte": [{k: n[k] for k in ("sigla", "titolo", "editore", "issn", "articoli")} for n in added],
            "rifiutate": refused, "elenco": {"scritto": saved["scritto"], "copia": saved["copia"]},
            "scrivi_riviste": written, "cartelle_create": created}


# ---------------------------------------------------------------- passo 4c: sposta-articoli, "Da assegnare"

def _probable(conn, doc_id: str, known: dict[str, str], sigla_of: dict[str, str]) -> dict | None:
    hits = Counter()
    for (text,) in conn.execute("SELECT text FROM document_page WHERE document_id = ? AND page_no <= ? ORDER BY page_no",
                                (doc_id, FIRST_PAGES)):
        for a, b in ISSN_IN_TEXT.findall(text or ""):
            issn = f"{a}-{b.upper()}"
            if is_valid_issn(issn) and issn in known and known[issn] in sigla_of:
                hits[known[issn]] += 1
    if not hits:
        return None
    jid, _n = hits.most_common(1)[0]
    return {"sigla": sigla_of[jid], "journal_id": jid, "perche": "ISSN della rivista nelle prime pagine del PDF"}


def plan_moves(conn, home: Path, inbox_dir: str, rows: list[dict], locator: layout.PdfLocator) -> dict:
    """Which PDFs of articoli_vari go to their journal's folder, and which stay ("Da assegnare"). Read-only."""
    sigla_of = _sigla_of_journal(conn, rows)
    classes = content.classify_all(conn)
    from .verify_doi import OUTCOME_IT, latest_checks
    checks = latest_checks(conn)
    known = {r[0]: r[1] for r in conn.execute("SELECT issn, journal_id FROM journal_issn")}
    misc = _inbox(home, inbox_dir) / layout.MISC_DIR
    mover = Mover(home, inbox_dir, locator, trial=True)
    to_move, todo, other = [], [], []
    seen_files = set()
    for d in _documents(conn):
        if d["kind"] != "article":
            continue
        now = locator.find(d["original_path"], d["file_sha256"])
        if now is None or locator.where(now)["cartella"] != layout.MISC_DIR:
            continue
        seen_files.add(str(now))
        cls = (classes.get(d["id"]) or {}).get("classe")
        if cls and cls != content.COMPLETE:
            other.append({"file": _rel(home, now), "motivo": f"{cls}: va in {content.SEPARATED_DIR} (leggi_pdf)"})
            continue
        jid = d["journal_id"]
        if jid and jid in sigla_of:
            sigla = sigla_of[jid]
            item = mover.move(now, _journals_dir(home, inbox_dir) / sigla / layout.ARTICLES_DIR,
                              document_id=d["id"], sigla=sigla)
            to_move.append(item)
            continue
        check = checks.get(d["id"])
        todo.append({"document_id": d["id"], "file": _rel(home, now), "pagine": d["page_count"],
                     "titolo_pdf": d["pdf_title"] or d["article_title"], "doi_candidato": d["doi_candidate"],
                     "esito_crossref": OUTCOME_IT.get(check["outcome"], check["outcome"]) if check else "non verificato",
                     "rivista_nel_database": bool(jid),
                     "probabile": _probable(conn, d["id"], known, sigla_of)})
    unread = sorted(_rel(home, p) for p in misc.rglob("*")
                    if misc.is_dir() and p.is_file() and p.suffix.lower() == ".pdf" and str(p) not in seen_files) \
        if misc.is_dir() else []
    return {"regole": RULES_VERSION, "da_spostare": to_move, "da_assegnare": todo, "altri": other,
            "non_ancora_letti": unread, "da_fare": len(to_move)}


def apply_moves(conn, home: Path, inbox_dir: str, rows: list[dict], locator: layout.PdfLocator, *,
                only_document: str | None = None, actor: str = "orpheus sposta-articoli") -> dict:
    the_plan = plan_moves(conn, home, inbox_dir, rows, locator)
    mover = Mover(home, inbox_dir, locator)
    for m in the_plan["da_spostare"]:
        if only_document and m["document_id"] != only_document:
            continue
        mover.move(home / Path(m["da"]), _journals_dir(home, inbox_dir) / m["sigla"] / layout.ARTICLES_DIR,
                   document_id=m["document_id"], sigla=m["sigla"])
    locator.refresh()
    reg = None
    if mover.moves or mover.problems:
        reg = write_registro(home, "sposta_articoli", {"spostamenti": [{"tipo": "file", **m} for m in mover.moves],
                                                      "problemi": mover.problems})
        audit.record(conn, actor=actor, action="move_articles_to_journals", entity_type="document",
                     payload={"regole": RULES_VERSION, "spostati": len(mover.moves), "problemi": len(mover.problems),
                              "per_rivista": dict(Counter(m["sigla"] for m in mover.moves)),
                              "registro": _rel(home, reg)})
    return {"spostati": mover.moves, "problemi": mover.problems, "registro": _rel(home, reg) if reg else None,
            "da_assegnare": the_plan["da_assegnare"], "altri": the_plan["altri"],
            "non_ancora_letti": the_plan["non_ancora_letti"]}


def undo(conn, home: Path, registro: Path, locator: layout.PdfLocator, *, trial: bool = False,
         actor: str = "orpheus sposta-articoli --annulla") -> dict:
    """Move back everything a registro moved (last move first). A file is moved back only if it is still where the
    registro put it, with the same fingerprint, and its old place is free."""
    data = json.loads(Path(registro).read_text(encoding="utf-8"))
    back, problems = [], []
    for m in reversed(data.get("spostamenti") or []):
        src, dst = home / Path(m["a"]), home / Path(m["da"])
        item = {"da": m["a"], "a": m["da"], "tipo": m.get("tipo", "file")}
        if m.get("tipo") == "cartella":
            if not src.is_dir() or dst.exists():
                problems.append({**item, "problema": "cartella non trovata o posto già occupato"})
                continue
        else:
            if not src.is_file():
                problems.append({**item, "problema": "il file non è più dove il registro l'ha messo"})
                continue
            if m.get("impronta") and locator.sha_of(src) != m["impronta"]:
                problems.append({**item, "problema": "il file è cambiato (altra impronta): non spostato"})
                continue
            if dst.exists():
                problems.append({**item, "problema": "al posto di prima c'è già un file: non spostato"})
                continue
        if not trial:
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                os.rename(src, dst)
            except OSError as exc:
                problems.append({**item, "problema": f"non spostato: {exc}"})
                continue
            item["ora"] = now_utc()
        back.append(item)
    reg = None
    if not trial:
        for m in data.get("spostamenti") or []:                  # folders left empty by the undo (merged ones)
            parent = (home / Path(m["a"])).parent
            if m.get("tipo") != "cartella" and parent.is_dir() and not any(parent.iterdir()) \
                    and parent.name != layout.ARTICLES_DIR and parent.name != layout.GUIDELINES_DIR:
                _remove_empty_dirs(parent)
        locator.refresh()
        reg = write_registro(home, "annulla", {"registro_annullato": _rel(home, Path(registro)),
                                               "spostamenti": back, "problemi": problems})
        audit.record(conn, actor=actor, action="undo_moves", entity_type="document",
                     payload={"regole": RULES_VERSION, "registro": _rel(home, Path(registro)),
                              "riportati": len(back), "problemi": len(problems), "nuovo_registro": _rel(home, reg)})
    return {"riportati": back, "problemi": problems, "registro": _rel(home, reg) if reg else None,
            "modo": "prova" if trial else "scrittura"}


def assign(conn, home: Path, inbox_dir: str, rows: list[dict], locator: layout.PdfLocator, document_id: str,
           sigla: str, *, actor: str = "cruscotto") -> dict:
    """The user's choice ("Da assegnare"): document.journal_id (and article.journal_id when the article has none),
    audit event, then the PDF goes to the journal's folder like sposta-articoli."""
    by_sigla = journal_by_sigla(conn, rows)
    jid = by_sigla.get((sigla or "").strip().lower())
    if not jid:
        raise ValueError(f"la rivista {sigla} non è nella tabella delle riviste (serve la sua riga con un ISSN e "
                         "\"Aggiorna le riviste\")")
    d = conn.execute("SELECT id, article_id, journal_id FROM document WHERE id = ? AND kind = 'article'",
                     (document_id,)).fetchone()
    if d is None:
        raise ValueError("documento non trovato")
    from .db import transaction
    with transaction(conn):
        n_doc = conn.execute("UPDATE document SET journal_id = ? WHERE id = ? AND (journal_id IS NULL OR journal_id = ?)",
                             (jid, document_id, jid)).rowcount
        if not n_doc:
            raise ValueError("il documento ha già un'altra rivista")
        n_art = 0
        if d["article_id"]:
            n_art = conn.execute("""UPDATE article SET journal_id = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
                                    WHERE id = ? AND journal_id IS NULL""", (jid, d["article_id"])).rowcount
        audit.record(conn, actor=actor, action="assign_document_journal", entity_type="document", entity_id=document_id,
                     payload={"regole": RULES_VERSION, "sigla": sigla, "journal_id": jid, "articolo_collegato": bool(n_art)},
                     in_transaction=True)
    moved = apply_moves(conn, home, inbox_dir, rows, locator, only_document=document_id, actor=actor)
    return {"assegnato": True, "sigla": sigla, "spostati": moved["spostati"], "problemi": moved["problemi"],
            "registro": moved["registro"]}


def latest_registro(home: Path, kind: str | None = None) -> Path | None:
    folder = Path(home) / MOVES_DIR
    if not folder.is_dir():
        return None
    files = sorted(p for p in folder.glob("*.json") if kind is None or p.name.startswith(kind + "_"))
    return files[-1] if files else None


# ---------------------------------------------------------------- passo 5: the dashboard window "Nuova rivista" / "Modifica"

def save_journal(conn, home: Path, inbox_dir: str, csv_path: Path, values: dict, original: str | None,
                 locator: layout.PdfLocator, *, archive_dir: str = "data/archive", actor: str = "cruscotto") -> dict:
    """One row of the list from the dashboard: added (original=None) or changed. Checks of journal_list_write; URLs
    without tracking parameters; a new sigla renames the folder (registro); folders created; journal / journal_issn
    written for that row (scrivi-riviste for that row only)."""
    from .layout import CSV_COLUMNS
    clean = {k: ("" if values.get(k) is None else str(values.get(k)).strip()) for k in CSV_COLUMNS if k in values}
    for k in ("sito_ufficiale", "url_istruzioni_autori"):
        if clean.get(k):
            clean[k] = journal_table.clean_url(clean[k]) or ""
    jl = JournalList.load(csv_path)
    if original:
        jl.update(original, clean, conn=conn, why="cruscotto: Modifica")
    else:
        jl.add(clean, conn=conn, why="cruscotto: Nuova rivista")
    saved = jl.save(home=home, archive_dir=archive_dir, conn=conn, actor=actor)
    sigla = clean.get("sigla") or original
    folder_moves: list[dict] = []
    reg = None
    if original and sigla and original != sigla:
        mover = Mover(home, inbox_dir, locator)
        move_folder(home, inbox_dir, original, sigla, mover, folder_moves, "sigla cambiata nel cruscotto")
        moves = folder_moves + [{"tipo": "file", **m} for m in mover.moves]
        if moves or mover.problems:
            reg = write_registro(home, "cambio_sigla", {"elenco": saved, "spostamenti": moves, "problemi": mover.problems})
        locator.refresh()
    rows = jl.data_rows()
    created = layout.ensure_journal_folders(home, inbox_dir, [sigla])
    the_plan = journal_table.plan(conn, home, rows)
    the_plan["riviste"] = [r for r in the_plan["riviste"] if (r.get("sigla") or "").lower() == (sigla or "").lower()]
    written = journal_table.write(conn, the_plan, actor=actor)
    row = next((r for r in the_plan["riviste"]), None)
    return {"sigla": sigla, "elenco": {"scritto": saved["scritto"], "copia": saved["copia"]},
            "cartelle_spostate": folder_moves, "cartelle_create": created, "registro": _rel(home, reg) if reg else None,
            "scrivi_riviste": written, "rivista": {"azione": row["azione"], "problemi": row["problemi"]} if row else None}


def lookup_issn(issn: str, *, crossref_client=None, openalex_client=None) -> dict:
    """"Compila dall'ISSN": Crossref /journals/{issn} (title, publisher, ISSNs and type) and OpenAlex /sources/issn:
    (site). Only a proposal: the user corrects it before saving."""
    if not is_valid_issn(issn or ""):
        raise ValueError(f"ISSN {issn!r} non valido (formato 1234-567X, controlla la cifra finale)")
    issn = normalize_issn(issn)
    values: dict[str, str] = {}
    notes, sources = [], []
    if crossref_client is not None:
        try:
            resp = crossref_client.get_journal(issn)
            if resp.status == 200:
                msg = json.loads(resp.body.decode("utf-8")).get("message") or {}
                from . import crossref
                if msg.get("title"):
                    values["titolo"] = crossref.strip_markup(str(msg["title"]))
                for t in msg.get("issn-type") or []:
                    v, typ = (t or {}).get("value") or "", (t or {}).get("type")
                    if is_valid_issn(v):
                        col = "issn_print" if typ == "print" else "issn_online" if typ == "electronic" else None
                        if col and not values.get(col):
                            values[col] = normalize_issn(v)
                if msg.get("publisher"):
                    values["editore"] = crossref.strip_markup(str(msg["publisher"]))
                sources.append("Crossref /journals/" + issn)
            else:
                notes.append("Crossref non conosce questo ISSN")
        except Exception as exc:                                          # noqa: BLE001 - shown in the window
            notes.append(f"Crossref non raggiungibile ({exc.__class__.__name__})")
    if openalex_client is not None:
        site, note = _openalex_homepage(openalex_client, [issn])
        if site:
            values["sito_ufficiale"] = site
            sources.append("OpenAlex /sources/issn:" + issn)
        if note:
            notes.append(note)
    if not values.get("issn_print") and not values.get("issn_online"):
        values["issn_print"] = issn
    return {"issn": issn, "valori": values, "fonti": sources, "note": notes}


def unassigned(conn, home: Path, inbox_dir: str, rows: list[dict], locator: layout.PdfLocator) -> dict:
    """The "Da assegnare" list of the dashboard, with the journals that can be chosen."""
    p = plan_moves(conn, home, inbox_dir, rows, locator)
    sigla_of = _sigla_of_journal(conn, rows)
    choices = sorted({sg for sg in sigla_of.values()}, key=str.lower)
    titles = {r["sigla"]: r.get("titolo") for r in rows}
    last = latest_registro(home, "sposta_articoli")
    return {"righe": p["da_assegnare"], "da_spostare": len(p["da_spostare"]), "altri": p["altri"],
            "non_ancora_letti": p["non_ancora_letti"],
            "riviste": [{"sigla": sg, "titolo": titles.get(sg)} for sg in choices],
            "ultimo_registro": _rel(home, last) if last else None}
