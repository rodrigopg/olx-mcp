# Contexto: Arquitetura

## Layout do server.py

Arquivo único em `mcp_brazil_marketplaces/server.py` (~940 linhas). Ordem das seções:

1. **Imports + logger** (`logger = logging.getLogger("mcp_brazil_marketplaces")`)
2. **`_env()` + flags** — `DISABLE_JINA`, `ALLOWED_OLX_HOSTS`
3. **Constantes** — `BASE_URL`, `BROWSER_PROFILES` (5 perfis), `_build_headers()`
4. **Env helpers** — `_env_float`, `_env_int` com clamp + fallback silencioso
5. **Rate limit** — `RATE_LIMIT_CONCURRENCY`, `RATE_LIMIT_MIN_GAP`, `_rate_semaphore`, `_RateGate` (async context manager)
6. **ESTADOS** — set imutável de siglas UF lowercase
7. **`mcp = FastMCP("mcp_brazil_marketplaces")`**
8. **Modelos pydantic** — `OrdenarPor` enum, `BuscarAnunciosInput`, `DetalheAnuncioInput`, `BuscarMLInput`, `DetalheMLInput`
9. **Validadores SSRF** — `_validar_url_olx`, `_validar_url_ml`
10. **Builders URL** — `_build_search_url`, `_build_ml_url` (retorna `tuple[str, list[str]]` para avisos)
11. **Coerção defensiva** — `_safe_str`, `_safe_dict`, `_safe_list`, `_format_ad_summary`, `_format_timestamp`
12. **Constantes regex** — `MAX_HTML_BYTES = 8MB`, `MAX_NEXT_DATA_BYTES = 5MB`, `_NEXT_DATA_RE`
13. **`_extract_next_data`** — regex linear `[^<]+` (não usa `.*?`)
14. **Fetchers** — `_fetch_with_evasion` (OLX, warm-up + retry + perfis), `_fetch_via_jina` (proxy fallback), `_fetch_with_retries` (genérico ML)
15. **Error handler** — `_handle_http_error` com correlation ID via `uuid.uuid4().hex[:8]`
16. **Parsers** — `_parse_ml_html`, `_parse_search_markdown`
17. **Tools** — `@mcp.tool` decorados: `olx_buscar_anuncios`, `olx_detalhe_anuncio`, `ml_buscar_anuncios`, `ml_detalhe_anuncio`
18. **`def main()` + `if __name__ == "__main__"`**

## Padrão de tool

Toda tool:
- Recebe `params: <ModelInput>` pydantic
- Valida URL via `_validar_url_*` (se aceita URL)
- Loga entrada via `logger.info("<tool> <key>=<val> ...")`
- Faz fetch via `_fetch_with_evasion` (OLX) ou `_fetch_with_retries` (ML)
- Em caso de erro retorna `{"erro": "..."}` JSON, **nunca raise**
- Adiciona campo `fonte` (`olx` | `olx_jina` | `ml`) no envelope e em cada anúncio
- Retorna `json.dumps(result, ensure_ascii=False, indent=2)`

## Schema unificado de anúncio

Campos comuns OLX + ML: `fonte`, `id`, `titulo`, `preco`, `localizacao`, `data`, `url`, `imagem`.
Específicos OLX: `categoria`, `bairro`, `profissional`, `entrega_olx`, `propriedades`.
Específicos ML: `frete`, `vendedor`, `atributos`.
Envelope: `fonte`, `total`, `pagina`, `por_pagina`, `url_busca`, `anuncios`, `avisos` (opcional).

## Anti-padrões a evitar

- **Não recriar `httpx.AsyncClient` por chamada** se tiver lifespan disponível (hoje não tem; aceitar).
- **Não usar `.*?` em `re.DOTALL`** — substituir por `[^<]+` ou similar linear.
- **Não retornar exceção stringificada ao caller** — sempre correlation ID + `logger.exception`.
- **Não passar tipos arbitrários** do payload externo ao LLM — sempre coerce via `_safe_*`.
- **Não fazer fetch sem `_RateGate`** — risco de queimar IP.
- **Não aceitar URLs sem `_validar_url_*`** — SSRF.

## Adicionar marketplace novo

Padrão a seguir:
1. Criar `_validar_url_<m>` com allowlist de hostnames.
2. Criar `class Buscar<M>Input(BaseModel)` + `Detalhe<M>Input`.
3. Criar `_build_<m>_url(p) -> tuple[str, list[str]]`.
4. Criar `_parse_<m>_html(html) -> list[dict]` retornando schema unificado + extras.
5. Criar `@mcp.tool` async usando `_fetch_with_retries(url, HEADERS)`.
6. Adicionar campo `fonte: "<m>"` em todos os anúncios.
7. Cobertura: pelo menos 3 unit tests no `tests/test_unit.py`.
