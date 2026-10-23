"""Deterministic check of the reference lists of the complete articles (T-014 parte 2, ADR-0004).

User's yes of 2026-10-11 ("Sì, verificali"); migration 0006 adds the columns used here to `citation`.

For every complete article whose Crossref answer (stored by verify-dois) has a deposited reference list and that
has no `citation` rows yet:
  1. the references are read (references.crossref_references); a reference deposited with only its DOI (Wiley)
     takes year, first author and title from the same reference of the article's Europe PMC full text, when
     ORPHEUS kept it (fulltext_check.text_stored);
  2. every cited DOI is looked up in Crossref: the 240 articles ORPHEUS already confirmed use their stored answer;
     the others are asked in groups of up to 50 DOIs (/works?filter=doi:…&select=…, one request per second), and a
     DOI missing from its group is asked alone (an alias or a DOI of another agency); raw answers are kept;
  3. rules cite-v2 compare year (±1), first author (surname) and title (normalized) of the reference with the
     Crossref record (cite-v2 = cite-v1 + surname tolerance: spaces and hyphens ignored, one edit — a swap of two
     adjacent letters counts as one — from 5 letters, two from 9, one letter cut at the end; the first run flagged
     "Abiss"/"Abbiss", "DeLuca"/"De Luca", "LUC??A", "Kelsye"/"Kelsey", "Dun"/"Dunn") and write one `citation` row per reference, in one transaction per article, with an audit event:
       verified   = year and title agree, the first author does not disagree;
       mismatch   = at least one of year, first author, title disagrees (reasons say which);
       not_found  = Crossref does not know the DOI;
       unverified = no DOI, or not enough to compare (shown as NON VERIFICATA).
An LLM never takes part. A network failure is not an outcome: the articles whose DOIs could not all be asked are
left for the next run.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from . import audit, crossref, europepmc
from .db import transaction
from .dedup import _fold
from .identifiers import IdentifierError, normalize_doi
from .ids import new_id
from .references import crossref_references
from .verify_doi import read_snapshot, store_snapshot

RULES_VERSION = "cite-v2"     # cite-v1 (2026-10-11 21:38, first run) + surname tolerance (typos, spaces, broken letters)
REF_SOURCE = "crossref_deposit"
SNAPSHOT_SUBDIR = "crossref_lotti"
STATUS_IT = {"verified": "verificato", "mismatch": "discordante", "not_found": "non trovato", "unverified": "non verificato"}
YEAR_TOLERANCE = 1            # online and print years often differ by one
TITLE_OVERLAP = 0.8           # share of the shorter title's words found in the other one
MIN_TITLE_WORDS = 3


# ---------------------------------------------------------------- what to check

def candidates(conn: sqlite3.Connection, home: Path) -> tuple[list[dict], list[dict]]:
    """Complete articles with a confirmed Crossref answer and no citation rows yet: [{id, doi, work}], problems."""
    done = {r[0] for r in conn.execute("SELECT DISTINCT citing_article_id FROM citation WHERE ref_source = ?", (REF_SOURCE,))}
    rows = conn.execute(
        """SELECT dc.article_id, dc.source_snapshot_id, a.doi FROM doi_check dc JOIN article a ON a.id = dc.article_id
           WHERE dc.outcome = 'confirmed' AND a.access_level = 'full_text' AND dc.checked_at = (
               SELECT max(d2.checked_at) FROM doi_check d2 WHERE d2.article_id = dc.article_id AND d2.outcome = 'confirmed')
           ORDER BY a.doi""").fetchall()
    out, problems, seen = [], [], set()
    for r in rows:
        if r["article_id"] in seen or r["article_id"] in done:
            continue
        seen.add(r["article_id"])
        try:
            _, body = read_snapshot(conn, r["source_snapshot_id"], home)
            work = crossref.parse_work(body)
        except (OSError, ValueError, KeyError) as exc:
            problems.append({"articolo": r["doi"], "problema": str(exc)})
            continue
        if work.get("reference"):
            out.append({"id": r["article_id"], "doi": r["doi"], "work": work})
    return out, problems


def stored_records(conn: sqlite3.Connection, home: Path) -> dict[str, tuple[dict, str]]:
    """{doi: (crossref.summary, snapshot id)} of every article ORPHEUS confirmed (any access level), no network."""
    out: dict[str, tuple[dict, str]] = {}
    for r in conn.execute(
            """SELECT a.doi, dc.source_snapshot_id FROM doi_check dc JOIN article a ON a.id = dc.article_id
               WHERE dc.outcome = 'confirmed' ORDER BY dc.checked_at"""):
        try:
            _, body = read_snapshot(conn, r["source_snapshot_id"], home)
            out[r["doi"]] = (crossref.summary(crossref.parse_work(body)), r["source_snapshot_id"])
        except (OSError, ValueError, KeyError):
            continue
    return out


def jats_references(conn: sqlite3.Connection, home: Path, article_id: str) -> dict[str, dict]:
    """{cited doi: JATS reference} from the article's kept Europe PMC full text, if any."""
    row = conn.execute(
        """SELECT fetch_snapshot_id FROM fulltext_check WHERE article_id = ? AND text_stored = 1
           ORDER BY checked_at DESC, rowid DESC LIMIT 1""", (article_id,)).fetchone()
    if row is None:
        return {}
    try:
        _, body = read_snapshot(conn, row["fetch_snapshot_id"], home)
        refs = europepmc.parse_jats(body)["references"]
    except (OSError, ValueError):
        return {}
    return {r["doi"]: r for r in refs if r.get("doi")}


def reference_meta(item: dict, jats: dict | None) -> dict:
    """Year, first author, title and text of a reference; empty fields filled from the JATS reference."""
    meta = {"year": item.get("year"), "first_author": item.get("first_author") or "", "title": item.get("title") or "",
            "text": item.get("text") or "", "origine": REF_SOURCE}
    if jats:
        used = False
        for key, jkey in (("year", "year"), ("first_author", "first_author"), ("title", "title")):
            if not meta[key] and jats.get(jkey):
                meta[key] = jats[jkey]
                used = True
        if not meta["text"] and jats.get("text"):
            meta["text"] = jats["text"]
            used = True
        if used:
            meta["origine"] = REF_SOURCE + "+jats"
    return meta


# ---------------------------------------------------------------- rules cite-v1

def _words(text: str) -> list[str]:
    return [w for w in _fold(text).split() if len(w) >= 3 or w.isdigit()]


def _first_family(rec: dict) -> str:
    authors = rec.get("authors") or []
    if not authors:
        return ""
    first = next((a for a in authors if a.get("sequence") == "first"), authors[0])
    return first.get("family") or first.get("name") or ""


def _lev(a: str, b: str, cap: int = 3) -> int:
    """Edit distance with adjacent transpositions counted as one edit (optimal string alignment), stopped early
    above `cap`: "kelsye"/"kelsey" and "synder"/"snyder" are one edit apart."""
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev2: list[int] | None = None
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            v = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            if prev2 is not None and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                v = min(v, prev2[j - 2] + 1)
            cur.append(v)
        if min(cur) > cap:
            return cap + 1
        prev2, prev = prev, cur
    return prev[-1]


def _close(a: str, b: str) -> bool:
    """Same surname despite a typo: one edit from 5 letters, two from 9; or one letter cut at the end of a
    surname of at least 3 letters ("dun"/"dunn")."""
    if not a or not b:
        return False
    if a == b:
        return True
    short, long_ = sorted((a, b), key=len)
    if len(short) >= 3 and len(long_) == len(short) + 1 and long_.startswith(short):
        return True
    allowed = 2 if len(long_) >= 9 else (1 if len(long_) >= 5 else 0)
    return allowed > 0 and _lev(a, b, allowed) <= allowed


_TRANSLIT = str.maketrans({"æ": "ae", "Æ": "AE", "ø": "o", "Ø": "O", "œ": "oe", "Œ": "OE", "ß": "ss", "ł": "l",
                           "Ł": "L", "đ": "d", "Đ": "D", "ð": "d", "þ": "th", "ı": "i", "ĸ": "k"})


def _fold_name(text: str) -> str:
    """_fold after spelling out the letters that have no accent to strip (Kjær -> kjaer, Şenışık -> senisik)."""
    return _fold((text or "").translate(_TRANSLIT))


_PARTICLES = {"de", "da", "di", "del", "della", "van", "von", "der", "den", "la", "le", "du", "dos", "das"}


def _only_initials(text: str) -> bool:
    """'W.', 'J.K.', 'de CN': no surname to compare (initials are short and upper-case or end with a dot)."""
    tokens = [t for t in re.split(r"[\s,;]+", text or "") if t]
    if not tokens:
        return False
    def initial(t: str) -> bool:
        core = t.replace(".", "")
        return t.lower() in _PARTICLES or (len(core) <= 2 and (t.endswith(".") or core.isupper()))
    return all(initial(t) for t in tokens)


def author_match(ref_author: str, family: str) -> bool | None:
    """True when the reference's first author is the record's first author (surname), tolerating typos,
    spaces/hyphens and broken letters; None when one of the two is missing or the reference gives only
    initials ("W.")."""
    fam = _fold_name(family).split()
    ref = _fold_name(ref_author).split()
    if not fam or not ref or _only_initials(ref_author):
        return None
    if all(t in ref for t in fam) or ref[0] in fam:                    # cite-v1
        return True
    if "?" in family or "\ufffd" in family:                             # letters lost in Crossref ("B??" for "Bø")
        parts = [re.sub(r"\s+", "", _fold_name(p)) for p in re.split(r"[?\ufffd]+", family)]
        pattern = ".{1,2}".join(re.escape(p) for p in parts)              # one lost letter (two when spelled out)
        compact = "".join(ref)
        if any(re.fullmatch(pattern, "".join(ref[:k])) for k in range(1, len(ref) + 1)) or re.fullmatch(pattern, compact):
            return True
    fam_joined = "".join(fam)
    for k in range(1, len(ref) + 1):
        cand = "".join(ref[:k])
        if _close(cand, fam_joined) or any(len(t) >= 4 and _close(cand, t) for t in fam):
            return True
    return False


def compare(meta: dict, rec: dict) -> dict:
    """{'year', 'author', 'title'}: True (agrees), False (disagrees) or None (not comparable)."""
    text_words = set(_fold(meta["text"]).split()) if meta["text"] else set()
    # year
    ref_year = meta["year"]
    if ref_year is None and meta["text"]:
        m = re.search(r"\b(19\d{2}|20\d{2})\b", meta["text"])
        ref_year = int(m.group(1)) if m else None
    years = rec.get("years") or []
    year = None if ref_year is None or not years else any(abs(ref_year - y) <= YEAR_TOLERANCE for y in years)
    # first author
    family = _fold(_first_family(rec)).split()
    ref_author = _fold(meta["first_author"]).split()
    if family and ref_author:
        author = author_match(meta["first_author"], _first_family(rec))
    elif family and text_words:
        author = True if all(t in text_words for t in family) else None
    else:
        author = None
    # title
    cr_title = " ".join(t for t in (rec.get("title"), rec.get("subtitle")) if t)
    a, b = _fold(meta["title"]), _fold(cr_title)
    if a and b:
        if a == b or (min(len(a), len(b)) >= 20 and (a.startswith(b) or b.startswith(a))):
            title = True
        else:
            wa, wb = set(_words(a)), set(_words(b))
            shorter = min(len(wa), len(wb))
            title = shorter >= MIN_TITLE_WORDS and len(wa & wb) / shorter >= TITLE_OVERLAP
    elif b and text_words:
        wb = set(_words(b))
        title = True if len(wb) >= MIN_TITLE_WORDS and len(wb & text_words) / len(wb) >= TITLE_OVERLAP else None
    else:
        title = None
    return {"year": year, "author": author, "title": title, "anno_riferimento": ref_year,
            "anni_crossref": years[:3], "primo_autore_crossref": _first_family(rec)}


def evaluate(meta: dict, rec: dict | None, doi: str | None) -> tuple[str, list[str], dict]:
    """(status, reasons in Italian, checks) for one reference (rules cite-v1)."""
    if not doi:
        return "unverified", ["riferimento senza DOI: non verificabile con Crossref"], {"origine": meta["origine"]}
    if rec is None:
        return "not_found", ["Crossref non conosce questo DOI (può essere registrato presso un'altra agenzia, "
                             "per esempio DataCite)"], {"origine": meta["origine"]}
    c = compare(meta, rec)
    checks = {**c, "origine": meta["origine"]}
    names = {"year": "anno", "author": "primo autore", "title": "titolo"}
    wrong = [names[k] for k in ("year", "author", "title") if c[k] is False]
    if wrong:
        detail = []
        if c["year"] is False:
            detail.append(f"anno {c['anno_riferimento']} contro {', '.join(map(str, c['anni_crossref']))}")
        if c["author"] is False:
            detail.append(f"primo autore '{meta['first_author']}' contro '{c['primo_autore_crossref']}'")
        if c["title"] is False:
            detail.append("titolo diverso")
        return "mismatch", [f"discorda: {', '.join(wrong)} ({'; '.join(detail)})"], checks
    if c["year"] and c["title"]:
        return "verified", ["anno e titolo coincidono" + (", primo autore anche" if c["author"] else
                                                           ", primo autore non confrontabile")], checks
    missing = [names[k] for k in ("year", "author", "title") if c[k] is None]
    return "unverified", [f"il DOI esiste in Crossref ma non si può confrontare: {', '.join(missing)}"], checks


# ---------------------------------------------------------------- running

@dataclass
class RunResult:
    articles: list[dict] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=lambda: {k: 0 for k in STATUS_IT})
    requests: int = 0
    batches: int = 0
    singles: int = 0
    errors: list[dict] = field(default_factory=list)
    stopped: str | None = None
    skipped_articles: int = 0
    problems: list[dict] = field(default_factory=list)


def _doi_ok(d: str | None) -> str | None:
    try:
        return normalize_doi(d) if d else None
    except IdentifierError:
        return None


def estimate(n_dois: int) -> dict:
    groups = math.ceil(n_dois / crossref.MAX_DOIS_PER_REQUEST) if n_dois else 0
    return {"doi_da_chiedere": n_dois, "richieste_minime": groups, "minuti_stimati": max(1, math.ceil(groups * 2 / 60)) if groups else 0}


def plan(conn: sqlite3.Connection, home: Path, limit: int | None = None) -> dict:
    """What a run would do, without network and without writing."""
    todo, problems = candidates(conn, home)
    if limit is not None:
        todo = todo[:max(0, limit)]
    stored = stored_records(conn, home)
    dois, refs = set(), 0
    for art in todo:
        for item in crossref_references(art["work"])["items"]:
            refs += 1
            if item["doi"] and item["doi"] not in stored:
                dois.add(item["doi"])
    return {"articoli_da_verificare": len(todo), "riferimenti": refs, **estimate(len(dois)), "problemi": problems}


def run(conn: sqlite3.Connection, *, home: Path, archive_dir: Path, client: crossref.CrossrefClient,
        limit: int | None = None, actor: str = "cli", max_consecutive_failures: int = 3) -> RunResult:
    out = RunResult()
    todo, out.problems = candidates(conn, home)
    if limit is not None:
        out.skipped_articles = max(0, len(todo) - max(0, limit))
        todo = todo[:max(0, limit)]
    stored = stored_records(conn, home)
    corpus = {r["doi"]: r["id"] for r in conn.execute("SELECT id, doi FROM article WHERE doi IS NOT NULL")}
    prepared = []
    wanted: list[str] = []
    for art in todo:
        refs = crossref_references(art["work"])["items"]
        jats = jats_references(conn, home, art["id"])
        prepared.append((art, refs, jats))
        for item in refs:
            if item["doi"] and item["doi"] not in stored and item["doi"] not in wanted:
                wanted.append(item["doi"])
    found: dict[str, tuple[dict | None, str]] = {}      # doi -> (summary or None, snapshot id)
    failed: set[str] = set()

    def keep(resp) -> str:
        with transaction(conn):
            return store_snapshot(conn, resp, home=home, archive_dir=archive_dir, subdir=SNAPSHOT_SUBDIR)

    in_a_row = 0
    batchable = [d for d in wanted if "," not in d]
    singles = [d for d in wanted if "," in d]
    step = crossref.MAX_DOIS_PER_REQUEST
    for i in range(0, len(batchable), step):
        group = batchable[i:i + step]
        try:
            resp = client.works_by_dois(group)
            items = crossref.parse_work_list(resp.body)
        except crossref.CrossrefBlocked as exc:
            out.errors.append({"gruppo": i // step + 1, "errore": str(exc)})
            out.stopped = str(exc)
            failed.update(batchable[i:])
            break
        except (crossref.CrossrefError, ValueError) as exc:
            out.errors.append({"gruppo": i // step + 1, "errore": str(exc)})
            failed.update(group)
            in_a_row += 1
            if in_a_row >= max_consecutive_failures:
                out.stopped = f"{max_consecutive_failures} errori di fila: Crossref non risponde, riprova più tardi"
                failed.update(batchable[i + step:])
                break
            continue
        in_a_row = 0
        out.batches += 1
        snap = keep(resp)
        got = {}
        for it in items:
            d = _doi_ok(it.get("DOI"))
            if d:
                got[d] = crossref.summary(it)
        for d in group:
            if d in got:
                found[d] = (got[d], snap)
            else:
                singles.append(d)                         # alias or another agency: ask alone
    if out.stopped is None:
        for d in singles:
            try:
                resp = client.get_work(d)
            except crossref.CrossrefBlocked as exc:
                out.errors.append({"doi": d, "errore": str(exc)})
                out.stopped = str(exc)
                failed.update(x for x in singles if x not in found)
                break
            except crossref.CrossrefError as exc:
                out.errors.append({"doi": d, "errore": str(exc)})
                failed.add(d)
                in_a_row += 1
                if in_a_row >= max_consecutive_failures:
                    out.stopped = f"{max_consecutive_failures} errori di fila: Crossref non risponde, riprova più tardi"
                    failed.update(x for x in singles if x not in found)
                    break
                continue
            in_a_row = 0
            out.singles += 1
            snap = keep(resp)
            if resp.status == 404:
                found[d] = (None, snap)
                continue
            try:
                found[d] = (crossref.summary(crossref.parse_work(resp.body)), snap)
            except ValueError as exc:
                out.errors.append({"doi": d, "errore": str(exc)})
                failed.add(d)
    else:
        failed.update(singles)

    for art, refs, jats in prepared:
        if any(item["doi"] in failed for item in refs if item["doi"]):
            out.skipped_articles += 1
            continue
        counts = {k: 0 for k in STATUS_IT}
        now = audit.utc_now()
        with transaction(conn):
            for item in refs:
                doi = item["doi"]
                meta = reference_meta(item, jats.get(doi) if doi else None)
                rec, snap = (stored[doi] if doi in stored else found.get(doi, (None, None))) if doi else (None, None)
                status, reasons, checks = evaluate(meta, rec, doi)
                raw = meta["text"] or " ".join(p for p in (meta["first_author"], f"({meta['year']})" if meta["year"] else "",
                                                           meta["title"], item.get("journal") or "") if p).strip()
                looked_up = doi is not None
                conn.execute(
                    """INSERT INTO citation (id, citing_article_id, cited_article_id, cited_doi, cited_raw,
                           verification_status, verified_at, verification_source, source_snapshot_id, ref_source,
                           ref_position, ref_key, rules_version, reasons, checks)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (new_id("citation"), art["id"], corpus.get(doi) if doi else None, doi, raw or None, status,
                     now if looked_up else None, crossref.SOURCE_ID if looked_up else None, snap if looked_up else None,
                     REF_SOURCE, item["position"], item["key"] or None, RULES_VERSION,
                     json.dumps(reasons, ensure_ascii=False), json.dumps(checks, ensure_ascii=False)))
                counts[status] += 1
            audit.record(conn, actor=actor, action="verify_citations", entity_type="article", entity_id=art["id"],
                         payload={"doi": art["doi"], "rules": RULES_VERSION, "references": len(refs), **counts},
                         in_transaction=True)
        for k, v in counts.items():
            out.counts[k] += v
        out.articles.append({"doi": art["doi"], "riferimenti": len(refs), **{STATUS_IT[k]: v for k, v in counts.items()}})
    out.requests = client.requests_made
    return out


def _citing_references(conn: sqlite3.Connection, home: Path) -> dict[str, dict[int, dict]]:
    """{citing article id: {position: reference}} from the newest confirmed Crossref answers (no network)."""
    out: dict[str, dict[int, dict]] = {}
    for r in conn.execute(
            """SELECT dc.article_id, dc.source_snapshot_id FROM doi_check dc
               WHERE dc.outcome = 'confirmed' AND dc.checked_at = (
                   SELECT max(d2.checked_at) FROM doi_check d2 WHERE d2.article_id = dc.article_id AND d2.outcome = 'confirmed')
                 AND dc.article_id IN (SELECT DISTINCT citing_article_id FROM citation WHERE ref_source = ?)""", (REF_SOURCE,)):
        if r["article_id"] in out:
            continue
        try:
            _, body = read_snapshot(conn, r["source_snapshot_id"], home)
            items = crossref_references(crossref.parse_work(body))["items"]
        except (OSError, ValueError, KeyError):
            continue
        out[r["article_id"]] = {i["position"]: i for i in items}
    return out


def _records_in(conn: sqlite3.Connection, home: Path, snapshot_id: str, cache: dict) -> tuple[dict[str, dict], dict | None]:
    """({doi: crossref.summary}, the record when the answer is ONE /works/{doi} record) of a stored Crossref answer
    (a /works list, one record, or a 404). A single record answers the DOI asked even when it carries another
    DOI (an alias), as in `run`."""
    if snapshot_id in cache:
        return cache[snapshot_id]
    found: dict[str, dict] = {}
    single = None
    try:
        status, body = read_snapshot(conn, snapshot_id, home)
        if status == 200:
            try:
                works = crossref.parse_work_list(body)
            except ValueError:
                works = [crossref.parse_work(body)]
                single = crossref.summary(works[0])
            for w in works:
                d = _doi_ok(w.get("DOI"))
                if d:
                    found[d] = crossref.summary(w)
    except (OSError, ValueError, KeyError):
        found, single = {}, None
    cache[snapshot_id] = (found, single)
    return found, single


def rivaluta(conn: sqlite3.Connection, *, home: Path, actor: str = "cli") -> dict:
    """Apply the current rules again to the citation rows already written, WITHOUT NETWORK: the Crossref answers
    are the stored ones; the reference metadata is completed by the Europe PMC texts kept since the first run.
    Rows whose outcome, reasons or checks change are updated in one transaction (rules_version = current), with
    one audit event listing the transitions. verified_at keeps the date of the Crossref look-up."""
    refs = _citing_references(conn, home)
    jats_cache: dict[str, dict] = {}
    snap_cache: dict = {}
    rows = conn.execute("SELECT * FROM citation WHERE ref_source = ? AND cited_doi IS NOT NULL", (REF_SOURCE,)).fetchall()
    updates, transitions, skipped = [], {}, 0
    stored = None
    for row in rows:
        item = refs.get(row["citing_article_id"], {}).get(row["ref_position"])
        doi = row["cited_doi"]
        if item is None or item["doi"] != doi:
            skipped += 1
            continue
        if row["citing_article_id"] not in jats_cache:
            jats_cache[row["citing_article_id"]] = jats_references(conn, home, row["citing_article_id"])
        meta = reference_meta(item, jats_cache[row["citing_article_id"]].get(doi))
        rec = None
        if row["source_snapshot_id"]:
            found, single = _records_in(conn, home, row["source_snapshot_id"], snap_cache)
            rec = found.get(doi) or single
            if rec is None and doi not in found:
                if stored is None:
                    stored = stored_records(conn, home)
                rec = stored.get(doi, (None, None))[0]
        status, reasons, checks = evaluate(meta, rec, doi)
        new = (status, json.dumps(reasons, ensure_ascii=False), json.dumps(checks, ensure_ascii=False))
        if new != (row["verification_status"], row["reasons"], row["checks"]) or row["rules_version"] != RULES_VERSION:
            updates.append((row["id"], *new))
            if status != row["verification_status"]:
                key = f"{STATUS_IT[row['verification_status']]} -> {STATUS_IT[status]}"
                transitions[key] = transitions.get(key, 0) + 1
    if updates:
        with transaction(conn):
            for cid, status, reasons, checks in updates:
                conn.execute("""UPDATE citation SET verification_status = ?, reasons = ?, checks = ?, rules_version = ?
                                WHERE id = ?""", (status, reasons, checks, RULES_VERSION, cid))
            audit.record(conn, actor=actor, action="reevaluate_citations",
                         payload={"rules": RULES_VERSION, "rows_read": len(rows), "rows_updated": len(updates),
                                  "transitions": transitions, "skipped": skipped}, in_transaction=True)
    return {"regole": RULES_VERSION, "righe_rilette": len(rows), "righe_aggiornate": len(updates),
            "esiti_cambiati": dict(sorted(transitions.items(), key=lambda kv: -kv[1])), "righe_saltate": skipped}


def overall(conn: sqlite3.Connection) -> dict:
    rows = conn.execute("SELECT verification_status s, count(*) n FROM citation WHERE ref_source = ? GROUP BY s",
                        (REF_SOURCE,)).fetchall()
    counts = {STATUS_IT[k].replace(" ", "_"): 0 for k in STATUS_IT}
    for r in rows:
        counts[STATUS_IT[r["s"]].replace(" ", "_")] = r["n"]
    one = lambda sql: conn.execute(sql, (REF_SOURCE,)).fetchone()[0]                # noqa: E731
    return {**counts, "righe_citation": sum(counts.values()),
            "articoli_con_citazioni": one("SELECT count(DISTINCT citing_article_id) FROM citation WHERE ref_source = ?"),
            "citazioni_verso_articoli_di_orpheus": one(
                "SELECT count(*) FROM citation WHERE ref_source = ? AND cited_article_id IS NOT NULL")}


def mismatches(conn: sqlite3.Connection, limit: int = 30) -> list[dict]:
    rows = conn.execute(
        """SELECT a.doi citing, c.ref_position, c.cited_doi, c.reasons FROM citation c JOIN article a ON a.id = c.citing_article_id
           WHERE c.ref_source = ? AND c.verification_status = 'mismatch' ORDER BY a.doi, c.ref_position LIMIT ?""",
        (REF_SOURCE, limit)).fetchall()
    return [{"articolo": r["citing"], "posizione": r["ref_position"], "doi_citato": r["cited_doi"],
             "motivo": "; ".join(json.loads(r["reasons"]))} for r in rows]
