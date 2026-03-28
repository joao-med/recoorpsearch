"""
metadata.py — Fetch and parse full article metadata from PubMed (efetch XML).
"""

import time
import requests
from lxml import etree
from typing import Optional

EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

DEFAULT_TOOL = "recoorpsearch"
DEFAULT_EMAIL = "jpmedeirosg@gmail.com"


def fetch_metadata(
    pmids: list[str],
    api_key: Optional[str] = None,
    batch_size: int = 100,
    verbose: bool = True,
) -> list[dict]:
    if not pmids:
        return []

    records: list[dict] = []
    total = len(pmids)
    processed = 0

    for start in range(0, total, batch_size):
        batch = pmids[start : start + batch_size]
        xml_bytes = _efetch_batch(batch, api_key)
        batch_records = _parse_pubmed_xml(xml_bytes)
        records.extend(batch_records)
        processed += len(batch)
        if verbose:
            print(f"[recoorpsearch] Parsed {processed:,}/{total:,} articles...")
        _rate_sleep(api_key)

    if verbose:
        print(f"[recoorpsearch] Metadata complete — {len(records):,} author-article rows.")

    return records


def _parse_pubmed_xml(xml_bytes: bytes) -> list[dict]:
    root = etree.fromstring(xml_bytes)
    records: list[dict] = []

    for article in root.findall(".//PubmedArticle"):
        base = _extract_article_base(article)
        authors = _extract_authors(article)

        if not authors:
            records.append({**base, "author_name": "", "author_affiliation": "", "corporate_flag": False})
        else:
            for author in authors:
                records.append({**base, **author})

    return records


def _extract_article_base(article) -> dict:
    medline = article.find("MedlineCitation")
    art = medline.find("Article") if medline is not None else None

    pmid = _text(medline, "PMID")
    title = _text(art, "ArticleTitle") if art is not None else ""
    journal = _text(art, "Journal/Title") if art is not None else ""
    abstract_parts = art.findall(".//AbstractText") if art is not None else []
    abstract = " ".join(_full_text(a) for a in abstract_parts)

    pub_date = art.find(".//PubDate") if art is not None else None
    pub_year = _text(pub_date, "Year") if pub_date is not None else ""
    pub_month = _text(pub_date, "Month") if pub_date is not None else ""

    doi = ""
    for id_el in article.findall(".//ArticleId"):
        if id_el.get("IdType") == "doi":
            doi = (id_el.text or "").strip()
            break

    coi = _text(medline, "CoiStatement") if medline is not None else ""

    grants = []
    for grant in article.findall(".//Grant"):
        agency = _text(grant, "Agency")
        country = _text(grant, "Country")
        if agency:
            grants.append(f"{agency} ({country})" if country else agency)
    funding_sources = "; ".join(grants)

    pubmed_url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else ""

    return {
        "pmid": pmid,
        "doi": doi,
        "pubmed_url": pubmed_url,
        "title": title,
        "journal": journal,
        "pub_year": pub_year,
        "pub_month": pub_month,
        "abstract": abstract,
        "coi_statement": coi,
        "funding_sources": funding_sources,
    }


def _extract_authors(article) -> list[dict]:
    authors = []
    for author_el in article.findall(".//AuthorList/Author"):
        last = _text(author_el, "LastName")
        fore = _text(author_el, "ForeName")
        name = f"{last}, {fore}".strip(", ") if last or fore else _text(author_el, "CollectiveName")

        affiliations = []
        for aff in author_el.findall(".//AffiliationInfo/Affiliation"):
            txt = (aff.text or "").strip()
            if txt:
                affiliations.append(txt)
        affiliation_str = " | ".join(affiliations)

        authors.append({
            "author_name": name,
            "author_affiliation": affiliation_str,
            "corporate_flag": False,
        })
    return authors


def _text(el, xpath: str) -> str:
    if el is None:
        return ""
    found = el.find(xpath)
    if found is None:
        return ""
    return (found.text or "").strip()


def _full_text(el) -> str:
    parts = []
    if el.text:
        parts.append(el.text)
    for child in el:
        parts.append(_full_text(child))
        if child.tail:
            parts.append(child.tail)
    return "".join(parts).strip()


def _efetch_batch(pmids: list[str], api_key: Optional[str]) -> bytes:
    params = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "retmode": "xml",
        "rettype": "abstract",
        "tool": DEFAULT_TOOL,
        "email": DEFAULT_EMAIL,
    }
    if api_key:
        params["api_key"] = api_key

    for attempt in range(3):
        try:
            r = requests.get(EFETCH_URL, params=params, timeout=60)
            r.raise_for_status()
            return r.content
        except requests.RequestException as exc:
            if attempt == 2:
                raise
            wait = 2 ** attempt
            print(f"[recoorpsearch] efetch error ({exc}), retrying in {wait}s...")
            time.sleep(wait)


def _rate_sleep(api_key: Optional[str]) -> None:
    time.sleep(0.11 if api_key else 0.34)
