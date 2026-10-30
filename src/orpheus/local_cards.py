"""T-042 (ADR-0010, pilot): article cards written by a free local model (Ollama) on the PC.

The model gets the same packet Claude gets (instructions, fields, example, text by page) and must return cards-v1
values. Long texts go in pieces (whole pages), the values of the pieces are merged, values that fail the content
checks one by one are dropped, then the whole card goes through exactly the checks of `controlla-scheda`.

Nothing here touches the database: cards are written to rapporti/schede/locali (ADR-0010 point 4: they enter the
database only after the hand check). Only the Python standard library talks to Ollama (http://127.0.0.1:11434).
"""
from __future__ import annotations

import json
import os
import re
import socket
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import card_store, cards

DEFAULT_HOST = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen2.5:7b-instruct"
DEFAULT_NUM_CTX = 8192
DEFAULT_CHUNK_CHARS = 12000          # ~3k tokens of text per call, leaves room for instructions and answer
DEFAULT_MAX_TOKENS = 2048            # an answer is 1100-1700 tokens; a model stuck in a loop stops here (2026-10-16 on
                                     # the PC: 4000 tokens in 15 minutes, then the timeout lost the whole run)
DEFAULT_TIMEOUT = 600                # seconds with no data from Ollama (loading the model, reading a long piece)
DEFAULT_MAX_SECONDS = 1800           # one answer at most (a laptop without GPU writes ~2-6 tokens a second)
LOOP_TAIL = 200                      # the same last 200 characters 3 times, or 200 blanks: the model is in a loop
EXECUTOR = "local_llm"
LOCAL_DIR = "locali"
VALUE_KEYS = ("campo", "nome", "valore", "unita", "gruppo", "pagina", "sezione", "citazione", "origin", "nota")
STOPS = {"stop": None, "length": "lunghezza"}   # Ollama's done_reason -> why the answer was cut (None = complete)

Chat = Callable[[list[dict]], str]
Progress = Callable[[str], None]


class LocalModelError(RuntimeError):
    """Ollama not installed, not started or without the model: nothing can be done, the run stops."""


class AnswerFailed(RuntimeError):
    """One answer failed (no data in time, an error of Ollama): that piece is skipped, the run goes on."""


class Answer(str):
    """The text of an answer; `cut` says why it was cut short (None = complete): lunghezza, ripetizione, tempo, attesa."""
    cut: str | None = None

    def __new__(cls, text: str, cut: str | None = None):
        obj = super().__new__(cls, text)
        obj.cut = cut
        return obj


def local_dir(home: Path) -> Path:
    return card_store.reports(home) / card_store.CARDS_DIR / LOCAL_DIR


def default_host() -> str:
    """OLLAMA_HOST as Ollama reads it (e.g. "127.0.0.1:11434"), else the default."""
    h = (os.environ.get("OLLAMA_HOST") or "").strip()
    if not h or h.startswith("0.0.0.0"):
        return DEFAULT_HOST
    return h if "://" in h else f"http://{h}"


# ---------------------------------------------------------------- Ollama client (stdlib only)

def _is_timeout(exc: BaseException) -> bool:
    reason = getattr(exc, "reason", exc)
    return isinstance(reason, (TimeoutError, socket.timeout)) or "timed out" in str(reason)


def looping(text: str) -> bool:
    if len(text) - len(text.rstrip()) >= LOOP_TAIL:
        return True
    return len(text) >= 3 * LOOP_TAIL and text.count(text[-LOOP_TAIL:]) >= 3


def ollama_chat(model: str = DEFAULT_MODEL, *, host: str = DEFAULT_HOST, num_ctx: int = DEFAULT_NUM_CTX,
                max_tokens: int = DEFAULT_MAX_TOKENS, timeout: float = DEFAULT_TIMEOUT,
                max_seconds: float = DEFAULT_MAX_SECONDS, opener=urllib.request.urlopen,
                clock=time.monotonic) -> Chat:
    """A function messages -> Answer, using Ollama's /api/chat in JSON mode, temperature 0, streamed: `timeout` is the
    longest wait for the next piece of the answer, not for the whole answer. An answer that goes on too long, repeats
    itself or takes more than `max_seconds` is cut (closing the connection stops Ollama) and kept as it is: the values
    already complete are used."""
    url = host.rstrip("/") + "/api/chat"

    def chat(messages: list[dict]) -> Answer:
        body = json.dumps({"model": model, "messages": messages, "stream": True, "format": "json",
                           "options": {"temperature": 0, "num_ctx": num_ctx, "num_predict": max_tokens}}).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        start = clock()
        try:
            resp = opener(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300] if hasattr(exc, "read") else ""
            if exc.code == 404:
                raise LocalModelError(f"Modello '{model}' non trovato in Ollama: scaricalo con `ollama pull {model}`. "
                                      f"{detail}") from exc
            raise AnswerFailed(f"Ollama ha risposto {exc.code}: {detail}") from exc
        except (urllib.error.URLError, OSError) as exc:
            if _is_timeout(exc):
                raise AnswerFailed(f"nessuna risposta da Ollama in {int(timeout)} s (modello lento o PC occupato)") from exc
            raise LocalModelError(f"Ollama non risponde su {host}: è installato e avviato? ({exc})") from exc
        parts: list[str] = []
        cut = "attesa"                      # the stream ended without "done"
        try:
            with resp:
                for n, line in enumerate(resp, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise AnswerFailed("risposta di Ollama non leggibile") from exc
                    if not isinstance(data, dict):
                        raise AnswerFailed("risposta di Ollama non leggibile")
                    if data.get("error"):
                        raise AnswerFailed(f"Ollama: {str(data['error'])[:200]}")
                    msg = data.get("message")
                    if isinstance(msg, dict) and isinstance(msg.get("content"), str):
                        parts.append(msg["content"])
                    if data.get("done"):
                        cut = STOPS.get(data.get("done_reason") or "stop", "lunghezza")
                        break
                    if clock() - start > max_seconds:
                        cut = "tempo"
                        break
                    if n % 50 == 0 and looping("".join(parts)):
                        cut = "ripetizione"
                        break
        except (urllib.error.URLError, OSError) as exc:     # the wait for the next piece ran out
            if not parts:
                raise AnswerFailed(f"nessuna risposta da Ollama in {int(timeout)} s (modello lento o PC occupato)") from exc
            cut = "attesa"
        text = "".join(parts)
        if cut is None and not text.strip():
            raise AnswerFailed("Risposta di Ollama vuota")
        return Answer(text, cut)

    return chat


# ---------------------------------------------------------------- prompt and pieces

REF_HEADING = re.compile(r"^[ \t]*(?:\d{1,2}\.?[ \t]+)?(?:References|REFERENCES|Reference [Ll]ist|REFERENCE LIST|"
                         r"Bibliography|BIBLIOGRAPHY|Literature [Cc]ited|LITERATURE CITED|Works [Cc]ited)\b", re.M)
YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}[a-z]?(?!\d)")
AUTHOR = re.compile(r"\b[A-Z][a-zA-Z'\-]+,? (?:[A-Z]\.?){1,3}[,.]")      # "Siem D," "Smith JM." "Chapman, P."
CAPTION = re.compile(r"^[ \t]*(?:table|fig\.?|figure)[ \t]*\d+", re.I | re.M)
# per 1000 characters, measured on 20 packets of the PC (2026-10-16): reference pages 3-9 years and 4-21 authors,
# body pages 0-2 authors (a review with author-year citations: up to 10 years but 0-3 authors)
REF_DENSITY = (3, 3)
REF_DENSITY_NO_HEADING = (5, 8)


def _reference_like(text: str, density: tuple[int, int] = REF_DENSITY) -> bool:
    """A piece of text that is only a reference list: many years and author names, no table or figure caption."""
    t = text.strip()
    if len(t) < 200 or CAPTION.search(t):
        return False
    k = 1000 / len(t)
    return len(YEAR.findall(t)) * k >= density[0] and len(AUTHOR.findall(t)) * k >= density[1]


def pages_for_model(packet: dict) -> tuple[list[dict], int]:
    """The pages the model reads: the reference list is left out (no card value is there, and on a PC without GPU it
    costs minutes). From the heading "References" (searched from the first third of the pages on): the rest of that
    page if it looks like references, then the following pages while they look like references; a page with a table
    or figure caption (tables after or among the references) and everything after it is kept. Without a heading, only
    the last pages that clearly look like references. The values are still checked against the whole pages of the
    packet. Returns (pages, pages shortened or left out)."""
    pages = [dict(p) for p in packet["testo"]["pagine"]]
    first = len(pages) // 3
    heading = None
    for i in range(first, len(pages)):
        text = pages[i]["testo"]
        for m in REF_HEADING.finditer(text):
            tail = text[m.end():]
            heading = i
            if len(tail.strip()) < 200 or _reference_like(tail):
                pages[i] = {**pages[i], "testo": text[:m.start()], "_cut": True}
            break
        if heading is not None:
            break
    if heading is not None:
        j = heading + 1
        while j < len(pages) and _reference_like(pages[j]["testo"]):
            pages[j] = {**pages[j], "testo": "", "_cut": True}
            j += 1
    else:
        j = len(pages) - 1
        while j >= max(first, 1) and _reference_like(pages[j]["testo"], REF_DENSITY_NO_HEADING):
            pages[j] = {**pages[j], "testo": "", "_cut": True}
            j -= 1
    cut = sum(1 for p in pages if p.pop("_cut", False))
    return [p for p in pages if p["testo"].strip()], cut


def pieces(packet: dict, max_chars: int = DEFAULT_CHUNK_CHARS, pages: list[dict] | None = None) -> list[list[dict]]:
    """Whole pages grouped so that each piece stays under max_chars (a longer page is a piece on its own)."""
    out: list[list[dict]] = []
    cur: list[dict] = []
    size = 0
    for p in packet["testo"]["pagine"] if pages is None else pages:
        n = len(p["testo"])
        if cur and size + n > max_chars:
            out.append(cur)
            cur, size = [], 0
        cur.append(p)
        size += n
    if cur:
        out.append(cur)
    return out


# The same for every call (system message first: Ollama reuses it without reading it again). Short answers: on a PC
# without GPU the model writes ~6 tokens a second and writing is ~85% of the time (2026-10-16), so only the keys that
# carry something, the shortest exact quote, and no rows for the missing fields (merge() adds them).
SYSTEM = """Sei un estrattore di dati da articoli scientifici (scheda cards-v1). Rispondi SOLO con un oggetto JSON
{"valori": [ ... ]} con i valori che trovi nelle pagine del testo. Se in queste pagine un campo non c'è, NON scriverlo
(i campi mancanti li aggiungo io). Ogni elemento ha:
- "campo": uno dei campi qui sotto;
- "valore": breve, nella lingua dell'articolo; ogni numero come è scritto nel testo (stesso segno e decimali: "-0.81",
  ".01"); niente numeri calcolati da te;
- "pagina": il numero tra [PAGINA n] (mai "sezione");
- "citazione": il pezzo PIÙ CORTO di testo, copiato ESATTAMENTE e di seguito da quella pagina, che contiene tutti i numeri
  del valore e l'unità (almeno 12 caratteri, di solito meno di 200); mai puntini né parafrasi; nelle tabelle il testo è
  spezzato su più righe: copia le parole nell'ordine in cui sono nella pagina;
- solo se servono: "nome" (nome della misura, obbligatorio per misura_principale), "unita" (scritta nella citazione;
  obbligatoria per l'età, mai per partecipanti_numero), "gruppo" (gruppo, sesso o condizione, se non è tutto il
  campione), "origin": "interpreted" (solo se il valore è dedotto dalla citazione, es. il disegno dello studio non
  nominato);
- nessun'altra chiave e nessuna chiave vuota.
"""
EXAMPLE_ANSWER = {"valori": [
    {"campo": "partecipanti_numero", "valore": "15", "pagina": 2, "citazione": "Fifteen children (9 male, 6 female)"},
    {"campo": "partecipanti_eta", "valore": "9.95 (1.35)", "unita": "years", "pagina": 3,
     "citazione": "Age (years) 9.95 (1.35)"},
    {"campo": "misura_principale", "nome": "stride length", "valore": "2.39 (0.32)", "unita": "m", "pagina": 3,
     "citazione": "Stride length (m) 2.39 (0.32)"},
]}


def system_message(packet: dict) -> str:
    return (SYSTEM + "\nCampi della scheda:\n" + json.dumps(packet.get("campi") or {}, ensure_ascii=False)
            + "\n\nEsempio di risposta:\n" + json.dumps(EXAMPLE_ANSWER, ensure_ascii=False))


def messages_for(packet: dict, piece: list[dict], index: int, total: int) -> list[dict]:
    art = packet.get("articolo", {})
    pages = "\n\n".join(f"[PAGINA {p['pagina']}]\n{p['testo']}" for p in piece)
    user = (f"Articolo: {art.get('titolo')} ({art.get('rivista')}, {art.get('anno')}). Pezzo {index} di {total} del "
            f"testo.\n\nTesto:\n{pages}\n\n"
            'Rispondi con {"valori": [...]} per i valori che trovi in queste pagine.')
    return [{"role": "system", "content": system_message(packet)}, {"role": "user", "content": user}]


# ---------------------------------------------------------------- answer -> values

def _clean_value(raw) -> dict | None:
    if not isinstance(raw, dict) or raw.get("campo") not in cards.FIELDS:
        return None
    v = {k: raw.get(k) for k in VALUE_KEYS if raw.get(k) not in (None, "")}
    if v.get("origin") not in cards.ORIGINS:
        v["origin"] = "reported"
    for k in ("nome", "valore", "unita", "gruppo", "citazione", "nota"):
        if k in v and not isinstance(v[k], str):
            v[k] = str(v[k])
    for k in ("pagina", "sezione"):
        if k in v:
            try:
                v[k] = int(v[k])
            except (TypeError, ValueError):
                del v[k]
    if "pagina" in v:
        v.pop("sezione", None)
    return v


_SEP = re.compile(r"\s*,?\s*")


def _salvage(text: str) -> list:
    """The complete objects of a JSON answer cut short ({"valori": [{...}, {...}, {"campo": "x", "val...)."""
    key = text.find('"valori"')
    start = text.find("[", key if key >= 0 else 0)
    if start < 0:
        return []
    dec, out, pos = json.JSONDecoder(), [], start + 1
    while True:
        pos = _SEP.match(text, pos).end()
        if pos >= len(text) or text[pos] != "{":
            return out
        try:
            obj, pos = dec.raw_decode(text, pos)
        except json.JSONDecodeError:
            return out
        out.append(obj)


def parse_answer(text: str) -> list[dict]:
    """The model's JSON -> list of cleaned values (unknown keys and fields dropped). An answer cut short keeps its
    complete values; anything else that is not JSON -> []."""
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        data = {"valori": _salvage(text)} if isinstance(text, str) else None
    raw = data.get("valori") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        return []
    return [v for v in (_clean_value(r) for r in raw) if v]


def _single_ok(packet: dict, value: dict, pages: dict[int, str], sections: dict[int, str]) -> bool:
    data = {"formato": cards.CARD_FORMAT, "versione": cards.CARD_VERSION, "workflow_run_id": packet["workflow_run_id"],
            "article_id": packet["article_id"], "modello": "check", "valori": [value]}
    card, fmt = cards.parse(data)
    if card is None:
        return False
    checks = [cards.check_positions(card, pages, sections), cards.check_quotes(card, pages, sections),
              cards.check_numbers(card), cards.check_units(card), cards.check_plausible(card)]
    if value["origin"] != "not_available":
        if not value.get("valore") or not value.get("citazione"):
            return False
        if value["campo"] == "misura_principale" and not value.get("nome"):
            return False
    return all(c.ok for c in checks)


def merge(packet: dict, values: list[dict]) -> tuple[list[dict], int]:
    """Drop values that fail the checks one by one and duplicates; one 'not_available' row per missing field."""
    pages = {p["pagina"]: p["testo"] for p in packet["testo"]["pagine"]}
    sections = {s["sezione"]: s["testo"] for s in packet["testo"]["sezioni"]}
    kept: list[dict] = []
    seen: set[tuple] = set()
    dropped = 0
    for v in values:
        if v["origin"] == "not_available":
            continue
        key = (v["campo"], v.get("nome"), v.get("valore"), v.get("gruppo"), v.get("pagina"), v.get("citazione"))
        if key in seen:
            continue
        seen.add(key)
        if _single_ok(packet, v, pages, sections):
            kept.append(v)
        else:
            dropped += 1
    kept = kept[:190]
    order = {name: i for i, name in enumerate(cards.FIELDS)}
    kept.sort(key=lambda v: order[v["campo"]])
    present = {v["campo"] for v in kept}
    for name in cards.FIELDS:
        if name not in present:
            kept.append({"campo": name, "origin": "not_available"})
    kept.sort(key=lambda v: order[v["campo"]])
    return kept, dropped


# ---------------------------------------------------------------- one card, many cards

def build_card(packet: dict, chat: Chat, model: str, *, chunk_chars: int = DEFAULT_CHUNK_CHARS,
               clock=time.monotonic, progress: Progress | None = None) -> tuple[dict | None, dict]:
    """(card, info). A piece whose answer fails is skipped; if every piece fails, card is None (the packet stays
    open and is tried again next time)."""
    start = clock()
    readable, ref_pages = pages_for_model(packet)
    parts = pieces(packet, chunk_chars, readable)
    raw: list[dict] = []
    empty = 0
    failed: list[str] = []
    cut: list[str] = []
    for i, piece in enumerate(parts, 1):
        if progress:
            progress(f"pezzo {i} di {len(parts)}")
        try:
            answer = chat(messages_for(packet, piece, i, len(parts)))
        except AnswerFailed as exc:
            failed.append(f"pezzo {i}: {exc}")
            continue
        if getattr(answer, "cut", None):
            cut.append(f"pezzo {i}: {answer.cut}")
        found = parse_answer(answer)
        empty += not found
        raw.extend(found)
    timing = {"pezzi": len(parts), "pagine_bibliografia_saltate": ref_pages, "pezzi_falliti": len(failed),
              "pezzi_tagliati": len(cut),
              "motivi_pezzi": failed + cut, "secondi": round(clock() - start, 1)}
    if parts and len(failed) == len(parts):
        return None, {"article_id": packet["article_id"], "executor": EXECUTOR, "modello": model,
                      "esito": "non riuscita", **timing}
    valori, dropped = merge(packet, raw)
    card = {"formato": cards.CARD_FORMAT, "versione": cards.CARD_VERSION, "workflow_run_id": packet["workflow_run_id"],
            "article_id": packet["article_id"], "modello": model[:80] if len(model) >= 3 else f"{model}-local",
            "valori": valori}
    outcome = card_store.check_with_packet(card, packet)
    info = {"article_id": packet["article_id"], "executor": EXECUTOR, "modello": model,
            "esito": "accettabile" if outcome.accepted else "da correggere",
            "pezzi_senza_valori": empty, "valori_proposti": len(raw), "valori_scartati": dropped,
            "valori_con_citazione": sum(v["origin"] != "not_available" for v in valori),
            "campi_mancanti": [v["campo"] for v in valori if v["origin"] == "not_available"],
            "problemi": outcome.problems()[:20], **timing}
    return card, info


def pending_packets(home: Path) -> list[Path]:
    """Open packets with no card yet (in da_inviare, accettate or locali)."""
    pdir = card_store.packets_dir(home)
    if not pdir.exists():
        return []
    done = set()
    for d in (card_store.to_send_dir(home), card_store.accepted_dir(home), local_dir(home)):
        if d.exists():
            done |= {p.stem for p in d.glob("*.json")}
    return [p for p in sorted(pdir.glob("art_*.json")) if p.stem not in done]


def run(home: Path, chat: Chat, model: str, *, how_many: int = 10, chunk_chars: int = DEFAULT_CHUNK_CHARS,
        now: datetime | None = None, progress: Progress | None = None, clock=time.monotonic) -> dict:
    """Up to `how_many` open packets without a card, one after the other; each card is written as soon as it is
    done (closing the window loses only the article in progress)."""
    out_dir = local_dir(home)
    out_dir.mkdir(parents=True, exist_ok=True)
    todo = pending_packets(home)[:max(0, how_many)]
    results = []
    for k, path in enumerate(todo, 1):
        try:
            packet = json.loads(path.read_bytes().decode("utf-8-sig"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            results.append({"article_id": path.stem, "esito": "pacchetto non leggibile", "problemi": [str(exc)]})
            continue
        say = (lambda msg, k=k: progress(f"articolo {k} di {len(todo)}: {msg}")) if progress else None
        card, info = build_card(packet, chat, model, chunk_chars=chunk_chars, progress=say, clock=clock)
        if card is not None:
            (out_dir / f"{packet['article_id']}.json").write_text(json.dumps(card, ensure_ascii=False, indent=2),
                                                                  encoding="utf-8")
            (out_dir / f"{packet['article_id']}.info.json").write_text(json.dumps(info, ensure_ascii=False, indent=2),
                                                                       encoding="utf-8")
        results.append(info)
        if say:
            say(f"{info['esito']} ({round(info.get('secondi', 0) / 60)} min)")
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d_%H%M%S")
    summary = {"modello": model, "executor": EXECUTOR, "cartella": str(out_dir), "pacchetti_fatti": len(results),
               "accettabili": sum(r.get("esito") == "accettabile" for r in results),
               "non_riuscite": sum(r.get("esito") == "non riuscita" for r in results),
               "secondi_totali": round(sum(r.get("secondi", 0) for r in results), 1), "schede": results}
    if results:
        (out_dir / f"riepilogo_{stamp}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                                         encoding="utf-8")
    return summary
