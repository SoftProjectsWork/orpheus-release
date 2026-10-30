"""Standard layout of the data folder (ORPHEUS_HOME) — see RUNBOOK.md.

    ORPHEUS_HOME/
      LEGGIMI.txt
      riviste_prioritarie.csv                    one row per journal; `sigla` = folder name
      inbox/
        riviste/<sigla>/linee_guida/**.pdf        author guidelines, policies, scope pages
        riviste/<sigla>/articoli/**.pdf           articles published in that journal
        articoli_vari/**.pdf                      any other article
      manoscritti/*.docx                          the user's manuscripts (T-037; never on GitHub)
      data/                                       managed by ORPHEUS only
        orpheus.db
        archive/

The folder a PDF sits in is recorded as a *hint* (document.journal_hint); the journal is
confirmed later from ISSN/DOI, never assumed from the folder name alone.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from pathlib import Path

from .config import Storage
from .identifiers import is_valid_issn

JOURNALS_DIR = "riviste"
MISC_DIR = "articoli_vari"
GUIDELINES_DIR = "linee_guida"
ARTICLES_DIR = "articoli"
README_NAME = "LEGGIMI.txt"
MANUSCRIPTS_DIR = "manoscritti"          # the user's manuscripts (.docx): only on this disk, never on GitHub (R10)
NEW_ARTICLES_DIR = "nuovi_articoli"      # T-041: new articles from the user's data (only on this disk, never on GitHub)
SIGLA_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$")
CSV_COLUMNS = ["sigla", "titolo", "issn_print", "issn_online", "sito_ufficiale", "url_istruzioni_autori", "priorita", "note"]

README_TEXT = """ORPHEUS — cartella dei dati
===========================

Questa cartella contiene i dati di ORPHEUS. Il codice NON sta qui: è installato su ogni PC
(es. C:\\ORPHEUS\\orpheus). Il file .env di ogni PC indica questa cartella con ORPHEUS_HOME.

COSA METTI TU
-------------
riviste_prioritarie.csv
    Una riga per rivista. Colonne: sigla; titolo; issn_print; issn_online; sito_ufficiale;
    url_istruzioni_autori; priorita; note. Si apre con Excel. La "sigla" è un nome breve senza
    spazi (lettere, numeri, - e _), per esempio J-BIOMECH, e deve coincidere con il nome della
    cartella della rivista qui sotto.

inbox\\riviste\\<sigla>\\linee_guida\\
    Istruzioni per gli autori, policy (IA, dati, etica), pagine di scope, salvate in PDF.
    Metti la data nel nome del file, per esempio 2026-10-09_istruzioni_autori.pdf: quando
    la rivista le aggiorna, aggiungi il nuovo PDF senza cancellare il vecchio.

inbox\\riviste\\<sigla>\\articoli\\
    Articoli pubblicati da quella rivista. Puoi creare sottocartelle (per anno, per tema).

inbox\\articoli_vari\\
    Articoli di qualsiasi rivista. Dopo la verifica del DOI ORPHEUS riconosce la rivista (se manca, la aggiunge
    all'elenco) e sposta il PDF in riviste\\<sigla>\\articoli, con un registro in rapporti\\spostamenti. I PDF
    senza rivista restano qui: la scegli tu nel cruscotto (Articoli, "Da assegnare").

PDF messi altrove dentro inbox vengono segnalati e non letti.

manoscritti\\
    I tuoi manoscritti in Word (.docx), da controllare rispetto alle regole delle riviste con
    manoscritto.cmd o dal cruscotto (sezione Manoscritti). Restano su questo disco: non vanno
    mai su GitHub. I rapporti dei controlli sono in rapporti\\manoscritti.

nuovi_articoli\\<nome>\\
    Un articolo nuovo scritto dai tuoi dati (nuovo_articolo.cmd): studio.csv e risultati.csv li compili tu,
    il resto lo scrivono ORPHEUS e Claude, una sezione alla volta. Resta su questo disco, mai su GitHub.
Dopo aver aggiunto PDF: orpheus ingest-inbox (rilanciarlo è sicuro: i file già letti si saltano).

non_completi\\abstract_congresso\\, non_completi\\pagina_singola\\
    ORPHEUS impara solo dagli articoli completi: dopo la lettura (leggi_pdf.cmd) gli abstract di
    congresso e i PDF di 1-2 pagine (editoriali, lettere) vengono spostati qui dalla inbox.
    Restano nel database ma sono esclusi da ricerca e benchmark. Non vengono riletti.

COSA GESTISCE SOLO ORPHEUS — non aprire, spostare o modificare
-----------------------------------------------------------
data\\orpheus.db         la base di conoscenza (con orpheus.db-wal e -shm mentre è in uso)
data\\archive\\           copie dei PDF letti, nominate per impronta (sha256)

REGOLE
------
- Non mettere questa cartella dentro OneDrive, Dropbox o simili.
- Usala da un solo PC alla volta. Prima di staccare il disco: chiudi i comandi ORPHEUS,
  poi "Rimozione sicura dell'hardware".
- Ogni PC tiene i propri backup automatici del database nella sua cartella locale
  (ORPHEUS_LOCAL_DIR), non qui.
- Per portare i dati su un PC senza il disco: orpheus export / orpheus import.
"""


def create_data_home(home: Path, storage: Storage) -> list[str]:
    """Create the standard structure; never overwrites existing files. Returns what was created."""
    created = []
    for rel in (f"{storage.inbox_dir}/{JOURNALS_DIR}", f"{storage.inbox_dir}/{MISC_DIR}",
                str(Path(storage.db_path).parent), storage.archive_dir, MANUSCRIPTS_DIR, NEW_ARTICLES_DIR):
        path = home / rel
        if not path.exists():
            path.mkdir(parents=True)
            created.append(rel)
    csv_path = home / storage.journals_csv
    if not csv_path.exists():
        # Semicolon + UTF-8 BOM: what Excel in Italian opens correctly with a double click.
        csv_path.write_text(";".join(CSV_COLUMNS) + "\r\n", encoding="utf-8-sig")
        created.append(storage.journals_csv)
    readme = home / README_NAME
    if not readme.exists():
        readme.write_text(README_TEXT.replace("\n", "\r\n"), encoding="utf-8-sig")
        created.append(README_NAME)
    return created


@dataclass(frozen=True)
class InboxItem:
    path: Path
    kind: str
    journal_hint: str | None


def plan_inbox(home: Path, inbox_dir: str) -> tuple[list[InboxItem], list[dict]]:
    """Classify every PDF under the inbox; return (items to read, ignored files with the reason)."""
    root = home / inbox_dir
    if not root.is_dir():
        return [], [{"path": str(root), "motivo": "cartella inbox non trovata (esegui `orpheus init` o crea la cartella)"}]
    items, ignored = [], []
    for pdf in sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf"):
        parts = pdf.relative_to(root).parts
        shown = pdf.relative_to(home).as_posix()
        if parts[0] == MISC_DIR:
            items.append(InboxItem(pdf, "article", None))
        elif parts[0] == JOURNALS_DIR and len(parts) == 3:
            sigla = parts[1]
            ignored.append({"path": shown, "motivo": f"PDF direttamente nella cartella della rivista: spostalo in "
                            f"riviste/{sigla}/{GUIDELINES_DIR} (istruzioni per gli autori, policy) oppure in "
                            f"riviste/{sigla}/{ARTICLES_DIR} (articoli)"})
        elif parts[0] == JOURNALS_DIR and len(parts) >= 4 and parts[2] in (GUIDELINES_DIR, ARTICLES_DIR):
            sigla = parts[1]
            if not SIGLA_RE.match(sigla):
                ignored.append({"path": shown, "motivo": f"nome della cartella rivista non valido: {sigla!r} "
                                "(solo lettere, numeri, - e _, senza spazi)"})
                continue
            kind = "author_guidelines" if parts[2] == GUIDELINES_DIR else "article"
            items.append(InboxItem(pdf, kind, sigla))
        else:
            ignored.append({"path": shown, "motivo": "fuori dalle cartelle previste: usa riviste/<sigla>/linee_guida, "
                            "riviste/<sigla>/articoli oppure articoli_vari"})
    return items, ignored


def ensure_journal_folders(home: Path, inbox_dir: str, siglas: list[str]) -> list[str]:
    """Create riviste/<sigla>/linee_guida and /articoli for every valid sigla of the journal list.
    Never touches existing folders or files; returns what was created (relative to home)."""
    created = []
    for sigla in siglas:
        if not SIGLA_RE.match(sigla or ""):
            continue
        for sub in (GUIDELINES_DIR, ARTICLES_DIR):
            folder = home / inbox_dir / JOURNALS_DIR / sigla / sub
            if not folder.exists():
                folder.mkdir(parents=True)
                created.append(folder.relative_to(home).as_posix())
    return created


def shown_path(home: Path, path: Path) -> str:
    """Path as shown to the user: relative to the data folder when inside it."""
    try:
        return Path(path).resolve().relative_to(Path(home).resolve()).as_posix()
    except ValueError:
        return str(path)


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def read_journal_list(path: Path) -> tuple[list[dict], list[str]]:
    """Read riviste_prioritarie.csv as saved by Excel (';' or ',' separated, UTF-8 or Windows-1252)."""
    if not path.exists():
        return [], [f"{path.name} non trovato"]
    try:
        raw = path.read_bytes()
    except OSError as exc:  # e.g. kept locked by Excel on Windows
        return [], [f"impossibile leggere {path.name} ({exc.__class__.__name__}): se è aperto in Excel, chiudilo e riprova"]
    text = _decode(raw)
    lines = text.splitlines()
    if not lines or not lines[0].strip():
        return [], [f"{path.name} è vuoto"]
    first = lines[0]
    delimiter = ";" if first.count(";") >= max(first.count(","), 1) else ("\t" if "\t" in first else ",")
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    reader.fieldnames = [(f or "").strip().lower() for f in reader.fieldnames or []]
    problems = [f"colonna mancante: {c}" for c in ("sigla", "titolo") if c not in reader.fieldnames]
    if problems:
        return [], problems
    rows, seen = [], set()
    for line_no, row in enumerate(reader, start=2):
        values = {k: (v or "").strip() for k, v in row.items() if k}
        if not any(values.values()):
            continue
        sigla = values.get("sigla", "")
        if not sigla:
            problems.append(f"riga {line_no}: sigla mancante")
            continue
        if not SIGLA_RE.match(sigla):
            problems.append(f"riga {line_no}: sigla {sigla!r} non valida (solo lettere, numeri, - e _, senza spazi)")
        if sigla.lower() in seen:
            problems.append(f"riga {line_no}: sigla {sigla!r} ripetuta")
        seen.add(sigla.lower())
        for col in ("issn_print", "issn_online"):
            value = values.get(col, "")
            if value and not is_valid_issn(value):
                problems.append(f"riga {line_no}: {col} {value!r} non valido (controlla la cifra finale)")
        rows.append(values)
    return rows, problems


# ---------------------------------------------------------------- T-054 passo 2: where a PDF is now

SEPARATED_DIR = "non_completi"           # content.SEPARATED_DIR (kept here to avoid an import cycle)
DUPLICATES_DIR = "doppioni"              # inbox/doppioni: a copy with the same fingerprint as a file already in place
LOCATOR_INDEX = "pdf_posizioni.json"     # in the PC's local folder: {path: [size, mtime_ns, sha256]}


def _sha256(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class PdfLocator:
    """Finds the PDF of a document where it is NOW (rules locate-v1).

    `original_path` stays the provenance in the database (no migration). A PDF moved by the user or by ORPHEUS
    (articoli_vari → riviste/<sigla>/articoli, non_completi, a renamed journal folder) is found again by its fingerprint:
      1. original_path, if the file is there with the same SHA-256;
      2. a file with the same name under inbox/ and non_completi/ with the same SHA-256;
      3. any PDF under inbox/ and non_completi/ with the same SHA-256.
    The fingerprints are kept in the PC's local folder (path, size, modification time) and recomputed only for files
    that changed. A file with the same name but another fingerprint is NOT the document ("non trovato")."""

    def __init__(self, home: Path, inbox_dir: str = "inbox", local_root: Path | None = None):
        self.home = Path(home)
        self.inbox_dir = inbox_dir
        self.index_path = Path(local_root) / LOCATOR_INDEX if local_root else None
        self._cache: dict | None = None
        self._by_sha: dict[str, list[Path]] | None = None
        self._dirty = False

    def _roots(self) -> list[Path]:
        return [self.home / self.inbox_dir, self.home / SEPARATED_DIR]

    def _load(self) -> dict:
        if self._cache is None:
            import json
            self._cache = {}
            if self.index_path is not None:
                try:
                    self._cache = json.loads(self.index_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    self._cache = {}
        return self._cache

    def sha_of(self, path: Path) -> str | None:
        cache = self._load()
        try:
            st = path.stat()
        except OSError:
            return None
        key = str(path)
        hit = cache.get(key)
        if hit and hit[0] == st.st_size and hit[1] == st.st_mtime_ns:
            return hit[2]
        try:
            sha = _sha256(path)
        except OSError:
            return None
        cache[key] = [st.st_size, st.st_mtime_ns, sha]
        self._dirty = True
        return sha

    def _index(self) -> dict[str, list[Path]]:
        if self._by_sha is None:
            self._by_sha = {}
            seen = set()
            for root in self._roots():
                if not root.is_dir():
                    continue
                for p in sorted(root.rglob("*")):
                    if p.is_file() and p.suffix.lower() == ".pdf":
                        seen.add(str(p))
                        sha = self.sha_of(p)
                        if sha:
                            self._by_sha.setdefault(sha, []).append(p)
            cache = self._load()
            for k in [k for k in cache if k not in seen]:
                del cache[k]
                self._dirty = True
            self.save()
        return self._by_sha

    def refresh(self) -> None:
        """Forget the file list (after moving files); the fingerprints of unchanged files stay."""
        self._by_sha = None

    def save(self) -> None:
        if not self._dirty or self.index_path is None:
            return
        import json
        import os
        try:
            self.index_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.index_path.with_name(self.index_path.name + ".tmp")
            tmp.write_text(json.dumps(self._cache), encoding="utf-8")
            os.replace(tmp, self.index_path)
            self._dirty = False
        except OSError:
            pass

    def original(self, original_path: str | None) -> Path | None:
        if not original_path:
            return None
        p = Path(original_path.replace("\\", "/"))
        return p if p.is_absolute() else self.home.joinpath(*p.parts)

    def find(self, original_path: str | None, sha: str | None) -> Path | None:
        """The file of the document now, or None."""
        first = self.original(original_path)
        if first is not None and first.is_file() and (not sha or self.sha_of(first) == sha):
            return first
        if not sha:
            return None
        hits = self._index().get(sha) or []
        hits = [h for h in hits if h.is_file()]
        if not hits:
            return None
        name = first.name.lower() if first is not None else ""
        same_name = [h for h in hits if h.name.lower() == name]
        return (same_name or hits)[0]

    def where(self, path: Path | None) -> dict:
        """{"cartella": "riviste"/"articoli_vari"/"non_completi"/..., "sigla": ..., "sotto": "articoli"/"linee_guida"}"""
        if path is None:
            return {"cartella": None, "sigla": None, "sotto": None}
        try:
            rel = Path(path).resolve().relative_to(self.home.resolve()).parts
        except ValueError:
            return {"cartella": "fuori", "sigla": None, "sotto": None}
        if len(rel) >= 2 and rel[0] == self.inbox_dir:
            if rel[1] == JOURNALS_DIR and len(rel) >= 4:
                return {"cartella": JOURNALS_DIR, "sigla": rel[2], "sotto": rel[3]}
            return {"cartella": rel[1], "sigla": None, "sotto": None}
        return {"cartella": rel[0] if rel else None, "sigla": None, "sotto": None}


def locate_document(home: Path, doc, *, inbox_dir: str = "inbox", locator: PdfLocator | None = None) -> Path | None:
    """Where the PDF of a document row (original_path, file_sha256) is now; None when it is not on this disk."""
    loc = locator or PdfLocator(home, inbox_dir)
    return loc.find(doc["original_path"], doc["file_sha256"])
