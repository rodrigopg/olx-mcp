# Changelog

Todas as mudanças relevantes deste projeto são registradas aqui.

O formato é baseado em [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
e este projeto adere a [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Branch protection nativa na `main` com PR obrigatório + CI verde (5 status checks).
- Badges no README (PyPI version, Python versions, downloads, CI, license, ruff).
- Este `CHANGELOG.md`.

## [0.3.0] — 2026-05-22

Primeira release publicada no PyPI: <https://pypi.org/project/mcp-brazil-marketplaces/0.3.0/>.

### Added
- Tool `ml_buscar_anuncios` — busca no Mercado Livre Brasil via UA Googlebot.
- Tool `ml_detalhe_anuncio` — paridade com `olx_detalhe_anuncio`.
- Bypass anti-bloqueio: pool de 5 perfis de browser, warm-up de cookies, retry com backoff exponencial, fallback `r.jina.ai`.
- Rate limit por host: `_RateGate` com semáforo global + gap mínimo (`MCP_BR_RATE_LIMIT_*`).
- Logging estruturado no logger `mcp_brazil_marketplaces` com correlation IDs nos erros.
- Schema unificado entre OLX e ML: campos comuns `fonte`, `id`, `titulo`, `preco`, `localizacao`, `data`, `url`, `imagem`.
- Compat env `MCP_BR_*` com fallback `OLX_MCP_*` legado.
- CI GitHub Actions: ruff + pytest em Python 3.10/3.11/3.12/3.13 + build wheel/sdist.
- 55 unit tests cobrindo SSRF guard, parsers, formatters, rate-limit, validação de payload.
- `SECURITY.md` com canal de disclosure via GitHub Security Advisories.
- `LICENSE` MIT (faltava no repo apesar do metadata).

### Changed
- **BREAKING:** pacote renomeado de `olx-mcp` para `mcp-brazil-marketplaces` (import `mcp_brazil_marketplaces`).
- **BREAKING:** schema de resposta uniformizado — ML retorna `total`/`por_pagina` (antes `total_retornados`) e cards têm `id`/`data`.
- Dependências fechadas no upper-bound: `mcp>=1.0,<2.0`, `httpx[http2]>=0.27,<1.0`, `pydantic>=2.0,<3.0`.
- Mensagens de erro genéricas com correlation ID em vez de stack trace serializada.
- Coerção defensiva em `_format_ad_summary` contra payloads `__NEXT_DATA__` envenenados (limites de tamanho de string, dict, list).

### Security
- **SSRF guard** em `olx_detalhe_anuncio` (allowlist `*.olx.com.br`) e `ml_detalhe_anuncio` (allowlist `*.mercadolivre.com.br`/`*.mercadolibre.com`).
- **ReDoS-proof** na regex `__NEXT_DATA__` — substituído `.*?` em `re.DOTALL` por `[^<]+` linear, com cap de 8 MB no HTML e 5 MB no blob JSON.
- **Opt-out do fallback Jina** via `MCP_BR_DISABLE_JINA=1` (evita que queries vazem para terceiros).
- **Opt-out do spoof Googlebot** via `MCP_BR_ML_USER_AGENT`.

## [0.2.0] — 2026-05-21 (não publicada no PyPI)

Versão interna pré-rename. Conteúdo equivalente a 0.3.0 ainda sob o nome `olx-mcp`.

## [0.1.0] — 2026-03-25

### Added
- Implementação inicial com `olx_buscar_anuncios` e `olx_detalhe_anuncio`.
- Scraping via `__NEXT_DATA__` da OLX com FastMCP sobre stdio.
- Estrutura de pacote `olx_mcp/` com console script.

[Unreleased]: https://github.com/rodrigopg/mcp-brazil-marketplaces/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/rodrigopg/mcp-brazil-marketplaces/releases/tag/v0.3.0
[0.2.0]: https://github.com/rodrigopg/mcp-brazil-marketplaces/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/rodrigopg/mcp-brazil-marketplaces/commits/v0.1.0
