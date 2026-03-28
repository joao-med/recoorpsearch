"""
pipeline.py — Full Recoorpsearch pipeline orchestrator.
"""

from typing import Optional

from .search import fetch_affiliation
from .metadata import fetch_metadata
from .affiliations import enrich_records
from .export import export_to_excel, export_to_csv


def run_pipeline(
    query: str,
    max_results: int = 200,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    api_key: Optional[str] = None,
    known_companies: Optional[list[str]] = None,
    output_dir: str = ".",
    output_format: str = "excel",
    filename: Optional[str] = None,
    verbose: bool = True,
) -> dict:
    if verbose:
        print("\n" + "\u2550" * 60)
        print("  RECOORPSEARCH \u2014 Iniciando pipeline")
        print("\u2550" * 60)

    pmids = fetch_affiliation(
        query=query,
        max_results=max_results,
        date_from=date_from,
        date_to=date_to,
        api_key=api_key,
        verbose=verbose,
    )

    if not pmids:
        if verbose:
            print("[recoorpsearch] Nenhum artigo encontrado. Encerrando.")
        return {"pmids": [], "records": [], "summary": {}}

    records = fetch_metadata(pmids=pmids, api_key=api_key, verbose=verbose)

    if verbose:
        print("[recoorpsearch] Detectando v\u00ednculos corporativos...")
    records = enrich_records(records, known_companies=known_companies)

    result: dict = {"pmids": pmids, "records": records}
    fn_base = filename

    if output_format in ("excel", "both"):
        fn = f"{fn_base}.xlsx" if fn_base else None
        excel_path = export_to_excel(
            records=records,
            query=query,
            output_dir=output_dir,
            filename=fn,
            known_companies=known_companies,
        )
        result["excel_path"] = excel_path
        if verbose:
            print(f"[recoorpsearch] Excel salvo: {excel_path}")

    if output_format in ("csv", "both"):
        fn = f"{fn_base}.csv" if fn_base else None
        csv_path = export_to_csv(
            records=records,
            query=query,
            output_dir=output_dir,
            filename=fn,
        )
        result["csv_path"] = csv_path
        if verbose:
            print(f"[recoorpsearch] CSV salvo: {csv_path}")

    result["summary"] = _build_summary(records)

    if verbose:
        _print_summary(result["summary"])

    return result


def _build_summary(records: list[dict]) -> dict:
    total_articles = len({r.get("pmid") for r in records if r.get("pmid")})
    total_authors = len({r.get("author_name") for r in records if r.get("author_name")})
    corp_records = [r for r in records if r.get("corporate_flag")]
    corp_articles = len({r.get("pmid") for r in corp_records if r.get("pmid")})
    corp_authors = len({r.get("author_name") for r in corp_records if r.get("author_name")})

    company_counts: dict[str, int] = {}
    for r in corp_records:
        c = r.get("company_detected", "").strip()
        if c:
            company_counts[c] = company_counts.get(c, 0) + 1

    top_companies = sorted(company_counts.items(), key=lambda x: x[1], reverse=True)[:10]

    return {
        "total_articles": total_articles,
        "total_authors": total_authors,
        "corporate_articles": corp_articles,
        "corporate_authors": corp_authors,
        "top_companies": top_companies,
    }


def _print_summary(s: dict) -> None:
    print("\n" + "\u2500" * 60)
    print("  SUM\u00c1RIO EXECUTIVO")
    print("\u2500" * 60)
    print(f"  Artigos recuperados      : {s['total_articles']:,}")
    print(f"  Autores \u00fanicos           : {s['total_authors']:,}")
    print(f"  Artigos c/ v\u00ednculo corp. : {s['corporate_articles']:,}")
    print(f"  Autores c/ v\u00ednculo corp. : {s['corporate_authors']:,}")
    if s["top_companies"]:
        print("\n  Top empresas detectadas:")
        for company, count in s["top_companies"]:
            print(f"    \u2022 {company}: {count} autores")
    print("\u2500" * 60 + "\n")
