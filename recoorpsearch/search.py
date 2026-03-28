"""
search.py — PubMed search via NCBI E-utilities esearch endpoint.
"""

import time
import requests
from typing import Optional

ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"

DEFAULT_TOOL = "recoorpsearch"
DEFAULT_EMAIL = "jpmedeirosg@gmail.com"


def fetch_affiliation(
    query: str,
    max_results: int = 100,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    api_key: Optional[str] = None,
    batch_size: int = 200,
    verbose: bool = True,
) -> list[str]:
    params = _base_params(api_key)
    params.update(
        {
            "term": _build_query(query, date_from, date_to),
            "retmode": "json",
            "usehistory": "y",
        }
    )

    resp = _get(ESEARCH_URL, params)
    data = resp.json()
    esearch = data.get("esearchresult", {})
    total = int(esearch.get("count", 0))
    web_env = esearch.get("webenv", "")
    query_key = esearch.get("querykey", "")

    if verbose:
        print(f"[recoorpsearch] Query: {params['term']}")
        print(f"[recoorpsearch] Total articles found: {total:,}")

    if total == 0:
        return []

    limit = total if max_results == 0 else min(max_results, total)
    pmids: list[str] = []
    retstart = 0

    while len(pmids) < limit:
        batch = min(batch_size, limit - len(pmids))
        fetch_params = dict(params)
        fetch_params.update(
            {
                "retstart": retstart,
                "retmax": batch,
                "webenv": web_env,
                "query_key": query_key,
            }
        )
        r = _get(ESEARCH_URL, fetch_params)
        ids = r.json().get("esearchresult", {}).get("idlist", [])
        if not ids:
            break
        pmids.extend(ids)
        retstart += batch
        if verbose and len(pmids) < limit:
            print(f"[recoorpsearch] Retrieved {len(pmids):,}/{limit:,} PMIDs...")
        _rate_sleep(api_key)

    if verbose:
        print(f"[recoorpsearch] Done — {len(pmids):,} PMIDs collected.")

    return pmids[:limit]


def _build_query(base: str, date_from: Optional[str], date_to: Optional[str]) -> str:
    if date_from or date_to:
        df = date_from or "1900/01/01"
        dt = date_to or "3000/12/31"
        return f"({base}) AND (\"{df}\"[PDAT]:\"{dt}\"[PDAT])"
    return base


def _base_params(api_key: Optional[str]) -> dict:
    p = {"db": "pubmed", "tool": DEFAULT_TOOL, "email": DEFAULT_EMAIL}
    if api_key:
        p["api_key"] = api_key
    return p


def _get(url: str, params: dict, retries: int = 3) -> requests.Response:
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=30)
            r.raise_for_status()
            return r
        except requests.RequestException as exc:
            if attempt == retries - 1:
                raise
            wait = 2 ** attempt
            print(f"[recoorpsearch] Request error ({exc}), retrying in {wait}s...")
            time.sleep(wait)


def _rate_sleep(api_key: Optional[str]) -> None:
    time.sleep(0.11 if api_key else 0.34)
