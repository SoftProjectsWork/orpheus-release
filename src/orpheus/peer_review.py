"""T-040 parte 3 (Fase 6, B4, F3): simulated peer review of a manuscript, contract review-v1.

ORPHEUS prepares on the PC a work packet for a session with Claude: the paragraphs of the manuscript numbered, the
section of each, the outcome of every check of the manuscript against the chosen journal (compliance-v1, numbers-v1,
checklists-v1; rules with the page of the guidelines) and three reviewer profiles (methodologist, statistician,
editor of the journal). Claude writes the review as JSON; ORPHEUS accepts it only if:
- every observation is anchored to a paragraph and quotes it exactly (same comparison as the article cards);
- every observation has at least one basis: a check of the packet (by id) or the reviewer's own judgement, marked so;
- every check "da correggere" of the packet is discussed by at least one observation;
- each of the three profiles is there once, with a summary, a recommendation and at least one observation;
- no text presents it as a prediction (probability of acceptance, "will be accepted"): it is a simulation.

R10: the manuscript and the review stay on the PC (rapporti/manoscritti/revisione/<impronta>_<rivista>/); the
command prints only counts and outcomes. Draft manuscripts never go to an external chat (ADR-0011).
"""
from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import cards, compliance, manuscript as ms

REVIEW_VERSION = "review-v1"
PACKET_FORMAT = "orpheus-pacchetto-revisione-v1"
REVIEW_FORMAT = "orpheus-revisione-v1"
FOLDER = "revisione"
PACKET = "pacchetto.json"
TO_SEND = "da_inviare"
ANSWER = "revisione.json"
ACCEPTED = "revisione_accettata.json"
REPORT = "revisione.html"
REASONS = "revisione.motivi.txt"
README = "LEGGIMI.txt"
QUOTE_MIN, QUOTE_MAX = 12, 400
SEVERITIES = ("maggiore", "minore", "suggerimento")
RECOMMENDATIONS = ("accettare", "revisioni minori", "revisioni maggiori", "rifiutare")
BASES = ("controllo", "giudizio")

PROFILES = {
    "metodologo": "Methodologist: research question and design, participants and sampling, procedures and their "
                  "reproducibility, bias and validity, measurement, adherence to the reporting checklist (CONSORT, "
                  "STROBE, PRISMA), consistency between aims, methods and conclusions.",
    "statistico": "Statistician: analysis plan and its fit to the design and the data, sample size, assumptions, "
                  "effect sizes and confidence intervals, multiplicity, consistency of numbers (n/N, percentages, CI "
                  "containing the estimate, p values), how results are reported in tables and abstract.",
    "editor": "Editor of the chosen journal: fit with the journal and its readers, the journal's rules (length, "
              "abstract, structure, references, declarations, ethics, registration, data, AI), novelty and "
              "practical relevance, clarity of title, abstract and conclusions, what to fix before submission.",
}

INSTRUCTIONS = """Revisione simulata (contratto review-v1). Scrivi il JSON in da_inviare/revisione.json di questa cartella e
controllalo con: orpheus revisione --controlla da_inviare/revisione.json --pacchetto pacchetto.json

Regole
1. Tre profili, una volta ciascuno: "metodologo", "statistico", "editor" (vedi "profili"). Ogni profilo ha "sintesi"
   (2-5 frasi), "raccomandazione" (una di: accettare, revisioni minori, revisioni maggiori, rifiutare) e almeno una
   osservazione. Scrivi in inglese, come un referee report vero, ma è una SIMULAZIONE: non scrivere probabilità di
   accettazione né previsioni ("will be accepted", "likely to be rejected").
2. Ogni osservazione: "gravita" (maggiore, minore, suggerimento); "paragrafo" (numero n in manoscritto.paragrafi);
   "citazione": un pezzo CONTINUO copiato da quel paragrafo (12-400 caratteri, niente puntini); "problema" e
   "proposta" (cosa cambiare, concreto); "basi": almeno una, {"tipo": "controllo", "id": "<id di un controllo del
   pacchetto>"} oppure {"tipo": "giudizio"} quando è il giudizio del revisore simulato.
3. Ogni controllo con esito "ko" (da correggere) del pacchetto deve essere discusso da almeno un'osservazione con
   quella base. Per i controlli "guarda" decidi tu.
4. Non inventare dati, riferimenti o regole: le regole della rivista sono quelle dei controlli (con la pagina delle
   linee guida in "fonte").
5. Copia "impronta" e "rivista" dal pacchetto; "modello": il modello che scrive (es. claude-opus-5-5).
"""

EXAMPLE = {
    "formato": REVIEW_FORMAT, "versione": REVIEW_VERSION, "impronta": "<impronta del pacchetto>", "rivista": "<sigla>",
    "modello": "claude-opus-5-5",
    "profili": [
        {"profilo": "metodologo", "sintesi": "A well-designed trial; allocation concealment is not described.",
         "raccomandazione": "revisioni maggiori",
         "osservazioni": [
             {"gravita": "maggiore", "paragrafo": 14, "citazione": "Participants were randomly allocated to two groups",
              "problema": "The method of sequence generation and allocation concealment is not reported.",
              "proposta": "Describe how the sequence was generated and concealed (CONSORT items 8-10).",
              "basi": [{"tipo": "controllo", "id": "checklist_consort"}]}]},
    ],
}

_PREDICTION = re.compile(r"probabilit[àa]\s+di\s+(?:essere\s+)?accett|probabilit[yi]e?s?\s+of\s+(?:being\s+)?accept|"
                         r"chance[s]?\s+of\s+(?:being\s+)?(?:accept|reject|publish)|will\s+(?:surely\s+|certainly\s+)?be\s+"
                         r"(?:accepted|rejected|published)|(?:likely|unlikely)\s+to\s+be\s+(?:accepted|rejected|published)|"
                         r"\d+\s*%\s*(?:chance|probabilit)", re.I)


# ------------------------------------------------------------------------------------------------------- the packet
def folder(home: Path, fingerprint: str, sigla: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", sigla or "rivista")
    return compliance.reports_dir(home) / FOLDER / f"{fingerprint[:12]}_{safe}"


def paragraphs(m: ms.Manuscript) -> list[dict]:
    """The paragraphs a reviewer reads (title, abstract, text, statements; not the reference list), with their section."""
    starts = sorted((s.start, s.name) for s in m.sections)
    out = []
    for p in m.doc.paragraphs:
        name = "front"
        for start, sname in starts:
            if start <= p.i:
                name = sname
        if name in ("references",) or not p.text.strip():
            continue
        out.append({"n": p.i, "sezione": ms.SECTION_NAMES.get(name, name), "testo": p.text})
    return out


def build_packet(m: ms.Manuscript, result: dict, now: datetime | None = None) -> dict:
    """result: compliance.run for ONE journal."""
    j = result["riviste"][0]
    checks = [{k: c.get(k) for k in ("id", "gruppo", "titolo", "esito", "trovato", "regola", "fonte", "dettagli")}
              for c in j["controlli"]]
    return {
        "formato": PACKET_FORMAT, "versione": REVIEW_VERSION, "impronta": result["manoscritto"]["impronta"],
        "creato_il": (now or datetime.now()).strftime("%Y-%m-%d %H:%M"),
        "rivista": {"sigla": j["rivista"], "titolo": j["titolo"], "tipo_scheda": j["tipo_scheda"],
                    "data_scheda": j["data_scheda"], "data_linee_guida": j["data_linee_guida"],
                    "affinita": j.get("affinita")},
        "tipo": result["tipo_testo"], "istruzioni": INSTRUCTIONS, "profili": PROFILES, "esempio": EXAMPLE,
        "riepilogo": result["manoscritto"], "controlli": checks,
        "manoscritto": {"titolo": m.title, "paragrafi": paragraphs(m),
                        "riferimenti": [{"n": r.n, "testo": r.text, "doi": r.doi} for r in m.references]},
        "nota": "Revisione simulata: non è una previsione dell'esito della rivista.",
    }


def readme(sigla: str) -> str:
    return f"""REVISIONE SIMULATA (review-v1) — rivista {sigla}

Questa cartella resta su questo PC (R10: mai su GitHub, mai in una chat esterna).
1. In una sessione con Claude, con il PC collegato, scrivi: "fai la revisione simulata" (Claude legge pacchetto.json
   e scrive da_inviare/revisione.json).
2. Rilancia revisione.cmd e scegli lo stesso manoscritto e la stessa rivista: ORPHEUS controlla la revisione.
   - Accettata: si apre revisione.html (osservazioni per profilo, con il paragrafo e la regola della rivista).
   - Rifiutata: revisione.motivi.txt dice perché; Claude la corregge.
"""


def write_packet(home: Path, packet: dict) -> Path:
    d = folder(home, packet["impronta"], packet["rivista"]["sigla"])
    (d / TO_SEND).mkdir(parents=True, exist_ok=True)
    (d / PACKET).write_text(json.dumps(packet, ensure_ascii=False, indent=1), encoding="utf-8")
    (d / README).write_text(readme(packet["rivista"]["sigla"]), encoding="utf-8")
    return d


# --------------------------------------------------------------------------------------------------- the checks
@dataclass
class Result:
    name: str
    ok: bool
    problems: list[str] = field(default_factory=list)


def _s(v) -> str:
    return v.strip() if isinstance(v, str) else ""


def check_review(packet: dict, data) -> list[Result]:
    out: list[Result] = []
    fmt: list[str] = []
    if not isinstance(data, dict):
        return [Result("formato", False, ["la revisione deve essere un oggetto JSON"])]
    if data.get("formato") != REVIEW_FORMAT or data.get("versione") != REVIEW_VERSION:
        fmt.append(f'"formato" deve essere {REVIEW_FORMAT} e "versione" {REVIEW_VERSION}')
    if len(_s(data.get("modello"))) < 3:
        fmt.append('manca "modello"')
    profiles = data.get("profili")
    if not isinstance(profiles, list):
        fmt.append('"profili" deve essere una lista')
        profiles = []
    out.append(Result("formato", not fmt, fmt))
    pk = []
    if data.get("impronta") != packet["impronta"]:
        pk.append("impronta diversa da quella del pacchetto (il manoscritto è cambiato?)")
    if data.get("rivista") != packet["rivista"]["sigla"]:
        pk.append("rivista diversa da quella del pacchetto")
    out.append(Result("pacchetto", not pk, pk))

    names = [p.get("profilo") for p in profiles if isinstance(p, dict)]
    prof = []
    for name in PROFILES:
        if names.count(name) != 1:
            prof.append(f"il profilo {name} deve comparire una volta (ora {names.count(name)})")
    for n in names:
        if n not in PROFILES:
            prof.append(f"profilo sconosciuto: {n}")
    paras = {p["n"]: p["testo"] for p in packet["manoscritto"]["paragrafi"]}
    check_ids = {c["id"]: c for c in packet["controlli"]}
    pos, quotes, bases, sim = [], [], [], []
    covered: set[str] = set()
    forms: dict[int, tuple[str, ...]] = {}
    for p in profiles:
        if not isinstance(p, dict):
            prof.append("un profilo non è un oggetto")
            continue
        who = p.get("profilo", "?")
        if not _s(p.get("sintesi")):
            prof.append(f"{who}: manca la sintesi")
        if p.get("raccomandazione") not in RECOMMENDATIONS:
            prof.append(f"{who}: raccomandazione non valida (una di: {', '.join(RECOMMENDATIONS)})")
        obs = p.get("osservazioni")
        if not isinstance(obs, list) or not obs:
            prof.append(f"{who}: serve almeno un'osservazione")
            obs = []
        texts = [_s(p.get("sintesi"))]
        for k, o in enumerate(obs, 1):
            lab = f"{who} {k}"
            if not isinstance(o, dict):
                prof.append(f"{lab}: non è un oggetto")
                continue
            if o.get("gravita") not in SEVERITIES:
                prof.append(f"{lab}: gravita non valida (una di: {', '.join(SEVERITIES)})")
            if not _s(o.get("problema")) or not _s(o.get("proposta")):
                prof.append(f"{lab}: servono problema e proposta")
            texts += [_s(o.get("problema")), _s(o.get("proposta"))]
            n = o.get("paragrafo")
            if not isinstance(n, int) or n not in paras:
                pos.append(f"{lab}: il paragrafo {n!r} non c'è nel pacchetto")
            else:
                q = _s(o.get("citazione"))
                if "..." in q or "…" in q:
                    quotes.append(f"{lab}: la citazione deve essere un pezzo continuo, senza puntini")
                elif not (QUOTE_MIN <= len(cards.squash(q)) and len(q) <= QUOTE_MAX):
                    quotes.append(f"{lab}: citazione di {QUOTE_MIN}-{QUOTE_MAX} caratteri")
                else:
                    forms.setdefault(n, cards.text_forms(paras[n]))
                    if not cards.quote_in_text(q, forms[n]):
                        quotes.append(f"{lab}: la citazione non si trova nel paragrafo {n}: \"{q[:100]}\"")
            b = o.get("basi")
            if not isinstance(b, list) or not b:
                bases.append(f"{lab}: serve almeno una base (controllo o giudizio)")
                continue
            for x in b:
                t = x.get("tipo") if isinstance(x, dict) else None
                if t not in BASES:
                    bases.append(f"{lab}: tipo di base non valido (controllo o giudizio)")
                elif t == "controllo":
                    cid = x.get("id")
                    if cid not in check_ids:
                        bases.append(f"{lab}: il controllo {cid!r} non c'è nel pacchetto")
                    else:
                        covered.add(cid)
        for t in texts:
            if t and _PREDICTION.search(t):
                sim.append(f"{who}: è una simulazione, non una previsione: togli \"{_PREDICTION.search(t).group(0)}\"")
    out.append(Result("profili_completi", not prof, prof))
    out.append(Result("posizione", not pos, pos))
    out.append(Result("citazione_nel_testo", not quotes, quotes))
    out.append(Result("basi", not bases, bases))
    miss = [f"il controllo da correggere \"{c['titolo']}\" ({c['id']}) non è discusso da nessuna osservazione"
            for c in packet["controlli"] if c["esito"] == "ko" and c["id"] not in covered]
    out.append(Result("copertura", not miss, miss))
    out.append(Result("simulazione", not sim, sim))
    return out


def verdict(results: list[Result]) -> str:
    return "accettabile" if all(r.ok for r in results) else "da correggere"


# ----------------------------------------------------------------------------------------------- read and report
def process(home: Path, packet: dict, now: datetime | None = None) -> dict:
    """If Claude wrote da_inviare/revisione.json: check it; accepted -> report. Counts only in the result."""
    d = folder(home, packet["impronta"], packet["rivista"]["sigla"])
    src = d / TO_SEND / ANSWER
    reasons = d / TO_SEND / REASONS
    out = {"cartella": str(d), "revisione": "da scrivere"}
    if not src.exists():
        if (d / ACCEPTED).exists():
            out.update(_counts(json.loads((d / ACCEPTED).read_text(encoding="utf-8"))), revisione="accettata",
                       rapporto=str(d / REPORT))
        return out
    try:
        data = json.loads(src.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        data = None
        results = [Result("formato", False, [f"JSON non leggibile: {exc}"])]
    else:
        results = check_review(packet, data)
    out["controlli"] = {r.name: ("ok" if r.ok else "non superato") for r in results}
    if verdict(results) != "accettabile":
        reasons.write_text("\n".join(f"[{r.name}] {p}" for r in results for p in r.problems) + "\n", encoding="utf-8")
        out.update(revisione="rifiutata", problemi=sum(len(r.problems) for r in results), motivi=str(reasons))
        return out
    (d / ACCEPTED).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    src.unlink()
    if reasons.exists():
        reasons.unlink()
    (d / REPORT).write_text(render_html(packet, data, now or datetime.now()), encoding="utf-8")
    out.update(_counts(data), revisione="accettata", rapporto=str(d / REPORT))
    return out


def _counts(data: dict) -> dict:
    obs = [o for p in data["profili"] for o in p["osservazioni"]]
    return {"osservazioni": len(obs), **{g: sum(o["gravita"] == g for o in obs) for g in SEVERITIES},
            "raccomandazioni": {p["profilo"]: p["raccomandazione"] for p in data["profili"]}}


def _e(x) -> str:
    return html.escape("" if x is None else str(x))


def render_html(packet: dict, data: dict, now: datetime) -> str:
    paras = {p["n"]: p for p in packet["manoscritto"]["paragrafi"]}
    checks = {c["id"]: c for c in packet["controlli"]}
    j = packet["rivista"]
    order = {g: i for i, g in enumerate(SEVERITIES)}
    parts = [f"<!doctype html><html lang='it'><head><meta charset='utf-8'><title>Revisione simulata</title>"
             f"<meta name='viewport' content='width=device-width,initial-scale=1'><style>{compliance.CSS}"
             ".q{background:#fff;border-left:3px solid #c9b98f;padding:4px 10px;margin:4px 0;font-style:italic}"
             "</style></head><body>",
             f"<h1>Revisione simulata — {_e(j['sigla'])}</h1>",
             f"<p class='sub'>{_e(j['titolo'])} · scheda del {_e(j['data_scheda'])} · linee guida del "
             f"{_e(j['data_linee_guida'])} · manoscritto {_e(packet['impronta'])} · {_e(packet['tipo'])} · "
             f"modello {_e(data['modello'])} · {now:%d/%m/%Y %H:%M}</p>",
             "<p class='note'><b>È una simulazione</b> (review-v1): tre revisori immaginari, ognuno con le sue "
             "osservazioni ancorate a un paragrafo e, quando c'è, alla regola della rivista o al controllo di "
             "ORPHEUS. Non è una previsione dell'esito della rivista.</p>"]
    c = _counts(data)
    parts.append("<div class='kpi'>" + "".join(f"<div><b>{c[g]}</b>{g}</div>" for g in SEVERITIES) +
                 "".join(f"<div><b>{_e(r)}</b>{_e(p)}</div>" for p, r in c["raccomandazioni"].items()) + "</div>")
    for p in data["profili"]:
        parts.append(f"<h2>{_e(p['profilo'].capitalize())} — {_e(p['raccomandazione'])}</h2>"
                     f"<p>{_e(p['sintesi'])}</p><table><tr><th>Gravità</th><th>Dove</th><th>Problema e proposta</th>"
                     "<th>Basi</th></tr>")
        for o in sorted(p["osservazioni"], key=lambda o: order[o["gravita"]]):
            where = paras.get(o["paragrafo"], {})
            cls = {"maggiore": "ko", "minore": "guarda", "suggerimento": "ok"}[o["gravita"]]
            basis = []
            for b in o["basi"]:
                if b["tipo"] == "giudizio":
                    basis.append("giudizio del revisore simulato")
                else:
                    ch = checks[b["id"]]
                    src = ch.get("fonte") or {}
                    page = f", pagina {src.get('pagina')} delle linee guida" if src.get("pagina") else ""
                    basis.append(f"{_e(ch['titolo'])} ({_e(ch['esito'])}){_e(page)}"
                                 + (f"<div class='src'>{_e(ch.get('regola'))}</div>" if ch.get("regola") else ""))
            parts.append(f"<tr><td><span class='b {cls}'>{_e(o['gravita'])}</span></td>"
                         f"<td>§{_e(o['paragrafo'])} · {_e(where.get('sezione'))}</td>"
                         f"<td><div class='q'>“{_e(o['citazione'])}”</div><b>Problema.</b> {_e(o['problema'])}"
                         f"<br><b>Proposta.</b> {_e(o['proposta'])}</td><td>{'<br>'.join(basis)}</td></tr>")
        parts.append("</table>")
    ko = [ch for ch in packet["controlli"] if ch["esito"] == "ko"]
    if ko:
        parts.append("<h2>Controlli da correggere e osservazioni che li discutono</h2><table><tr><th>Controllo</th>"
                     "<th>Trovato</th><th>Discusso da</th></tr>")
        for ch in ko:
            who = sorted({p["profilo"] for p in data["profili"] for o in p["osservazioni"]
                          for b in o["basi"] if b.get("id") == ch["id"]})
            parts.append(f"<tr><td>{_e(ch['titolo'])}</td><td>{_e(ch['trovato'])}</td><td>{_e(', '.join(who))}</td></tr>")
        parts.append("</table>")
    parts.append("<p class='src'>Fonte: pacchetto della revisione (review-v1) e controllo del manoscritto "
                 "(compliance-v1) su questo PC. Il manoscritto non lascia il PC.</p></body></html>")
    return "".join(parts)
