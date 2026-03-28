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

# ---------------------------------------------------------------------------
# Patch: sanitizar lone surrogates antes do orjson serializar qualquer resposta
#
# O orjson (usado pelo Gradio/FastAPI) rejeita strings com caracteres surrogate
# (U+D800–U+DFFF), que podem aparecer em dados do PubMed ou em strings
# de sistema no Python 3.13, causando:
#   TypeError: str is not valid UTF-8: surrogates not allowed
#
# A solução é interceptar o ORJSONResponse do Gradio e sanitizar recursivamente
# todo o conteúdo antes de passar pro orjson — tanto no get_config (startup)
# quanto em qualquer resposta de runtime.
# ---------------------------------------------------------------------------
def _deep_sanitize(obj):
    """Substitui recursivamente lone surrogates por U+FFFD em qualquer objeto."""
    if isinstance(obj, str):
        return obj.encode("utf-8", errors="replace").decode("utf-8")
    if isinstance(obj, dict):
        return {_deep_sanitize(k): _deep_sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return type(obj)(_deep_sanitize(i) for i in obj)
    return obj


try:
    import orjson
    import gradio.routes as _gr_routes

    class _SafeORJSONResponse(_gr_routes.ORJSONResponse):
        @classmethod
        def _render(cls, content):
            return orjson.dumps(
                _deep_sanitize(content),
                option=orjson.OPT_SERIALIZE_NUMPY | orjson.OPT_PASSTHROUGH_DATETIME,
                default=_gr_routes.ORJSONResponse.default,
            )

    _gr_routes.ORJSONResponse = _SafeORJSONResponse
except Exception:
    pass  # Se o Gradio mudar sua estrutura interna, falha silenciosa
# ---------------------------------------------------------------------------

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
            confirmed = df[df["veredicto_afiliacao"] == "✅ CONFIRMADO"]
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
                        "Nº Autores":   sub["author_name"].nunique() if "author_name" in sub.columns else "-",
                        "Nº Artigos":   sub["pmid"].nunique() if "pmid" in sub.columns else "-",
                        "Periódicos":   ", ".join(sub["journal"].dropna().unique()[:3]) if "journal" in sub.columns else "-",
                        "Período":      f"{sub['pub_year'].min()}–{sub['pub_year'].max()}" if "pub_year" in sub.columns else "-",
                    })
            if rows:
                pd.DataFrame(rows).to_excel(writer, sheet_name="Resumo por Empresa", index=False)

        pd.DataFrame([{
            "Query":           query,
            "Data da busca":   datetime.date.today().isoformat(),
            "Total de linhas": len(df),
            "Artigos únicos":  df["pmid"].nunique() if "pmid" in df.columns else "-",
        }]).to_excel(writer, sheet_name="Parâmetros", index=False)

    return buf.getvalue()


def _slug(text: str, max_len: int = 35) -> str:
    return re.sub(r"[^\w]", "_", text[:max_len])


import re  # noqa: E402 (imported at top for _slug)

# ---------------------------------------------------------------------------
# Step 1 — PubMed search
# ---------------------------------------------------------------------------

def do_search(query: str, max_results: int, date_from: str, date_to: str):
    if not query.strip():
        return "⚠️ Insira uma query PubMed.", None, gr.update(visible=False)

    try:
        result = run_pipeline(
            query=query.strip(),
            max_results=int(max_results),
            date_from=date_from.strip() or None,
            date_to=date_to.strip() or None,
            api_key=NCBI_API_KEY,
            output_format="none",   # no file output here
            verbose=False,
        )
    except Exception as exc:
        return f"❌ Erro: {exc}", None, gr.update(visible=False)

    records = result.get("records", [])
    if not records:
        return "Nenhum artigo encontrado para esta query.", None, gr.update(visible=False)

    df = pd.DataFrame(records)
    _STATE["df"]    = df
    _STATE["query"] = query

    status = (
        f"✅ {result['summary'].get('total_articles', '?')} artigos únicos | "
        f"{len(df)} pares autor × artigo"
    )
    return status, df, gr.update(visible=True)


# ---------------------------------------------------------------------------
# Step 2 — Affiliation agent
# ---------------------------------------------------------------------------

def do_agent(target_txt: str):
    df = _STATE.get("df")
    if df is None or df.empty:
        return "⚠️ Faça a busca primeiro.", None, gr.update(visible=False), ""

    targets = [c.strip() for c in target_txt.split(",") if c.strip()]

    try:
        result_df = run_agent(df, target_companies=targets or None)
    except Exception as exc:
        return f"❌ Erro no agente: {exc}", None, gr.update(visible=False), ""

    _STATE["result_df"] = result_df
    summary = build_summary(result_df, targets or None)
    summary_md = _make_summary_md(summary, targets)

    return "✅ Análise concluída!", result_df, gr.update(visible=True), summary_md


def _make_summary_md(s: dict, targets: list) -> str:
    if not s:
        return ""
    total = s.get("total", 1)
    doubt_pct = round(100 * s.get("duvida", 0) / max(total, 1), 1)

    lines = [
        "## 📊 Resumo do Agente",
        "",
        "| Métrica | Valor |",
        "|---|---|",
        f"| Linhas analisadas | {total} |",
        f"| Artigos únicos | {s.get('unique_articles', '-')} |",
        f"| ✅ CONFIRMADO | **{s.get('confirmado', 0)}** |",
        f"| ❌ NEGADO | {s.get('negado', 0)} |",
        f"| ⚠️ DÚVIDA | {s.get('duvida', 0)} |",
        "",
    ]
    companies = s.get("companies_found", [])
    if companies:
        lines += ["### 🏢 Empresas detectadas", ", ".join(companies), ""]
    if targets:
        lines += ["### 🎯 Empresas-alvo", ", ".join(targets), ""]
    if doubt_pct > 0:
        lines.append(
            f"> ⚠️ {doubt_pct}% das linhas estão como DÚVIDA — revisar manualmente."
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
          <h1>🔬 Recoorpsearch</h1>
          <p style="color:#6b7280;">
            Busca PubMed · Detecção de vínculos corporativos em afiliações de autores
          </p>
        </div>""")

        # ── Passo 1 ──────────────────────────────────────────────────────────
        gr.Markdown("### 📡 Passo 1 — Busca no PubMed")
        with gr.Row():
            query_box = gr.Textbox(
                label="Query PubMed",
                placeholder='"Pfizer"[Affiliation] AND "diabetes"[MeSH Terms]',
                scale=4,
            )
            max_results_slider = gr.Slider(
                label="Máx. artigos", minimum=10, maximum=500, step=10, value=100,
                scale=1,
            )
        with gr.Row():
            date_from_box = gr.Textbox(label="Data início (AAAA ou AAAA/MM/DD)", placeholder="2020", scale=1)
            date_to_box   = gr.Textbox(label="Data fim",   placeholder="2024", scale=1)

        gr.Examples(examples=EXAMPLE_QUERIES, inputs=query_box, label="Exemplos")

        search_btn    = gr.Button("🔍 Buscar Artigos", variant="primary")
        search_status = gr.Textbox(label="Status", interactive=False, lines=1)
        raw_table     = gr.Dataframe(label="Resultados Brutos", wrap=True, interactive=False, max_height=400)
        export_raw_btn = gr.Button("📥 Exportar tabela bruta (.xlsx)", visible=False)
        raw_file       = gr.File(label="Download", visible=False)

        gr.HTML("<hr/>")

        # ── Passo 2 ──────────────────────────────────────────────────────────
        gr.Markdown("### 🤖 Passo 2 — Agente de Análise de Vínculos")
        gr.Markdown(
            "O agente classifica cada par autor × artigo como **✅ CONFIRMADO**, "
            "**❌ NEGADO** ou **⚠️ DÚVIDA**, relendo o contexto ao redor de nomes "
            "ambíguos antes de emitir o veredito. O resultado é adicionado à tabela "
            "por *left join* no índice original."
        )
        target_box = gr.Textbox(
            label="Empresas-alvo (opcional, separadas por vírgula)",
            placeholder="Pfizer, Novo Nordisk, Roche",
            info="Prioriza a busca por essas empresas no contexto de cada linha.",
        )
        agent_btn    = gr.Button("🤖 Rodar Agente de Flags", variant="primary")
        agent_status = gr.Textbox(label="Status", interactive=False, lines=1)
        result_table = gr.Dataframe(
            label="Tabela com Veredito (left join)", wrap=True,
            interactive=False, max_height=500,
        )
        summary_out       = gr.Markdown()
        export_verdict_btn = gr.Button("📥 Exportar com veredito (.xlsx)", visible=False)
        verdict_file       = gr.File(label="Download", visible=False)

        gr.HTML("""
        <hr/>
        <p style="text-align:center;color:#9ca3af;font-size:.85rem;">
          Recoorpsearch · Dados: NCBI PubMed ·
          <a href="https://www.ncbi.nlm.nih.gov/books/NBK25501/" target="_blank">
            Documentação E-utilities
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
