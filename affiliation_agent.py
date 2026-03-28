"""
affiliation_agent.py — Agente de veredito de vínculos corporativos.

Consome os registros já enriquecidos pelo pipeline recoorpsearch e aplica
uma classificação em três níveis:

  ✅ CONFIRMADO — vínculo corporativo identificado com alta confiança
  ❌ NEGADO     — sem evidência de vínculo corporativo
  ⚠️  DÚVIDA    — nome ambíguo ou contexto insuficiente; reanálise do contexto
                  é realizada automaticamente antes de emitir o veredito final.

O veredito é acrescentado como coluna nova via left join no DataFrame.
"""

import re
from typing import Optional

import pandas as pd

# ---------------------------------------------------------------------------
# Knowledge base — nomes ambíguos e empresas conhecidas
# ---------------------------------------------------------------------------

AMBIGUOUS_MAP: dict[str, list[str]] = {
    "novo":     ["Novo Nordisk"],
    "lilly":    ["Eli Lilly"],
    "bristol":  ["Bristol-Myers Squibb"],
    "merck":    ["Merck & Co.", "Merck KGaA"],
    "msd":      ["Merck Sharp & Dohme"],
    "sun":      ["Sun Pharma"],
    "abbott":   ["Abbott Laboratories"],
    "bd":       ["Becton Dickinson"],
    "bms":      ["Bristol-Myers Squibb"],
    "gsk":      ["GlaxoSmithKline"],
    "ucb":      ["UCB S.A."],
    "ipsen":    ["Ipsen S.A."],
    "organon":  ["Organon & Co."],
    "gilead":   ["Gilead Sciences"],
    "biogen":   ["Biogen Inc."],
    "vertex":   ["Vertex Pharmaceuticals"],
    "moderna":  ["Moderna Inc."],
    "alcon":    ["Alcon Inc."],
    "bausch":   ["Bausch Health"],
    "hologic":  ["Hologic Inc."],
    "danaher":  ["Danaher Corp."],
    "illumina": ["Illumina Inc."],
    "qiagen":   ["Qiagen N.V."],
    "ferring":  ["Ferring Pharmaceuticals"],
    "almirall": ["Almirall S.A."],
    "grifols":  ["Grifols S.A."],
    "servier":  ["Servier"],
    "seagen":   ["Seagen Inc."],
}

KNOWN_COMPANIES: list[str] = [
    "Pfizer", "Novartis", "Roche", "Hoffmann-La Roche",
    "Johnson & Johnson", "AstraZeneca", "Sanofi", "AbbVie",
    "Bristol-Myers Squibb", "Eli Lilly", "Gilead Sciences", "Amgen",
    "Biogen", "Regeneron", "Moderna", "BioNTech", "Novo Nordisk",
    "Bayer", "Boehringer Ingelheim", "GlaxoSmithKline", "GSK",
    "Takeda", "Astellas", "Daiichi Sankyo", "Eisai", "Otsuka",
    "Medtronic", "Boston Scientific", "Stryker", "Zimmer Biomet",
    "Becton Dickinson", "Edwards Lifesciences", "Intuitive Surgical",
    "Vertex Pharmaceuticals", "Alexion", "Blueprint Medicines",
    "Seagen", "Incyte", "Viatris", "Mylan", "Teva", "Sun Pharma",
    "Cipla", "Dr. Reddy", "Aurobindo", "Lupin", "UCB", "Servier",
    "Ipsen", "Pierre Fabre", "Organon", "Ferring", "Almirall",
    "Grifols", "Fresenius", "Vifor", "Genentech", "MedImmune",
    "Bausch", "Alcon", "Hologic", "Danaher", "Thermo Fisher",
    "Bio-Rad", "Qiagen", "Illumina", "10x Genomics",
    "Exact Sciences", "Genomic Health", "Guardant", "Myriad Genetics",
    "Abbott", "Merck", "MSD",
]

_LINK_PATTERNS = re.compile(
    r"\b(employ(?:ed|ee|ment)|works?\s+(?:at|for|with)|consult(?:ant|ing|ancy)|"
    r"advisor(?:y)?|speaker\s+(?:bureau|fee)|honorari\w*|"
    r"stock\s+(?:option|holder|ownership)|equity\s+interest|sharehold\w*|"
    r"has\s+received|research\s+support|funding\s+from|sponsored\s+by|"
    r"conflict\s+of\s+interest|discloses?|declaration\s+of\s+interest)\b",
    re.IGNORECASE,
)

_CORPORATE_SUFFIXES = re.compile(
    r"\b(Inc\.?|Ltd\.?|LLC|L\.L\.C\.?|Corp\.?|Corporation|GmbH|S\.A\.?|"
    r"S\.p\.A\.|B\.V\.|N\.V\.|AG|PLC|plc|Co\.\s|A/S|ApS|Ltda\.?|S\.L\.?|S\.R\.L\.?)\b",
    re.IGNORECASE,
)

_ACADEMIC_KEYWORDS = re.compile(
    r"\b(University|Universidade|Universit\u00e4t|Universit\u00e9|Universidad|Universit\u00e0|"
    r"College|Institute|Instituto|Institut|Hospital|Clinic|Foundation|"
    r"Ministry|Government|National\s+Center|NIH|CDC|WHO|NHS|CNRS|INSERM)\b",
    re.IGNORECASE,
)


def _full_context(row: pd.Series) -> str:
    parts = [
        str(row.get("author_affiliation", "") or ""),
        str(row.get("coi_statement", "") or ""),
        str(row.get("funding_sources", "") or ""),
        str(row.get("affiliation", "") or ""),
        str(row.get("grant_list", "") or ""),
    ]
    return " ".join(p for p in parts if p.strip())


def _reread_window(text: str, term: str, window: int = 150) -> str:
    idx = text.lower().find(term.lower())
    if idx < 0:
        return text
    return text[max(0, idx - window // 2) : idx + len(term) + window // 2]


def classify_row(row: pd.Series, target_companies: Optional[list[str]] = None) -> tuple[str, str]:
    ctx = _full_context(row)

    if not ctx.strip():
        return "\u274c NEGADO", "Sem texto de afilia\u00e7\u00e3o/COI dispon\u00edvel."

    if target_companies:
        for company in target_companies:
            if re.search(r"\b" + re.escape(company) + r"\b", ctx, re.IGNORECASE):
                return _confirm_or_doubt(ctx, company, source="empresa-alvo")

    for company in KNOWN_COMPANIES:
        if re.search(r"\b" + re.escape(company) + r"\b", ctx, re.IGNORECASE):
            return _confirm_or_doubt(ctx, company, source="empresa conhecida")

    for short, candidates in AMBIGUOUS_MAP.items():
        if re.search(r"\b" + re.escape(short) + r"\b", ctx, re.IGNORECASE):
            for full_name in candidates:
                if re.search(r"\b" + re.escape(full_name) + r"\b", ctx, re.IGNORECASE):
                    return "\u2705 CONFIRMADO", f"Nome completo confirmado: {full_name}."

            window_text = _reread_window(ctx, short)
            corp_in_window = _CORPORATE_SUFFIXES.search(window_text)
            link_in_window = _LINK_PATTERNS.search(window_text)

            if corp_in_window or link_in_window:
                hint = (corp_in_window or link_in_window).group(0)
                cands = " / ".join(candidates)
                return (
                    "\u2705 CONFIRMADO",
                    f"Nome amb\u00edguo '{short}' (provavelmente {cands}) confirmado por "
                    f"contexto corporativo pr\u00f3ximo: '{hint}'.",
                )

            cands = " ou ".join(candidates)
            return (
                "\u26a0\ufe0f  D\u00daVIDA",
                f"Nome '{short}' encontrado mas contexto ao redor \u00e9 inconclusivo "
                f"(pode ser {cands}). Requer verifica\u00e7\u00e3o manual.",
            )

    if row.get("corporate_flag") or row.get("coi_corporate_flag"):
        company_det = str(row.get("company_detected", "") or "")
        coi_co      = str(row.get("coi_companies", "") or "")
        companies   = company_det or coi_co or "indicador gen\u00e9rico"

        has_academic = bool(_ACADEMIC_KEYWORDS.search(ctx))
        has_suffix   = bool(_CORPORATE_SUFFIXES.search(ctx))

        if has_academic and not has_suffix:
            return (
                "\u26a0\ufe0f  D\u00daVIDA",
                f"Indicador corporativo detectado ({companies}) mas contexto "
                f"tamb\u00e9m \u00e9 acad\u00eamico. Pode ser colabora\u00e7\u00e3o ou men\u00e7\u00e3o indireta.",
            )

        link_type = str(row.get("link_type", "") or row.get("coi_link_type", "") or "")
        suffix = f" | tipo: {link_type}" if link_type else ""
        return "\u2705 CONFIRMADO", f"V\u00ednculo corporativo: {companies}{suffix}."

    if row.get("private_funding_flag"):
        funders = str(row.get("private_funders", "") or "")
        return "\u2705 CONFIRMADO", f"Financiamento privado identificado: {funders}."

    return "\u274c NEGADO", "Nenhum indicador de v\u00ednculo corporativo detectado."


def _confirm_or_doubt(ctx: str, company: str, source: str) -> tuple[str, str]:
    link_hit = _LINK_PATTERNS.search(ctx)
    if link_hit:
        return (
            "\u2705 CONFIRMADO",
            f"[{source}: {company}] Padr\u00e3o de v\u00ednculo direto: '{link_hit.group(0)}'.",
        )

    window = _reread_window(ctx, company)
    corp_in_window = _CORPORATE_SUFFIXES.search(window)

    academic_count = len(_ACADEMIC_KEYWORDS.findall(ctx))
    if academic_count >= 2 and not corp_in_window:
        return (
            "\u26a0\ufe0f  D\u00daVIDA",
            f"[{source}: {company}] Encontrado mas contexto \u00e9 predominantemente "
            f"acad\u00eamico ({academic_count} indicadores). Pode ser men\u00e7\u00e3o em refer\u00eancia "
            f"ou conflito n\u00e3o declarado explicitamente.",
        )

    return (
        "\u2705 CONFIRMADO",
        f"[{source}: {company}] Nome identificado na afilia\u00e7\u00e3o/contexto.",
    )


def run_agent(
    df: pd.DataFrame,
    target_companies: Optional[list[str]] = None,
    progress_cb=None,
) -> pd.DataFrame:
    if df.empty:
        return df

    verdicts:       list[str] = []
    justifications: list[str] = []
    total = len(df)

    for i, (_, row) in enumerate(df.iterrows()):
        v, j = classify_row(row, target_companies)
        verdicts.append(v)
        justifications.append(j)
        if progress_cb:
            progress_cb(i + 1, total)

    result = df.copy()
    result["veredicto_afiliacao"]  = verdicts
    result["justificativa_agente"] = justifications
    return result


def build_summary(df: pd.DataFrame, target_companies: Optional[list[str]] = None) -> dict:
    if "veredicto_afiliacao" not in df.columns:
        return {}

    counts = df["veredicto_afiliacao"].value_counts().to_dict()
    total  = len(df)

    companies_found: list[str] = []
    confirmed = df[df["veredicto_afiliacao"] == "\u2705 CONFIRMADO"]
    for company in KNOWN_COMPANIES + (target_companies or []):
        mask = confirmed.apply(
            lambda r: company.lower() in _full_context(r).lower(), axis=1
        )
        if mask.any() and company not in companies_found:
            companies_found.append(company)

    return {
        "total":           total,
        "unique_articles": df["pmid"].nunique() if "pmid" in df.columns else "-",
        "confirmado":      counts.get("\u2705 CONFIRMADO", 0),
        "negado":          counts.get("\u274c NEGADO", 0),
        "duvida":          counts.get("\u26a0\ufe0f  D\u00daVIDA", 0),
        "companies_found": companies_found,
    }
