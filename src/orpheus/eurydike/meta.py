"""EURYDIKE E8–E9 — outcome rows of the included studies, conversions to mean and SD, meta-analyses (T-051i, T-051j).

E8 (eurydike-ma-v1, EURYDIKE_CONTRATTI.md §4). The outcome rows come from the unified card (card_outcome_row): Claude
writes them in the extraction packets (E6) with the statistics as written, the sentence or table row and the page; the
user confirms or corrects them. ORPHEUS converts to mean and SD (`core.stats.to_mean_sd`: reported; Luo 2018; Wan 2014;
SD from SE; SD from the 95% CI), always with the method next to the number. Only rows confirmed or corrected by the user
enter an analysis (decision of 2026-11-03, «Claude, tu confermi», as for E6 and E7): a statistic of Claude never reaches
the pooling without the user's check.

E9. An analysis is a plan chosen by the user in the dashboard — outcome and time label, measure (MD or SMD), model (FE,
RE-DL, RE-REML), CI (Wald or HKSJ) and, for every study, the experimental and the control row (with an optional sign
inversion for scales in the opposite direction) — computed by `core.ma_core` (checked against metafor). «Registra»
writes a snapshot: `ma_analysis` (plan, code version, SHA-256 of the inputs, result) and one `ma_input` per study (the
two rows, mean/SD/n as converted, the conversion methods, yi and vi). A registered analysis never changes (trigger):
when the rows change, the analysis is registered again. The HTML report with forest and funnel plots is written in
`revisioni\\<cartella>\\meta_analisi\\` on the PC.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .. import audit
from ..ids import new_id
from . import ma_plots
from . import review as rv
from . import store
from .core import ma_core
from .core.stats import REPORTED, num, row_problems, to_mean_sd

MA_DIR = "meta_analisi"
USABLE_STATES = ("confirmed", "edited")
CONTROL = re.compile(r"\b(control|controls|ctrl|con|placebo|sham|usual|standard care|no[- ]intervention|waitlist|"
                     r"wait[- ]list|comparator|cg)\b", re.I)


class MetaError(ValueError):
    """A problem for the user (message in Italian)."""


def _norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).casefold()


def ma_dir(conn: sqlite3.Connection, home: Path, review_id: str) -> Path:
    from .protocol import review_dir
    r = conn.execute("SELECT data_dir FROM review WHERE id = ?", (review_id,)).fetchone()
    if r is None:
        raise MetaError("revisione non trovata")
    return review_dir(home, r[0]) / MA_DIR


def _included(conn: sqlite3.Connection, review_id: str) -> list[dict]:
    if conn.execute("SELECT 1 FROM review WHERE id = ?", (review_id,)).fetchone() is None:
        raise MetaError("revisione non trovata")
    return [dict(r) for r in conn.execute(
        """SELECT s.id, s.label, s.article_id, r.title, r.year, r.doi FROM review_study s
           LEFT JOIN review_record r ON r.id = s.record_id
           WHERE s.review_id = ? AND s.ft_decision = 'include' ORDER BY s.created_at, s.rowid""", (review_id,))]


# ================================================================================================ E8: rows and conversions

def _mean_sd(row: dict) -> dict:
    """Mean and SD of one row, with the method. A reported mean and SD keep all their decimals (no rounding)."""
    conv = to_mean_sd(row)
    if conv is None:
        return {"media": None, "ds": None, "metodo": None}
    if conv["method"] == REPORTED:
        return {"media": num(row.get("mean")), "ds": num(row.get("sd")), "metodo": REPORTED}
    return {"media": conv["mean"], "ds": conv["sd"], "metodo": conv["method"]}


def row_view(r: dict) -> dict:
    stats = {k: r.get(k) for k in store.STATS}
    conv = _mean_sd(stats)
    probs = row_problems(stats)
    n = num(stats.get("n"))
    why = None
    if r["state"] not in USABLE_STATES:
        why = "da confermare"
    elif probs:
        why = probs[0]
    elif conv["media"] is None or conv["ds"] is None:
        why = "media o DS non ricavabili"
    elif n is None or n < 2 or n != int(n):
        why = "n mancante"
    return {"id": r["id"], "esito": r["outcome"], "gruppo": r["group_label"], "tempo": r["timepoint"],
            "unita": r["unit"], "statistiche": {k: v for k, v in stats.items() if v not in (None, "")},
            "n": n, **conv, "problemi": probs, "stato": r["state"], "chi": r["actor"], "frase": r["quote"],
            "pagina": r["page_no"], "tabella": r["table_label"], "verifica_frase": r["quote_check"],
            "usabile": why is None, "perche_no": why}


def outcomes(conn: sqlite3.Connection, review_id: str) -> dict:
    """The outcome rows of every included study, with the conversion to mean and SD and what is still to confirm."""
    studies, groups = [], {}
    for s in _included(conn, review_id):
        rows = [row_view(r) for r in store.current_outcome_rows(conn, s["article_id"])] if s["article_id"] else []
        studies.append({"id": s["id"], "studio": s["label"], "articolo": s["article_id"], "titolo": s["title"],
                        "anno": s["year"], "righe": rows,
                        "da_confermare": sum(1 for x in rows if x["stato"] == "ai")})
        for x in rows:
            key = (_norm(x["esito"]), _norm(x["tempo"]))
            g = groups.setdefault(key, {"esito": x["esito"], "tempo": x["tempo"], "studi": set(), "usabili": set()})
            g["studi"].add(s["id"])
            if x["usabile"]:
                g["usabili"].add(s["id"])
    esiti = sorted(({"esito": g["esito"], "tempo": g["tempo"], "studi": len(g["studi"]), "studi_pronti": len(g["usabili"])}
                    for g in groups.values()), key=lambda g: (-g["studi"], _norm(g["esito"]), _norm(g["tempo"])))
    return {"studi": studies, "esiti": esiti,
            "conteggi": {"studi": len(studies), "senza_articolo": sum(1 for s in studies if not s["articolo"]),
                         "senza_righe": sum(1 for s in studies if s["articolo"] and not s["righe"]),
                         "righe": sum(len(s["righe"]) for s in studies),
                         "da_confermare": sum(s["da_confermare"] for s in studies),
                         "usabili": sum(1 for s in studies for x in s["righe"] if x["usabile"])}}


def _row_of_review(conn: sqlite3.Connection, review_id: str, row_id: str) -> tuple[dict, dict]:
    r = conn.execute("SELECT * FROM card_outcome_row WHERE id = ?", (row_id,)).fetchone()
    if r is None:
        raise MetaError("riga di esito non trovata")
    r = dict(r)
    study = next((s for s in _included(conn, review_id) if s["article_id"] == r["article_id"]), None)
    if study is None:
        raise MetaError("la riga non è di uno studio incluso in questa revisione")
    return r, study


def confirm_row(conn: sqlite3.Connection, review_id: str, row_id: str, *, actor: str = "user") -> dict:
    """The user confirms one row of Claude as it is (to correct it: the card of the article, Correggi)."""
    r, _ = _row_of_review(conn, review_id, row_id)
    if r["superseded_by"]:
        raise MetaError("la riga è stata sostituita: ricarica la pagina")
    return store.set_outcome_row(conn, r["article_id"], r["outcome"], r["group_label"], r["timepoint"],
                                 state="confirmed", actor=actor)


def confirm_exact_rows(conn: sqlite3.Connection, review_id: str, *, study_id: str | None = None,
                       actor: str = "user") -> dict:
    """Confirm in one go the rows of Claude whose sentence was found exactly in the PDF and that have no problem."""
    n = left = 0
    for s in _included(conn, review_id):
        if not s["article_id"] or (study_id and s["id"] != study_id):
            continue
        for r in store.current_outcome_rows(conn, s["article_id"]):
            if r["state"] != "ai":
                continue
            if r["quote_check"] == "exact" and not row_problems({k: r[k] for k in store.STATS}):
                store.set_outcome_row(conn, r["article_id"], r["outcome"], r["group_label"], r["timepoint"],
                                      state="confirmed", actor=actor)
                n += 1
            else:
                left += 1
    return {"confermate": n, "da_guardare": left}


def edit_row(conn: sqlite3.Connection, review_id: str, study_id: str, *, outcome: str, group: str, timepoint: str,
             unit: str | None, stats: dict, quote: str | None = None, page: int | None = None,
             table: str | None = None, note: str | None = None, actor: str = "user") -> dict:
    """The user corrects or adds a row (numbers as written in the paper; ORPHEUS converts)."""
    study = next((s for s in _included(conn, review_id) if s["id"] == study_id), None)
    if study is None:
        raise MetaError("studio non incluso in questa revisione")
    if not study["article_id"]:
        raise MetaError("lo studio non ha ancora un articolo in ORPHEUS (Estrazione → «Collega gli articoli»)")
    for name, v in (("esito", outcome), ("gruppo", group), ("tempo", timepoint)):
        if not (v or "").strip():
            raise MetaError(f"manca {name}")
    clean = {k: (str(v).strip() if v not in (None, "") else None) for k, v in (stats or {}).items() if k in store.STATS}
    for k, v in clean.items():
        if v is not None and num(v) is None:
            raise MetaError(f"{k}: non è un numero")
    probs = row_problems(clean)
    if probs:
        raise MetaError("; ".join(probs))
    return store.set_outcome_row(conn, study["article_id"], outcome.strip(), group.strip(), timepoint.strip(),
                                 state="edited", unit=(unit or "").strip() or None, stats=clean, quote=quote, page=page,
                                 table_label=table, note=note, actor=actor)


# ================================================================================================ E9: plan, compute, register

def suggest(conn: sqlite3.Connection, review_id: str, outcome: str, timepoint: str | None) -> dict:
    """For every included study, the rows of this outcome and time, with a guess of the experimental and control row
    (the control is the row whose group says control, placebo, sham, usual care…). The user checks every choice."""
    want_o, want_t = _norm(outcome), _norm(timepoint)
    out = []
    for s in outcomes(conn, review_id)["studi"]:
        rows = [x for x in s["righe"] if _norm(x["esito"]) == want_o and (not want_t or _norm(x["tempo"]) == want_t)]
        ctrl = [x for x in rows if CONTROL.search(x["gruppo"] or "")]
        exp = [x for x in rows if x not in ctrl]
        guess_c = ctrl[0]["id"] if ctrl else None
        guess_e = exp[0]["id"] if exp and (ctrl or len(exp) > 1) else None
        out.append({"studio": s["id"], "etichetta": s["studio"], "righe": rows, "riga_e": guess_e, "riga_c": guess_c,
                    "tutte": s["righe"]})
    return {"esito": outcome, "tempo": timepoint, "studi": out}


def _plan(plan: dict) -> dict:
    if not isinstance(plan, dict):
        raise MetaError("piano dell'analisi non valido")
    p = {"nome": str(plan.get("nome") or "").strip(), "esito": str(plan.get("esito") or "").strip(),
         "tempo": str(plan.get("tempo") or "").strip() or None, "misura": plan.get("misura") or "SMD",
         "modello": plan.get("modello") or "RE-REML", "ic": plan.get("ic") or "wald",
         "sinistra": str(plan.get("sinistra") or "Favours control").strip(),
         "destra": str(plan.get("destra") or "Favours intervention").strip(), "coppie": []}
    if not p["esito"]:
        raise MetaError("manca l'esito dell'analisi")
    if p["misura"] not in ma_core.MEASURES:
        raise MetaError("misura: MD o SMD")
    if p["modello"] not in ma_core.MODELS:
        raise MetaError("modello: FE, RE-DL o RE-REML")
    if p["ic"] not in ma_core.CI_METHODS:
        raise MetaError("intervallo: wald o hksj")
    seen = set()
    for c in plan.get("coppie") or []:
        if not isinstance(c, dict) or not c.get("riga_e") or not c.get("riga_c"):
            continue                                            # a study left out of the analysis
        sid = str(c.get("studio") or "")
        if sid in seen:
            raise MetaError("uno studio compare due volte: una coppia di righe per studio")
        seen.add(sid)
        p["coppie"].append({"studio": sid, "riga_e": str(c["riga_e"]), "riga_c": str(c["riga_c"]),
                            "inverti": bool(c.get("inverti"))})
    if len(p["coppie"]) < 2:
        raise MetaError("servono almeno 2 studi con la riga sperimentale e quella di controllo")
    return p


def _inputs(conn: sqlite3.Connection, review_id: str, p: dict) -> tuple[list[dict], list[str]]:
    studies = {s["id"]: s for s in _included(conn, review_id)}
    inputs, problems = [], []
    units = set()
    for c in p["coppie"]:
        s = studies.get(c["studio"])
        if s is None:
            problems.append(f"studio {c['studio']}: non incluso in questa revisione")
            continue
        pair = []
        for side in ("riga_e", "riga_c"):
            r = conn.execute("SELECT * FROM card_outcome_row WHERE id = ?", (c[side],)).fetchone()
            if r is None or r["article_id"] != s["article_id"]:
                problems.append(f"{s['label']}: la riga scelta non è di questo studio")
                break
            if r["superseded_by"]:
                problems.append(f"{s['label']}: la riga scelta è stata corretta nel frattempo: sceglila di nuovo")
                break
            v = row_view(dict(r))
            if not v["usabile"]:
                problems.append(f"{s['label']} · {r['group_label']}: {v['perche_no']}")
                break
            pair.append((dict(r), v))
        if len(pair) < 2:
            continue
        (re_, ve), (rc, vc) = pair
        if re_["id"] == rc["id"]:
            problems.append(f"{s['label']}: la riga sperimentale e quella di controllo sono la stessa")
            continue
        units.update(u for u in (_norm(re_["unit"]), _norm(rc["unit"])) if u)
        try:
            eff = ma_core.effect(p["misura"], ve["media"], ve["ds"], ve["n"], vc["media"], vc["ds"], vc["n"])
        except ma_core.MAError as exc:
            problems.append(f"{s['label']}: {exc}")
            continue
        yi = -eff["yi"] if c["inverti"] else eff["yi"]
        inputs.append({"studio": s["id"], "etichetta": s["label"], "anno": s["year"], "riga_e": re_["id"],
                       "riga_c": rc["id"], "gruppo_e": re_["group_label"], "gruppo_c": rc["group_label"],
                       "media_e": ve["media"], "ds_e": ve["ds"], "n_e": ve["n"], "metodo_e": ve["metodo"],
                       "media_c": vc["media"], "ds_c": vc["ds"], "n_c": vc["n"], "metodo_c": vc["metodo"],
                       "inverti": c["inverti"], "yi": yi, "vi": eff["vi"]})
    if p["misura"] == "MD" and len(units) > 1:
        problems.append("MD con unità diverse fra gli studi (" + ", ".join(sorted(units)) + "): usa SMD")
    return inputs, problems


def _result(p: dict, inputs: list[dict]) -> dict:
    yi, vi = [x["yi"] for x in inputs], [x["vi"] for x in inputs]
    res = ma_core.analyse(yi, vi, model=p["modello"], ci=p["ic"])
    for st, x in zip(res["studies"], inputs):
        st["study_id"], st["label"] = x["studio"], x["etichetta"]
    for lo in res["leave_one_out"]:
        lo["label"] = inputs[lo["omitted"]]["etichetta"]
    return res


def _plots(p: dict, res: dict) -> dict:
    st = res["studies"]
    title = f"{p['nome'] or p['esito']}{' · ' + p['tempo'] if p['tempo'] else ''}"
    return {"forest": ma_plots.forest([x["label"] for x in st], [x["yi"] for x in st], [x["ci_low"] for x in st],
                                      [x["ci_high"] for x in st], [x["weight"] for x in st], res, measure=p["misura"],
                                      left=p["sinistra"], right=p["destra"], title=title),
            "funnel": ma_plots.funnel([x["yi"] for x in st], [x["se"] for x in st], res["estimate"],
                                      measure=p["misura"], title=title)}


def compute(conn: sqlite3.Connection, review_id: str, plan: dict) -> dict:
    """The analysis as it would be registered (nothing is written)."""
    p = _plan(plan)
    inputs, problems = _inputs(conn, review_id, p)
    if problems:
        return {"piano": p, "problemi": problems, "input": inputs, "risultato": None}
    res = _result(p, inputs)
    return {"piano": p, "problemi": [], "input": inputs, "risultato": res, **_plots(p, res)}


def _sha(inputs: list[dict]) -> str:
    keep = [{k: x[k] for k in ("studio", "riga_e", "riga_c", "media_e", "ds_e", "n_e", "metodo_e", "media_c", "ds_c",
                               "n_c", "metodo_c", "inverti", "yi", "vi")} for x in inputs]
    return hashlib.sha256(json.dumps(keep, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _code_version() -> str:
    try:
        from importlib.metadata import version
        return f"{ma_core.VERSION}; orpheus {version('orpheus')}"
    except Exception:                                           # noqa: BLE001 — not installed (tests from source)
        return ma_core.VERSION


def register(conn: sqlite3.Connection, home: Path | None, review_id: str, plan: dict, *, actor: str = "user") -> dict:
    """Write the snapshot of the analysis (ma_analysis + ma_input) and, with `home`, the HTML report on the PC."""
    out = compute(conn, review_id, plan)
    if out["problemi"]:
        raise MetaError("; ".join(out["problemi"]))
    p, inputs, res = out["piano"], out["input"], out["risultato"]
    mid = new_id("ma_analysis")
    settings = {k: p[k] for k in ("nome", "sinistra", "destra", "coppie")}
    with rv._write(conn):
        conn.execute("""INSERT INTO ma_analysis (id, review_id, outcome, timepoint, measure, model, ci_method, settings,
                                                 code_version, inputs_sha256, result)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                     (mid, review_id, p["esito"], p["tempo"], p["misura"], p["modello"], p["ic"],
                      json.dumps(settings, ensure_ascii=False), _code_version(), _sha(inputs),
                      json.dumps(res, ensure_ascii=False)))
        for x in inputs:
            conn.execute("""INSERT INTO ma_input (analysis_id, study_id, row_e_id, row_c_id, mean_e, sd_e, n_e, mean_c,
                                                  sd_c, n_c, conversion_e, conversion_c, yi, vi)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                         (mid, x["studio"], x["riga_e"], x["riga_c"], repr(x["media_e"]), repr(x["ds_e"]),
                          str(int(x["n_e"])), repr(x["media_c"]), repr(x["ds_c"]), str(int(x["n_c"])),
                          x["metodo_e"] + ("; sign inverted" if x["inverti"] else ""),
                          x["metodo_c"] + ("; sign inverted" if x["inverti"] else ""), x["yi"], x["vi"]))
        audit.record(conn, actor=actor, action="ma_analysis_registered", entity_type="ma_analysis", entity_id=mid,
                     payload={"review_id": review_id, "k": len(inputs), "measure": p["misura"], "model": p["modello"]},
                     in_transaction=True)
    report = None
    if home is not None:
        report = write_report(conn, home, review_id, mid)
    return {"id": mid, "k": len(inputs), "rapporto": str(report) if report else None}


# ================================================================================================ reading back

def _analysis_rows(conn: sqlite3.Connection, review_id: str, analysis_id: str) -> tuple[dict, list[dict]]:
    a = conn.execute("SELECT * FROM ma_analysis WHERE id = ? AND review_id = ?", (analysis_id, review_id)).fetchone()
    if a is None:
        raise MetaError("analisi non trovata")
    ins = [dict(r) for r in conn.execute(
        """SELECT i.*, s.label FROM ma_input i JOIN review_study s ON s.id = i.study_id WHERE i.analysis_id = ?
           ORDER BY i.rowid""", (analysis_id,))]
    return dict(a), ins


def analyses(conn: sqlite3.Connection, review_id: str) -> list[dict]:
    out = []
    for a in conn.execute("SELECT * FROM ma_analysis WHERE review_id = ? ORDER BY created_at, rowid", (review_id,)):
        res, settings = json.loads(a["result"]), json.loads(a["settings"])
        out.append({"id": a["id"], "nome": settings.get("nome") or a["outcome"], "esito": a["outcome"],
                    "tempo": a["timepoint"], "misura": a["measure"], "modello": a["model"], "ic": a["ci_method"],
                    "k": res.get("k"), "creata": a["created_at"], "versione": a["code_version"],
                    "risultato": {k: res.get(k) for k in ("estimate", "ci_low", "ci_high", "p", "i2", "tau2", "pi_low",
                                                          "pi_high", "q", "q_df", "q_p")},
                    "attuale": is_current(conn, a["id"])})
    return out


def is_current(conn: sqlite3.Connection, analysis_id: str) -> bool:
    """False when a row used by the analysis has been corrected or confirmed again after the registration."""
    return conn.execute("""SELECT count(*) FROM ma_input i JOIN card_outcome_row e ON e.id = i.row_e_id
                           JOIN card_outcome_row c ON c.id = i.row_c_id WHERE i.analysis_id = ?
                           AND (e.superseded_by IS NOT NULL OR c.superseded_by IS NOT NULL)""",
                        (analysis_id,)).fetchone()[0] == 0


def detail(conn: sqlite3.Connection, review_id: str, analysis_id: str) -> dict:
    a, ins = _analysis_rows(conn, review_id, analysis_id)
    res, settings = json.loads(a["result"]), json.loads(a["settings"])
    p = {"nome": settings.get("nome") or a["outcome"], "esito": a["outcome"], "tempo": a["timepoint"],
         "misura": a["measure"], "modello": a["model"], "ic": a["ci_method"],
         "sinistra": settings.get("sinistra") or "Favours control",
         "destra": settings.get("destra") or "Favours intervention", "coppie": settings.get("coppie") or []}
    return {"id": a["id"], "piano": p, "risultato": res, "creata": a["created_at"], "versione": a["code_version"],
            "sha256": a["inputs_sha256"], "attuale": is_current(conn, a["id"]),
            "input": [{"studio": x["study_id"], "etichetta": x["label"], "media_e": x["mean_e"], "ds_e": x["sd_e"],
                       "n_e": x["n_e"], "metodo_e": x["conversion_e"], "media_c": x["mean_c"], "ds_c": x["sd_c"],
                       "n_c": x["n_c"], "metodo_c": x["conversion_c"], "yi": x["yi"], "vi": x["vi"]} for x in ins],
            "metodi": methods_text(p, res), **_plots(p, res)}


def methods_text(p: dict, res: dict) -> str:
    """One English sentence on how the analysis was done (for the report and the draft of the Methods)."""
    measure = {"MD": "mean differences (MD)", "SMD": "standardised mean differences (Hedges' g with the exact "
               "small-sample correction)"}[p["misura"]]
    model = {"FE": "a fixed-effect (inverse-variance) model",
             "RE-DL": "a random-effects model with the DerSimonian-Laird estimator of τ²",
             "RE-REML": "a random-effects model with the restricted maximum-likelihood (REML) estimator of τ²"}[p["modello"]]
    ci = ("95% confidence intervals with the Hartung-Knapp-Sidik-Jonkman adjustment" if p["ic"] == "hksj"
          else "Wald-type 95% confidence intervals")
    txt = (f"Effects were expressed as {measure} and pooled with {model}, with {ci}; heterogeneity was quantified with "
           "Cochran's Q, I² and τ²")
    txt += ", and a 95% prediction interval was computed" if p["modello"] != "FE" else ""
    txt += (". Small-study effects were assessed with Egger's regression test." if res.get("egger")
            else ". Egger's test was not performed (fewer than 10 studies).")
    return txt


def write_report(conn: sqlite3.Connection, home: Path, review_id: str, analysis_id: str) -> Path:
    d = detail(conn, review_id, analysis_id)
    p, res = d["piano"], d["risultato"]
    f2 = lambda x: "—" if x is None else f"{x:.2f}"            # noqa: E731
    f3 = lambda x: "—" if x is None else (f"{x:.3f}" if x >= 0.001 else "<0.001")  # noqa: E731
    e = html.escape
    rows = "".join(
        f"<tr><td>{e(st['label'])}</td><td>{e(str(x['media_e']))} ± {e(str(x['ds_e']))} (n={e(str(x['n_e']))})"
        f"<br><small>{e(x['metodo_e'])}</small></td><td>{e(str(x['media_c']))} ± {e(str(x['ds_c']))} "
        f"(n={e(str(x['n_c']))})<br><small>{e(x['metodo_c'])}</small></td><td>{f2(st['yi'])} [{f2(st['ci_low'])}; "
        f"{f2(st['ci_high'])}]</td><td>{st['weight']:.1f}%</td></tr>" for st, x in zip(res["studies"], d["input"]))
    loo = "".join(f"<tr><td>{e(x['label'])}</td><td>{f2(x['estimate'])} [{f2(x['ci_low'])}; {f2(x['ci_high'])}]</td>"
                  f"<td>{x['i2']:.0f}%</td></tr>" for x in res.get("leave_one_out") or [])
    eg = res.get("egger")
    eg_txt = (f"Egger: t = {eg['t']:.2f} (df = {eg['df']}), p = {f3(eg['p'])}; limit estimate {f2(eg['limit_estimate'])} "
              f"[{f2(eg['limit_ci_low'])}; {f2(eg['limit_ci_high'])}]" if eg else res.get("egger_note", ""))
    stat = f"{res['stat_name']}{'(' + str(res['stat_df']) + ')' if res['stat_df'] else ''} = {res['stat']:.2f}"
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>{e(p['nome'])}</title><style>
:root{{color-scheme:light dark}}body{{font-family:system-ui,Segoe UI,Arial,sans-serif;max-width:980px;margin:24px auto;
padding:0 16px;line-height:1.45}}table{{border-collapse:collapse;width:100%;margin:12px 0}}td,th{{border-bottom:1px solid
#8884;padding:6px 8px;text-align:left;vertical-align:top}}small{{opacity:.75}}.src{{opacity:.7;font-size:.9em}}</style>
</head><body><h1>{e(p['nome'])}</h1>
<p>Outcome: <b>{e(p['esito'])}</b>{' · time: ' + e(p['tempo']) if p['tempo'] else ''} · {e(p['misura'])} ·
{e(p['modello'])} · CI {e(p['ic'].upper())} · k = {res['k']}</p>
<p><b>Pooled: {f2(res['estimate'])} [95% CI {f2(res['ci_low'])}; {f2(res['ci_high'])}]</b>, {e(stat)}, p = {f3(res['p'])};
I² = {res['i2']:.1f}%, τ² = {res['tau2']:.4f}, Q = {res['q']:.2f} (df = {res['q_df']}, p = {f3(res['q_p'])})
{'; 95% PI ' + f2(res['pi_low']) + '; ' + f2(res['pi_high']) if res.get('pi_low') is not None else ''}.</p>
<p>{e(eg_txt)}</p>{d['forest']}<h2>Studies</h2><table><thead><tr><th>Study</th><th>Experimental</th><th>Control</th>
<th>{e(p['misura'])} [95% CI]</th><th>Weight</th></tr></thead><tbody>{rows}</tbody></table>
<h2>Funnel plot</h2>{d['funnel']}
{'<h2>Leave-one-out</h2><table><thead><tr><th>Omitted</th><th>Estimate [95% CI]</th><th>I²</th></tr></thead><tbody>' + loo + '</tbody></table>' if loo else ''}
<h2>Methods</h2><p>{e(d['metodi'])}</p>
<p class="src">ORPHEUS · EURYDIKE · analysis {e(d['id'])} registered {e(d['creata'])} · {e(d['versione'])} · inputs SHA-256
{e(d['sha256'])} · written {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC</p></body></html>"""
    folder = ma_dir(conn, home, review_id)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{analysis_id}.html"
    path.write_text(page, encoding="utf-8")
    return path


def board(conn: sqlite3.Connection, review_id: str) -> dict:
    """Everything the Meta-analisi section shows: the rows with their conversions and the registered analyses."""
    return {**outcomes(conn, review_id), "analisi": analyses(conn, review_id), "versione_calcolo": ma_core.VERSION}
