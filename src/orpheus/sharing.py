"""Share the knowledge base with colleagues — T-053 (decisions of the user, 2026-10-23 17:00).

Not the same thing as `transfer` (ADR-0007): `transfer` moves ONE database between the PCs of the same person and
refuses copies that diverged. Here two DIFFERENT databases (the user's and a colleague's) exchange what they know:

esporta → one .zip file (format orpheus-condivisione/1): manifest.json + condivisione.db, an SQLite file with the same
          schema holding only
            * journals, ISSN, metrics, current journal cards with their values and the guideline versions they come from;
            * articles with their metadata (authors, MeSH, publication types, versions, references), checks of DOI,
              PubMed and full text;
            * current article cards (cards-v1) with their values, cards-v2 items and outcome rows, current claims and
              evidence;
            * the parts of the articles: text segments with their vectors, sections of open texts;
            * what the articles report about methods, statistics, transparency and structure (T-057, stats-terms):
              text_method_item and text_structure (the user's hand check text_method_check stays here);
            * the rows those depend on (documents, snapshots, runs, tasks, agents, models, project, sources).
          Never: PDF files, page texts, manuscripts and drafts (R10), EURYDIKE reviews, the audit log, backups, .env.
          Snapshot storage paths are blanked (the files are not in the package). Export only READS the database.
unisci  → merges a package into this PC's database. "Keep yours" (decision of 2026-10-23): what this PC already has is
          never changed or replaced; only what is missing is added.
            * same row (same id) or same natural key (DOI/PMID of an article, ISSN of a journal, SHA-256 of a PDF, …)
              → it is the same thing: the local one is used, nothing is written;
            * a card (journal or article) for something that already has a current card of that version here → skipped,
              listed in the report ("tenute le tue");
            * segments, sections, cards-v2 items, claims: imported for an article only when this PC has none;
            * details of an article or journal (authors, ISSN, references, …) only with an article or journal new here.
          Everything happens in one transaction after a backup; --prova rolls it back and only reports.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tempfile
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, audit, db

FORMAT = "orpheus-condivisione/1"
MANIFEST = "manifest.json"
DB_MEMBER = "condivisione.db"
FOLDER_OUT = "condivisione"                 # under ORPHEUS_HOME: packages made here
FOLDER_IN = "inbox/condivisione"            # under ORPHEUS_HOME: packages received from colleagues

# Root tables, in import order: (table, WHERE of the export, group key or None, extra condition of the group check).
# A table with a group key is imported group by group, and a group only when this PC has no row with that key.
ROOTS: list[tuple[str, str, tuple[str, ...] | None, str]] = [
    ("journal", "1", None, ""),
    ("journal_metric", "1", None, ""),
    ("journal_card", "status = 'current'", ("journal_id", "card_version"), "status = 'current'"),
    # T-052: values written by hand (journal facts, with source): per journal, "tieni sempre la tua" as for the cards
    ("journal_card_manual", "status = 'current'", ("journal_id",), "status = 'current'"),
    ("article", "1", None, ""),
    ("article_section", "1", ("article_id",), ""),
    ("segment", "article_id IS NOT NULL", ("article_id", "rules"), ""),
    ("text_structure", "1", ("article_id", "rules"), ""),         # T-057: counted from the articles (decision 2026-10-27)
    ("text_method_item", "1", ("article_id", "rules"), ""),
    ("article_card", "status = 'current'", ("article_id", "card_version"), "status = 'current'"),
    ("card_item", "1", ("article_id",), ""),
    ("card_outcome_row", "1", ("article_id",), ""),
    ("scientific_claim", "status = 'current' AND article_id IS NOT NULL", ("article_id",), "status = 'current'"),
]

# Rows that belong to another row: exported with it, imported only when the parent row is new on this PC.
CHILDREN: dict[str, list[tuple[str, str]]] = {
    "journal": [("journal_issn", "journal_id")],
    "journal_card": [("journal_card_value", "card_id")],
    "journal_policy_version": [("journal_policy_document", "policy_version_id")],
    "article": [("article_author", "article_id"), ("article_mesh", "article_id"),
                ("article_publication_type", "article_id"), ("article_version", "article_id"),
                ("citation", "citing_article_id"), ("pubmed_check", "article_id"), ("fulltext_check", "article_id")],
    "document": [("doi_check", "document_id"), ("document_doi", "document_id")],
    "segment": [("segment_vector", "segment_id")],
    "article_card": [("card_value", "card_id")],
    "workflow_run": [("validation_result", "workflow_run_id")],
    "scientific_claim": [("evidence_record", "claim_id")],
}

# Tables that may enter the package only as rows another row depends on.
DEPENDENCIES = {"author", "document", "source_snapshot", "source", "journal_policy_version", "journal_policy_document",
                "workflow_run", "task", "agent", "model", "project", "pubmed_check", "fulltext_check", "doi_check",
                "validation_result", "document_doi", "card_value", "journal_card_value", "journal_issn",
                "segment_vector", "evidence_record", "article_author", "article_mesh", "article_publication_type",
                "article_version", "citation"}
ALLOWED = {t for t, *_ in ROOTS} | DEPENDENCIES
DOCUMENT_KINDS = ("article", "author_guidelines")     # never 'other' (where a manuscript could be)


class SharingError(Exception):
    def __init__(self, message: str, *, code: int = 5, **details):
        super().__init__(message)
        self.message, self.code, self.details = message, code, details


# ---------------------------------------------------------------- schema helpers

class Schema:
    """Primary keys, foreign keys, unique keys and columns of the tables, read from a connection."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._cols: dict[str, list[dict]] = {}
        self._fks: dict[str, list[dict]] = {}
        self._uniq: dict[str, list[tuple[str, ...]]] = {}

    def cols(self, t: str) -> list[dict]:
        if t not in self._cols:
            self._cols[t] = [dict(r) for r in self.conn.execute(f"PRAGMA table_info({t})")]
        return self._cols[t]

    def names(self, t: str) -> list[str]:
        return [c["name"] for c in self.cols(t)]

    def pk(self, t: str) -> tuple[str, ...]:
        cs = sorted((c for c in self.cols(t) if c["pk"]), key=lambda c: c["pk"])
        if not cs:
            raise SharingError(f"la tabella {t} non ha una chiave primaria: non si può condividere")
        return tuple(c["name"] for c in cs)

    def notnull(self, t: str, col: str) -> bool:
        return any(c["name"] == col and (c["notnull"] or c["pk"]) for c in self.cols(t))

    def fks(self, t: str) -> list[dict]:
        """[{table, from: (cols), to: (cols of the parent's primary key)}]."""
        if t not in self._fks:
            groups: dict[int, list] = defaultdict(list)
            for r in self.conn.execute(f"PRAGMA foreign_key_list({t})"):
                groups[r["id"]].append(r)
            out = []
            for rows in groups.values():
                rows.sort(key=lambda r: r["seq"])
                parent = rows[0]["table"]
                frm = tuple(r["from"] for r in rows)
                to = tuple(r["to"] for r in rows) if rows[0]["to"] else self.pk(parent)
                if to != self.pk(parent):
                    raise SharingError(f"{t}.{frm} punta a colonne di {parent} che non sono la sua chiave")
                out.append({"table": parent, "from": frm, "to": to})
            self._fks[t] = out
        return self._fks[t]

    def uniques(self, t: str) -> list[tuple[str, ...]]:
        """Unique keys that hold for every row (primary key included; partial indexes left out)."""
        if t not in self._uniq:
            out = []
            for ix in self.conn.execute(f"PRAGMA index_list({t})"):
                if not ix["unique"] or ix["partial"]:
                    continue
                cols = tuple(r["name"] for r in self.conn.execute(f"PRAGMA index_info({ix['name']})"))
                if cols and None not in cols:
                    out.append(cols)
            if self.pk(t) not in out:
                out.insert(0, self.pk(t))
            self._uniq[t] = out
        return self._uniq[t]

    def fk_cols(self, t: str) -> set[str]:
        return {c for fk in self.fks(t) for c in fk["from"]}


def _q(cols) -> str:
    return ", ".join(f'"{c}"' for c in cols)


def _where(cols) -> str:
    return " AND ".join(f'"{c}" = ?' for c in cols)


# ---------------------------------------------------------------- export

def export_package(conn: sqlite3.Connection, *, dest_dir: Path, note: str | None = None) -> dict:
    """Write the package; never writes into `conn`."""
    sch = Schema(conn)
    rows: dict[str, dict[tuple, sqlite3.Row]] = defaultdict(dict)

    def add(t: str, r: sqlite3.Row) -> bool:
        if t not in ALLOWED:
            raise SharingError(f"la tabella {t} non può entrare nel pacchetto (richiesta da una dipendenza)")
        if t == "document" and r["kind"] not in DOCUMENT_KINDS:
            raise SharingError(f"il documento {r['id']} è di tipo {r['kind']!r}: non entra nel pacchetto (R10)")
        key = tuple(r[c] for c in sch.pk(t))
        if key in rows[t]:
            return False
        rows[t][key] = r
        return True

    conn.execute("BEGIN")                     # one consistent read
    try:
        todo: list[tuple[str, tuple]] = []
        for t, where, _g, _c in ROOTS:
            for r in conn.execute(f"SELECT * FROM {t} WHERE {where}"):
                if add(t, r):
                    todo.append((t, tuple(r[c] for c in sch.pk(t))))
        while todo:                           # children of every exported row, then the rows they depend on
            t, key = todo.pop()
            r = rows[t][key]
            for ct, col in CHILDREN.get(t, []):
                for cr in conn.execute(f"SELECT * FROM {ct} WHERE \"{col}\" = ?", (key[0],)):
                    if add(ct, cr):
                        todo.append((ct, tuple(cr[c] for c in sch.pk(ct))))
            for fk in sch.fks(t):
                vals = tuple(r[c] for c in fk["from"])
                if any(v is None for v in vals):
                    continue
                pr = conn.execute(f"SELECT * FROM {fk['table']} WHERE {_where(fk['to'])}", vals).fetchone()
                if pr is not None and add(fk["table"], pr):
                    todo.append((fk["table"], vals))
        migrations = db.applied_versions(conn)
    finally:
        conn.execute("ROLLBACK")

    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    out_zip = dest_dir / f"ORPHEUS_condivisione_{stamp}.zip"
    with tempfile.TemporaryDirectory() as tmp:
        share_path = Path(tmp) / DB_MEMBER
        share = db.connect(share_path)
        try:
            db.migrate(share)
            share.execute("PRAGMA foreign_keys = OFF")
            share_schema = Schema(share)
            with db.transaction(share):
                for t, by_key in rows.items():
                    names = [n for n in sch.names(t) if n in set(share_schema.names(t))]
                    for r in by_key.values():
                        vals = [r[n] for n in names]
                        if t == "source_snapshot":
                            vals[names.index("storage_path")] = None     # the files are not in the package
                        share.execute(f"INSERT OR IGNORE INTO {t} ({_q(names)}) VALUES ({', '.join('?' * len(names))})",
                                      vals)
            share.execute("PRAGMA foreign_keys = ON")
            broken = share.execute("PRAGMA foreign_key_check").fetchall()
            if broken:
                raise SharingError(f"pacchetto incompleto: {len(broken)} collegamenti senza la riga collegata "
                                   f"(prima: {tuple(broken[0])})")
            share.execute("PRAGMA journal_mode = DELETE")
        finally:
            share.close()
        counts = {t: len(v) for t, v in sorted(rows.items())}
        sha = hashlib.sha256(share_path.read_bytes()).hexdigest()
        manifest = {"formato": FORMAT, "versione_orpheus": __version__, "creato_il": audit.utc_now(),
                    "migrazioni": migrations, "righe": counts, "sha256_db": sha, "nota": note,
                    "riepilogo": summary_counts(counts)}
        partial = out_zip.with_name(out_zip.name + ".partial")
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(MANIFEST, json.dumps(manifest, indent=2, ensure_ascii=False))
            z.write(share_path, DB_MEMBER)
        partial.replace(out_zip)
    return {"pacchetto": str(out_zip), "dimensione_mb": round(out_zip.stat().st_size / 1e6, 1), **manifest}


def summary_counts(counts: dict[str, int]) -> dict[str, int]:
    return {"riviste": counts.get("journal", 0), "schede_riviste": counts.get("journal_card", 0),
            "articoli": counts.get("article", 0), "schede_articoli": counts.get("article_card", 0),
            "valori_scheda_unica": counts.get("card_item", 0), "pezzi_di_testo": counts.get("segment", 0),
            "vettori": counts.get("segment_vector", 0), "sezioni_testi_aperti": counts.get("article_section", 0),
            "affermazioni": counts.get("scientific_claim", 0), "voci_metodi": counts.get("text_method_item", 0),
            "articoli_con_struttura": counts.get("text_structure", 0)}


# ---------------------------------------------------------------- import

def read_package(path: Path, workdir: Path) -> tuple[dict, Path]:
    path = Path(path)
    if not path.is_file():
        raise SharingError(f"file non trovato: {path}")
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise SharingError(f"{path.name} non è un pacchetto di ORPHEUS (zip non valido)") from exc
    with z:
        names = set(z.namelist())
        if names != {MANIFEST, DB_MEMBER}:
            raise SharingError(f"{path.name} non è un pacchetto di condivisione di ORPHEUS (contenuto inatteso)")
        manifest = json.loads(z.read(MANIFEST).decode("utf-8"))
        if manifest.get("formato") != FORMAT:
            raise SharingError(f"formato {manifest.get('formato')!r} non riconosciuto (atteso {FORMAT})")
        target = Path(workdir) / DB_MEMBER
        with z.open(DB_MEMBER) as src, open(target, "wb") as dst:
            shutil.copyfileobj(src, dst)
    if hashlib.sha256(target.read_bytes()).hexdigest() != manifest.get("sha256_db"):
        raise SharingError("il pacchetto è rovinato (impronta del database diversa dal manifest): fattelo rimandare")
    return manifest, target


class Merger:
    def __init__(self, local: sqlite3.Connection, share: sqlite3.Connection):
        self.local, self.share = local, share
        self.ls, self.ss = Schema(local), Schema(share)
        self.map: dict[tuple[str, tuple], tuple] = {}
        self.failed: set[tuple[str, tuple]] = set()
        self.busy: set[tuple[str, tuple]] = set()
        self.new: dict[str, int] = defaultdict(int)
        self.same: dict[str, int] = defaultdict(int)
        self.kept: dict[str, int] = defaultdict(int)          # groups skipped because this PC has its own
        self.problems: list[dict] = []
        self.unlinked: dict[str, int] = defaultdict(int)      # optional links dropped (parent not importable)

    # --- lookups
    def share_row(self, t: str, key: tuple):
        return self.share.execute(f"SELECT * FROM {t} WHERE {_where(self.ss.pk(t))}", key).fetchone()

    def local_key(self, t: str, cols: tuple, vals: tuple):
        if any(v is None for v in vals):
            return None
        r = self.local.execute(f"SELECT {_q(self.ls.pk(t))} FROM {t} WHERE {_where(cols)} LIMIT 1", vals).fetchone()
        return tuple(r) if r else None

    def match_raw(self, t: str, r) -> tuple | None:
        """Same thing already here, found without following links (id, DOI, ISSN, SHA-256 of the file…)."""
        fkc = self.ls.fk_cols(t)
        if t == "project":
            p = self.local.execute("SELECT id FROM project ORDER BY created_at, id LIMIT 1").fetchone()
            if p:
                return (p[0],)
        if t == "journal":
            for (issn,) in self.share.execute("SELECT issn FROM journal_issn WHERE journal_id = ?", (r["id"],)):
                j = self.local.execute("SELECT journal_id FROM journal_issn WHERE issn = ?", (issn,)).fetchone()
                if j:
                    return (j[0],)
        for cols in self.ls.uniques(t):
            if not set(cols) & fkc and all(c in r.keys() for c in cols):
                k = self.local_key(t, cols, tuple(r[c] for c in cols))
                if k:
                    return k
        return None

    # --- the core
    def ensure(self, t: str, key: tuple) -> tuple | None:
        """Local key of the share row (t, key): the same row already here, or the row added now; None if impossible."""
        mk = (t, key)
        if mk in self.map:
            return self.map[mk]
        if mk in self.failed or mk in self.busy:
            return None
        r = self.share_row(t, key)
        if r is None:                                          # not in the package: usable only if already here
            k = self.local_key(t, self.ls.pk(t), key)
            if k is None:
                self.failed.add(mk)
            else:
                self.map[mk] = k
            return k
        found = self.match_raw(t, r)
        if found:
            self.map[mk] = found
            self.same[t] += 1
            return found
        self.busy.add(mk)
        try:
            vals = {n: r[n] for n in self.ss.names(t) if n in set(self.ls.names(t))}
            for fk in self.ls.fks(t):
                pv = tuple(r[c] for c in fk["from"])
                if all(v is None for v in pv):
                    continue
                lk = self.ensure(fk["table"], pv)
                if lk is None:
                    if all(not self.ls.notnull(t, c) for c in fk["from"]):
                        for c in fk["from"]:
                            vals[c] = None
                        self.unlinked[f"{t}.{'+'.join(fk['from'])}"] += 1
                        continue
                    self.failed.add(mk)
                    self.problems.append({"tabella": t, "chiave": list(key),
                                          "motivo": f"manca la riga collegata in {fk['table']}"})
                    return None
                for c, v in zip(fk["from"], lk):
                    vals[c] = v
            for cols in self.ls.uniques(t):                    # same thing once the links are translated
                k = self.local_key(t, cols, tuple(vals.get(c) for c in cols))
                if k:
                    self.map[mk] = k
                    self.same[t] += 1
                    return k
            names = list(vals)
            try:
                self.local.execute(f"INSERT INTO {t} ({_q(names)}) VALUES ({', '.join('?' * len(names))})",
                                   [vals[n] for n in names])
            except sqlite3.IntegrityError as exc:
                self.failed.add(mk)
                self.problems.append({"tabella": t, "chiave": list(key), "motivo": str(exc)})
                return None
            lk = tuple(vals[c] for c in self.ls.pk(t))
            self.map[mk] = lk
            self.new[t] += 1
        finally:
            self.busy.discard(mk)
        for ct, col in CHILDREN.get(t, []):
            pkc = self.ss.pk(ct)
            for cr in self.share.execute(f"SELECT {_q(pkc)} FROM {ct} WHERE \"{col}\" = ?", (key[0],)).fetchall():
                self.ensure(ct, tuple(cr))
        return lk

    def run(self) -> None:
        for t, _where_sql, group, cond in ROOTS:
            pkc = self.ss.pk(t)
            rows = self.share.execute(f"SELECT * FROM {t}").fetchall()
            if group is None:
                for r in rows:
                    self.ensure(t, tuple(r[c] for c in pkc))
                continue
            groups: dict[tuple, list] = defaultdict(list)
            for r in rows:
                groups[tuple(r[c] for c in group)].append(r)
            fk_of = {c: fk for fk in self.ls.fks(t) for c in fk["from"]}
            for gkey, members in groups.items():
                local_vals = []
                for c, v in zip(group, gkey):
                    fk = fk_of.get(c)
                    if fk is None or v is None:
                        local_vals.append(v)
                        continue
                    lk = self.ensure(fk["table"], (v,))
                    local_vals.append(lk[0] if lk else None)
                if any(v is None for v in local_vals):
                    self.problems.append({"tabella": t, "gruppo": list(gkey), "motivo": "manca la riga collegata"})
                    continue
                extra = f" AND {cond}" if cond else ""
                if self.local.execute(f"SELECT 1 FROM {t} WHERE {_where(group)}{extra} LIMIT 1",
                                      local_vals).fetchone():
                    self.kept[t] += 1
                    continue
                for r in members:
                    self.ensure(t, tuple(r[c] for c in pkc))


def merge_package(local: sqlite3.Connection, package: Path, *, dry_run: bool = False) -> dict:
    """Merge a package into `local` in one transaction (rolled back with dry_run)."""
    with tempfile.TemporaryDirectory() as tmp:
        manifest, share_path = read_package(package, Path(tmp))
        share = db.connect(share_path)
        try:
            unknown = db.unknown_applied(share)
            if unknown:
                raise SharingError("Il pacchetto viene da una versione più recente di ORPHEUS "
                                   f"(migrazioni {', '.join(unknown)}): aggiorna ORPHEUS su questo PC e riprova.", code=4)
            db.migrate(share)                                 # an older package gets this PC's schema first
            m = Merger(local, share)
            local.execute("BEGIN IMMEDIATE")
            try:
                local.execute("PRAGMA defer_foreign_keys = ON")   # rows go in parent-first, but checked at the end
                m.run()
                broken = local.execute("PRAGMA foreign_key_check").fetchall()
                if broken:
                    raise SharingError(f"unione interrotta: {len(broken)} collegamenti rotti, nessun dato scritto "
                                       f"(prima: {tuple(broken[0])})")
                result = {
                    "pacchetto": str(package), "creato_il": manifest.get("creato_il"),
                    "versione_orpheus_pacchetto": manifest.get("versione_orpheus"),
                    "nuove": dict(sorted(m.new.items())), "gia_presenti": dict(sorted(m.same.items())),
                    "tenute_le_tue": dict(sorted(m.kept.items())),
                    "collegamenti_facoltativi_tolti": dict(sorted(m.unlinked.items())),
                    "problemi": m.problems[:50], "problemi_totali": len(m.problems),
                    "riepilogo": {"riviste_nuove": m.new.get("journal", 0),
                                  "schede_riviste_nuove": m.new.get("journal_card", 0),
                                  "articoli_nuovi": m.new.get("article", 0),
                                  "schede_articoli_nuove": m.new.get("article_card", 0),
                                  "pezzi_di_testo_nuovi": m.new.get("segment", 0),
                                  "voci_metodi_nuove": m.new.get("text_method_item", 0),
                                  "schede_gia_presenti_tenute_le_tue": m.kept.get("journal_card", 0)
                                  + m.kept.get("article_card", 0)},
                    "prova": dry_run,
                }
                wrote = sum(m.new.values()) > 0
                if dry_run or not wrote:
                    local.execute("ROLLBACK")
                else:
                    audit.record(local, actor="cli", action="condivisione_unisci",
                                 payload={"pacchetto": Path(package).name, "sha256_db": manifest.get("sha256_db"),
                                          "nuove": result["nuove"], "tenute_le_tue": result["tenute_le_tue"],
                                          "problemi": len(m.problems)}, in_transaction=True)
                    local.execute("COMMIT")
                result["scritto"] = wrote and not dry_run
                return result
            except BaseException:
                if local.in_transaction:
                    local.execute("ROLLBACK")
                raise
        finally:
            share.close()


def newest_package(home: Path) -> Path | None:
    folder = Path(home) / FOLDER_IN
    found = sorted(folder.glob("*.zip"), key=lambda p: p.stat().st_mtime) if folder.is_dir() else []
    return found[-1] if found else None
