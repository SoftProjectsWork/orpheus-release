"""Q6 — OpenAlex client and READ-ONLY report (nothing is written to the database).

OpenAlex (https://api.openalex.org, open catalogue, CC0 metadata) gives for a DOI: the OpenAlex id, the
source (journal) with its ISSN-L, open-access status, citation count and number of references.

The source is `enabled: false` in config/sources.yaml: enabling it, and saving anything it returns in the
database, are decisions of the user. Until then only `orpheus openalex --prova` works (no network: which DOIs
would be asked and how many requests). The access policy (API key, daily limits) must be checked on the
official documentation before the first real run: this client sends an optional key (OPENALEX_API_KEY) and
the contact e-mail (mailto), one request at a time, at most max_requests_per_second. The example answers in
the tests follow the documented Work object (fields id, doi, display_name, publication_year, type,
cited_by_count, referenced_works_count, open_access, primary_location.source) and must be compared with a
real answer on the PC.

Network access happens only in the client (shared retry/throttle/TLS logic of crossref.py).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from urllib.parse import quote, urlencode

from . import crossref, journals

API_BASE = "https://api.openalex.org"
SOURCE_ID = "openalex"
MAX_DOIS_PER_REQUEST = 50
SELECT = ("id,doi,display_name,publication_year,type,cited_by_count,referenced_works_count,open_access,"
          "primary_location")
LICENCE = "OpenAlex: metadata CC0 (free to use)."


class OpenAlexError(crossref.CrossrefError):
    """The request could not be completed after the retries."""


def normalize_doi(doi: str | None) -> str | None:
    """'https://doi.org/10.1/AbC' or '10.1/AbC' -> '10.1/abc' (DOIs are case-insensitive)."""
    if not isinstance(doi, str):
        return None
    d = doi.strip()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if d.lower().startswith(prefix):
            d = d[len(prefix):]
    d = d.strip().lower()
    return d if d.startswith("10.") and "/" in d else None


def works_url(dois: list[str]) -> str:
    """GET /works?filter=doi:A|B|…&select=…&per-page=N (the '|' is an OR inside one filter)."""
    if not dois or len(dois) > MAX_DOIS_PER_REQUEST or any(("|" in d or "," in d) for d in dois):
        raise ValueError(f"servono da 1 a {MAX_DOIS_PER_REQUEST} DOI senza '|' né ','")
    params = [("filter", "doi:" + "|".join(dois)), ("select", SELECT), ("per-page", str(len(dois)))]
    return f"{API_BASE}/works?{urlencode(params, safe=':|/,')}"


@dataclass
class OpenAlexClient:
    contact_email: str
    api_key: str | None = None
    max_requests_per_second: float = 1.0
    max_retries: int = 3
    transport: callable = None
    sleep: callable = None
    log: list[dict] = field(default_factory=list)

    def __post_init__(self):
        kw = {"max_requests_per_second": self.max_requests_per_second, "max_retries": self.max_retries,
              "transport": self.transport}
        if self.sleep is not None:
            kw["sleep"] = self.sleep
        self._http = crossref.CrossrefClient(self.contact_email, **kw)   # validates the e-mail
        self._http.log = self.log

    @property
    def requests_made(self) -> int:
        return self._http.requests_made

    def works_by_dois(self, dois: list[str]) -> crossref.Response:
        public = works_url(dois)
        extra = [("mailto", self._http.contact_email)] + ([("api_key", self.api_key)] if self.api_key else [])
        try:
            return self._http._request(public, f"{public}&{urlencode(extra)}", f"{len(dois)} DOI", (200,))
        except crossref.CrossrefBlocked as exc:      # 403 or refused certificate: stop at once
            raise crossref.CrossrefBlocked(_rename(exc)) from exc
        except crossref.CrossrefError as exc:
            raise OpenAlexError(_rename(exc)) from exc


def _rename(exc: Exception) -> str:
    return str(exc).replace("api.crossref.org", "api.openalex.org").replace("Crossref", "OpenAlex")


def parse_works(body: bytes) -> list[dict]:
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise OpenAlexError(f"risposta di OpenAlex non leggibile: {exc}") from exc
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        raise OpenAlexError("risposta di OpenAlex senza 'results'")
    return [w for w in results if isinstance(w, dict)]


def summary(work: dict) -> dict:
    oa = work.get("open_access") or {}
    src = ((work.get("primary_location") or {}).get("source") or {})
    return {"doi": normalize_doi(work.get("doi")), "openalex_id": work.get("id"), "titolo": work.get("display_name"),
            "anno": work.get("publication_year"), "tipo": work.get("type"),
            "citato_da": work.get("cited_by_count"), "riferimenti": work.get("referenced_works_count"),
            "accesso_aperto": oa.get("is_oa"), "stato_oa": oa.get("oa_status"),
            "rivista": src.get("display_name"), "issn_l": src.get("issn_l")}


def dois_to_ask(conn) -> list[dict]:
    """The articles with a confirmed DOI (latest Crossref check) and their ISSNs. Read-only."""
    out, seen = [], set()
    for r in journals._records(conn):
        d = normalize_doi(r["doi"])
        if d and d not in seen and "|" not in d and "," not in d:
            seen.add(d)
            out.append({"article_id": r["article_id"], "doi": d, "issn_crossref": r["issn"]})
    return sorted(out, key=lambda x: x["doi"])


def plan(conn, limit: int | None = None) -> dict:
    items = dois_to_ask(conn)[: limit or None]
    n = len(items)
    return {"doi": n, "richieste": -(-n // MAX_DOIS_PER_REQUEST) if n else 0}


def build(conn, client: OpenAlexClient, limit: int | None = None) -> dict:
    """Ask OpenAlex for the confirmed DOIs (in groups of 50) and compare with Crossref. Nothing is written."""
    items = dois_to_ask(conn)[: limit or None]
    found: dict[str, dict] = {}
    retrieved = []
    for i in range(0, len(items), MAX_DOIS_PER_REQUEST):
        chunk = [x["doi"] for x in items[i:i + MAX_DOIS_PER_REQUEST]]
        resp = client.works_by_dois(chunk)
        retrieved.append(resp.retrieved_at)
        for w in parse_works(resp.body):
            s = summary(w)
            if s["doi"] in chunk and s["doi"] not in found:
                found[s["doi"]] = s
    rows, missing, issn_mismatch = [], [], []
    for it in items:
        s = found.get(it["doi"])
        if s is None:
            missing.append(it["doi"])
            continue
        row = {"article_id": it["article_id"], **s}
        if s["issn_l"] and it["issn_crossref"] and s["issn_l"] not in it["issn_crossref"]:
            row["issn_diverso_da_crossref"] = it["issn_crossref"]
            issn_mismatch.append(it["doi"])
        rows.append(row)
    oa = sum(1 for r in rows if r["accesso_aperto"])
    cites = sorted(r["citato_da"] for r in rows if isinstance(r["citato_da"], int))
    return {"fonte": {"api": API_BASE, "licenza": LICENCE, "richieste": client.requests_made,
                      "data": max(retrieved) if retrieved else None, "scritture_nel_database": 0},
            "riepilogo": {"doi_chiesti": len(items), "trovati": len(rows), "non_trovati": len(missing),
                          "accesso_aperto": oa, "issn_diverso_da_crossref": len(issn_mismatch),
                          "citazioni_mediana": cites[len(cites) // 2] if cites else None},
            "articoli": rows, "non_trovati": missing}
