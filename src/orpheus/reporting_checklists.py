"""T-040 part 1 (B4): reporting checklists looked for in the text of a manuscript (checklists-v1, deterministic).

Which checklist: SPIRIT (2013, or 2025 when cited) for the protocol of a trial or study and PRISMA-P 2015 for the
protocol of a review (kind "protocollo", 2026-10-17); PRISMA 2020 for a systematic review or meta-analysis (the kind
chosen for the check), CONSORT for a
randomised trial (words "randomised/randomized" in the title or abstract, or CONSORT cited), STROBE for an observational
study (cohort, case-control, cross-sectional, or STROBE cited); otherwise no checklist (nothing is added).
For every item: present when its key words are in the expected sections (the whole text when those sections are
missing). The item names are short summaries in our own words, not the texts of the checklists. Outcomes: "conforme"
when every item is found, otherwise "da guardare" with the missing items: key words never prove that an item is
reported well, nor that it is missing (R10: the details carry only item and section names, no text of the manuscript).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .compliance import Check
from .manuscript import Manuscript

CHECKLISTS_VERSION = "checklists-v1"
GROUP = "Checklist di reporting"
MAX_DETAILS = 14

ALIASES = {"methods": ("methods", "ethics", "registration"),
           "results": ("results", "results_discussion", "tables", "legends"),
           "discussion": ("discussion", "results_discussion", "limitations", "conclusion", "practical"),
           "introduction": ("introduction",),
           "title": (), "abstract": (), "all": ()}
SECTION_IT = {"title": "titolo", "abstract": "abstract", "introduction": "Introduction", "methods": "Methods",
              "results": "Results", "discussion": "Discussion", "all": "tutto il testo"}


@dataclass(frozen=True)
class Item:
    id: str
    name: str                 # our own short summary
    where: tuple[str, ...]    # title, abstract, introduction, methods, results, discussion, all
    pattern: str
    since: str | None = None  # "2025": only for CONSORT 2025


def _i(id_, name, where, pattern, since=None) -> Item:
    return Item(id_, name, tuple(where.split()), pattern, since)


_DATES = (r"(?:January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|"
          r"Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.?\s+(?:19|20)\d{2}|(?:between|from)\s+(?:19|20)\d{2}\s+(?:and|to)\s+"
          r"(?:19|20)\d{2}|recruitment period|data collection (?:took place|occurred|was conducted)")
_STATS = r"statistic|ANOVA|regression|mixed[- ]model|t[- ]tests?\b|Mann[- ]Whitney|Wilcoxon|SPSS|\bR software|Stata"
_FUNDING = r"\bfund(?:ing|ed)\b|grant|financial support|no (?:specific )?(?:funding|grant)"

CONSORT = (
    _i("titolo", "Titolo che dice 'randomizzato'", "title", r"randomi[sz]"),
    _i("obiettivi", "Obiettivi o ipotesi", "introduction abstract", r"\baims?\b|objective|hypothes|purpose"),
    _i("disegno", "Disegno dello studio (parallelo, crossover, rapporto di allocazione)", "methods",
       r"parallel|cross-?over|factorial|allocation ratio|randomi[sz]ed controlled|two-arm|three-arm"),
    _i("criteri", "Criteri di inclusione ed esclusione", "methods", r"inclusion|exclusion|eligib"),
    _i("contesto", "Dove e come sono stati reclutati", "methods", r"recruit|setting|conducted at|took place"),
    _i("intervento", "Interventi descritti per ogni gruppo", "methods",
       r"intervention|training (?:program|programme|protocol|sessions?)|control group|placebo"),
    _i("esiti", "Esito primario (ed eventuali secondari) definiti", "methods",
       r"primary (?:outcome|end-?point)|secondary outcome|outcome measures?"),
    _i("numerosita", "Calcolo della numerosità del campione", "methods",
       r"sample size|power (?:analysis|calculation)|a priori power|G\s?\*\s?Power"),
    _i("sequenza", "Come è stata generata la sequenza casuale", "methods",
       r"computer[- ]generated|random (?:number|sequence|allocation)|block randomi|stratified randomi|randomi[sz]ation "
       r"(?:sequence|list|software|was)|randomly (?:assigned|allocated)"),
    _i("occultamento", "Occultamento dell'allocazione", "methods",
       r"conceal|sealed|opaque|central(?:ised|ized)? (?:allocation|randomi)"),
    _i("cecita", "Cecità (chi non sapeva il gruppo)", "methods", r"blind|mask"),
    _i("statistica", "Metodi statistici", "methods", _STATS + r"|intention[- ]to[- ]treat"),
    _i("date", "Date del reclutamento e del follow-up", "methods results", _DATES),
    _i("flusso", "Flusso dei partecipanti (diagramma, persi, esclusi)", "results",
       r"flow (?:diagram|chart)|CONSORT diagram|screened|withdr[ae]w|lost to follow|dropped out|drop-?outs?"),
    _i("basale", "Caratteristiche di partenza dei gruppi", "results", r"baseline|Table 1\b|demographic"),
    _i("analizzati", "Numero di partecipanti analizzati per gruppo", "results",
       r"analy[sz]ed|intention[- ]to[- ]treat|per[- ]protocol|included in the analys"),
    _i("effetto", "Dimensione dell'effetto con la sua precisione", "results",
       r"95\s*%\s*(?:CI|confidence)|confidence intervals?|effect sizes?|Cohen'?s|Hedges"),
    _i("danni", "Eventi avversi o danni", "results discussion", r"adverse|harms?\b|side[- ]effects?|injur"),
    _i("limiti", "Limiti dello studio", "discussion", r"limitation"),
    _i("generalizzabilita", "Generalizzabilità dei risultati", "discussion",
       r"generali[sz]|external validity|applicab|transferab"),
    _i("registrazione", "Registrazione del trial", "all",
       r"\bregist(?:ered|ration|ry)\b|NCT\d{8}|ISRCTN|ACTRN|DRKS|ChiCTR|CTRI/|EudraCT"),
    _i("protocollo", "Dove si trova il protocollo", "all", r"(?:study|trial) protocol|protocol (?:was |has been )?"
       r"(?:published|available|registered)"),
    _i("finanziamento", "Finanziamento e ruolo del finanziatore", "all", _FUNDING),
    _i("dati", "Condivisione dei dati", "all", r"data (?:availab|sharing)|data (?:are|is|will be) available", "2025"),
    _i("conflitti", "Conflitti di interesse", "all", r"conflicts? of interests?|competing interests?|disclos", "2025"),
)

STROBE = (
    _i("disegno_titolo", "Disegno dello studio nel titolo o nell'abstract", "title abstract",
       r"cohort|case[- ]control|cross[- ]sectional|observational|prospective|retrospective|longitudinal"),
    _i("obiettivi", "Obiettivi o ipotesi", "introduction abstract", r"\baims?\b|objective|hypothes|purpose"),
    _i("contesto", "Luogo, periodo e date (reclutamento, esposizione, follow-up)", "methods", _DATES),
    _i("partecipanti", "Criteri di scelta dei partecipanti", "methods", r"inclusion|exclusion|eligib|recruit"),
    _i("variabili", "Esiti, esposizioni e possibili confondenti definiti", "methods",
       r"outcome|exposure|confound|covariate|predictor"),
    _i("misure", "Fonti dei dati e modo di misurare", "methods",
       r"measured|assessed|questionnaire|recorded|instrument|device"),
    _i("bias", "Possibili distorsioni (bias) e come sono state affrontate", "methods discussion", r"\bbias"),
    _i("numerosita", "Come si è arrivati alla numerosità", "methods",
       r"sample size|power (?:analysis|calculation)|G\s?\*\s?Power|all eligible"),
    _i("statistica", "Metodi statistici (anche per i confondenti)", "methods", _STATS + r"|adjust"),
    _i("mancanti", "Dati mancanti e come sono stati trattati", "methods results", r"missing|imput"),
    _i("flusso", "Numeri dei partecipanti a ogni fase (inclusi, esclusi)", "results",
       r"flow (?:diagram|chart)|excluded|were included|lost to follow|response rate|participated"),
    _i("descrittivi", "Caratteristiche dei partecipanti", "results", r"characteristics|Table 1\b|demographic|baseline"),
    _i("risultati", "Stime principali (grezze e aggiustate) con la precisione", "results",
       r"95\s*%\s*(?:CI|confidence)|confidence intervals?|odds ratio|hazard ratio|risk ratio|\bOR\b|\bHR\b|adjusted"),
    _i("limiti", "Limiti dello studio", "discussion", r"limitation"),
    _i("generalizzabilita", "Generalizzabilità dei risultati", "discussion",
       r"generali[sz]|external validity|applicab|transferab"),
    _i("finanziamento", "Finanziamento", "all", _FUNDING),
)

PRISMA = (
    _i("titolo", "Titolo che dice revisione sistematica o meta-analisi", "title",
       r"systematic review|meta[- ]?analys|scoping review|umbrella review"),
    _i("obiettivi", "Obiettivi o domanda della revisione", "introduction abstract",
       r"\baims?\b|objective|research question|purpose|PICO"),
    _i("registrazione", "Registrazione e protocollo", "all",
       r"PROSPERO|CRD\s?\d{6,}|\bOSF\b|INPLASY|regist(?:ered|ration)|protocol"),
    _i("criteri", "Criteri di inclusione degli studi", "methods", r"inclusion|eligib|PICO|exclusion"),
    _i("fonti", "Banche dati e altre fonti consultate", "methods",
       r"PubMed|MEDLINE|Embase|Scopus|Web of Science|SPORTDiscus|Cochrane|CINAHL|PsycINFO|Google Scholar"),
    _i("strategia", "Strategia di ricerca con le date", "methods",
       r"(?:search|searched)[^.]{0,200}?(?:inception|up to|until|through|(?:19|20)\d{2})|search strings?|"
       r"Boolean|search terms"),
    _i("selezione", "Selezione degli studi (chi, quanti revisori, indipendenti)", "methods",
       r"independent(?:ly)?|two (?:authors|reviewers|investigators)|screen"),
    _i("estrazione", "Raccolta dei dati dagli studi", "methods", r"extract"),
    _i("rischio", "Rischio di bias degli studi inclusi", "methods",
       r"risk of bias|RoB\s?2|ROBINS|PEDro|Newcastle[- ]Ottawa|quality assessment|methodological quality|TESTEX"),
    _i("sintesi", "Misure dell'effetto e metodi di sintesi", "methods",
       r"meta-?analy|random[- ]effects?|fixed[- ]effects?|pooled|narrative synthesis|standardi[sz]ed mean "
       r"difference|\bSMD\b|heterogeneity|I\s?²|I2\b"),
    _i("bias_pubblicazione", "Bias di pubblicazione o di reporting", "methods results",
       r"publication bias|reporting bias|funnel|Egger|small[- ]study"),
    _i("certezza", "Certezza delle prove (es. GRADE)", "methods results", r"GRADE|certainty of (?:the )?evidence"),
    _i("flusso", "Flusso della selezione (diagramma con i numeri)", "results",
       r"flow (?:diagram|chart)|PRISMA (?:flow|diagram)|records (?:were )?identified|records screened|full[- ]text"),
    _i("caratteristiche", "Caratteristiche degli studi inclusi", "results",
       r"characteristics of (?:the )?(?:included )?studies|study characteristics|Table 1\b|included studies"),
    _i("risultati", "Risultati delle sintesi con la precisione", "results",
       r"95\s*%\s*(?:CI|confidence)|confidence intervals?|forest plot|pooled|\bSMD\b"),
    _i("limiti", "Limiti delle prove e della revisione", "discussion", r"limitation"),
    _i("finanziamento", "Finanziamento", "all", _FUNDING),
    _i("conflitti", "Conflitti di interesse", "all", r"conflicts? of interests?|competing interests?|disclos"),
    _i("dati", "Disponibilità di dati, codice e materiali", "all", r"data (?:availab|sharing)|available (?:from|on|at)|"
       r"supplementary"),
)

# Protocols (2026-10-17): SPIRIT for the protocol of a trial or study, PRISMA-P for the protocol of a review. Short
# summaries in our own words; a protocol is written in the future tense, so the patterns accept "will be".
SPIRIT = (
    _i("titolo", "Titolo che dice protocollo e disegno", "title", r"protocol"),
    _i("registrazione", "Registro e numero (o dove sarà registrato)", "all",
       r"\bregist(?:ered|ration|ry)\b|NCT\d{8}|ISRCTN|ACTRN|DRKS|ChiCTR|CTRI/|EudraCT"),
    _i("versione", "Versione e data del protocollo", "all", r"protocol version|version\s?\d|version date"),
    _i("finanziamento", "Finanziamento e ruolo del finanziatore", "all", _FUNDING),
    _i("razionale", "Razionale e contesto", "introduction", r"rationale|background|gap|little is known|unclear"),
    _i("obiettivi", "Obiettivi o ipotesi", "introduction abstract", r"\baims?\b|objective|hypothes|purpose"),
    _i("disegno", "Disegno (parallelo, crossover, rapporto di allocazione, superiorità…)", "methods",
       r"parallel|cross-?over|factorial|allocation ratio|superiority|non-?inferiority|randomi[sz]ed|cohort|"
       r"cross-sectional|single-arm|pilot"),
    _i("contesto", "Dove si farà lo studio (centri, paesi)", "methods", r"setting|cent(?:re|er)s?\b|conducted at|will take place|clubs?|schools?|universit"),
    _i("criteri", "Criteri di inclusione ed esclusione", "methods", r"inclusion|exclusion|eligib"),
    _i("intervento", "Interventi di ogni gruppo, con abbastanza dettagli da ripeterli", "methods",
       r"intervention|training (?:program|programme|protocol|sessions?)|control group|placebo|exposure"),
    _i("esiti", "Esito primario e secondari, con i tempi di misura", "methods",
       r"primary (?:outcome|end-?point)|secondary outcomes?|outcome measures?"),
    _i("cronologia", "Calendario dei partecipanti (arruolamento, interventi, misure)", "methods all",
       r"timeline|schedule of (?:enrolment|assessments)|time points?|baseline and|SPIRIT (?:figure|diagram)|weeks? \d+"),
    _i("numerosita", "Calcolo della numerosità del campione", "methods",
       r"sample size|power (?:analysis|calculation)|a priori power|G\s?\*\s?Power"),
    _i("reclutamento", "Come si raggiungerà la numerosità (reclutamento)", "methods", r"recruit"),
    _i("allocazione", "Sequenza casuale e occultamento dell'allocazione", "methods",
       r"random (?:number|sequence|allocation)|computer[- ]generated|conceal|sealed|opaque|block randomi|stratified"),
    _i("cecita", "Cecità (chi non saprà il gruppo)", "methods", r"blind|mask"),
    _i("raccolta", "Raccolta e gestione dei dati", "methods", r"data (?:collection|management|entry)|case report form|REDCap|stored"),
    _i("statistica", "Metodi statistici (analisi principali, dati mancanti)", "methods", _STATS + r"|intention[- ]to[- ]treat|missing"),
    _i("monitoraggio", "Monitoraggio dei dati e analisi intermedie", "methods all",
       r"data monitoring|DMC\b|DSMB|interim analys|stopping rules?|auditing"),
    _i("danni", "Eventi avversi: come saranno raccolti e riportati", "methods all", r"adverse|harms?\b|side[- ]effects?|injur"),
    _i("etica", "Approvazione etica (o quando sarà chiesta)", "all", r"ethic|institutional review board|\bIRB\b"),
    _i("emendamenti", "Come saranno comunicate le modifiche al protocollo", "all", r"amendment|modification|changes to the protocol"),
    _i("consenso", "Consenso informato (chi lo raccoglie, come)", "all", r"informed consent|consent"),
    _i("riservatezza", "Riservatezza dei dati personali", "all", r"confidential|anonymi[sz]|pseudonym|de-?identif|GDPR|privacy"),
    _i("diffusione", "Come saranno diffusi i risultati", "all", r"dissemina|publish|publication|conference"),
    _i("conflitti", "Conflitti di interesse", "all", r"conflicts? of interests?|competing interests?|disclos"),
)

PRISMA_P = (
    _i("titolo", "Titolo che dice protocollo di revisione sistematica o meta-analisi", "title",
       r"protocol.*(?:review|meta[- ]?analys)|(?:review|meta[- ]?analys).*protocol"),
    _i("registrazione", "Registro e numero (es. PROSPERO)", "all", r"PROSPERO|CRD\s?\d{6,}|\bOSF\b|INPLASY|regist(?:ered|ration)"),
    _i("autori", "Contributi degli autori e garante", "all", r"contribut|guarantor"),
    _i("emendamenti", "Come saranno registrate le modifiche", "all", r"amendment|modification|changes to the protocol|deviation"),
    _i("finanziamento", "Finanziamento e ruolo del finanziatore", "all", _FUNDING),
    _i("razionale", "Razionale (cosa si sa già)", "introduction", r"rationale|background|gap|little is known|unclear"),
    _i("obiettivi", "Domanda esplicita (partecipanti, interventi, confronti, esiti)", "introduction abstract",
       r"\baims?\b|objective|research question|PICO|purpose"),
    _i("criteri", "Criteri di inclusione (disegni, partecipanti, anni, lingue)", "methods", r"inclusion|eligib|PICO|exclusion"),
    _i("fonti", "Banche dati e altre fonti, con le date previste", "methods",
       r"PubMed|MEDLINE|Embase|Scopus|Web of Science|SPORTDiscus|Cochrane|CINAHL|PsycINFO|Google Scholar"),
    _i("strategia", "Stringa di ricerca di almeno una banca dati", "methods all",
       r"search strings?|Boolean|search terms|search strategy|\bAND\b.*\bOR\b|\bOR\b.*\bAND\b"),
    _i("gestione", "Gestione dei record (programma, doppioni)", "methods", r"Rayyan|Covidence|EndNote|Zotero|Mendeley|duplicat|reference manager"),
    _i("selezione", "Selezione degli studi (due revisori indipendenti, disaccordi)", "methods",
       r"independent(?:ly)?|two (?:authors|reviewers|investigators)|screen|disagreement|consensus"),
    _i("estrazione", "Raccolta dei dati (modulo, chi, come)", "methods", r"extract|data collection form"),
    _i("voci", "Dati da raccogliere ed esiti, con le priorità", "methods", r"data items|outcomes?|primary outcome|prioriti"),
    _i("rischio", "Rischio di bias degli studi", "methods",
       r"risk of bias|RoB\s?2|ROBINS|PEDro|Newcastle[- ]Ottawa|quality assessment|methodological quality|TESTEX"),
    _i("sintesi", "Sintesi: criteri per la meta-analisi, modelli, eterogeneità, analisi aggiuntive", "methods",
       r"meta-?analy|random[- ]effects?|fixed[- ]effects?|pooled|narrative synthesis|heterogeneity|I\s?²|I2\b|subgroup|sensitivity"),
    _i("metabias", "Bias di pubblicazione o di reporting", "methods", r"publication bias|reporting bias|funnel|Egger|small[- ]study"),
    _i("certezza", "Certezza delle prove (es. GRADE)", "methods", r"GRADE|certainty of (?:the )?evidence"),
)

_RCT = re.compile(r"randomi[sz]", re.I)
_OBS = re.compile(r"cohort|case[- ]control|cross[- ]sectional|observational", re.I)


def choose(m: Manuscript, kind: str) -> str | None:
    cited = " ".join(m.guidelines).upper()
    if kind == "protocollo":
        from .manuscript import REVIEW_WORDS
        if REVIEW_WORDS.search(f"{m.title or ''}\n{m.abstract_text}") or "PRISMA-P" in cited or "PRISMA P" in cited:
            return "PRISMA-P 2015"
        return "SPIRIT 2025" if re.search(r"SPIRIT\s?2025", _full(m)) else "SPIRIT 2013"
    if kind == "revisione":
        return "PRISMA 2020"
    head = f"{m.title or ''}\n{m.abstract_text}"
    if "CONSORT" in cited or _RCT.search(head):
        return "CONSORT 2025" if re.search(r"CONSORT\s?2025", _full(m)) else "CONSORT 2010"
    if "STROBE" in cited or _OBS.search(head):
        return "STROBE"
    return None


def _full(m: Manuscript) -> str:
    return "\n".join(p.text for p in m.doc.paragraphs)


def items_of(name: str) -> tuple[Item, ...]:
    if name.startswith("CONSORT"):
        return tuple(x for x in CONSORT if x.since is None or name.endswith(x.since))
    if name.startswith("SPIRIT"):
        return SPIRIT
    return {"STROBE": STROBE, "PRISMA 2020": PRISMA, "PRISMA-P 2015": PRISMA_P}[name]


class _Areas:
    def __init__(self, m: Manuscript):
        self.m = m
        self.tables = "\n".join("\n".join(t) for t in m.doc.tables)
        self.cache: dict[str, str | None] = {}

    def text(self, where: str) -> str | None:
        """The text of an area, or None when the manuscript has no such section."""
        if where in self.cache:
            return self.cache[where]
        m = self.m
        if where == "title":
            out = m.title or None
        elif where == "abstract":
            out = m.abstract_text or None
        elif where == "all":
            out = _full(m) + "\n" + self.tables
        else:
            idx = [i for s in m.sections if s.name in ALIASES[where] for i in s.paras]
            out = m.text_of(idx) if idx else None
            if out is not None and where == "results":
                out += "\n" + self.tables
        self.cache[where] = out
        return out

    def found(self, item: Item) -> tuple[bool, bool]:
        """(found, searched the whole text because the expected sections are missing)."""
        texts = [self.text(w) for w in item.where]
        fallback = all(t is None for t in texts)
        if fallback:
            texts = [_full(self.m) + "\n" + self.tables]
        rx = re.compile(item.pattern, re.I)
        return any(t and rx.search(t) for t in texts), fallback


def audit(m: Manuscript, kind: str) -> list[Check]:
    name = choose(m, kind)
    if name is None:
        return []
    areas = _Areas(m)
    items = items_of(name)
    missing, fallback = [], 0
    for it in items:
        ok, fb = areas.found(it)
        fallback += fb
        if not ok:
            where = " o ".join(SECTION_IT[w] for w in it.where)
            missing.append(f"{it.name} (cercata in: {where})")
    n = len(items)
    cited = [g for g in m.guidelines if g.upper().startswith(name.split()[0])]
    why = "citata nel testo" if cited else ("tipo scelto per il controllo" if name.startswith(("PRISMA", "SPIRIT"))
                                           else "dal disegno nel titolo o nell'abstract")
    found = f"{name} ({why}): {n - len(missing)}/{n} voci trovate con parole chiave"
    if fallback:
        found += f"; {fallback} cercate in tutto il testo (sezioni attese non trovate)"
    details = missing[:MAX_DETAILS] + ([f"... e altre {len(missing) - MAX_DETAILS}"] if len(missing) > MAX_DETAILS
                                       else [])
    if missing:
        found += ": controlla le voci che mancano e allega la checklist compilata"
    return [Check("checklist_" + name.split()[0].lower().replace("-", "_"), GROUP, f"Checklist {name}", "guarda" if missing else "ok",
                  found, None, None, details)]
