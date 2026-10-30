"""Crossref REST API client (T-008) — free, no key, standard library only.

Checked on the official documentation on 2026-10-10 (pages last updated October 2025):
  - https://www.crossref.org/documentation/retrieve-metadata/rest-api/access-and-authentication/
    pools: public 5 requests / 1 concurrent; polite (mailto parameter or User-Agent with mailto) 10 / 3;
    over the limit -> HTTP 429 ("wait a short while and try again at a lower rate"); blocked -> HTTP 403.
    Limits are announced in the x-rate-limit-limit / x-rate-limit-interval headers.
  - https://www.crossref.org/documentation/retrieve-metadata/rest-api/tips-for-using-the-crossref-rest-api/
    "back off if you start seeing 429 statuses"; "Cache your results".
  - https://www.crossref.org/documentation/retrieve-metadata/rest-api/ — no sign-up; "almost none of the
    metadata is subject to copyright, and you may use it for any purpose"; abstracts may be copyrighted.
  - /works/{doi} returns one record; a DOI not registered with Crossref gives 404.

ORPHEUS is stricter than the polite pool: one request at a time, at most `max_requests_per_second`
(config, default 1), and it never asks again for a DOI whose answer is already stored.
Network access happens only here; everything else works on the stored responses.
"""

from __future__ import annotations

import email.utils
import json
import re
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import quote, urlencode

from . import __version__

API_BASE = "https://api.crossref.org"
SOURCE_ID = "crossref"
TIMEOUT_SECONDS = 30
MAX_WAIT_SECONDS = 120
MAX_DOIS_PER_REQUEST = 50                # one /works?filter=doi:…,doi:… request (T-014, cite-v1)
BATCH_SELECT = "DOI,title,subtitle,author,issued,published-print,published-online,type,container-title"
SEARCH_SELECT = "DOI,title,subtitle,author,issued,type,container-title,ISSN,volume,issue,page"
LICENCE = ("Crossref metadata: free to use for any purpose (no sign-up); abstracts inside a record may be "
           "copyrighted by publishers or authors — kept as a private copy, not republished.")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class CrossrefError(Exception):
    """The request could not be completed (network down, server errors, too many 429) after the retries."""


class CrossrefBlocked(CrossrefError):
    """HTTP 403: Crossref has blocked this client. Stop at once; never retry automatically."""


class CrossrefCertificateError(CrossrefBlocked):
    """The server's TLS certificate was not accepted by this PC. Not transient: stop at once."""


# ---------------------------------------------------------------- TLS (shared by every connector)

_TLS_CONTEXT: ssl.SSLContext | None = None


def tls_context() -> ssl.SSLContext:
    """The TLS context of every HTTPS request, always with certificate and host-name verification.

    With the `truststore` package (0.8.1) the certificate is checked by the operating system's own verifier
    (on Windows: CryptoAPI, which also fetches a missing trusted root, like Edge does), instead of the list of
    roots Python reads from the Windows store — a list that can lack the root of a site (2026-10-11: NCBI
    refused with "self-signed certificate in certificate chain" while Crossref worked). Without the package:
    Python's default context."""
    global _TLS_CONTEXT
    if _TLS_CONTEXT is None:
        try:
            import truststore
            _TLS_CONTEXT = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        except ImportError:
            _TLS_CONTEXT = ssl.create_default_context()
    return _TLS_CONTEXT


def tls_verifier() -> str:
    return "sistema operativo (truststore)" if type(tls_context()).__module__.startswith("truststore") else "Python"


def is_certificate_error(exc: BaseException) -> bool:
    """True for a refused TLS certificate, also when urllib wraps it in URLError."""
    return isinstance(exc, ssl.SSLCertVerificationError) or isinstance(getattr(exc, "reason", None),
                                                                       ssl.SSLCertVerificationError)


def certificate_message(host: str, exc: BaseException) -> str:
    return (f"il certificato di {host} non è stato accettato da questo PC (verifica: {tls_verifier()}; "
            f"{getattr(exc, 'reason', exc)}). Interrotto subito, nessun dato scritto. Non è un errore del sito: "
            "di solito manca un certificato radice o un antivirus/proxy controlla le connessioni HTTPS.")


def valid_email(value: str | None) -> bool:
    return bool(value and _EMAIL.match(value.strip()))


@dataclass
class Response:
    status: int
    body: bytes
    headers: dict[str, str]
    public_url: str                  # without the contact e-mail: this is what the database stores
    attempts: int = 1
    waited_seconds: float = 0.0
    retrieved_at: str = ""

    @property
    def content_type(self) -> str:
        return (self.headers.get("content-type") or "").split(";")[0].strip() or "application/octet-stream"


@dataclass
class _Throttle:
    min_interval: float
    clock: callable = time.monotonic
    sleep: callable = time.sleep
    last: float | None = None
    waited: float = 0.0

    def wait(self) -> None:
        now = self.clock()
        if self.last is not None:
            gap = self.min_interval - (now - self.last)
            if gap > 0:
                self.sleep(gap)
                self.waited += gap
        self.last = self.clock()


def work_url(doi: str) -> str:
    """https://api.crossref.org/works/<doi>; '/' and '()' stay readable, anything else unusual is escaped."""
    return f"{API_BASE}/works/{quote(doi, safe='/()')}"


def user_agent(contact_email: str) -> str:
    # Format from Crossref's etiquette example: "App/1.1 (https://site; mailto:someone@example.org)".
    return f"ORPHEUS/{__version__} (https://github.com/SoftProjectsWork/orpheus-release; mailto:{contact_email})"


def urllib_transport(url: str, headers: dict[str, str], timeout: float) -> tuple[int, dict[str, str], bytes]:
    """One HTTP GET. Returns (status, headers, body) for every HTTP answer, including 4xx/5xx;
    raises OSError subclasses (URLError, TimeoutError, ConnectionError) when no answer arrives.
    On Windows, urllib uses the system proxy settings; certificates are checked by `tls_context()`."""
    request = urllib.request.Request(url, headers=headers, method="GET")
    context = tls_context()
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as resp:
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read()
    except urllib.error.HTTPError as exc:
        body = exc.read() if exc.fp is not None else b""
        return exc.code, {k.lower(): v for k, v in (exc.headers or {}).items()}, body


def _retry_after(headers: dict[str, str]) -> float | None:
    value = (headers.get("retry-after") or "").strip()
    if not value:
        return None
    if value.isdigit():
        return float(value)
    try:
        when = email.utils.parsedate_to_datetime(value)
        return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError):
        return None


def _announced_interval(headers: dict[str, str]) -> float | None:
    """Seconds per request implied by x-rate-limit-limit / x-rate-limit-interval (e.g. '10' and '1s')."""
    try:
        limit = float(headers.get("x-rate-limit-limit", ""))
        m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*s?\s*", headers.get("x-rate-limit-interval", ""))
        if limit > 0 and m:
            return float(m.group(1)) / limit
    except ValueError:
        pass
    return None


@dataclass
class CrossrefClient:
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
            raise ValueError("serve un indirizzo e-mail valido in ORPHEUS_CONTACT_EMAIL (polite pool di Crossref)")
        self.contact_email = self.contact_email.strip()
        self._throttle = _Throttle(1.0 / self.max_requests_per_second, clock=self.clock, sleep=self.sleep)

    def get_work(self, doi: str) -> Response:
        """GET /works/{doi}. Returns 200 and 404 answers; retries network errors, 429 and 5xx with
        growing waits (2, 4, 8 … s, or what Retry-After asks, at most 120 s); raises CrossrefBlocked
        on 403 and CrossrefError when the retries are used up."""
        public = work_url(doi)
        return self._request(public, f"{public}?mailto={quote(self.contact_email, safe='@')}", doi, (200, 404))

    def get_journal(self, issn: str) -> Response:
        """GET /journals/{issn} (T-054, "Compila dall'ISSN"): title, publisher, ISSNs with their type. 200 and 404."""
        public = f"{API_BASE}/journals/{quote(issn, safe='-')}"
        return self._request(public, f"{public}?mailto={quote(self.contact_email, safe='@')}", f"ISSN {issn}", (200, 404))

    def works_by_dois(self, dois: list[str], select: str = BATCH_SELECT) -> Response:
        """GET /works?filter=doi:A,doi:B,… (the same filter repeated is an OR) with only the `select` fields:
        one request for up to MAX_DOIS_PER_REQUEST DOIs. DOIs that Crossref does not know are simply absent
        from the items. DOIs containing a comma cannot be sent this way (use get_work)."""
        if not dois or len(dois) > MAX_DOIS_PER_REQUEST or any("," in d for d in dois):
            raise ValueError(f"works_by_dois vuole da 1 a {MAX_DOIS_PER_REQUEST} DOI senza virgole")
        params = [("filter", ",".join(f"doi:{d}" for d in dois)), ("select", select), ("rows", str(len(dois)))]
        public = f"{API_BASE}/works?{urlencode(params)}"
        return self._request(public, f"{public}&{urlencode([('mailto', self.contact_email)])}",
                             f"{len(dois)} DOI", (200,))

    def search_works(self, query: str, issns: list[str], rows: int = 5, select: str = SEARCH_SELECT) -> Response:
        """GET /works?query.bibliographic=…&filter=issn:A,issn:B (the same filter repeated is an OR): the records of
        those journals that best match a bibliographic text (doi_search.py, 2026-10-14). Only `select` fields."""
        if not query.strip() or not issns:
            raise ValueError("search_works vuole un testo e almeno un ISSN")
        params = [("query.bibliographic", query), ("filter", ",".join(f"issn:{i}" for i in issns)),
                  ("rows", str(rows)), ("select", select)]
        public = f"{API_BASE}/works?{urlencode(params)}"
        return self._request(public, f"{public}&{urlencode([('mailto', self.contact_email)])}",
                             f"ricerca {query[:40]}", (200,))

    def search_bibliographic(self, query: str, rows: int = 3, select: str = SEARCH_SELECT) -> Response:
        """GET /works?query.bibliographic=…&rows=… without a journal filter: the records that best match the title,
        first author and year of a cited work (manuscript_refs.py, T-038). Only `select` fields."""
        if not query.strip():
            raise ValueError("search_bibliographic vuole un testo")
        params = [("query.bibliographic", query), ("rows", str(rows)), ("select", select)]
        public = f"{API_BASE}/works?{urlencode(params)}"
        return self._request(public, f"{public}&{urlencode([('mailto', self.contact_email)])}", "ricerca", (200,))

    def _request(self, public: str, url: str, what: str, ok_statuses: tuple[int, ...]) -> Response:
        headers = {"User-Agent": user_agent(self.contact_email), "Accept": "application/json"}
        waited, last_problem = 0.0, ""
        for attempt in range(1, self.max_retries + 2):
            self._throttle.wait()
            self.requests_made += 1
            retrieved_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            try:
                status, resp_headers, body = (self.transport or urllib_transport)(url, headers, TIMEOUT_SECONDS)
            except OSError as exc:            # URLError, TimeoutError, ConnectionError, ssl errors…
                if is_certificate_error(exc):
                    raise CrossrefCertificateError(certificate_message("api.crossref.org", exc)) from exc
                status, resp_headers, body = None, {}, b""
                last_problem = f"rete: {type(exc).__name__}: {getattr(exc, 'reason', exc)}"
            self.log.append({"doi": what, "tentativo": attempt, "http": status, "problema": last_problem if status is None else None})
            if status is not None:
                announced = _announced_interval(resp_headers)
                if announced and announced > self._throttle.min_interval:
                    self._throttle.min_interval = announced          # never faster than Crossref asks
                if status in ok_statuses:
                    return Response(status, body, resp_headers, public, attempt, waited, retrieved_at)
                if status == 403:
                    raise CrossrefBlocked("Crossref ha risposto 403 (accesso bloccato): verifica interrotta. "
                                          "Non rilanciare subito; controlla l'e-mail di contatto e riprova più tardi.")
                if status != 429 and status < 500:
                    raise CrossrefError(f"risposta inattesa di Crossref: HTTP {status}")
                last_problem = f"HTTP {status}" + (" (troppe richieste)" if status == 429 else " (errore del server)")
            if attempt > self.max_retries:
                break
            pause = min(MAX_WAIT_SECONDS, _retry_after(resp_headers) or 2.0 ** attempt)
            if status == 429:
                self._throttle.min_interval *= 2                      # slow down for the rest of the run
            self.sleep(pause)
            waited += pause
        raise CrossrefError(f"{last_problem}; {self.max_retries + 1} tentativi")


def parse_work_list(body: bytes) -> list[dict]:
    """The items of a /works list answer (status ok, message-type work-list), or ValueError."""
    data = json.loads(body.decode("utf-8"))
    if not isinstance(data, dict) or data.get("status") != "ok" or data.get("message-type") != "work-list":
        raise ValueError("la risposta non è un elenco 'work-list' di Crossref")
    items = (data.get("message") or {}).get("items")
    if not isinstance(items, list):
        raise ValueError("elenco Crossref senza items")
    return [i for i in items if isinstance(i, dict) and i.get("DOI")]


# ---------------------------------------------------------------- reading a /works record

def parse_work(body: bytes) -> dict:
    """The 'message' of a /works/{doi} answer, or ValueError if the body is not a Crossref work."""
    data = json.loads(body.decode("utf-8"))
    if not isinstance(data, dict) or data.get("status") != "ok" or data.get("message-type") != "work":
        raise ValueError("la risposta non è un record 'work' di Crossref")
    msg = data.get("message")
    if not isinstance(msg, dict) or not msg.get("DOI"):
        raise ValueError("record Crossref senza DOI")
    return msg


def date_parts(value) -> list[int]:
    try:
        parts = (value or {}).get("date-parts") or [[]]
        return [int(p) for p in parts[0] if p is not None]
    except (TypeError, ValueError, AttributeError):
        return []


def years(work: dict) -> list[int]:
    out = []
    for key in ("issued", "published", "published-print", "published-online"):
        p = date_parts(work.get(key))
        if p and 1600 <= p[0] <= 2100 and p[0] not in out:
            out.append(p[0])
    return sorted(out)


def iso_date(work: dict) -> str | None:
    """Full 'YYYY-MM-DD' from 'issued' only when the day is known (the schema stores whole dates)."""
    p = date_parts(work.get("issued"))
    if len(p) >= 3:
        try:
            return datetime(p[0], p[1], p[2]).strftime("%Y-%m-%d")
        except ValueError:
            return None
    return None


def strip_markup(text: str) -> str:
    import html
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text or ""))).strip()


def summary(work: dict) -> dict:
    """The fields ORPHEUS compares and shows, in plain form."""
    authors = []
    for a in work.get("author") or []:
        if a.get("family"):
            authors.append({"family": strip_markup(a["family"]), "given": strip_markup(a.get("given") or ""),
                            "sequence": a.get("sequence"), "orcid": a.get("ORCID")})
        elif a.get("name"):
            authors.append({"name": strip_markup(a["name"])})
    issns = []
    for v in list(work.get("ISSN") or []) + [i.get("value") for i in work.get("issn-type") or [] if isinstance(i, dict)]:
        if v and v.upper() not in issns:
            issns.append(v.upper())
    return {
        "doi": (work.get("DOI") or "").lower(),
        "title": strip_markup((work.get("title") or [""])[0] if work.get("title") else ""),
        "subtitle": strip_markup((work.get("subtitle") or [""])[0] if work.get("subtitle") else ""),
        "container_title": [strip_markup(t) for t in work.get("container-title") or []],
        "short_container_title": [strip_markup(t) for t in work.get("short-container-title") or []],
        "issn": issns,
        "authors": authors,
        "years": years(work),
        "issued": date_parts(work.get("issued")),
        "type": work.get("type"),
        "page": work.get("page"),
        "volume": work.get("volume"),
        "issue": work.get("issue"),
        "publisher": work.get("publisher"),
        "language": work.get("language"),
        "licence": [l.get("URL") for l in work.get("license") or [] if isinstance(l, dict) and l.get("URL")],
        "update_to": [{"doi": (u.get("DOI") or "").lower(), "type": u.get("type")} for u in work.get("update-to") or []
                      if isinstance(u, dict)],
        "updated_by": [{"doi": (u.get("DOI") or "").lower(), "type": u.get("type")} for u in work.get("updated-by") or []
                       if isinstance(u, dict)],
    }
