"""EURYDIKE E7 — risk of bias and quality (T-051h, contract `eurydike-appraisal-v1`; decisions of the user of 2026-11-03
16:2x: «Proposto dal disegno», «Claude, tu confermi», «Tutti e due», PDF «Come in E6»).

1. The tools of every included study: one risk-of-bias tool (RoB 2, ROBINS-I, QUADAS-2, Newcastle–Ottawa) and, if
   wanted, one quality scale (QualSyst, Downs & Black, AXIS, QAREL, COSMIN, PEDro, MMAT) — the tools of Tabula v19
   (`core.tools`). ORPHEUS proposes them from the design in the article card (natura_studio, tipo_obiettivo,
   disegno_studio) or else from the defaults of the protocol; the user accepts or changes them
   (`appraisal_tool_choice`, written by a human reviewer).
2. Claude judges first, in a session: one packet per study (`eurydike-pacchetto-bias-v1`: the tools with every item,
   its scale and hint, and the text with [[p. N]]). His answer judges every item: the judgement (a code of the item's
   scale), the reason and a verbatim sentence with its page (a sentence is required unless the judgement says that
   the information is missing: na, ni, u). Accepted judgements are proposals (`ai`).
3. The user confirms or changes every judgement with the PDF beside (state confirmed / edited, trigger: human only).
   The overall risk of bias is a SUGGESTION computed by `core.tools.rob_overall`; the final one is written by the user
   (item `_overall`). Scores of the quality scales are computed by `core.tools.qual_score`, never typed.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path

from .. import audit, card_store, cards
from ..db import transaction
from ..ids import new_id
from . import extraction as ex
from . import review as rv
from . import screening as sc
from . import store
from .core import tools as tl
from .core.text import chunk_pages, trim_references

CONTRACT = "eurydike-appraisal-v1"
PACKET_FORMAT = "eurydike-pacchetto-bias-v1"
RB_DIR, PACKETS, TO_SEND, ACCEPTED = "rischio_di_bias", "pacchetti", "da_inviare", "accettate"
TASK_PREFIX = "T-051h:rob:"
BUDGET = 60_000
MAX_OPEN = 40
KINDS = ("rob", "quality")
NONE = "none"                                       # «nessuna scala di qualità» chosen by the user
OVERALL = "_overall"
NO_INFO = {"na", "ni", "u"}                         # judgements that say the information is missing: no sentence needed
ROB_KEYS = tl.ROB_KEYS
QUAL_KEYS = tl.QUAL_KEYS

INSTRUCTIONS = [
    "You assess the risk of bias and the methodological quality of ONE study for a systematic review, from the FULL "
    "TEXT of this packet only (pages are marked [[p. N]]).",
    "Write ONE JSON file: rischio_di_bias/da_inviare/<packet file name>, format 'eurydike-appraisal-v1', with packet_id "
    "and study_id copied from this packet and 'model' = your model name.",
    "'giudizi': one object per item of every tool of 'strumenti' (strumento = the tool key, voce = the item id): "
    "giudizio = one code of the item's scale; motivo = why, in one or two sentences (English); citazione = one "
    "verbatim sentence (12 to 500 characters) copied from the cited page that supports the judgement, with its page in "
    "'pagina'. When the judgement says that the information is missing or not applicable (na, ni, u) the sentence can "
    "be left out. Do not write an overall judgement: ORPHEUS computes the suggestion, the user writes the final one.",
    "Be conservative: judge only what the text reports; if the text does not report it, say so.",
]


class AppraisalError(ValueError):
    pass


def rb_dir(conn: sqlite3.Connection, home: Path, review_id: str) -> Path:
    from .protocol import review_dir
    r = conn.execute("SELECT data_dir FROM review WHERE id = ?", (review_id,)).fetchone()
    if r is None:
        raise AppraisalError("revisione non trovata")
    return review_dir(home, r[0]) / RB_DIR


def catalogue() -> dict:
    """The tools that can be chosen, with their name, use and number of items."""
    def row(k):
        t = tl.get_tool(k)
        return {"key": k, "nome": t.name, "uso": t.use, "voci": len(t.items)}
    return {"rob": [row(k) for k in ROB_KEYS], "quality": [row(k) for k in QUAL_KEYS]}


# ------------------------------------------------------------------ tools of a study
def _card_values(conn: sqlite3.Connection, aid: str | None) -> dict[str, str]:
    if not aid:
        return {}
    out: dict[str, str] = {}
    for r in store.current_items(conn, aid):
        if r["state"] != "nr" and r["value"] and r["field_key"] not in out:
            out[r["field_key"]] = r["value"]
    return out


def propose(conn: sqlite3.Connection, review_id: str, study_id: str) -> dict:
    """The tools proposed for a study: from the design in the card, else the protocol's defaults. With the reason."""
    st = conn.execute("SELECT article_id FROM review_study WHERE id = ? AND review_id = ?", (study_id, review_id)).fetchone()
    if st is None:
        raise AppraisalError("studio non trovato in questa revisione")
    r = conn.execute("SELECT rob_tool_default, qual_tool_default FROM review WHERE id = ?", (review_id,)).fetchone()
    v = _card_values(conn, st["article_id"])
    nature, aim = v.get("natura_studio"), v.get("tipo_obiettivo")
    design = (v.get("disegno_studio") or "").lower()
    rob = qual = None
    why = ""
    if aim in ("affidabilita", "validita"):
        rob, qual, why = None, "cosminrel" if aim == "affidabilita" else "qualsyst", f"obiettivo: {aim}"
        if aim == "affidabilita" and "agreement" in design:
            qual = "qarel"
    elif nature in ("rct",) or (nature is None and re.search(r"randomi[sz]ed", design) and "non-random" not in design):
        rob, qual, why = "rob2", "pedro", f"disegno: {nature or v.get('disegno_studio')}"
    elif nature == "crossover":
        rob, qual, why = ("rob2" if re.search(r"randomi[sz]", design) else "robinsi"), "qualsyst", "disegno: crossover"
    elif nature == "non_randomizzato" or (nature is None and re.search(r"non-?random|quasi-?experiment|pre-?post", design)):
        rob, qual, why = "robinsi", "mmatnr", f"disegno: {nature or v.get('disegno_studio')}"
    elif nature in ("coorte", "caso_controllo") or (nature is None and re.search(r"cohort|case-control", design)):
        rob, qual, why = "nos", "qualsyst", f"disegno: {nature or v.get('disegno_studio')}"
    elif nature in ("trasversale", "indagine") or (nature is None and re.search(r"cross-sectional|survey", design)):
        rob, qual, why = None, "axis", f"disegno: {nature or v.get('disegno_studio')}"
    elif nature in ("serie_di_casi", "qualitativo", "altro"):
        rob, qual, why = None, "qualsyst", f"disegno: {nature}"
    if not why:
        rob, qual = r["rob_tool_default"], r["qual_tool_default"]
        why = "strumenti predefiniti del protocollo" if (rob or qual) else "disegno non noto: scegli tu"
    elif rob is None and r["rob_tool_default"] and nature not in ("trasversale", "indagine", "qualitativo"):
        rob = r["rob_tool_default"]
    return {"rob": rob if rob in ROB_KEYS else None, "quality": qual if qual in QUAL_KEYS else None, "perche": why}


def chosen(conn: sqlite3.Connection, study_id: str) -> dict[str, str]:
    return {r["kind"]: r["tool"] for r in conn.execute(
        "SELECT kind, tool FROM appraisal_tool_choice WHERE study_id = ? AND superseded_by IS NULL", (study_id,))}


def choose(conn: sqlite3.Connection, review_id: str, study_id: str, kind: str, tool: str | None, *,
           label: str | None = None, actor: str = "user") -> dict:
    """The user's choice of a tool (rob or quality) for a study; `none` = no quality scale."""
    if kind not in KINDS:
        raise AppraisalError("tipo: rob o quality")
    tool = tool or NONE
    allowed = (ROB_KEYS if kind == "rob" else QUAL_KEYS) + (NONE,)
    if tool not in allowed:
        raise AppraisalError(f"strumento non ammesso: {tool}")
    _study(conn, review_id, study_id)
    me = sc.human_reviewer(conn, review_id, label)
    old = conn.execute("SELECT id, tool FROM appraisal_tool_choice WHERE study_id = ? AND kind = ? AND superseded_by IS NULL",
                       (study_id, kind)).fetchone()
    if old is not None and old["tool"] == tool:
        return {"id": old["id"], "strumento": tool, "nuovo": False}
    cid = new_id("appraisal_tool_choice")
    with rv._write(conn):
        if old is not None:
            conn.execute("UPDATE appraisal_tool_choice SET superseded_by = ? WHERE id = ?", (cid, old["id"]))
        conn.execute("INSERT INTO appraisal_tool_choice (id, study_id, kind, tool, chosen_by) VALUES (?, ?, ?, ?, ?)",
                     (cid, study_id, kind, tool, me["id"]))
        audit.record(conn, actor=actor, action="appraisal_tool_chosen", entity_type="review_study", entity_id=study_id,
                     payload={"kind": kind, "tool": tool, "before": old["tool"] if old else None}, in_transaction=True)
    return {"id": cid, "strumento": tool, "nuovo": True}


def accept_proposals(conn: sqlite3.Connection, review_id: str, *, label: str | None = None, actor: str = "user") -> dict:
    """«Usa le proposte»: the proposed tools for every included study that has no choice yet (kind by kind)."""
    n = 0
    for s in ex.included(conn, review_id):
        have, prop = chosen(conn, s["id"]), propose(conn, review_id, s["id"])
        for kind in KINDS:
            if kind not in have and (prop[kind] or kind == "quality"):
                choose(conn, review_id, s["id"], kind, prop[kind] or NONE, label=label, actor=actor)
                n += 1
    return {"scelti": n}


def _study(conn: sqlite3.Connection, review_id: str, study_id: str) -> sqlite3.Row:
    st = conn.execute("SELECT * FROM review_study WHERE id = ?", (study_id,)).fetchone()
    if st is None or st["review_id"] != review_id:
        raise AppraisalError("studio non trovato in questa revisione")
    return st


def _tools_of(conn: sqlite3.Connection, study_id: str) -> list[tuple[str, tl.Tool]]:
    return [(k, tl.get_tool(v)) for k, v in sorted(chosen(conn, study_id).items()) if v != NONE]


def _tool_spec(kind: str, t: tl.Tool) -> dict:
    return {"strumento": t.key, "tipo": kind, "nome": t.name, "uso": t.use,
            "voci": [{"voce": i.id, "testo": i.text, **({"aiuto": i.hint} if i.hint else {}),
                      **({"sezione": i.section} if i.section else {}),
                      "scala": [{"codice": c, "etichetta": t.labels.get(c, c)} for c in i.codes()]} for i in t.items]}


# ------------------------------------------------------------------ packets for Claude
def open_packets(conn: sqlite3.Connection, review_id: str) -> list[sqlite3.Row]:
    return conn.execute("""SELECT w.* FROM workflow_run w JOIN task t ON t.id = w.task_id
                           WHERE t.idempotency_key LIKE ? AND w.status = 'running'
                           AND json_extract(w.parameters, '$.review_id') = ? ORDER BY w.started_at, w.rowid""",
                        (TASK_PREFIX + "%", review_id)).fetchall()


def _judged_by_claude(conn: sqlite3.Connection, study_id: str, claude_id: str | None) -> set[str]:
    if not claude_id:
        return set()
    return {r[0] for r in conn.execute("SELECT DISTINCT tool FROM appraisal WHERE study_id = ? AND reviewer_id = ? "
                                       "AND superseded_by IS NULL", (study_id, claude_id))}


def to_judge(conn: sqlite3.Connection, review_id: str) -> list[str]:
    """Included studies with a PDF and chosen tools that Claude has not judged yet, without an open packet."""
    busy = {json.loads(r["parameters"])["study_id"] for r in open_packets(conn, review_id)}
    claude = sc._claude_id(conn, review_id)
    out = []
    for s in ex.included(conn, review_id):
        tools = {t.key for _k, t in _tools_of(conn, s["id"])}
        if s["document_id"] and tools and s["id"] not in busy and not tools <= _judged_by_claude(conn, s["id"], claude):
            out.append(s["id"])
    return out


def packet(conn: sqlite3.Connection, review_id: str, run: dict) -> dict:
    p = run["parameters"]
    r = conn.execute("SELECT * FROM review WHERE id = ?", (review_id,)).fetchone()
    st = conn.execute("SELECT * FROM review_study WHERE id = ?", (p["study_id"],)).fetchone()
    pages, cut = trim_references(ex._pages(conn, st["document_id"]))
    return {"formato": PACKET_FORMAT, "contratto": CONTRACT, "packet_id": run["id"], "review_id": review_id,
            "study_id": st["id"], "studio": p["numero"], "emesso_il": run["started_at"], "istruzioni": INSTRUCTIONS,
            "protocollo": {"titolo": r["title"], "domanda": r["question"], "picos": json.loads(r["pico"] or "{}")},
            "titolo": st["label"],
            "strumenti": [_tool_spec(k, tl.get_tool(v)) for k, v in sorted(p["strumenti"].items())],
            "pagine": len(pages), "bibliografia_tolta_da_pagina": cut, "testo": chunk_pages(pages, BUDGET),
            "esempio": {"format": CONTRACT, "packet_id": "run_<copy>", "study_id": "rst_<copy>", "model": "claude-opus-5-5",
                        "giudizi": [{"strumento": "rob2", "voce": "D1", "giudizio": "some",
                                     "motivo": "Random sequence generated by computer; allocation concealment not reported.",
                                     "citazione": "<verbatim sentence>", "pagina": 3}]}}


def issue_packets(conn: sqlite3.Connection, home: Path, review_id: str, n: int, *, project_name: str = "ORPHEUS",
                  project_version: str = "", actor: str = "orpheus eurydike-bias") -> dict:
    if n > MAX_OPEN:
        raise AppraisalError(f"al massimo {MAX_OPEN} pacchetti aperti alla volta")
    sc.claude_reviewer(conn, review_id)
    opened = open_packets(conn, review_id)
    todo = to_judge(conn, review_id)[:max(0, n - len(opened))]
    folder = rb_dir(conn, home, review_id)
    new_runs = []
    if todo:
        with transaction(conn):
            project_id = card_store._ensure_project(conn, project_name, project_version)
            agent_id = card_store._ensure_agent(conn)
            model_id = card_store._ensure_model(conn, card_store.GENERIC_MODEL)
            n0 = conn.execute("SELECT count(*) FROM task WHERE idempotency_key LIKE ? AND title LIKE ?",
                              (TASK_PREFIX + "%", f"%{review_id}%")).fetchone()[0]
            for i, sid in enumerate(todo, 1):
                num = n0 + i
                task_id = new_id("task")
                conn.execute("""INSERT INTO task (id, project_id, title, priority, status, owner_agent_id, expected_output,
                                                  acceptance_criteria, idempotency_key)
                                VALUES (?, ?, ?, 2, 'ready', ?, ?, ?, ?)""",
                             (task_id, project_id, f"Rischio di bias ({CONTRACT}) studio {num} della revisione {review_id}",
                              agent_id, f"{RB_DIR}/{TO_SEND}/studio_{num:03d}.json (formato {CONTRACT})",
                              f"supera i controlli di {CONTRACT}", f"{TASK_PREFIX}{sid}:{num}"))
                tools = {k: v for k, v in chosen(conn, sid).items() if v != NONE}
                params = {"contratto": CONTRACT, "review_id": review_id, "study_id": sid, "numero": num, "strumenti": tools}
                run_id = new_id("workflow_run")
                conn.execute("""INSERT INTO workflow_run (id, task_id, agent_id, executor, model_id, started_at, status,
                                                          parameters, validation_status)
                                VALUES (?, ?, ?, 'claude_session', ?, ?, 'running', ?, 'pending')""",
                             (run_id, task_id, agent_id, model_id, audit.utc_now(),
                              json.dumps(params, ensure_ascii=False, sort_keys=True)))
                new_runs.append(run_id)
            audit.record(conn, actor=actor, action="appraisal_packets_issued", entity_type="review", entity_id=review_id,
                         payload={"contract": CONTRACT, "new_runs": new_runs}, in_transaction=True)
    written = []
    for run in open_packets(conn, review_id):
        params = json.loads(run["parameters"])
        pk = packet(conn, review_id, {"id": run["id"], "started_at": run["started_at"], "parameters": params})
        path = folder / PACKETS / f"studio_{params['numero']:03d}.json"
        card_store._write_if_changed(path, json.dumps(pk, ensure_ascii=False, indent=1).encode("utf-8"))
        written.append({"studio": params["numero"], "file": str(path), "nuovo": run["id"] in new_runs})
    (folder / TO_SEND).mkdir(parents=True, exist_ok=True)
    return {"aperti": written, "nuovi": len(new_runs), "da_giudicare": len(to_judge(conn, review_id)), "cartella": str(folder)}


# ------------------------------------------------------------------ checks of Claude's answer
def check(data, pk: dict) -> list[str]:
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
    extra = set(data) - {"format", "packet_id", "study_id", "model", "giudizi", "note"}
    if extra:
        probs.append("campi non previsti: " + ", ".join(sorted(extra)))
    texts = ex.page_texts(pk)
    items = {(t["strumento"], v["voce"]): {c["codice"] for c in v["scala"]} for t in pk["strumenti"] for v in t["voci"]}
    seen: dict[tuple[str, str], int] = {}
    js = data.get("giudizi")
    if not isinstance(js, list):
        return probs + ["giudizi: una lista"]
    for i, g in enumerate(js, 1):
        if not isinstance(g, dict):
            probs.append(f"giudizio {i}: deve essere un oggetto")
            continue
        key = (g.get("strumento"), g.get("voce"))
        lab = f"giudizio {i} ({key[0]} {key[1]})"
        bad = set(g) - {"strumento", "voce", "giudizio", "motivo", "citazione", "pagina"}
        if bad:
            probs.append(f"{lab}: campi non previsti: {', '.join(sorted(bad))}")
        if key not in items:
            probs.append(f"{lab}: strumento o voce non nel pacchetto")
            continue
        seen[key] = seen.get(key, 0) + 1
        j = g.get("giudizio")
        if j not in items[key]:
            probs.append(f"{lab}: giudizio '{j}' non ammesso (ammessi: {', '.join(sorted(items[key]))})")
        why = g.get("motivo")
        if not isinstance(why, str) or not why.strip() or len(why) > 1000:
            probs.append(f"{lab}: serve il motivo (al massimo 1000 caratteri)")
        quote, page = g.get("citazione"), g.get("pagina")
        if quote in (None, ""):
            if j not in NO_INFO:
                probs.append(f"{lab}: serve la citazione (frase del testo) per questo giudizio")
            if page not in (None, ""):
                probs.append(f"{lab}: pagina senza citazione")
            continue
        if not isinstance(quote, str) or not cards.QUOTE_MIN <= len(quote) <= cards.QUOTE_MAX:
            probs.append(f"{lab}: citazione di {cards.QUOTE_MIN}-{cards.QUOTE_MAX} caratteri")
        elif not isinstance(page, int) or isinstance(page, bool) or page not in texts:
            probs.append(f"{lab}: pagina: il numero di una pagina del pacchetto")
        elif ex.quote_check(quote, texts[page]) == "not_found":
            probs.append(f"{lab}: la frase non è a pagina {page} (copiala come è scritta)")
    for key in items:
        if seen.get(key, 0) == 0:
            probs.append(f"manca il giudizio di {key[0]} {key[1]}")
        elif seen[key] > 1:
            probs.append(f"{key[0]} {key[1]} giudicato più volte")
    return probs


def check_with_packet_files(answer: Path, pk_path: Path) -> list[str]:
    try:
        data = json.loads(Path(answer).read_text(encoding="utf-8-sig"))
        pk = json.loads(Path(pk_path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        return [f"file non leggibile: {exc}"]
    return check(data, pk)


def _current(conn: sqlite3.Connection, study_id: str, reviewer_id: str, tool: str, item: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM appraisal WHERE study_id = ? AND reviewer_id = ? AND tool = ? AND item_id = ? "
                        "AND superseded_by IS NULL", (study_id, reviewer_id, tool, item)).fetchone()


def _insert(conn: sqlite3.Connection, row: dict, old_id: str | None) -> str:
    aid = new_id("appraisal")
    if old_id:
        conn.execute("UPDATE appraisal SET superseded_by = ? WHERE id = ?", (aid, old_id))
    cols = ("id", "study_id", "reviewer_id", "tool", "item_id", "judgement", "rationale", "quote", "page_no",
            "quote_check", "state", "workflow_run_id")
    conn.execute(f"INSERT INTO appraisal ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                 tuple({**row, "id": aid}.get(c) for c in cols))
    return aid


def submit(conn: sqlite3.Connection, home: Path, review_id: str, *, actor: str = "claude_session") -> dict:
    folder = rb_dir(conn, home, review_id)
    results = []
    for path in sorted((folder / TO_SEND).glob("*.json")) if (folder / TO_SEND).is_dir() else []:
        raw = path.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        res = {"file": path.name}
        run, params, stop, pk, data = None, {}, None, None, None
        try:
            data = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            stop = f"file non leggibile o non JSON: {exc}"
        if stop is None:
            pid = data.get("packet_id") if isinstance(data, dict) else None
            run = conn.execute("SELECT * FROM workflow_run WHERE id = ?", (pid,)).fetchone() if isinstance(pid, str) else None
            params = json.loads(run["parameters"]) if run else {}
            if run is None or params.get("contratto") != CONTRACT:
                stop = "packet_id mancante o non è un pacchetto del rischio di bias di questo database"
            elif params.get("review_id") != review_id:
                stop = "il pacchetto è di un'altra revisione"
            elif run["status"] != "running":
                stop = "pacchetto già accettato o non più valido"
        if stop:
            probs = [stop]
        else:
            pk = packet(conn, review_id, {"id": run["id"], "started_at": run["started_at"], "parameters": params})
            probs = check(data, pk)
        n = 0
        if not probs:
            claude = sc.claude_reviewer(conn, review_id)
            texts = ex.page_texts(pk)
            with rv._write(conn):
                for g in data["giudizi"]:
                    q = g.get("citazione") or None
                    old = _current(conn, pk["study_id"], claude, g["strumento"], g["voce"])
                    _insert(conn, {"study_id": pk["study_id"], "reviewer_id": claude, "tool": g["strumento"],
                                   "item_id": g["voce"], "judgement": g["giudizio"], "rationale": g["motivo"].strip(),
                                   "quote": q, "page_no": g.get("pagina") if q else None,
                                   "quote_check": ("exact" if ex.quote_check(q, texts[g["pagina"]]) == "exact" else "fuzzy")
                                   if q else "none", "state": "ai", "workflow_run_id": run["id"]},
                            old["id"] if old else None)
                    n += 1
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
            audit.record(conn, actor="orpheus eurydike-bias", action="appraisal_answer_" + ("refused" if probs else "accepted"),
                         entity_type="review", entity_id=review_id,
                         payload={"file_sha256": sha, "workflow_run_id": run["id"] if run else None, "judgements": n},
                         in_transaction=True)
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
            res |= {"esito": "accettata", "giudizi": n}
        results.append(res)
    return {"inviate": len(results), "accettate": sum(r["esito"] == "accettata" for r in results),
            "rifiutate": sum(r["esito"] != "accettata" for r in results), "risposte": results}


# ------------------------------------------------------------------ the user's judgements
def decide(conn: sqlite3.Connection, review_id: str, study_id: str, tool: str, item: str, judgement: str, *,
           rationale: str | None = None, quote: str | None = None, page: int | None = None,
           label: str | None = None, actor: str = "user") -> dict:
    """The user's judgement of one item (or of the overall risk of bias, item `_overall`): equal to Claude's →
    confirmed (with Claude's reason and sentence), else edited."""
    st = _study(conn, review_id, study_id)
    if tool not in chosen(conn, study_id).values():
        raise AppraisalError("strumento non scelto per questo studio")
    t = tl.get_tool(tool)
    if item == OVERALL:
        if t.kind != "rob" or t.mode == "stars":
            raise AppraisalError("il giudizio complessivo si scrive solo per RoB 2, ROBINS-I e QUADAS-2")
        codes = t.order
    else:
        it = t.item(item)
        if it is None:
            raise AppraisalError("voce non di questo strumento")
        codes = it.codes()
    if judgement not in codes:
        raise AppraisalError(f"giudizio non ammesso (ammessi: {', '.join(codes)})")
    me = sc.human_reviewer(conn, review_id, label)
    claude_id = sc._claude_id(conn, review_id)
    cl = _current(conn, study_id, claude_id, tool, item) if claude_id else None
    mine = _current(conn, study_id, me["id"], tool, item)
    if cl is not None and cl["judgement"] == judgement and not (quote and quote.strip()):
        row = {"rationale": (rationale or "").strip() or cl["rationale"], "quote": cl["quote"], "page_no": cl["page_no"],
               "quote_check": cl["quote_check"], "state": "confirmed"}
    else:
        row = {"rationale": (rationale or "").strip() or None, "quote": None, "page_no": None, "quote_check": "none",
               "state": "edited"}
        if quote and quote.strip():
            if not page:
                raise AppraisalError("con la frase scrivi anche la pagina")
            texts = {i: x for i, x in enumerate(ex._pages(conn, st["document_id"]), 1)} if st["document_id"] else {}
            if int(page) not in texts:
                raise AppraisalError("la pagina non è nel PDF dello studio")
            row.update(quote=quote.strip(), page_no=int(page), quote_check=ex.quote_check(quote, texts[int(page)]))
    with rv._write(conn):
        aid = _insert(conn, {"study_id": study_id, "reviewer_id": me["id"], "tool": tool, "item_id": item,
                             "judgement": judgement, **row}, mine["id"] if mine else None)
        audit.record(conn, actor=actor, action=f"appraisal_{row['state']}", entity_type="appraisal", entity_id=aid,
                     payload={"study_id": study_id, "tool": tool, "item": item, "judgement": judgement,
                              "replaces": mine["id"] if mine else None}, in_transaction=True)
    return {"id": aid, "stato": row["state"]}


def confirm_exact(conn: sqlite3.Connection, review_id: str, study_id: str, *, label: str | None = None,
                  actor: str = "user") -> dict:
    """Confirm in one go Claude's judgements still to check whose sentence was found exactly in the text."""
    v = study(conn, review_id, study_id, label=label)
    n = 0
    for s in v["strumenti"]:
        for x in s["voci"]:
            if x["stato"] == "ai" and x["controllo_citazione"] == "exact":
                decide(conn, review_id, study_id, s["strumento"], x["voce"], x["giudizio"], label=label, actor=actor)
                n += 1
    return {"confermati": n}


# ------------------------------------------------------------------ the pages of the dashboard
def study(conn: sqlite3.Connection, review_id: str, study_id: str, *, label: str | None = None) -> dict:
    st = _study(conn, review_id, study_id)
    rec = conn.execute("SELECT title, year, doi, authors, journal FROM review_record WHERE id = ?",
                       (st["record_id"],)).fetchone() if st["record_id"] else None
    pages = conn.execute("SELECT page_count FROM document WHERE id = ?", (st["document_id"],)).fetchone() if st["document_id"] else None
    claude_id = sc._claude_id(conn, review_id)
    try:
        me = sc.human_reviewer(conn, review_id, label)["id"]
    except sc.ScreeningError:
        me = None
    tools_out = []
    for kind, t in _tools_of(conn, study_id):
        items, effective = [], {}
        for it in t.items:
            cl = _current(conn, study_id, claude_id, t.key, it.id) if claude_id else None
            mi = _current(conn, study_id, me, t.key, it.id) if me else None
            use = mi or cl
            if use is not None:
                effective[it.id] = use["judgement"]
            items.append({"voce": it.id, "testo": it.text, "aiuto": it.hint, "sezione": it.section,
                          "scala": [{"codice": c, "etichetta": t.labels.get(c, c)} for c in it.codes()],
                          "stato": use["state"] if use else "vuoto", "giudizio": use["judgement"] if use else None,
                          "motivo": use["rationale"] if use else None, "citazione": use["quote"] if use else None,
                          "pagina": use["page_no"] if use else None,
                          "controllo_citazione": use["quote_check"] if use else "none",
                          "claude": ({"giudizio": cl["judgement"], "motivo": cl["rationale"]}
                                     if cl and mi and mi["state"] == "edited" else None)})
        out = {"tipo": kind, "strumento": t.key, "nome": t.name, "uso": t.use, "voci": items,
               "etichette": dict(t.labels), "giudicate": len(effective), "totale": len(t.items)}
        if kind == "rob":
            out["suggerito"] = tl.rob_overall(effective, t)
            if t.mode != "stars":
                fin = _current(conn, study_id, me, t.key, OVERALL) if me else None
                out["complessivo"] = {"giudizio": fin["judgement"] if fin else None, "motivo": fin["rationale"] if fin else None,
                                      "scala": [{"codice": c, "etichetta": t.labels.get(c, c)} for c in t.order]}
        else:
            out["punteggio"] = tl.qual_score(t, effective)
        tools_out.append(out)
    have = chosen(conn, study_id)
    return {"studio": {"id": st["id"], "etichetta": st["label"], "titolo": rec["title"] if rec else st["label"],
                       "anno": rec["year"] if rec else None, "autori": rec["authors"] if rec else None,
                       "rivista": rec["journal"] if rec else None, "articolo": st["article_id"],
                       "documento": st["document_id"], "pagine": pages[0] if pages else None},
            "scelti": have, "proposta": propose(conn, review_id, study_id), "strumenti": tools_out,
            "esatte_da_confermare": sum(1 for s in tools_out for x in s["voci"]
                                        if x["stato"] == "ai" and x["controllo_citazione"] == "exact")}


def board(conn: sqlite3.Connection, home: Path, review_id: str, *, label: str | None = None) -> dict:
    if conn.execute("SELECT 1 FROM review WHERE id = ?", (review_id,)).fetchone() is None:
        raise AppraisalError("revisione non trovata")
    rows = []
    for s in ex.included(conn, review_id):
        v = study(conn, review_id, s["id"], label=label)
        rob = next((x for x in v["strumenti"] if x["tipo"] == "rob"), None)
        qual = next((x for x in v["strumenti"] if x["tipo"] == "quality"), None)
        rows.append({"id": s["id"], "titolo": s["title"] or s["label"], "anno": s["year"], "pdf": bool(s["document_id"]),
                     "scelti": v["scelti"], "proposta": v["proposta"],
                     "rob": rob and {"nome": rob["nome"], "giudicate": rob["giudicate"], "totale": rob["totale"],
                                     "suggerito": rob["suggerito"], "complessivo": (rob.get("complessivo") or {}).get("giudizio")},
                     "qualita": qual and {"nome": qual["nome"], "giudicate": qual["giudicate"], "totale": qual["totale"],
                                          "punteggio": qual["punteggio"]},
                     "da_controllare": sum(1 for x in v["strumenti"] for y in x["voci"] if y["stato"] in ("ai", "vuoto"))})
    return {"studi": rows, "catalogo": catalogue(), "cartella": str(rb_dir(conn, home, review_id)),
            "conteggi": {"inclusi": len(rows), "senza_strumenti": sum(1 for r in rows if "rob" not in r["scelti"]
                                                                       and "quality" not in r["scelti"]),
                         "da_controllare": sum(r["da_controllare"] for r in rows)},
            "claude": {"pacchetti_aperti": len(open_packets(conn, review_id)), "da_giudicare": len(to_judge(conn, review_id))}}
