"""
recoorpsearch — Investigação de vínculos corporativos em artigos do PubMed.
"""

from .search import fetch_affiliation
from .metadata import fetch_metadata
from .affiliations import (
    detect_corporate_affiliation,
    detect_coi_links,
    detect_funding_links,
    enrich_records,
)
from .export import export_to_excel, export_to_csv
from .pipeline import run_pipeline

__version__ = "0.1.0"
__author__ = "João Medeiros"
__email__ = "jpmedeirosg@gmail.com"

__all__ = [
    "fetch_affiliation",
    "fetch_metadata",
    "detect_corporate_affiliation",
    "detect_coi_links",
    "detect_funding_links",
    "enrich_records",
    "export_to_excel",
    "export_to_csv",
    "run_pipeline",
]
