"""T-037 — Fase 6 (B1): a manuscript checked against the current card of a journal (compliance-v1, agent F4).

Read-only on the database (journal_card, journal_card_value, journal_policy_version). The rules are the values of the
journal card checked by hand by the user (T-027, 320/320): every result names the rule, the page of the author
guidelines and the date of the card. Outcomes:

    ok      conforme             the manuscript follows the rule
    ko      da correggere        it does not (a number over the limit, a required part missing)
    guarda  da guardare          a program cannot decide (or the journal asks for it only in some cases): a reminder
    na      non indicato         the journal does not give the rule for this type of article

Compatibility with the rules of a journal is not a probability of acceptance (REQUIREMENTS).
Nothing here writes the text of the manuscript anywhere: `summary` (what goes to the log) has numbers and outcomes only.
"""
from __future__ import annotations

import html
import json
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from . import journal_cards, manuscript as ms, policy_versions
from .manuscript import Manuscript
from .quality import REPORTS_DIR

COMPLIANCE_VERSION = "compliance-v1"
KINDS = {"originale": "studio originale", "revisione": "revisione sistematica o meta-analisi",
         "protocollo": "protocollo di studio o di revisione"}
KIND_PATTERNS = {"originale": re.compile(r"original|investigation|research article", re.I),
                 "revisione": re.compile(r"review(?!.*protocol)", re.I),
                 "protocollo": re.compile(r"protocol", re.I)}


def protocol_of(m: Manuscript) -> str:
    """What a protocol is for: 'revisione' (a review protocol, PRISMA-P) or 'originale' (a study protocol, SPIRIT)."""
    return "revisione" if ms.REVIEW_WORDS.search(f"{m.title or ''}\n{m.abstract_text}") else "originale"
OUTCOMES = {"ko": "da correggere", "guarda": "da guardare", "ok": "conforme", "na": "non indicato dalla rivista"}
ORDER = ("ko", "guarda", "ok", "na")
GROUPS = ("Il file", "Lunghezza", "Abstract e parole chiave", "Struttura del testo", "Figure e tabelle",
          "Riferimenti e citazioni", "Numeri e statistiche", "Checklist di reporting", "Revisione sistematica",
          "Dichiarazioni", "Formato della pagina", "Altre regole della rivista",
          "Confronto con gli articoli della rivista")
REPORTS = "manoscritti"
CORE_SECTIONS = ("introduction", "methods", "results", "discussion")


@dataclass
class Check:
    id: str
    gruppo: str
    titolo: str
    esito: str
    trovato: str
    regola: str | None = None
    fonte: dict | None = None
    dettagli: list[str] = field(default_factory=list)       # may quote the manuscript: report on the PC only


@dataclass
class Rule:
    field: str
    article_type: str | None
    value: str | None
    number: float | None
    unit: str | None
    page: int | None
    origin: str                    # reported / interpreted / not_available (from the PDF) or manual (T-052)
    source: str | None = None      # manual: where the user read it
    read_on: str | None = None     # manual: date read


@dataclass
class JournalRules:
    journal_id: str
    title: str
    sigla: str | None
    card_id: str | None            # None: values only by hand (journal without guideline PDF, T-052)
    card_date: str
    guidelines_date: str | None
    type_name: str | None          # the article type of the card used (e.g. 'Original Research')
    type_value: str | None
    rules: dict[str, list[Rule]]

    def get(self, name: str) -> list[Rule]:
        return [r for r in self.rules.get(name, []) if r.origin != "not_available" and r.value]

    def source(self, r: Rule | None) -> dict:
        out = {"rivista": self.sigla or self.title, "scheda": self.card_date[:10], "linee_guida": self.guidelines_date}
        if r is not None:
            out.update({"campo": r.field, "tipo": r.article_type, "pagina": r.page})
            if r.origin == "manual":
                out.update({"a_mano": True, "fonte_a_mano": r.source, "letto_il": r.read_on})
        return out


# ----------------------------------------------------------------------------------------------------- the rules
def journals_with_cards(conn: sqlite3.Connection, journal_list: list[dict] | None = None) -> list[dict]:
    """Journals with rules: a current card from the guideline PDF and/or values written by hand (T-052)."""
    sigla_of = {jid: sg for sg, jid in policy_versions.journal_by_sigla(conn, journal_list or []).items()}
    exact = {r["sigla"].lower(): r["sigla"] for r in journal_list or [] if r.get("sigla")}
    out = []
    for jid in journal_cards.journals_with_rules(conn):
        j = conn.execute("SELECT id, title FROM journal WHERE id = ?", (jid,)).fetchone()
        card = journal_cards.current_card(conn, jid)
        manual = journal_cards.manual_values(conn, jid)
        dates = ([card["created_at"]] if card else []) + [m["created_at"] for m in manual]
        sg = sigla_of.get(jid)
        out.append({"id": jid, "titolo": j["title"], "sigla": exact.get(sg, sg.upper()) if sg else None,
                    "scheda": card["id"] if card else None, "data_scheda": max(dates) if dates else "",
                    "valori_a_mano": len(manual), "solo_a_mano": card is None})
    return sorted(out, key=lambda x: (x["titolo"] or "").lower())


def pick_journals(conn, journal_list: list[dict], wanted: list[str] | None) -> tuple[list[dict], list[str]]:
    """'tutte' or nothing -> every journal with a card; otherwise sigle or journal ids. Returns (journals, unknown)."""
    allj = journals_with_cards(conn, journal_list)
    if not wanted or any(w.lower() in ("tutte", "all", "*") for w in wanted):
        return allj, []
    out, unknown = [], []
    for w in wanted:
        hit = next((j for j in allj if w.lower() in ((j["sigla"] or "").lower(), j["id"].lower())), None)
        if hit and hit not in out:
            out.append(hit)
        elif not hit:
            unknown.append(w)
    return out, unknown


def load_rules(conn: sqlite3.Connection, journal: dict, kind: str) -> JournalRules:
    card = conn.execute("SELECT id, policy_version_id, created_at FROM journal_card WHERE id = ?",
                        (journal["scheda"],)).fetchone() if journal.get("scheda") else None
    pv = conn.execute("SELECT detected_at, summary FROM journal_policy_version WHERE id = ?",
                      (card["policy_version_id"],)).fetchone() if card else None
    printed = re.search(r"stampata dal browser il (\d{4}-\d{2}-\d{2})", (pv["summary"] or "") if pv else "")
    gdate = printed.group(1) if printed else ((pv["detected_at"] or "")[:10] if pv else None)
    vals = [Rule(v["field"], v["article_type"], v["value"], v["number"], v["unit"], v["page_no"], v["origin"],
                 v.get("source"), v.get("read_on"))
            for v in journal_cards.effective_values(conn, journal["id"])]
    types = [v for v in vals if v.field == "tipo_articolo"]
    chosen = next((v for v in types if KIND_PATTERNS[kind].search(v.article_type or "")), None)
    tname = chosen.article_type if chosen else None
    rules: dict[str, list[Rule]] = {}
    for f in journal_cards.TYPE_FIELDS:
        if f == "tipo_articolo":
            continue
        own = [v for v in vals if v.field == f and tname and v.article_type == tname]
        general = [v for v in vals if v.field == f and v.article_type == journal_cards.ALL_TYPES]
        if f == "altri_elementi":
            rules[f] = own + general
        else:
            usable = [v for v in own if v.origin != "not_available"]
            rules[f] = usable or general or own
            rules[f + "__generale"] = general if usable else []
    for f in journal_cards.JOURNAL_FIELDS:
        rules[f] = [v for v in vals if v.field == f]
    return JournalRules(journal["id"], journal["titolo"], journal["sigla"], card["id"] if card else None,
                        journal.get("data_scheda") or (card["created_at"] if card else ""), gdate,
                        tname, chosen.value if chosen else None, rules)


def _num(s: str) -> float:
    s = s.strip()
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", s):
        return float(re.sub(r"[.,]", "", s))
    return float(s.replace(",", "."))


@dataclass
class Limits:
    lo: float | None = None
    hi: float | None = None
    approx: bool = False
    strict: bool = False
    none: bool = False


def limits(rule: Rule) -> Limits:
    v = (rule.value or "").lower()
    out = Limits(approx=any(w in v for w in ("circa", "di solito", "limitat")), strict="rigido" in v)
    if "nessun limite" in v or "nessun numero fisso" in v or "numero non indicato" in v:
        out.none = True
        return out
    n = r"(\d{1,3}(?:[.,]\d{3})+|\d+(?:[.,]\d+)?)"
    m = re.search(rf"{n}\s*[-–]\s*{n}", v)
    if m:
        out.lo, out.hi = _num(m.group(1)), _num(m.group(2))
    m = re.search(rf"(?:fino a|al massimo|massimo|max\.?|non più di|up to)\s*{n}", v)
    if m:
        out.hi = _num(m.group(1))
    m = re.search(rf"almeno\s*{n}", v)
    if m:
        out.lo = _num(m.group(1))
    m = re.search(rf"meno di\s*{n}", v)
    if m:
        out.hi = _num(m.group(1)) - 1
    if out.hi is None and out.lo is None and rule.number is not None:
        out.hi = rule.number
    return out


def _n(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _fmt(n) -> str:
    if n is None:
        return "—"
    n = int(n) if float(n).is_integer() else n
    return f"{n:,}".replace(",", ".") if isinstance(n, int) else str(n).replace(".", ",")


# ------------------------------------------------------------------------------------------------------ the checks
class _Run:
    def __init__(self, m: Manuscript, jr: JournalRules, kind: str, conn: sqlite3.Connection | None):
        self.m, self.jr, self.kind, self.conn = m, jr, kind, conn
        # protocols follow the rules of what they are for (ethics, registration, reporting guideline); their text has
        # no Results or Discussion yet
        self.protocol = kind == "protocollo"
        if self.protocol:
            self.kind = protocol_of(m)
        self.core = ("introduction", "methods") if self.protocol else CORE_SECTIONS
        self.out: list[Check] = []

    def add(self, id_, group, title, outcome, found, rule: Rule | None = None, details=None, rule_text=None):
        self.out.append(Check(id_, group, title, outcome, found, rule_text or (rule.value if rule else None),
                              self.jr.source(rule) if (rule or rule_text) else None, list(details or [])))

    # ---- the file
    def file(self):
        m, d = self.m, self.m.doc
        if d.revisions:
            self.add("revisioni", "Il file", "Revisioni non accettate", "guarda",
                     f"{_n(d.revisions, 'modifica', 'modifiche')} con 'Revisioni' attive: accettale o rifiutale prima dell'invio")
        if d.comments:
            self.add("commenti", "Il file", "Commenti", "guarda", f"{_n(d.comments, 'commento', 'commenti')}: toglili prima dell'invio")
        if m.language and m.language != "en":
            self.add("lingua", "Il file", "Lingua", "ko", "il testo non sembra in inglese (R11: manoscritti in inglese)")
        elif m.language == "en":
            self.add("lingua", "Il file", "Lingua", "ok", "inglese")
        if not m.title:
            self.add("titolo", "Il file", "Titolo", "guarda", "titolo non riconosciuto (primo paragrafo o stile 'Titolo')")
        if len(m.sections) <= 1:
            self.add("struttura_letta", "Il file", "Titoli delle sezioni", "guarda",
                     "nessun titolo di sezione riconosciuto (Introduction, Methods, Results, Discussion...): i conteggi "
                     "delle parole e dei riferimenti non sono affidabili")

    # ---- length
    def length(self):
        m = self.m
        rules = self.jr.get("parole_testo_max")
        if not rules:
            self.add("parole_testo", "Lunghezza", "Parole del testo", "na", f"{_fmt(m.main_words)} parole")
        for r in rules[:1]:
            self._limit("parole_testo", "Lunghezza", "Parole del testo", m.main_words, r, "parole",
                        note="testo da Introduction alla fine della Discussion/Conclusion, titoli compresi; senza "
                             "abstract, riferimenti, tabelle e didascalie")

    def _limit(self, id_, group, title, value, r: Rule, unit, note=None):
        lim = limits(r)
        found = f"{_fmt(value)} {unit}"
        if lim.none:
            self.add(id_, group, title, "ok", found + " (la rivista non indica un limite)", r)
            return
        if lim.hi is None and lim.lo is None:
            self.add(id_, group, title, "guarda", found, r)
            return
        details = [note] if note else []
        if lim.hi is not None and value > lim.hi:
            extra = value - lim.hi
            self.add(id_, group, title, "guarda" if lim.approx else "ko",
                     f"{found}, al massimo {_fmt(lim.hi)}: {_fmt(extra)} in più", r, details)
        elif lim.lo is not None and value < lim.lo:
            self.add(id_, group, title, "guarda" if lim.approx else "ko",
                     f"{found}, almeno {_fmt(lim.lo)}", r, details)
        else:
            rng = f"tra {_fmt(lim.lo)} e {_fmt(lim.hi)}" if lim.lo is not None and lim.hi is not None else \
                (f"al massimo {_fmt(lim.hi)}" if lim.hi is not None else f"almeno {_fmt(lim.lo)}")
            self.add(id_, group, title, "ok", f"{found} ({rng})", r, details)

    # ---- abstract and keywords
    def abstract(self):
        m = self.m
        g = "Abstract e parole chiave"
        struct = self.jr.get("abstract_struttura")
        no_abstract = any("nessun abstract" in (r.value or "").lower() for r in struct)
        if not m.abstract_paras:
            if no_abstract:
                self.add("abstract", g, "Abstract", "ok", "nessun abstract, come chiede la rivista", struct[0])
            else:
                self.add("abstract", g, "Abstract", "guarda",
                         "abstract non trovato: serve un paragrafo 'Abstract' prima del testo")
            return
        if no_abstract:
            self.add("abstract", g, "Abstract", "ko", "per questo tipo di articolo la rivista non vuole l'abstract",
                     struct[0])
        rules = self.jr.get("abstract_parole_max")
        if rules:
            self._limit("parole_abstract", g, "Parole dell'abstract", m.abstract_words, rules[0], "parole")
        else:
            self.add("parole_abstract", g, "Parole dell'abstract", "na", f"{_fmt(m.abstract_words)} parole")
        found = [w for _, w in m.abstract_labels]
        if not struct:
            self.add("struttura_abstract", g, "Struttura dell'abstract", "na",
                     f"titoletti trovati: {', '.join(found) or 'nessuno'}")
        for r in struct[:1]:
            text = (r.value or "").strip()
            low = text.lower()
            if low.startswith("strutturato"):
                wanted = [w.strip(" .") for w in re.split(r"[,;]", text.split(":", 1)[1].split(";")[0])
                          if w.strip(" .")] if ":" in text else []
                have = {lab: written for lab, written in m.abstract_labels}
                missing, wording = [], []
                for w in wanted:
                    lab = ms.label_of(w)
                    if lab is None or lab not in have:
                        missing.append(w)
                    elif ms.norm(have[lab]).rstrip("s") != ms.norm(w).rstrip("s"):
                        wording.append(f"'{have[lab]}' invece di '{w}'")
                if not m.abstract_labels:
                    self.add("struttura_abstract", g, "Struttura dell'abstract", "ko",
                             f"abstract senza titoletti; servono: {', '.join(wanted)}", r)
                elif missing:
                    self.add("struttura_abstract", g, "Struttura dell'abstract", "ko",
                             f"mancano i titoletti: {', '.join(missing)} (trovati: {', '.join(found)})", r)
                elif wording:
                    self.add("struttura_abstract", g, "Struttura dell'abstract", "guarda",
                             f"titoletti con un altro nome: {'; '.join(wording)}", r)
                else:
                    self.add("struttura_abstract", g, "Struttura dell'abstract", "ok",
                             f"titoletti: {', '.join(found)}", r)
            elif low.startswith("non strutturato"):
                if len(m.abstract_labels) >= 3:
                    self.add("struttura_abstract", g, "Struttura dell'abstract", "ko",
                             f"la rivista vuole l'abstract senza titoletti (trovati: {', '.join(found)})", r)
                else:
                    self.add("struttura_abstract", g, "Struttura dell'abstract", "ok", "abstract senza titoletti", r)
            elif "nessun abstract" not in low:
                self.add("struttura_abstract", g, "Struttura dell'abstract", "guarda",
                         f"titoletti trovati: {', '.join(found) or 'nessuno'}", r)
        abbreviation_rules = [r for r in struct + self.jr.rules.get("abstract_struttura__generale", [])
                              if "abbreviaz" in (r.value or "").lower()]
        if abbreviation_rules:
            labels = {ms.norm(w) for _, w in m.abstract_labels}
            ids = {x[1] for x in m.registrations}
            abbr = sorted({a for a in re.findall(r"\b(?=[A-Za-z0-9]*[A-Z][A-Za-z0-9]*[A-Z])[A-Za-z0-9]{2,}\b",
                                                 m.abstract_text) if ms.norm(a) not in labels and a not in ids
                           and not any(a in i for i in ids)})
            self.add("abbreviazioni_abstract", g, "Abbreviazioni nell'abstract", "guarda" if abbr else "ok",
                     f"{len(abbr)} possibili abbreviazioni" if abbr else "nessuna abbreviazione trovata",
                     abbreviation_rules[0], abbr[:30])
        self._keywords()

    def _keywords(self):
        m, g = self.m, "Abstract e parole chiave"
        rules = self.jr.get("parole_chiave")
        if not m.keywords:
            self.add("parole_chiave", g, "Parole chiave", "guarda" if rules else "na",
                     "parole chiave non trovate (riga 'Keywords:' dopo l'abstract)", rules[0] if rules else None)
            return
        if not rules:
            self.add("parole_chiave", g, "Parole chiave", "na", f"{len(m.keywords)} parole chiave")
            return
        r = rules[0]
        self._limit("parole_chiave", g, "Numero di parole chiave", len(m.keywords), r, "parole chiave")
        low = (r.value or "").lower()
        if "titolo" in low and m.title:
            title = ms.norm(m.title)
            in_title = [k for k in m.keywords if re.search(rf"\b{re.escape(ms.norm(k))}\b", title)]
            if in_title:
                self.add("parole_chiave_titolo", g, "Parole chiave non nel titolo", "ko",
                         f"{len(in_title)} parole chiave già nel titolo", r, in_title)
            else:
                stop = {"and", "the", "of", "in", "for", "with", "on", "to", "a", "an", "vs", "versus"}
                words = sorted({w for k in m.keywords for w in re.findall(r"[a-z0-9]+", ms.norm(k))
                                if len(w) > 3 and w not in stop and re.search(rf"\b{w}\b", title)})
                self.add("parole_chiave_titolo", g, "Parole chiave non nel titolo", "guarda" if words else "ok",
                         f"parole del titolo dentro le parole chiave: {', '.join(words)}" if words else
                         "nessuna parola chiave ripete il titolo", r)
        if "mesh" in low and self.conn is not None:
            try:
                names = {ms.norm(n) for (n,) in self.conn.execute("SELECT DISTINCT descriptor_name FROM article_mesh")}
            except sqlite3.Error:
                names = set()
            hits = [k for k in m.keywords if ms.norm(k) in names]
            self.add("parole_chiave_mesh", g, "Parole chiave MeSH", "guarda",
                     f"{len(hits)} di {len(m.keywords)} riconosciute tra i termini MeSH degli articoli già in ORPHEUS "
                     "(elenco incompleto: controlla le altre su meshb.nlm.nih.gov)", r, hits)

    # ---- structure of the text
    def _optional(self) -> set[str]:
        """Sections the journal calls optional ('Practical Implications facoltative')."""
        out = set()
        for r in self.jr.get("altri_elementi"):
            if "facoltativ" in r.value.lower():
                canon = _section_named(r.value)
                if canon:
                    out.add(canon)
        return out

    def structure(self):
        m, g = self.m, "Struttura del testo"
        names = [s.name for s in m.sections]
        text_rules = [r for r in self.jr.get("altri_elementi") if STRUCTURE_RULE.match(r.value)]
        if not text_rules:
            missing = [ms.SECTION_NAMES[c] for c in self.core
                       if c not in names and not (c in ("results", "discussion") and "results_discussion" in names)]
            self.add("sezioni", g, "Sezioni principali", "ko" if missing else "ok",
                     f"mancano: {', '.join(missing)}" if missing else
                     ("Introduction e Methods presenti (protocollo)" if self.protocol else
                      "Introduction, Methods, Results, Discussion presenti"))
        optional = self._optional()
        for r in text_rules:
            items = _split_outside_parentheses(STRUCTURE_RULE.match(r.value).group(1))
            missing, wording, forbidden, order, lower, skipped = [], [], [], [], [], []
            for item in items:
                bad = re.search(r"\(non\s+'([^']+)'\)", item)
                name = re.sub(r"\(.*?\)", "", item).strip(" .")
                english = ms.heading_of(name)
                canon = english or ITALIAN_SECTIONS.get(ms.norm(name))
                if canon is None:
                    skipped.append(name)
                    continue
                sec = m.section(canon) if canon not in ("abstract", "keywords") else \
                    (m.section(canon) or (True if canon == "keywords" and m.keywords else None))
                if sec is None:
                    if canon not in optional:
                        missing.append(name)
                    continue
                if sec is True:
                    continue
                order.append(names.index(canon))
                written = re.sub(r"^\s*(?:\d+(?:\.\d+)*\.?)\s+", "", sec.heading or "").rstrip(":. ")
                if bad and ms.norm(written) == ms.norm(bad.group(1)):
                    forbidden.append(f"'{written}' (la rivista vuole '{name}')")
                elif english and canon not in ("abstract", "keywords") and \
                        ms.norm(written).rstrip("s") != ms.norm(name).rstrip("s"):
                    wording.append(f"'{written}' invece di '{name}'")
                if "maiuscol" in r.value.lower() and written and written != written.upper():
                    lower.append(written)
            core_missing = [x for x in missing if (ms.heading_of(x) or ITALIAN_SECTIONS.get(ms.norm(x))) in self.core]
            det = [f"non controllabili qui: {', '.join(skipped)}"] if skipped else []
            if forbidden:
                self.add("sezioni_nomi_vietati", g, "Nomi delle sezioni", "ko", "; ".join(forbidden), r)
            if core_missing:
                self.add("sezioni", g, "Sezioni richieste", "ko", f"mancano: {', '.join(missing)}", r, det)
            elif missing:
                self.add("sezioni", g, "Sezioni richieste", "guarda", f"non trovate: {', '.join(missing)}", r, det)
            else:
                self.add("sezioni", g, "Sezioni richieste", "ok", "tutte presenti", r, det)
            if order != sorted(order):
                self.add("sezioni_ordine", g, "Ordine delle sezioni", "ko" if "ordine" in r.value.lower() else "guarda",
                         "le sezioni non sono nell'ordine indicato dalla rivista", r)
            if wording and not forbidden:
                self.add("sezioni_nomi", g, "Nomi delle sezioni", "guarda", "; ".join(wording), r)
            if lower:
                self.add("sezioni_maiuscolo", g, "Titoli delle sezioni in maiuscolo", "ko",
                         f"non in maiuscolo: {', '.join(lower)}", r)

    # ---- figures and tables
    def figures(self):
        m, g = self.m, "Figure e tabelle"
        figs = set(m.captions["figure"]) or set(m.mentions["figure"])
        tabs = set(m.captions["table"]) | set(range(1, len(m.doc.tables) + 1)) if m.doc.tables else \
            set(m.captions["table"]) or set(m.mentions["table"])
        nf, nt = len(figs), len(tabs)
        found = f"{nf} figure e {nt} tabelle ({nf + nt} in tutto)"
        rules = self.jr.get("figure_tabelle_max")
        if not rules:
            self.add("figure_tabelle", g, "Figure e tabelle", "na", found)
        for r in rules[:1]:
            low = (r.value or "").lower()
            sep = re.search(r"(\d+)\s*tabelle\s+e\s+(\d+)\s*figure", low)
            if sep:
                tmax, fmax = int(sep.group(1)), int(sep.group(2))
                bad = (nt > tmax) or (nf > fmax)
                self.add("figure_tabelle", g, "Figure e tabelle", "ko" if bad else "ok",
                         f"{found}; al massimo {tmax} tabelle e {fmax} figure", r)
            elif re.search(r"insieme|e/o|una tabella o|una figura oppure|\(una tabella|tabelle o figure", low) or \
                    re.fullmatch(r"\s*\d+\s*", low):
                lim = r.number if r.number is not None else (1 if re.search(r"\buna\b", low) else None)
                if lim is None:
                    self.add("figure_tabelle", g, "Figure e tabelle", "guarda", found, r)
                else:
                    self.add("figure_tabelle", g, "Figure e tabelle", "ko" if nf + nt > lim else "ok",
                             f"{found}; al massimo {_fmt(lim)} insieme", r)
            else:
                self.add("figure_tabelle", g, "Figure e tabelle", "guarda", found, r)
        for kind, word in (("figure", "Figure"), ("table", "Table")):
            caps, men = set(m.captions[kind]), m.mentions[kind]
            uncited = sorted(caps - set(men))
            nocap = sorted(set(men) - caps) if caps else []
            first = [n for n in men]
            if uncited or nocap:
                det = ([f"{word} {n}: didascalia ma nessuna citazione nel testo" for n in uncited] +
                       [f"{word} {n}: citata nel testo ma senza didascalia" for n in nocap])
                self.add(f"{kind}_citate", g, f"{'Figure' if kind == 'figure' else 'Tabelle'} citate nel testo",
                         "guarda", f"{len(uncited) + len(nocap)} da controllare", details=det)
            elif caps:
                self.add(f"{kind}_citate", g, f"{'Figure' if kind == 'figure' else 'Tabelle'} citate nel testo", "ok",
                         f"tutte le {len(caps)} citate")
            if first and first != sorted(first):
                self.add(f"{kind}_ordine", g, f"Ordine delle {'figure' if kind == 'figure' else 'tabelle'}", "guarda",
                         "la prima citazione nel testo non segue l'ordine dei numeri",
                         details=[" → ".join(str(n) for n in first[:15])])

    # ---- references and citations
    def references(self):
        m, g = self.m, "Riferimenti e citazioni"
        n = len(m.references)
        rules = self.jr.get("riferimenti_max")
        if not m.references:
            self.add("riferimenti", g, "Riferimenti", "guarda", "elenco dei riferimenti non trovato (titolo 'References')",
                     rules[0] if rules else None)
        elif rules:
            self._limit("riferimenti", g, "Numero di riferimenti", n, rules[0], "riferimenti")
        else:
            self.add("riferimenti", g, "Numero di riferimenti", "na", f"{n} riferimenti")
        c = m.citations
        style_rules = self.jr.get("stile_citazioni")
        style_text = " ".join((r.value or "").lower() for r in style_rules)
        free = re.search(r"qualunque stile|free format", style_text)
        wants_numeric = re.search(r"numeric|numerat|\bama\b|vancouver|numeri", style_text)
        wants_ay = re.search(r"\bapa\b|harvard|autore[- ]anno", style_text)
        found_style = {"numerica": f"numerica ({c.form})", "autore-anno": "autore-anno", "nessuna": "nessuna citazione"}
        if c.style == "nessuna":
            self.add("citazioni", g, "Citazioni nel testo", "guarda" if m.references else "na",
                     "nessuna citazione riconosciuta nel testo")
        else:
            expected = "numerica" if wants_numeric and not wants_ay else ("autore-anno" if wants_ay else None)
            if expected and expected != c.style:
                self.add("stile_citazioni", g, "Stile delle citazioni", "guarda" if free else "ko",
                         f"nel manoscritto {found_style[c.style]}; la rivista: {expected}"
                         + (" (all'invio è accettato qualunque stile coerente)" if free else ""), style_rules[0])
            elif style_rules:
                self.add("stile_citazioni", g, "Stile delle citazioni", "ok", found_style[c.style], style_rules[0])
        if c.style == "numerica" and m.references:
            self._numeric(n, style_rules, style_text, bool(free))
        elif c.style == "autore-anno" and m.references:
            self._author_year()
        if m.references and re.search(r"alfabetic", style_text):
            keys = [ms.surname_key(r.first_author) for r in m.references if r.first_author]
            bad = sum(1 for a, b in zip(keys, keys[1:]) if a > b)
            self.add("riferimenti_ordine_alfabetico", g, "Riferimenti in ordine alfabetico", "ko" if bad else "ok",
                     f"{bad} riferimenti fuori dall'ordine alfabetico del primo autore" if bad else
                     "in ordine alfabetico", next(r for r in style_rules if "alfabetic" in (r.value or "").lower()))
        for r in style_rules:
            if "abstract di congresso" in (r.value or "").lower():
                conf = [x.text[:160] for x in m.references
                        if re.search(r"\b(abstract|congress|conference|proceedings|annual meeting|symposium)\b",
                                     x.text, re.I)]
                self.add("riferimenti_congressi", g, "Abstract di congresso tra i riferimenti",
                         "guarda" if conf else "ok", f"{len(conf)} possibili" if conf else "nessuno trovato", r, conf)
            if re.search(r"autori:\s*i primi", (r.value or "").lower()):
                self.add("riferimenti_autori", g, "Autori nei riferimenti", "guarda",
                         "controlla quanti autori scrivere prima di 'et al.'", r)

    def _numeric(self, n: int, style_rules, style_text: str, free: bool):
        m, g = self.m, "Riferimenti e citazioni"
        cited, first = set(), []
        for _, _, nums in m.citations.groups:
            for x in nums:
                cited.add(x)
                if x not in first:
                    first.append(x)
        out_of_range = sorted(x for x in cited if x < 1 or x > n)
        never = sorted(set(range(1, n + 1)) - cited)
        if out_of_range:
            self.add("citazioni_numeri", g, "Numeri citati senza riferimento", "ko",
                     f"{_n(len(out_of_range), 'numero citato', 'numeri citati')} oltre i {n} riferimenti",
                     details=[", ".join(map(str, out_of_range[:40]))])
        else:
            self.add("citazioni_numeri", g, "Numeri citati senza riferimento", "ok", "nessuno")
        if never:
            self.add("riferimenti_non_citati", g, "Riferimenti mai citati nel testo", "ko",
                     f"{len(never)} di {n}", details=[", ".join(map(str, never[:60]))])
        else:
            self.add("riferimenti_non_citati", g, "Riferimenti mai citati nel testo", "ok", f"tutti i {n} citati")
        wants_order = re.search(r"ordine di citazione|ordine di comparsa", style_text)
        if wants_order and not re.search(r"alfabetic", style_text):
            expected, wrong = 1, []
            for x in first:
                if x != expected and x > expected:
                    wrong.append(f"{x} citato prima di {expected}")
                expected = max(expected, x + 1)
            rule = next(r for r in style_rules if re.search(r"ordine di (?:citazione|comparsa)", (r.value or "").lower()))
            self.add("citazioni_ordine", g, "Riferimenti numerati in ordine di citazione",
                     ("guarda" if free else "ko") if wrong else "ok",
                     f"{_n(len(wrong), 'salto', 'salti')} nell'ordine" if wrong else "in ordine", rule, wrong[:15])
        for r in self.jr.get("altri_elementi"):
            if re.search(r"riferimenti per sostenere", (r.value or "").lower()):
                lim = int(re.search(r"(\d+)", r.value).group(1)) if re.search(r"(\d+)", r.value) else 3
                big = [lab for _, lab, nums in m.citations.groups if len(nums) > lim]
                self.add("citazioni_gruppi", g, f"Al massimo {lim} riferimenti per affermazione",
                         "ko" if big else "ok", f"{_n(len(big), 'gruppo', 'gruppi')} con più di {lim} riferimenti" if big else
                         f"nessun gruppo con più di {lim}", r, big[:20])

    def _author_year(self):
        m, g = self.m, "Riferimenti e citazioni"
        refs = [(ms.surname_key(r.first_author), r.year) for r in m.references]
        used, unmatched = set(), []
        for _, surname, year, label in m.citations.author_year:
            k = ms.surname_key(surname)
            hit = [i for i, (s, y) in enumerate(refs) if s == k and y == year]
            if hit:
                used.update(hit)
            elif label not in unmatched:
                unmatched.append(label)
        never = [m.references[i].text[:120] for i in range(len(refs)) if i not in used]
        self.add("citazioni_abbinate", g, "Citazioni con il loro riferimento", "guarda" if unmatched else "ok",
                 f"{len(unmatched)} citazioni senza riferimento con lo stesso autore e anno" if unmatched else
                 "tutte abbinate", details=unmatched[:30])
        self.add("riferimenti_non_citati", g, "Riferimenti mai citati nel testo", "guarda" if never else "ok",
                 f"{len(never)} di {len(refs)} senza una citazione riconosciuta" if never else "tutti citati",
                 details=never[:30])

    # ---- statements
    def statements(self):
        m, g, st = self.m, "Dichiarazioni", self.m.statements
        refs = m.section("references")
        for r in self.jr.get("conflitti_finanziamenti"):
            low = r.value.lower()
            if "conflitt" in low:
                must = re.search(r"anche se|obbligatori|da dichiarare", low)
                quoted = re.search(r"'([^']{6,})'", r.value)
                if quoted and st["conflitti"]:
                    text = ms.norm(" ".join(m.doc.paragraphs[i].text for i in st["conflitti"]))
                    none_said = re.search(r"\bnone\b|no (?:potential )?conflicts?|nothing to disclose|no competing", text)
                    if ms.norm(quoted.group(1)) in text or not none_said:
                        self.add("conflitti_frase", g, "Frase per nessun conflitto", "ok", "come chiede la rivista", r)
                    else:
                        self.add("conflitti_frase", g, "Frase per nessun conflitto", "guarda",
                                 f"usa la frase della rivista: '{quoted.group(1)}'", r)
                else:
                    self._statement("conflitti", "Conflitti di interesse", st["conflitti"], r, must)
            if "finanziament" in low and "sponsor" not in low:
                self._statement("finanziamenti", "Finanziamenti", st["finanziamenti"], r,
                                re.search(r"obbligatori|elencati|dichiararlo", low))
            if "sponsor" in low:
                self.add("sponsor", g, "Ruolo degli sponsor", "guarda", "scrivi il ruolo dei finanziatori nello studio", r)
            if "approvazione del prodotto" in low:
                self.add("prodotti", g, "Nessuna approvazione di prodotti", "guarda",
                         "frase richiesta dalla rivista: i risultati non sono un'approvazione del prodotto", r)
        for r in self.jr.get("dati")[:1]:
            must = re.search(r"obbligatori", r.value.lower())
            self._statement("dati", "Disponibilità dei dati", st["dati"], r, False if not must else must,
                            hint="obbligatoria all'accettazione" if must else None)
        for r in self.jr.get("politica_ia"):
            low = r.value.lower()
            if re.search(r"dichiar|descritt", low):
                if st["ia"]:
                    where = "ok"
                    if "prima dei riferimenti" in low and refs and min(st["ia"]) > refs.start:
                        where = "guarda"
                    self.add("ia", g, "Uso dell'IA", where, "dichiarazione sull'IA trovata"
                             + ("" if where == "ok" else ", ma dopo i riferimenti"), r)
                else:
                    self.add("ia", g, "Uso dell'IA", "guarda",
                             "nessuna dichiarazione sull'IA: serve se avete usato l'IA generativa", r)
                break
        if self.kind == "originale":
            for r in self.jr.get("etica_consenso"):
                low = r.value.lower()
                if "helsinki" in low:
                    self._statement("helsinki", "Dichiarazione di Helsinki", st["helsinki"], r, True)
                elif "consenso" in low and "comitato" not in low and "irb" not in low and "approvazione" not in low:
                    if "genitori" in low or "18 anni" in low:
                        self.add("consenso_minori", g, "Consenso per i minori", "guarda",
                                 "con partecipanti sotto i 18 anni serve il consenso dei genitori o tutori", r)
                    else:
                        self._statement("consenso", "Consenso informato", st["consenso"], r, True)
                else:
                    self._statement("etica", "Approvazione etica", st["etica"], r, True)
                    if "consenso" in low:
                        self._statement("consenso", "Consenso informato", st["consenso"], r, True)
                    if "numero" in low:
                        has_number = any(ms.APPROVAL_NUMBER.search(m.doc.paragraphs[i].text) for i in st["etica"])
                        self.add("etica_numero", g, "Numero e data dell'approvazione etica",
                                 "ok" if has_number else "guarda",
                                 "numero trovato vicino all'approvazione" if has_number else
                                 "scrivi il numero (e la data) dell'approvazione", r)
        regs = self.jr.get("registrazione")
        ids = m.registrations
        review_ids = [x for x in ids if x[0] in ("PROSPERO", "OSF", "INPLASY")]
        trial_ids = [x for x in ids if x not in review_ids]
        for r in regs:
            low = r.value.lower()
            if "revision" in low and self.kind == "revisione":
                must = re.search(r"obbligatori", low)
                if review_ids:
                    self.add("registrazione", g, "Registrazione della revisione", "ok",
                             ", ".join(f"{a} {b}" for a, b, _ in review_ids), r)
                else:
                    self.add("registrazione", g, "Registrazione della revisione", "ko" if must else "guarda",
                             "numero di registrazione non trovato (es. PROSPERO CRD…)", r)
            elif "revision" not in low and self.kind == "originale" and re.search(r"clinic|registr", low):
                if trial_ids:
                    in_abs = any(x[2] for x in trial_ids)
                    where = "abstract" in low and not in_abs
                    self.add("registrazione", g, "Registrazione dello studio", "guarda" if where else "ok",
                             ", ".join(f"{a} {b}" for a, b, _ in trial_ids) + (" (non nell'abstract)" if where else ""), r)
                elif not any(c.id == "registrazione" for c in self.out):
                    self.add("registrazione", g, "Registrazione dello studio", "guarda",
                             "nessun numero di registrazione: serve se è uno studio clinico (trial)", r)
        design, sager = [], []
        for r in self.jr.get("linee_guida_reporting"):
            low = r.value.lower()
            names = re.findall(r"\b(consort|strobe|prisma|tidier|stard|spirit|coreq|arrive|tripod)\b", low)
            if "sager" in low:
                sager.append(r)
            if names and ("revision" in low or "prisma" in names) == (self.kind == "revisione"):
                design.append((r, names))
        if design:
            names = list(dict.fromkeys(n.upper() for _, ns in design for n in ns))
            cited = [x for x in m.guidelines if not x.upper().startswith("SAGER")]
            if self.kind == "revisione":
                cited = [x for x in cited if x.upper().startswith("PRISMA")]
            must = self.kind == "revisione" and any("prisma" in ns and "es." not in r.value.lower() for r, ns in design)
            rules = [r for r, _ in design]
            self._add_rules("reporting", g, "Linea guida di reporting", "ok" if cited else ("ko" if must else "guarda"),
                            f"citata nel testo: {', '.join(cited)}" if cited else
                            f"nessuna linea guida citata ({', '.join(names)}): indica quella seguita e allega la checklist",
                            rules)
        if sager:
            have = any(x.upper().startswith("SAGER") for x in m.guidelines)
            self._add_rules("sager", g, "Sesso e genere (SAGER)", "ok" if have else "guarda",
                            "SAGER citata" if have else "consigliata: riporta sesso e genere secondo SAGER", sager)
        for r in self.jr.get("orcid"):
            self.add("orcid", g, "ORCID", "guarda", "si inserisce nel sistema di invio della rivista", r)
        for r in self.jr.get("revisione"):
            if "cieco" in r.value.lower() and "doppio" in r.value.lower():
                exposed = m.emails or m.orcids
                self.add("doppio_cieco", g, "Revisione in doppio cieco", "guarda",
                         ("nel manoscritto ci sono indirizzi e-mail o ORCID: " if exposed else "") +
                         "nomi e affiliazioni degli autori solo nella pagina del titolo separata", r)

    def _add_rules(self, id_, group, title, outcome, found, rules: list[Rule], details=None):
        text = " · ".join(dict.fromkeys(r.value for r in rules))
        self.add(id_, group, title, outcome, found, rules[0], details, rule_text=text)
        pages = ", ".join(dict.fromkeys(str(r.page) for r in rules if r.page))
        if pages:
            self.out[-1].fonte["pagina"] = pages

    def _statement(self, id_, title, where: list[int], r: Rule, must, hint=None):
        if where:
            self.add(id_, "Dichiarazioni", title, "ok", "trovata", r)
        else:
            text = "non trovata" + (f" ({hint})" if hint else "")
            self.add(id_, "Dichiarazioni", title, "ko" if must else "guarda", text, r)

    # ---- page format and the other rules
    def _format(self, r: Rule) -> bool:
        """Font, size, line spacing, margins, page and line numbers. False when the rule says none of these."""
        lay, low = self.m.doc.layout, r.value.lower()
        problems, checked = [], []
        fonts = [f for f in ("Arial", "Times New Roman", "Calibri", "Helvetica", "Cambria", "Georgia") if f.lower() in low]
        if fonts and lay.get("carattere"):
            checked.append("carattere")
            if lay["carattere"] not in fonts:
                problems.append(f"carattere {lay['carattere']} (la rivista: {' o '.join(fonts)})")
        size = re.search(r"(\d+(?:[.,]\d+)?)\s*pt\b", low)
        if size and lay.get("corpo_pt"):
            checked.append("corpo")
            if abs(float(lay["corpo_pt"]) - _num(size.group(1))) > 0.01:
                problems.append(f"corpo {_fmt(lay['corpo_pt'])} pt (la rivista: {size.group(1)} pt)")
        sp = re.search(r"interlinea\s+(doppia|singola|1[,.]5)", low)
        if sp:
            checked.append("interlinea")
            want = sp.group(1).replace(".", ",")
            if lay.get("interlinea") != want:
                problems.append(f"interlinea {lay.get('interlinea')} (la rivista: {want})")
        mm = re.search(r"margin\w*\s+(?:di\s+)?(almeno\s+)?(\d+(?:[.,]\d+)?|un|1)\s*(cm|poll\w*|inch\w*)", low)
        margins = lay.get("margini_cm") or {}
        if mm and margins:
            checked.append("margini")
            amount = 1.0 if mm.group(2) == "un" else _num(mm.group(2))
            want = amount * (2.54 if not mm.group(3).startswith("cm") else 1)
            off = {k: x for k, x in margins.items() if (x < want - 0.05 if mm.group(1) else abs(x - want) > 0.1)}
            if off:
                problems.append("margini " + ", ".join(f"{SIDES.get(k, k)} {_fmt(x)} cm" for k, x in off.items())
                                + f" (la rivista: {'almeno ' if mm.group(1) else ''}{_fmt(round(want, 2))} cm)")
        if re.search(r"numeri di pagina|pagine numerate|numerazione delle pagine", low):
            checked.append("numeri di pagina")
            if not lay.get("numeri_di_pagina"):
                problems.append("mancano i numeri di pagina (Inserisci > Numero di pagina)")
        if re.search(r"numeri di riga|\bdi riga\b|numerazione delle righe", low):
            checked.append("numeri di riga")
            if not lay.get("numeri_di_riga"):
                problems.append("mancano i numeri di riga (Layout > Numeri di riga > Continua)")
        if not checked:
            return False
        self.add(f"formato_{len(self.out)}", "Formato della pagina", "Formato: " + ", ".join(checked),
                 "ko" if problems else "ok", "; ".join(problems) if problems else
                 f"{lay.get('carattere')}, {_fmt(lay.get('corpo_pt'))} pt, interlinea {lay.get('interlinea')}"
                 + (", numeri di riga" if lay.get("numeri_di_riga") else "")
                 + (", numeri di pagina" if lay.get("numeri_di_pagina") else ""), r)
        return True

    def _section_rule(self, r: Rule) -> bool:
        """'Practical Applications al massimo 350 parole', 'paragrafo Perspective obbligatorio ...', 'Practical
        Implications facoltative: 3-5 punti', 'introduzione di 4-6 paragrafi', 'Metodi con sottotitoli obbligatori
        (...)'. False when the rule is about something else."""
        m, low, g = self.m, r.value.lower(), "Altre regole della rivista"
        sub = re.search(r"sottotitoli obbligatori[^(]*\(([^)]+)\)", r.value, re.I)
        if sub:
            canon = _section_named(r.value) or ITALIAN_SECTIONS.get(ms.norm(r.value.split()[0]))
            sec = m.section(canon) if canon else None
            wanted = [x.strip() for x in sub.group(1).split(",") if x.strip()]
            inside = [ms.norm(ms._NUMBERING.sub("", m.doc.paragraphs[i].text).rstrip(":. ")) for i in (sec.paras if sec
                                                                                                       else [])]
            missing = [w for w in wanted if ms.norm(w) not in inside]
            pos = [inside.index(ms.norm(w)) for w in wanted if ms.norm(w) in inside]
            outcome = "ko" if missing or pos != sorted(pos) else "ok"
            self.add(f"sottotitoli_{canon}", g, f"Sottotitoli obbligatori ({ms.SECTION_NAMES.get(canon, '?')})", outcome,
                     (f"mancano: {', '.join(missing)}" if missing else "") +
                     (" fuori ordine" if pos != sorted(pos) else "") if outcome == "ko" else "tutti presenti, in ordine",
                     r)
            return True
        para_rule = re.search(r"(introduzione|metodi|risultati|discussione)\s+di\s+(\d+)\s*[-–]\s*(\d+)\s+paragrafi", low)
        if para_rule:
            canon = ITALIAN_SECTIONS[para_rule.group(1)]
            sec = m.section(canon)
            if sec is None:
                self.add(f"paragrafi_{canon}", g, f"Paragrafi ({ms.SECTION_NAMES[canon]})", "guarda",
                         "sezione non trovata", r)
                return True
            body = [m.doc.paragraphs[i] for i in sec.paras if m.doc.paragraphs[i].text]
            heads = [p for p in body if ms._looks_like_heading(p) and p.words <= 8]
            n = len(body) - len(heads)
            lo, hi = int(para_rule.group(2)), int(para_rule.group(3))
            extra = f"; {len(heads)} sottotitoli" if heads and "senza sottotitoli" in low else ""
            self.add(f"paragrafi_{canon}", g, f"Paragrafi ({ms.SECTION_NAMES[canon]})",
                     "ok" if lo <= n <= hi and not extra else "guarda", f"{_n(n, 'paragrafo', 'paragrafi')} (tra {lo} e {hi}){extra}", r)
            return True
        canon = _section_named(r.value)
        words = re.search(r"al massimo\s+(\d+)\s+parole", low)
        points = re.search(r"(\d+)\s*[-–]\s*(\d+)\s+punti", low)
        if not canon or not (words or points or "obbligatori" in low):
            return False
        sec = m.section(canon)
        label = _name_in_rule(r.value) or ms.SECTION_NAMES[canon]
        if sec is None:
            optional = "facoltativ" in low
            self.add(f"sezione_{canon}", g, label, "ok" if optional else ("ko" if "obbligatori" in low else "guarda"),
                     "facoltativa: non presente" if optional else "sezione non trovata", r)
            return True
        written = re.sub(r"^\s*(?:\d+(?:\.\d+)*\.?)\s+", "", sec.heading or "").rstrip(":. ")
        problems = []
        if ms.norm(written).rstrip("s") != ms.norm(label).rstrip("s"):
            problems.append(f"si chiama '{written}', la rivista: '{label}'")
        body = [m.doc.paragraphs[i] for i in sec.paras if m.doc.paragraphs[i].text]
        n_words = sum(p.words for p in body)
        found = f"{n_words} parole"
        if words and n_words > int(words.group(1)):
            problems.append(f"{n_words} parole, al massimo {words.group(1)}")
        if points:
            lo, hi = int(points.group(1)), int(points.group(2))
            found = f"{len(body)} punti"
            if not lo <= len(body) <= hi:
                problems.append(f"{len(body)} punti, tra {lo} e {hi}")
        self.add(f"sezione_{canon}", g, label, ("ko" if any("al massimo" in x or "tra " in x for x in problems)
                                                else "guarda") if problems else "ok",
                 "; ".join(problems) if problems else found, r)
        return True

    def _linked_rule(self, r: Rule) -> bool:
        """Rules among the 'other elements' that repeat a statement: registration, reporting guideline, ethics."""
        m, low, g = self.m, r.value.lower(), "Altre regole della rivista"
        if "segnaposto" in low:
            return False
        if "registrazione" in low and ("revision" in low) == (self.kind == "revisione"):
            ids = [x for x in m.registrations if (x[0] in REVIEW_REGISTRIES) == (self.kind == "revisione")]
            must = "obbligatori" in low
            self.add(f"altro_registrazione_{len(self.out)}", g, "Registrazione", "ok" if ids else ("ko" if must else
                                                                                                  "guarda"),
                     ", ".join(f"{a} {b}" for a, b, _ in ids) if ids else "numero di registrazione non trovato", r)
            return True
        names = re.findall(r"\b(prisma|consort|strobe)\b", low)
        if names and ("prisma" in names) == (self.kind == "revisione"):
            have = [x for x in m.guidelines if any(x.lower().startswith(n) for n in names)]
            upload = "caricat" in low or "all'invio" in low
            self.add(f"altro_reporting_{len(self.out)}", g, f"{names[0].upper()}", "ok" if have else "guarda",
                     ("citata nel testo" + ("; checklist e diagramma di flusso vanno caricati all'invio" if upload else ""))
                     if have else "non citata nel testo", r)
            return True
        if re.search(r"\birb\b|approvazione etica", low) and self.kind == "originale":
            self._statement(f"altro_etica_{len(self.out)}", "Approvazione etica (IRB)", m.statements["etica"], r, True)
            return True
        return False

    def others(self):
        m = self.m
        for r in self.jr.get("altri_elementi"):
            v = r.value
            low = v.lower()
            if STRUCTURE_RULE.match(v) or re.search(r"riferimenti per sostenere", low):
                continue                                           # see structure() and _numeric()
            if self._format(r):
                continue
            if re.search(r"non\s+usare\s+'?subjects", low):
                hits = sum(len(re.findall(r"\bsubjects?\b", m.doc.paragraphs[i].text, re.I)) for i in m.main_paras)
                self.add("parola_subjects", "Altre regole della rivista", "Niente 'subjects'", "ko" if hits else "ok",
                         f"'subject(s)' {hits} volte nel testo" if hits else "mai usata", r)
                continue
            if re.search(r"titolo breve|running title", low):
                lim = re.search(r"(\d+)\s*caratteri", low)
                if not m.running_title:
                    self.add("titolo_breve", "Altre regole della rivista", "Titolo breve", "guarda",
                             "titolo breve non trovato (riga 'Running title:')", r)
                else:
                    n = len(m.running_title)
                    ok = not lim or n < int(lim.group(1))
                    self.add("titolo_breve", "Altre regole della rivista", "Titolo breve", "ok" if ok else "ko",
                             f"{n} caratteri", r)
                continue
            if re.search(r"highlights|key points", low):
                sec = m.section("highlights")
                if sec is None:
                    self.add("highlights", "Altre regole della rivista", "Highlights / key points", "guarda",
                             "non trovati" + (" (servono alla revisione)" if "revision" in low else ""), r)
                else:
                    n = sum(1 for i in sec.paras if m.doc.paragraphs[i].text)
                    lim = re.search(r"al massimo (\d+)", low)
                    ok = not lim or n <= int(lim.group(1))
                    self.add("highlights", "Altre regole della rivista", "Highlights / key points",
                             "ok" if ok else "ko", f"{n} punti", r)
                continue
            if re.search(r"plain language|linguaggio semplice", low):
                sec = m.section("plain")
                if sec is None:
                    self.add("plain_summary", "Altre regole della rivista", "Riassunto in linguaggio semplice", "guarda",
                             "non trovato (titolo 'Plain Language Summary')", r)
                else:
                    words = sum(m.doc.paragraphs[i].words for i in sec.paras)
                    lim = re.search(r"al massimo (\d+)\s*parole", low)
                    ok = not lim or words <= int(lim.group(1))
                    self.add("plain_summary", "Altre regole della rivista", "Riassunto in linguaggio semplice",
                             "ok" if ok else "ko", f"{words} parole", r)
                continue
            if self._section_rule(r) or self._linked_rule(r):
                continue
            self.add(f"regola_{r.page or 0}_{len(self.out)}", "Altre regole della rivista", "Promemoria", "guarda",
                     "da controllare a mano", r)
        for r in self.jr.get("documenti_invio"):
            self.add(f"invio_{len(self.out)}", "Altre regole della rivista", "Documenti per l'invio", "guarda",
                     "da preparare per l'invio", r)


STRUCTURE_RULE = re.compile(r"^\s*(?:testo|sezioni[^:]{0,40})\s*:\s*(.+)$", re.I | re.S)
ITALIAN_SECTIONS = {"introduzione": "introduction", "metodi": "methods", "metodo": "methods", "risultati": "results",
                    "discussione": "discussion", "conclusioni": "conclusion", "conclusione": "conclusion",
                    "ringraziamenti": "acknowledgments", "riferimenti": "references", "bibliografia": "references",
                    "abstract": "abstract", "riassunto": "abstract", "parole chiave": "keywords",
                    "didascalie": "legends", "limiti": "limitations"}
SIDES = {"top": "sopra", "bottom": "sotto", "left": "sinistro", "right": "destro"}
REVIEW_REGISTRIES = ("PROSPERO", "OSF", "INPLASY")
_VARIANTS = sorted(((v, k) for v, k in ms._HEADING_OF.items() if len(v) > 4 and k in ms.MAIN + ("highlights", "plain")),
                   key=lambda x: -len(x[0]))


def _name_in_rule(text: str) -> str | None:
    """The English section name written in a rule, as written ('Practical Applications', 'Perspective')."""
    for v, _ in _VARIANTS:
        hit = re.search(rf"\b{re.escape(v)}\b", text, re.I)
        if hit:
            return hit.group(0)
    return None


def _section_named(text: str) -> str | None:
    name = _name_in_rule(text)
    return ms.heading_of(name) if name else None


def _split_outside_parentheses(text: str) -> list[str]:
    out, depth, cur = [], 0, ""
    for ch in text:
        depth += ch == "("
        depth -= ch == ")"
        if ch in ",;" and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return [x for x in out if x]


def _merge(checks: list[Check]) -> list[Check]:
    """One row per check: the same check from several rules keeps the worst outcome and all the rules."""
    out: dict[str, Check] = {}
    for c in checks:
        prev = out.get(c.id)
        if prev is None:
            out[c.id] = c
            continue
        rules = [x for x in (prev.regola, c.regola) if x]
        pages = [str(x) for x in ((prev.fonte or {}).get("pagina"), (c.fonte or {}).get("pagina")) if x]
        worst = prev if ORDER.index(prev.esito) <= ORDER.index(c.esito) else c
        merged = Check(c.id, worst.gruppo, worst.titolo, worst.esito, worst.trovato,
                       " · ".join(dict.fromkeys(" · ".join(rules).split(" · "))) or None,
                       dict(worst.fonte or prev.fonte or {}), list(dict.fromkeys(prev.dettagli + c.dettagli)))
        if merged.fonte is not None and pages:
            merged.fonte["pagina"] = ", ".join(dict.fromkeys(", ".join(pages).split(", ")))
        out[c.id] = merged
    return list(out.values())


def check(m: Manuscript, jr: JournalRules, kind: str, conn: sqlite3.Connection | None = None,
          path: Path | None = None) -> list[Check]:
    run = _Run(m, jr, kind, conn)
    if jr.type_name is None:
        run.add("tipo", "Il file", "Tipo di articolo", "ko",
                f"la scheda della rivista non ha un tipo per: {KINDS[kind]}")
    for step in (run.file, run.length, run.abstract, run.structure, run.figures, run.references, run.statements,
                 run.others):
        step()
    from .numbers_audit import audit          # T-039: numbers and statistics (here to avoid a circular import)
    run.out += audit(m)
    from .reporting_checklists import audit as checklists      # T-040 part 1: reporting checklists
    run.out += checklists(m, kind)
    if kind == "revisione":                   # T-051b (EURYDIKE E1): flow, abstract, table, k/n, I², registration
        from .eurydike.review_audit import audit as review_audit
        run.out += review_audit(m, path)
    if conn is not None and _has_table(conn, "text_structure"):    # T-057: vs the journal's published articles
        from .journal_profile import compare
        run.out += compare(conn, m, jr)
    if conn is not None and _has_table(conn, "comment_label"):    # T-056: lessons from the user's own submissions
        from .submissions import compare as lessons_compare
        run.out += lessons_compare(conn, jr)
    return _merge(run.out)


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone() is not None


def counts(checks: list[Check]) -> dict[str, int]:
    return {OUTCOMES[k]: sum(1 for c in checks if c.esito == k) for k in ORDER}


def manuscript_summary(m: Manuscript) -> dict:
    """Numbers only (no text of the manuscript): safe for the log."""
    lay = m.doc.layout
    return {"impronta": m.doc.sha256[:12], "versione": ms.MANUSCRIPT_VERSION, "lingua": m.language,
            "tipo_stimato": m.kind_guess, "parole_totali": m.total_words, "parole_testo": m.main_words,
            "parole_abstract": m.abstract_words, "titoletti_abstract": len(m.abstract_labels),
            "parole_chiave": len(m.keywords), "sezioni": [ms.SECTION_NAMES.get(s.name, s.name) for s in m.sections
                                                          if s.name != "front"],
            "riferimenti": len(m.references), "riferimenti_con_doi": sum(1 for r in m.references if r.doi),
            "citazioni": m.citations.style + (f" ({m.citations.form})" if m.citations.form else ""),
            "figure": len(set(m.captions["figure"]) or set(m.mentions["figure"])),
            "tabelle": max(len(m.doc.tables), len(set(m.captions["table"]))),
            "immagini": m.doc.images, "revisioni_non_accettate": m.doc.revisions, "commenti": m.doc.comments,
            "carattere": lay.get("carattere"), "corpo_pt": lay.get("corpo_pt"), "interlinea": lay.get("interlinea"),
            "numeri_di_riga": lay.get("numeri_di_riga"), "numeri_di_pagina": lay.get("numeri_di_pagina")}


def run(conn: sqlite3.Connection, path: Path, journal_list: list[dict], wanted: list[str] | None,
        kind: str = "auto", *, ref_client=None, verify_refs: bool = False, local_dir: Path | None = None,
        matcher=None) -> dict:
    """Check one manuscript against one or more journals. Read-only. verify_refs (T-038): the references checked
    with ORPHEUS and Crossref (manuscript_refs; only DOIs and cited titles leave the PC)."""
    m = ms.read(path)
    refs_result = None
    if verify_refs:
        from . import manuscript_refs
        refs_result = manuscript_refs.run(m.references, conn, ref_client, local_dir)
    used_kind = m.kind_guess if kind == "auto" else kind
    journals, unknown = pick_journals(conn, journal_list, wanted)
    results = []
    for j in journals:
        jr = load_rules(conn, j, used_kind)
        checks = check(m, jr, used_kind, conn, path)
        results.append({"rivista": j["sigla"] or j["titolo"], "titolo": j["titolo"], "journal_id": j["id"],
                        "tipo_scheda": jr.type_name, "tipo_scheda_testo": jr.type_value,
                        "data_scheda": jr.card_date[:10], "data_linee_guida": jr.guidelines_date,
                        "conteggi": counts(checks), "controlli": [asdict(c) for c in checks]})
    from . import journal_match                   # T-040 parte 2 (D5): closeness to what each journal publishes
    affinity = journal_match.match(conn, m, journals, matcher)
    for r in results:
        r["affinita"] = affinity["riviste"].get(r["journal_id"])
    results.sort(key=lambda r: (r["conteggi"]["da correggere"], r["conteggi"]["da guardare"]))
    return {"versione": COMPLIANCE_VERSION, "tipo": used_kind, "tipo_testo": KINDS[used_kind],
            "tipo_stimato": m.kind_guess, "motivo_tipo": m.kind_reason, "scelto_da": "utente" if kind != "auto" else
            "stima", "manoscritto": manuscript_summary(m), "titolo": m.title, "riviste": results,
            "riviste_sconosciute": unknown, "verifica_riferimenti": refs_result,
            "affinita": {k: affinity[k] for k in ("versione", "modo", "nota")}, "nota": "Compatibilità con le regole delle riviste, non probabilità di "
                                                    "accettazione."}


def summary(result: dict, report: Path | None = None) -> dict:
    """What the command prints (and the log keeps): numbers and outcomes, no text of the manuscript."""
    rows = []
    for r in result["riviste"]:
        aff = r.get("affinita") or {}
        rows.append({"rivista": r["rivista"], "tipo_scheda": r["tipo_scheda"], "conteggi": r["conteggi"],
                     "vicinanza_temi": aff.get("vicinanza_temi"), "articoli_in_orpheus": aff.get("articoli_in_orpheus"),
                     "da_correggere": [c["titolo"] for c in r["controlli"] if c["esito"] == "ko"]})
    out = {"versione": result["versione"], "tipo": result["tipo_testo"], "scelto_da": result["scelto_da"],
           "affinita": (result.get("affinita") or {}).get("modo"),
           "manoscritto": result["manoscritto"], "riviste": rows, "riviste_sconosciute": result["riviste_sconosciute"],
           "nota": result["nota"]}
    if result.get("verifica_riferimenti"):
        from . import manuscript_refs
        out["verifica_riferimenti"] = manuscript_refs.public(result["verifica_riferimenti"])
    if report:
        out["rapporto"] = str(report)
    return out


# ---------------------------------------------------------------------------------------------------- the report
CSS = """body{font-family:Segoe UI,Arial,sans-serif;margin:24px auto;max-width:1100px;padding:0 16px;color:#1d2433;
background:#fbfaf7;line-height:1.45}h1{font-size:1.5rem;margin:0 0 4px}h2{font-size:1.2rem;margin:32px 0 6px;
border-bottom:2px solid #d8d3c8;padding-bottom:4px}h3{font-size:1rem;margin:18px 0 6px;color:#4a5160}
.sub{color:#5b6372;font-size:.92rem}table{border-collapse:collapse;width:100%;font-size:.92rem;margin:6px 0 12px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid #e4e0d8;vertical-align:top}th{background:#f1eee7}
.b{display:inline-block;padding:1px 8px;border-radius:10px;font-size:.8rem;font-weight:600;white-space:nowrap}
.ko{background:#fbe1dc;color:#8a1c0d}.guarda{background:#fdf1cf;color:#6b4e00}.ok{background:#dcefe0;color:#1e5b2e}
.na{background:#ebe9e4;color:#555}.det{color:#5b6372;font-size:.85rem;margin:2px 0 0;padding-left:16px}
.src{color:#6b7280;font-size:.8rem}.kpi{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}.kpi div{background:#fff;
border:1px solid #e4e0d8;border-radius:8px;padding:6px 10px;font-size:.85rem}.kpi b{display:block;font-size:1.05rem}
.note{background:#eef3fb;border-left:4px solid #6b8fc9;padding:8px 12px;font-size:.9rem}"""


def _e(x) -> str:
    return html.escape("" if x is None else str(x))


def render_html(result: dict, file_name: str, now: datetime) -> str:
    s = result["manoscritto"]
    out = ["<!doctype html><html lang='it'><head><meta charset='utf-8'>",
           "<meta name='viewport' content='width=device-width,initial-scale=1'>",
           f"<title>Controllo del manoscritto</title><style>{CSS}</style></head><body>",
           "<h1>Controllo del manoscritto</h1>",
           f"<div class='sub'>{_e(file_name)} · impronta {_e(s['impronta'])} · {now:%Y-%m-%d %H:%M} · "
           f"{_e(COMPLIANCE_VERSION)}, {_e(s['versione'])}</div>"]
    if result.get("titolo"):
        out.append(f"<p><b>{_e(result['titolo'])}</b></p>")
    out.append(f"<p class='note'>Tipo di articolo: <b>{_e(result['tipo_testo'])}</b> "
               f"({'scelto da te' if result['scelto_da'] == 'utente' else 'stimato: ' + _e(result['motivo_tipo'])}). "
               f"{_e(result['nota'])} Il rapporto resta su questo PC.</p>")
    kpi = [("Parole del testo", s["parole_testo"]), ("Parole dell'abstract", s["parole_abstract"]),
           ("Parole chiave", s["parole_chiave"]), ("Riferimenti", s["riferimenti"]),
           ("con DOI", s["riferimenti_con_doi"]), ("Figure", s["figure"]), ("Tabelle", s["tabelle"]),
           ("Citazioni", s["citazioni"]), ("Lingua", s["lingua"] or "?"),
           ("Formato", f"{s['carattere'] or '?'}, {_fmt(s['corpo_pt'])} pt, {s['interlinea']}")]
    out.append("<div class='kpi'>" + "".join(f"<div>{_e(k)}<b>{_e(_fmt(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v)}</b></div>"
                                             for k, v in kpi) + "</div>")
    out.append(f"<div class='sub'>Sezioni riconosciute: {_e(', '.join(s['sezioni']) or 'nessuna')}</div>")
    if len(result["riviste"]) > 1:
        out.append("<h2>Riviste a confronto</h2><table><tr><th>Rivista</th><th>Tipo nella scheda</th>"
                   "<th>Vicinanza ai temi</th><th>SJR (quartile)</th>"
                   "<th>Da correggere</th><th>Da guardare</th><th>Conformi</th></tr>")
        for r in result["riviste"]:
            c = r["conteggi"]
            a = r.get("affinita") or {}
            met = a.get("metriche") or {}
            near = "—" if a.get("vicinanza_temi") is None else f"{_fmt(a['vicinanza_temi'])}/100"
            if a.get("incerta"):
                near += f" <span class='src'>(solo {a.get('articoli_in_orpheus')} articoli in ORPHEUS)</span>"
            out.append(f"<tr><td><a href='#{_e(r['journal_id'])}'>{_e(r['rivista'])}</a> <span class='src'>"
                       f"{_e(r['titolo'])}</span></td><td>{_e(r['tipo_scheda'] or '—')}</td><td>{near}</td>"
                       f"<td>{_e(_fmt(met.get('sjr')) if met.get('sjr') is not None else '—')}"
                       f"{' (' + _e(met['quartile']) + ')' if met.get('quartile') else ''}</td>"
                       f"<td><span class='b ko'>{c['da correggere']}</span></td><td><span class='b guarda'>"
                       f"{c['da guardare']}</span></td><td><span class='b ok'>{c['conforme']}</span></td></tr>")
        aff = result.get("affinita") or {}
        out.append("</table><div class='sub'>Ordinate per numero di cose da correggere. Vicinanza ai temi: "
                   f"{_e(aff.get('nota') or '')} ({_e(aff.get('versione') or '')}). SJR e quartile: SCImago. "
                   "Non è una probabilità di accettazione.</div>")
    for r in result["riviste"]:
        out.append(f"<h2 id='{_e(r['journal_id'])}'>{_e(r['titolo'])}</h2>")
        out.append(f"<div class='sub'>Tipo nella scheda: {_e(r['tipo_scheda'] or 'non trovato')} · scheda del "
                   f"{_e(r['data_scheda'])} · linee guida del {_e(r['data_linee_guida'] or '?')}</div>")
        a = r.get("affinita") or {}
        if a.get("articoli_simili"):
            near = "" if a.get("vicinanza_temi") is None else f"vicinanza {_fmt(a['vicinanza_temi'])}/100; "
            out.append(f"<p class='sub'>Temi: {near}articoli più vicini pubblicati da questa rivista (in ORPHEUS): "
                       + "; ".join(f"{_e(x['titolo'])} ({_e(x['anno'])})" for x in a["articoli_simili"]) + "</p>")
        for grp in GROUPS:
            rows = [c for c in r["controlli"] if c["gruppo"] == grp]
            if not rows:
                continue
            rows.sort(key=lambda c: ORDER.index(c["esito"]))
            out.append(f"<h3>{_e(grp)}</h3><table><tr><th style='width:22%'>Controllo</th><th style='width:13%'>Esito"
                       "</th><th>Nel manoscritto</th><th style='width:32%'>Regola della rivista</th></tr>")
            for c in rows:
                det = "".join(f"<div class='det'>• {_e(d)}</div>" for d in c["dettagli"][:30])
                src = c["fonte"] or {}
                where = f" <span class='src'>(pag. {_e(src.get('pagina'))})</span>" if src.get("pagina") else ""
                out.append(f"<tr><td>{_e(c['titolo'])}</td><td><span class='b {c['esito']}'>{OUTCOMES[c['esito']]}"
                           f"</span></td><td>{_e(c['trovato'])}{det}</td><td>{_e(c['regola'] or '—')}{where}</td></tr>")
            out.append("</table>")
    vr = result.get("verifica_riferimenti")
    if vr:
        c = vr["conteggi"]
        out.append(f"<h2>Riferimenti verificati</h2><div class='sub'>Regole {_e(vr['regole'])} (anno ±1, primo autore, "
                   f"titolo) · {_e(vr['data'])} · richieste a Crossref {vr['richieste_crossref']}, dalla cache "
                   f"{vr['dalla_cache']}. A Crossref vanno solo i DOI e i titoli dei lavori citati.</div>")
        out.append("<div class='kpi'>" + "".join(f"<div>{_e(k)}<b>{v}</b></div>" for k, v in c.items()) + "</div>")
        if vr.get("interrotta"):
            out.append(f"<p class='note'>Verifica interrotta: {_e(vr['interrotta'])}</p>")
        badge = {"verified": "ok", "mismatch": "ko", "not_found": "guarda", "unverified": "na"}
        out.append("<table><tr><th style='width:5%'>N.</th><th style='width:12%'>Esito</th><th>Riferimento</th>"
                   "<th style='width:30%'>Motivo</th><th style='width:16%'>Fonte</th></tr>")
        for r in sorted(vr["riferimenti"], key=lambda r: (["mismatch", "not_found", "unverified", "verified"]
                                                            .index(r["esito"]), r["n"])):
            doi = r["doi_trovato"] or r["doi"]
            src = f"{_e(r['fonte'])}<br><span class='src'>{_e(r['data'])}</span>" if r["fonte"] else "—"
            out.append(f"<tr><td>{_e(r['numero'] or r['n'] + 1)}</td><td><span class='b {badge[r['esito']]}'>"
                       f"{_e(r['esito_testo'])}</span></td><td>{_e(r['testo'])}"
                       + (f"<div class='src'>DOI {_e(doi)}</div>" if doi else "") +
                       f"</td><td>{_e('; '.join(r['motivi']))}</td><td>{src}</td></tr>")
        out.append("</table>")
    out.append("<p class='src'>Le regole vengono dalle schede delle riviste controllate a mano (journal-cards-v1); "
               "la pagina è quella del PDF delle linee guida. "
               + ("I riferimenti sono confrontati con ORPHEUS e Crossref (sopra)." if vr else
                  "I riferimenti non sono stati verificati con Crossref (manoscritto.cmd lo chiede).")
               + "</p></body></html>")
    return "\n".join(out)


def last_reference_check(home: Path, fingerprint: str) -> dict | None:
    """The newest saved reference check (T-038) of the manuscript with this fingerprint, from its JSON reports in
    rapporti/manoscritti; no network (the dashboard shows it)."""
    folder = reports_dir(home)
    for p in sorted(folder.glob(f"controllo_{fingerprint[:8]}_*.json"), reverse=True) if folder.is_dir() else []:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("manoscritto", {}).get("impronta") == fingerprint and data.get("verifica_riferimenti"):
            return data["verifica_riferimenti"]
    return None


def reports_dir(home: Path) -> Path:
    return Path(home) / REPORTS_DIR / REPORTS


def write_report(result: dict, home: Path, file_name: str, now: datetime | None = None) -> Path:
    now = now or datetime.now()
    folder = reports_dir(home)
    folder.mkdir(parents=True, exist_ok=True)
    base = folder / f"controllo_{result['manoscritto']['impronta'][:8]}_{now:%Y-%m-%d_%H%M%S}"
    path = base.with_suffix(".html")
    path.write_text(render_html(result, file_name, now), encoding="utf-8")
    base.with_suffix(".json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return path
