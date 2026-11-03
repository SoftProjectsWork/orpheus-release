"""EURYDIKE E11 — final gate (T-051l, D9): redo a published open-access meta-analysis and compare.

The user chooses the published meta-analysis (with Claude), imports its searches, screens, extracts and registers the
analysis in EURYDIKE as for any review. Then:
  1. `template` writes `meta_analisi\\gate_E11\\<analysis>_pubblicata.csv`: one row per study of the registered
     analysis, one row «POOLED» and the PRISMA rows, with the values of EURYDIKE and empty columns for the values
     printed in the article;
  2. the user copies the published values from the article (forest plot, tables, PRISMA diagram);
  3. `compare` reads the file and writes `<analysis>_confronto.md`: g (or MD) of every study within ±0.01; pooled
     estimate, CI and I² within the rounding of the article (half a unit of the last decimal written); PRISMA counts
     equal — or the difference explained by the user in the column «spiegazione». The report ends with the line for the
     signature of the user (copied into EVALUATION_REPORTS when signed).
Nothing is written in the database.
"""
from __future__ import annotations

import csv
import io
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import meta
from . import overview as ov
from .core.stats import num

GATE_DIR = "gate_E11"
COLUMNS = ["voce", "eurydike", "eurydike_ic_basso", "eurydike_ic_alto", "pubblicato", "pubblicato_ic_basso",
           "pubblicato_ic_alto", "spiegazione"]
POOLED = "POOLED"
I2 = "I2 (%)"
PRISMA = [("identified", "PRISMA: identified"), ("duplicates", "PRISMA: duplicates removed"),
          ("screened", "PRISMA: screened"), ("excluded", "PRISMA: excluded on title/abstract"),
          ("assessed", "PRISMA: full texts assessed"), ("ft_excluded", "PRISMA: full texts excluded"),
          ("included", "PRISMA: included")]
STUDY_TOL = 0.01


class GateError(ValueError):
    """A problem for the user (message in Italian)."""


def _dir(conn: sqlite3.Connection, home: Path, review_id: str) -> Path:
    return meta.ma_dir(conn, home, review_id) / GATE_DIR


def _r(x) -> str:
    return "" if x is None else f"{x:.4f}"


def template(conn: sqlite3.Connection, home: Path, review_id: str, analysis_id: str) -> dict:
    d = meta.detail(conn, review_id, analysis_id)
    res = d["risultato"]
    p = ov.review_overview(conn, review_id)["prisma"]
    rows = [[st["label"], _r(st["yi"]), _r(st["ci_low"]), _r(st["ci_high"]), "", "", "", ""] for st in res["studies"]]
    rows.append([POOLED, _r(res["estimate"]), _r(res["ci_low"]), _r(res["ci_high"]), "", "", "", ""])
    rows.append([I2, f"{res['i2']:.2f}", "", "", "", "", "", ""])
    rows += [[label, str(p[k]), "", "", "", "", "", ""] for k, label in PRISMA]
    folder = _dir(conn, home, review_id)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{analysis_id}_pubblicata.csv"
    if path.exists():
        raise GateError(f"il file esiste già: {path.name} (compilalo e premi «Confronta»)")
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    w.writerow(COLUMNS)
    w.writerows(rows)
    path.write_text(buf.getvalue(), encoding="utf-8-sig")
    return {"file": str(path), "righe": len(rows)}


def _read(path: Path) -> list[dict]:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    first = text.splitlines()[0] if text.strip() else ""
    delim = ";" if first.count(";") >= first.count(",") else ","
    return [{(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
            for r in csv.DictReader(io.StringIO(text), delimiter=delim)]


def _decimals(s: str) -> int:
    s = s.strip().replace(",", ".")
    return len(s.split(".")[1]) if "." in s else 0


def _within_rounding(ours: float, published: str) -> tuple[bool, float]:
    tol = 0.5 * 10 ** -_decimals(published) + 1e-9
    return abs(ours - num(published)) <= tol, tol


def compare(conn: sqlite3.Connection, home: Path, review_id: str, analysis_id: str) -> dict:
    path = _dir(conn, home, review_id) / f"{analysis_id}_pubblicata.csv"
    if not path.exists():
        raise GateError("manca il file dei valori pubblicati: premi prima «Prepara il file»")
    d = meta.detail(conn, review_id, analysis_id)
    res = d["risultato"]
    p = ov.review_overview(conn, review_id)["prisma"]
    ours = {st["label"]: st for st in res["studies"]}
    checks, missing = [], []
    for r in _read(path):
        voce, pub = r.get("voce", ""), r.get("pubblicato", "")
        why = r.get("spiegazione", "")
        if not voce:
            continue
        if not pub:
            missing.append(voce)
            continue
        if num(pub) is None:
            checks.append({"voce": voce, "esito": "non leggibile", "ok": False, "spiegazione": why})
            continue
        if voce in ours:
            st = ours[voce]
            ok = abs(st["yi"] - num(pub)) <= STUDY_TOL + 1e-9
            item = {"voce": voce, "eurydike": st["yi"], "pubblicato": num(pub), "differenza": st["yi"] - num(pub),
                    "regola": f"±{STUDY_TOL}", "ok": ok}
        elif voce == POOLED:
            parts = [_within_rounding(res["estimate"], pub)]
            for side, key in (("pubblicato_ic_basso", "ci_low"), ("pubblicato_ic_alto", "ci_high")):
                if r.get(side) and num(r[side]) is not None:
                    parts.append(_within_rounding(res[key], r[side]))
            item = {"voce": voce, "eurydike": res["estimate"], "pubblicato": num(pub),
                    "differenza": res["estimate"] - num(pub), "regola": "arrotondamento dell'articolo (stima e IC)",
                    "ok": all(x[0] for x in parts)}
        elif voce == I2:
            ok, tol = _within_rounding(res["i2"], pub)
            item = {"voce": voce, "eurydike": res["i2"], "pubblicato": num(pub), "differenza": res["i2"] - num(pub),
                    "regola": f"±{tol:g}", "ok": ok}
        elif voce in dict((label, k) for k, label in PRISMA):
            k = dict((label, k) for k, label in PRISMA)[voce]
            item = {"voce": voce, "eurydike": p[k], "pubblicato": num(pub), "differenza": p[k] - num(pub),
                    "regola": "uguale", "ok": p[k] == num(pub)}
        else:
            item = {"voce": voce, "esito": "voce sconosciuta", "ok": False}
        item["spiegazione"] = why
        item["esito"] = item.get("esito") or ("uguale" if item["ok"] else ("differenza spiegata" if why else "DIVERSO"))
        checks.append(item)
    passed = bool(checks) and not missing and all(c["ok"] or c["spiegazione"] for c in checks)
    report = _write_report(path.with_name(f"{analysis_id}_confronto.md"), d, checks, missing, passed)
    return {"superato": passed, "controlli": len(checks), "diversi": sum(1 for c in checks if not c["ok"]),
            "spiegati": sum(1 for c in checks if not c["ok"] and c["spiegazione"]), "mancanti": missing,
            "rapporto": str(report), "dettaglio": checks}


def _write_report(path: Path, d: dict, checks: list[dict], missing: list[str], passed: bool) -> Path:
    f = lambda x: "—" if x is None else (f"{x:.4f}" if isinstance(x, float) else str(x))  # noqa: E731
    p = d["piano"]
    lines = [f"# Gate E11 — confronto con la meta-analisi pubblicata ({datetime.now(timezone.utc).strftime('%Y-%m-%d')})",
             "", f"Analisi EURYDIKE `{d['id']}`: {p['nome']} · {p['esito']}{' · ' + p['tempo'] if p['tempo'] else ''} · "
             f"{p['misura']} · {p['modello']} · IC {p['ic'].upper()} · k = {d['risultato']['k']} · {d['versione']} · "
             f"SHA-256 degli input `{d['sha256']}`", "",
             "Regole (EURYDIKE_PIANO §4, E11): effetto di ogni studio entro ±0,01; stima complessiva, IC e I² entro "
             "l'arrotondamento dell'articolo; conteggi PRISMA uguali; altrimenti differenza spiegata.", "",
             "| Voce | EURYDIKE | Pubblicato | Differenza | Regola | Esito | Spiegazione |", "|---|---|---|---|---|---|---|"]
    for c in checks:
        lines.append(f"| {c['voce']} | {f(c.get('eurydike'))} | {f(c.get('pubblicato'))} | {f(c.get('differenza'))} | "
                     f"{c.get('regola', '')} | {c['esito']} | {c.get('spiegazione') or ''} |")
    if missing:
        lines += ["", "Valori pubblicati non ancora scritti: " + ", ".join(missing)]
    lines += ["", f"**Esito: {'SUPERATO' if passed else 'NON ANCORA SUPERATO'}**", "",
              "Firma dell'utente (scrivere «Superato, Nome Cognome» o i motivi): ____________________", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def status(conn: sqlite3.Connection, home: Path, review_id: str) -> list[dict]:
    """For every registered analysis: whether the file of the published values and the comparison exist."""
    folder = _dir(conn, home, review_id)
    out = []
    for a in meta.analyses(conn, review_id):
        csv_path = folder / f"{a['id']}_pubblicata.csv"
        rep = folder / f"{a['id']}_confronto.md"
        out.append({"analisi": a["id"], "nome": a["nome"], "file": str(csv_path) if csv_path.exists() else None,
                    "rapporto": str(rep) if rep.exists() else None})
    return out

