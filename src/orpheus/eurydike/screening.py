"""EURYDIKE E4 (T-051e): screening on title and abstract — lots of 25 records for Claude (contract eurydike-screen-v1,
EURYDIKE_CONTRATTI.md §1) and the decisions of the user from the dashboard.

Decision of the user (2026-11-01 00:xx, «Screening ora»): screening first, with test data; the test with real exports
(T-051d) comes later. From the plan (EURYDIKE_PIANO.md D6-D7, E4): lots of 25; Claude is the second reviewer and screens
**blind** (a packet never holds a decision of another reviewer: test); the user decides in the dashboard one record at a
time (keys I / E / M) without seeing Claude's decision first; agreement (κ), conflicts and the consensus as in Tabula.

Flow (nothing reaches the database without a command or a button of the user):
  1. «Prepara i lotti per Claude» (or `orpheus eurydike-screening --revisione ID --lotti N`): one task and one
     workflow_run (executor claude_session) per lot; packet `revisioni/<nome>/screening/pacchetti/<lotto>.json` with the
     protocol (question, PICOS, criteria with id) and 25 records {id, title, year, abstract[:2000]}.
  2. Claude, in a session, writes `screening/da_inviare/<lotto>.json` (format eurydike-screen-v1) and checks it with
     `orpheus eurydike-screening --controlla RISPOSTA PACCHETTO` (no database).
  3. «Leggi le risposte di Claude» (or `--invia`): every answer checked again on the database; accepted decisions enter
     `screening_decision` with origin 'model' and the workflow_run; a refused answer stays with a `.motivi.txt`.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from .. import audit, card_store
from ..db import transaction
from ..ids import new_id
from . import protocol
from . import review as rv

CONTRACT = "eurydike-screen-v1"
PACKET_FORMAT = "eurydike-pacchetto-screening-v1"
LOT = 25
ABSTRACT_MAX = 2000
RATIONALE_WORDS = 25
MAX_OPEN = 40
SCREEN_DIR, PACKETS, TO_SEND, ACCEPTED = "screening", "pacchetti", "da_inviare", "accettate"
TASK_KEY = "T-051e:screen:{review_id}:{first}"
CONFIDENCE = ("high", "medium", "low")
FORBIDDEN_KEYS = ("decisioni", "decisions", "decision", "decisione", "screening_decision", "finale", "revisori")

INSTRUCTIONS = [
    "You are an expert reviewer screening titles and abstracts for a systematic review. Apply the criteria "
    "conservatively: if in doubt, or if the abstract is missing or insufficient, choose \"maybe\" (the full text will "
    "decide). Decide from the study aim and results, not from the presence of a word.",
    "Write ONE JSON file: screening/da_inviare/<packet file name>, format 'eurydike-screen-v1', with packet_id copied "
    "from this packet and 'model' = your model name; reply with one object per record, same order, every record once.",
    "decision: include, exclude or maybe; confidence: high, medium or low; rationale: at most 25 words, English, naming "
    "the criterion; criterion_id only for exclude (the id of an exclusion criterion of the protocol).",
]
EXAMPLE = {"format": CONTRACT, "packet_id": "run_<copy>", "model": "claude-opus-5-5",
           "decisions": [{"record_id": "rrc_<copy>", "decision": "exclude", "confidence": "high",
                          "rationale": "Narrative review, not an original study (exclusion 2).",
                          "criterion_id": "rcr_<copy>"}]}


class ScreeningError(rv.ReviewError):
    """A request the screening refuses (message in Italian)."""


# ------------------------------------------------------------------ folders and reviewers
def screen_dir(conn: sqlite3.Connection, home: Path, review_id: str) -> Path:
    r = conn.execute("SELECT data_dir FROM review WHERE id = ?", (review_id,)).fetchone()
    if r is None:
        raise ScreeningError("revisione non trovata")
    return protocol.review_dir(Path(home), r[0]) / SCREEN_DIR


def _claude_id(conn: sqlite3.Connection, review_id: str) -> str | None:
    r = conn.execute("SELECT id FROM review_reviewer WHERE review_id = ? AND kind = 'claude_session' "
                     "ORDER BY created_at, rowid", (review_id,)).fetchone()
    return r[0] if r else None


def claude_reviewer(conn: sqlite3.Connection, review_id: str) -> str:
    """The reviewer that is Claude in a session (created with the review; added the first time if missing)."""
    r = _claude_id(conn, review_id)
    return r if r else rv.add_reviewer(conn, review_id, kind="claude_session", label="Claude",
                                         model_ref="claude (sessione)", actor="orpheus")


def human_reviewer(conn: sqlite3.Connection, review_id: str, label: str | None = None) -> sqlite3.Row:
    """The human reviewer who decides from the dashboard: the one with that label, else the first human."""
    rows = conn.execute("SELECT * FROM review_reviewer WHERE review_id = ? AND kind = 'human' ORDER BY created_at, rowid",
                        (review_id,)).fetchall()
    if not rows:
        raise ScreeningError("la revisione non ha un revisore umano")
    for r in rows:
        if label and r["label"] == label:
            return r
    return rows[0]


def _criteria(conn: sqlite3.Connection, review_id: str) -> list[dict]:
    return [{"id": c["id"], "tipo": c["kind"], "ordine": c["ord"], "testo": c["text"]}
            for c in rv.current_criteria(conn, review_id) if c["kind"] in ("inclusion", "exclusion")]


# ------------------------------------------------------------------ lots for Claude
def open_lots(conn: sqlite3.Connection, review_id: str) -> list[sqlite3.Row]:
    out = []
    for r in conn.execute("""SELECT w.* FROM workflow_run w JOIN task t ON t.id = w.task_id
                             WHERE w.status = 'running' AND t.idempotency_key LIKE ? ORDER BY w.started_at, w.rowid""",
                          (f"T-051e:screen:{review_id}:%",)):
        out.append(r)
    return out


def _lot_records(run: sqlite3.Row) -> list[str]:
    return json.loads(run["parameters"])["record"]


def to_screen_by_claude(conn: sqlite3.Connection, review_id: str) -> list[str]:
    """Records that are not duplicates, without a current decision of Claude, not in an open lot and not proposed for
    exclusion by a bulk rule (import order)."""
    claude = _claude_id(conn, review_id)
    decided = {r[0] for r in conn.execute("SELECT record_id FROM screening_decision WHERE reviewer_id = ? "
                                          "AND superseded_by IS NULL", (claude,))} if claude else set()
    in_lot = {x for run in open_lots(conn, review_id) for x in _lot_records(run)}
    from .rules import skipped_for_claude        # pre-filter: records a rule proposes to exclude (0.70.0)
    skip = skipped_for_claude(conn, review_id)
    return [r[0] for r in conn.execute("SELECT id FROM review_record WHERE review_id = ? AND duplicate_of IS NULL "
                                       "ORDER BY created_at, rowid", (review_id,))
            if r[0] not in decided and r[0] not in in_lot and r[0] not in skip]


def packet(conn: sqlite3.Connection, review_id: str, run: dict) -> dict:
    """The packet of one lot: protocol and records only — never a decision (blind, D6)."""
    r = conn.execute("SELECT * FROM review WHERE id = ?", (review_id,)).fetchone()
    ids = run["parameters"]["record"]
    marks = ",".join("?" * len(ids))
    recs = {x["id"]: x for x in conn.execute(f"SELECT id, title, year, abstract FROM review_record WHERE id IN ({marks})",
                                              ids)}
    return {"formato": PACKET_FORMAT, "contratto": CONTRACT, "packet_id": run["id"], "review_id": review_id,
            "lotto": run["parameters"]["lotto"], "emesso_il": run["started_at"], "istruzioni": INSTRUCTIONS,
            "protocollo": {"titolo": r["title"], "domanda": r["question"], "picos": json.loads(r["pico"] or "{}"),
                           "criteri": _criteria(conn, review_id)},
            "record": [{"id": i, "title": recs[i]["title"], "year": recs[i]["year"],
                        "abstract": (recs[i]["abstract"] or "")[:ABSTRACT_MAX] or None} for i in ids],
            "esempio": EXAMPLE}


def blind(pk: dict) -> list[str]:
    """Problems if a packet carries anything that is a decision (test and check before writing the file)."""
    probs = []

    def walk(x, where):
        if isinstance(x, dict):
            for k, v in x.items():
                if k in FORBIDDEN_KEYS and where != "esempio":
                    probs.append(f"il pacchetto contiene «{k}» ({where}): deve essere cieco")
                walk(v, where if where != "root" else k)
        elif isinstance(x, list):
            for v in x:
                walk(v, where)
    walk(pk, "root")
    return probs


def issue_lots(conn: sqlite3.Connection, home: Path, review_id: str, lots: int, *, project_name: str = "ORPHEUS",
               project_version: str = "", actor: str = "orpheus eurydike-screening") -> dict:
    """Open lots until `lots` are open (25 records each, the last may be shorter); the packets of the open lots are
    written again (idempotent). Returns {"aperti", "nuovi", "record_da_schermare", "cartella"}."""
    if lots > MAX_OPEN:
        raise ScreeningError(f"al massimo {MAX_OPEN} lotti aperti alla volta")
    claude_reviewer(conn, review_id)
    opened = open_lots(conn, review_id)
    todo = to_screen_by_claude(conn, review_id)
    chunks = [todo[i:i + LOT] for i in range(0, len(todo), LOT)][:max(0, lots - len(opened))]
    folder = screen_dir(conn, home, review_id)
    new_runs = []
    if chunks:
        with transaction(conn):
            project_id = card_store._ensure_project(conn, project_name, project_version)
            agent_id = card_store._ensure_agent(conn)
            model_id = card_store._ensure_model(conn, card_store.GENERIC_MODEL)
            n0 = conn.execute("SELECT count(*) FROM task WHERE idempotency_key LIKE ?",
                              (f"T-051e:screen:{review_id}:%",)).fetchone()[0]
            for i, ids in enumerate(chunks, 1):
                lot_no = n0 + i
                task_id = new_id("task")
                conn.execute("""INSERT INTO task (id, project_id, title, priority, status, owner_agent_id, expected_output,
                                                  acceptance_criteria, idempotency_key)
                                VALUES (?, ?, ?, 2, 'ready', ?, ?, ?, ?)""",
                             (task_id, project_id, f"Screening ({CONTRACT}) lotto {lot_no} della revisione {review_id}",
                              agent_id, f"{SCREEN_DIR}/{TO_SEND}/lotto_{lot_no:03d}.json (formato {CONTRACT})",
                              f"supera i controlli di {CONTRACT}", TASK_KEY.format(review_id=review_id, first=ids[0])))
                params = {"contratto": CONTRACT, "review_id": review_id, "lotto": lot_no, "record": ids,
                          "packet_file": f"{SCREEN_DIR}/{PACKETS}/lotto_{lot_no:03d}.json"}
                run_id = new_id("workflow_run")
                conn.execute("""INSERT INTO workflow_run (id, task_id, agent_id, executor, model_id, started_at, status,
                                                          parameters, validation_status)
                                VALUES (?, ?, ?, 'claude_session', ?, ?, 'running', ?, 'pending')""",
                             (run_id, task_id, agent_id, model_id, audit.utc_now(),
                              json.dumps(params, ensure_ascii=False, sort_keys=True)))
                new_runs.append(run_id)
            audit.record(conn, actor=actor, action="screening_lots_issued", entity_type="review", entity_id=review_id,
                         payload={"contract": CONTRACT, "new_runs": new_runs, "records": sum(len(c) for c in chunks)},
                         in_transaction=True)
    written = []
    for run in open_lots(conn, review_id):
        params = json.loads(run["parameters"])
        pk = packet(conn, review_id, {"id": run["id"], "started_at": run["started_at"], "parameters": params})
        probs = blind(pk)
        if probs:                                                     # never written: a bug, not a user error
            raise ScreeningError("; ".join(probs))
        path = folder / PACKETS / f"lotto_{params['lotto']:03d}.json"
        card_store._write_if_changed(path, json.dumps(pk, ensure_ascii=False, indent=1).encode("utf-8"))
        written.append({"lotto": params["lotto"], "file": str(path), "record": len(params["record"]),
                        "nuovo": run["id"] in new_runs})
    (folder / TO_SEND).mkdir(parents=True, exist_ok=True)
    return {"aperti": written, "nuovi": len(new_runs), "record_da_schermare": len(to_screen_by_claude(conn, review_id)),
            "cartella": str(folder)}


# ------------------------------------------------------------------ checks of Claude's answer
def check(data, pk: dict) -> list[str]:
    """The checks of eurydike-screen-v1 on an answer against its packet (no database). Empty list = acceptable."""
    if not isinstance(data, dict):
        return ["la risposta non è un oggetto JSON"]
    probs = []
    if data.get("format") != CONTRACT:
        probs.append(f"formato: serve '{CONTRACT}'")
    if data.get("packet_id") != pk.get("packet_id"):
        probs.append("packet_id diverso da quello del pacchetto")
    model = data.get("model")
    if not isinstance(model, str) or not 3 <= len(model) <= 80:
        probs.append("model: il nome del modello (3-80 caratteri)")
    decisions = data.get("decisions")
    if not isinstance(decisions, list):
        return probs + ["decisions: serve un elenco"]
    wanted = [r["id"] for r in pk["record"]]
    excl = {c["id"] for c in pk["protocollo"]["criteri"] if c["tipo"] == "exclusion"}
    seen = []
    for i, d in enumerate(decisions, 1):
        lab = f"decisione {i}"
        if not isinstance(d, dict):
            probs.append(f"{lab}: non è un oggetto")
            continue
        extra = set(d) - {"record_id", "decision", "confidence", "rationale", "criterion_id"}
        if extra:
            probs.append(f"{lab}: campi non previsti {sorted(extra)}")
        rid = d.get("record_id")
        lab = f"decisione {i} ({rid})"
        seen.append(rid)
        if d.get("decision") not in rv.DECISIONS:
            probs.append(f"{lab}: decisione '{d.get('decision')}' non ammessa (include, exclude, maybe)")
        if d.get("confidence") is not None and d.get("confidence") not in CONFIDENCE:
            probs.append(f"{lab}: confidence '{d.get('confidence')}' non ammessa (high, medium, low)")
        why = d.get("rationale")
        if not isinstance(why, str) or not why.strip():
            probs.append(f"{lab}: manca il motivo (rationale)")
        elif len(why.split()) > RATIONALE_WORDS:
            probs.append(f"{lab}: motivo di {len(why.split())} parole (al massimo {RATIONALE_WORDS})")
        crit = d.get("criterion_id")
        if d.get("decision") == "exclude":
            if crit not in excl:
                probs.append(f"{lab}: per exclude serve l'id di un criterio di esclusione del protocollo")
        elif crit not in (None, ""):
            probs.append(f"{lab}: criterion_id solo per exclude")
    if sorted(x for x in seen if isinstance(x, str)) != sorted(wanted) or len(seen) != len(set(map(str, seen))):
        missing = [x for x in wanted if x not in seen]
        unknown = [x for x in seen if x not in wanted]
        dup = sorted({str(x) for x in seen if seen.count(x) > 1})
        probs.append("tutti_i_record: servono esattamente i record del pacchetto, una volta ciascuno"
                     + (f"; mancano {len(missing)}" if missing else "") + (f"; non del pacchetto {unknown[:3]}" if unknown else "")
                     + (f"; ripetuti {dup[:3]}" if dup else ""))
    return probs


def submit(conn: sqlite3.Connection, home: Path, review_id: str, *, actor: str = "claude_session") -> dict:
    """The answers in screening/da_inviare: checked again against the database, accepted ones written as decisions of
    Claude (origin 'model'); a refused file stays with `<name>.motivi.txt`."""
    folder = screen_dir(conn, home, review_id)
    results = []
    claude = claude_reviewer(conn, review_id)
    for path in sorted((folder / TO_SEND).glob("*.json")) if (folder / TO_SEND).is_dir() else []:
        raw = path.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        res = {"file": path.name}
        try:
            data = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            data, stop = None, f"file non leggibile o non JSON: {exc}"
        else:
            stop = None
        run = None
        if stop is None:
            pid = data.get("packet_id") if isinstance(data, dict) else None
            run = conn.execute("SELECT * FROM workflow_run WHERE id = ?", (pid,)).fetchone() if isinstance(pid, str) else None
            params = json.loads(run["parameters"]) if run else {}
            if run is None or params.get("contratto") != CONTRACT:
                stop = "packet_id mancante o non è un lotto di screening di questo database (copialo dal pacchetto)"
            elif params.get("review_id") != review_id:
                stop = "il lotto è di un'altra revisione"
            elif run["status"] == "succeeded":
                stop = "lotto già accettato"
            elif run["status"] != "running":
                stop = f"il lotto non è più valido ({run['status']})"
        if stop is None:
            pk = packet(conn, review_id, {"id": run["id"], "started_at": run["started_at"], "parameters": params})
            probs = check(data, pk)
        else:
            probs = [stop]
        if not probs:
            for d in data["decisions"]:
                rv.decide(conn, d["record_id"], claude, d["decision"], criterion_id=d.get("criterion_id") or None,
                          rationale=d["rationale"].strip(), confidence=d.get("confidence"), origin="model",
                          workflow_run_id=run["id"], actor=actor)
        with transaction(conn):
            if run is not None:
                conn.execute("""INSERT INTO validation_result (id, workflow_run_id, check_name, outcome, validator, details)
                                VALUES (?, ?, ?, ?, 'deterministic', ?)""",
                             (new_id("validation_result"), run["id"], CONTRACT, "fail" if probs else "pass",
                              json.dumps({"file": path.name, "file_sha256": sha, "problemi": probs[:50]},
                                         ensure_ascii=False)))
                if probs:
                    conn.execute("UPDATE workflow_run SET validation_status = 'fail' WHERE id = ? AND status = 'running'",
                                 (run["id"],))
                else:
                    conn.execute("""UPDATE workflow_run SET status = 'succeeded', finished_at = ?, validation_status = 'pass',
                                    model_id = ? WHERE id = ?""",
                                 (max(audit.utc_now(), run["started_at"]),
                                  card_store._ensure_model(conn, data["model"]), run["id"]))
                    conn.execute("UPDATE task SET status = 'done' WHERE id = ?", (run["task_id"],))
            audit.record(conn, actor="orpheus eurydike-screening", action="screening_answer_" +
                         ("refused" if probs else "accepted"), entity_type="review", entity_id=review_id,
                         payload={"file_sha256": sha, "workflow_run_id": run["id"] if run else None},
                         in_transaction=True)
        motivi = path.with_name(path.stem + ".motivi.txt")
        if probs:
            motivi.write_text("Risposta rifiutata (eurydike-screen-v1):\n- " + "\n- ".join(probs[:60]) + "\n",
                              encoding="utf-8")
            res |= {"esito": "rifiutata", "motivi": probs[:20]}
        else:
            dest = folder / ACCEPTED / path.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                dest = dest.with_name(f"{path.stem}_{sha[:8]}{path.suffix}")
            path.replace(dest)
            if motivi.exists():
                motivi.unlink()
            res |= {"esito": "accettata", "decisioni": len(data["decisions"])}
        results.append(res)
    return {"inviate": len(results), "accettate": sum(r["esito"] == "accettata" for r in results),
            "rifiutate": sum(r["esito"] != "accettata" for r in results), "risposte": results}


def check_with_packet_files(answer: Path, pk_path: Path) -> list[str]:
    return check(json.loads(Path(answer).read_text(encoding="utf-8-sig")),
                 json.loads(Path(pk_path).read_text(encoding="utf-8-sig")))


# ------------------------------------------------------------------ the user's decisions (dashboard)
def next_for_user(conn: sqlite3.Connection, review_id: str, label: str | None = None) -> dict:
    """The next record the human reviewer has not decided yet (import order), with the criteria — never another
    reviewer's decision before the user's own (blind)."""
    me = human_reviewer(conn, review_id, label)
    decided = {r[0] for r in conn.execute("SELECT record_id FROM screening_decision WHERE reviewer_id = ? "
                                          "AND superseded_by IS NULL", (me["id"],))}
    ids = [r[0] for r in conn.execute("SELECT id FROM review_record WHERE review_id = ? AND duplicate_of IS NULL "
                                      "ORDER BY created_at, rowid", (review_id,))]
    left = [i for i in ids if i not in decided]
    rec = None
    if left:
        r = conn.execute("SELECT * FROM review_record WHERE id = ?", (left[0],)).fetchone()
        rec = {"id": r["id"], "titolo": r["title"], "abstract": r["abstract"], "autori": r["authors"], "anno": r["year"],
               "rivista": r["journal"], "doi": r["doi"], "pmid": r["pmid"]}
    return {"revisore": me["label"], "record": rec, "fatti": len(ids) - len(left), "totale": len(ids),
            "restano": len(left), "criteri": _criteria(conn, review_id)}


def decide_user(conn: sqlite3.Connection, review_id: str, record_id: str, decision: str, *,
                criterion_id: str | None = None, rationale: str | None = None, label: str | None = None,
                consensus: bool = False) -> dict:
    """A decision of the user from the dashboard (or the consensus on a conflict)."""
    rec = conn.execute("SELECT review_id FROM review_record WHERE id = ?", (record_id,)).fetchone()
    if rec is None or rec[0] != review_id:
        raise ScreeningError("record non trovato in questa revisione")
    why = (rationale or "").strip() or None
    if consensus:
        out = rv.decide_consensus(conn, record_id, decision, criterion_id=criterion_id or None, rationale=why)
    else:
        me = human_reviewer(conn, review_id, label)
        out = rv.decide(conn, record_id, me["id"], decision, criterion_id=criterion_id or None, rationale=why)
    return {**out, "decisioni": rv.record_decisions(conn, record_id)}


def status(conn: sqlite3.Connection, review_id: str) -> dict:
    claude = _claude_id(conn, review_id)
    n = conn.execute("SELECT count(*) FROM review_record WHERE review_id = ? AND duplicate_of IS NULL",
                     (review_id,)).fetchone()[0]
    by_claude = conn.execute("SELECT count(*) FROM screening_decision WHERE reviewer_id = ? AND superseded_by IS NULL",
                             (claude,)).fetchone()[0]
    return {"record": n, "decisi_da_claude": by_claude, "lotti_aperti": len(open_lots(conn, review_id)),
            "da_mettere_in_lotti": len(to_screen_by_claude(conn, review_id)), "finali": rv.screening_status(conn, review_id)}


