"""
affiliations.py — Detect and classify corporate affiliations in PubMed records.
"""

import re
from typing import Optional

CORPORATE_SUFFIXES = re.compile(
    r"\b(Inc\.?|Ltd\.?|LLC|L\.L\.C\.?|Corp\.?|Corporation|GmbH|S\.A\.?|S\.p\.A\.|"
    r"B\.V\.|N\.V\.|AG|PLC|plc|Co\.|Company|A/S|ApS|K/S|Ltda\.?|S\.L\.?|S\.R\.L\.?)\b",
    re.IGNORECASE,
)

CORPORATE_KEYWORDS = re.compile(
    r"\b(Pharma(?:ceutical)?s?|Biotech(?:nology)?|Therapeutics?|Biosciences?|"
    r"Biologics?|Biopharm(?:a)?|Medical(?:\s+Device)?s?|Diagnostics?|Genomics?|"
    r"Oncology|Vaccines?|Sciences?|Laboratories?|Labs?\b|Research\s+&\s+Development|"
    r"R&D\s+Center)\b",
    re.IGNORECASE,
)

ACADEMIC_KEYWORDS = re.compile(
    r"\b(University|Universidade|Universit\u00e4t|Universit\u00e9|Universidad|Universit\u00e0|"
    r"College|Institute|Instituto|Institut|Hospital|Clinic|Foundation|Ministry|"
    r"Government|National\s+Center|NIH|CDC|WHO|NHS|CNRS|INSERM)\b",
    re.IGNORECASE,
)


def detect_corporate_affiliation(affiliation: str) -> dict:
    if not affiliation or not affiliation.strip():
        return _no_match()

    has_suffix = bool(CORPORATE_SUFFIXES.search(affiliation))
    has_keyword = bool(CORPORATE_KEYWORDS.search(affiliation))
    has_academic = bool(ACADEMIC_KEYWORDS.search(affiliation))

    corporate = has_suffix or has_keyword

    if not corporate:
        return _no_match()

    link_type = "dual" if (corporate and has_academic) else "employment"
    company = _extract_company_name(affiliation)
    method = "suffix" if has_suffix else "keyword"

    return {
        "corporate_flag": True,
        "company_detected": company,
        "link_type": link_type,
        "detection_method": method,
    }


def detect_coi_links(coi_statement: str, known_companies: Optional[list[str]] = None) -> dict:
    if not coi_statement or not coi_statement.strip():
        return {"coi_corporate_flag": False, "coi_companies": "", "link_type": ""}

    text = coi_statement
    found_companies: list[str] = []

    if known_companies:
        for company in known_companies:
            if re.search(re.escape(company), text, re.IGNORECASE):
                found_companies.append(company)

    for match in CORPORATE_SUFFIXES.finditer(text):
        start = max(0, match.start() - 60)
        snippet = text[start : match.end()]
        candidate = snippet.split(",")[-1].strip()
        if candidate and candidate not in found_companies:
            found_companies.append(candidate)

    link_types: list[str] = []
    if re.search(r"\b(consult|advisory\s+board|speaker)\b", text, re.IGNORECASE):
        link_types.append("consultancy")
    if re.search(r"\b(grant|funding|support)\b", text, re.IGNORECASE):
        link_types.append("grant")
    if re.search(r"\b(employ|staff|employee)\b", text, re.IGNORECASE):
        link_types.append("employment")
    if re.search(r"\b(stock|equity|share|ownership)\b", text, re.IGNORECASE):
        link_types.append("stock")

    link_type = link_types[0] if len(link_types) == 1 else ("multiple" if link_types else "disclosed")

    return {
        "coi_corporate_flag": bool(found_companies or link_types),
        "coi_companies": ", ".join(found_companies),
        "link_type": link_type,
    }


def detect_funding_links(funding_sources: str, known_companies: Optional[list[str]] = None) -> dict:
    if not funding_sources:
        return {"private_funding_flag": False, "private_funders": ""}

    private: list[str] = []

    if known_companies:
        for company in known_companies:
            if re.search(re.escape(company), funding_sources, re.IGNORECASE):
                private.append(company)

    for funder in funding_sources.split(";"):
        funder = funder.strip()
        if CORPORATE_SUFFIXES.search(funder) or CORPORATE_KEYWORDS.search(funder):
            if not ACADEMIC_KEYWORDS.search(funder) and funder not in private:
                private.append(funder)

    return {
        "private_funding_flag": bool(private),
        "private_funders": "; ".join(private),
    }


def enrich_records(
    records: list[dict],
    known_companies: Optional[list[str]] = None,
) -> list[dict]:
    for rec in records:
        aff_result = detect_corporate_affiliation(rec.get("author_affiliation", ""))
        rec["corporate_flag"] = aff_result["corporate_flag"]
        rec["company_detected"] = aff_result["company_detected"]
        rec["link_type"] = aff_result["link_type"]
        rec["detection_method"] = aff_result["detection_method"]

        coi_result = detect_coi_links(rec.get("coi_statement", ""), known_companies)
        rec["coi_corporate_flag"] = coi_result["coi_corporate_flag"]
        rec["coi_companies"] = coi_result["coi_companies"]
        rec["coi_link_type"] = coi_result["link_type"]

        fund_result = detect_funding_links(rec.get("funding_sources", ""), known_companies)
        rec["private_funding_flag"] = fund_result["private_funding_flag"]
        rec["private_funders"] = fund_result["private_funders"]

        if rec["corporate_flag"] and not rec["company_detected"] and rec["coi_companies"]:
            rec["company_detected"] = rec["coi_companies"].split(",")[0].strip()

    return records


def _extract_company_name(affiliation: str) -> str:
    parts = [p.strip() for p in affiliation.split(",")]
    for part in parts:
        if CORPORATE_SUFFIXES.search(part) or CORPORATE_KEYWORDS.search(part):
            return part[:120]
    return ""


def _no_match() -> dict:
    return {
        "corporate_flag": False,
        "company_detected": "",
        "link_type": "",
        "detection_method": "",
    }
