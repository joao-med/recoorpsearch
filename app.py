"""
app.py — Recoorpsearch · Interface Gradio
Busca artigos no PubMed e detecta vínculos corporativos em afiliações de autores.
"""

import io
import os
import datetime
from typing import Optional

import gradio as gr
import pandas as pd

from recoorpsearch.pipeline import run_pipeline
from affiliation_agent import run_agent, build_summary

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

NCBI_API_KEY: Optional[str] = os.getenv("NCBI_API_KEY")

EXAMPLE_QUERIES = [
    '"Pfizer"[Affiliation] AND "diabetes"[MeSH Terms] AND ("2022"[PDAT]:"2024"[PDAT])',
    '"Novo Nordisk"[Affiliation] AND "obesity"[MeSH Terms]',
    '("AstraZeneca"[Affiliation] OR "AstraZeneca"[Grant Agency])',
    '"Roche"[Affiliation] AND "cancer"[MeSH Terms] AND ("2023"[PDAT]:"2024"[PDAT])',
]

# Simple in-memory session state
_STATE: dict = {}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_excel_bytes(df: pd.DataFrame, query: str = "") -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Resultados", index=False)

        # Summary sheet (only when agent has run)
        if "veredicto_afiliacao" in df.columns:
            from affiliation_agent import KNOWN_COMPANIES
            confirmed = df[df["veredicto_afiliacao"] == "\u2705 CONFIRMADO"]
            rows = []
            for company in KNOWN_COMPANIES:
                mask = confirmed.apply(
                    lambda r: company.lower() in " ".join([
                        str(r.get("author_affiliation", "")),
                        str(r.get("coi_statement", "")),
                        str(r.get("funding_sources", "")),
                    ]).lower(),
                    axis=1,
                )
                if mask.any():
                    sub = confirmed[mask]
                    rows.append({
                        "Empresa":      company,
                        "N\u00ba Autores":   sub["author_name"].nunique() if "author_name" in sub.columns else "-",
                        "N\u00ba Artigos":   sub["pmid"].nunique() if "pmid" in sub.columns else "-",
                        "Peri\u00f3dicos":   ", ".join(sub["journal"].dropna().unique()[:3]) if "journal" in sub.columns else "-",
                        "Per\u00edodo":      f"{sub['pub_year'].min()}\u2013{sub['pub_year'].max()}" if "pub_year" in sub.columns else "-",
                    })
            if rows:
                pd.DataFrame(rows).to_excel(writer, sheet_name="Resumo por Empresa", index=False)

        pd.DataFrame([{
            "Query":           query,
            "Data da busca":   datetime.date.today().isoformat(),
            "Total de linhas": len(df),
            "Artigos \u00fanicos":  df["pmid"].nunique() if "pmid" in df.columns else "-",
        }]).to_excel(writer, sheet_name="Par\u00e2metros", index=False)

    return buf.getvalue()


import re  # noqa: E402


def _slug(text: str, max_len: int = 35) -> str:
    return re.sub(r"[^\w]", "_", text[:max_len])


# ---------------------------------------------------------------------------
# Step 1 — PubMed search
# ---------------------------------------------------------------------------

def do_search(query: str, max_results: int, date_from: str, date_to: str):
    if not query.strip():
        return "\u26a0\ufe0f Insira uma query PubMed.", None, gr.update(visible=False)

    try:
        result = run_pipeline(
            query=query.strip(),
            max_results=int(max_results),
            date_from=date_from.strip() or None,
            date_to=date_to.strip() or None,
            api_key=NCBI_API_KEY,
            output_format="none",
            verbose=False,
        )
    except Exception as exc:
        return f"\u274c Erro: {exc}", None, gr.update(visible=False)

    records = result.get("records", [])
    if not records:
        return "Nenhum artigo encontrado para esta query.", None, gr.update(visible=False)

    df = pd.DataFrame(records)
    _STATE["df"]    = df
    _STATE["query"] = query

    status = (
        f"\u2705 {result['summary'].get('total_articles', '?')} artigos \u00fanicos | "
        f"{len(df)} pares autor \u00d7 artigo"
    )
    return status, df, gr.update(visible=True)


# ---------------------------------------------------------------------------
# Step 2 — Affiliation agent
# ---------------------------------------------------------------------------

def do_agent(target_txt: str):
    df = _STATE.get("df")
    if df is None or df.empty:
        return "\u26a0\ufe0f Fa\u00e7a a busca primeiro.", None, gr.update(visible=False), ""

    targets = [c.strip() for c in target_txt.split(",") if c.strip()]

    try:
        result_df = run_agent(df, target_companies=targets or None)
    except Exception as exc:
        return f"\u274c Erro no agente: {exc}", None, gr.update(visible=False), ""

    _STATE["result_df"] = result_df
    summary = build_summary(result_df, targets or None)
    summary_md = _make_summary_md(summary, targets)

    return "\u2705 An\u00e1lise conclu\u00edda!", result_df, gr.update(visible=True), summary_md


def _make_summary_md(s: dict, targets: list) -> str:
    if not s:
        return ""
    total = s.get("total", 1)
    doubt_pct = round(100 * s.get("duvida", 0) / max(total, 1), 1)

    lines = [
        "## \ud83d\udcca Resumo do Agente",
        "",
        "| M\u00e9trica | Valor |",
        "|---|---|",
        f"| Linhas analisadas | {total} |",
        f"| Artigos \u00fanicos | {s.get('unique_articles', '-')} |",
        f"| \u2705 CONFIRMADO | **{s.get('confirmado', 0)}** |",
        f"| \u274c NEGADO | {s.get('negado', 0)} |",
        f"| \u26a0\ufe0f D\u00daVIDA | {s.get('duvida', 0)} |",
        "",
    ]
    companies = s.get("companies_found", [])
    if companies:
        lines += ["### \ud83c\udfe2 Empresas detectadas", ", ".join(companies), ""]
    if targets:
        lines += ["### \ud83c\udfaf Empresas-alvo", ", ".join(targets), ""]
    if doubt_pct > 0:
        lines.append(
            f"> \u26a0\ufe0f {doubt_pct}% das linhas est\u00e3o como D\u00daVIDA \u2014 revisar manualmente."
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export_raw():
    df = _STATE.get("df")
    if df is None or df.empty:
        return None
    path = f"/tmp/recoorpsearch_raw_{datetime.date.today()}.xlsx"
    with open(path, "wb") as f:
        f.write(_to_excel_bytes(df, _STATE.get("query", "")))
    return path


def export_with_verdict():
    df = _STATE.get("result_df")
    if df is None or df.empty:
        return None
    query = _STATE.get("query", "query")
    slug  = re.sub(r"[^\w]", "_", query[:30])
    path  = f"/tmp/recoorpsearch_{slug}_{datetime.date.today()}.xlsx"
    with open(path, "wb") as f:
        f.write(_to_excel_bytes(df, query))
    return path


# ---------------------------------------------------------------------------
# Gradio UI
# ---------------------------------------------------------------------------

def build_ui() -> gr.Blocks:
    with gr.Blocks(
        title="Recoorpsearch",
        theme=gr.themes.Soft(primary_hue="blue"),
        css=".title { text-align:center; padding:1rem 0; } footer{display:none!important}",
    ) as demo:

        gr.HTML("""
        <div class="title">
          <h1>\ud83d\udd2c Recoorpsearch</h1>
          <p style="color:#6b7280;">
            Busca PubMed \u00b7 Detec\u00e7\u00e3o de v\u00ednculos corporativos em afilia\u00e7\u00f5es de autores
          </p>
        </div>""")

        # ── Passo 1 ──────────────────────────────────────────────────────────
        gr.Markdown("### \ud83d\udce1 Passo 1 \u2014 Busca no PubMed")
        with gr.Row():
            query_box = gr.Textbox(
                label="Query PubMed",
                placeholder='"Pfizer"[Affiliation] AND "diabetes"[MeSH Terms]',
                scale=4,
            )
            max_results_slider = gr.Slider(
                label="M\u00e1x. artigos", minimum=10, maximum=500, step=10, value=100,
                scale=1,
            )
        with gr.Row():
            date_from_box = gr.Textbox(label="Data in\u00edcio (AAAA ou AAAA/MM/DD)", placeholder="2020", scale=1)
            date_to_box   = gr.Textbox(label="Data fim",   placeholder="2024", scale=1)

        gr.Examples(examples=EXAMPLE_QUERIES, inputs=query_box, label="Exemplos")

        search_btn    = gr.Button("\ud83d\udd0d Buscar Artigos", variant="primary")
        search_status = gr.Textbox(label="Status", interactive=False, lines=1)
        raw_table     = gr.Dataframe(label="Resultados Brutos", wrap=True, interactive=False, max_height=400)
        export_raw_btn = gr.Button("\ud83d\udce5 Exportar tabela bruta (.xlsx)", visible=False)
        raw_file       = gr.File(label="Download", visible=False)

        gr.HTML("<hr/>")

        # ── Passo 2 ──────────────────────────────────────────────────────────
        gr.Markdown("### \ud83e\udd16 Passo 2 \u2014 Agente de An\u00e1lise de V\u00ednculos")
        gr.Markdown(
            "O agente classifica cada par autor \u00d7 artigo como **\u2705 CONFIRMADO**, "
            "**\u274c NEGADO** ou **\u26a0\ufe0f D\u00daVIDA**, relendo o contexto ao redor de nomes "
            "amb\u00edguos antes de emitir o veredito. O resultado \u00e9 adicionado \u00e0 tabela "
            "por *left join* no \u00edndice original."
        )
        target_box = gr.Textbox(
            label="Empresas-alvo (opcional, separadas por v\u00edrgula)",
            placeholder="Pfizer, Novo Nordisk, Roche",
            info="Prioriza a busca por essas empresas no contexto de cada linha.",
        )
        agent_btn    = gr.Button("\ud83e\udd16 Rodar Agente de Flags", variant="primary")
        agent_status = gr.Textbox(label="Status", interactive=False, lines=1)
        result_table = gr.Dataframe(
            label="Tabela com Veredito (left join)", wrap=True,
            interactive=False, max_height=500,
        )
        summary_out       = gr.Markdown()
        export_verdict_btn = gr.Button("\ud83d\udce5 Exportar com veredito (.xlsx)", visible=False)
        verdict_file       = gr.File(label="Download", visible=False)

        gr.HTML("""
        <hr/>
        <p style="text-align:center;color:#9ca3af;font-size:.85rem;">
          Recoorpsearch \u00b7 Dados: NCBI PubMed \u00b7
          <a href="https://www.ncbi.nlm.nih.gov/books/NBK25501/" target="_blank">
            Documenta\u00e7\u00e3o E-utilities
          </a>
        </p>""")

        # ── Wiring ───────────────────────────────────────────────────────────
        search_btn.click(
            fn=do_search,
            inputs=[query_box, max_results_slider, date_from_box, date_to_box],
            outputs=[search_status, raw_table, export_raw_btn],
        )
        export_raw_btn.click(
            fn=export_raw,
            outputs=raw_file,
        ).then(fn=lambda: gr.update(visible=True), outputs=raw_file)

        agent_btn.click(
            fn=do_agent,
            inputs=[target_box],
            outputs=[agent_status, result_table, export_verdict_btn, summary_out],
        )
        export_verdict_btn.click(
            fn=export_with_verdict,
            outputs=verdict_file,
        ).then(fn=lambda: gr.update(visible=True), outputs=verdict_file)

    return demo


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app = build_ui()
    app.launch(server_name="0.0.0.0", server_port=7860)
