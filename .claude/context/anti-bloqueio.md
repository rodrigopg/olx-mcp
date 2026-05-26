# Contexto: Anti-bloqueio

OLX e Mercado Livre ativamente bloqueiam scraping. Aprendizados validados em produção:

## OLX

- **HTTP/2 obrigatório** — HTTP/1.1 retorna 403. Instalar `httpx[http2]`.
- **Pool de 5 perfis de browser** (`BROWSER_PROFILES`): Chrome mac/win/linux + Firefox + iOS Safari. UA + `Sec-Ch-Ua` + platform coerentes.
- **Warm-up homepage** antes do search (probabilidade configurável, default 0.7) para captar cookies anti-bot.
- **Referer alternado**: `https://www.google.com/` ou `https://www.olx.com.br/` aleatório, com `Sec-Fetch-Site` coerente (cross-site vs same-origin).
- **Retry exponencial + jitter** em 403/429/503: `(2**attempt) + random.uniform(0.3, 1.5)`.
- **`sf=1` na URL zera resultados** em algumas buscas — remover (commit `add anti-block evasion`).
- **Fallback Jina** (`r.jina.ai`) quando todos retries falham. Retorna markdown, não HTML — parser dedicado.

## Mercado Livre

- **Micro-landing anti-bot** com UA real. Workaround: `User-Agent: Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)`.
- ML respeita Googlebot por SEO. Sem isso, página retorna ~7KB com challenge JS.
- **`/lista.mercadolivre.com.br/<slug>`** funciona; `/celulares-telefones/.../<query>_NoIndex_True` também.
- **API `api.mercadolibre.com` exige bearer token** (mesmo endpoints públicos). Não usar.
- **Filtro `_ITEM_CONDITION_<id>` ignorado via URL** — ML canonicaliza removendo. Aplicar pós-scraping no título.
- **Cards classe `poly-component__title`, preço `andes-money-amount__fraction`** — estáveis há anos.
- **ID extraído via regex `MLB-?(\d+)`** na URL do produto.

## r.jina.ai fallback

- URL: `https://r.jina.ai/<url-target>`
- Retorna markdown, prefixado por `Title: ...\nURL Source: ...\nMarkdown Content:\n`
- Cobra rate limit silencioso — pode dar 401 inesperadamente
- **ML não funciona via Jina** (cookie wall persiste)
- **Anonymous queries** — Jina logga URLs. Opt-out: `MCP_BR_DISABLE_JINA=1`.
- Parser de markdown: cortar seção `## Você pode gostar` antes de extrair (recomendações poluem).

## Não fazer

- **Não usar `selenium`/`playwright` no MCP** — pesado demais, perde stdio responsivo.
- **Não tentar resolver Cloudflare challenge** — gato e rato infinito.
- **Não compartilhar cookies cross-request** — perfil rotativo precisa cookie jar próprio.
- **Não usar UA real em ML por padrão** — bate em landing.
- **Não confiar em IP datacenter** — OLX bloqueia AWS/GCP/Azure mais agressivamente.

## Detecção de quebra

Sintomas de OLX/ML mudaram estrutura:
- `_extract_next_data` falha com "Não foi possível encontrar dados estruturados"
- `_parse_ml_html` retorna lista vazia mas HTML > 500KB
- Tudo retorna 403 mesmo com perfil novo + warm-up
- Jina fallback retorna markdown sem `R$`

Mitigação: roadmap #R-19 (schema diff detector).
