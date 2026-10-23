"""Fase 2 — dated versions of the author guidelines (`journal_policy_version`), rules policy-v2 (T-050).

User's decisions: 2026-10-14 "Istantanee, poi schede" (first the versions of the guidelines, then the journal cards);
2026-10-19 "leggere più pdf per cartella" (policy-v2). Source: the guideline PDFs already read by leggi_pdf from
inbox/riviste/<SIGLA>/linee_guida (document.kind = 'author_guidelines'); nothing is downloaded. Deterministic, no LLM.
  - journal: the folder sigla -> its ISSNs in riviste_prioritarie.csv -> journal_issn (written by scrivi-riviste);
    without it the PDF is "non scritta" (run riviste.cmd after the journal is in the list);
  - fingerprint of a PDF (policy-v1, unchanged): the text of all pages WITHOUT the lines a browser adds when it prints a
    web page — "13/10/26, 15:21 <page title>" at the top and "<address> 1/16" at the bottom — with the whitespace
    collapsed: the same page printed on another day gives the same fingerprint, a changed text a new one;
  - date of a PDF: date and time printed by the browser (day/month/year as on an Italian PC; month/day only when the
    day/month reading falls after the PDF was read), in UTC with the time zone of the PC; without it, when it was read;
  - policy-v2: a version is the SET of the journal's current guideline PDFs. Each PDF belongs to a page — the address
    the browser printed at the bottom (without "#…"), or its file name when none is printed. For each page the newest
    print counts (an older print of the same page is "sostituita"); a PDF whose file is no longer in the folder is left
    out (when `home` is given). The instructions page (the address of the list, url_istruzioni_autori) comes first,
    then the others by file name. Fingerprint of the version: with one PDF the PDF's fingerprint (as policy-v1, so the
    versions written before stay current), with more PDFs the SHA-256 of their page keys and fingerprints;
  - a new row only when the fingerprint differs from the latest version of the journal (the latest = the last written).
    A new version of a journal that already has one supersedes it (supersedes_id) and is marked needs_human_review = 1;
    its PDFs are listed in journal_policy_document (migration 0013);
  - document.journal_id of the guideline PDFs is set when empty.
`plan` writes nothing; `write` = one transaction, one audit event per version ('write_policy_version') and one for
the links. Nothing is overwritten or deleted.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import datetime, timezone, tzinfo
from pathlib import Path

from . import audit
from .db import transaction
from .identifiers import is_valid_issn, normalize_issn
from .ids import new_id
from .journal_table import clean_url

RULES_VERSION = "policy-v2"
POLICY_TYPE = "author_guidelines"
_HEADER = re.compile(r"^\s*(\d{1,2})/(\d{1,2})/(\d{2}|\d{4}),? (\d{1,2}):(\d{2})(?: ?([AaPp])\.? ?[Mm]\.?)?\s+\S.*$")
_FOOTER = re.compile(r"^\s*((?:https?|file)://\S+)\s+(\d{1,4})/(\d{1,4})\s*$")


def _lines(page: str) -> list[str]:
    return [ln.rstrip() for ln in (page or "").splitlines()]


def browser_lines(pages: list[str]) -> tuple[set[str], str | None, tuple | None]:
    """(lines added by the browser's print, printed address, (d, m, y, H, M) of the first header).
    Header: matches _HEADER and is repeated on 2+ pages (or the PDF has one page). Footer: "<address> n/N" with
    N = number of pages."""
    total = len(pages)
    seen: dict[str, int] = {}
    for p in pages:
        for ln in set(_lines(p)):
            if _HEADER.match(ln):
                seen[ln] = seen.get(ln, 0) + 1
    headers = {ln for ln, n in seen.items() if n >= 2 or total == 1}
    footers, url = set(), None
    for p in pages:
        for ln in _lines(p):
            m = _FOOTER.match(ln)
            if m and int(m.group(3)) == total and 1 <= int(m.group(2)) <= total:
                footers.add(ln)
                url = url or m.group(1)
    first = None
    for p in pages:
        hit = next((ln for ln in _lines(p) if ln in headers), None)
        if hit:
            m = _HEADER.match(hit)
            first = (int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5)), m.group(6))
            break
    return headers | footers, url, first


def fingerprint(pages: list[str]) -> tuple[str, str, str | None, tuple | None]:
    """(sha256 of the content text, content text, printed address, first browser header)."""
    drop, url, header = browser_lines(pages)
    text = " ".join(" ".join(ln for ln in _lines(p) if ln not in drop) for p in pages)
    text = " ".join(text.split())
    return hashlib.sha256(text.encode("utf-8")).hexdigest(), text, url, header


def print_time(header: tuple | None, read_at: str | None, tz: tzinfo | None = None) -> str | None:
    """UTC ISO time of the browser's print line ('…Z'), or None. Day/month first (Italian PC); month/day only when
    day/month would fall after the PDF was read and month/day would not."""
    if not header:
        return None
    a, b, y, hh, mm, ampm = header
    y = y + 2000 if y < 100 else y
    if ampm:
        hh = hh % 12 + (12 if ampm.lower() == "p" else 0)
    read = _parse(read_at)

    def build(day: int, month: int) -> datetime | None:
        try:
            local = datetime(y, month, day, hh, mm)
        except ValueError:
            return None
        return (local.replace(tzinfo=tz) if tz else local.astimezone()).astimezone(timezone.utc)

    dm, md = build(a, b), build(b, a)
    choice = dm
    if dm is None or (read and dm > read and md is not None and md <= read):
        choice = md
    return choice.strftime("%Y-%m-%dT%H:%M:%SZ") if choice else None


def _as_printed(header: tuple | None) -> str | None:
    """The browser's date and time as the PDF prints them ("13/10/26, 16:23"), for the log."""
    if not header:
        return None
    a, b, y, hh, mm, ampm = header
    return f"{a:02d}/{b:02d}/{y:02d}, {hh}:{mm:02d}" + (f" {ampm.upper()}M" if ampm else "")


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def journal_by_sigla(conn: sqlite3.Connection, journal_list: list[dict]) -> dict[str, str]:
    """sigla (lower case) -> journal_id, through the ISSNs of the list and journal_issn."""
    by_issn = {r["issn"]: r["journal_id"] for r in conn.execute("SELECT issn, journal_id FROM journal_issn")}
    out = {}
    for row in journal_list:
        ids = {by_issn.get(normalize_issn(v)) for v in (row.get("issn_print"), row.get("issn_online"))
               if v and is_valid_issn(v)} - {None}
        if row.get("sigla") and len(ids) == 1:
            out[row["sigla"].lower()] = ids.pop()
    return out


def page_key(url: str | None, file_name: str) -> str:
    """What identifies the web page of a guideline PDF: the printed address without '#…' and final '/', else the file."""
    if url:
        clean = (clean_url(url) or url).split("#", 1)[0].rstrip("/.")
        if clean:
            return clean
    return "file:" + file_name.lower()


def version_fingerprint(docs: list[dict]) -> str:
    """One PDF: its own fingerprint (policy-v1). More PDFs: SHA-256 of 'page key TAB fingerprint' lines, sorted."""
    if len(docs) == 1:
        return docs[0]["impronta"]
    lines = sorted(f"{d['chiave']}\t{d['impronta']}" for d in docs)
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def latest_version(conn: sqlite3.Connection, journal_id: str) -> sqlite3.Row | None:
    """The current version of a journal's author guidelines: the last one written."""
    return conn.execute("""SELECT * FROM journal_policy_version WHERE journal_id = ? AND policy_type = ?
                           ORDER BY id DESC LIMIT 1""", (journal_id, POLICY_TYPE)).fetchone()


def version_documents(conn: sqlite3.Connection, version_id: str) -> list[dict]:
    """The PDFs of a version, in order: [{document_id, file_name, ord, page_key}]. Versions written before migration
    0013 (or with one PDF and no rows) -> the document of the version's source snapshot."""
    rows = []
    try:
        rows = conn.execute("""SELECT document_id, file_name, ord, page_key FROM journal_policy_document
                               WHERE policy_version_id = ? ORDER BY ord""", (version_id,)).fetchall()
    except sqlite3.OperationalError:          # database before migration 0013
        rows = []
    if rows:
        return [dict(r) for r in rows]
    d = conn.execute("""SELECT d.id, d.original_path FROM journal_policy_version v
                        JOIN document d ON d.source_snapshot_id = v.source_snapshot_id
                        WHERE v.id = ? AND d.kind = 'author_guidelines'""", (version_id,)).fetchone()
    if d is None:
        return []
    return [{"document_id": d["id"], "file_name": _file_name(d["original_path"]), "ord": 1, "page_key": None}]


def _file_name(path: str | None) -> str:
    return (path or "").replace("\\", "/").rsplit("/", 1)[-1]


def _present(home: Path | None, original: str | None) -> bool:
    if home is None or not original:
        return True
    p = Path(original)
    return (p if p.is_absolute() else Path(home) / original).is_file()


def plan(conn: sqlite3.Connection, journal_list: list[dict], *, tz: tzinfo | None = None,
         home: Path | None = None) -> dict:
    """What `write` would do: one row per guideline PDF ("linee_guida") and one per journal ("versioni"). Read-only."""
    journals = journal_by_sigla(conn, journal_list)
    listed_url = {r["sigla"].lower(): clean_url(r.get("url_istruzioni_autori")) for r in journal_list if r.get("sigla")}
    pdfs = []
    for d in conn.execute("""SELECT d.*, s.retrieved_at FROM document d JOIN source_snapshot s ON s.id = d.source_snapshot_id
                             WHERE d.kind = 'author_guidelines' ORDER BY d.original_path, d.id"""):
        pages = [r["text"] or "" for r in conn.execute(
            "SELECT text FROM document_page WHERE document_id = ? ORDER BY page_no", (d["id"],))]
        name = _file_name(d["original_path"])
        sigla = d["journal_hint"]
        jid = journals.get((sigla or "").lower())
        sha, text, url, header = fingerprint(pages)
        printed = print_time(header, d["ingested_at"], tz)
        item = {"file": name, "sigla": sigla, "document_id": d["id"], "source_snapshot_id": d["source_snapshot_id"],
                "journal_id": jid, "impronta": sha, "pagine": len(pages), "caratteri": len(text),
                "data_stampa": printed, "stampa_come_nel_pdf": _as_printed(header), "indirizzo": url,
                "chiave": page_key(url, name), "letto": d["ingested_at"] or "",
                "detected_at": printed or d["retrieved_at"] or d["ingested_at"],
                "collega_documento": jid is not None and d["journal_id"] is None}
        if url and listed_url.get((sigla or "").lower()):
            item["indirizzo_uguale_all_elenco"] = clean_url(url).rstrip("/.") == listed_url[sigla.lower()].rstrip("/.")
        if not sigla:
            item.update(azione="non scritta", motivo="linee guida fuori dalle cartelle delle riviste: rivista sconosciuta")
        elif jid is None:
            item.update(azione="non scritta", motivo=f"rivista {sigla} non nella tabella journal (serve riviste.cmd con "
                                                     "la rivista in riviste_prioritarie.csv)")
        elif len(text) < 200:
            item.update(azione="non scritta", motivo="testo del PDF troppo corto o assente (PDF scansionato?)")
        elif not _present(home, d["original_path"]):
            item.update(azione="fuori dalla versione", motivo="il file non è più nella cartella linee_guida")
        else:
            item["azione"] = "nella versione"
        pdfs.append(item)

    versions = []
    by_journal: dict[str, list[dict]] = {}
    for item in pdfs:
        if item["azione"] == "nella versione":
            by_journal.setdefault(item["journal_id"], []).append(item)
    for jid, items in sorted(by_journal.items(), key=lambda kv: kv[1][0]["sigla"].lower()):
        newest: dict[str, dict] = {}
        for item in sorted(items, key=lambda i: (i["detected_at"] or "", i["letto"], i["document_id"])):
            old = newest.get(item["chiave"])
            if old is not None:
                old.update(azione="sostituita", motivo=f"c'è una stampa più recente della stessa pagina ({item['file']})")
            newest[item["chiave"]] = item
        sigla = items[0]["sigla"]
        main = listed_url.get(sigla.lower())
        main = main.split("#", 1)[0].rstrip("/.") if main else None
        docs = sorted(newest.values(), key=lambda i: (i["chiave"] != main, i["file"].lower()))
        sha = version_fingerprint(docs)
        last = latest_version(conn, jid)
        v = {"sigla": sigla, "journal_id": jid, "impronta": sha, "documenti": docs,
             "detected_at": max(d["detected_at"] or "" for d in docs) or None,
             "source_snapshot_id": docs[0]["source_snapshot_id"]}
        if last is not None and last["content_sha256"] == sha:
            v.update(azione="già presente", version_id=last["id"])
        elif conn.execute("SELECT id FROM journal_policy_version WHERE journal_id = ? AND policy_type = ? "
                          "AND content_sha256 = ?", (jid, POLICY_TYPE, sha)).fetchone():
            v.update(azione="non scritta", motivo="gli stessi PDF di una versione più vecchia: togli dalla cartella i "
                                                  "PDF che non valgono più (o rimetti quelli nuovi)")
        else:
            v.update(azione="nuova versione" if last is not None else "nuova",
                     version_id=new_id("journal_policy_version"), supersedes_id=last["id"] if last else None)
            v["summary"] = _summary(v)
        versions.append(v)
    return {"regole": RULES_VERSION, "linee_guida": pdfs, "versioni": versions,
            "da_scrivere": sum(1 for v in versions if v["azione"] in ("nuova", "nuova versione")),
            "documenti_da_collegare": sum(1 for i in pdfs if i["collega_documento"])}


def _summary(v: dict) -> str:
    docs = v["documenti"]
    pages = sum(d["pagine"] for d in docs)
    chars = sum(d["caratteri"] for d in docs)
    parts = [f"Linee guida per gli autori: {len(docs)} PDF, {pages} pagine ({chars} caratteri di testo)"]
    printed = sorted({d["data_stampa"][:10] for d in docs if d.get("data_stampa")})
    if printed:
        parts.append("pagine web stampate dal browser il " + ", ".join(printed))
    first = docs[0]
    if first.get("indirizzo"):
        same = first.get("indirizzo_uguale_all_elenco")
        parts.append(f"prima pagina da {first['indirizzo']}" + ("" if same is None else
                                                             " (indirizzo dell'elenco)" if same else " (diverso dall'elenco)"))
    if v.get("supersedes_id"):
        parts.append("testo cambiato rispetto alla versione precedente: da rileggere")
    return "; ".join(parts) + ". File: " + "; ".join(f"{d['file']} ({d['pagine']} p.)" for d in docs)


def write(conn: sqlite3.Connection, the_plan: dict, *, actor: str = "orpheus linee-guida") -> dict:
    """Apply a plan: one transaction. Returns how many versions were written and guideline PDFs linked."""
    written, linked = 0, 0
    with transaction(conn):
        for v in the_plan["versioni"]:
            if v["azione"] not in ("nuova", "nuova versione"):
                continue
            conn.execute("""INSERT INTO journal_policy_version (id, journal_id, policy_type, content_sha256,
                                source_snapshot_id, detected_at, effective_date, summary, supersedes_id, importance,
                                needs_human_review) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, NULL, ?)""",
                         (v["version_id"], v["journal_id"], POLICY_TYPE, v["impronta"], v["source_snapshot_id"],
                          v["detected_at"], v["summary"], v.get("supersedes_id"), 1 if v.get("supersedes_id") else 0))
            for n, d in enumerate(v["documenti"], 1):
                conn.execute("""INSERT INTO journal_policy_document (policy_version_id, document_id, ord, file_name,
                                    page_key, content_sha256) VALUES (?, ?, ?, ?, ?, ?)""",
                             (v["version_id"], d["document_id"], n, d["file"], d["chiave"], d["impronta"]))
            audit.record(conn, actor=actor, action="write_policy_version", entity_type="journal_policy_version",
                         entity_id=v["version_id"],
                         payload={"regole": RULES_VERSION, "sigla": v["sigla"], "journal_id": v["journal_id"],
                                  "impronta": v["impronta"], "supersedes_id": v.get("supersedes_id"),
                                  "documenti": [{"file": d["file"], "document_id": d["document_id"],
                                                 "pagina_web": d["chiave"], "impronta": d["impronta"],
                                                 "data_stampa": d.get("data_stampa"), "pagine": d["pagine"]}
                                                for d in v["documenti"]]}, in_transaction=True)
            written += 1
        docs = [(i["journal_id"], i["document_id"]) for i in the_plan["linee_guida"] if i["collega_documento"]]
        for jid, did in docs:
            linked += conn.execute("UPDATE document SET journal_id = ? WHERE id = ? AND journal_id IS NULL",
                                   (jid, did)).rowcount
        if linked:
            audit.record(conn, actor=actor, action="link_guidelines_to_journals",
                         payload={"regole": RULES_VERSION, "documenti": [d for _, d in docs]}, in_transaction=True)
    return {"versioni_scritte": written, "linee_guida_collegate": linked}


def public(the_plan: dict) -> list[dict]:
    """PDF rows for JSON output (fingerprint shortened, internal ids left out)."""
    keep = ("file", "sigla", "azione", "motivo", "pagine", "caratteri", "data_stampa", "stampa_come_nel_pdf", "indirizzo",
            "indirizzo_uguale_all_elenco", "detected_at")
    return [{**{k: i[k] for k in keep if k in i}, "impronta": i["impronta"][:12]} for i in the_plan["linee_guida"]]


def public_versions(the_plan: dict) -> list[dict]:
    """One row per journal: what happens to its version and which PDFs make it up."""
    out = []
    for v in the_plan["versioni"]:
        row = {"sigla": v["sigla"], "azione": v["azione"], "impronta": v["impronta"][:12],
               "pdf": [d["file"] for d in v["documenti"]], "sostituisce_una_versione": bool(v.get("supersedes_id"))}
        if v.get("motivo"):
            row["motivo"] = v["motivo"]
        out.append(row)
    return out
