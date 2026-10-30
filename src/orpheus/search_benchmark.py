"""T-032 — Fase 3 gate: benchmark of the search on questions checked by a human (rules search-bench-v1). Read-only.

User's decision of 2026-10-15 13:40: "Claude propone, io controllo". Claude writes the questions (Italian and English,
each with the articles that answer it, by DOI) into rapporti/benchmark_ricerca_<n>_<data>.csv; the user checks every
row (giusta S/N, can add or remove DOIs in articoli_attesi, notes) and saves it as …_compilato.csv. Only the rows with
giusta = S and at least one expected article in the database are measured.

Methods, each with the Italian and the English question:
  - pagine: the search that existed before Fase 3 (`cerca`: every word on the same page, page_fts) — the BASELINE;
  - parole: BM25 on the segments (any significant word);
  - significato: vectors of the local multilingual model;
  - ibrida: fusion of parole and significato (hybrid-v1).
Measures per method and language: recall@5 and recall@10 (expected articles found among the first k articles,
averaged over the questions), hit@10 (at least one found) and MRR (1 / rank of the first expected article).
"""

from __future__ import annotations

import csv
import io
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from . import content, semantic
from .benchmark import read_filled
from .ingest_pdf import search_documents

RULES_VERSION = "search-bench-v1"
PREFIX = "benchmark_ricerca"
COLUMNS = ["n", "domanda_it", "domanda_en", "articoli_attesi", "titoli_attesi", "da_dove", "giusta", "note"]
METHODS = ("pagine", "parole", "significato", "ibrida")
KS = (5, 10)
_ORIGINAL = re.compile(rf"^{PREFIX}_(\d+)_\d{{4}}-\d{{2}}-\d{{2}}(_\d{{6}})?$")
REPORTS_DIR = "rapporti"


def write_questions(rows: list[dict], home: Path, now: datetime | None = None) -> Path:
    now = now or datetime.now()
    buf = io.StringIO(newline="")
    w = csv.DictWriter(buf, fieldnames=COLUMNS, delimiter=";", lineterminator="\r\n")
    w.writeheader()
    for i, r in enumerate(rows, 1):
        w.writerow({**{c: "" for c in COLUMNS}, **r, "n": i})
    folder = Path(home) / REPORTS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{PREFIX}_{len(rows)}_{now.strftime('%Y-%m-%d')}.csv"
    path.write_bytes(buf.getvalue().encode("utf-8-sig"))
    return path


def latest_filled(home: Path) -> Path | None:
    folder = Path(home) / REPORTS_DIR
    if not folder.is_dir():
        return None
    found = [p for p in folder.glob(f"{PREFIX}_*.csv") if not _ORIGINAL.match(p.stem) and "risultati" not in p.stem]
    return max(found, key=lambda p: (p.stat().st_mtime, p.name)) if found else None


def _dois(cell: str | None) -> list[str]:
    return [d.strip().lower() for d in re.split(r"[|,;\s]+", cell or "") if d.strip().lower().startswith("10.")]


def questions(path: Path, conn: sqlite3.Connection) -> tuple[list[dict], dict]:
    rows, info = read_filled(path, need={"domanda_it", "domanda_en", "articoli_attesi", "giusta"})
    by_doi = {r["doi"]: r["id"] for r in conn.execute("SELECT id, doi FROM article WHERE doi IS NOT NULL")}
    out, skipped = [], {"non_giuste": 0, "senza_articoli_nel_database": 0, "non_compilate": 0}
    for r in rows:
        verdict = (r.get("giusta") or "").strip().upper()[:1]
        if verdict == "N":
            skipped["non_giuste"] += 1
            continue
        if verdict != "S":
            skipped["non_compilate"] += 1
            continue
        expected = {by_doi[d] for d in _dois(r.get("articoli_attesi")) if d in by_doi}
        if not expected:
            skipped["senza_articoli_nel_database"] += 1
            continue
        out.append({"n": r.get("n"), "it": (r.get("domanda_it") or "").strip(), "en": (r.get("domanda_en") or "").strip(),
                    "attesi": expected})
    return out, {**info, "righe": len(rows), "scartate": skipped}


def _articles_pages(conn, text: str, exclude: set) -> list[str]:
    out = []
    for d in search_documents(conn, text, limit=50, exclude=exclude):
        art = conn.execute("SELECT article_id FROM document WHERE id = ?", (d["document_id"],)).fetchone()
        if art and art[0] and art[0] not in out:
            out.append(art[0])
    return out


def _articles_segments(conn, segment_ids: list[str]) -> list[str]:
    out = []
    for sid in segment_ids:
        r = conn.execute("SELECT article_id FROM segment WHERE id = ?", (sid,)).fetchone()
        if r and r[0] and r[0] not in out:
            out.append(r[0])
    return out


def run(conn: sqlite3.Connection, qs: list[dict], *, embedder=None, index=None) -> dict:
    exclude = content.not_complete(conn)
    methods = [m for m in METHODS if m in ("pagine", "parole") or (embedder is not None and index is not None and index.ids)]
    per_question = []
    for q in qs:
        row = {"n": q["n"], "attesi": len(q["attesi"]), "risultati": {}}
        for lang in ("it", "en"):
            text = q[lang]
            if not text:
                continue
            for m in methods:
                if m == "pagine":
                    ranked = _articles_pages(conn, text, exclude)
                else:
                    ranked = _articles_segments(conn, semantic.ranked_segments(conn, text, m, embedder=embedder, index=index))
                ranks = sorted(ranked.index(a) + 1 for a in q["attesi"] if a in ranked)
                row["risultati"][f"{m}_{lang}"] = ranks
        per_question.append(row)
    summary = {}
    for key in sorted({k for r in per_question for k in r["risultati"]}):
        rows = [r for r in per_question if key in r["risultati"]]
        if not rows:
            continue
        rec = {f"recall@{k}": round(sum(sum(1 for x in r["risultati"][key] if x <= k) / r["attesi"] for r in rows)
                                    / len(rows), 3) for k in KS}
        hit = sum(1 for r in rows if any(x <= 10 for x in r["risultati"][key])) / len(rows)
        mrr = sum((1 / r["risultati"][key][0]) if r["risultati"][key] else 0 for r in rows) / len(rows)
        summary[key] = {**rec, "hit@10": round(hit, 3), "mrr": round(mrr, 3), "domande": len(rows)}
    return {"regole": RULES_VERSION, "metodi": methods, "domande": len(qs), "riepilogo": summary,
            "per_domanda": per_question}


def write_results(res: dict, home: Path, now: datetime | None = None) -> Path:
    now = now or datetime.now()
    keys = sorted({k for r in res["per_domanda"] for k in r["risultati"]})
    buf = io.StringIO(newline="")
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    w.writerow(["n", "articoli_attesi"] + [f"{k} (posizioni)" for k in keys])
    for r in res["per_domanda"]:
        w.writerow([r["n"], r["attesi"]] + [" ".join(map(str, r["risultati"].get(k, []))) or "-" for k in keys])
    path = Path(home) / REPORTS_DIR / f"{PREFIX}_risultati_{now.strftime('%Y-%m-%d_%H%M%S')}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(buf.getvalue().encode("utf-8-sig"))
    return path
