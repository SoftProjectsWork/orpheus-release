"""Fase 7 (T-046): validation of ORPHEUS on a real, authorised case (validation-v1, deterministic).

The case chosen by the user on 2026-10-17 is a manuscript of the user for one journal (criteria B and C of
EVALUATION_REPORTS/2026-10-17_piano-fase-7.md; thresholds confirmed by the user on 2026-10-17 13:05).

Two steps, both read-only on the database:
1. prepare: one control file (CSV, ';', UTF-8 with BOM, Excel-proof) in
   rapporti/manoscritti/validazione/<impronta>_<sigla>/ with one row per thing ORPHEUS said:
   B1 every reference checked with ORPHEUS and Crossref (manoscritto.cmd, risposta S), B2 every compliance warning,
   B4 every warning of "Numeri e statistiche", C1/C2 every observation of the accepted simulated review. The user
   writes S or N in "giudizio" (B: ORPHEUS is right; C: the observation is useful) and, for C, S or N in
   "inventato" (the observation states something that is not in the manuscript). mancati.txt: problems the user
   found and ORPHEUS did not (B3).
2. score: the filled file (any CSV of the folder ending in _compilato.csv, or the control file itself once filled)
   -> precision with the Wilson 95% interval for each criterion, compared with the thresholds; C3 and C4 from the
   accepted letter and response; E2 and E4 from `orpheus salute`. risultati.json in the folder.

R10: the control file quotes the manuscript, so it stays on the PC; the output of the command (the log, GitHub)
carries counts and outcomes only. The gate needs the signature of the user anyway: this module never says
"superato" for the gate, only for each criterion."""
from __future__ import annotations

import csv
import io
import json
import math
import re
from datetime import datetime
from pathlib import Path

from . import benchmark, compliance, peer_review, submission_letters as sl

VALIDATION_VERSION = "validation-v1"
FOLDER = "validazione"
CONTROL = "controllo_validazione.csv"
FILLED_SUFFIX = "_compilato.csv"
MISSED = "mancati.txt"
RESULTS = "risultati.json"
README = "LEGGIMI.txt"
COLUMNS = ["n", "criterio", "id", "gruppo", "cosa_dice_orpheus", "dove", "dettagli", "domanda", "giudizio",
           "inventato", "note"]
MISSED_TEMPLATE = ("# B3 - problemi del manoscritto che hai trovato tu e che ORPHEUS non ha segnalato.\n"
                   "# Uno per riga (bastano poche parole). Se non ce ne sono, scrivi: nessuno\n"
                   "# Togli la riga DA COMPILARE quando hai finito.\nDA COMPILARE\n")
NUMBERS_GROUP = "Numeri e statistiche"
REMINDER_PREFIXES = ("invio_", "regola_")        # reminders for the submission, not problems of the manuscript

# Thresholds of the plan, confirmed by the user on 2026-10-17 ("Sì, come proposte").
THRESHOLDS = {
    "B1": {"titolo": "Riferimenti verificati correttamente", "precisione_min": 0.95, "falsi_verificati_max": 0},
    "B2": {"titolo": "Problemi di compliance veri", "precisione_min": 0.90},
    "B3": {"titolo": "Problemi di compliance mancati", "max": 1},
    "B4": {"titolo": "Numeri e statistiche", "precisione_min": 0.80},
    "C1": {"titolo": "Commenti della revisione pertinenti", "precisione_min": 0.70},
    "C2": {"titolo": "Nessuna affermazione inventata nei commenti", "max": 0},
    "C3": {"titolo": "Lettera conforme alla rivista", "max": 0},
    "C4": {"titolo": "Risposta ai revisori completa", "precisione_min": 1.0},
    "E2": {"titolo": "Copia di sicurezza e ripristino"},
    "E4": {"titolo": "Budget zero"},
}
QUESTIONS = {"B1": "L'esito di ORPHEUS per questo riferimento è giusto? (S/N)",
             "B2": "La segnalazione è vera (c'è davvero da guardare/correggere)? (S/N)",
             "B4": "La segnalazione sui numeri è vera? (S/N)",
             "C1": "L'osservazione è utile? (S/N) - inventato: dice cose che nel manoscritto non ci sono? (S/N)"}


class ValidationError(Exception):
    pass


def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float] | None:
    """Wilson score interval for k successes out of n (None when n = 0)."""
    if n <= 0:
        return None
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)


def folder(home: Path, fingerprint: str, sigla: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", sigla or "rivista")
    return compliance.reports_dir(home) / FOLDER / f"{fingerprint[:12]}_{safe}"


def _short(text, n: int = 300) -> str:
    t = " ".join(str(text or "").split())
    return t if len(t) <= n else t[: n - 1] + "…"


# --------------------------------------------------------------------------------------------- 1. the control file
def rows_for(home: Path, result: dict) -> tuple[list[dict], dict]:
    """The rows of the control file for one manuscript and ONE journal (result of compliance.run)."""
    fp = result["manoscritto"]["impronta"]
    j = result["riviste"][0]
    rows, sources = [], {}
    refs = compliance.last_reference_check(home, fp)
    sources["B1"] = "verifica dei riferimenti di manoscritto.cmd" if refs else None
    for r in (refs or {}).get("riferimenti", []):
        rows.append({"criterio": "B1", "id": f"rif_{r.get('numero') or r.get('n')}", "gruppo": "Riferimenti",
                     "cosa_dice_orpheus": f"{r.get('esito_testo') or r.get('esito')}"
                                          + (f" (DOI {r['doi_trovato']})" if r.get("doi_trovato") else ""),
                     "dove": f"riferimento {r.get('numero') or r.get('n')}", "dettagli": _short(r.get("testo"))})
    flagged = [c for c in j["controlli"] if c["esito"] in ("guarda", "ko") and not c["id"].startswith(REMINDER_PREFIXES)]
    sources["B2"] = sources["B4"] = f"controllo del manoscritto ({compliance.COMPLIANCE_VERSION})"
    for c in flagged:
        crit = "B4" if c["gruppo"] == NUMBERS_GROUP else "B2"
        page = (c.get("fonte") or {}).get("pagina")
        rows.append({"criterio": crit, "id": c["id"], "gruppo": c["gruppo"],
                     "cosa_dice_orpheus": f"{'da correggere' if c['esito'] == 'ko' else 'da guardare'}: {c['titolo']} - "
                                          f"{c['trovato']}",
                     "dove": f"regola: {_short(c.get('regola'), 160)}" + (f" (linee guida p. {page})" if page else ""),
                     "dettagli": _short("; ".join(c.get("dettagli") or []))})
    rev_dir = peer_review.folder(home, fp, j["rivista"])
    accepted = rev_dir / peer_review.ACCEPTED
    sources["C1"] = sources["C2"] = "revisione simulata accettata (review-v1)" if accepted.exists() else None
    if accepted.exists():
        data = json.loads(accepted.read_text(encoding="utf-8"))
        for p in data.get("profili", []):
            for k, o in enumerate(p.get("osservazioni", []), 1):
                rows.append({"criterio": "C1", "id": f"{p.get('profilo')}_{k}", "gruppo": f"revisione - {p.get('profilo')}",
                             "cosa_dice_orpheus": f"[{o.get('gravita')}] {_short(o.get('problema'), 400)}",
                             "dove": f"paragrafo {o.get('paragrafo')}: \"{_short(o.get('citazione'), 160)}\"",
                             "dettagli": "proposta: " + _short(o.get("proposta"), 300)})
    for n, r in enumerate(rows, 1):
        r.update(n=n, domanda=QUESTIONS[r["criterio"]], giudizio="", inventato="", note="")
        r["dettagli"] = benchmark.excel_text(r["dettagli"])
    return rows, sources


def _csv_bytes(rows: list[dict]) -> bytes:
    buf = io.StringIO(newline="")
    w = csv.DictWriter(buf, fieldnames=COLUMNS, delimiter=";", lineterminator="\r\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k, "") for k in COLUMNS})
    return buf.getvalue().encode("utf-8-sig")


def _readme(sigla: str) -> str:
    return (f"VALIDAZIONE DELLA FASE 7 ({VALIDATION_VERSION}) - manoscritto per {sigla}\n\n"
            "Questa cartella resta su questo PC (R10): il file cita il manoscritto.\n"
            f"1. Apri {CONTROL} con Excel. Per ogni riga scrivi S o N nella colonna 'giudizio':\n"
            "   B1 = l'esito di ORPHEUS per il riferimento e' giusto; B2/B4 = la segnalazione e' vera;\n"
            "   C1 = l'osservazione della revisione e' utile. Per le righe C1 scrivi S o N anche in 'inventato'\n"
            "   (S = dice cose che nel manoscritto non ci sono). Se vuoi, una nota in 'note'.\n"
            f"2. Salva con lo stesso nome piu' _compilato alla fine (controllo_validazione{FILLED_SUFFIX}),\n"
            "   formato CSV (separato da punto e virgola).\n"
            f"3. Apri {MISSED}: scrivi i problemi che hai trovato tu e ORPHEUS no (o 'nessuno'), togli DA COMPILARE.\n"
            "4. Rilancia validazione.cmd con lo stesso manoscritto e la stessa rivista: ORPHEUS calcola la precisione\n"
            "   con l'intervallo di confidenza al 95% e la confronta con le soglie. Poi scrivi a Claude: \"validazione fatta\".\n")


def prepare(home: Path, result: dict) -> dict:
    fp, sigla = result["manoscritto"]["impronta"], result["riviste"][0]["rivista"]
    d = folder(home, fp, sigla)
    d.mkdir(parents=True, exist_ok=True)
    rows, sources = rows_for(home, result)
    target = d / CONTROL
    data = _csv_bytes(rows)
    wrote = False
    if not target.exists():
        target.write_bytes(data)
        wrote = True
    elif target.read_bytes() != data and not _filled(target):
        target.write_bytes(data)                         # still empty: refresh it (e.g. a new review was accepted)
        wrote = True
    if not (d / MISSED).exists():
        (d / MISSED).write_text(MISSED_TEMPLATE, encoding="utf-8")
    (d / README).write_text(_readme(sigla), encoding="utf-8")
    counts = {c: sum(r["criterio"] == c for r in rows) for c in ("B1", "B2", "B4", "C1")}
    return {"cartella": str(d), "file_controllo": str(target), "file_nuovo": wrote, "righe": len(rows),
            "righe_per_criterio": counts, "fonti_mancanti": [c for c, s in sources.items() if not s]}


# ------------------------------------------------------------------------------------------------------ 2. score
def _filled(path: Path) -> bool:
    try:
        rows, _ = benchmark.read_filled(path, need={"n", "criterio", "giudizio"})
    except benchmark.FilledFileError:
        return False
    return any((r.get("giudizio") or "").strip() for r in rows)


def filled_file(d: Path) -> Path | None:
    found = sorted(d.glob(f"*{FILLED_SUFFIX}"), key=lambda p: (p.stat().st_mtime, p.name))
    if found:
        return found[-1]
    return d / CONTROL if (d / CONTROL).exists() and _filled(d / CONTROL) else None


def _yn(cell) -> str:
    v = (cell or "").strip().rstrip(".").strip().lower()
    return "" if not v else "S" if v in {"s", "si", "sì", "sí", "y", "yes"} else "N" if v in {"n", "no"} else "?"


def _precision(verdicts: list[str]) -> dict:
    s, n = verdicts.count("S"), verdicts.count("N")
    ci = wilson(s, s + n)
    return {"righe": len(verdicts), "si": s, "no": n, "non_compilate": verdicts.count(""),
            "non_valide": verdicts.count("?"), "precisione": round(s / (s + n), 4) if s + n else None,
            "ic95": list(ci) if ci else None}


def missed_count(d: Path) -> int | None:
    p = d / MISSED
    if not p.exists():
        return None
    lines = [x.strip() for x in p.read_text(encoding="utf-8-sig", errors="replace").splitlines()]
    lines = [x for x in lines if x and not x.startswith("#")]
    if any(x.upper() == "DA COMPILARE" for x in lines):
        return None
    if len(lines) == 1 and lines[0].lower().rstrip(".") in {"nessuno", "nessuna", "niente", "0", "none"}:
        return 0
    return len(lines)


def _rate_outcome(crit: str, stats: dict) -> str:
    t = THRESHOLDS[crit]
    if not stats["righe"]:
        return "non misurato"
    if stats["non_compilate"] or stats["non_valide"] or stats["precisione"] is None:
        return "da compilare"
    return "superato" if stats["precisione"] >= t["precisione_min"] else "non superato"


def score(d: Path, *, letter_dir: Path | None = None, response_dir: Path | None = None,
          health: dict | None = None) -> dict:
    """Scores of the filled control file of folder d (counts only)."""
    out: dict = {"versione": VALIDATION_VERSION, "criteri": {}}
    path = filled_file(d)
    rows: list[dict] = []
    if path:
        rows, info = benchmark.read_filled(path, need={"n", "criterio", "giudizio"})
        out["file_compilato"] = path.name
        out["salvato_come"] = info
    by = lambda c: [r for r in rows if (r.get("criterio") or "").strip() == c]   # noqa: E731
    crit = out["criteri"]
    for c in ("B1", "B2", "B4", "C1"):
        stats = _precision([_yn(r.get("giudizio")) for r in by(c)])
        crit[c] = {**THRESHOLDS[c], **stats, "esito": _rate_outcome(c, stats) if path else "da compilare"}
    b1 = by("B1")
    false_ok = sum(1 for r in b1 if _yn(r.get("giudizio")) == "N"
                   and re.match(r"\s*verificat", benchmark.excel_plain(r.get("cosa_dice_orpheus")), re.I))
    crit["B1"]["falsi_verificati"] = false_ok
    if crit["B1"]["esito"] == "superato" and false_ok > THRESHOLDS["B1"]["falsi_verificati_max"]:
        crit["B1"]["esito"] = "non superato"
    invented = [_yn(r.get("inventato")) for r in by("C1")]
    crit["C2"] = {**THRESHOLDS["C2"], "righe": len(invented), "inventati": invented.count("S"),
                  "non_compilate": invented.count("") + invented.count("?"),
                  "esito": ("non misurato" if not invented else "da compilare" if "" in invented or "?" in invented
                            else "superato" if invented.count("S") == 0 else "non superato")}
    missed = missed_count(d)
    crit["B3"] = {**THRESHOLDS["B3"], "mancati": missed,
                  "esito": "da compilare" if missed is None else "superato" if missed <= 1 else "non superato"}
    if letter_dir is not None:
        ok = (letter_dir / sl.COVER["accepted"]).exists()
        crit["C3"] = {**THRESHOLDS["C3"], "lettera_accettata": ok,
                      "esito": "superato" if ok else "non misurato"}
    if response_dir is not None:
        acc = response_dir / sl.RESPONSE["accepted"]
        if acc.exists():
            crit["C4"] = {**THRESHOLDS["C4"], "risposta_accettata": True, "esito": "superato"}
        else:
            crit["C4"] = {**THRESHOLDS["C4"], "risposta_accettata": False,
                          "esito": "non applicabile (ancora nessun commento della rivista)"}
    if health is not None:
        checks = {c.get("nome"): c for c in health.get("controlli", [])}
        def _h(*names):
            got = [checks[n] for n in names if n in checks]
            if not got:
                return "non misurato"
            return "superato" if all(c.get("esito") == "ok" for c in got) else "non superato"
        crit["E2"] = {**THRESHOLDS["E2"], "esito": _h("copie_di_sicurezza", "prova_ripristino")}
        crit["E4"] = {**THRESHOLDS["E4"], "esito": _h("servizi_a_pagamento")}
    states = [v["esito"] for v in crit.values()]
    out["riepilogo"] = {s: states.count(s) for s in dict.fromkeys(states)}
    out["gate"] = ("firma dell'utente necessaria (EVALUATION_REPORTS/...validazione-fase-7.md, §4 del piano)"
                   if all(not s.startswith(("da compilare", "non superato")) for s in states)
                   else "non ancora: ci sono criteri da compilare o non superati")
    return out


def write_results(d: Path, results: dict, now: datetime | None = None) -> Path:
    now = now or datetime.now()
    p = d / RESULTS
    p.write_text(json.dumps({**results, "data": now.strftime("%Y-%m-%d %H:%M")}, ensure_ascii=False, indent=1),
                 encoding="utf-8")
    return p
