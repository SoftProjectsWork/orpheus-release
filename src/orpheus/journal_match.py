"""T-040 parte 2 — Fase 6: which journals suit a manuscript (agent D5, rules match-v1).

For each journal with a card, criteria side by side, each with its source; NO probability of acceptance (REQUIREMENTS:
"distinzione esplicita tra compatibilità editoriale e probabilità di accettazione"):

  - temi: how close the manuscript (title, abstract, keywords) is to what the journal has published, measured on the
    articles of that journal already in ORPHEUS (segments of the complete articles):
      * significato (when the local model and the vectors are on this PC, hybrid-v2's model): cosine similarity between
        the manuscript and every segment; an article scores its best segment; the journal scores the mean of its
        5 best articles (0-100);
      * parole (otherwise): the manuscript's significant words searched in the segments (BM25, top 200 segments);
        an article scores by its rank (1 for the first, 0.5 at rank 11, 0.33 at rank 21...); the journal scores the
        mean of its 5 best articles, missing ones counting 0 (0-100). Indicative only.
    With the 3 closest articles of that journal (title, year, DOI) as the reason, and how many articles of the journal
    ORPHEUS has (few articles = uncertain estimate);
  - ambito: closeness to the scope written in the journal card ("ambito", only with the model);
  - regole: what must be corrected for that journal (compliance-v1, computed elsewhere);
  - metriche: SJR and quartile (SCImago, journal_metric), as they are.

Read-only. The manuscript text is used on this PC only; nothing leaves it.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from . import semantic
from .manuscript import Manuscript

MATCH_VERSION = "match-v1"
TOP_ARTICLES = 5          # articles per journal in the score
SHOW_ARTICLES = 3         # closest articles shown as the reason
WORD_POOL = 200           # segments from the word search
FEW_ARTICLES = 15         # below this the estimate is marked uncertain


def query_text(m: Manuscript) -> str:
    parts = [m.title or "", m.abstract_text or "", "; ".join(m.keywords)]
    text = "\n".join(p for p in parts if p).strip()
    if not text:                                  # no title nor abstract recognised: the start of the main text
        text = "\n".join(m.doc.paragraphs[i].text for i in m.main_paras[:8])
    return text[:4000]


@dataclass
class Matcher:
    """Embedder and index loaded once (the dashboard keeps one); both None = word mode."""
    embedder: object = None
    index: object = None
    _articles: dict = field(default_factory=dict)

    @classmethod
    def from_local(cls, conn, local_root) -> "Matcher":
        try:
            emb = semantic.OnnxEmbedder(semantic.model_dir(local_root))
        except semantic.ModelMissing:
            return cls()
        index = semantic.VectorIndex.load(conn)
        return cls(emb, index) if index.ids else cls()

    @property
    def mode(self) -> str:
        return "significato" if self.embedder is not None and self.index is not None and self.index.ids else "parole"


def _segment_articles(conn, ids: list[str]) -> dict[str, str]:
    out = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        marks = ",".join("?" for _ in chunk)
        for r in conn.execute(f"SELECT id, article_id FROM segment WHERE id IN ({marks})", chunk):
            if r[1]:
                out[r[0]] = r[1]
    return out


def _articles_of(conn, journal_ids: list[str]) -> dict[str, dict]:
    marks = ",".join("?" for _ in journal_ids) or "''"
    return {r["id"]: {"journal_id": r["journal_id"], "titolo": r["title"], "anno": r["year"], "doi": r["doi"]}
            for r in conn.execute(f"""SELECT id, journal_id, title, publication_year AS year, doi FROM article
                                      WHERE journal_id IN ({marks})""", journal_ids)}


def _metrics(conn, journal_id: str) -> dict:
    out = {}
    for key, metric in (("sjr", "SJR"), ("quartile", "quartile")):
        r = conn.execute("""SELECT value, value_text, reference_year, provider FROM journal_metric WHERE journal_id = ?
                            AND metric = ? AND subject_category = '' ORDER BY reference_year DESC LIMIT 1""",
                         (journal_id, metric)).fetchone()
        if r is not None:
            out[key] = r["value_text"] if r["value"] is None else round(r["value"], 3)
            out["anno"], out["fonte"] = r["reference_year"], r["provider"]
    return out


def _scope(conn, journal_id: str) -> str:
    rows = conn.execute("""SELECT v.value FROM journal_card_value v JOIN journal_card c ON c.id = v.card_id
                           WHERE c.journal_id = ? AND c.status = 'current' AND v.field = 'ambito' AND v.value IS NOT NULL
                           ORDER BY v.ord""", (journal_id,)).fetchall()
    return "; ".join(r[0] for r in rows)


def match(conn: sqlite3.Connection, m: Manuscript, journals: list[dict], matcher: Matcher | None = None) -> dict:
    """{journal_id: {...criteria...}} for the given journals (dicts with "id"). Read-only."""
    matcher = matcher or Matcher()
    ids = [j["id"] for j in journals]
    arts = _articles_of(conn, ids)
    text = query_text(m)
    per_article: dict[str, float] = {}
    scope_scores: dict[str, float | None] = {jid: None for jid in ids}
    mode = matcher.mode
    if mode == "significato":
        import numpy as np
        q = matcher.embedder.embed([text], "query")[0]
        scores = matcher.index.matrix @ np.asarray(q, dtype="<f4").reshape(-1)
        seg_art = _segment_articles(conn, matcher.index.ids)
        for sid, s in zip(matcher.index.ids, scores.tolist()):
            aid = seg_art.get(sid)
            if aid in arts and s > per_article.get(aid, -1.0):
                per_article[aid] = s
        scopes = {jid: _scope(conn, jid) for jid in ids}
        with_scope = [jid for jid in ids if scopes[jid]]
        if with_scope:
            vecs = matcher.embedder.embed([scopes[j] for j in with_scope], "passage")
            for jid, v in zip(with_scope, vecs):
                scope_scores[jid] = round(float(np.dot(q, v)) * 100, 1)
    else:
        ranked = semantic.search_words(conn, text, WORD_POOL)
        seg_art = _segment_articles(conn, ranked)
        order: list[str] = []
        for sid in ranked:
            aid = seg_art.get(sid)
            if aid and aid not in order:
                order.append(aid)
        for rank, aid in enumerate(order, 1):          # 1 for the first article, 0.5 at rank 11, 0.33 at rank 21...
            if aid in arts:
                per_article[aid] = 1.0 / (1.0 + (rank - 1) / 10.0)
    out = {}
    for jid in ids:
        mine = sorted(((s, a) for a, s in per_article.items() if arts[a]["journal_id"] == jid), reverse=True)
        n_articles = sum(1 for a in arts.values() if a["journal_id"] == jid)
        best = [s for s, _ in mine[:TOP_ARTICLES]]
        if mode == "significato":
            score = round(sum(best) / len(best) * 100, 1) if best else None
        else:                                          # missing articles count 0: few matches, low score
            score = round(sum(best) / TOP_ARTICLES * 100, 1) if per_article else None
        out[jid] = {"vicinanza_temi": score, "modo": mode, "ambito": scope_scores[jid],
                    "articoli_in_orpheus": n_articles, "incerta": n_articles < FEW_ARTICLES,
                    "articoli_simili": [{**{k: arts[a][k] for k in ("titolo", "anno", "doi")}, "article_id": a,
                                         "punteggio": round(s * 100, 1) if mode == "significato" else None}
                                        for s, a in mine[:SHOW_ARTICLES]],
                    "metriche": _metrics(conn, jid)}
    return {"versione": MATCH_VERSION, "modo": mode, "riviste": out,
            "nota": ("vicinanza per significato (modello locale, articoli già in ORPHEUS)" if mode == "significato" else
                     "vicinanza per parole: il modello o i vettori non sono su questo PC (lavora.cmd li prepara)")
            + "; compatibilità, non probabilità di accettazione"}
