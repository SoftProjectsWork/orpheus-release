"""T-057 — profile of a journal from its published articles: what they report about statistics, transparency and
structure (rules stats-terms-v1, tables text_method_item and text_structure). Read-only; every number is counted here,
in code, never by a model.

Decisions of the user (2026-10-27): distributions per journal and per year, always with the number of articles; the
fixed warning that these are published articles (they say what the journals publish, not what they reject); profiles
can be shared with colleagues (they are counts from the articles; no manuscript, letter or reviewer's comment is used).

An article counts in a journal's profile when its complete-article PDF has been analysed (a row in text_structure for
the current rules). A term counts once per article (several sentences = one article). The power calculation declared
as not done ("No a priori power analysis was performed") is counted apart and never as done.
"""
from __future__ import annotations

import re
import sqlite3
import statistics

from . import stats_terms

WARNING = ("Sono articoli pubblicati: dicono cosa le riviste pubblicano, non cosa rifiutano. "
           "Con pochi articoli le percentuali cambiano molto: guarda sempre N.")
WARNING_EN = ("These are published articles: they say what the journals publish, not what they reject. "
              "With few articles the percentages change a lot: always look at N.")
STRUCTURE_FIELDS = (("words_abstract", "Parole dell'abstract"), ("words_introduction", "Parole dell'Introduzione"),
                    ("words_methods", "Parole dei Metodi"), ("words_results", "Parole dei Risultati"),
                    ("words_discussion", "Parole della Discussione"), ("words_body", "Parole del corpo"),
                    ("figures", "Figure"), ("tables", "Tabelle"), ("references_n", "Riferimenti"))
MIN_FOR_QUARTILES = 5


def distribution(values: list[int]) -> dict:
    """n, median and quartiles (method 'inclusive'), min, max; quartiles only from MIN_FOR_QUARTILES values."""
    v = sorted(x for x in values if x is not None)
    out: dict = {"n": len(v)}
    if not v:
        return out
    out |= {"mediana": statistics.median(v), "min": v[0], "max": v[-1]}
    if len(v) >= MIN_FOR_QUARTILES:
        q = statistics.quantiles(v, n=4, method="inclusive")
        out |= {"q1": q[0], "q3": q[2]}
    return out


def _pct(n: int, total: int) -> float | None:
    return round(100 * n / total, 1) if total else None


def _analysed(conn: sqlite3.Connection, rules: str) -> list[sqlite3.Row]:
    return list(conn.execute(
        """SELECT s.*, a.journal_id, a.publication_year AS year FROM text_structure s
           JOIN article a ON a.id = s.article_id WHERE s.rules = ? AND a.journal_id IS NOT NULL""", (rules,)))


def _items(conn: sqlite3.Connection, rules: str) -> dict[str, list[sqlite3.Row]]:
    by_article: dict[str, list[sqlite3.Row]] = {}
    for r in conn.execute("SELECT article_id, grp, term, detail FROM text_method_item WHERE rules = ?", (rules,)):
        by_article.setdefault(r["article_id"], []).append(r)
    return by_article


def _terms_of(items: list[sqlite3.Row]) -> tuple[set[str], set[str], dict[str, set[str]]]:
    """(terms done, terms declared not done, {term: details})."""
    done, not_done, details = set(), set(), {}
    for r in items:
        if r["term"] == "power_analysis" and r["detail"] == "not_done":
            not_done.add(r["term"])
            continue
        done.add(r["term"])
        for d in (r["detail"] or "").split(","):
            if d:
                details.setdefault(r["term"], set()).add(d)
    return done, not_done - done, details


def profile_of(rows: list[sqlite3.Row], items: dict[str, list[sqlite3.Row]]) -> dict:
    """Profile of a set of analysed articles (one journal, or one journal and year)."""
    n = len(rows)
    per_term: dict[str, int] = {}
    per_detail: dict[str, dict[str, int]] = {}
    not_done: dict[str, int] = {}
    with_any_stats = 0
    for r in rows:
        done, nd, details = _terms_of(items.get(r["article_id"], []))
        if any(stats_terms.TERMS_BY_KEY[t].grp in ("confronto", "modello", "approccio", "affidabilita") for t in done):
            with_any_stats += 1
        for t in done:
            per_term[t] = per_term.get(t, 0) + 1
        for t in nd:
            not_done[t] = not_done.get(t, 0) + 1
        for t, ds in details.items():
            for d in ds:
                per_detail.setdefault(t, {})[d] = per_detail.setdefault(t, {}).get(d, 0) + 1
    groups = []
    for g, label in stats_terms.GROUPS.items():
        terms = [{"voce": t.key, "nome": t.label, "articoli": per_term.get(t.key, 0),
                  "percentuale": _pct(per_term.get(t.key, 0), n),
                  "dettagli": dict(sorted(per_detail.get(t.key, {}).items(), key=lambda kv: -kv[1])),
                  **({"dichiarato_non_fatto": not_done[t.key]} if t.key in not_done else {})}
                 for t in stats_terms.TERMS if t.grp == g]
        groups.append({"gruppo": g, "nome": label, "nome_en": stats_terms.GROUPS_EN[g],
                       "voci": sorted(terms, key=lambda x: -x["articoli"])})
    structure = {key: {"nome": label, **distribution([r[key] for r in rows])} for key, label in STRUCTURE_FIELDS}
    return {"articoli": n, "articoli_con_metodi_statistici": with_any_stats, "gruppi": groups, "struttura": structure}


# ---------------------------------------------------------------- level 2: tests by objective (T-057c)
OBJECTIVE_NOTE = ("Obiettivo e disegno dalle schede (contratto obiettivi-v1): «proposti» sono valori di Claude non ancora "
                  "controllati da te; i test e i modelli sono le voci stats-terms-v1 dei Metodi e dei Risultati, contati "
                  "una volta per articolo; «analisi principale» è quella scritta nella scheda per l'esito primario. "
                  "Guarda sempre N.")


def objective_items(conn: sqlite3.Connection) -> dict[str, dict]:
    """{article_id: {field: value, "confermato": bool}} for the articles whose objective type is in the card (current
    values, a value marked not reported left out). `confermato`: every choice field written was confirmed or corrected
    by the user."""
    from . import objectives
    out: dict[str, dict] = {}
    marks = ",".join("?" * len(objectives.CHOICE_FIELDS))
    for r in conn.execute(f"""SELECT article_id, field_key, value, state FROM card_item WHERE superseded_by IS NULL
                              AND item_ord = 1 AND state <> 'nr' AND field_key IN ({marks}, 'analisi_principale')""",
                          objectives.CHOICE_FIELDS):
        a = out.setdefault(r["article_id"], {"confermato": True})
        if r["value"] in (None, "not reported"):
            continue
        a[r["field_key"]] = r["value"]
        if r["state"] == "ai" and r["field_key"] != "analisi_principale":
            a["confermato"] = False
    return {k: v for k, v in out.items() if v.get("tipo_obiettivo")}


def design_label(o: dict) -> str:
    from . import objectives
    parts = [objectives.OBJECTIVE_IT.get(o.get("tipo_obiettivo"), "—")]
    if o.get("disegno_tempi"):
        parts.append(f"tempi: {objectives.COUNT_IT[o['disegno_tempi']]}")
    if o.get("disegno_gruppi"):
        parts.append(f"gruppi: {objectives.COUNT_IT[o['disegno_gruppi']]}")
    if o.get("stessi_soggetti"):
        parts.append(f"stessi soggetti: {objectives.YES_NO_IT[o['stessi_soggetti']]}")
    if o.get("crossover") == "si":
        parts.append("crossover")
    return " · ".join(parts)


def tests_by_objective(article_ids, items: dict[str, list[sqlite3.Row]], objectives_of: dict[str, dict]) -> dict:
    """The statistics of the articles (stats-terms-v1, groups of methods) read by objective and design: one row per
    combination (objective type, time points, groups, same subjects, crossover), with N, how many of them confirmed by
    the user, and every test or model with its number of articles. Counted here, never by a model."""
    from . import objectives
    combos: dict[tuple, dict] = {}
    for aid in article_ids:
        o = objectives_of.get(aid)
        if not o:
            continue
        key = (o.get("tipo_obiettivo"),) + tuple(o.get(k) for k in objectives.DESIGN_FIELDS)
        c = combos.setdefault(key, {"obiettivo": o.get("tipo_obiettivo"), **{k: o.get(k) for k in objectives.DESIGN_FIELDS},
                                    "disegno": design_label(o), "articoli": 0, "confermati": 0, "_voci": {},
                                    "_analisi": {}})
        c["articoli"] += 1
        if o.get("analisi_principale"):
            c["_analisi"][o["analisi_principale"]] = c["_analisi"].get(o["analisi_principale"], 0) + 1
        c["confermati"] += bool(o.get("confermato"))
        done, _nd, _det = _terms_of(items.get(aid, []))
        for t in done:
            if stats_terms.TERMS_BY_KEY[t].grp in objectives.METHOD_GROUPS:
                c["_voci"][t] = c["_voci"].get(t, 0) + 1
    rows = []
    for c in sorted(combos.values(), key=lambda c: (-c["articoli"], c["disegno"])):
        voci = c.pop("_voci")
        analyses = c.pop("_analisi")
        c["analisi_principali"] = [{"analisi": a, "articoli": n}
                                   for a, n in sorted(analyses.items(), key=lambda kv: (-kv[1], kv[0].lower()))]
        c["test"] = [{"voce": t, "nome": stats_terms.TERMS_BY_KEY[t].label, "gruppo": stats_terms.TERMS_BY_KEY[t].grp,
                      "articoli": n, "percentuale": _pct(n, c["articoli"])}
                     for t, n in sorted(voci.items(), key=lambda kv: (-kv[1], kv[0]))]
        rows.append(c)
    return {"articoli_con_obiettivo": sum(r["articoli"] for r in rows), "combinazioni": rows, "nota": OBJECTIVE_NOTE}


# ---------------------------------------------------------------- point 5: useful sentences as writing models (2026-10-28)
# Short sentences already checked in the cards (exact quote on the page of the article): how the published articles of a
# journal write the aim, the hypothesis, the primary outcome and the main analysis. Only short sentences with the
# reference (title, year, page): never long copies of the texts. Decided by the user (2026-10-28): from the cards, in the
# profile, also to colleagues (the card items are already in the sharing package).
MODEL_FIELDS = (("obiettivo", "Obiettivo o ipotesi"), ("esito_primario", "Esito primario"),
                ("analisi_principale", "Analisi principale"), ("effetto_principale", "Effetto principale"))
MODEL_MAX_WORDS = 30
MODEL_PER_FIELD = 12
_OPENING = re.compile(r"[A-Za-z][\w'’-]*")


def _opening(quote: str, n: int = 5) -> str:
    words = _OPENING.findall(quote)
    return " ".join(w.lower() for w in words[:n])


def writing_models(conn: sqlite3.Connection, journal_id: str | None = None, *, limit: int = MODEL_PER_FIELD) -> dict:
    """For each field of MODEL_FIELDS: the openings that recur (first five words, with N) and up to `limit` short
    sentences (≤ MODEL_MAX_WORDS words) with the reference, the objective type of the article and whether the user
    confirmed the value. Read-only; counted here, never by a model."""
    from . import objectives
    objs = objective_items(conn)
    where = "AND a.journal_id = ?" if journal_id else ""
    args = (journal_id,) if journal_id else ()
    rows = conn.execute(f"""SELECT i.article_id, i.field_key, i.quote, i.page_no, i.state, a.title,
                                   a.publication_year AS year
                            FROM card_item i JOIN article a ON a.id = i.article_id
                            WHERE i.superseded_by IS NULL AND i.state <> 'nr' AND i.quote IS NOT NULL
                              AND i.field_key IN ({",".join("?" * len(MODEL_FIELDS))}) {where}
                            ORDER BY a.publication_year DESC, a.id, i.field_key, i.item_ord""",
                        (*[f for f, _ in MODEL_FIELDS], *args)).fetchall()
    out = []
    for field, label in MODEL_FIELDS:
        mine = [r for r in rows if r["field_key"] == field and len(r["quote"].split()) <= MODEL_MAX_WORDS]
        seen, sentences, openings = set(), [], {}
        for r in mine:
            q = " ".join(r["quote"].split())
            if q.lower() in seen:
                continue
            seen.add(q.lower())
            op = _opening(q)
            if len(op.split()) == 5:
                openings[op] = openings.get(op, 0) + 1
            if len(sentences) < limit:
                tipo = (objs.get(r["article_id"]) or {}).get("tipo_obiettivo")
                sentences.append({"frase": q, "articolo": r["article_id"], "titolo": r["title"], "anno": r["year"],
                                  "pagina": r["page_no"], "confermata": r["state"] != "ai",
                                  "tipo_obiettivo": objectives.OBJECTIVE_IT.get(tipo) if tipo else None})
        out.append({"campo": field, "nome": label, "frasi_brevi": len(seen), "frasi": sentences,
                    "aperture": [{"inizio": k, "articoli": n} for k, n in
                                 sorted(openings.items(), key=lambda kv: (-kv[1], kv[0])) if n >= 2][:8]})
    return {"campi": out, "massimo_parole": MODEL_MAX_WORDS,
            "nota": f"Frasi brevi (al massimo {MODEL_MAX_WORDS} parole) già controllate nelle schede, con il riferimento: "
                    "un modello di struttura, non testo da copiare. «Aperture» = come cominciano le frasi più spesso "
                    "(prime cinque parole, almeno 2 articoli)."}


def build(conn: sqlite3.Connection, *, journal_id: str | None = None, rules: str = stats_terms.RULES_VERSION) -> dict:
    """Profiles of every journal (or one), with a per-year breakdown of the articles, from the database."""
    rows = _analysed(conn, rules)
    items = _items(conn, rules)
    titles = {r["id"]: r["title"] for r in conn.execute("SELECT id, title FROM journal")}
    objs = objective_items(conn)
    by_journal: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        if journal_id is None or r["journal_id"] == journal_id:
            by_journal.setdefault(r["journal_id"], []).append(r)
    out = []
    for jid, rs in sorted(by_journal.items(), key=lambda kv: -len(kv[1])):
        years: dict[int | None, list[sqlite3.Row]] = {}
        for r in rs:
            years.setdefault(r["year"], []).append(r)
        per_year = []
        for y in sorted(years, key=lambda y: (y is None, y or 0)):
            p = profile_of(years[y], items)
            per_year.append({"anno": y, "articoli": p["articoli"],
                             "voci": {v["voce"]: v["articoli"] for g in p["gruppi"] for v in g["voci"] if v["articoli"]}})
        out.append({"journal_id": jid, "titolo": titles.get(jid), **profile_of(rs, items), "per_anno": per_year,
                    "test_per_obiettivo": tests_by_objective([r["article_id"] for r in rs], items, objs),
                    "frasi_modello": writing_models(conn, jid)})
    every = [r["article_id"] for r in rows if journal_id is None or r["journal_id"] == journal_id]
    return {"regole": rules, "avviso": WARNING, "avviso_en": WARNING_EN, "riviste": out,
            "articoli_analizzati": len(rows), "test_per_obiettivo": tests_by_objective(every, items, objs),
            "frasi_modello": writing_models(conn, journal_id)}


def examples(conn: sqlite3.Connection, journal_id: str, term: str, *, limit: int = 20,
             rules: str = stats_terms.RULES_VERSION) -> list[dict]:
    """The sentences behind a count (article, page, sentence), newest articles first."""
    return [dict(r) for r in conn.execute(
        """SELECT i.article_id, a.title, a.doi, a.publication_year AS anno, i.page_no AS pagina, i.section AS sezione,
                  i.detail AS dettaglio, i.quote AS frase
           FROM text_method_item i JOIN article a ON a.id = i.article_id
           WHERE a.journal_id = ? AND i.term = ? AND i.rules = ?
           ORDER BY a.publication_year DESC, a.id, i.page_no LIMIT ?""", (journal_id, term, rules, limit))]


# ---------------------------------------------------------------- your manuscript vs the journal's articles

GROUP = "Confronto con gli articoli della rivista"
MIN_ARTICLES = 10
REPORTING_TERMS = ("effect_size", "ci", "power_analysis", "normality", "multiple_comparisons", "registration",
                   "data_availability")
COMMON = 50.0                                  # a reporting item "usual" in a journal: written in ≥ 50% of its articles
NOTE = ("Confronto con gli articoli pubblicati dalla rivista (regole {r}): dice cosa la rivista pubblica, non cosa "
        "rifiuta; mai un motivo di correzione da solo. Nei PDF le parole contano anche il testo delle tabelle stampate "
        "dentro le sezioni: il confronto delle lunghezze è indicativo.")


def manuscript_items(m) -> tuple[set[str], dict[str, int | None]]:
    """Terms of the dictionary in the Methods and Results of a manuscript (transparency: everywhere but Introduction and
    Discussion) and its structure, with the same rules as the articles. Nothing is written anywhere."""
    from .manuscript import words_of
    paras = {p.i: p for p in m.doc.paragraphs}
    by_section: dict[str, list[str]] = {}
    for sec in m.sections:
        major = {"results_discussion": "results", "limitations": "discussion", "conclusion": "discussion",
                 "practical": "discussion"}.get(sec.name, sec.name)
        by_section.setdefault(major, []).extend(paras[i].text for i in sec.paras if i in paras)
    found: set[str] = set()
    for section, texts in by_section.items():
        place = {"methods": "methods", "results": "results", "introduction": None, "discussion": None}.get(
            section, "back")
        if place is None:
            continue
        for sentence in (s for t in texts for s in stats_terms._SENTENCE.split(t)):
            for term in stats_terms.TERMS:
                if place not in term.scope:
                    continue
                for pat in term.patterns:
                    mm = pat.search(sentence)
                    if mm:
                        if not (term.key == "power_analysis"
                                and stats_terms._detail(term, sentence, mm) == "not_done"):
                            found.add(term.key)
                        break
    for sentence in stats_terms._SENTENCE.split(m.abstract_text or ""):
        for term in stats_terms.TERMS:
            if term.scope is stats_terms.SCOPE_OPEN and any(p.search(sentence) for p in term.patterns):
                found.add(term.key)
    words = {k: sum(words_of(t) for t in by_section.get(k, [])) or None
             for k in ("introduction", "methods", "results", "discussion")}
    words["body"] = sum(v or 0 for v in words.values()) or None
    structure = {"words_abstract": m.abstract_words or None, **{f"words_{k}": v for k, v in words.items()},
                 "figures": len(m.captions.get("figure", {})) or None, "tables": len(m.captions.get("table", {})) or None,
                 "references_n": len(m.references) or None}
    return found, structure


def compare(conn: sqlite3.Connection, m, jr) -> list:
    """Checks (group GROUP) of a manuscript against the analysed articles of one journal: lengths outside the journal's
    middle half and reporting items that most of its articles write → "guarda"; never "ko"."""
    from .compliance import Check
    prof = build(conn, journal_id=jr.journal_id)
    src = {"rivista": jr.sigla or jr.title, "scheda": None, "linee_guida": None, "regole": stats_terms.RULES_VERSION}
    note = NOTE.format(r=stats_terms.RULES_VERSION)
    if not prof["riviste"]:
        return [Check("profilo_rivista", GROUP, "Articoli della rivista analizzati", "na",
                      "nessun articolo completo della rivista analizzato (passo «metodi» di lavora)", None, src, [note])]
    p = prof["riviste"][0]
    n = p["articoli"]
    few = n < MIN_ARTICLES
    out = [Check("profilo_rivista", GROUP, "Articoli della rivista analizzati", "guarda" if few else "ok",
                 f"{n} articoli" + (f": meno di {MIN_ARTICLES}, confronto molto incerto" if few else ""), None, src,
                 [note])]
    found, mine = manuscript_items(m)
    for key, label in STRUCTURE_FIELDS:
        d = p["struttura"].get(key) or {}
        value = mine.get(key)
        if value is None or "q1" not in d:
            continue
        inside = d["q1"] <= value <= d["q3"]
        out.append(Check(f"profilo_{key}", GROUP, label, "ok" if inside and not few else "guarda",
                         f"nel manoscritto {_f(value)}; nella rivista mediana {_f(d['mediana'])} "
                         f"[{_f(d['q1'])}–{_f(d['q3'])}], N = {d['n']}", None, src))
    voci = {v["voce"]: v for g in p["gruppi"] for v in g["voci"]}
    for key in REPORTING_TERMS:
        v = voci[key]
        if not v["articoli"]:
            continue
        have = key in found
        usual = (v["percentuale"] or 0) >= COMMON
        out.append(Check(f"profilo_{key}", GROUP, stats_terms.TERMS_BY_KEY[key].label,
                         "ok" if have or not usual else "guarda",
                         ("presente nel manoscritto" if have else "non trovato nel manoscritto")
                         + f"; lo scrivono {v['articoli']} articoli su {n} della rivista ({_f(v['percentuale'])}%)",
                         None, src))
    methods = [k for k in sorted(found) if stats_terms.TERMS_BY_KEY[k].grp in
               ("confronto", "modello", "approccio", "affidabilita")]
    if methods:
        det = [f"{stats_terms.TERMS_BY_KEY[k].label}: {voci[k]['articoli']} articoli su {n} ({_f(voci[k]['percentuale'])}%)"
               for k in methods]
        rare = [k for k in methods if not voci[k]["articoli"]]
        out.append(Check("profilo_metodi", GROUP, "Metodi statistici del manoscritto nella rivista",
                         "guarda" if rare else "ok",
                         f"{len(methods)} metodi trovati" + (f"; mai negli articoli della rivista: "
                                                             f"{', '.join(stats_terms.TERMS_BY_KEY[k].label for k in rare)}"
                                                             if rare else ""), None, src, det))
    return out


def _f(x) -> str:
    if x is None:
        return "—"
    x = int(x) if float(x).is_integer() else round(float(x), 1)
    return f"{x:,}".replace(",", ".") if isinstance(x, int) else str(x).replace(".", ",")
