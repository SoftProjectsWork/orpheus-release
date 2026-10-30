"""T-031 — Fase 3: vectors of the segments and search by words + meaning (hybrid), rules hybrid-v1.

User's decisions of 2026-10-15 13:40: a free MULTILINGUAL model that runs on the PC (questions also in Italian on
articles in English), vectors in the database (migration 0009). Model: intfloat/multilingual-e5-small (MIT licence,
384 dimensions, ~100 languages), its ONNX export onnx/model.onnx (~470 MB) and tokenizer.json, downloaded ONCE from
huggingface.co into this PC's local folder (<ORPHEUS_LOCAL_DIR>/modelli/…, never the external disk, never GitHub);
afterwards everything runs offline with onnxruntime on the CPU. E5 recipe: "passage: " before a segment, "query: "
before a question, mean pooling over the tokens, L2 normalisation, at most 512 tokens.

Search (`cerca-testi`):
  - parole: BM25 on segment_fts with the significant words of the question (English and Italian stop words left out),
    any of them; words of 6 letters or more also as a prefix ("injur" no, "injuries" → "injuries*");
  - significato: cosine similarity between the question's vector and the segments' vectors (exact, all segments);
  - ibrida: reciprocal rank fusion of the two lists (score = w / (60 + rank in parole) + 1 / (60 + rank in
    significato), top 100 of each). hybrid-v2 (0.21.1, after the first benchmark on the PC): w = share of the question's
    significant words that exist in the segments (an Italian question on English articles has few: its word list is
    mostly noise and counts little; an English question has w close to 1, as in hybrid-v1).
Results are grouped by article (its best segment first) with page, section and the start of the segment.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from . import audit, crossref
from .db import transaction
from .segments import RULES_VERSION as SEGMENT_RULES

RULES_VERSION = "hybrid-v2"
MODEL_ID = "intfloat/multilingual-e5-small"
MODEL_FILE = "onnx/model.onnx"
TOKENIZER_FILE = "tokenizer.json"
MODEL_KEY = f"{MODEL_ID}:{MODEL_FILE}"
DIM = 384
MAX_TOKENS = 512
QUERY_PREFIX, PASSAGE_PREFIX = "query: ", "passage: "
DOWNLOAD_BASE = "https://huggingface.co"
MODELS_DIR = "modelli"
RRF_K, POOL = 60, 100

STOP = set("""a about above after again all also an and any are as at be been being before between both but by can could
did do does doing during each few for from further had has have having here how i if in into is it its itself just more
most no nor not of off on once only or other our out over own same should so some such than that the their them then
there these they this those through to too under until up very was we were what when where which while who whom why will
with would you your vs versus versu using use used study studies effect effects
il lo la i gli le un uno una di da del dello della dei degli delle in nel nello nella nei negli nelle con su sul sullo
sulla sui sugli sulle per tra fra e ed o od ma se che chi cui non come dove quando quale quali quanto quanti piu più meno
al allo alla ai agli alle dal dallo dalla dai dagli dalle è sono essere ha hanno ci si ne anche quale qual cosa cose
studio studi effetto effetti""".split())


class ModelMissing(RuntimeError):
    """The model files are not on this PC (or the packages are not installed)."""


def model_dir(local_root: Path) -> Path:
    return Path(local_root) / MODELS_DIR / MODEL_ID.replace("/", "__")


# ---------------------------------------------------------------- model files

def download_model(target: Path, *, opener=None, log=print) -> dict:
    """Download onnx/model.onnx and tokenizer.json (once). Returns {file: sha256}; the sha256 go into model.json."""
    target.mkdir(parents=True, exist_ok=True)
    out = {}
    for name in (TOKENIZER_FILE, MODEL_FILE):
        dest = target / name
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            url = f"{DOWNLOAD_BASE}/{MODEL_ID}/resolve/main/{name}"
            log(f"scarico {url}")
            tmp = dest.with_name(dest.name + ".partial")
            request = urllib.request.Request(url, headers={"User-Agent": "ORPHEUS (local research tool)"})
            open_url = opener or (lambda req: urllib.request.urlopen(req, timeout=120, context=crossref.tls_context()))
            with open_url(request) as resp, open(tmp, "wb") as fh:
                size = int(resp.headers.get("content-length") or 0)
                done, step = 0, max(size // 10, 1)
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    fh.write(chunk)
                    if size and (done // step) != ((done + len(chunk)) // step):
                        log(f"  {name}: {min(100, (done + len(chunk)) * 100 // size)}%")
                    done += len(chunk)
            if size and done != size:
                tmp.unlink(missing_ok=True)
                raise OSError(f"{name}: scaricati {done} byte invece di {size}")
            tmp.replace(dest)
        out[name] = hashlib.sha256(dest.read_bytes()).hexdigest()
    (target / "model.json").write_text(json.dumps({"model": MODEL_ID, "files": out}, indent=1), encoding="utf-8")
    return out


@dataclass
class OnnxEmbedder:
    """E5 embeddings with onnxruntime + tokenizers (CPU)."""
    folder: Path
    batch_size: int = 16
    name: str = MODEL_KEY
    dim: int = DIM
    _session: object = field(default=None, repr=False)

    def __post_init__(self):
        try:
            import numpy  # noqa: F401
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except ImportError as exc:
            raise ModelMissing(f"mancano i pacchetti per la ricerca per significato ({exc.name}): "
                               "rilancia installa_orpheus.cmd") from exc
        model, tok = Path(self.folder) / MODEL_FILE, Path(self.folder) / TOKENIZER_FILE
        if not model.exists() or not tok.exists():
            raise ModelMissing(f"modello non scaricato in {self.folder}")
        self._tok = Tokenizer.from_file(str(tok))
        self._tok.enable_truncation(max_length=MAX_TOKENS)
        pad = self._tok.token_to_id("<pad>")
        self._tok.enable_padding(pad_id=1 if pad is None else pad, pad_token="<pad>")
        options = ort.SessionOptions()
        options.log_severity_level = 3
        self._session = ort.InferenceSession(str(model), sess_options=options, providers=["CPUExecutionProvider"])
        self._inputs = {i.name for i in self._session.get_inputs()}

    def embed(self, texts: list[str], kind: str = "passage"):
        import numpy as np
        prefix = QUERY_PREFIX if kind == "query" else PASSAGE_PREFIX
        out = []
        for i in range(0, len(texts), self.batch_size):
            enc = self._tok.encode_batch([prefix + t for t in texts[i:i + self.batch_size]])
            ids = np.array([e.ids for e in enc], dtype=np.int64)
            mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
            feeds = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self._inputs:
                feeds["token_type_ids"] = np.zeros_like(ids)
            hidden = self._session.run(None, {k: v for k, v in feeds.items() if k in self._inputs})[0]
            if hidden.ndim == 3:
                m = mask[..., None].astype(hidden.dtype)
                hidden = (hidden * m).sum(axis=1) / np.clip(m.sum(axis=1), 1e-9, None)
            norms = np.linalg.norm(hidden, axis=1, keepdims=True)
            out.append((hidden / np.clip(norms, 1e-12, None)).astype("<f4"))
        return np.concatenate(out) if out else np.zeros((0, self.dim), dtype="<f4")


# ---------------------------------------------------------------- vectors in the database

def missing_segments(conn: sqlite3.Connection, model: str = MODEL_KEY) -> list[sqlite3.Row]:
    return conn.execute("""SELECT s.id, s.text FROM segment s
                           WHERE s.rules = ? AND NOT EXISTS (SELECT 1 FROM segment_vector v
                                                             WHERE v.segment_id = s.id AND v.model = ?)
                           ORDER BY s.document_id, s.ord""", (SEGMENT_RULES, model)).fetchall()


def write_vectors(conn: sqlite3.Connection, embedder, *, batch: int = 256, log=None,
                  actor: str = "orpheus vettori") -> dict:
    """Vectors for the segments that do not have one for this model; one transaction per batch (resumable)."""
    todo = missing_segments(conn, embedder.name)
    written = 0
    for i in range(0, len(todo), batch):
        rows = todo[i:i + batch]
        vecs = embedder.embed([r["text"] for r in rows], "passage")
        with transaction(conn):
            for r, v in zip(rows, vecs):
                conn.execute("INSERT INTO segment_vector (segment_id, model, dim, vector) VALUES (?, ?, ?, ?)",
                             (r["id"], embedder.name, int(len(v)), v.astype("<f4").tobytes()))
            written += len(rows)
            if i + batch >= len(todo):
                audit.record(conn, actor=actor, action="write_segment_vectors",
                             payload={"modello": embedder.name, "vettori": written, "regole_segmenti": SEGMENT_RULES},
                             in_transaction=True)
        if log:
            log(f"  vettori {written} di {len(todo)}")
    return {"da_calcolare": len(todo), "scritti": written}


def vector_status(conn: sqlite3.Connection, model: str = MODEL_KEY) -> dict:
    q = lambda sql, *a: conn.execute(sql, a).fetchone()[0]   # noqa: E731
    return {"segmenti": q("SELECT count(*) FROM segment WHERE rules = ?", SEGMENT_RULES),
            "con_vettore": q("""SELECT count(*) FROM segment s JOIN segment_vector v ON v.segment_id = s.id
                                WHERE s.rules = ? AND v.model = ?""", SEGMENT_RULES, model),
            "modello": model}


# ---------------------------------------------------------------- search

def words_query(text: str) -> str:
    seen = significant_words(text)
    return " OR ".join('"' + w.replace('"', '""') + '"' + ("*" if len(w) >= 6 else "") for w in seen)


def search_words(conn: sqlite3.Connection, text: str, limit: int = POOL) -> list[str]:
    q = words_query(text)
    if not q:
        return []
    return [r[0] for r in conn.execute(
        """SELECT s.id FROM segment_fts JOIN segment s ON s.rowid = segment_fts.rowid
           WHERE segment_fts MATCH ? AND s.rules = ? ORDER BY bm25(segment_fts) LIMIT ?""", (q, SEGMENT_RULES, limit))]


@dataclass
class VectorIndex:
    ids: list[str]
    matrix: object

    @classmethod
    def load(cls, conn: sqlite3.Connection, model: str = MODEL_KEY) -> "VectorIndex":
        import numpy as np
        rows = conn.execute("""SELECT v.segment_id, v.vector, v.dim FROM segment_vector v JOIN segment s ON s.id = v.segment_id
                               WHERE v.model = ? AND s.rules = ? ORDER BY s.rowid""", (model, SEGMENT_RULES)).fetchall()
        if not rows:
            return cls([], np.zeros((0, DIM), dtype="<f4"))
        return cls([r[0] for r in rows], np.frombuffer(b"".join(r[1] for r in rows), dtype="<f4").reshape(len(rows), -1))

    def search(self, query_vector, limit: int = POOL) -> list[str]:
        import numpy as np
        if not self.ids:
            return []
        scores = self.matrix @ np.asarray(query_vector, dtype="<f4").reshape(-1)
        top = np.argsort(-scores)[:limit]
        return [self.ids[i] for i in top]


def fuse(*lists: list[str], k: int = RRF_K, weights: list[float] | None = None) -> list[str]:
    score: dict[str, float] = {}
    for i, lst in enumerate(lists):
        w = 1.0 if weights is None else weights[i]
        for rank, sid in enumerate(lst, 1):
            score[sid] = score.get(sid, 0.0) + w / (k + rank)
    return sorted(score, key=lambda s: (-score[s], s))


def significant_words(text: str) -> list[str]:
    words = [w for w in re.findall(r"\w[\w\-]*", (text or "").lower(), flags=re.UNICODE)
             if w not in STOP and len(w) > 1 and not w.isdigit()]
    return list(dict.fromkeys(words))


def word_coverage(conn: sqlite3.Connection, text: str) -> float:
    """Share of the significant words of the question that occur in the segments (exact word, any segment)."""
    words = significant_words(text)
    if not words:
        return 0.0
    found = 0
    for w in words:
        q = '"' + w.replace('"', '""') + '"'
        try:
            if conn.execute("SELECT 1 FROM segment_fts WHERE segment_fts MATCH ? LIMIT 1", (q,)).fetchone():
                found += 1
        except sqlite3.OperationalError:
            continue
    return found / len(words)


def ranked_segments(conn: sqlite3.Connection, text: str, mode: str, *, embedder=None, index: VectorIndex | None = None,
                    pool: int = POOL) -> list[str]:
    words = search_words(conn, text, pool) if mode in ("parole", "ibrida") else []
    meaning: list[str] = []
    if mode in ("significato", "ibrida"):
        if embedder is None or index is None:
            raise ModelMissing("ricerca per significato non disponibile (modello o vettori mancanti)")
        meaning = index.search(embedder.embed([text], "query")[0], pool)
    if mode == "parole":
        return words
    if mode == "significato":
        return meaning
    return fuse(words, meaning, weights=[word_coverage(conn, text), 1.0])


def by_article(conn: sqlite3.Connection, segment_ids: list[str], limit: int = 10) -> list[dict]:
    """The ranked segments grouped by article (best segment first), with title, DOI, page, section and start."""
    out, seen = [], {}
    for sid in segment_ids:
        r = conn.execute("""SELECT s.article_id, s.page_from, s.page_to, s.section, s.text, a.title, a.doi, a.publication_year AS year
                            FROM segment s LEFT JOIN article a ON a.id = s.article_id WHERE s.id = ?""", (sid,)).fetchone()
        if r is None:
            continue
        key = r["article_id"] or sid
        if key in seen:
            seen[key]["altri_passaggi"] += 1
            continue
        if len(out) >= limit:
            continue
        item = {"article_id": r["article_id"], "titolo": r["title"], "doi": r["doi"], "anno": r["year"],
                "pagine": f"{r['page_from']}" + (f"-{r['page_to']}" if r["page_to"] != r["page_from"] else ""),
                "sezione": r["section"], "passaggio": r["text"][:320], "altri_passaggi": 0}
        seen[key] = item
        out.append(item)
    return out
