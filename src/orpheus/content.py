"""What an article PDF contains. User's decision of 2026-10-10: ORPHEUS learns only from COMPLETE articles.

Classes (documents of kind 'article'; guidelines and other kinds are not classified):
  abstract di congresso  a conference-abstract page holding several abstracts (quality.is_conference_abstract),
                         a longer PDF of a congress supplement (its contents or author index: supplement volume
                         "22S (2016)" in the running head) or a collection of abstracts ("Abstracts of the Second
                         Congress of …", from 2026-10-14, real FAS PDFs),
                         or a PDF of at most 2 pages from a conference supplement: Crossref page "S53"/"e61",
                         a supplement page number at the top ("S230"), a supplement volume in the header
                         ("20S (2017)"), an abstract code ("SMA-2024-228",
                         "2025SMA-401") or an Abstracts/Conference/Congress/Symposium heading;
  pagina singola         any other PDF of at most 2 pages (editorial, letter to the editor, short item);
  articolo completo      everything else: 3 pages or more.
Derived from the 135 real JSAMS PDFs (2026-10-10): 56 pages with several abstracts, 10 supplement pages with one
abstract, 1 editorial, 1 letter, 67 complete articles (4 of them "Journal Pre-proof" without Crossref pages).
None of the 1-2 page PDFs is the first page of a longer article: verify-dois rejects that case ("lunghezza diversa").
Computed from the database, never stored: the same database gives the same classes on every PC.

Access level (user's decision of 2026-10-11, proposal A): an `article` whose linked PDFs are all NOT complete
articles has access_level 'abstract_only'; with at least one complete article PDF, 'full_text'
(align_access, run by `verify-dois` and by `orpheus allinea-accesso`). Only these two values are ever changed;
nothing is deleted.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path, PurePosixPath

from . import audit
from .db import transaction
from .quality import _ABSTRACT_HEADER, is_conference_abstract
from .verify_doi import latest_checks

COMPLETE = "articolo completo"
CONFERENCE = "abstract di congresso"
SHORT = "pagina singola"
CLASSES = (COMPLETE, CONFERENCE, SHORT)
SEPARATED_DIR = "non_completi"                                          # under ORPHEUS_HOME
FOLDERS = {CONFERENCE: "abstract_congresso", SHORT: "pagina_singola"}   # under ORPHEUS_HOME/non_completi/
README = """ORPHEUS - PDF che non sono articoli completi
============================================

ORPHEUS impara solo dagli articoli completi (decisione del 2026-10-10). Dopo la lettura dei PDF
(leggi_pdf.cmd) i file che non lo sono vengono spostati qui dalla inbox:

abstract_congresso\\   pagine di abstract di congresso (anche con altri abstract sulla stessa pagina)
pagina_singola\\       PDF di 1-2 pagine: editoriali, lettere, testi brevi

Restano nel database (non si perde nulla) ma sono esclusi dalla ricerca e dal benchmark.
Non vengono riletti. Nessun file viene cancellato. La regola e' in src/orpheus/content.py.
"""

_MAX_SHORT_PAGES = 2
_SUPPLEMENT_PAGE = re.compile(r"^[SsEe]\d")                     # Crossref "S53", "e61"
_SUPPLEMENT_VOLUME = re.compile(r"\b\d{1,3}S\s*\((?:19|20)\d\d\)")  # "20S (2017)" in the running head
_ABSTRACT_CODE = re.compile(r"SMA-\d")                          # Sports Medicine Australia abstract codes
# "Abstracts of the Second Congress of …", "Abstracts of free papers presented at … Meeting" (real FAS PDFs, 2026-10-14)
_ABSTRACT_COLLECTION = re.compile(
    r"\bAbstracts\s+(?:of|from)\b[^\n]{0,120}?\b(?:Congress|Conference|Meeting|Symposium)\b", re.IGNORECASE)


def classify(kind: str, page_count: int, page1: str, dois, doi_candidate: str | None,
             crossref_page: str | None) -> tuple[str | None, str]:
    """(class, reason). class is None for documents that are not articles."""
    if kind != "article":
        return None, "non è un articolo"
    if is_conference_abstract(kind, page_count, page1, dois, doi_candidate):
        return CONFERENCE, "pagina con più abstract"
    head = page1[:400]
    if page_count > _MAX_SHORT_PAGES:
        # longer PDFs of a congress supplement (real PDFs, 2026-10-14): a collection of abstracts or its contents
        # (FAS, 6-21 pages: "Abstracts of free papers presented at … Congress"), or one long abstract under the running
        # head "Abstracts / Journal … 12S (2009)" (JSAMS, 4 pages, 2 of them almost empty). A supplement volume alone
        # is not enough (a complete article can be published in a supplement), and "Contents lists available at
        # ScienceDirect" is printed on every Elsevier article. Only the running head and the heading count.
        if _ABSTRACT_COLLECTION.search(head):
            return CONFERENCE, f"raccolta di abstract di congresso ({page_count} pagine)"
        if _SUPPLEMENT_VOLUME.search(head) and re.search(r"\bAbstracts\b", head):
            return CONFERENCE, (f"abstract di un supplemento di congresso ({_SUPPLEMENT_VOLUME.search(head).group(0)}, "
                                f"{page_count} pagine)")
        return COMPLETE, f"{page_count} pagine"
    if crossref_page and _SUPPLEMENT_PAGE.match(crossref_page):
        return CONFERENCE, f"supplemento di congresso (pagina Crossref {crossref_page})"
    printed = re.match(r"\s*([Se]\d{1,4})\b", head)
    if printed:
        return CONFERENCE, f"supplemento di congresso (pagina {printed.group(1)} in testa)"
    if _SUPPLEMENT_VOLUME.search(head):
        return CONFERENCE, f"supplemento di congresso ({_SUPPLEMENT_VOLUME.search(head).group(0)})"
    if _ABSTRACT_CODE.search(head) or _ABSTRACT_HEADER.search(head):
        return CONFERENCE, "codice o intestazione di abstract di congresso"
    return SHORT, f"{page_count} pagin{'a' if page_count == 1 else 'e'} (editoriale, lettera o testo breve)"


def classify_all(conn: sqlite3.Connection) -> dict[str, dict]:
    """{document_id: {"classe", "motivo"}} for every article document."""
    checks = latest_checks(conn)
    out = {}
    for d in conn.execute("SELECT id, kind, page_count, doi_candidate FROM document WHERE kind = 'article' ORDER BY id"):
        page1 = conn.execute("SELECT text FROM document_page WHERE document_id = ? ORDER BY page_no LIMIT 1",
                             (d["id"],)).fetchone()
        dois = conn.execute("SELECT page_no, doi FROM document_doi WHERE document_id = ?", (d["id"],)).fetchall()
        page = None
        c = checks.get(d["id"])
        if c is not None and c["details"]:
            page = (json.loads(c["details"]).get("crossref") or {}).get("page")
        cls, why = classify(d["kind"], d["page_count"], page1["text"] if page1 else "", dois, d["doi_candidate"], page)
        out[d["id"]] = {"classe": cls, "motivo": why}
    return out


def not_complete(conn: sqlite3.Connection) -> set[str]:
    """Ids of article documents that are not complete articles (left out of searches and of the benchmark)."""
    return {i for i, c in classify_all(conn).items() if c["classe"] != COMPLETE}


FULL_TEXT, ABSTRACT_ONLY = "full_text", "abstract_only"


def align_access(conn: sqlite3.Connection, *, actor: str = "orpheus allinea-accesso") -> dict:
    """Set article.access_level from the content of its PDFs (decision A of 2026-10-11): 'abstract_only' when
    every linked article PDF is a conference abstract or a single page, 'full_text' when at least one is a
    complete article. Articles with other levels (metadata_only, not_accessible) or without PDFs are left alone.
    One transaction, one audit event per changed article. Running it again changes nothing."""
    classes = classify_all(conn)
    links: dict[str, list[str]] = {}
    for d in conn.execute("SELECT id, article_id FROM document WHERE kind = 'article' AND article_id IS NOT NULL"):
        links.setdefault(d["article_id"], []).append(d["id"])
    changed = []
    with transaction(conn):
        for art_id in sorted(links):
            row = conn.execute("SELECT access_level FROM article WHERE id = ?", (art_id,)).fetchone()
            if row is None or row["access_level"] not in (FULL_TEXT, ABSTRACT_ONLY):
                continue
            docs = sorted(links[art_id])
            kinds = sorted({(classes.get(d) or {}).get("classe") or "" for d in docs})
            target = FULL_TEXT if COMPLETE in kinds else ABSTRACT_ONLY
            if row["access_level"] == target:
                continue
            conn.execute("UPDATE article SET access_level = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                         "WHERE id = ?", (target, art_id))
            audit.record(conn, actor=actor, action="align_access_level", entity_type="article", entity_id=art_id,
                         payload={"da": row["access_level"], "a": target, "documenti": docs, "contenuto": kinds,
                                  "regola": "content.align_access (decisione A del 2026-10-11)"}, in_transaction=True)
            changed.append({"articolo": art_id, "da": row["access_level"], "a": target, "contenuto": kinds})
    levels = dict(conn.execute("SELECT access_level, count(*) FROM article GROUP BY access_level").fetchall())
    return {"cambiati": changed, "per_livello": {k: levels.get(k, 0) for k in sorted(levels)}}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def separate(conn: sqlite3.Connection, home: Path, inbox_dir: str = "inbox") -> dict:
    """Move the PDFs that are not complete articles out of the inbox, into ORPHEUS_HOME/non_completi/<folder>/.

    Only files still where they were read (inside the inbox), with the same sha256 as then; never overwrites;
    nothing is deleted and the database is not changed (the document keeps its original path as provenance).
    Files read from outside the inbox (ingest-pdf PATH) are left alone.
    """
    home = Path(home)
    moved, problems = [], []
    for doc_id, c in classify_all(conn).items():
        if c["classe"] == COMPLETE:
            continue
        row = conn.execute("SELECT original_path, file_sha256 FROM document WHERE id = ?", (doc_id,)).fetchone()
        rel = PurePosixPath(row["original_path"].replace("\\", "/"))
        if rel.is_absolute() or ":" in rel.parts[0] or rel.parts[0] != inbox_dir:
            continue
        src = home.joinpath(*rel.parts)
        if not src.is_file():
            continue                                   # already moved, or removed by the user
        item = {"file": src.name, "contenuto": c["classe"], "motivo": c["motivo"]}
        try:
            if _sha256(src) != row["file_sha256"]:
                problems.append({**item, "problema": "il file è cambiato da quando è stato letto: non spostato"})
                continue
            dest = home / SEPARATED_DIR / FOLDERS[c["classe"]] / src.name
            if dest.exists():
                problems.append({**item, "problema": f"esiste già {SEPARATED_DIR}/{FOLDERS[c['classe']]}/{src.name}: non spostato"})
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            readme = home / SEPARATED_DIR / "LEGGIMI.txt"
            if not readme.exists():
                readme.write_text(README, encoding="utf-8-sig")
            src.replace(dest)
        except OSError as exc:                         # e.g. the PDF is open in another program
            problems.append({**item, "problema": f"non spostato: {exc}"})
            continue
        moved.append({**item, "cartella": f"{SEPARATED_DIR}\\{FOLDERS[c['classe']]}"})
    moved.sort(key=lambda m: (m["cartella"], m["file"]))
    return {"spostati": moved, "problemi": problems}
