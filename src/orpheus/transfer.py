"""Move the knowledge base between PCs without cloud sync — ADR-0007.

export  → one .zip file: a consistent copy of the database, the archived PDFs it refers to,
          and manifest.json with identity, audit head and the sha256 of every member.
          Export only READS the database (nothing is written into it).
import  → brings a bundle in only when that cannot lose work:
            no local database                 → install
            bundle identical to local         → nothing to do (missing PDFs are still copied)
            same bundle imported again        → nothing to do ("già importato")
            bundle = local + later work       → fast-forward (local copy backed up first)
            bundle older than local           → REFUSED
            bundle and local diverged         → REFUSED (reports the last common point)
            bundle of a different database    → REFUSED, always
          The bundle is also checked against this PC's memory (state.json, local backups).
          --discard-local-changes overrides "older"/"diverged" deliberately, after a backup.
          A local backup (.db) can be imported the same way, with the same checks (restore).
import --pdf-only → copy into the archive only the PDFs that are missing; never touches the
          database (archive files are named by their sha256 and verified, so adding them is always safe).
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import tempfile
import time
import zipfile
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from . import __version__, audit, backups, db, lineage
from .localstate import LocalState, utc_now

FORMAT = "orpheus-export/1"
MANIFEST = "manifest.json"
DB_MEMBER = "orpheus.db"
HOME_PREFIX = "home/"
SQLITE_MAGIC = b"SQLite format 3\x00"


class TransferError(Exception):
    def __init__(self, message: str, *, code: int = 5, **details):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details


def _copy(src, dst, chunk: int = 1 << 20) -> tuple[str, int]:
    h, n = hashlib.sha256(), 0
    for block in iter(lambda: src.read(chunk), b""):
        h.update(block)
        dst.write(block)
        n += len(block)
    return h.hexdigest(), n


def safe_home_path(home: Path, rel: str) -> Path:
    """Resolve a home-relative POSIX path, refusing anything that could escape ORPHEUS_HOME."""
    p = PurePosixPath(rel)
    if not rel or "\\" in rel or ":" in rel or p.is_absolute() or ".." in p.parts:
        raise TransferError(f"percorso non valido nel pacchetto: {rel!r}")
    return Path(home).joinpath(*p.parts)


def _zipinfo(name: str, compress: int) -> zipfile.ZipInfo:
    zi = zipfile.ZipInfo(name, date_time=time.localtime()[:6])
    zi.compress_type = compress
    return zi


# ---------------------------------------------------------------- export

def export_bundle(conn, *, home: Path, dest_dir: Path, include_archive: bool = True) -> dict:
    h = lineage.head(conn)
    if h.database_id is None:
        raise TransferError("Database senza identità: esegui `orpheus migrate` prima di esportare.")
    chain = audit.verify_chain(conn)
    if not chain.ok:
        raise TransferError(f"Registro di audit non valido (evento {chain.first_bad_seq}): esportazione rifiutata.", code=2)
    integ = db.integrity_check(conn)
    if integ["integrity"] != "ok" or integ["foreign_key_violations"]:
        raise TransferError(f"Database non integro ({integ}): esportazione rifiutata.", code=2)

    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    name = f"orpheus-export-{h.database_id[:8]}-{h.seq:07d}-{now.strftime('%Y%m%dT%H%M%SZ')}.zip"
    final = dest_dir / name
    partial = dest_dir / (name + ".partial")
    files, missing = [], []
    try:
        with tempfile.TemporaryDirectory(prefix="orpheus-export-") as td:
            copy_path = db.backup(conn, Path(td) / DB_MEMBER)
            copy = db.connect_readonly(copy_path)
            try:
                copied_head, migrations = lineage.head(copy), db.applied_versions(copy)
            finally:
                copy.close()
            if copied_head != h:
                raise TransferError("Il database è cambiato durante l'esportazione: riprova.")
            with zipfile.ZipFile(partial, "w", allowZip64=True) as zf:
                with copy_path.open("rb") as src, zf.open(_zipinfo(DB_MEMBER, zipfile.ZIP_DEFLATED), "w", force_zip64=True) as dst:
                    db_sha, db_size = _copy(src, dst)
                if include_archive:
                    rows = conn.execute("""SELECT DISTINCT storage_path, content_sha256 FROM source_snapshot
                                           WHERE storage_path IS NOT NULL ORDER BY storage_path""").fetchall()
                    for rel, expected in rows:
                        try:
                            src_path = safe_home_path(home, rel)
                        except TransferError:
                            missing.append({"path": rel, "motivo": "percorso fuori da ORPHEUS_HOME"})
                            continue
                        if not src_path.is_file():
                            missing.append({"path": rel, "motivo": "file non trovato"})
                            continue
                        with src_path.open("rb") as src, zf.open(_zipinfo(HOME_PREFIX + rel, zipfile.ZIP_STORED), "w", force_zip64=True) as dst:
                            sha, size = _copy(src, dst)
                        if sha != expected:
                            raise TransferError(f"File dell'archivio danneggiato (impronta diversa): {rel}", code=2)
                        files.append({"path": rel, "sha256": sha, "size": size})
                manifest = {
                    "format": FORMAT, "orpheus_version": __version__,
                    "database_id": h.database_id, "head": {"seq": h.seq, "hash": h.hash},
                    "exported_at": utc_now(), "exported_from": platform.node(),
                    "migrations": migrations,
                    "db": {"member": DB_MEMBER, "sha256": db_sha, "size": db_size},
                    "archive": {"included": include_archive, "files": files, "missing": missing},
                }
                zf.writestr(MANIFEST, json.dumps(manifest, indent=2, ensure_ascii=False))
        os.replace(partial, final)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return {"pacchetto": str(final), "posizione": h.seq, "database_id": h.database_id,
            "file_archivio": len(files), "mancanti": missing, "dimensione_byte": final.stat().st_size}


# ---------------------------------------------------------------- import

def read_manifest(bundle: Path) -> dict:
    bundle = Path(bundle)
    if not bundle.is_file():
        raise TransferError(f"{bundle} non è un file: indica il pacchetto .zip creato da `orpheus export`.")
    try:
        with zipfile.ZipFile(bundle) as zf:
            names = set(zf.namelist())
            if MANIFEST not in names or DB_MEMBER not in names:
                raise TransferError(f"{Path(bundle).name} non è un pacchetto di esportazione ORPHEUS.")
            manifest = json.loads(zf.read(MANIFEST).decode("utf-8"))
    except zipfile.BadZipFile as exc:
        raise TransferError(f"{Path(bundle).name} non è un file zip valido ({exc}).") from exc
    if manifest.get("format") != FORMAT:
        raise TransferError(f"Formato di pacchetto non supportato: {manifest.get('format')!r}.")
    return manifest


def _refusal(rel: str, lh, bh, manifest: dict, common: int | None, ref_report) -> str:
    what = manifest.get("_what", "Il pacchetto")
    origin = f"{manifest.get('_verb', 'esportato')} il {manifest['exported_at'][:16].replace('T', ' ')} UTC da {manifest.get('exported_from') or 'PC sconosciuto'}"
    tail = (f" Nessuna modifica è stata fatta. Se vuoi davvero tenere {what.lower()} e scartare lo stato più recente, "
            "ripeti con --discard-local-changes (prima verrà fatto un backup del database locale, se esiste).")
    if rel == "other_behind":
        return (f"{what} (posizione #{bh.seq}, {origin}) è PIÙ VECCHIO del database locale (#{lh.seq}): "
                "importarlo cancellerebbe il lavoro più recente." + tail)
    if rel == "diverged":
        return (f"{what} (#{bh.seq}, {origin}) e il database locale (#{lh.seq}) hanno storie DIVERSE dal punto #{common}: "
                f"qui ci sono {lh.seq - common} operazioni che non ha, e ne ha {bh.seq - common} "
                "che qui mancano. ORPHEUS non unisce copie divergenti." + tail)
    return f"{what} non è compatibile con quanto questo PC ha già visto. " + ref_report.message + tail


def _is_sqlite(path: Path) -> bool:
    with Path(path).open("rb") as fh:
        return fh.read(16) == SQLITE_MAGIC


def _stage(source: Path, work: Path, state: LocalState, stack: ExitStack):
    """Put the incoming database in `work`; return (path, manifest, open zip or None)."""
    incoming = work / "incoming.db"
    if source.is_file() and _is_sqlite(source):
        shutil.copyfile(source, incoming)
        entry = next((e for e in state.index() if e["file"] == source.name), None)
        if entry and Path(state.backup_dir / entry["file"]).resolve() == source.resolve():
            with incoming.open("rb") as fh:
                if hashlib.sha256(fh.read()).hexdigest() != entry["sha256"]:
                    raise TransferError("Backup danneggiato: l'impronta non corrisponde a quella registrata quando è stato creato.", code=2)
        when = entry["created_at"] if entry else utc_now()
        manifest = {"format": "backup-db", "_what": "Il backup", "_verb": "creato", "exported_at": when,
                    "exported_from": (entry or {}).get("host") or "backup", "archive": {"files": []}}
        return incoming, manifest, None
    manifest = read_manifest(source)
    zf = stack.enter_context(zipfile.ZipFile(source))
    with zf.open(DB_MEMBER) as src, incoming.open("wb") as dst:
        sha, _ = _copy(src, dst)
    if sha != manifest["db"]["sha256"]:
        raise TransferError("Pacchetto danneggiato: l'impronta del database non corrisponde al manifest.", code=2)
    return incoming, manifest, zf


def import_bundle(bundle: Path, *, home: Path, db_rel: str, state: LocalState,
                  discard_local_changes: bool = False, actor: str = "cli") -> dict:
    bundle = Path(bundle)
    if not bundle.is_file():
        raise TransferError(f"{bundle} non è un file: indica il pacchetto .zip creato da `orpheus export` "
                            "oppure un backup .db della cartella locale.")
    db_path = Path(home) / db_rel
    db_path.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".orpheus-import-", dir=db_path.parent))
    local = None
    try:
        with ExitStack() as stack:
            incoming, manifest, zf = _stage(bundle, work, state, stack)

            inc = db.connect_readonly(incoming)
            try:
                integ = db.integrity_check(inc)
                if integ["integrity"] != "ok" or integ["foreign_key_violations"]:
                    raise TransferError(f"Il database nel pacchetto non è integro ({integ}).", code=2)
                if not audit.verify_chain(inc).ok:
                    raise TransferError("Il registro di audit nel pacchetto non è valido.", code=2)
                bh = lineage.head(inc)
                if bh.database_id is None:
                    raise TransferError("Il database da importare è di una versione precedente senza identità: non può essere importato.")
                if zf is not None and (bh.database_id, bh.seq, bh.hash) != (
                        manifest["database_id"], manifest["head"]["seq"], manifest["head"]["hash"]):
                    raise TransferError("Il manifest non corrisponde al database contenuto nel pacchetto.", code=2)
                unknown = sorted(set(db.applied_versions(inc)) - {m.version for m in db.discover()})
                if unknown:
                    raise TransferError(f"Il pacchetto è stato creato da una versione più recente di ORPHEUS "
                                        f"(migrazioni {', '.join(unknown)}): esegui `git pull` e reinstalla, poi riprova.")

                if db_path.exists():
                    local = db.connect(db_path)
                    lh = lineage.head(local)
                    if lh.database_id is None:
                        raise TransferError("Il database locale è di una versione precedente: esegui `orpheus migrate` e riprova.")
                    rel = lineage.relation(local, inc)
                    common = lineage.common_seq(local, inc) if rel == "diverged" else None
                    # The same bundle imported twice: locally there is nothing after it but import records.
                    later = {r[0] for r in local.execute("SELECT action FROM audit_event WHERE seq > ?", (bh.seq,))}
                    already_imported = rel == "other_behind" and later <= {"import"}
                else:
                    lh, rel, common, already_imported = None, "no_local", None, False
                ref = lineage.check(inc, state)
            finally:
                inc.close()

            if rel == "different_database":
                raise TransferError(f"Il pacchetto appartiene a un ALTRO database (id {bh.database_id[:8]}…) "
                                    f"rispetto a quello locale (id {lh.database_id[:8]}…). Nessuna modifica fatta.", code=4)
            aligned = rel == "identical" or already_imported
            blocked = None if aligned else (
                "older" if rel == "other_behind" else "diverged" if rel == "diverged" else (ref.status if ref.blocking else None))
            if blocked and not discard_local_changes:
                raise TransferError(_refusal(rel, lh, bh, manifest, common, ref), code=4,
                                    relazione=rel, ultimo_punto_comune=common, verifica_pc=ref.as_dict())

            copied, present = _copy_archive(zf, manifest, home) if zf is not None else (0, 0)
            if aligned:
                if local is not None:
                    local.close()
                    local = None
                return {"risultato": "già importato" if already_imported else "già allineato", "posizione": bh.seq,
                        "pdf_copiati": copied, "pdf_presenti": present}

            safety = None
            if local is not None:
                safety = backups.make_backup(local, state, reason="before_import")
                local.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                local.close()
                local = None
            _replace_db(incoming, db_path)

        new = db.connect(db_path)
        try:
            applied = db.migrate(new)
            audit.record(new, actor=actor, action="import", payload={
                "bundle": bundle.name, "source_kind": "backup" if zf is None else "export",
                "exported_from": manifest.get("exported_from"),
                "exported_at": manifest.get("exported_at"), "relation": rel,
                "previous_local_seq": lh.seq if lh else None, "bundle_seq": bh.seq,
                "discarded_local_changes": bool(blocked), "migrations_applied": applied, "pdf_copied": copied})
            nh = lineage.head(new)
            state.record_seen(nh, db_path, accept=bool(blocked))
        finally:
            new.close()
        result = {"risultato": "importato", "relazione": rel, "posizione": nh.seq, "pdf_copiati": copied,
                  "pdf_presenti": present}
        if safety:
            result["backup_prima_dell_import"] = str(state.backup_dir / safety["file"])
        if blocked:
            result["avviso"] = "Stato locale precedente scartato su tua richiesta; resta nel backup indicato."
        return result
    finally:
        if local is not None:
            local.close()
        shutil.rmtree(work, ignore_errors=True)


def _copy_archive(zf: zipfile.ZipFile, manifest: dict, home: Path) -> tuple[int, int]:
    names = set(zf.namelist())
    copied = present = 0
    for f in manifest.get("archive", {}).get("files", []):
        dest = safe_home_path(home, f["path"])
        if dest.is_file() and dest.stat().st_size == f["size"]:
            present += 1
            continue
        member = HOME_PREFIX + f["path"]
        if member not in names:
            raise TransferError(f"Pacchetto incompleto: manca {f['path']}.", code=2)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".partial")
        with zf.open(member) as src, tmp.open("wb") as dst:
            sha, _ = _copy(src, dst)
        if sha != f["sha256"]:
            tmp.unlink(missing_ok=True)
            raise TransferError(f"Pacchetto danneggiato: impronta diversa per {f['path']}.", code=2)
        os.replace(tmp, dest)
        copied += 1
    return copied, present


def _replace_db(incoming: Path, db_path: Path) -> None:
    try:
        for suffix in ("-wal", "-shm"):
            Path(str(db_path) + suffix).unlink(missing_ok=True)
        os.replace(incoming, db_path)
    except PermissionError as exc:
        raise TransferError("Impossibile sostituire il database: è aperto da un altro programma. "
                            "Chiudi gli altri programmi che lo usano e riprova.") from exc


def missing_archive_files(conn, home: Path) -> list[str]:
    """Archived files the database refers to that are not on disk (e.g. after a disk failure)."""
    out = []
    for (rel,) in conn.execute("SELECT DISTINCT storage_path FROM source_snapshot WHERE storage_path IS NOT NULL"):
        try:
            if not safe_home_path(home, rel).is_file():
                out.append(rel)
        except TransferError:
            out.append(rel)
    return sorted(out)


def import_pdfs_only(bundle: Path, *, home: Path, db_rel: str) -> dict:
    """Copy into the archive the PDFs of a bundle that are missing here. The database is not touched."""
    manifest = read_manifest(Path(bundle))
    db_path = Path(home) / db_rel
    if not db_path.exists():
        raise TransferError("Nessun database locale: prima importa un pacchetto o ripristina un backup, poi i PDF.")
    local = db.connect(db_path)
    try:
        local_id = lineage.database_id(local)
        if local_id != manifest["database_id"]:
            raise TransferError("Il pacchetto appartiene a un ALTRO database: nessun PDF copiato.", code=4)
        with zipfile.ZipFile(bundle) as zf:
            copied, present = _copy_archive(zf, manifest, home)
        still = missing_archive_files(local, home)
    finally:
        local.close()
    return {"risultato": "pdf copiati", "pdf_copiati": copied, "pdf_presenti": present,
            "pdf_ancora_mancanti": len(still), "esempi_mancanti": still[:20]}
