"""EURYDIKE in the dashboard (E2b, T-051n): read-only views of a systematic review for the sections of the module.

Nothing here writes. Every number is computed from the stored rows with the functions of `core` (final screening
decision, PRISMA counts and their check, Cohen's kappa, risk-of-bias suggestion, quality score), never typed; every
block carries the tables it reads ("fonte") and the newest date among them ("data"), as every widget of the dashboard.

Sections (EURYDIKE_PIANO.md §9): Panoramica della revisione, Protocollo, Screening, Full text, Estrazione (the cards
of the included studies, shared with ORPHEUS), Rischio di bias, Consenso, Meta-analisi, Esporta. Next to every phase
the number of things to do (`da_fare`)."""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict

from .core.agreement import screening_kappa
from .core.screening import CONSENSUS, final_decision, flow_problems, prisma_counts
from .core.tools import get_tool, qual_score, rob_overall

KIND_LABELS = {"systematic_review": "revisione sistematica", "meta_analysis": "meta-analisi",
               "scoping_review": "scoping review"}
STATUS_LABELS = {"protocol": "protocollo", "screening": "screening", "fulltext": "full text",
                 "extraction": "estrazione", "analysis": "analisi", "closed": "chiusa"}
REVIEWER_LABELS = {"human": "umano", "claude_session": "Claude (sessione)", "chat_manuale": "chat a mano",
                   "consensus": "consenso"}
FILTERS = ("tutti", "da_decidere", "parziali", "conflitti", "inclusi", "esclusi", "forse", "doppioni")
PAGE = 100


def _src(fonte: str, data) -> dict:
    return {"fonte": fonte, "data": data}


def _max(*values):
    vs = [v for v in values if v]
    return max(vs) if vs else None


def _review(conn: sqlite3.Connection, review_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM review WHERE id = ?", (review_id,)).fetchone()


def _decisions(conn: sqlite3.Connection, review_id: str) -> dict[str, dict[str, dict]]:
    """record id -> {reviewer label or _consenso -> {"d", "r", "kind", "c"}} (current decisions, in decision order)."""
    out: dict[str, dict[str, dict]] = defaultdict(dict)
    for r in conn.execute("""SELECT d.record_id, d.decision, d.rationale, d.criterion_id, v.kind, v.label
                             FROM screening_decision d JOIN review_reviewer v ON v.id = d.reviewer_id
                             WHERE v.review_id = ? AND d.superseded_by IS NULL ORDER BY d.created_at, d.rowid""",
                          (review_id,)):
        key = CONSENSUS if r["kind"] == "consensus" else r["label"]
        out[r["record_id"]][key] = {"d": r["decision"], "r": r["rationale"] or "", "kind": r["kind"],
                                    "c": r["criterion_id"]}
    return out


def _finals(conn: sqlite3.Connection, review_id: str, need: int, decisions: dict) -> dict[str, dict]:
    """Final decision of every record that is not a duplicate (computed, never stored)."""
    out = {}
    for (rid,) in conn.execute("SELECT id FROM review_record WHERE review_id = ? AND duplicate_of IS NULL", (review_id,)):
        out[rid] = final_decision({k: {"d": v["d"], "r": v["r"]} for k, v in decisions.get(rid, {}).items()}, need=need)
    return out


def _bucket(f: dict) -> str:
    if f["status"] in ("agreed", "resolved"):
        return {"include": "inclusi", "exclude": "esclusi", "maybe": "forse"}.get(f["d"], "forse")
    return {"pending": "da_decidere", "partial": "parziali", "conflict": "conflitti"}[f["status"]]


def _kappa(conn: sqlite3.Connection, review_id: str, decisions: dict) -> dict | None:
    """User–Claude agreement (D6): the first human reviewer against the first reviewer that is a model; with no model,
    the first two humans. None with fewer than two reviewers or no record decided by both."""
    revs = conn.execute("SELECT kind, label FROM review_reviewer WHERE review_id = ? AND kind <> 'consensus' "
                        "ORDER BY created_at, rowid", (review_id,)).fetchall()
    humans = [r["label"] for r in revs if r["kind"] == "human"]
    models = [r["label"] for r in revs if r["kind"] != "human"]
    pair = (humans[0], models[0]) if humans and models else (tuple(humans[:2]) if len(humans) >= 2 else None)
    if not pair:
        return None
    a = {rid: d[pair[0]]["d"] for rid, d in decisions.items() if pair[0] in d}
    b = {rid: d[pair[1]]["d"] for rid, d in decisions.items() if pair[1] in d}
    k = screening_kappa(a, b)
    if k is None:
        return None
    return {**k, "tra": list(pair), "uno_e_claude": bool(humans and models)}


def _studies(conn: sqlite3.Connection, review_id: str) -> list[dict]:
    rows = conn.execute("""SELECT s.*, c.text AS criterio, c.ord AS criterio_ord, v.label AS deciso_da,
                                  d.page_count AS pagine, a.title AS titolo_articolo, a.doi AS doi_articolo,
                                  p.decision AS proposta, p.summary AS proposta_sintesi, p.rationale AS proposta_motivo,
                                  p.quote AS proposta_frase, p.page_no AS proposta_pagina, p.created_at AS proposta_data
                           FROM review_study s
                           LEFT JOIN review_criterion c ON c.id = s.ft_criterion_id
                           LEFT JOIN review_reviewer v ON v.id = s.ft_decided_by
                           LEFT JOIN document d ON d.id = s.document_id
                           LEFT JOIN article a ON a.id = s.article_id
                           LEFT JOIN ft_proposal p ON p.study_id = s.id AND p.superseded_by IS NULL
                           WHERE s.review_id = ? ORDER BY s.label, s.created_at""", (review_id,)).fetchall()
    return [dict(r) for r in rows]


def _card_summary(conn: sqlite3.Connection, article_id: str | None) -> dict | None:
    """Values of the shared card (cards-v2) of an article: how many, how many still to check."""
    if not article_id:
        return None
    items = conn.execute("SELECT state, count(*) FROM card_item WHERE article_id = ? AND superseded_by IS NULL "
                         "GROUP BY state", (article_id,)).fetchall()
    rows = conn.execute("SELECT state, count(*) FROM card_outcome_row WHERE article_id = ? AND superseded_by IS NULL "
                        "GROUP BY state", (article_id,)).fetchall()
    by: dict[str, int] = defaultdict(int)
    for st, n in list(items) + list(rows):
        by[st] += n
    total = sum(by.values())
    if not total:
        return None
    return {"valori": total, "da_controllare": by.get("ai", 0), "confermati": by.get("confirmed", 0),
            "corretti": by.get("edited", 0), "non_riportati": by.get("nr", 0)}


def _appraisals(conn: sqlite3.Connection, study_ids: list[str]) -> dict[str, dict]:
    """study id -> {"rob": {...}, "quality": {...}}: tool chosen, judgements of the reviewer who judged most
    (consensus or human first), computed suggestion."""
    out: dict[str, dict] = {sid: {} for sid in study_ids}
    if not study_ids:
        return out
    marks = ",".join("?" * len(study_ids))
    for r in conn.execute(f"""SELECT t.study_id, t.kind, t.tool, v.label FROM appraisal_tool_choice t
                              JOIN review_reviewer v ON v.id = t.chosen_by
                              WHERE t.superseded_by IS NULL AND t.study_id IN ({marks})""", study_ids):
        out[r["study_id"]][r["kind"]] = {"strumento": r["tool"], "scelto_da": r["label"]}
    judged: dict[tuple[str, str], dict[str, dict]] = defaultdict(lambda: defaultdict(dict))
    states: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    rank = {"consensus": 0, "human": 1}
    who: dict[tuple[str, str], tuple[int, str]] = {}
    for r in conn.execute(f"""SELECT a.study_id, a.tool, a.item_id, a.judgement, a.state, v.kind, v.label
                              FROM appraisal a JOIN review_reviewer v ON v.id = a.reviewer_id
                              WHERE a.superseded_by IS NULL AND a.study_id IN ({marks})""", study_ids):
        k = (r["study_id"], r["tool"])
        judged[k][r["label"]][r["item_id"]] = r["judgement"]
        states[k][r["state"]] += 1
        cand = (rank.get(r["kind"], 2), r["label"])
        if k not in who or cand < who[k]:
            who[k] = cand
    for sid, kinds in out.items():
        for kind, choice in kinds.items():
            key = (sid, choice["strumento"])
            try:
                tool = get_tool(choice["strumento"])
            except KeyError:
                choice.update({"nome": choice["strumento"], "voci": None, "giudicate": 0})
                continue
            label = who.get(key, (9, None))[1]
            d = judged[key].get(label, {}) if label else {}
            choice.update({"nome": tool.name, "voci": len(tool.items), "giudicate": len(d), "giudice": label,
                           "stati": dict(states[key])})
            if kind == "rob":
                choice["suggerito"] = rob_overall(d, tool) if d else None
            else:
                choice["punteggio"] = qual_score(tool, d) if d else None
    return out


# ------------------------------------------------------------------ public views

def reviews(conn: sqlite3.Connection) -> dict:
    """Every review, newest first, with its main numbers."""
    out = []
    for r in conn.execute("SELECT * FROM review ORDER BY created_at DESC, rowid DESC"):
        n = conn.execute("""SELECT count(*), sum(duplicate_of IS NOT NULL), max(created_at) FROM review_record
                            WHERE review_id = ?""", (r["id"],)).fetchone()
        st = conn.execute("SELECT count(*), sum(ft_decision = 'include') FROM review_study WHERE review_id = ?",
                          (r["id"],)).fetchone()
        out.append({"id": r["id"], "titolo": r["title"], "tipo": KIND_LABELS.get(r["kind"], r["kind"]),
                    "stato": STATUS_LABELS.get(r["status"], r["status"]), "creata": r["created_at"],
                    "record": (n[0] or 0) - (n[1] or 0), "doppioni": n[1] or 0, "studi": st[0] or 0,
                    "inclusi": st[1] or 0})
    data = _max(*(x["creata"] for x in out))
    return {"revisioni": out, **_src("review, review_record, review_study", data)}


def review_overview(conn: sqlite3.Connection, review_id: str) -> dict | None:
    """Everything the sections of the module show for one review (records are listed by `records`)."""
    rv = _review(conn, review_id)
    if rv is None:
        return None
    need = rv["screen_reviewers"]
    reviewers = [dict(r) for r in conn.execute("SELECT * FROM review_reviewer WHERE review_id = ? "
                                               "ORDER BY created_at, rowid", (review_id,))]
    criteria = [dict(r) for r in conn.execute("SELECT * FROM review_criterion WHERE review_id = ? "
                                              "ORDER BY kind, ord, created_at", (review_id,))]
    by_id = {c["id"]: c for c in criteria}
    imports = [dict(r) for r in conn.execute("SELECT * FROM search_import WHERE review_id = ? ORDER BY imported_at",
                                             (review_id,))]
    logs = [dict(r) for r in conn.execute("""SELECT l.*, v.label AS revisore FROM review_log l
                                             JOIN review_reviewer v ON v.id = l.reviewer_id
                                             WHERE l.review_id = ? ORDER BY l.created_at""", (review_id,))]
    decisions = _decisions(conn, review_id)
    finals = _finals(conn, review_id, need, decisions)
    buckets: dict[str, int] = defaultdict(int)
    for f in finals.values():
        buckets[_bucket(f)] += 1
    dupes = conn.execute("SELECT count(*) FROM review_record WHERE review_id = ? AND duplicate_of IS NOT NULL",
                         (review_id,)).fetchone()[0]
    last_decision = conn.execute("""SELECT max(d.created_at) FROM screening_decision d JOIN review_reviewer v
                                    ON v.id = d.reviewer_id WHERE v.review_id = ?""", (review_id,)).fetchone()[0]
    studies = _studies(conn, review_id)
    included = [s for s in studies if s["ft_decision"] == "include"]
    apprs = _appraisals(conn, [s["id"] for s in included])
    prisma = prisma_counts([{"raw": i["raw_count"], "dupes": i["dupes_count"]} for i in imports],
                           len(finals), finals,
                           [{"ft": {"not_retrieved": "na"}.get(s["ft_decision"], s["ft_decision"]),
                             "pages": s["pagine"] or 0} for s in studies])
    kappa = _kappa(conn, review_id, decisions)

    # full text and Claude's proposals
    ft_rows = []
    for s in studies:
        ft_rows.append({"id": s["id"], "studio": s["label"], "articolo": s["article_id"],
                        "titolo": s["titolo_articolo"], "doi": s["doi_articolo"], "pagine": s["pagine"],
                        "decisione": s["ft_decision"], "criterio": s["criterio"], "criterio_ord": s["criterio_ord"],
                        "nota": s["ft_note"], "deciso_da": s["deciso_da"], "deciso_il": s["ft_decided_at"],
                        "proposta": s["proposta"] and {"decisione": s["proposta"], "sintesi": s["proposta_sintesi"],
                                                       "motivo": s["proposta_motivo"], "frase": s["proposta_frase"],
                                                       "pagina": s["proposta_pagina"]}})
    ft_diff = [x for x in ft_rows if x["proposta"] and x["decisione"] in ("include", "exclude")
               and x["proposta"]["decisione"] in ("include", "exclude") and x["proposta"]["decisione"] != x["decisione"]]

    # extraction: the shared cards of the included studies
    extraction = []
    for s in included:
        extraction.append({"id": s["id"], "studio": s["label"], "articolo": s["article_id"],
                           "scheda": _card_summary(conn, s["article_id"])})

    # risk of bias and quality
    appraisal_rows = [{"id": s["id"], "studio": s["label"], "articolo": s["article_id"],
                       "rob": apprs[s["id"]].get("rob"), "qualita": apprs[s["id"]].get("quality")} for s in included]

    # consensus: conflicts at screening, Claude's full-text proposals different from the decision
    conflicts = []
    for rid, f in finals.items():
        if f["status"] == "conflict":
            conflicts.append(rid)
    conflict_rows = _record_rows(conn, review_id, conflicts, decisions, finals, by_id) if conflicts else []

    # meta-analyses
    analyses = []
    for a in conn.execute("""SELECT m.*, (SELECT count(*) FROM ma_input i WHERE i.analysis_id = m.id) AS k
                             FROM ma_analysis m WHERE m.review_id = ? ORDER BY m.created_at""", (review_id,)):
        try:
            res = json.loads(a["result"])
        except ValueError:
            res = {}
        analyses.append({"id": a["id"], "esito": a["outcome"], "tempo": a["timepoint"], "misura": a["measure"],
                         "modello": a["model"], "ic": a["ci_method"], "k": a["k"], "versione": a["code_version"],
                         "creata": a["created_at"],
                         "risultato": {k: res.get(k) for k in ("estimate", "ci_low", "ci_high", "p", "i2", "tau2",
                                                               "pi_low", "pi_high") if k in res}})

    cur_criteria = [c for c in criteria if c["superseded_by"] is None]
    to_do = {
        "protocollo": 0 if any(c["kind"] == "inclusion" for c in cur_criteria)
        and any(c["kind"] == "exclusion" for c in cur_criteria) else 1,
        "screening": buckets["da_decidere"] + buckets["parziali"],
        "fulltext": sum(1 for s in studies if s["ft_decision"] == "pending"),
        "estrazione": sum((x["scheda"] or {}).get("da_controllare", 0) for x in extraction)
        + sum(1 for x in extraction if x["scheda"] is None),
        "bias": sum(1 for x in appraisal_rows if not x["rob"] or not x["rob"].get("giudicate")),
        "consenso": len(conflicts) + len(ft_diff),
        "meta": 0,
        "esporta": 0,
    }
    data_all = _max(rv["created_at"], *(i["imported_at"] for i in imports), last_decision,
                    *(s["ft_decided_at"] for s in studies), *(s["created_at"] for s in studies))
    pico = {}
    try:
        pico = json.loads(rv["pico"] or "{}")
    except ValueError:
        pico = {}
    return {
        "revisione": {"id": rv["id"], "titolo": rv["title"], "domanda": rv["question"],
                      "tipo": KIND_LABELS.get(rv["kind"], rv["kind"]), "stato": STATUS_LABELS.get(rv["status"], rv["status"]),
                      "registrazione": rv["registration"], "strategia": rv["search_strategy"], "pico": pico,
                      "revisori_screening": need, "cieco": bool(rv["blind_reviewers"]), "cieco_ai": bool(rv["blind_ai"]),
                      "rob_predefinito": rv["rob_tool_default"], "qualita_predefinita": rv["qual_tool_default"],
                      "creata": rv["created_at"], "cartella": rv["data_dir"]},
        "da_fare": to_do,
        "protocollo": {
            "criteri": [{"id": c["id"], "tipo": c["kind"], "ord": c["ord"], "testo": c["text"],
                         "regola": json.loads(c["rule_json"]) if c["rule_json"] else None, "creato": c["created_at"],
                         "sostituito_da": c["superseded_by"]} for c in criteria],
            "revisori": [{"id": r["id"], "tipo": REVIEWER_LABELS.get(r["kind"], r["kind"]), "nome": r["label"],
                          "modello": r["model_ref"]} for r in reviewers],
            "importazioni": [{"fonte_file": i["source_name"], "banca_dati": i["database_name"], "formato": i["format"],
                              "cercato_il": i["searched_on"], "record": i["raw_count"], "doppioni": i["dupes_count"],
                              "importato_il": i["imported_at"], "sha256": i["file_sha256"]} for i in imports],
            "registro": [{"tipo": g["kind"], "titolo": g["title"], "perche": g["why"], "record": g["n_records"],
                          "revisore": g["revisore"], "data": g["created_at"]} for g in logs],
            **_src("review, review_criterion, review_reviewer, search_import, review_log",
                   _max(rv["created_at"], *(c["created_at"] for c in criteria), *(i["imported_at"] for i in imports))),
        },
        "screening": {"conteggi": {**{k: buckets.get(k, 0) for k in FILTERS if k not in ("tutti", "doppioni")},
                                   "doppioni": dupes, "tutti": len(finals)},
                      "kappa": kappa, "revisori_necessari": need,
                      **_src("review_record, screening_decision (decisione finale calcolata)", last_decision)},
        "prisma": {**prisma, "problemi": flow_problems(prisma),
                   **_src("search_import, review_record, screening_decision, review_study, document", data_all)},
        "fulltext": {"studi": ft_rows, **_src("review_study, ft_proposal, document, article",
                                               _max(*(s["ft_decided_at"] for s in studies),
                                                    *(s["created_at"] for s in studies),
                                                    *(s["proposta_data"] for s in studies)))},
        "estrazione": {"studi": extraction, **_src("review_study, card_item, card_outcome_row (cards-v2)",
                                                    _max(*(s["created_at"] for s in included)))},
        "bias": {"studi": appraisal_rows, **_src("appraisal_tool_choice, appraisal (strumenti di Tabula v19)",
                                                 _max(*(s["created_at"] for s in included)))},
        "consenso": {"conflitti": conflict_rows, "full_text_diversi": ft_diff, "kappa": kappa,
                     **_src("screening_decision, ft_proposal, review_study", last_decision)},
        "meta": {"analisi": analyses, **_src("ma_analysis, ma_input", _max(*(a["creata"] for a in analyses)))},
        **_src("tabelle di EURYDIKE (migrazioni 0014 e 0015)", data_all),
    }


def _record_rows(conn: sqlite3.Connection, review_id: str, ids: list[str], decisions: dict, finals: dict,
                 criteria: dict) -> list[dict]:
    if not ids:
        return []
    out = []
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        marks = ",".join("?" * len(chunk))
        rows = {r["id"]: r for r in conn.execute(f"SELECT * FROM review_record WHERE id IN ({marks})", chunk)}
        for rid in chunk:
            r = rows[rid]
            ds = decisions.get(rid, {})
            f = finals.get(rid) or {"d": None, "status": "doppione"}
            out.append({"id": rid, "titolo": r["title"], "anno": r["year"], "rivista": r["journal"], "doi": r["doi"],
                        "pmid": r["pmid"], "autori": r["authors"], "doppione_di": r["duplicate_of"],
                        "decisioni": [{"revisore": "Consenso" if k == CONSENSUS else k, "decisione": v["d"],
                                       "motivo": v["r"] or None, "tipo": v["kind"],
                                       "criterio": (criteria.get(v["c"]) or {}).get("ord")} for k, v in ds.items()],
                        "finale": f.get("d"), "stato": f["status"]})
    return out


def records(conn: sqlite3.Connection, review_id: str, *, filtro: str = "tutti", q: str = "", da: int = 0,
            quanti: int = PAGE) -> dict | None:
    """The records of a review for the Screening section: filtered by final decision, searched in title, authors,
    journal and DOI; one page at a time (titles and abstracts are contents: shown as they are)."""
    rv = _review(conn, review_id)
    if rv is None:
        return None
    if filtro not in FILTERS:
        filtro = "tutti"
    decisions = _decisions(conn, review_id)
    finals = _finals(conn, review_id, rv["screen_reviewers"], decisions)
    if filtro == "doppioni":
        ids = [r[0] for r in conn.execute("SELECT id FROM review_record WHERE review_id = ? AND duplicate_of IS NOT NULL "
                                          "ORDER BY created_at, rowid", (review_id,))]
    else:
        ids = [r[0] for r in conn.execute("SELECT id FROM review_record WHERE review_id = ? AND duplicate_of IS NULL "
                                          "ORDER BY created_at, rowid", (review_id,))]
        if filtro != "tutti":
            ids = [i for i in ids if _bucket(finals[i]) == filtro]
    q = (q or "").strip().lower()
    if q:
        hay = {r[0]: " ".join(x or "" for x in r[1:]).lower() for r in conn.execute(
            "SELECT id, title, authors, journal, doi FROM review_record WHERE review_id = ?", (review_id,))}
        ids = [i for i in ids if q in hay.get(i, "")]
    total = len(ids)
    da = max(0, int(da or 0))
    quanti = max(1, min(int(quanti or PAGE), 500))
    criteria = {c["id"]: dict(c) for c in conn.execute("SELECT * FROM review_criterion WHERE review_id = ?", (review_id,))}
    page = _record_rows(conn, review_id, ids[da:da + quanti], decisions, finals, criteria)
    return {"righe": page, "totale": total, "da": da, "quanti": quanti, "filtro": filtro,
            **_src("review_record, screening_decision (decisione finale calcolata)",
                   conn.execute("SELECT max(created_at) FROM review_record WHERE review_id = ?", (review_id,)).fetchone()[0])}


def article_reviews(conn: sqlite3.Connection, article_id: str) -> list[dict]:
    """The reviews in which an ORPHEUS article is a study ("usato nella revisione …")."""
    return [{"revisione": r["review_id"], "titolo": r["title"], "studio": r["label"], "full_text": r["ft_decision"]}
            for r in conn.execute("""SELECT s.review_id, s.label, s.ft_decision, v.title FROM review_study s
                                     JOIN review v ON v.id = s.review_id WHERE s.article_id = ?
                                     ORDER BY v.created_at""", (article_id,))]
