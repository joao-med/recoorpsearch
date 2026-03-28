---
title: Recoorpsearch
emoji: 🔬
colorFrom: blue
colorTo: indigo
sdk: gradio
sdk_version: "4.44.0"
app_file: app.py
pinned: false
license: mit
short_description: Busca PubMed + detecção de vínculos corporativos em afiliações
---

# 🔬 Recoorpsearch

Agente de pesquisa científica que investiga **vínculos de afiliação** entre autores de artigos indexados no PubMed e empresas farmacêuticas, biotecnológicas e de dispositivos médicos.

## Como usar

### Passo 1 — Busca no PubMed

Digite uma query PubMed e clique em **Buscar Artigos**. A tabela gerada contém um par **autor × artigo** por linha.

```
"Pfizer"[Affiliation] AND "diabetes"[MeSH Terms] AND ("2022"[PDAT]:"2024"[PDAT])
"Novo Nordisk"[Affiliation] AND "obesity"[MeSH Terms]
("AstraZeneca"[Affiliation] OR "AstraZeneca"[Grant Agency])
```

### Passo 2 — Agente de Análise

Clique em **Rodar Agente de Flags**. O agente classifica cada linha com um de três vereditos:

| Veredito | Significado |
|---|---|
| ✅ CONFIRMADO | Vínculo corporativo identificado com alta confiança |
| ❌ NEGADO | Sem evidência de vínculo corporativo |
| ⚠️ DÚVIDA | Nome ambíguo ou contexto insuficiente — reanálise realizada automaticamente |

Para casos **⚠️ DÚVIDA**, o agente relê a janela de texto ao redor do nome encontrado antes de emitir o veredito. A coluna `veredicto_afiliacao` é adicionada por **left join** ao índice original.

### Exportar

Baixe a planilha Excel com três abas: **Resultados**, **Resumo por Empresa** e **Parâmetros da busca**.

## Variáveis de ambiente

| Variável | Descrição |
|---|---|
| `NCBI_API_KEY` | Chave NCBI (opcional — aumenta limite de 3 para 10 req/s) |

## Arquitetura

```
recoorpsearch/          ← package Python (backend)
│   search.py           ← esearch NCBI
│   metadata.py         ← efetch + parse XML
│   affiliations.py     ← detecção de afiliação corporativa
│   pipeline.py         ← orquestrador end-to-end
│   export.py           ← exportação Excel/CSV
affiliation_agent.py    ← agente de veredito (CONFIRMADO/NEGADO/DÚVIDA)
app.py                  ← interface Gradio
requirements.txt
```

## Contato

**João Medeiros** · jpmedeirosg@gmail.com
