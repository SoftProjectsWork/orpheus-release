"""Europe PMC client (T-010) — free, no key, standard library only.

What was checked, and where (2026-10-11, interactive session):
  - The Europe PMC pages (europepmc.org: RestfulWebService, developers, help) can NOT be read from Claude's
    sandbox: robots.txt forbids automatic reading, and a test call of the API through WebFetch was refused
    (429). Endpoints and field names below are therefore the ones in common use and are CONFIRMED ONLY by the
    first run on the PC (`europepmc.cmd`, read-only): until then the parser is lenient and reports what it
    does not find instead of guessing.
  - EMBL-EBI Terms of Use (https://www.ebi.ac.uk/about/terms-of-use, last revised 2024-02-05), which cover
    Europe PMC's services: no numeric limits; use "to a level that prevents, or is likely to prevent, EMBL-EBI
    providing services to others" -> the user is blocked (EMBL-EBI tries to contact first); access only
    "through the service interfaces provided"; attribution expected; no restriction on the data beyond those
    of the data owner — for full texts, each article's own licence.

API as used here (to be confirmed by the first run):
  GET {API_BASE}/search?query=DOI:"<doi>"&resultType=core&format=json&pageSize=25
      -> {"hitCount": n, "resultList": {"result": [{id, source, pmid, pmcid, doi, title, pubYear, isOpenAccess,
          inEPMC, inPMC, hasPDF, license, journalInfo, fullTextUrlList}, ...]}}
  GET {API_BASE}/<PMCID>/fullTextXML -> JATS XML (open-access subset and author manuscripts); 404 if none.

ORPHEUS is prudent: one request at a time, at most 1 per second (config may only lower it), retries on network
errors, 429 and 5xx (2, 4, 8 s or Retry-After), stop at once on 403 or on a refused certificate. The contact
e-mail goes only in the User-Agent (as for Crossref), never in a URL.
"""

from __future__ import annotations

import json
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import quote, urlencode

from .crossref import (MAX_WAIT_SECONDS, Response, _retry_after, _Throttle, certificate_message, is_certificate_error,
                       urllib_transport, user_agent, valid_email)
from .identifiers import IdentifierError, normalize_doi

API_BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"
HOST = "www.ebi.ac.uk"
SOURCE_ID = "europepmc"
TIMEOUT_SECONDS = 60                   # a full text can be a few MB
PAGE_SIZE = 25                         # more than one record with the DOI is already "ambiguous"
RULES_VERSION = "epmc-v1"
TERMS_URL = "https://www.ebi.ac.uk/about/terms-of-use"
ATTRIBUTION = ("Dati da Europe PMC (EMBL-EBI), https://europepmc.org - condizioni d'uso EMBL-EBI: " + TERMS_URL
               + "; ogni testo integrale ha la sua licenza, riportata da ORPHEUS.")
_PMCID = re.compile(r"^PMC[1-9]\d{0,9}$")
_PMID = re.compile(r"^[1-9]\d{0,9}$")
XLINK = "{http://www.w3.org/1999/xlink}"


class EuropePMCError(Exception):
    """The request could not be completed (network down, server errors, too many 429) after the retries."""


class EuropePMCBlocked(EuropePMCError):
    """HTTP 403: access refused. Stop at once; never retry automatically."""


class EuropePMCCertificateError(EuropePMCBlocked):
    """The server's TLS certificate was not accepted by this PC. Not transient: stop at once."""


def doi_query(doi: str) -> str:
    """Europe PMC query for one DOI, quoted (DOIs may contain parentheses or colons)."""
    return 'DOI:"' + doi.strip().replace('"', "") + '"'


@dataclass
class EuropePMCClient:
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
            raise ValueError("serve un indirizzo e-mail valido in ORPHEUS_CONTACT_EMAIL (contatto nello User-Agent)")
        if not 0 < self.max_requests_per_second <= 1:
            raise ValueError("ORPHEUS chiede a Europe PMC al massimo 1 richiesta al secondo")
        self.contact_email = self.contact_email.strip()
        self._throttle = _Throttle(1.0 / self.max_requests_per_second, clock=self.clock, sleep=self.sleep)

    def _get(self, url: str, what: str, *, ok_statuses: tuple[int, ...] = (200,)) -> Response:
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
                    raise EuropePMCCertificateError(certificate_message(HOST, exc)) from exc
                status, resp_headers, body = None, {}, b""
                last_problem = f"rete: {type(exc).__name__}: {getattr(exc, 'reason', exc)}"
            self.log.append({"richiesta": what, "tentativo": attempt, "http": status,
                             "problema": last_problem if status is None else None})
            if status is not None:
                if status == 403:
                    raise EuropePMCBlocked("Europe PMC ha risposto 403 (accesso rifiutato): ricerca interrotta. Non "
                                           "rilanciare subito; EMBL-EBI di solito scrive prima all'indirizzo di contatto.")
                if status in ok_statuses:
                    return Response(status, body, resp_headers, url, attempt, waited, retrieved_at)
                if status == 429:
                    last_problem = "HTTP 429 (troppe richieste)"
                elif status >= 500:
                    last_problem = f"HTTP {status} (errore del server)"
                else:
                    raise EuropePMCError(f"risposta inattesa di Europe PMC: HTTP {status}")
            if attempt > self.max_retries:
                break
            pause = min(MAX_WAIT_SECONDS, _retry_after(resp_headers) or 2.0 ** attempt)
            if status == 429:
                self._throttle.min_interval *= 2                      # slow down for the rest of the run
            self.sleep(pause)
            waited += pause
        raise EuropePMCError(f"{what}: {last_problem}; {self.max_retries + 1} tentativi")

    def search_doi(self, doi: str) -> Response:
        """Search one DOI (JSON, resultType=core). Read it with `parse_search`."""
        params = [("query", doi_query(doi)), ("resultType", "core"), ("format", "json"), ("pageSize", str(PAGE_SIZE))]
        return self._get(f"{API_BASE}/search?{urlencode(params)}", f"search {doi}")

    def full_text_xml(self, pmcid: str) -> Response:
        """JATS full text of one PMCID; returns the 404 answer too (no full text): check `.status`."""
        if not _PMCID.match(pmcid or ""):
            raise ValueError(f"PMCID non valido: {pmcid!r}")
        return self._get(f"{API_BASE}/{quote(pmcid)}/fullTextXML", f"fullTextXML {pmcid}", ok_statuses=(200, 404))


# ---------------------------------------------------------------- reading the search answer

def _yn(value) -> bool | None:
    if isinstance(value, str) and value.strip().upper() in ("Y", "N"):
        return value.strip().upper() == "Y"
    return None


def _doi_or_none(value) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return normalize_doi(value)
    except IdentifierError:
        return None


def parse_search(body: bytes) -> dict:
    """{'hit_count': int, 'records': [...], 'missing_fields': [...]} from a search answer; ValueError otherwise.

    Fields that are absent are reported in `missing_fields` (the first run on the PC confirms the format)."""
    data = json.loads(body.decode("utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("resultList"), dict):
        raise ValueError("la risposta non è un risultato di ricerca di Europe PMC (manca resultList)")
    results = data["resultList"].get("result") or []
    if not isinstance(results, list):
        raise ValueError("resultList.result non è un elenco")
    try:
        hit_count = int(data.get("hitCount", len(results)))
    except (TypeError, ValueError):
        raise ValueError("hitCount non numerico") from None
    records, missing = [], set()
    for r in results:
        if not isinstance(r, dict):
            continue
        for key in ("doi", "isOpenAccess", "inEPMC"):          # "license" is absent when there is none
            if key not in r:
                missing.add(key)
        pmcid = str(r.get("pmcid") or "").strip().upper() or None
        pmid = str(r.get("pmid") or "").strip() or None
        urls = []
        for u in ((r.get("fullTextUrlList") or {}).get("fullTextUrl") or []):
            if isinstance(u, dict):
                urls.append({k: u.get(k) for k in ("availability", "availabilityCode", "documentStyle", "site", "url")})
        journal = ((r.get("journalInfo") or {}).get("journal") or {}) if isinstance(r.get("journalInfo"), dict) else {}
        year = str(r.get("pubYear") or "").strip()
        records.append({
            "id": str(r.get("id") or ""), "source": str(r.get("source") or ""),
            "pmid": pmid if pmid and _PMID.match(pmid) else None,
            "pmcid": pmcid if pmcid and _PMCID.match(pmcid) else None,
            "doi": _doi_or_none(r.get("doi")),
            "title": re.sub(r"\s+", " ", str(r.get("title") or "")).strip(),
            "year": int(year) if year.isdigit() else None,
            "journal": str(journal.get("medlineAbbreviation") or journal.get("title") or ""),
            "is_open_access": _yn(r.get("isOpenAccess")), "in_epmc": _yn(r.get("inEPMC")),
            "in_pmc": _yn(r.get("inPMC")), "has_pdf": _yn(r.get("hasPDF")),
            "license": (str(r["license"]).strip() or None) if r.get("license") else None,
            "full_text_urls": urls,
        })
    return {"hit_count": hit_count, "records": records, "missing_fields": sorted(missing)}


# ---------------------------------------------------------------- rules epmc-v1

def _work_keys(r: dict) -> set[str]:
    keys = {k for k in (r["pmid"] and f"pmid:{r['pmid']}", r["pmcid"] and f"pmc:{r['pmcid']}") if k}
    return keys or {f"{r['source']}:{r['id']}"}


def _merge(records: list[dict]) -> list[dict]:
    """Records of the same work (sharing a PMID or a PMCID) become one; the record with a PMCID leads and the
    others fill its empty fields."""
    groups: list[tuple[set[str], list[dict]]] = []
    for r in records:
        keys, recs = set(_work_keys(r)), [r]
        rest = []
        for g_keys, g_recs in groups:
            if g_keys & keys:
                keys |= g_keys
                recs += g_recs
            else:
                rest.append((g_keys, g_recs))
        groups = rest + [(keys, recs)]
    out = []
    for _, recs in groups:
        lead = dict(next((x for x in recs if x["pmcid"]), recs[0]))
        for x in recs:
            for k, v in x.items():
                if lead.get(k) in (None, "", []) and v not in (None, "", []):
                    lead[k] = v
        out.append(lead)
    return out


def evaluate(doi: str, search: dict, known_pmid: str | None = None) -> dict:
    """Outcome of the DOI look-up (rules epmc-v1):
      - no record                                   -> not_found
      - records, none with this DOI                 -> mismatch
      - records with this DOI of more than one work -> ambiguous (none is taken)
      - one work with this DOI, but a PMID different from the one PubMed confirmed -> mismatch
      - one work with this DOI                      -> found
    """
    wanted = normalize_doi(doi)
    recs = search.get("records") or []
    if not recs:
        return {"outcome": "not_found", "record": None, "reasons": ["Europe PMC non ha nessun record per questo DOI"]}
    same = [r for r in recs if r["doi"] == wanted]
    if not same:
        others = ", ".join(sorted({r["doi"] or "senza DOI" for r in recs})[:5])
        return {"outcome": "mismatch", "record": None,
                "reasons": [f"{len(recs)} record ma nessuno con questo DOI ({others})"]}
    works = _merge(same)
    if len(works) > 1:
        ids = ", ".join(w["pmcid"] or w["pmid"] or f"{w['source']}:{w['id']}" for w in works[:5])
        return {"outcome": "ambiguous", "record": None,
                "reasons": [f"{len(works)} lavori diversi con lo stesso DOI ({ids}): nessuno viene preso"]}
    rec = works[0]
    if known_pmid and rec["pmid"] and rec["pmid"] != known_pmid:
        return {"outcome": "mismatch", "record": rec,
                "reasons": [f"Europe PMC indica il PMID {rec['pmid']}, PubMed aveva confermato {known_pmid}"]}
    return {"outcome": "found", "record": rec, "reasons": ["un record con lo stesso DOI"]}


def availability(rec: dict | None) -> str:
    """'open_access' (full text in Europe PMC, open-access subset), 'free_not_oa' (full text in Europe PMC
    but not open access, e.g. an author manuscript), 'metadata_only'."""
    if not rec or not rec.get("pmcid") or not rec.get("in_epmc"):
        return "metadata_only"
    return "open_access" if rec.get("is_open_access") else "free_not_oa"


# ---------------------------------------------------------------- licences

_CC = re.compile(r"creativecommons\.org/(licenses|publicdomain)/([a-z\-]+)/?(\d\.\d)?", re.I)


def normalize_licence(value: str | None) -> str | None:
    """'http://creativecommons.org/licenses/by-nc-nd/4.0/' -> 'CC BY-NC-ND 4.0'; 'cc by' -> 'CC BY';
    anything else is returned as it is (stripped), None when empty."""
    if not value or not str(value).strip():
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    m = _CC.search(text)
    if m:
        kind, code, version = m.group(1).lower(), m.group(2).lower(), m.group(3)
        if kind == "publicdomain":
            name = "CC0" if code == "zero" else "Public Domain Mark"
        else:
            name = "CC " + code.upper()
        return f"{name} {version}" if version else name
    m = re.fullmatch(r"cc[ \-_]?(0|by(?:[ \-_](?:nc|nd|sa)){0,2})", text, re.I)
    if m:
        code = m.group(1).upper().replace(" ", "-").replace("_", "-")
        return "CC0" if code == "0" else "CC " + code
    return text


# ---------------------------------------------------------------- reading the JATS full text

_CC_TEXT = re.compile(r"\bCC[ \-]?(0|BY(?:[ \-](?:NC|ND|SA)){0,2})\b(?:[ \-]?(\d\.\d))?")


def licence_in_text(text: str) -> str | None:
    """A Creative Commons licence named in a sentence ('... under the CC BY-NC-ND 4.0 license')."""
    url = normalize_licence(text) if text and "creativecommons.org" in text else None
    if url and url.startswith(("CC", "Public")):
        return url
    m = _CC_TEXT.search(text or "")
    if not m:
        return None
    code = m.group(1).replace(" ", "-")
    name = "CC0" if code == "0" else "CC " + code
    return f"{name} {m.group(2)}" if m.group(2) else name


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


SKIP_IN_TEXT = {"fig", "table-wrap", "disp-formula", "supplementary-material", "graphic", "media", "fn-group"}


def _text(el, skip: set[str] = frozenset()) -> str:
    """Text of an element with its inline markup, without the subtrees named in `skip`; spaces normalized."""
    if el is None:
        return ""
    parts: list[str] = []

    def walk(node):
        if node.text:
            parts.append(node.text)
        for child in node:
            if _local(child.tag) not in skip:
                walk(child)
            if child.tail:
                parts.append(child.tail)
    walk(el)
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def _children(el, name: str) -> list:
    return [c for c in el if _local(c.tag) == name] if el is not None else []


def _find(el, path: str):
    """First descendant following a '/'-separated path of local names (namespaces ignored)."""
    nodes = [el]
    for name in path.split("/"):
        nodes = [c for n in nodes if n is not None for c in n if _local(c.tag) == name]
        if not nodes:
            return None
    return nodes[0]


def _iter(el, name: str):
    return (n for n in el.iter() if _local(n.tag) == name)


def _words(text: str) -> int:
    return len(re.findall(r"\w+", text))


def _sections(body) -> list[dict]:
    out: list[dict] = []

    def walk(sec, level):
        title = _text(_find(sec, "title"))
        paras = [_text(p, SKIP_IN_TEXT) for p in sec if _local(p.tag) == "p"]
        text = "\n".join(p for p in paras if p)
        out.append({"level": level, "title": title, "sec_type": sec.get("sec-type") or "",
                    "text": text, "words": _words(text)})
        for child in _children(sec, "sec"):
            walk(child, level + 1)
    loose = [_text(p, SKIP_IN_TEXT) for p in body if _local(p.tag) == "p"]
    if any(loose):
        text = "\n".join(p for p in loose if p)
        out.append({"level": 1, "title": "", "sec_type": "", "text": text, "words": _words(text)})
    for sec in _children(body, "sec"):
        walk(sec, 1)
    return out


def _references(root) -> list[dict]:
    refs = []
    for ref in _iter(root, "ref"):
        cit = next((c for c in ref if _local(c.tag) in ("element-citation", "mixed-citation", "citation",
                                                          "nlm-citation")), None)
        ids = {"doi": None, "pmid": None, "pmcid": None}
        for pid in (_iter(cit, "pub-id") if cit is not None else []):
            kind = (pid.get("pub-id-type") or "").lower()
            value = _text(pid)
            if kind == "doi":
                ids["doi"] = ids["doi"] or _doi_or_none(value)
            elif kind == "pmid" and _PMID.match(value):
                ids["pmid"] = ids["pmid"] or value
            elif kind in ("pmcid", "pmc"):
                v = value.upper() if value.upper().startswith("PMC") else f"PMC{value}"
                ids["pmcid"] = ids["pmcid"] or (v if _PMCID.match(v) else None)
        if cit is not None and not ids["doi"]:
            for ext in _iter(cit, "ext-link"):
                href = ext.get(f"{XLINK}href") or _text(ext)
                if (ext.get("ext-link-type") or "").lower() == "doi" or "doi.org/" in href:
                    ids["doi"] = _doi_or_none(href)
                    if ids["doi"]:
                        break
        year = _text(_find(cit, "year")) if cit is not None else ""
        first = None
        if cit is not None:
            surname = next(_iter(cit, "surname"), None)
            collab = next(_iter(cit, "collab"), None)
            first = _text(surname) if surname is not None else (_text(collab) if collab is not None else None)
        refs.append({"id": ref.get("id") or "", "label": _text(_find(ref, "label")),
                     "text": _text(cit) if cit is not None else _text(ref),
                     "title": _text(_find(cit, "article-title")) if cit is not None else "",
                     "source": _text(_find(cit, "source")) if cit is not None else "",
                     "year": int(year[:4]) if year[:4].isdigit() else None, "first_author": first or None, **ids})
    return refs


def parse_jats(body: bytes) -> dict:
    """The parts of a JATS article ORPHEUS uses: identifiers, title, licence, abstract, sections (with level),
    figure and table counts, reference list. ValueError for anything that is not a JATS <article>."""
    if b"<!ENTITY" in body[:20000]:
        raise ValueError("XML con dichiarazioni di entità: rifiutato")
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise ValueError(f"XML non leggibile: {exc}") from None
    if _local(root.tag) != "article":
        raise ValueError(f"la risposta non è un articolo JATS (radice <{_local(root.tag)}>)")
    meta = _find(root, "front/article-meta")
    ids: dict[str, str] = {}
    for el in _children(meta, "article-id"):
        kind = (el.get("pub-id-type") or "").lower()
        value = _text(el)
        if kind == "doi":
            ids["doi"] = _doi_or_none(value) or ""
        elif kind == "pmid" and _PMID.match(value):
            ids["pmid"] = value
        elif kind in ("pmcid", "pmc"):
            v = value.upper() if value.upper().startswith("PMC") else f"PMC{value}"
            if _PMCID.match(v):
                ids["pmcid"] = v
    licence_url, licence_text, licence_type = None, "", ""
    perm = _find(meta, "permissions") if meta is not None else None
    lic = next(_iter(perm, "license"), None) if perm is not None else None
    if lic is not None:
        licence_url = lic.get(f"{XLINK}href")
        licence_type = lic.get("license-type") or ""
        ref = next(_iter(lic, "license_ref"), None)              # <ali:license_ref>
        licence_url = licence_url or (_text(ref) if ref is not None else None)
        licence_text = _text(lic)
    if not licence_url and perm is not None:                     # a CC link written only in the text
        for ext in _iter(perm, "ext-link"):
            if "creativecommons.org" in (ext.get(f"{XLINK}href") or ""):
                licence_url = ext.get(f"{XLINK}href")
                break
    body_el = _find(root, "body")
    sections = _sections(body_el) if body_el is not None else []
    back = _find(root, "back")
    refs = _references(back if back is not None else root)
    abstract = _find(meta, "abstract") if meta is not None else None
    return {
        "ids": ids,
        "title": _text(_find(meta, "title-group/article-title")) if meta is not None else "",
        "article_type": root.get("article-type") or "",
        "licence": (normalize_licence(licence_url) or licence_in_text(licence_text) or
                    (licence_type.strip() or None)),
        "licence_url": licence_url,
        "licence_text": licence_text,
        "copyright": _text(_find(perm, "copyright-statement")) if perm is not None else "",
        "abstract": _text(abstract, SKIP_IN_TEXT),
        "has_body": body_el is not None,
        "sections": sections,
        "words": sum(s["words"] for s in sections),
        "figures": sum(1 for _ in _iter(body_el, "fig")) if body_el is not None else 0,
        "tables": sum(1 for _ in _iter(body_el, "table-wrap")) if body_el is not None else 0,
        "references": refs,
    }
