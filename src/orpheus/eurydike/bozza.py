"""EURYDIKE E10 — from the review to the article (T-051k): studi_inclusi.csv, PRISMA numbers computed, results of the
registered meta-analyses and a first draft of Methods and Results in Word.

`to_new_article` fills (or creates) the project `nuovi_articoli\\<name>` of type «revisione» used by `nuovo_articolo.cmd`
(writing-v1, new_article.py):
  - studio.csv: the PRISMA numbers are always rewritten from the database (computed, never typed: principle 3); the
    facts of the protocol (question, criteria, registration, search, selection, extraction, risk of bias, synthesis,
    use of AI) are written only in the cells the user left empty — a value written by the user is never replaced;
  - studi_inclusi.csv: one row per included study (label, DOI, design, n, participants, intervention, comparison,
    outcomes, overall risk of bias);
  - risultati.csv: one row per registered meta-analysis that is still current (estimate, 95% CI, p, I², τ², PI);
the previous files are kept as *_precedente.csv. Then Claude writes the sections as for any article (numbers traced).

`draft` writes `metodi_risultati_eurydike.docx` in the same folder: Methods and Results written by ORPHEUS from the
facts only (no model), with the declaration that Claude was the second reviewer and with the limit of the blinding; every
number of the draft is checked against the numbers of the three files (`new_article.traced_values`): a number without
an origin stops the draft (gate E10: 0 numbers without origin).
R10: everything stays on the PC; nothing is written in the database.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from .. import article_docx as ad
from .. import new_article as na
from . import meta
from . import overview as ov
from .core.tools import get_tool

DRAFT_NAME = "metodi_risultati_eurydike.docx"
OWNED = ("prisma_identificati", "prisma_doppioni", "prisma_esaminati", "prisma_esclusi_esame", "prisma_non_reperiti",
         "prisma_testo_completo", "prisma_esclusi_testo", "prisma_inclusi", "prisma_meta_analisi")


class DraftError(ValueError):
    """A problem for the user (message in Italian)."""


def _f(x: float | None, d: int = 2) -> str:
    if x is None:
        return ""
    s = f"{x:.{d}f}"
    return "0." + "0" * d if s.startswith("-0.") and float(s) == 0 else s


def _p(p: float | None) -> str:
    return "" if p is None else ("<0.001" if p < 0.001 else f"{p:.3f}")


def _first_int(text: str | None) -> str:
    m = re.search(r"\b(\d{1,6})\b", text or "")
    return m.group(1) if m else ""


def _card(conn: sqlite3.Connection, aid: str | None) -> dict[str, str]:
    if not aid:
        return {}
    out: dict[str, list[str]] = {}
    for r in conn.execute("""SELECT field_key, value FROM card_item WHERE article_id = ? AND superseded_by IS NULL
                             AND state <> 'nr' AND value IS NOT NULL ORDER BY field_key, item_ord""", (aid,)):
        out.setdefault(r["field_key"], []).append(r["value"])
    return {k: "; ".join(v) for k, v in out.items()}


def _overall_rob(conn: sqlite3.Connection, sid: str, appr: dict) -> str:
    rob = appr.get("rob") or {}
    if not rob.get("strumento"):
        return ""
    r = conn.execute("""SELECT a.judgement FROM appraisal a JOIN review_reviewer v ON v.id = a.reviewer_id
                        WHERE a.study_id = ? AND a.tool = ? AND a.item_id = '_overall' AND a.superseded_by IS NULL
                        AND v.kind IN ('consensus','human') ORDER BY v.kind = 'consensus' DESC, a.created_at DESC""",
                     (sid, rob["strumento"])).fetchone()
    if r:
        return r[0]
    return f"{rob['suggerito']} (suggested)" if rob.get("suggerito") else ""


def facts(conn: sqlite3.Connection, review_id: str) -> dict:
    """The facts of the review from the database only: what the draft and the files may say."""
    o = ov.review_overview(conn, review_id)
    if o is None:
        raise DraftError("revisione non trovata")
    rv, p = o["revisione"], o["prisma"]
    crit = [c for c in o["protocollo"]["criteri"] if not c["sostituito_da"]]
    revs = o["protocollo"]["revisori"]
    imports = o["protocollo"]["importazioni"]
    included = [s for s in o["fulltext"]["studi"] if s["decisione"] == "include"]
    apprs = {x["id"]: x for x in o["bias"]["studi"]}
    current = [a for a in meta.analyses(conn, review_id) if a["attuale"]]
    latest: dict[tuple, dict] = {}
    for a in current:                                       # the last registered analysis of each outcome and measure
        latest[(a["esito"], a["tempo"], a["misura"])] = a
    tools = sorted({(k, x[k]["strumento"]) for x in o["bias"]["studi"] for k in ("rob", "qualita") if x.get(k)})
    in_ma = set()
    for a in latest.values():
        in_ma.update(r[0] for r in conn.execute("SELECT study_id FROM ma_input WHERE analysis_id = ?", (a["id"],)))
    return {"studi_meta_analisi": len(in_ma), "revisione": rv, "prisma": p, "criteri": crit, "revisori": revs, "importazioni": imports,
            "inclusi": included, "valutazioni": apprs, "analisi": list(latest.values()), "kappa": o["screening"]["kappa"],
            "strumenti": tools}


def _tool_name(tool: str) -> str:
    try:
        return get_tool(tool).name
    except KeyError:
        return tool


# ------------------------------------------------------------------------------------------------ the three files

def _rows_included(conn: sqlite3.Connection, review_id: str, f: dict) -> list[list[str]]:
    out = []
    oc = {s["id"]: s for s in meta.outcomes(conn, review_id)["studi"]}
    for s in f["inclusi"]:
        c = _card(conn, s["articolo"])
        esiti = sorted({x["esito"] for x in oc.get(s["id"], {}).get("righe", [])})
        appr = {"rob": (f["valutazioni"].get(s["id"]) or {}).get("rob")}
        n = c.get("partecipanti_numero", "")
        out.append([s["studio"], s["doi"] or "", c.get("disegno_studio", ""),
                    n if na._num(n) is not None else _first_int(n),
                    "; ".join(x for x in (c.get("partecipanti_livello", ""), c.get("partecipanti_sport", "")) if x),
                    c.get("intervento_esposizione", ""), c.get("confronto", ""), "; ".join(esiti),
                    _overall_rob(conn, s["id"], appr), f"EURYDIKE {s['id']}"])
    return out


def _rows_results(f: dict) -> list[list[str]]:
    out = []
    for i, a in enumerate(f["analisi"], 1):
        r = a["risultato"]
        name = a["nome"] + (f" ({a['tempo']})" if a["tempo"] else "")
        model = {"FE": "fixed effect", "RE-DL": "random effects DL", "RE-REML": "random effects REML"}[a["modello"]]
        note = (f"k = {a['k']} studies; {model}; CI {a['ic'].upper()}; I2 = {_f(r['i2'], 0)}%; tau2 = {_f(r['tau2'], 3)}"
                + (f"; 95% PI {_f(r['pi_low'])} to {_f(r['pi_high'])}" if r.get("pi_low") is not None else ""))
        out.append(["Table 2", name, "pooled", str(a["k"]), "", "", "", "", "", "",
                    _f(r["estimate"]) if a["misura"] == "MD" else "", _f(r["ci_low"]), _f(r["ci_high"]), _p(r["p"]),
                    _f(r["estimate"]) if a["misura"] == "SMD" else "", a["misura"], "", note])
    return out


def _study_values(f: dict) -> dict[str, str]:
    rv, p = f["revisione"], f["prisma"]
    inc = [c["testo"] for c in f["criteri"] if c["tipo"] == "inclusion"]
    exc = [c["testo"] for c in f["criteri"] if c["tipo"] == "exclusion"]
    humans = [r for r in f["revisori"] if r["tipo"] == ov.REVIEWER_LABELS["human"]]
    models = [r for r in f["revisori"] if r["tipo"] in (ov.REVIEWER_LABELS["claude_session"],
                                                        ov.REVIEWER_LABELS["chat_manuale"])]
    dbs = sorted({i["banca_dati"] for i in f["importazioni"]})
    dates = sorted(i["cercato_il"] for i in f["importazioni"] if i["cercato_il"])
    sel = (f"Titles and abstracts were screened independently by {len(humans)} human reviewer"
           f"{'s' if len(humans) != 1 else ''}"
           + (f" and by Claude ({_model(models[0]['modello'])}), used as a declared second reviewer blinded to the "
              "human decisions; disagreements were resolved by consensus" if models else "")
           + ". Full texts were assessed by the human reviewers, with Claude's proposals shown only after their decision.")
    k = f["kappa"]
    if k and k.get("kappa") is not None:
        sel += (f" Agreement at the title and abstract stage ({k['tra'][0]} vs {k['tra'][1]}) was Cohen's kappa = "
                f"{_f(k['kappa'])} on {k['n']} records.")
    extr = ("Data were extracted with a form fixed before extraction; Claude extracted first, quoting the sentence and the "
            "page of the PDF for every value, and every value was checked and confirmed or corrected by a human reviewer. "
            "Means and SDs not reported were estimated from medians and interquartile ranges or ranges (Luo 2018; "
            "Wan 2014) or from standard errors and confidence intervals (Cochrane Handbook 6.5.2).")
    rob = "; ".join(_tool_name(t) for _k, t in f["strumenti"])
    synth = " ".join(dict.fromkeys(meta.methods_text({"misura": a["misura"], "modello": a["modello"], "ic": a["ic"]},
                                                     {"egger": a["k"] >= 10}) for a in f["analisi"]))
    pico = rv.get("pico") or {}
    return {
        "tipo": "revisione", "titolo_provvisorio": rv.get("titolo") or "", "domanda_di_ricerca": rv.get("domanda") or "",
        "criteri_inclusione": "; ".join(inc), "criteri_esclusione": "; ".join(exc),
        "registrazione_revisione": rv.get("registrazione") or "not registered",
        "banche_dati": "; ".join(dbs), "strategia_ricerca": rv.get("strategia") or "",
        "date_ricerca": ("; ".join(dict.fromkeys(dates))) if dates else "", "selezione_studi": sel,
        "estrazione_dati": extr, "rischio_bias": rob, "sintesi": synth or "narrative synthesis",
        "uso_ia": ("Claude (Anthropic) was used within the ORPHEUS/EURYDIKE system as a declared second reviewer for "
                   "screening, as first extractor and assessor of risk of bias (every value and judgement checked by a "
                   "human reviewer), and to draft the text from the data of the review; all statistics were computed by "
                   "deterministic code, not by the language model."),
        "variabili_principali": "; ".join(dict.fromkeys(a["esito"] for a in f["analisi"])),
        "partecipanti_numero": "", "disegno": "systematic review" + (" and meta-analysis" if f["analisi"] else ""),
        "contesto": pico.get("setting", "") if isinstance(pico, dict) else "",
        "prisma_identificati": str(p["identified"]), "prisma_doppioni": str(p["duplicates"]),
        "prisma_esaminati": str(p["screened"]), "prisma_esclusi_esame": str(p["excluded"]),
        "prisma_non_reperiti": str(p["not_retrieved"]), "prisma_testo_completo": str(p["assessed"]),
        "prisma_esclusi_testo": str(p["ft_excluded"]), "prisma_inclusi": str(p["included"]),
        "prisma_meta_analisi": str(f["studi_meta_analisi"]) if f["analisi"] else "",
    }


def _model(ref: str | None) -> str:
    return f"Anthropic, model {ref}" if ref and re.match(r"^claude-[a-z0-9.-]+$", ref.strip()) else "Anthropic"


def _keep_old(path: Path) -> None:
    if path.exists():
        path.replace(path.with_name(path.stem + "_precedente" + path.suffix))


def to_new_article(conn: sqlite3.Connection, home: Path, review_id: str, name: str, journal: str | None = None) -> dict:
    """Create or update the project of the article of the review (nuovo_articolo.cmd)."""
    if not na.NAME_RE.match(name or ""):
        raise DraftError("nome del progetto: lettere, numeri, - e _ (fino a 60)")
    f = facts(conn, review_id)
    d = na.project_dir(home, name)
    created = False
    if not d.exists():
        na.create(home, name, journal, kind="revisione")
        created = True
    elif na.kind_of(d) != "revisione":
        raise DraftError("il progetto esiste già e non è una revisione: scegli un altro nome")
    values = _study_values(f)
    rows = na._read_csv(d / "studio.csv") if (d / "studio.csv").exists() else []
    have = {r.get("campo", ""): r for r in rows}
    out_rows, written, kept = [], [], []
    for c, _req, desc in na.FIELDS_BY_KIND["revisione"]:
        old = have.get(c, {}).get("valore", "")
        new = values.get(c, "")
        if c in OWNED or (not old and new):
            val = new
            if new and new != old:
                written.append(c)
        else:
            val = old
            if old and new and old != new:
                kept.append(c)
        need = "OBBLIGATORIO. " if c in na.REQUIRED_BY_KIND["revisione"] else ""
        out_rows.append([c, val, (have.get(c, {}).get("descrizione") or need + desc)])
    _keep_old(d / "studio.csv")
    na._write_csv(d / "studio.csv", ["campo", "valore", "descrizione"], out_rows)
    _keep_old(d / na.INCLUDED_FILE)
    na._write_csv(d / na.INCLUDED_FILE, na.INCLUDED_COLUMNS, _rows_included(conn, review_id, f))
    results = _rows_results(f)
    _keep_old(d / "risultati.csv")
    na._write_csv(d / "risultati.csv", na.RESULT_COLUMNS, results)
    study, problems = na.read_study(d)
    return {"cartella": str(d), "creato": created, "campi_scritti": written, "campi_tuoi_lasciati": kept,
            "studi": len(f["inclusi"]), "risultati": len(results), "problemi": problems}


# ------------------------------------------------------------------------------------------------ the draft

def _methods(f: dict, study: dict) -> list[str]:
    paras = [f"This systematic review addressed the following question: {study.get('domanda_di_ricerca')}" +
             ("" if (study.get("domanda_di_ricerca") or "").endswith((".", "?")) else ".")]
    reg = study.get("registrazione_revisione") or "not registered"
    paras.append(f"The protocol was registered ({reg})." if reg != "not registered"
                 else "The protocol was not registered.")
    if study.get("banche_dati"):
        paras.append(f"Records were retrieved from {study['banche_dati']}"
                     + (f" (searches run on {study['date_ricerca']})" if study.get("date_ricerca") else "")
                     + "; duplicates were removed before screening and checked by hand.")
    if study.get("criteri_inclusione"):
        paras.append(f"Inclusion criteria: {study['criteri_inclusione']}."
                     + (f" Exclusion criteria: {study['criteri_esclusione']}." if study.get("criteri_esclusione") else ""))
    paras.append(study.get("selezione_studi") or "")
    paras.append(study.get("estrazione_dati") or "")
    if study.get("rischio_bias"):
        paras.append(f"Risk of bias and methodological quality were assessed with {study['rischio_bias']}; Claude "
                     "judged first, quoting the sentence and the page, and a human reviewer confirmed or changed every "
                     "judgement and wrote the overall judgement.")
    if f["analisi"]:
        paras.append(study.get("sintesi") or "")
        paras.append("All statistics were computed by the deterministic code of ORPHEUS (EURYDIKE), checked against "
                     "the R package metafor; the language model did not compute any number.")
    paras.append("Limitations of the process: Claude was blinded to the human decisions, but the human reviewers saw "
                 "Claude's proposals after their own decision; Claude is a language model and not an independent "
                 "human second reviewer.")
    return [x for x in paras if x]


def _results(f: dict) -> list[str]:
    p = f["prisma"]
    paras = [f"The searches identified {p['identified']} records; after removing {p['duplicates']} duplicates, "
             f"{p['screened']} records were screened and {p['excluded']} were excluded on title and abstract. "
             + (f"{p['not_retrieved']} reports could not be retrieved. " if p["not_retrieved"] else "")
             + f"{p['assessed']} full texts were assessed, {p['ft_excluded']} were excluded and {p['included']} "
             f"studies were included."]
    for a in f["analisi"]:
        r = a["risultato"]
        txt = (f"{a['nome']}{' (' + a['tempo'] + ')' if a['tempo'] else ''}: {a['misura']} = {_f(r['estimate'])} "
               f"(95% CI {_f(r['ci_low'])} to {_f(r['ci_high'])}; p {('= ' + _p(r['p'])) if r['p'] >= 0.001 else '< 0.001'}; "
               f"k = {a['k']}), with I2 = {_f(r['i2'], 0)}% and tau2 = {_f(r['tau2'], 3)}")
        if r.get("pi_low") is not None:
            txt += f"; the 95% prediction interval was {_f(r['pi_low'])} to {_f(r['pi_high'])}"
        paras.append(txt + ".")
    return paras


def draft(conn: sqlite3.Connection, home: Path, review_id: str, name: str) -> dict:
    """Write metodi_risultati_eurydike.docx in the project of the article (after `to_new_article`)."""
    d = na.project_dir(home, name)
    if not (d / "studio.csv").exists():
        raise DraftError("prima «Prepara l'articolo» (studio.csv, studi_inclusi.csv, risultati.csv)")
    f = facts(conn, review_id)
    study, _probs = na.read_study(d)
    results, _ = na.read_results(d)
    included, _ = na.read_included(d)
    methods, res = _methods(f, study), _results(f)
    traced = na.traced_values(study, results + included)
    untraced = sorted({n for para in methods + res for n in na.numbers_in(para) if na.value(n) not in traced},
                      key=lambda s: na.value(s))
    if untraced:
        raise DraftError("numeri senza origine nella bozza: " + ", ".join(untraced[:10]) +
                         " (rifai «Prepara l'articolo» e registra di nuovo le analisi)")
    blocks = [ad.Block("title", [(study.get("titolo_provvisorio") or f["revisione"].get("titolo") or "Draft", {})]),
              ad.Block("p", [("Draft written by ORPHEUS (EURYDIKE) from the data of the review: to be revised by the "
                              "authors.", {"i": True})]),
              ad.Block("h1", [("Methods", {})]), *[ad.Block("p", [(t, {})]) for t in methods],
              ad.Block("h1", [("Results", {})]), *[ad.Block("p", [(t, {})]) for t in res]]
    if results:
        rows = [["Outcome", "k", "Effect [95% CI]", "p", "Notes"]] + [
            [r["variabile"], r["n"], f"{r['differenza'] or r['effetto']} [{r['ic95_basso']}; {r['ic95_alto']}]", r["p"],
             r["note"]] for r in results]
        blocks.append(ad.Block("table", rows=rows))
    path = d / DRAFT_NAME
    ad.write_docx(path, blocks, dict(ad.DEFAULT_LAYOUT), title=blocks[0].runs[0][0])
    return {"file": str(path), "paragrafi": len(methods) + len(res), "numeri_senza_origine": 0,
            "analisi": len(f["analisi"])}


def board(conn: sqlite3.Connection, home: Path, review_id: str) -> dict:
    """What the Esporta section shows for the article: the projects of type revisione and the facts ready."""
    f = facts(conn, review_id)
    projects = []
    for n in na.list_projects(home):
        try:
            if na.kind_of(na.project_dir(home, n)) == "revisione":
                dd = na.project_dir(home, n)
                projects.append({"nome": n, "bozza": (dd / DRAFT_NAME).exists()})
        except Exception:                                       # noqa: BLE001 — a broken project is just not listed
            continue
    return {"progetti": projects, "studi_inclusi": len(f["inclusi"]), "analisi_attuali": len(f["analisi"]),
            "prisma": {k: f["prisma"][k] for k in ("identified", "duplicates", "screened", "excluded", "not_retrieved",
                                                     "assessed", "ft_excluded", "included")},
            "suggerito": re.sub(r"[^A-Za-z0-9_-]+", "_", (f["revisione"].get("titolo") or "revisione"))[:40].strip("_")
            or "revisione"}
