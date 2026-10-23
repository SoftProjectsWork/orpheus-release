"""T-015 — status report: one static HTML page in rapporti/ (read-only on the database).

Every number is read from the database at the moment the report is made; each section names the
tables it comes from and the date of the most recent row it counts. Nothing is written to the
database: `build` only runs SELECTs, `write_html` writes one file in the data folder.

Journal coverage is grouped by the journal folder a PDF was filed under (document.journal_hint,
inbox/riviste/<sigla>/...): a hint, not a verified fact. A PDF read before it was put in a journal folder
has no hint: then the sigla of riviste_prioritarie.csv whose ISSN Crossref gave for the confirmed DOI is
used (column "Da ISSN"; only when exactly one sigla matches). The journal name shown is the one Crossref
gave for the confirmed DOIs of the group (doi_check.details), when there is one.
The `journal` table is not used: it is still empty (writing it is a decision of the user).
"""
from __future__ import annotations

import html
import re
from collections import Counter
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from . import benchmark, card_store, claims, journal_cards, journal_metrics, journals, policy_changes, policy_versions
from .content import CLASSES, COMPLETE, classify_all

REPORT_PREFIX = "report"
NO_FOLDER = "(senza cartella di rivista)"

DOI_IT = {"confirmed": "confermato", "not_found": "non trovato", "mismatch": "non corrisponde"}
PUBMED_IT = {"confirmed": "confermato", "not_found": "non trovato", "mismatch": "discordante", "ambiguous": "ambiguo"}
EPMC_IT = {"found": "trovato", "not_found": "non trovato", "mismatch": "discordante", "ambiguous": "ambiguo"}
AVAIL_IT = {"open_access": "accesso aperto", "free_not_oa": "gratuito, non aperto", "metadata_only": "solo metadati"}
CITE_IT = {"verified": "verificato", "unverified": "non verificato", "not_found": "non trovato", "mismatch": "discordante"}
KIND_IT = {"article": "articoli", "author_guidelines": "linee guida per gli autori", "other": "altro"}


def _one(conn: sqlite3.Connection, sql: str, params=()):
    row = conn.execute(sql, params).fetchone()
    return row[0] if row else None


def _latest(table: str, key: str) -> str:
    """The most recent row per `key` of a check table (re-checks add rows; the last one counts)."""
    return f"""(SELECT * FROM (SELECT t.*, ROW_NUMBER() OVER (PARTITION BY t.{key} ORDER BY t.checked_at DESC, t.id DESC)
                AS rn FROM {table} t) WHERE rn = 1)"""


def _counts(conn: sqlite3.Connection, sql: str, names: dict | None = None) -> dict:
    out = {}
    for k, n in conn.execute(sql):
        out[(names or {}).get(k, k) if k is not None else "(vuoto)"] = n
    return out


def _documents(conn: sqlite3.Connection) -> dict:
    by_kind = _counts(conn, "SELECT kind, count(*) FROM document GROUP BY 1 ORDER BY 1", KIND_IT)
    classes = classify_all(conn)
    by_class = {c: 0 for c in CLASSES}
    for c in classes.values():
        if c.get("classe") in by_class:
            by_class[c["classe"]] += 1
    return {"fonte": "document, document_page", "data": _one(conn, "SELECT max(ingested_at) FROM document"),
            "pdf": _one(conn, "SELECT count(*) FROM document"), "per_tipo": by_kind, "articoli_per_classe": by_class,
            "testo": _counts(conn, "SELECT text_status, count(*) FROM document GROUP BY 1 ORDER BY 1"),
            "con_articolo": _one(conn, "SELECT count(*) FROM document WHERE article_id IS NOT NULL")}


def _coverage(conn: sqlite3.Connection, journal_list: list[dict] | None = None) -> dict:
    """One row per journal (folder, or sigla from the Crossref ISSN): documents, articles and what is known."""
    folder_of = {}
    for aid, hint in conn.execute("""SELECT article_id, min(journal_hint) FROM document
                                     WHERE article_id IS NOT NULL GROUP BY article_id"""):
        folder_of[aid] = hint or NO_FOLDER
    for (aid,) in conn.execute("SELECT id FROM article"):
        folder_of.setdefault(aid, NO_FOLDER)
    crossref = journals.records_by_article(conn)
    listed = journals.sigla_by_issn(journal_list)
    from_table = set()   # article.journal_id (riviste.cmd) wins over the folder when the journal has a sigla
    if journal_list:
        sigla_of = {jid: sg for sg, jid in policy_versions.journal_by_sigla(conn, journal_list).items()}
        names_of = {}
        for row in journal_list:
            if row.get("sigla"):
                names_of[row["sigla"].lower()] = row["sigla"]
        for aid, jid in conn.execute("SELECT id, journal_id FROM article WHERE journal_id IS NOT NULL"):
            sg = sigla_of.get(jid)
            if sg:
                folder_of[aid] = names_of.get(sg, sg)
                from_table.add(aid)
    from_issn = set()
    for aid, f in list(folder_of.items()):
        if f == NO_FOLDER:
            found = {listed[x] for x in crossref.get(aid, {}).get("issn", []) if x in listed}
            if len(found) == 1:
                folder_of[aid] = found.pop()
                from_issn.add(aid)
    complete_docs = {d for d, c in classify_all(conn).items() if c.get("classe") == COMPLETE}

    def per_article(sql: str) -> set:
        return {r[0] for r in conn.execute(sql)}

    sets = {
        "doi_confermato": per_article(f"SELECT article_id FROM {_latest('doi_check', 'document_id')} "
                                      "WHERE outcome = 'confirmed'"),
        "pubmed": per_article(f"SELECT article_id FROM {_latest('pubmed_check', 'article_id')} WHERE outcome = 'confirmed'"),
        "testo_aperto": per_article("SELECT DISTINCT article_id FROM article_section"),
        "bibliografia": per_article("SELECT DISTINCT citing_article_id FROM citation"),
        "scheda": per_article("SELECT DISTINCT article_id FROM article_card WHERE status = 'current'"),
    }
    complete = {aid for did, aid in conn.execute("SELECT id, article_id FROM document WHERE article_id IS NOT NULL")
                if did in complete_docs}
    cites = {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT citing_article_id, count(*), sum(verification_status = 'verified') FROM citation GROUP BY 1")}
    def majority(pairs) -> dict:
        groups: dict[str, Counter] = {}
        for aid, name in pairs:
            groups.setdefault(folder_of.get(aid, NO_FOLDER), Counter())[name] += 1
        return {g: sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[0][0] for g, c in groups.items()}

    names = majority((aid, r["titolo"]) for aid, r in crossref.items() if r["titolo"])
    names.update(majority(conn.execute(   # the name compared by the DOI check wins when there is one
        f"""SELECT d.article_id, json_extract(c.details, '$.controlli.rivista.crossref') AS name
            FROM {_latest('doi_check', 'document_id')} c JOIN document d ON d.id = c.document_id
            WHERE c.outcome = 'confirmed' AND d.article_id IS NOT NULL AND name IS NOT NULL AND name <> ''""")))
    pdfs = Counter((folder_of.get(aid) if aid else None) or hint or NO_FOLDER for aid, hint in conn.execute(
        "SELECT article_id, journal_hint FROM document WHERE kind = 'article'"))
    folders = sorted({*pdfs, *folder_of.values()}, key=lambda f: (f == NO_FOLDER, f))
    rows = []
    for f in folders:
        arts = {a for a, h in folder_of.items() if h == f}
        row = {"rivista": f, "nome_crossref": names.get(f),
               "pdf_articoli": pdfs.get(f, 0), "da_tabella": len(arts & from_table), "da_issn": len(arts & from_issn),
               "articoli": len(arts), "articoli_completi": len(arts & complete)}
        row.update({k: len(arts & s) for k, s in sets.items()})
        row["citazioni"] = sum(cites.get(a, (0, 0))[0] for a in arts)
        row["citazioni_verificate"] = sum(cites.get(a, (0, 0))[1] or 0 for a in arts)
        rows.append(row)
    return {"fonte": "article.journal_id, journal_issn, document.journal_hint, riviste_prioritarie.csv, article, doi_check, pubmed_check, article_section, "
                     "citation, article_card",
            "nota": "rivista = quella della tabella journal (article.journal_id, scritta da riviste.cmd; colonna \"Da "
                    "tabella\") quando c'è; altrimenti la cartella in cui è stato letto il PDF (inbox/riviste/<sigla>), un'indicazione; se il PDF è "
                    "stato letto fuori da una cartella di rivista, la sigla di riviste_prioritarie.csv con l'ISSN dato da "
                    "Crossref per il DOI confermato (colonna \"Da ISSN\"); il nome è quello di Crossref per i DOI confermati",
            "righe": rows}


def _guidelines(conn: sqlite3.Connection, journal_list: list[dict] | None = None) -> dict:
    """Dated versions of the author guidelines (journal_policy_version, written by `linee-guida`). Read-only."""
    sigla_of = {jid: sg.upper() for sg, jid in policy_versions.journal_by_sigla(conn, journal_list or []).items()}
    if journal_list:
        exact = {r["sigla"].lower(): r["sigla"] for r in journal_list if r.get("sigla")}
        sigla_of = {jid: exact.get(sg.lower(), sg) for jid, sg in sigla_of.items()}
    rows = []
    for r in conn.execute("""SELECT v.id, v.journal_id, j.title, v.detected_at, v.content_sha256, v.summary,
                                    v.supersedes_id, v.needs_human_review
                             FROM journal_policy_version v JOIN journal j ON j.id = v.journal_id
                             WHERE v.policy_type = 'author_guidelines' ORDER BY j.title, v.detected_at, v.id"""):
        url = re.search(r"\bda (https?://\S+)", r["summary"] or "")
        rows.append({"rivista": sigla_of.get(r["journal_id"]) or r["title"], "titolo": r["title"],
                     "data": r["detected_at"], "indirizzo": url.group(1).rstrip(".,;)") if url else None,
                     "impronta": r["content_sha256"][:12], "sostituisce_una_versione": r["supersedes_id"] is not None,
                     "da_rileggere": bool(r["needs_human_review"])})
    return {"fonte": "journal_policy_version (policy_type author_guidelines), journal",
            "data": _one(conn, "SELECT max(detected_at) FROM journal_policy_version WHERE policy_type = 'author_guidelines'"),
            "versioni": len(rows), "riviste": len({r["titolo"] for r in rows}),
            "da_rileggere": sum(r["da_rileggere"] for r in rows), "righe": rows}


ORIGINAL_FIELDS = [("parole_testo_max", "Parole del testo"), ("abstract_parole_max", "Abstract (parole)"),
                   ("abstract_struttura", "Struttura dell'abstract"), ("riferimenti_max", "Riferimenti"),
                   ("figure_tabelle_max", "Tabelle e figure")]
RULE_FIELDS = [("preprint", "Preprint"), ("politica_ia", "Intelligenza artificiale"), ("revisione", "Revisione"),
               ("orcid", "ORCID"), ("dati", "Dati")]


def _sigla_of(conn: sqlite3.Connection, journal_list: list[dict] | None) -> dict[str, str]:
    sigla_of = {jid: sg.upper() for sg, jid in policy_versions.journal_by_sigla(conn, journal_list or []).items()}
    exact = {r["sigla"].lower(): r["sigla"] for r in journal_list or [] if r.get("sigla")}
    return {jid: exact.get(sg.lower(), sg) for jid, sg in sigla_of.items()}


def _comparison(conn: sqlite3.Connection, journal_list: list[dict] | None = None) -> dict:
    """The current journal cards side by side (T-027 values): the first article type of each card (its original
    article) and the journal rules. Read-only."""
    sigla_of = _sigla_of(conn, journal_list)
    rows = []
    for c in conn.execute("""SELECT c.id, c.journal_id, j.title FROM journal_card c JOIN journal j ON j.id = c.journal_id
                             WHERE c.status = 'current' ORDER BY j.title"""):
        vals = conn.execute("SELECT ord, field, article_type, value, origin FROM journal_card_value WHERE card_id = ? "
                            "ORDER BY ord", (c["id"],)).fetchall()
        first = next((v for v in vals if v["field"] == "tipo_articolo"), None)
        kind = first["article_type"] if first else None

        def pick(field, types):
            for ty in types:
                hit = [v for v in vals if v["field"] == field and v["article_type"] == ty]
                if hit:
                    got = [v["value"] for v in hit if v["value"]]
                    return "; ".join(got) if got else "non indicato"
            return "non indicato"
        row = {"rivista": sigla_of.get(c["journal_id"]) or c["title"], "titolo": c["title"],
               "tipo": first["value"] if first else None}
        for f, _ in ORIGINAL_FIELDS:
            row[f] = pick(f, [kind, journal_cards.ALL_TYPES])
        for f, _ in RULE_FIELDS:
            row[f] = pick(f, [None])
        rows.append(row)
    return {"fonte": "journal_card, journal_card_value (schede delle riviste correnti, journal-cards-v1)",
            "data": _one(conn, "SELECT max(created_at) FROM journal_card"), "righe": rows}


def _metrics(conn: sqlite3.Connection, journal_list: list[dict] | None = None) -> dict:
    sigla_of = _sigla_of(conn, journal_list)
    by_title = {r["title"]: sigla_of.get(r["id"]) for r in conn.execute("SELECT id, title FROM journal")}
    rows = [{**r, "rivista": by_title.get(r["titolo"]) or r["titolo"]} for r in journal_metrics.table(conn)]
    return {"fonte": "journal_metric (SCImago: file scimagojr; OpenAlex: /sources)",
            "data": _one(conn, "SELECT max(retrieved_at) FROM journal_metric"), **journal_metrics.status(conn),
            "righe": [r for r in rows if len(r) > 2]}


def _changes(conn: sqlite3.Connection, journal_list: list[dict] | None = None) -> dict:
    return {"fonte": "journal_policy_version, journal_card, document_page (policy-diff-v1)",
            "data": _one(conn, "SELECT max(detected_at) FROM journal_policy_version"),
            "avvisi": policy_changes.alerts(conn, _sigla_of(conn, journal_list))}


def _claims(conn: sqlite3.Connection) -> dict:
    return {"fonte": "scientific_claim, evidence_record (claims-v1, appraisal-v1)",
            "data": _one(conn, "SELECT max(created_at) FROM scientific_claim"), **claims.status(conn)}


def _checks(conn: sqlite3.Connection) -> dict:
    return {
        "doi": {"fonte": "doi_check (ultimo controllo per PDF)", "data": _one(conn, "SELECT max(checked_at) FROM doi_check"),
                "esiti": _counts(conn, f"SELECT outcome, count(*) FROM {_latest('doi_check', 'document_id')} GROUP BY 1", DOI_IT),
                "regole": _counts(conn, "SELECT rules_version, count(*) FROM doi_check GROUP BY 1")},
        "pubmed": {"fonte": "pubmed_check (ultimo controllo per articolo)",
                   "data": _one(conn, "SELECT max(checked_at) FROM pubmed_check"),
                   "esiti": _counts(conn, f"SELECT outcome, count(*) FROM {_latest('pubmed_check', 'article_id')} GROUP BY 1",
                                    PUBMED_IT),
                   "con_mesh": _one(conn, "SELECT count(DISTINCT article_id) FROM article_mesh")},
        "europepmc": {"fonte": "fulltext_check (ultimo controllo per articolo), article_section",
                      "data": _one(conn, "SELECT max(checked_at) FROM fulltext_check"),
                      "esiti": _counts(conn, f"SELECT outcome, count(*) FROM {_latest('fulltext_check', 'article_id')} "
                                             "GROUP BY 1", EPMC_IT),
                      "disponibilita": _counts(conn, f"SELECT availability, count(*) FROM "
                                                     f"{_latest('fulltext_check', 'article_id')} GROUP BY 1", AVAIL_IT),
                      "testi_salvati": _one(conn, "SELECT count(DISTINCT article_id) FROM article_section"),
                      "sezioni": _one(conn, "SELECT count(*) FROM article_section")},
    }


def _citations(conn: sqlite3.Connection) -> dict:
    return {"fonte": "citation", "data": _one(conn, "SELECT max(verified_at) FROM citation"),
            "riferimenti": _one(conn, "SELECT count(*) FROM citation"),
            "articoli_con_bibliografia": _one(conn, "SELECT count(DISTINCT citing_article_id) FROM citation"),
            "verso_articoli_di_orpheus": _one(conn, "SELECT count(*) FROM citation WHERE cited_article_id IS NOT NULL"),
            "stato": _counts(conn, "SELECT verification_status, count(*) FROM citation GROUP BY 1 ORDER BY 2 DESC", CITE_IT),
            "regole": _counts(conn, "SELECT rules_version, count(*) FROM citation GROUP BY 1")}


def _cards(conn: sqlite3.Connection, home: Path) -> dict:
    st = card_store.status(conn)
    out = {"fonte": "article_card, card_value, workflow_run", "data": _one(conn, "SELECT max(created_at) FROM article_card"),
           **st, "controllo_a_mano": None}
    path = card_store.latest_filled_control(home)
    if path is not None:
        try:
            score = card_store.score_control(path)
            out["controllo_a_mano"] = {"file": path.name, "precisione": score["precisione_complessiva"],
                                       "schede_tutte_giuste": score["schede_tutte_giuste"]}
        except (benchmark.FilledFileError, OSError, KeyError) as exc:
            out["controllo_a_mano"] = {"file": path.name, "errore": str(exc)}
    return out


def build(conn: sqlite3.Connection, home: Path, now: datetime | None = None,
          journal_list: list[dict] | None = None) -> dict:
    """All the numbers of the report. Read-only. `journal_list`: rows of riviste_prioritarie.csv."""
    now = now or datetime.now(UTC)
    return {"generato": now.strftime("%Y-%m-%d %H:%M UTC"),
            "articoli": _one(conn, "SELECT count(*) FROM article"),
            "documenti": _documents(conn), "copertura": _coverage(conn, journal_list), "verifiche": _checks(conn),
            "citazioni": _citations(conn), "schede": _cards(conn, home),
            "catalogo": journals.build(conn, journal_list), "linee_guida": _guidelines(conn, journal_list),
            "avvisi_linee_guida": _changes(conn, journal_list), "metriche": _metrics(conn, journal_list),
            "confronto": _comparison(conn, journal_list), "affermazioni": _claims(conn)}


# ---------------------------------------------------------------- HTML

CSS = """body{font-family:system-ui,Segoe UI,Arial,sans-serif;margin:24px;color:#1d2433;background:#fff;max-width:1100px}
h1{font-size:22px}h2{font-size:17px;margin-top:28px;border-bottom:1px solid #d6dbe4;padding-bottom:4px}
table{border-collapse:collapse;margin:8px 0;font-size:14px}th,td{border:1px solid #d6dbe4;padding:4px 8px;text-align:left}
td.n{text-align:right;font-variant-numeric:tabular-nums}th{background:#f2f4f8}.fonte{color:#5b6475;font-size:12px}
.nota{color:#5b6475;font-size:13px}"""


def _e(x) -> str:
    return html.escape("—" if x is None else str(x))


def _src(sec: dict) -> str:
    return f'<p class="fonte">Fonte: {_e(sec.get("fonte"))} · dato più recente: {_e(sec.get("data"))}</p>'


def _kv(d: dict) -> str:
    if not d:
        return "<p class=\"nota\">nessuna riga</p>"
    return "<table>" + "".join(f"<tr><td>{_e(k)}</td><td class=\"n\">{_e(v)}</td></tr>" for k, v in d.items()) + "</table>"


COVERAGE_COLUMNS = [("rivista", "Rivista"), ("nome_crossref", "Nome (Crossref)"), ("pdf_articoli", "PDF"),
                    ("da_tabella", "Da tabella"), ("da_issn", "Da ISSN"),
                    ("articoli", "Articoli"), ("articoli_completi", "Completi"), ("doi_confermato", "DOI confermati"),
                    ("pubmed", "In PubMed"), ("testo_aperto", "Testi aperti"), ("bibliografia", "Con bibliografia"),
                    ("citazioni", "Riferimenti"), ("citazioni_verificate", "Verificati"), ("scheda", "Schede")]


def _catalogue_html(cat: dict) -> list[str]:
    r = cat["riepilogo"]
    parts = ["<h2>Catalogo delle riviste (Crossref)</h2>", _src(cat),
             f"<p class=\"nota\">Dalle risposte Crossref già salvate (DOI confermati), raggruppate per ISSN: "
             f"{_e(r['riviste'])} riviste per {_e(r['articoli'])} articoli; senza ISSN {_e(r['senza_issn'])}, con più "
             f"titoli {_e(r['con_piu_titoli'])}, con più editori {_e(r['con_piu_editori'])}. La tabella journal non è "
             f"scritta (decisione dell'utente); già presenti: {_e(r['gia_in_tabella_journal'])}.</p>"]
    if not cat["riviste"]:
        return parts + ["<p class=\"nota\">nessun DOI confermato</p>"]
    parts.append("<table><tr><th>Rivista</th><th>Sigla (elenco)</th><th>ISSN</th><th>Editore</th><th>Articoli</th>"
                 "<th>Cartelle</th></tr>")
    for j in cat["riviste"]:
        alt = f"<br><span class=\"nota\">anche: {_e('; '.join(j['altri_titoli']))}</span>" if j["altri_titoli"] else ""
        folders = ", ".join(f"{k} ({v})" for k, v in j["cartelle"].items())
        parts.append(f"<tr><td>{_e(j['titolo'])}{alt}</td><td>{_e(', '.join(j.get('sigla_elenco') or []) or None)}</td>"
                     f"<td>{_e(', '.join(j['issn']) or None)}</td>"
                     f"<td>{_e('; '.join(j['editori']) or None)}</td><td class=\"n\">{_e(j['articoli'])}</td>"
                     f"<td>{_e(folders)}</td></tr>")
    return parts + ["</table>"]


def _guidelines_html(g: dict) -> list[str]:
    parts = ["<h2>Linee guida per gli autori</h2>", _src(g),
             f"<p class=\"nota\">Versioni datate salvate da <code>linee-guida</code> (dentro riviste.cmd): "
             f"{_e(g['versioni'])} versioni di {_e(g['riviste'])} riviste; da rileggere: {_e(g['da_rileggere'])}. "
             "Data = stampa della pagina web dal browser (o lettura del PDF); impronta = inizio dell'impronta SHA-256 del "
             "testo senza le righe del browser.</p>"]
    if not g["righe"]:
        return parts + ["<p class=\"nota\">nessuna versione salvata</p>"]
    parts.append("<table><tr><th>Rivista</th><th>Nome</th><th>Data</th><th>Indirizzo</th><th>Impronta</th>"
                 "<th>Nuova versione</th><th>Da rileggere</th></tr>")
    for r in g["righe"]:
        link = f"<a href=\"{_e(r['indirizzo'])}\">{_e(r['indirizzo'])}</a>" if r["indirizzo"] else _e(None)
        parts.append(f"<tr><td>{_e(r['rivista'])}</td><td>{_e(r['titolo'])}</td><td>{_e(r['data'])}</td><td>{link}</td>"
                     f"<td><code>{_e(r['impronta'])}</code></td><td>{'sì' if r['sostituisce_una_versione'] else 'no'}</td>"
                     f"<td>{'sì' if r['da_rileggere'] else 'no'}</td></tr>")
    return parts + ["</table>"]


def _changes_html(ch: dict) -> list[str]:
    parts = ["<h3>Linee guida cambiate dopo la scheda</h3>", _src(ch)]
    if not ch["avvisi"]:
        return parts + ["<p class=\"nota\">Nessun avviso: le schede delle riviste sono sulle linee guida più recenti.</p>"]
    for a in ch["avvisi"]:
        parts.append(f"<p><b>{_e(policy_changes.summary_line(a))}</b></p><ul>")
        parts += [f"<li>{_e(e['tipo'])} p.{_e(e['pagina'])}: {_e(e['frase'])}</li>" for e in a["esempi"]]
        parts.append("</ul>")
        if a.get("valori_da_ricontrollare"):
            parts.append("<p class=\"nota\">Valori da ricontrollare: " + _e("; ".join(
                f"{v['campo']} {v['tipo_articolo'] or ''}: {v['valore']}" for v in a["valori_da_ricontrollare"])) + "</p>")
    return parts


def _metrics_html(m: dict) -> list[str]:
    parts = ["<h2>Metriche delle riviste</h2>", _src(m),
             "<p class=\"nota\">SCImago (dati Scopus): SJR, quartile migliore e per categoria, h-index, citazioni per documento "
             "in 2 anni, anno di riferimento del file. OpenAlex: citazioni medie in 2 anni e h-index, ricalcolati di continuo "
             "(anno della richiesta). Non sono l'Impact Factor e non vanno confrontati fra fonti diverse.</p>"]
    if not m["righe"]:
        return parts + ["<p class=\"nota\">nessuna metrica (metriche_riviste.cmd)</p>"]
    parts.append("<table><tr><th>Rivista</th><th>SJR</th><th>Quartile</th><th>Categorie (quartile)</th><th>h-index SCImago</th>"
                 "<th>Citazioni/doc 2 anni (SCImago)</th><th>Citazioni medie 2 anni (OpenAlex)</th><th>h-index OpenAlex</th>"
                 "<th>Anni</th></tr>")
    for r in m["righe"]:
        anni = ", ".join(f"{k} {v}" for k, v in (r.get("anni") or {}).items())
        parts.append(f"<tr><td>{_e(r['rivista'])}</td><td class=\"n\">{_e(r.get('sjr'))}</td><td>{_e(r.get('quartile'))}</td>"
                     f"<td>{_e('; '.join(r.get('categorie') or []) or None)}</td><td class=\"n\">{_e(r.get('h_index_scimago'))}</td>"
                     f"<td class=\"n\">{_e(r.get('citazioni_2_anni_scimago'))}</td>"
                     f"<td class=\"n\">{_e(r.get('citazioni_2_anni_openalex'))}</td>"
                     f"<td class=\"n\">{_e(r.get('h_index_openalex'))}</td><td>{_e(anni)}</td></tr>")
    return parts + ["</table>"]


def _comparison_html(c: dict) -> list[str]:
    parts = ["<h2>Riviste a confronto (schede delle riviste)</h2>", _src(c),
             "<p class=\"nota\">Dalle schede correnti, ogni valore copiato dalle linee guida con pagina e frase (controllo a mano "
             "nel database). Articolo originale = il primo tipo di articolo della scheda; per i campi senza quel tipo, il "
             "valore per tutti i tipi.</p>"]
    if not c["righe"]:
        return parts + ["<p class=\"nota\">nessuna scheda delle riviste</p>"]
    parts.append("<h3>Articolo originale</h3><table><tr><th>Rivista</th><th>Tipo</th>" +
                 "".join(f"<th>{_e(t)}</th>" for _, t in ORIGINAL_FIELDS) + "</tr>")
    for r in c["righe"]:
        parts.append(f"<tr><td>{_e(r['rivista'])}</td><td>{_e(r['tipo'])}</td>" +
                     "".join(f"<td>{_e(r[f])}</td>" for f, _ in ORIGINAL_FIELDS) + "</tr>")
    parts.append("</table><h3>Regole della rivista</h3><table><tr><th>Rivista</th>" +
                 "".join(f"<th>{_e(t)}</th>" for _, t in RULE_FIELDS) + "</tr>")
    for r in c["righe"]:
        parts.append(f"<tr><td>{_e(r['rivista'])}</td>" + "".join(f"<td>{_e(r[f])}</td>" for f, _ in RULE_FIELDS) + "</tr>")
    return parts + ["</table>"]


def render_html(data: dict) -> str:
    d, cov, v, c, s = data["documenti"], data["copertura"], data["verifiche"], data["citazioni"], data["schede"]
    parts = [f"<!doctype html><html lang=\"it\"><head><meta charset=\"utf-8\"><title>ORPHEUS — stato</title>"
             f"<style>{CSS}</style></head><body>",
             f"<h1>ORPHEUS — stato della base di conoscenza</h1><p class=\"nota\">Generato il {_e(data['generato'])}, "
             "in sola lettura dal database (nessuna modifica). Articoli nel database: "
             f"<b>{_e(data['articoli'])}</b>.</p>",
             "<h2>Documenti (PDF)</h2>", _src(d),
             f"<p>PDF letti: <b>{_e(d['pdf'])}</b>; collegati a un articolo: {_e(d['con_articolo'])}.</p>",
             "<h3>Per tipo</h3>", _kv(d["per_tipo"]), "<h3>Articoli per classe</h3>", _kv(d["articoli_per_classe"]),
             "<h3>Testo estratto</h3>", _kv(d["testo"]),
             "<h2>Copertura per rivista</h2>", f'<p class="fonte">Fonte: {_e(cov["fonte"])}</p>',
             f'<p class="nota">{_e(cov["nota"])}</p>']
    if cov["righe"]:
        parts.append("<table><tr>" + "".join(f"<th>{_e(t)}</th>" for _, t in COVERAGE_COLUMNS) + "</tr>")
        for r in cov["righe"]:
            parts.append("<tr>" + "".join(
                f"<td>{_e(r[k])}</td>" if k in ("rivista", "nome_crossref") else f"<td class=\"n\">{_e(r[k])}</td>"
                for k, _ in COVERAGE_COLUMNS) + "</tr>")
        parts.append("</table>")
    else:
        parts.append("<p class=\"nota\">nessun documento</p>")
    parts += _catalogue_html(data["catalogo"])
    parts += _guidelines_html(data["linee_guida"])
    if "avvisi_linee_guida" in data:
        parts += _changes_html(data["avvisi_linee_guida"])
    if "metriche" in data:
        parts += _metrics_html(data["metriche"])
    if "confronto" in data:
        parts += _comparison_html(data["confronto"])
    if "affermazioni" in data:
        a = data["affermazioni"]
        parts += ["<h2>Affermazioni ed evidenze</h2>", _src(a),
                  f"<p>Affermazioni: <b>{_e(a['affermazioni'])}</b> di {_e(a['articoli'])} articoli, evidenze "
                  f"{_e(a['evidenze'])}, controllate a mano {_e(a['controllate_a_mano'])}; in italiano "
                  f"{_e(a.get('per_lingua', {}).get('it', 0))}, in inglese {_e(a.get('per_lingua', {}).get('en', 0))}. "
                  "Articoli per livello della prova "
                  "(appraisal-v1: disegno dello studio, almeno 20 partecipanti, gruppo di confronto):</p>",
                  _kv(a["articoli_per_livello"])]
    parts += ["<h2>Verifiche</h2>", "<h3>DOI (Crossref)</h3>", _src(v["doi"]), _kv(v["doi"]["esiti"]),
              "<h3>PubMed</h3>", _src(v["pubmed"]), _kv(v["pubmed"]["esiti"]),
              f"<p>Articoli con termini MeSH: {_e(v['pubmed']['con_mesh'])}</p>",
              "<h3>Europe PMC</h3>", _src(v["europepmc"]), _kv(v["europepmc"]["esiti"]), _kv(v["europepmc"]["disponibilita"]),
              f"<p>Testi aperti salvati (licenza Creative Commons): {_e(v['europepmc']['testi_salvati'])}, "
              f"sezioni: {_e(v['europepmc']['sezioni'])}</p>",
              "<h2>Citazioni (bibliografie)</h2>", _src(c),
              f"<p>Riferimenti: <b>{_e(c['riferimenti'])}</b> di {_e(c['articoli_con_bibliografia'])} articoli; "
              f"verso articoli di ORPHEUS: {_e(c['verso_articoli_di_orpheus'])}.</p>", _kv(c["stato"]),
              "<h2>Schede degli articoli</h2>", _src(s),
              f"<p>Schede accettate: <b>{_e(s['schede_accettate'])}</b>, valori: {_e(s['valori'])}; "
              f"pacchetti aperti: {_e(s['pacchetti_aperti'])}.</p>"]
    ctrl = s.get("controllo_a_mano")
    if ctrl and "precisione" in ctrl:
        p, w = ctrl["precisione"], ctrl["schede_tutte_giuste"]
        parts.append(f"<p>Controllo a mano ({_e(ctrl['file'])}): {_e(p['giusti'])} valori giusti, {_e(p['sbagliati'])} "
                     f"sbagliati, non compilati {_e(p['non_compilati'])}; precisione {_e(p['precisione'])}; schede "
                     f"interamente giuste {_e(w['tutte_giuste'])} su {_e(w['compilate'])}.</p>")
    elif ctrl:
        parts.append(f"<p class=\"nota\">Controllo a mano ({_e(ctrl['file'])}) non leggibile: {_e(ctrl.get('errore'))}</p>")
    else:
        parts.append("<p class=\"nota\">Nessun controllo a mano compilato.</p>")
    parts.append("</body></html>\n")
    return "\n".join(parts)


def write_html(data: dict, home: Path, now: datetime | None = None) -> Path:
    now = now or datetime.now()
    folder = card_store.reports(home)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{REPORT_PREFIX}_{now.strftime('%Y-%m-%d_%H%M%S')}.html"
    path.write_text(render_html(data), encoding="utf-8")
    return path
