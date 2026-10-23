"""PubMed client — NCBI E-utilities (T-009) — free, no key, standard library only.

Checked on the official pages on 2026-10-11 (interactive session):
  - https://www.ncbi.nlm.nih.gov/books/NBK25497/ (last update 2022-11-17): base URL
    https://eutils.ncbi.nlm.nih.gov/entrez/eutils/; without an API key no more than 3 requests per second
    per IP, with a free key 10; `tool` (no spaces) and `email` (the developer's address) in every request;
    over the limit -> error {"error":"API rate limit exceeded",...}; violations -> the IP may be blocked;
    large jobs on weekends or 21:00-05:00 US Eastern; abstracts may be copyrighted.
  - https://www.ncbi.nlm.nih.gov/books/NBK25499/ (last update 2026-03-04): ESearch (db, term, retmax
    default 20, retmode json), EFetch (db, id, retmode xml); more than about 200 UIDs -> use POST.
  - DOI -> PMID: search field tags "Article Identifier [aid]" and "Location ID [lid]" (PubMed help, last
    update 2026-09-01; NLM Technical Bulletin 2008: "10.1136/bmj.a295 [lid]"). ORPHEUS searches both and
    accepts a PMID only if the PubMed record itself carries the same DOI (rules `pubmed-v1`).

ORPHEUS is stricter than the documented limits: no API key, one request at a time, at most
`max_requests_per_second` (default 1), at most 200 PMIDs per EFetch (GET).
Network access happens only here; everything else works on the stored responses.
"""

from __future__ import annotations

import json
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlencode

from .crossref import (MAX_WAIT_SECONDS, Response, _retry_after, _Throttle, certificate_message, is_certificate_error,
                       urllib_transport, user_agent, valid_email)
from .identifiers import IdentifierError, normalize_doi

API_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
SOURCE_ID = "pubmed"
TOOL = "orpheus"                      # registered name of the software (no spaces, as NCBI asks)
TIMEOUT_SECONDS = 30
ESEARCH_RETMAX = 20                   # more than one PMID is already "ambiguous": 20 is plenty to show them
MAX_IDS_PER_FETCH = 200               # NBK25499: above about 200 UIDs use POST; ORPHEUS stays under it with GET
RULES_VERSION = "pubmed-v1"
LICENCE = ("NCBI E-utilities / PubMed: free, no sign-up; NCBI's Disclaimer and Copyright notice applies; "
           "abstracts may be copyrighted — ORPHEUS keeps only identifiers, MeSH and publication types.")
DISCLAIMER_URL = "https://www.ncbi.nlm.nih.gov/home/about/policies/"
_PMID = re.compile(r"^[1-9]\d{0,9}$")


class PubMedError(Exception):
    """The request could not be completed (network down, server errors, too many 429) after the retries."""


class PubMedBlocked(PubMedError):
    """HTTP 403: NCBI has blocked this client. Stop at once; never retry automatically."""


class PubMedCertificateError(PubMedBlocked):
    """NCBI's TLS certificate was not accepted by this PC. Not transient: stop at once."""


def doi_term(doi: str) -> str:
    """The ESearch query for one DOI: quoted (DOIs may contain parentheses) and limited to the two
    identifier fields of PubMed."""
    d = doi.strip().replace('"', "")
    return f'"{d}"[aid] OR "{d}"[lid]'


def _rate_limited(status: int, body: bytes) -> bool:
    """NCBI may announce the rate limit in the body (documented error text), whatever the status."""
    return status == 429 or (body[:200].lstrip().startswith(b"{") and b"rate limit exceeded" in body[:200].lower())


@dataclass
class PubMedClient:
    contact_email: str
    max_requests_per_second: float = 1.0
    max_retries: int = 3
    transport: callable = None                 # None = urllib_transport (looked up at call time; tests replace it)
    sleep: callable = time.sleep
    clock: callable = time.monotonic
    requests_made: int = 0
    log: list[dict] = field(default_factory=list)

    def __post_init__(self):
        if not valid_email(self.contact_email):
            raise ValueError("serve un indirizzo e-mail valido in ORPHEUS_CONTACT_EMAIL (parametro email di NCBI)")
        if not 0 < self.max_requests_per_second <= 3:
            raise ValueError("senza chiave API NCBI permette al massimo 3 richieste al secondo")
        self.contact_email = self.contact_email.strip()
        self._throttle = _Throttle(1.0 / self.max_requests_per_second, clock=self.clock, sleep=self.sleep)

    # -- one GET with throttle and retries
    def _get(self, utility: str, params: list[tuple[str, str]], what: str) -> Response:
        public_params = params + [("tool", TOOL)]
        public = f"{API_BASE}/{utility}?{urlencode(public_params)}"
        url = f"{public}&{urlencode([('email', self.contact_email)])}"
        headers = {"User-Agent": user_agent(self.contact_email)}
        waited, last_problem = 0.0, ""
        for attempt in range(1, self.max_retries + 2):
            self._throttle.wait()
            self.requests_made += 1
            retrieved_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            try:
                status, resp_headers, body = (self.transport or urllib_transport)(url, headers, TIMEOUT_SECONDS)
            except OSError as exc:            # URLError, TimeoutError, ConnectionError, ssl errors…
                if is_certificate_error(exc):
                    raise PubMedCertificateError(certificate_message("eutils.ncbi.nlm.nih.gov", exc)) from exc
                status, resp_headers, body = None, {}, b""
                last_problem = f"rete: {type(exc).__name__}: {getattr(exc, 'reason', exc)}"
            self.log.append({"richiesta": what, "tentativo": attempt, "http": status,
                             "problema": last_problem if status is None else None})
            if status is not None:
                if status == 403:
                    raise PubMedBlocked("NCBI ha risposto 403 (accesso bloccato): verifica interrotta. Non rilanciare "
                                        "subito; controlla l'e-mail di contatto (NCBI scrive lì prima di bloccare).")
                if status == 200 and not _rate_limited(status, body):
                    return Response(status, body, resp_headers, public, attempt, waited, retrieved_at)
                if _rate_limited(status, body):
                    last_problem = f"HTTP {status} (troppe richieste)"
                elif status >= 500:
                    last_problem = f"HTTP {status} (errore del server)"
                else:
                    raise PubMedError(f"risposta inattesa di NCBI: HTTP {status}")
            if attempt > self.max_retries:
                break
            pause = min(MAX_WAIT_SECONDS, _retry_after(resp_headers) or 2.0 ** attempt)
            if status is not None and _rate_limited(status, body):
                self._throttle.min_interval *= 2                      # slow down for the rest of the run
            self.sleep(pause)
            waited += pause
        raise PubMedError(f"{last_problem}; {self.max_retries + 1} tentativi")

    def esearch_doi(self, doi: str) -> Response:
        """ESearch in PubMed for one DOI (JSON). Read the PMIDs with `parse_esearch`."""
        return self._get("esearch.fcgi", [("db", "pubmed"), ("term", doi_term(doi)), ("retmode", "json"),
                                          ("retmax", str(ESEARCH_RETMAX))], f"esearch {doi}")

    def efetch(self, pmids: list[str]) -> Response:
        """EFetch of up to 200 PubMed records (XML). Read them with `parse_pubmed_xml`."""
        ids = [str(p).strip() for p in pmids]
        if not ids or len(ids) > MAX_IDS_PER_FETCH or not all(_PMID.match(p) for p in ids):
            raise ValueError(f"efetch vuole da 1 a {MAX_IDS_PER_FETCH} PMID numerici")
        return self._get("efetch.fcgi", [("db", "pubmed"), ("id", ",".join(ids)), ("retmode", "xml")],
                         f"efetch {len(ids)} PMID")


# ---------------------------------------------------------------- reading the answers

def parse_esearch(body: bytes) -> dict:
    """{'count': int, 'pmids': [...], 'not_found': [...] } from an ESearch JSON answer; ValueError otherwise."""
    data = json.loads(body.decode("utf-8"))
    res = data.get("esearchresult") if isinstance(data, dict) else None
    if not isinstance(res, dict):
        raise ValueError("la risposta non è un risultato di ESearch")
    if res.get("ERROR"):
        raise ValueError(f"ESearch: {res['ERROR']}")
    ids = res.get("idlist")
    if not isinstance(ids, list) or not all(isinstance(i, str) and _PMID.match(i) for i in ids):
        raise ValueError("ESearch senza un elenco di PMID valido")
    try:
        count = int(res.get("count", len(ids)))
    except (TypeError, ValueError):
        raise ValueError("ESearch con un conteggio non numerico") from None
    errors = res.get("errorlist") or {}
    not_found = [str(p) for k in ("phrasesnotfound", "fieldsnotfound") for p in (errors.get(k) or [])]
    return {"count": count, "pmids": ids, "not_found": not_found}


def _text(el) -> str:
    """Text of an element including inline markup (<i>, <sup> …), with spaces normalized."""
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip() if el is not None else ""


def _doi_or_none(value: str) -> str | None:
    try:
        return normalize_doi(value)
    except IdentifierError:
        return None


def _year(article) -> int | None:
    for path in ("Journal/JournalIssue/PubDate/Year", "ArticleDate/Year"):
        y = article.findtext(path)
        if y and y.strip().isdigit():
            return int(y)
    m = re.match(r"\s*(\d{4})", article.findtext("Journal/JournalIssue/PubDate/MedlineDate") or "")
    return int(m.group(1)) if m else None


def parse_pubmed_xml(body: bytes) -> dict[str, dict]:
    """PMID -> record from an EFetch XML answer (PubmedArticleSet). Only the article's own identifiers are
    read (PubmedData/ArticleIdList and Article/ELocationID), never those of its reference list."""
    if b"<!ENTITY" in body[:5000]:
        raise ValueError("XML con dichiarazioni di entità: rifiutato")
    root = ET.fromstring(body)
    if root.tag != "PubmedArticleSet":
        raise ValueError("la risposta non è un PubmedArticleSet")
    out: dict[str, dict] = {}
    for art in root.findall("PubmedArticle"):
        cit = art.find("MedlineCitation")
        if cit is None:
            continue
        pmid = (cit.findtext("PMID") or "").strip()
        if not _PMID.match(pmid):
            continue
        article = cit.find("Article")
        if article is None:
            continue
        dois: list[str] = []
        for el in article.findall("ELocationID"):
            if el.get("EIdType") == "doi":
                d = _doi_or_none(_text(el))
                if d and d not in dois:
                    dois.append(d)
        for el in art.findall("PubmedData/ArticleIdList/ArticleId"):
            if el.get("IdType") == "doi":
                d = _doi_or_none(_text(el))
                if d and d not in dois:
                    dois.append(d)
        pmc = [_text(el) for el in art.findall("PubmedData/ArticleIdList/ArticleId") if el.get("IdType") == "pmc"]
        mesh = []
        for h in cit.findall("MeshHeadingList/MeshHeading"):
            d = h.find("DescriptorName")
            if d is None or not d.get("UI"):
                continue
            quals = [{"ui": q.get("UI"), "name": _text(q), "major": q.get("MajorTopicYN") == "Y"}
                     for q in h.findall("QualifierName")]
            mesh.append({"ui": d.get("UI"), "name": _text(d),
                         "descriptor_major": d.get("MajorTopicYN") == "Y",
                         # PubMed's [majr]: the descriptor or one of its qualifiers is a major topic
                         "major": d.get("MajorTopicYN") == "Y" or any(q["major"] for q in quals),
                         "qualifiers": quals})
        ptypes = [{"ui": p.get("UI"), "name": _text(p)} for p in article.findall("PublicationTypeList/PublicationType")
                  if p.get("UI")]
        corrections = [{"type": c.get("RefType"), "pmid": (c.findtext("PMID") or "").strip() or None,
                        "source": _text(c.find("RefSource"))}
                       for c in cit.findall("CommentsCorrectionsList/CommentsCorrections")
                       if c.get("RefType") in ("RetractionIn", "RetractionOf", "ErratumIn", "ExpressionOfConcernIn",
                                               "UpdateIn", "CorrectedandRepublishedIn")]
        journal = article.find("Journal")
        out[pmid] = {
            "pmid": pmid,
            "dois": dois,
            "pmcid": pmc[0] if pmc else None,
            "title": _text(article.find("ArticleTitle")),
            "journal": _text(journal.find("Title")) if journal is not None else "",
            "journal_iso": _text(journal.find("ISOAbbreviation")) if journal is not None else "",
            "issn": [_text(i) for i in (journal.findall("ISSN") if journal is not None else []) if _text(i)],
            "year": _year(article),
            "mesh": mesh,
            "publication_types": ptypes,
            "corrections": corrections,
        }
    return out


# ---------------------------------------------------------------- rules pubmed-v1

def evaluate(doi: str, pmids: list[str], record: dict | None) -> dict:
    """Outcome of the DOI -> PMID check (rules pubmed-v1):
      - no PMID                         -> not_found
      - more than one PMID              -> ambiguous (none is taken)
      - one PMID, record with that DOI  -> confirmed
      - one PMID, record without the DOI or with another DOI -> mismatch
    With exactly one PMID the record must be given (ValueError otherwise: the caller retries later)."""
    wanted = normalize_doi(doi)
    if not pmids:
        return {"outcome": "not_found", "pmid": None, "reasons": ["PubMed non ha nessun PMID per questo DOI"]}
    if len(pmids) > 1:
        shown = ", ".join(pmids[:10]) + (" …" if len(pmids) > 10 else "")
        return {"outcome": "ambiguous", "pmid": None,
                "reasons": [f"{len(pmids)} PMID per lo stesso DOI ({shown}): nessuno viene preso"]}
    pmid = pmids[0]
    if record is None or record.get("pmid") != pmid:
        raise ValueError(f"manca il record PubMed del PMID {pmid}")
    if wanted in record.get("dois", []):
        return {"outcome": "confirmed", "pmid": pmid, "reasons": [f"il record PubMed {pmid} ha lo stesso DOI"]}
    if not record.get("dois"):
        return {"outcome": "mismatch", "pmid": pmid, "reasons": [f"il record PubMed {pmid} non ha DOI"]}
    return {"outcome": "mismatch", "pmid": pmid,
            "reasons": [f"il record PubMed {pmid} ha un altro DOI: {', '.join(record['dois'])}"]}
