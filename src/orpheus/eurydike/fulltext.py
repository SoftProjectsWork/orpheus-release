"""EURYDIKE E5 — full text (T-051f; decisions of the user of 2026-11-03: "Inclusi + forse/conflitti", PDFs in the folder
of the review, one study per packet for Claude).

1. `prepare`: a study (review_study) for every record that goes to the full text (`review.goes_to_fulltext`).
2. `read_pdfs`: the PDFs of `revisioni\\<cartella>\\full_text\\` are read like every PDF of ORPHEUS (`ingest_pdf`,
   archive by SHA-256) and linked to their study by DOI, else by the title on the first two pages; the ones not
   recognised are listed and linked by hand (`link_document`).
3. Claude, second reviewer blinded (D11): `issue_packets` writes one packet per study (`eurydike-ft-v1`: protocol and the
   text in pieces with [[p. N]] markers, without the reference list); his answer in `full_text\\da_inviare` is checked
   (`check`: the quote must be on the cited page) and stored as `ft_proposal` by `submit` — never a decision.
4. The decision (include, exclude with a criterion, not retrieved) is written only by a human reviewer
   (`review.decide_fulltext`); Claude's proposal is shown to the user only after his own decision.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
from pathlib import Path

from .. import audit, card_store
from ..db import transaction
from ..ids import new_id
from ..ingest_pdf import ingest_pdf
from . import review as rv
from . import screening as sc
from .core import records as core_records
from .core.text import chunk_pages, trim_references

CONTRACT = "eurydike-ft-v1"
PACKET_FORMAT = "eurydike-pacchetto-fulltext-v1"
FT_DIR, PACKETS, TO_SEND, ACCEPTED = "full_text", "pacchetti", "da_inviare", "accettate"
TASK_KEY = "T-051f:ft:{study_id}"
BUDGET = 60_000                     # UTF-8 bytes of text per piece (a packet holds all the pieces of one study)
QUOTE_MAX = 300
MAX_OPEN = 40
INSTRUCTIONS = [
    "You are the second, blinded reviewer assessing the FULL TEXT of a study for eligibility. Use only the text of this "
    "packet (pages are marked [[p. N]]).",
    "Write ONE JSON file: full_text/da_inviare/<packet file name>, format 'eurydike-ft-v1', with packet_id and study_id "
    "copied from this packet and 'model' = your model name.",
    "decision: include, exclude or unsure; summary: the study in one sentence (design, population, measures); "
    "rationale: why this decision, naming the criterion; criterion_id: only for exclude (an exclusion criterion of the "
    "protocol); quote: one verbatim sentence (at most 300 characters) from the text that best supports the decision, "
    "with its page number in 'page'. If the text does not allow a decision, answer unsure.",
]
EXAMPLE = {"format": CONTRACT, "packet_id": "run_<copy>", "study_id": "rst_<copy>", "model": "claude-opus-5-5",
           "decision": "exclude", "summary": "Cross-sectional survey of 120 adults with autism on physical activity.",
           "rationale": "Participants are adults older than 25 (exclusion 1).", "criterion_id": "rcr_<copy>",
           "quote": "<verbatim sentence>", "page": 3}


class FullTextError(ValueError):
    pass


def ft_dir(conn: sqlite3.Connection, home: Path, review_id: str) -> Path:
    from .protocol import review_dir
    r = conn.execute("SELECT data_dir FROM review WHERE id = ?", (review_id,)).fetchone()
    if r is None:
        raise FullTextError("revisione non trovata")
    return review_dir(home, r[0]) / FT_DIR


# ------------------------------------------------------------------ studies
def prepare(conn: sqlite3.Connection, review_id: str, *, actor: str = "user") -> dict:
    """A study for every record that goes to the full text and has none yet. Returns {"nuovi", "studi"}."""
    have = {r[0] for r in conn.execute("SELECT record_id FROM review_study WHERE review_id = ? AND record_id IS NOT NULL",
                                       (review_id,))}
    new = []
    for (rid,) in conn.execute("SELECT id FROM review_record WHERE review_id = ? AND duplicate_of IS NULL "
                               "ORDER BY created_at, rowid", (review_id,)).fetchall():
        if rid not in have and rv.goes_to_fulltext(conn, rid):
            new.append(rv.add_study(conn, review_id, record_id=rid, actor=actor)["id"])
    total = conn.execute("SELECT count(*) FROM review_study WHERE review_id = ?", (review_id,)).fetchone()[0]
    return {"nuovi": len(new), "studi": total}


def _first_pages(conn: sqlite3.Connection, document_id: str, n: int = 2) -> str:
    return " ".join(r[0] or "" for r in conn.execute("SELECT text FROM document_page WHERE document_id = ? AND page_no <= ? "
                                                     "ORDER BY page_no", (document_id, n)))


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def _match_study(conn: sqlite3.Connection, review_id: str, document_id: str, doi: str | None) -> str | None:
    """The study of a PDF: same DOI as the record, else the record title (≥ 25 characters) on the first two pages."""
    studies = conn.execute("""SELECT s.id, r.doi, r.title FROM review_study s JOIN review_record r ON r.id = s.record_id
                              WHERE s.review_id = ? AND s.document_id IS NULL""", (review_id,)).fetchall()
    d = core_records.clean_doi(doi)
    if d:
        for s in studies:
            if core_records.clean_doi(s["doi"]) == d:
                return s["id"]
    page = _norm(_first_pages(conn, document_id))
    hits = [s["id"] for s in studies if len(_norm(s["title"])) >= 25 and _norm(s["title"]) in page]
    return hits[0] if len(hits) == 1 else None


def link_document(conn: sqlite3.Connection, review_id: str, study_id: str, document_id: str, *, actor: str = "user") -> None:
    st = conn.execute("SELECT id, document_id FROM review_study WHERE id = ? AND review_id = ?",
                      (study_id, review_id)).fetchone()
    if st is None:
        raise FullTextError("studio non trovato in questa revisione")
    doc = conn.execute("SELECT id, article_id FROM document WHERE id = ?", (document_id,)).fetchone()
    if doc is None:
        raise FullTextError("PDF non trovato")
    with rv._write(conn):
        conn.execute("UPDATE review_study SET document_id = ?, article_id = coalesce(article_id, ?) WHERE id = ?",
                     (document_id, doc["article_id"], study_id))
        audit.record(conn, actor=actor, action="review_study_pdf_linked", entity_type="review_study", entity_id=study_id,
                     payload={"document_id": document_id, "before": st["document_id"]}, in_transaction=True)


def pdf_files(conn: sqlite3.Connection, home: Path, review_id: str) -> list[Path]:
    folder = ft_dir(conn, home, review_id)
    folder.mkdir(parents=True, exist_ok=True)
    return sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".pdf")


def read_pdfs(conn: sqlite3.Connection, home: Path, review_id: str, *, archive_dir: Path | None = None,
              actor: str = "user") -> dict:
    """Read the PDFs of the full_text folder and link them to their study. Returns one line per file."""
    out = []
    for path in pdf_files(conn, home, review_id):
        res = ingest_pdf(conn, path, kind="article", archive_dir=archive_dir, home=home, actor=actor)
        item = {"file": path.name, "esito": res.status, "pagine": res.pages}
        if res.status == "error" or not res.document_id:
            item["errore"] = res.error
            out.append(item)
            continue
        linked = conn.execute("SELECT id FROM review_study WHERE review_id = ? AND document_id = ?",
                              (review_id, res.document_id)).fetchone()
        if linked:
            item["studio"], item["collegato"] = linked[0], "già"
        else:
            sid = _match_study(conn, review_id, res.document_id, res.doi_candidate)
            if sid:
                link_document(conn, review_id, sid, res.document_id, actor=actor)
                item["studio"], item["collegato"] = sid, "sì"
            else:
                item["collegato"], item["documento"] = "no", res.document_id
        out.append(item)
    return {"file": out, "cartella": str(ft_dir(conn, home, review_id)),
            "da_collegare": [x for x in out if x.get("collegato") == "no"]}


def unlinked_documents(conn: sqlite3.Connection, home: Path, review_id: str) -> list[dict]:
    """PDFs of the folder already read but linked to no study (to link by hand)."""
    out = []
    linked = {r[0] for r in conn.execute("SELECT document_id FROM review_study WHERE review_id = ? AND document_id IS NOT NULL",
                                         (review_id,))}
    for path in pdf_files(conn, home, review_id):
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        d = conn.execute("SELECT id, pdf_title, page_count FROM document WHERE file_sha256 = ?", (sha,)).fetchone()
        if d and d["id"] not in linked:
            out.append({"documento": d["id"], "file": path.name, "titolo_pdf": d["pdf_title"], "pagine": d["page_count"]})
    return out


# ------------------------------------------------------------------ packets for Claude
def _pages(conn: sqlite3.Connection, document_id: str) -> list[str]:
    return [r[0] or "" for r in conn.execute("SELECT text FROM document_page WHERE document_id = ? ORDER BY page_no",
                                             (document_id,))]


def open_packets(conn: sqlite3.Connection, review_id: str) -> list[sqlite3.Row]:
    return conn.execute("""SELECT w.* FROM workflow_run w JOIN task t ON t.id = w.task_id
                           WHERE t.idempotency_key LIKE 'T-051f:ft:%' AND w.status = 'running'
                           AND json_extract(w.parameters, '$.review_id') = ? ORDER BY w.started_at, w.rowid""",
                        (review_id,)).fetchall()


def to_propose(conn: sqlite3.Connection, review_id: str) -> list[str]:
    """Studies with a PDF, no decision yet, no current proposal of Claude and no open packet."""
    busy = {json.loads(r["parameters"])["study_id"] for r in open_packets(conn, review_id)}
    return [r[0] for r in conn.execute("""SELECT s.id FROM review_study s WHERE s.review_id = ? AND s.document_id IS NOT NULL
                                          AND NOT EXISTS (SELECT 1 FROM ft_proposal p WHERE p.study_id = s.id
                                                          AND p.superseded_by IS NULL)
                                          ORDER BY s.created_at, s.rowid""", (review_id,)) if r[0] not in busy]


def packet(conn: sqlite3.Connection, review_id: str, run: dict) -> dict:
    """The packet of one study: protocol and text only — never a decision of a reviewer (blind, D11)."""
    p = run["parameters"]
    r = conn.execute("SELECT * FROM review WHERE id = ?", (review_id,)).fetchone()
    st = conn.execute("SELECT * FROM review_study WHERE id = ?", (p["study_id"],)).fetchone()
    pages, cut = trim_references(_pages(conn, st["document_id"]))
    return {"formato": PACKET_FORMAT, "contratto": CONTRACT, "packet_id": run["id"], "review_id": review_id,
            "study_id": st["id"], "studio": p["numero"], "emesso_il": run["started_at"], "istruzioni": INSTRUCTIONS,
            "protocollo": {"titolo": r["title"], "domanda": r["question"], "picos": json.loads(r["pico"] or "{}"),
                           "criteri": sc._criteria(conn, review_id)},
            "titolo": st["label"], "pagine": len(pages), "bibliografia_tolta_da_pagina": cut,
            "testo": chunk_pages(pages, BUDGET), "esempio": EXAMPLE}


def issue_packets(conn: sqlite3.Connection, home: Path, review_id: str, n: int, *, project_name: str = "ORPHEUS",
                  project_version: str = "", actor: str = "orpheus eurydike-fulltext") -> dict:
    """Open packets (one study each) until `n` are open; the packets of the open ones are written again."""
    if n > MAX_OPEN:
        raise FullTextError(f"al massimo {MAX_OPEN} pacchetti aperti alla volta")
    sc.claude_reviewer(conn, review_id)
    opened = open_packets(conn, review_id)
    todo = to_propose(conn, review_id)[:max(0, n - len(opened))]
    folder = ft_dir(conn, home, review_id)
    new_runs = []
    if todo:
        with transaction(conn):
            project_id = card_store._ensure_project(conn, project_name, project_version)
            agent_id = card_store._ensure_agent(conn)
            model_id = card_store._ensure_model(conn, card_store.GENERIC_MODEL)
            n0 = conn.execute("SELECT count(*) FROM task WHERE idempotency_key LIKE 'T-051f:ft:%' "
                              "AND title LIKE ?", (f"%{review_id}%",)).fetchone()[0]
            for i, sid in enumerate(todo, 1):
                num = n0 + i
                task_id = new_id("task")
                conn.execute("""INSERT INTO task (id, project_id, title, priority, status, owner_agent_id, expected_output,
                                                  acceptance_criteria, idempotency_key)
                                VALUES (?, ?, ?, 2, 'ready', ?, ?, ?, ?)""",
                             (task_id, project_id, f"Full text ({CONTRACT}) studio {num} della revisione {review_id}",
                              agent_id, f"{FT_DIR}/{TO_SEND}/studio_{num:03d}.json (formato {CONTRACT})",
                              f"supera i controlli di {CONTRACT}", f"{TASK_KEY.format(study_id=sid)}:{num}"))
                params = {"contratto": CONTRACT, "review_id": review_id, "study_id": sid, "numero": num}
                run_id = new_id("workflow_run")
                conn.execute("""INSERT INTO workflow_run (id, task_id, agent_id, executor, model_id, started_at, status,
                                                          parameters, validation_status)
                                VALUES (?, ?, ?, 'claude_session', ?, ?, 'running', ?, 'pending')""",
                             (run_id, task_id, agent_id, model_id, audit.utc_now(),
                              json.dumps(params, ensure_ascii=False, sort_keys=True)))
                new_runs.append(run_id)
            audit.record(conn, actor=actor, action="fulltext_packets_issued", entity_type="review", entity_id=review_id,
                         payload={"contract": CONTRACT, "new_runs": new_runs}, in_transaction=True)
    written = []
    for run in open_packets(conn, review_id):
        params = json.loads(run["parameters"])
        pk = packet(conn, review_id, {"id": run["id"], "started_at": run["started_at"], "parameters": params})
        probs = sc.blind(pk)
        if probs:
            raise FullTextError("; ".join(probs))
        path = folder / PACKETS / f"studio_{params['numero']:03d}.json"
        card_store._write_if_changed(path, json.dumps(pk, ensure_ascii=False, indent=1).encode("utf-8"))
        written.append({"studio": params["numero"], "file": str(path), "nuovo": run["id"] in new_runs})
    (folder / TO_SEND).mkdir(parents=True, exist_ok=True)
    return {"aperti": written, "nuovi": len(new_runs), "da_proporre": len(to_propose(conn, review_id)),
            "cartella": str(folder)}


# ------------------------------------------------------------------ checks of Claude's answer
def _ws(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text or "")).strip()


def _page_texts(pk: dict) -> dict[int, str]:
    out: dict[int, str] = {}
    for block in pk.get("testo") or []:
        for m in re.finditer(r"\[\[p\. (\d+)\]\]\n(.*?)(?=\n?\[\[p\. \d+\]\]|\Z)", block, re.S):
            out[int(m.group(1))] = out.get(int(m.group(1)), "") + m.group(2)
    return out


def check(data, pk: dict) -> list[str]:
    """Problems of one answer against its packet (empty = acceptable)."""
    if not isinstance(data, dict):
        return ["la risposta deve essere un oggetto JSON"]
    probs = []
    if data.get("format") != CONTRACT:
        probs.append(f"format deve essere «{CONTRACT}»")
    if data.get("packet_id") != pk["packet_id"]:
        probs.append("packet_id diverso da quello del pacchetto")
    if data.get("study_id") != pk["study_id"]:
        probs.append("study_id diverso da quello del pacchetto")
    if not isinstance(data.get("model"), str) or not data["model"].strip():
        probs.append("model mancante")
    dec = data.get("decision")
    if dec not in ("include", "exclude", "unsure"):
        probs.append("decision: include, exclude o unsure")
    summary, why = str(data.get("summary") or "").strip(), str(data.get("rationale") or "").strip()
    if not summary or not why:
        probs.append("summary e rationale devono esserci")
    elif summary == why:
        probs.append("summary e rationale devono essere diversi")
    excl = {c["id"] for c in pk["protocollo"]["criteri"] if c["tipo"] == "exclusion"}
    cid = data.get("criterion_id")
    if dec == "exclude" and cid not in excl:
        probs.append("per exclude serve criterion_id di un criterio di esclusione del protocollo")
    if dec != "exclude" and cid:
        probs.append("criterion_id solo per exclude")
    quote = data.get("quote")
    if quote:
        if not isinstance(quote, str) or len(quote) > QUOTE_MAX:
            probs.append(f"quote: una frase di al massimo {QUOTE_MAX} caratteri")
        else:
            page = data.get("page")
            texts = _page_texts(pk)
            if not isinstance(page, int) or page not in texts:
                probs.append("page: il numero di una pagina del pacchetto")
            elif _ws(quote) not in _ws(texts[page]):
                probs.append(f"la frase non è a pagina {page} (copiala come è scritta)")
    elif dec in ("include", "exclude"):
        probs.append("quote: serve una frase del testo che sostenga la decisione")
    allowed = {"format", "packet_id", "study_id", "model", "decision", "summary", "rationale", "criterion_id", "quote", "page"}
    extra = set(data) - allowed
    if extra:
        probs.append("campi non previsti: " + ", ".join(sorted(extra)))
    return probs


def check_with_packet_files(answer: Path, pk_path: Path) -> list[str]:
    try:
        data = json.loads(Path(answer).read_text(encoding="utf-8-sig"))
        pk = json.loads(Path(pk_path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        return [f"file non leggibile: {exc}"]
    return check(data, pk)


def submit(conn: sqlite3.Connection, home: Path, review_id: str, *, actor: str = "claude_session") -> dict:
    """The answers in full_text/da_inviare checked again against the database; accepted ones become `ft_proposal`."""
    folder = ft_dir(conn, home, review_id)
    results = []
    for path in sorted((folder / TO_SEND).glob("*.json")) if (folder / TO_SEND).is_dir() else []:
        raw = path.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        res = {"file": path.name}
        run, params, stop = None, {}, None
        try:
            data = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            data, stop = None, f"file non leggibile o non JSON: {exc}"
        if stop is None:
            pid = data.get("packet_id") if isinstance(data, dict) else None
            run = conn.execute("SELECT * FROM workflow_run WHERE id = ?", (pid,)).fetchone() if isinstance(pid, str) else None
            params = json.loads(run["parameters"]) if run else {}
            if run is None or params.get("contratto") != CONTRACT:
                stop = "packet_id mancante o non è un pacchetto di full text di questo database"
            elif params.get("review_id") != review_id:
                stop = "il pacchetto è di un'altra revisione"
            elif run["status"] != "running":
                stop = "pacchetto già accettato o non più valido"
        probs = [stop] if stop else check(data, packet(conn, review_id, {"id": run["id"], "started_at": run["started_at"],
                                                                          "parameters": params}))
        if not probs:
            rv.add_ft_proposal(conn, params["study_id"], workflow_run_id=run["id"], decision=data["decision"],
                               summary=data["summary"].strip(), rationale=data["rationale"].strip(),
                               criterion_id=data.get("criterion_id") or None, quote=data.get("quote") or None,
                               page_no=data.get("page") if data.get("quote") else None, actor=actor)
        with transaction(conn):
            if run is not None:
                conn.execute("""INSERT INTO validation_result (id, workflow_run_id, check_name, outcome, validator, details)
                                VALUES (?, ?, ?, ?, 'deterministic', ?)""",
                             (new_id("validation_result"), run["id"], CONTRACT, "fail" if probs else "pass",
                              json.dumps({"file": path.name, "file_sha256": sha, "problemi": probs[:50]}, ensure_ascii=False)))
                if not probs:
                    conn.execute("""UPDATE workflow_run SET status = 'succeeded', finished_at = ?, validation_status = 'pass',
                                    model_id = ? WHERE id = ?""",
                                 (max(audit.utc_now(), run["started_at"]), card_store._ensure_model(conn, data["model"]),
                                  run["id"]))
                    conn.execute("UPDATE task SET status = 'done' WHERE id = ?", (run["task_id"],))
            audit.record(conn, actor="orpheus eurydike-fulltext", action="fulltext_answer_" + ("refused" if probs else "accepted"),
                         entity_type="review", entity_id=review_id,
                         payload={"file_sha256": sha, "workflow_run_id": run["id"] if run else None}, in_transaction=True)
        motivi = path.with_name(path.stem + ".motivi.txt")
        if probs:
            motivi.write_text(f"Risposta rifiutata ({CONTRACT}):\n- " + "\n- ".join(probs[:60]) + "\n", encoding="utf-8")
            res |= {"esito": "rifiutata", "motivi": probs[:20]}
        else:
            dest = folder / ACCEPTED / path.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                dest = dest.with_name(f"{path.stem}_{sha[:8]}{path.suffix}")
            path.replace(dest)
            if motivi.exists():
                motivi.unlink()
            res["esito"] = "accettata"
        results.append(res)
    return {"inviate": len(results), "accettate": sum(r["esito"] == "accettata" for r in results),
            "rifiutate": sum(r["esito"] != "accettata" for r in results), "risposte": results}


# ------------------------------------------------------------------ the page
FT_LABELS = {"pending": "da decidere", "include": "incluso", "exclude": "escluso", "not_retrieved": "non reperito"}


def board(conn: sqlite3.Connection, home: Path, review_id: str, *, filtro: str = "tutti") -> dict:
    """The studies for the Full text page: PDF, decision, Claude's proposal (only after the decision: blind)."""
    if conn.execute("SELECT 1 FROM review WHERE id = ?", (review_id,)).fetchone() is None:
        raise FullTextError("revisione non trovata")
    crit = {r[0]: {"ord": r[1], "testo": r[2]} for r in conn.execute(
        "SELECT id, ord, text FROM review_criterion WHERE review_id = ?", (review_id,))}
    rows = []
    for s in conn.execute("""SELECT s.*, r.title, r.year, r.doi, r.authors, r.journal, d.page_count, d.file_sha256
                             FROM review_study s LEFT JOIN review_record r ON r.id = s.record_id
                             LEFT JOIN document d ON d.id = s.document_id WHERE s.review_id = ?
                             ORDER BY s.created_at, s.rowid""", (review_id,)):
        prop = conn.execute("SELECT * FROM ft_proposal WHERE study_id = ? AND superseded_by IS NULL", (s["id"],)).fetchone()
        decided = s["ft_decision"] != "pending"
        rows.append({"id": s["id"], "titolo": s["title"] or s["label"], "anno": s["year"], "doi": s["doi"],
                     "autori": s["authors"], "rivista": s["journal"], "pdf": bool(s["document_id"]),
                     "documento": s["document_id"], "pagine": s["page_count"],
                     "decisione": s["ft_decision"], "criterio": crit.get(s["ft_criterion_id"]), "criterio_id": s["ft_criterion_id"],
                     "nota": s["ft_note"],
                     "claude": ({"decisione": prop["decision"], "sintesi": prop["summary"], "motivo": prop["rationale"],
                                 "criterio": crit.get(prop["criterion_id"]), "frase": prop["quote"], "pagina": prop["page_no"]}
                                if prop and decided else ("nascosta" if prop else None))})
    counts = {"tutti": len(rows), "da_decidere": sum(r["decisione"] == "pending" for r in rows),
              "senza_pdf": sum(not r["pdf"] for r in rows), "inclusi": sum(r["decisione"] == "include" for r in rows),
              "esclusi": sum(r["decisione"] == "exclude" for r in rows),
              "non_reperiti": sum(r["decisione"] == "not_retrieved" for r in rows)}
    keep = {"tutti": lambda r: True, "da_decidere": lambda r: r["decisione"] == "pending", "senza_pdf": lambda r: not r["pdf"],
            "inclusi": lambda r: r["decisione"] == "include", "esclusi": lambda r: r["decisione"] == "exclude",
            "non_reperiti": lambda r: r["decisione"] == "not_retrieved"}.get(filtro, lambda r: True)
    eligible = sum(1 for (rid,) in conn.execute("SELECT id FROM review_record WHERE review_id = ? AND duplicate_of IS NULL",
                                                (review_id,)).fetchall() if rv.goes_to_fulltext(conn, rid))
    return {"righe": [r for r in rows if keep(r)], "conteggi": counts, "passano": eligible,
            "da_preparare": max(0, eligible - sum(1 for r in rows)),
            "pdf_da_collegare": unlinked_documents(conn, home, review_id),
            "cartella": str(ft_dir(conn, home, review_id)), "criteri": sc._criteria(conn, review_id),
            "claude": {"pacchetti_aperti": len(open_packets(conn, review_id)), "da_proporre": len(to_propose(conn, review_id)),
                       "proposte": conn.execute("""SELECT count(*) FROM ft_proposal p JOIN review_study s ON s.id = p.study_id
                                                   WHERE s.review_id = ? AND p.superseded_by IS NULL""", (review_id,)).fetchone()[0]}}


def decide(conn: sqlite3.Connection, review_id: str, study_id: str, decision: str, *, criterion_id: str | None = None,
           note: str | None = None, label: str | None = None) -> dict:
    st = conn.execute("SELECT review_id FROM review_study WHERE id = ?", (study_id,)).fetchone()
    if st is None or st[0] != review_id:
        raise FullTextError("studio non trovato in questa revisione")
    me = sc.human_reviewer(conn, review_id, label)
    return rv.decide_fulltext(conn, study_id, me["id"], decision, criterion_id=criterion_id or None, note=note)


def pdf_path(conn: sqlite3.Connection, home: Path, archive_dir: Path, document_id: str) -> Path:
    """The file of a PDF to show it in the page: the archived copy (by SHA-256), else the original path."""
    from ..ingest_pdf import archive_path
    d = conn.execute("SELECT file_sha256, original_path FROM document WHERE id = ?", (document_id,)).fetchone()
    if d is None:
        raise FullTextError("PDF non trovato")
    p = archive_path(Path(archive_dir), d["file_sha256"])
    if p.exists():
        return p
    orig = Path(d["original_path"] or "")
    orig = orig if orig.is_absolute() else Path(home) / orig
    if orig.exists():
        return orig
    raise FullTextError("il file del PDF non c'è più (né nell'archivio né nella cartella)")
