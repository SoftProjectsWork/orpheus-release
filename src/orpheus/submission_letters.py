"""Fase 6 (Q28, Q29): the letters around a submission, written by Claude in a session and checked by ORPHEUS.

- Cover letter (contract cover-v1): to the editor of ONE journal, from the manuscript (.docx) and the journal card.
  Accepted only if it names the manuscript title and the journal, the type of article, says the work is not published
  or under consideration elsewhere and that all authors approved it, has a conflicts statement, carries the
  registration number when the manuscript has one, every number in it is in the manuscript, 150-600 words, and it does
  not predict the outcome. ORPHEUS writes lettera.docx (date, "Dear Editor", the paragraphs, a signature to fill in).
- Response to reviewers (contract response-v1): from the reviewers' comments pasted by the user into
  commenti_revisori.txt and the REVISED manuscript. Every comment is answered once; a change is shown by an exact
  quote of the paragraph of the revised manuscript; a comment not followed is explained (at least 15 words); every
  number in an answer is in the manuscript or in the comment. ORPHEUS writes risposta_revisori.docx.

R10: manuscripts, comments and letters stay on the PC (rapporti/manoscritti/lettera|risposta/<impronta>_<sigla>/);
the command prints only counts and outcomes. Never in an external chat (ADR-0011).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import article_docx, cards, compliance, manuscript as ms
from .peer_review import _PREDICTION, paragraphs

COVER_VERSION, COVER_FORMAT = "cover-v1", "orpheus-lettera-v1"
RESPONSE_VERSION, RESPONSE_FORMAT = "response-v1", "orpheus-risposta-v1"
TO_SEND = "da_inviare"
PACKET = "pacchetto.json"
README = "LEGGIMI.txt"
COMMENTS = "commenti_revisori.txt"
COVER = {"folder": "lettera", "answer": "lettera.json", "docx": "lettera.docx", "reasons": "lettera.motivi.txt",
         "accepted": "lettera_accettata.json"}
RESPONSE = {"folder": "risposta", "answer": "risposta.json", "docx": "risposta_revisori.docx",
            "reasons": "risposta.motivi.txt", "accepted": "risposta_accettata.json"}
WORDS_MIN, WORDS_MAX = 150, 600
ACTIONS = ("modificato", "chiarito", "non modificato")
QUOTE_MIN, QUOTE_MAX = 12, 400

KIND_WORDS = {"originale": r"original (?:research|article|investigation|study|paper)|research article|"
                           r"original contribution|randomi[sz]ed|trial|study",
              "revisione": r"systematic review|meta-?analys|scoping review|review",
              "protocollo": r"protocol"}
ORIGINALITY = re.compile(r"not (?:been )?(?:previously )?(?:published|submitted|under consideration|being considered)|"
                         r"(?:is|are) not under consideration|no part of (?:this|the) (?:work|manuscript)|"
                         r"has not been published|original (?:work|and unpublished)|unpublished", re.I)
APPROVED = re.compile(r"all (?:the )?(?:co-?)?authors (?:have )?(?:read and )?(?:approved|agreed|consented)|"
                      r"approved by all (?:the )?(?:co-?)?authors|all authors have seen and approved", re.I)
CONFLICTS = ms.STATEMENTS["conflitti"]


@dataclass
class Result:
    name: str
    ok: bool
    problems: list[str] = field(default_factory=list)


def verdict(results: list[Result]) -> str:
    return "accettabile" if all(r.ok for r in results) else "da correggere"


def _s(v) -> str:
    return v.strip() if isinstance(v, str) else ""


def folder(home: Path, kind: dict, fingerprint: str, sigla: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", sigla or "rivista")
    return compliance.reports_dir(home) / kind["folder"] / f"{fingerprint[:12]}_{safe}"


def _full_text(m: ms.Manuscript) -> str:
    return "\n".join(p.text for p in m.doc.paragraphs) + "\n" + "\n".join("\n".join(t) for t in m.doc.tables)


def _common(m: ms.Manuscript, result: dict, version: str, fmt: str, now: datetime | None) -> dict:
    j = result["riviste"][0]
    return {"formato": fmt, "versione": version, "impronta": result["manoscritto"]["impronta"],
            "creato_il": (now or datetime.now()).strftime("%Y-%m-%d %H:%M"),
            "rivista": {"sigla": j["rivista"], "titolo": j["titolo"], "tipo_scheda": j["tipo_scheda"],
                        "data_scheda": j["data_scheda"], "data_linee_guida": j["data_linee_guida"]},
            "tipo": result["tipo"], "tipo_testo": result["tipo_testo"],
            "manoscritto": {"titolo": m.title, "paragrafi": paragraphs(m),
                            "registrazioni": [f"{a} {b}" for a, b, _ in m.registrations]}}


def _write(home: Path, kind: dict, packet: dict, readme: str) -> Path:
    d = folder(home, kind, packet["impronta"], packet["rivista"]["sigla"])
    (d / TO_SEND).mkdir(parents=True, exist_ok=True)
    (d / PACKET).write_text(json.dumps(packet, ensure_ascii=False, indent=1), encoding="utf-8")
    (d / README).write_text(readme, encoding="utf-8")
    return d


def _numbers_traced(text: str, sources: str) -> list[str]:
    return cards.missing_numbers(text, sources)


# ================================================================================================ cover letter
COVER_INSTRUCTIONS = """Lettera di presentazione (contratto cover-v1), in inglese, all'editor di questa rivista. Scrivi il JSON in
da_inviare/lettera.json di questa cartella e controllalo con:
orpheus lettera --controlla da_inviare/lettera.json --pacchetto pacchetto.json

Regole
1. "paragrafi": 3-6 paragrafi, 150-600 parole in tutto, senza saluto iniziale né firma (li aggiunge ORPHEUS).
2. Deve contenere: il titolo esatto del manoscritto; il nome della rivista; il tipo di articolo; perché è adatto ai
   lettori della rivista (temi dell'affinità, se ci sono); il risultato principale con i numeri del manoscritto;
   che il lavoro non è pubblicato né in valutazione altrove; che tutti gli autori l'hanno approvato; i conflitti di
   interesse; il numero di registrazione se il manoscritto ne ha uno; quello che chiedono le regole in "regole_lettera".
3. Ogni numero della lettera deve essere scritto nel manoscritto. Niente nomi di editor, autori o istituzioni
   inventati: usa "Dear Editor" (lo mette ORPHEUS). Nessuna previsione dell'esito.
4. Copia "impronta" e "rivista" dal pacchetto; "modello": il modello che scrive.
"""


def cover_packet(m: ms.Manuscript, result: dict, rules: compliance.JournalRules, now: datetime | None = None) -> dict:
    p = _common(m, result, COVER_VERSION, COVER_FORMAT, now)
    letter_rules = [{"campo": r.field, "valore": r.value, "pagina": r.page}
                    for f in ("documenti_invio", "conflitti_finanziamenti", "preprint", "revisione")
                    for r in rules.get(f)]
    j = result["riviste"][0]
    p.update({"istruzioni": COVER_INSTRUCTIONS, "regole_lettera": letter_rules, "affinita": j.get("affinita"),
              "abstract": m.abstract_text,
              "controlli_da_correggere": [c["titolo"] for c in j["controlli"] if c["esito"] == "ko"],
              "esempio": {"formato": COVER_FORMAT, "versione": COVER_VERSION, "impronta": "<impronta>",
                          "rivista": "<sigla>", "modello": "claude-opus-5-5",
                          "paragrafi": ["We are pleased to submit our original research article entitled \"…\" for "
                                        "consideration in …", "…"]}})
    return p


def check_cover(packet: dict, data) -> list[Result]:
    if not isinstance(data, dict):
        return [Result("formato", False, ["la lettera deve essere un oggetto JSON"])]
    fmt = []
    if data.get("formato") != COVER_FORMAT or data.get("versione") != COVER_VERSION:
        fmt.append(f'"formato" deve essere {COVER_FORMAT} e "versione" {COVER_VERSION}')
    if len(_s(data.get("modello"))) < 3:
        fmt.append('manca "modello"')
    paras = data.get("paragrafi")
    if not isinstance(paras, list) or not all(isinstance(x, str) and x.strip() for x in paras) or not 3 <= len(paras) <= 6:
        fmt.append('"paragrafi": da 3 a 6 testi')
        paras = [x for x in paras if isinstance(x, str)] if isinstance(paras, list) else []
    pk = []
    if data.get("impronta") != packet["impronta"]:
        pk.append("impronta diversa da quella del pacchetto (il manoscritto è cambiato?)")
    if data.get("rivista") != packet["rivista"]["sigla"]:
        pk.append("rivista diversa da quella del pacchetto")
    text = "\n".join(paras)
    words = ms.words_of(text)
    length = [] if WORDS_MIN <= words <= WORDS_MAX else [f"{words} parole: servono {WORDS_MIN}-{WORDS_MAX}"]
    need = []
    sq = cards.squash(text)
    title = packet["manoscritto"]["titolo"] or ""
    if title and cards.squash(title) not in sq:
        need.append("manca il titolo esatto del manoscritto")
    if cards.squash(packet["rivista"]["titolo"]) not in sq:
        need.append(f"manca il nome della rivista ({packet['rivista']['titolo']})")
    if not re.search(KIND_WORDS.get(packet["tipo"], r"article"), text, re.I):
        need.append(f"manca il tipo di articolo ({packet['tipo_testo']})")
    if not ORIGINALITY.search(text):
        need.append("manca che il lavoro non è pubblicato né in valutazione altrove")
    if not APPROVED.search(text):
        need.append("manca che tutti gli autori hanno approvato il manoscritto")
    if not CONFLICTS.search(text):
        need.append("manca la dichiarazione sui conflitti di interesse")
    for reg in packet["manoscritto"]["registrazioni"]:
        if cards.squash(reg.split(" ", 1)[-1]) not in sq:
            need.append(f"manca il numero di registrazione {reg}")
    sources = "\n".join(p["testo"] for p in packet["manoscritto"]["paragrafi"]) + "\n" + packet.get("abstract", "") + \
        "\n" + title + "\n" + packet["rivista"]["titolo"]
    miss = _numbers_traced(text, sources)
    nums = [f"numeri che non sono nel manoscritto: {', '.join(miss)}"] if miss else []
    sim = []
    if _PREDICTION.search(text):
        sim.append(f"è una lettera, non una previsione: togli \"{_PREDICTION.search(text).group(0)}\"")
    if re.search(r"\[[^\]]*\]|<[^>]*>|\bXX+\b|…|\.\.\.", text):
        sim.append("niente segnaposto ([…], <…>, XX) né puntini: la lettera deve essere completa")
    return [Result("formato", not fmt, fmt), Result("pacchetto", not pk, pk), Result("lunghezza", not length, length),
            Result("elementi", not need, need), Result("numeri_tracciati", not nums, nums),
            Result("completezza", not sim, sim)]


def cover_docx(path: Path, packet: dict, data: dict, lay: dict, now: datetime) -> Path:
    B = article_docx.Block
    blocks = [B("p", [(now.strftime("%d %B %Y"), {})]), B("p", [(f"The Editor, {packet['rivista']['titolo']}", {})]),
              B("p", [("Dear Editor,", {})])]
    blocks += [B("p", [(t.strip(), {})]) for t in data["paragrafi"]]
    blocks += [B("p", [("Yours sincerely,", {})]),
               B("p", [("[Name of the corresponding author, on behalf of all authors]", {"i": True})]),
               B("p", [("[Affiliation, address, e-mail, ORCID]", {"i": True})])]
    return article_docx.write_docx(path, blocks, {**lay, "numeri_di_riga": False}, "Cover letter")


# ========================================================================================= response to reviewers
RESPONSE_INSTRUCTIONS = """Risposta ai revisori (contratto response-v1), in inglese. Scrivi il JSON in da_inviare/risposta.json di
questa cartella e controllalo con:
orpheus risposta --controlla da_inviare/risposta.json --pacchetto pacchetto.json

Regole
1. Una risposta per ogni commento di "commenti" (stesso "id"), una sola volta.
2. "azione": "modificato" (hai cambiato il manoscritto: "paragrafo" = numero n nel manoscritto RIVEDUTO e
   "citazione" = un pezzo continuo copiato da quel paragrafo, 12-400 caratteri, che mostra il cambiamento),
   "chiarito" (spieghi senza cambiare; paragrafo e citazione facoltativi), "non modificato" (non segui il commento:
   spiega perché con rispetto, almeno 15 parole).
3. "risposta": cortese, precisa, senza promettere cose che il manoscritto non fa. Ogni numero deve essere nel
   manoscritto riveduto o nel commento. Niente dati, riferimenti o analisi inventati.
4. Copia "impronta" e "rivista" dal pacchetto; "modello": il modello che scrive.
"""

_REVIEWER = re.compile(r"^\s*(?:(?:reviewer|referee|revisore)\s*#?\s*(\d+)|(editor|associate editor|editore))\b[\s:#.-]*",
                       re.I)
_ITEM = re.compile(r"^\s*(?:(?:comment|point|issue|commento|punto)\s*)?#?\s*(\d{1,3})\s*[.):\-–]\s+", re.I)


def parse_comments(text: str) -> list[dict]:
    """The comments pasted from the decision letter: a heading line 'Reviewer 1' / 'Editor' starts a reviewer; a line
    starting with a number ('1.', '2)', 'Comment 3:') starts a comment; otherwise a blank line ends a comment."""
    who, n_by, out, cur = "R1", {}, [], None

    def close():
        nonlocal cur
        if cur and cur["testo"].strip():
            out.append(cur)
        cur = None

    for raw in (text or "").replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        head = _REVIEWER.match(line)
        if head and len(line) < 60:
            close()
            who = f"R{head.group(1)}" if head.group(1) else "ED"
            rest = line[head.end():].strip()
            if rest:
                n_by[who] = n_by.get(who, 0) + 1
                cur = {"id": f"{who}.{n_by[who]}", "revisore": who, "testo": rest}
            continue
        if not line:
            close()
            continue
        item = _ITEM.match(line)
        if item or cur is None:
            close()
            n_by[who] = n_by.get(who, 0) + 1
            cur = {"id": f"{who}.{n_by[who]}", "revisore": who, "testo": line[item.end():] if item else line}
        else:
            cur["testo"] += " " + line
    close()
    return out


def response_packet(m: ms.Manuscript, result: dict, comments: list[dict], now: datetime | None = None) -> dict:
    p = _common(m, result, RESPONSE_VERSION, RESPONSE_FORMAT, now)
    p.update({"istruzioni": RESPONSE_INSTRUCTIONS, "commenti": comments,
              "esempio": {"formato": RESPONSE_FORMAT, "versione": RESPONSE_VERSION, "impronta": "<impronta>",
                          "rivista": "<sigla>", "modello": "claude-opus-5-5",
                          "risposte": [{"commento": "R1.1", "azione": "modificato",
                                        "risposta": "Thank you. We now report how the sequence was generated.",
                                        "paragrafo": 14, "citazione": "The allocation sequence was generated by"}]}})
    return p


def check_response(packet: dict, data) -> list[Result]:
    if not isinstance(data, dict):
        return [Result("formato", False, ["la risposta deve essere un oggetto JSON"])]
    fmt = []
    if data.get("formato") != RESPONSE_FORMAT or data.get("versione") != RESPONSE_VERSION:
        fmt.append(f'"formato" deve essere {RESPONSE_FORMAT} e "versione" {RESPONSE_VERSION}')
    if len(_s(data.get("modello"))) < 3:
        fmt.append('manca "modello"')
    answers = data.get("risposte")
    if not isinstance(answers, list):
        fmt.append('"risposte" deve essere una lista')
        answers = []
    pk = []
    if data.get("impronta") != packet["impronta"]:
        pk.append("impronta diversa da quella del pacchetto (il manoscritto riveduto è cambiato?)")
    if data.get("rivista") != packet["rivista"]["sigla"]:
        pk.append("rivista diversa da quella del pacchetto")
    comments = {c["id"]: c for c in packet["commenti"]}
    paras = {p["n"]: p["testo"] for p in packet["manoscritto"]["paragrafi"]}
    full = "\n".join(paras.values())
    seen: dict[str, int] = {}
    cover, act, quotes, nums, sim = [], [], [], [], []
    for a in answers:
        if not isinstance(a, dict):
            act.append("una risposta non è un oggetto")
            continue
        cid = a.get("commento")
        if cid not in comments:
            cover.append(f"commento sconosciuto: {cid!r}")
            continue
        seen[cid] = seen.get(cid, 0) + 1
        text = _s(a.get("risposta"))
        action = a.get("azione")
        if action not in ACTIONS:
            act.append(f"{cid}: azione non valida (una di: {', '.join(ACTIONS)})")
        if not text:
            act.append(f"{cid}: manca la risposta")
        if action == "non modificato" and ms.words_of(text) < 15:
            act.append(f"{cid}: se non segui il commento spiega perché (almeno 15 parole)")
        n, q = a.get("paragrafo"), _s(a.get("citazione"))
        if action == "modificato" or n is not None or q:
            if not isinstance(n, int) or n not in paras:
                quotes.append(f"{cid}: il paragrafo {n!r} non c'è nel manoscritto riveduto")
            elif "..." in q or "…" in q or not (QUOTE_MIN <= len(cards.squash(q)) and len(q) <= QUOTE_MAX):
                quotes.append(f"{cid}: citazione di {QUOTE_MIN}-{QUOTE_MAX} caratteri, continua, senza puntini")
            elif not cards.quote_in_text(q, cards.text_forms(paras[n])):
                quotes.append(f"{cid}: la citazione non si trova nel paragrafo {n}: \"{q[:100]}\"")
        miss = _numbers_traced(text, full + "\n" + comments[cid]["testo"])
        if miss:
            nums.append(f"{cid}: numeri che non sono nel manoscritto né nel commento: {', '.join(miss)}")
        if _PREDICTION.search(text):
            sim.append(f"{cid}: togli \"{_PREDICTION.search(text).group(0)}\"")
    cover += [f"il commento {cid} non ha risposta" for cid in comments if cid not in seen]
    cover += [f"il commento {cid} ha {k} risposte: ne serve una" for cid, k in seen.items() if k > 1]
    return [Result("formato", not fmt, fmt), Result("pacchetto", not pk, pk), Result("copertura", not cover, cover),
            Result("azioni", not act, act), Result("citazione_nel_testo", not quotes, quotes),
            Result("numeri_tracciati", not nums, nums), Result("previsioni", not sim, sim)]


def response_docx(path: Path, packet: dict, data: dict, lay: dict, now: datetime) -> Path:
    B = article_docx.Block
    comments = {c["id"]: c for c in packet["commenti"]}
    paras = {p["n"]: p for p in packet["manoscritto"]["paragrafi"]}
    by_id = {a["commento"]: a for a in data["risposte"]}
    names = {"ED": "Editor"}
    blocks = [B("title", [("Response to reviewers", {})]),
              B("p", [(f"Manuscript: {packet['manoscritto']['titolo'] or ''}", {})]),
              B("p", [(f"Journal: {packet['rivista']['titolo']} · {now:%d %B %Y}", {})]),
              B("p", [("We thank the editor and the reviewers for their comments. Our answers follow each comment; the "
                       "changes are quoted from the revised manuscript.", {})])]
    current = None
    for c in packet["commenti"]:
        if c["revisore"] != current:
            current = c["revisore"]
            blocks.append(B("h1", [(names.get(current, f"Reviewer {current[1:]}"), {})]))
        a = by_id[c["id"]]
        blocks.append(B("p", [(f"Comment {c['id']}. ", {"b": True}), (comments[c["id"]]["testo"], {"i": True})]))
        blocks.append(B("p", [("Response. ", {"b": True}), (a["risposta"], {})]))
        if a.get("citazione") and a.get("paragrafo") in paras:
            where = paras[a["paragrafo"]]
            blocks.append(B("p", [(f"Change ({where['sezione']}, paragraph {a['paragrafo']}): ", {"b": True}),
                                  (f"“{a['citazione']}”", {"i": True})]))
    return article_docx.write_docx(path, blocks, {**lay, "numeri_di_riga": False}, "Response to reviewers")


# ===================================================================================================== process
def process(home: Path, kind: dict, packet: dict, lay: dict, now: datetime | None = None) -> dict:
    """If Claude wrote da_inviare/<answer>: check it; accepted -> the .docx. Counts only in the result."""
    now = now or datetime.now()
    d = folder(home, kind, packet["impronta"], packet["rivista"]["sigla"])
    src, reasons = d / TO_SEND / kind["answer"], d / TO_SEND / kind["reasons"]
    out = {"cartella": str(d), "esito": "da scrivere"}
    if not src.exists():
        if (d / kind["accepted"]).exists():
            out.update(esito="accettata", documento=str(d / kind["docx"]))
        return out
    try:
        data = json.loads(src.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        data, results = None, [Result("formato", False, [f"JSON non leggibile: {exc}"])]
    else:
        results = (check_cover if kind is COVER else check_response)(packet, data)
    out["controlli"] = {r.name: ("ok" if r.ok else "non superato") for r in results}
    if verdict(results) != "accettabile":
        reasons.write_text("\n".join(f"[{r.name}] {p}" for r in results for p in r.problems) + "\n", encoding="utf-8")
        out.update(esito="rifiutata", problemi=sum(len(r.problems) for r in results), motivi=str(reasons))
        return out
    (d / kind["accepted"]).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    src.unlink()
    if reasons.exists():
        reasons.unlink()
    (cover_docx if kind is COVER else response_docx)(d / kind["docx"], packet, data, lay, now)
    out.update(esito="accettata", documento=str(d / kind["docx"]))
    return out


def readme(kind: dict, sigla: str) -> str:
    if kind is COVER:
        what, word = "LETTERA DI PRESENTAZIONE (cover-v1)", "scrivi la lettera di presentazione"
        cmd, extra = "lettera.cmd", "Poi in Word aggiungi nome, affiliazione e firma dell'autore corrispondente."
    else:
        what, word = "RISPOSTA AI REVISORI (response-v1)", "scrivi la risposta ai revisori"
        cmd = "risposta.cmd"
        extra = ("Prima incolla in commenti_revisori.txt i commenti della lettera della rivista (una riga 'Reviewer 1', "
                 "'Reviewer 2', 'Editor' prima dei commenti di ognuno; ogni commento su una riga che inizia con il "
                 "suo numero, oppure separato da una riga vuota) e rilancia risposta.cmd.")
    return f"""{what} — rivista {sigla}

Questa cartella resta su questo PC (R10: mai su GitHub, mai in una chat esterna).
{extra}
1. In una sessione con Claude, con il PC collegato, scrivi: "{word}".
2. Rilancia {cmd} con lo stesso manoscritto e la stessa rivista: ORPHEUS controlla e scrive il file Word.
   Se non passa, i motivi sono in da_inviare.
"""


def write_cover(home: Path, packet: dict) -> Path:
    return _write(home, COVER, packet, readme(COVER, packet["rivista"]["sigla"]))


def comments_file(home: Path, fingerprint: str, sigla: str) -> Path:
    d = folder(home, RESPONSE, fingerprint, sigla)
    d.mkdir(parents=True, exist_ok=True)
    f = d / COMMENTS
    if not f.exists():
        f.write_text("", encoding="utf-8")
        (d / README).write_text(readme(RESPONSE, sigla), encoding="utf-8")
    return f


def write_response(home: Path, packet: dict) -> Path:
    return _write(home, RESPONSE, packet, readme(RESPONSE, packet["rivista"]["sigla"]))
