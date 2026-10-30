"""T-043 (ADR-0011): article cards written by a free chat used by hand by the user (executor chat_manuale).

No code here talks to any service. ORPHEUS prepares, for one open packet, the messages to paste into a free chat
(ChatGPT without a paid subscription, or NotebookLM with the PDF or the text file as source): the cards-v1 rules,
the fields, an example and the text by page and section. The user pastes the chat's answer into `risposta.txt` in the
same folder; ORPHEUS reads it, checks it with exactly the validators of `submit` / `controlla-scheda`, and writes the
outcome: accepted (scheda.json) or rejected with the reasons (and a message to paste back into the chat).

Everything stays on the PC in rapporti/schede/chat/<article_id>/ and the database is not touched (ADR-0011 point 3 and
6, like ADR-0010): accepted cards enter the database only after the hand check. Draft manuscripts never go here.

2026-10-17 19:13 (user's decision): the free chat is the emergency route (few articles, or when Claude cannot work);
Claude in session stays the main one. `send` puts the accepted cards into the database with the same checks as
`submit` (on the text of the database), recording executor chat_manuale and the chat declared by the user. Before the
checks, `tidy` fixes only the form of the answer (empty strings, numbers written as text, spaces, capital letters in
"origin"): values and quotes are never touched.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from . import card_store, cards, local_cards

EXECUTOR = "chat_manuale"
CHAT_DIR = "chat"
DEFAULT_PART_CHARS = 20000          # one message of a free chat (text of whole pages; a longer page is a part alone)
ANSWER = "risposta.txt"
STATE = "stato.json"
OUTCOME = "esito.json"
CARD = "scheda.json"
FIX = "correzione.txt"
README = "LEGGIMI.txt"
FULL_TEXT = "testo_completo.txt"
MODEL_FILE = "modello.txt"          # in rapporti/schede/chat: the chat last declared by the user
SENT = "inviata.json"               # the card entered the database (orpheus chat-schede --invia)
CHATS = ("ChatGPT gratuito", "Perplexity gratuito", "NotebookLM", "Gemini gratuito")   # menu of chat_schede.cmd


def chat_dir(home: Path) -> Path:
    return card_store.reports(home) / card_store.CARDS_DIR / CHAT_DIR


def _now(now: datetime | None) -> datetime:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc)


def _iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- what is pasted into the chat

RULES = card_store.INSTRUCTIONS.split("Regole", 1)[1].replace(
    '8. Copia "workflow_run_id" e "article_id" da questo pacchetto; "modello": il modello che compila (es. claude-opus-5-5).',
    '8. Non servono "workflow_run_id", "article_id" e "modello": li aggiunge ORPHEUS.')

HEADER = """Sei un estrattore di dati da un articolo scientifico. Compila la scheda dell'articolo (contratto cards-v1).
Un programma controlla ogni valore: la citazione deve essere copiata ESATTAMENTE dal testo della pagina indicata e
ogni numero del valore deve comparire nella citazione. Un valore inventato, calcolato o parafrasato fa rifiutare tutta
la scheda.

Regole{rules}
Campi (tutti devono comparire almeno una volta; "obbligatorio" = serve un valore con citazione):
{fields}

Esempio di risposta (solo la forma; i valori sono di un altro articolo):
{example}

Forma della risposta (un programma la legge):
- un oggetto JSON {{"valori": [ ... ]}} in un unico blocco di codice, senza altro testo;
- in ogni riga solo questi nomi: campo, nome, valore, unita, gruppo, pagina, sezione, citazione, origin, nota;
- "origin" solo "reported", "interpreted" o "not_available";
- "pagina" e "sezione" sono numeri interi (mai tutte e due); un campo che non c'è si omette oppure vale null, mai "".

Rispondi SOLO con un oggetto JSON {{"valori": [ ... ]}}, senza altro testo, in un unico blocco di codice."""


def _fields_text() -> str:
    return "\n".join(f"- {k}{' (obbligatorio)' if v['obbligatorio'] else ''}: {v['descrizione']}"
                     for k, v in cards.FIELDS.items())


def _example_text() -> str:
    return json.dumps({"valori": card_store.EXAMPLE["valori"]}, ensure_ascii=False, indent=1)


def header_text() -> str:
    return HEADER.format(rules=RULES.rstrip(), fields=_fields_text(), example=_example_text())


def _blocks(packet: dict) -> list[str]:
    """The text the chat reads: pages (references left out, as for the local model) and, if any, the sections of
    the open full text. The values are still checked against the whole pages of the packet."""
    pages, _cut = local_cards.pages_for_model(packet)
    out = [f"[PAGINA {p['pagina']}]\n{p['testo']}" for p in pages if p["testo"].strip()]
    for s in packet["testo"].get("sezioni", []):
        title = f": {s['titolo']}" if s.get("titolo") else ""
        out.append(f"[SEZIONE {s['sezione']}{title}]\n{s['testo']}")
    return out


def parts_for(packet: dict, max_chars: int = DEFAULT_PART_CHARS) -> list[str]:
    """The messages to paste, in order. The first has the rules; the last asks for the answer; the ones in between
    ask only for "OK" (a free chat takes long texts in more messages)."""
    groups: list[list[str]] = []
    cur: list[str] = []
    size = 0
    for b in _blocks(packet):
        if cur and size + len(b) > max_chars:
            groups.append(cur)
            cur, size = [], 0
        cur.append(b)
        size += len(b)
    if cur or not groups:
        groups.append(cur)
    art = packet.get("articolo", {})
    title = f"Articolo: {art.get('titolo')} ({art.get('rivista')}, {art.get('anno')})."
    n = len(groups)
    out = []
    for i, g in enumerate(groups, 1):
        text = "\n\n".join(g)
        if i == 1:
            intro = header_text() + f"\n\n{title}\n"
            intro += (f"Il testo arriva in {n} messaggi. Rispondi solo \"OK\" finché non ricevi il messaggio {n} di {n}.\n"
                      if n > 1 else "")
        else:
            intro = f"Messaggio {i} di {n} (stesso articolo)."
        tail = ("\n\nOra compila la scheda con tutto il testo ricevuto. Rispondi SOLO con il JSON {\"valori\": [...]}."
                if i == n else f"\n\nFine del messaggio {i} di {n}: rispondi solo \"OK\".")
        prefix = f"Messaggio {i} di {n}.\n" if n > 1 and i == 1 else ""
        out.append(f"{prefix}{intro}\n\nTesto:\n{text}{tail}")
    return out


def readme_text(article_id: str, n_parts: int) -> str:
    files = "parte_01.txt" if n_parts == 1 else f"parte_01.txt … parte_{n_parts:02d}.txt"
    return f"""SCHEDA CON UNA CHAT GRATUITA (ADR-0011) — articolo {article_id}

Questi file restano su questo PC. Non metterli su GitHub né inviarli ad altri.
Usa solo un piano gratuito (niente carta, niente abbonamento): se la chat chiede di pagare, fermati.

1. Apri una chat NUOVA (ChatGPT gratuito, oppure NotebookLM).
   - ChatGPT: incolla {files} uno alla volta, in ordine; aspetta "OK" dopo ognuno tranne l'ultimo.
   - NotebookLM: aggiungi come fonte testo_completo.txt (non il PDF: le citazioni devono essere uguali a questo testo),
     poi incolla nella chat solo parte_01.txt e scrivi "Il testo è nella fonte".
2. Copia TUTTA la risposta della chat (il blocco JSON) e incollala in risposta.txt; salva.
3. Doppio clic su chat_schede.cmd: ORPHEUS controlla la risposta e scrive esito.json.
   - Accettata: la scheda è in scheda.json (non entra nel database finché non c'è il controllo a mano).
   - Rifiutata: in correzione.txt c'è il messaggio da incollare nella STESSA chat; incolla la nuova risposta
     in risposta.txt (sostituendo quella vecchia) e rilancia chat_schede.cmd.
"""


def full_text(packet: dict) -> str:
    return "\n\n".join(_blocks(packet))


# ---------------------------------------------------------------- prepare

def _done_ids(home: Path) -> set[str]:
    done = set()
    for d in (card_store.to_send_dir(home), card_store.accepted_dir(home), local_cards.local_dir(home)):
        if d.exists():
            done |= {p.stem for p in d.glob("art_*.json")}
    return done


def prepared(home: Path) -> list[Path]:
    root = chat_dir(home)
    return sorted(p for p in root.glob("art_*") if (p / STATE).exists()) if root.exists() else []


def pending_packets(home: Path) -> list[Path]:
    """Open packets with no card (da_inviare, accettate, locali) and not yet prepared for the chat."""
    pdir = card_store.packets_dir(home)
    if not pdir.exists():
        return []
    skip = _done_ids(home) | {p.name for p in prepared(home)}
    return [p for p in sorted(pdir.glob("art_*.json")) if p.stem not in skip]


def prepare(home: Path, *, how_many: int = 1, part_chars: int = DEFAULT_PART_CHARS,
            now: datetime | None = None) -> dict:
    t = _now(now)
    out = []
    for path in pending_packets(home)[:max(0, how_many)]:
        try:
            packet = json.loads(path.read_bytes().decode("utf-8-sig"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            out.append({"article_id": path.stem, "esito": "pacchetto non leggibile", "problemi": [str(exc)]})
            continue
        folder = chat_dir(home) / packet["article_id"]
        folder.mkdir(parents=True, exist_ok=True)
        parts = parts_for(packet, part_chars)
        for i, text in enumerate(parts, 1):
            (folder / f"parte_{i:02d}.txt").write_text(text, encoding="utf-8")
        (folder / FULL_TEXT).write_text(full_text(packet), encoding="utf-8")
        (folder / README).write_text(readme_text(packet["article_id"], len(parts)), encoding="utf-8")
        answer = folder / ANSWER
        if not answer.exists():
            answer.write_text("", encoding="utf-8")
        state = {"article_id": packet["article_id"], "workflow_run_id": packet["workflow_run_id"],
                 "executor": EXECUTOR, "pacchetto": str(path), "preparato_il": _iso(t), "parti": len(parts),
                 "caratteri": sum(len(p) for p in parts)}
        (folder / STATE).write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        out.append({"article_id": packet["article_id"], "esito": "preparato", "parti": len(parts),
                    "cartella": str(folder)})
    return {"executor": EXECUTOR, "cartella": str(chat_dir(home)), "preparati": out,
            "ancora_da_preparare": len(pending_packets(home))}


# ---------------------------------------------------------------- read the pasted answer

_FENCE = re.compile(r"```(?:json|JSON)?\s*\n(.*?)```", re.S)


def extract_json(text: str):
    """The JSON of a chat answer: inside ```json fences or bare, with any text before or after. None if absent."""
    text = (text or "").lstrip("﻿")
    candidates = [m.group(1) for m in _FENCE.finditer(text)] + [text]
    dec = json.JSONDecoder()
    for c in candidates:
        c = c.strip()
        try:
            return json.loads(c)
        except json.JSONDecodeError:
            pass
        for start in (i for i, ch in enumerate(c) if ch in "{["):
            try:
                obj, _ = dec.raw_decode(c, start)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and "valori" in obj or isinstance(obj, list):
                return obj
    return None


_OPTIONAL_TEXT = ("nome", "valore", "unita", "gruppo", "citazione", "nota")
_ORIGIN_ALIASES = {"riportato": "reported", "report": "reported", "interpretato": "interpreted",
                   "non disponibile": "not_available", "not available": "not_available", "n/a": "not_available",
                   "na": "not_available"}


def tidy(values: list) -> tuple[list, int]:
    """Only the form of a free chat's answer (user's report of 2026-10-17: ChatGPT refused again and again for the
    format): "" -> missing, "3" -> 3 for pagina and sezione, spaces around texts, "Reported" -> "reported". Values,
    quotes, units and groups are never changed in substance: the checks of submit still decide. Returns (values,
    number of fixes)."""
    fixed, out = 0, []
    for v in values:
        if not isinstance(v, dict):
            out.append(v)
            continue
        w = dict(v)
        for k in ("pagina", "sezione"):
            x = w.get(k)
            if isinstance(x, str):
                x2 = x.strip()
                if x2 == "":
                    w.pop(k)
                    fixed += 1
                elif re.fullmatch(r"\d+", x2):
                    w[k] = int(x2)
                    fixed += 1
        for k in _OPTIONAL_TEXT:
            x = w.get(k)
            if isinstance(x, str) and x != x.strip():
                w[k] = x.strip()
                fixed += 1
            if isinstance(w.get(k), str) and w[k] == "":
                w.pop(k)
                fixed += 1
        o = w.get("origin")
        if isinstance(o, str):
            o2 = _ORIGIN_ALIASES.get(o.strip().lower(), o.strip().lower())
            if o2 != o:
                w["origin"] = o2
                fixed += 1
        if isinstance(w.get("campo"), str) and w["campo"] != w["campo"].strip():
            w["campo"] = w["campo"].strip()
            fixed += 1
        out.append(w)
    return out, fixed


def card_from_answer(text: str, packet: dict, model: str) -> tuple[dict | None, list[str]]:
    data = extract_json(text)
    if data is None:
        return None, ["nella risposta non c'è un JSON leggibile: incolla tutto il blocco di codice della chat"]
    values = data.get("valori") if isinstance(data, dict) else data
    if not isinstance(values, list):
        return None, ['il JSON non ha la lista "valori"']
    values, _fixed = tidy(values)
    card = {"formato": cards.CARD_FORMAT, "versione": cards.CARD_VERSION, "workflow_run_id": packet["workflow_run_id"],
            "article_id": packet["article_id"], "modello": model, "valori": values}
    return card, []


def fix_message(problems: list[str]) -> str:
    lines = "\n".join(f"- {p}" for p in problems[:40])
    more = f"\n- … e altri {len(problems) - 40} problemi" if len(problems) > 40 else ""
    return ("Il controllo automatico ha rifiutato la scheda per questi motivi:\n" + lines + more +
            "\n\nCorreggi SOLO questi punti, con citazioni copiate esattamente dal testo della pagina indicata "
            "(niente numeri calcolati, niente parafrasi). Rispondi di nuovo con il JSON completo {\"valori\": [...]}.")


def read_answers(home: Path, model: str, *, now: datetime | None = None, article_id: str | None = None) -> dict:
    """Every prepared folder (or only `article_id`) with a non-empty risposta.txt newer than its outcome: check and
    write the outcome."""
    model = (model or "").strip()[:80]
    t = _now(now)
    results = []
    for folder in prepared(home):
        if article_id and folder.name != article_id:
            continue
        answer = folder / ANSWER
        if not answer.exists() or not answer.read_text(encoding="utf-8-sig").strip():
            continue
        outcome_file = folder / OUTCOME
        if outcome_file.exists():
            old = json.loads(outcome_file.read_text(encoding="utf-8"))
            if old.get("sha_risposta") == answer_sha(answer):
                continue
        if len(model) < 3:
            results.append({"article_id": folder.name, "esito": "non letta",
                            "problemi": ["manca il nome della chat usata (es. \"ChatGPT gratuito\")"]})
            continue
        state = json.loads((folder / STATE).read_text(encoding="utf-8"))
        packet = json.loads(Path(state["pacchetto"]).read_bytes().decode("utf-8-sig"))
        card, problems = card_from_answer(answer.read_text(encoding="utf-8-sig"), packet, model)
        checks = {}
        if card is not None:
            res = card_store.check_with_packet(card, packet)
            checks = {c.name: ("ok" if c.ok else "non superato") for c in res.checks}
            problems = res.problems()
            accepted = res.accepted
        else:
            accepted = False
        prepared_at = datetime.strptime(state["preparato_il"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        pasted_at = datetime.fromtimestamp(answer.stat().st_mtime, timezone.utc)
        tries = (json.loads(outcome_file.read_text(encoding="utf-8")).get("tentativo", 0) if outcome_file.exists()
                 else 0) + 1
        info = {"article_id": folder.name, "workflow_run_id": packet["workflow_run_id"], "executor": EXECUTOR,
                "modello": model, "esito": "accettata" if accepted else "rifiutata", "tentativo": tries,
                "controlli": checks, "problemi": problems[:60],
                "valori": len(card["valori"]) if card else 0,
                "valori_con_citazione": sum(isinstance(v, dict) and v.get("origin") != "not_available"
                                            for v in card["valori"]) if card else 0,
                "minuti": max(0, round((pasted_at - prepared_at).total_seconds() / 60)),
                "letta_il": _iso(t), "sha_risposta": answer_sha(answer)}
        if accepted:
            (folder / CARD).write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
            if (folder / FIX).exists():
                (folder / FIX).unlink()
        else:
            if (folder / CARD).exists():
                (folder / CARD).unlink()
            (folder / FIX).write_text(fix_message(problems), encoding="utf-8")
        outcome_file.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        results.append(info)
    if model and len(model) >= 3:
        root = chat_dir(home)
        root.mkdir(parents=True, exist_ok=True)
        (root / MODEL_FILE).write_text(model, encoding="utf-8")
    summary = {"executor": EXECUTOR, "modello": model, "cartella": str(chat_dir(home)), "lette": len(results),
               "accettate": sum(r["esito"] == "accettata" for r in results),
               "rifiutate": sum(r["esito"] == "rifiutata" for r in results), "schede": results}
    if results:
        stamp = t.strftime("%Y-%m-%d_%H%M%S")
        (chat_dir(home) / f"riepilogo_{stamp}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                                                encoding="utf-8")
    return summary


def _parts(folder: Path) -> int:
    return len(list(folder.glob("parte_*.txt")))


def answer_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------- into the database (orpheus chat-schede --invia)

SEND_REFUSED = "invio_rifiutato.txt"


def ready_to_send(home: Path) -> list[Path]:
    """Folders with an accepted card (scheda.json) not yet sent. The answer that made it may be newer only if it was
    accepted too (read_answers removes scheda.json when a new answer is refused)."""
    out = []
    for folder in prepared(home):
        if (folder / CARD).exists() and not (folder / SENT).exists():
            out.append(folder)
    return out


def mark_sent(home: Path, folder: Path, summary: dict, *, now: datetime | None = None) -> None:
    """After the database took the card (or already had one for this packet): inviata.json, a copy in
    schede/accettate (so no other route prepares the article again) and no old refusal."""
    info = {k: summary.get(k) for k in ("article_id", "workflow_run_id", "esito", "id_scheda", "valori")}
    info |= {"executor": EXECUTOR, "inviata_il": _iso(_now(now))}
    (folder / SENT).write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    dest_dir = card_store.accepted_dir(home)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{folder.name}.json"
    if not dest.exists():
        dest.write_bytes((folder / CARD).read_bytes())
    if (folder / SEND_REFUSED).exists():
        (folder / SEND_REFUSED).unlink()


def mark_refused(folder: Path, summary: dict, stop: str | None = None) -> None:
    lines = [f"ORPHEUS - invio rifiutato - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", ""]
    if stop:
        lines.append(f"- {stop}")
    lines += [f"- {m}" for m in summary.get("motivi", [])]
    (folder / SEND_REFUSED).write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")


def status(home: Path) -> dict:
    """Counts for the window and the log (no text of the articles)."""
    rows = []
    for folder in prepared(home):
        o = folder / OUTCOME
        answer = folder / ANSWER
        filled = answer.exists() and bool(answer.read_text(encoding="utf-8-sig").strip())
        old = json.loads(o.read_text(encoding="utf-8")) if o.exists() else {}
        esito = old.get("esito") if filled and old.get("sha_risposta") == answer_sha(answer) else None
        if (folder / SENT).exists():
            esito = "inviata"
        elif (folder / CARD).exists() and not esito:
            esito = "accettata"
        rows.append({"article_id": folder.name, "esito": esito or ("risposta da leggere" if filled else
                                                                   "aspetta la risposta"),
                     "modello": old.get("modello"), "tentativo": old.get("tentativo"),
                     "parti": _parts(folder)})
    last = chat_dir(home) / MODEL_FILE
    return {"preparati": len(rows), "accettate": sum(r["esito"] == "accettata" for r in rows),
            "rifiutate": sum(r["esito"] == "rifiutata" for r in rows),
            "da_inviare": sum(r["esito"] == "accettata" for r in rows),
            "inviate": sum(r["esito"] == "inviata" for r in rows),
            "da_leggere": sum(r["esito"] == "risposta da leggere" for r in rows),
            "in_attesa": sum(r["esito"] == "aspetta la risposta" for r in rows),
            "ancora_da_preparare": len(pending_packets(home)),
            "ultimo_modello": last.read_text(encoding="utf-8-sig").strip() if last.exists() else "",
            "cartella": str(chat_dir(home)), "articoli": rows}
