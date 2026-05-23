# mcp-brazil-marketplaces

MCP server para buscar anúncios públicos da [OLX Brasil](https://www.olx.com.br) e do [Mercado Livre Brasil](https://www.mercadolivre.com.br) — com bypass automático de bloqueios anti-bot (rotação de User-Agent, warm-up de cookies, retry com backoff, fallback via `r.jina.ai`, Googlebot UA para o Mercado Livre).

## Instalação rápida (zero clone, zero venv)

Use [`uv`](https://docs.astral.sh/uv/) — instale uma vez:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Depois rode direto:

```bash
uvx mcp-brazil-marketplaces
```

Ou via `pip` tradicional:

```bash
pip install mcp-brazil-marketplaces
mcp-brazil-marketplaces
```

## Configuração no Claude Desktop

Cole o bloco abaixo em `claude_desktop_config.json`:

- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
- **Linux:** `~/.config/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "marketplaces-br": {
      "command": "uvx",
      "args": ["mcp-brazil-marketplaces"]
    }
  }
}
```

Se preferir `pip` em vez de `uvx`:

```json
{
  "mcpServers": {
    "marketplaces-br": {
      "command": "mcp-brazil-marketplaces"
    }
  }
}
```

Reinicie o Claude Desktop. As ferramentas `olx_buscar_anuncios`, `olx_detalhe_anuncio` e `ml_buscar_anuncios` ficam disponíveis.

## Configuração no Claude Code / Cursor / Continue

**Claude Code (CLI):**

```bash
claude mcp add olx -- uvx mcp-brazil-marketplaces
```

**Cursor** (`~/.cursor/mcp.json`) e **Continue** (`~/.continue/config.json`) usam o mesmo bloco JSON do Claude Desktop.

## Ferramentas

### `olx_buscar_anuncios`

Busca anúncios na OLX com filtros.

| Parâmetro | Tipo | Obrigatório | Descrição |
|---|---|---|---|
| `query` | string | Sim | Termo de busca |
| `estado` | string | Não | Sigla do estado (`sp`, `rj`, `go`…) |
| `categoria` | string | Não | Slug de categoria (`celulares`, `imoveis`…) |
| `preco_min` | int | Não | Preço mínimo em reais |
| `preco_max` | int | Não | Preço máximo em reais |
| `ordenar` | string | Não | `relevance` \| `price` \| `date` |
| `pagina` | int | Não | Página (1–50) |

### `olx_detalhe_anuncio`

Retorna detalhes completos de um anúncio da OLX pela URL.

| Parâmetro | Tipo | Obrigatório | Descrição |
|---|---|---|---|
| `url` | string | Sim | URL completa do anúncio na OLX |

### `ml_buscar_anuncios`

Busca anúncios no Mercado Livre Brasil.

| Parâmetro | Tipo | Obrigatório | Descrição |
|---|---|---|---|
| `query` | string | Sim | Termo de busca |
| `preco_min` | int | Não | Preço mínimo em reais |
| `preco_max` | int | Não | Preço máximo em reais |
| `condicao` | string | Não | `novo` \| `usado` |
| `estado` | string | Não | Sigla UF para filtragem pós-scraping (ver avisos) |
| `pagina` | int | Não | Página (1–20, 50 itens cada) |

### `ml_detalhe_anuncio`

Retorna detalhes de um anúncio do Mercado Livre.

| Parâmetro | Tipo | Obrigatório | Descrição |
|---|---|---|---|
| `url` | string | Sim | URL completa do anúncio (`*.mercadolivre.com.br` ou `*.mercadolibre.com`) |

### Diferenças entre as tools

| Aspecto | OLX | Mercado Livre |
|---|---|---|
| Páginas máx. | 50 | 20 |
| Ordenação | `relevance` \| `price` \| `date` | Não suportada (ML não aceita via URL pública) |
| Filtro `condicao` | N/A | Heurística pós-scraping no título |
| Filtro `estado` | Nativo na URL | Heurística pós-scraping (frequentemente vazio) |
| Detalhe de anúncio | `olx_detalhe_anuncio` | `ml_detalhe_anuncio` |

Os limites de página diferem porque cada site retorna ~50 itens por página por padrão e a profundidade útil é menor no ML (resultados ficam ruins após a página 20).

## Exemplos de uso

> Busque iPhones usados em São Paulo por até R$ 2000, ordenados por menor preço.

> Procure Google Pixel 10 Pro XL na OLX e no Mercado Livre. Monte uma tabela comparativa.

> Me dê os detalhes do anúncio: https://sp.olx.com.br/...

## Desenvolvimento

```bash
git clone https://github.com/rodrigopg/mcp-brazil-marketplaces
cd mcp-brazil-marketplaces
python -m venv .venv
.venv/bin/pip install -e .
```

Rodar o servidor localmente:

```bash
.venv/bin/mcp-brazil-marketplaces
# ou
.venv/bin/python -m mcp_brazil_marketplaces
```

## Schema unificado de anúncio

Todas as tools devolvem anúncios com os mesmos campos básicos. Campos específicos por fonte são adicionais.

**Comum a OLX e ML:**

| Campo | Tipo | Descrição |
|---|---|---|
| `fonte` | string | `olx` \| `olx_jina` \| `ml` |
| `id` | int \| string | ID do anúncio |
| `titulo` | string | Título |
| `preco` | string | Preço formatado (`R$ X`) |
| `localizacao` | string \| null | Cidade/bairro/UF (pode ser null no ML) |
| `data` | string \| null | Data legível (null no ML — não exposta nos cards) |
| `url` | string | URL canônica do anúncio |
| `imagem` | string \| null | URL da imagem principal |

**Específicos da OLX:** `categoria`, `bairro`, `profissional`, `entrega_olx`, `propriedades`.
**Específicos do ML:** `frete`, `vendedor`, `atributos`.

**Envelope da resposta:** `fonte`, `total`, `pagina`, `por_pagina`, `url_busca`, `anuncios`, `avisos` (opcional).

## Campo `fonte` na resposta

Toda resposta inclui um campo `fonte` no envelope (e em cada anúncio) indicando a origem dos dados:

| Valor | Significado |
|---|---|
| `olx` | Scraping direto da OLX via httpx (caminho preferido) |
| `olx_jina` | Fallback: a OLX bloqueou e usamos [r.jina.ai](https://r.jina.ai) como proxy reader |
| `ml` | Scraping direto do Mercado Livre via UA Googlebot |

Verifique sempre `fonte` antes de tomar decisão crítica — payloads `olx_jina` vêm de markdown reduzido, com menos campos (sem `propriedades`, sem `entrega_olx`, sem timestamps precisos). Para desabilitar o fallback Jina, defina `MCP_BR_DISABLE_JINA=1` (ver abaixo).

## Variáveis de ambiente

Todos os parâmetros operacionais podem ser ajustados via env (com clamp seguro):

| Variável | Default | Faixa | Descrição |
|---|---|---|---|
| `MCP_BR_REQUEST_TIMEOUT` | `25.0` | 1.0–300.0 | Timeout HTTP em segundos |
| `MCP_BR_MAX_RETRIES` | `4` | 0–20 | Tentativas no fetcher OLX (retry + troca de perfil) |
| `MCP_BR_WARMUP_PROBABILITY` | `0.7` | 0.0–1.0 | Chance de warm-up da homepage antes do search |
| `MCP_BR_DISABLE_JINA` | `0` | `0`/`1` | Desabilita fallback via `r.jina.ai` |
| `MCP_BR_LOG_LEVEL` | `WARNING` | `DEBUG`/`INFO`/`WARNING`/`ERROR` | Nível do logger `mcp_brazil_marketplaces` |
| `MCP_BR_ML_USER_AGENT` | (Googlebot) | qualquer string | Sobrescreve UA usado no Mercado Livre. Use se o spoof de Googlebot for inaceitável — ML geralmente devolverá a página anti-bot e a tool retornará lista vazia. |
| `MCP_BR_RATE_LIMIT_CONCURRENCY` | `2` | 1–16 | Máx. de requests HTTP simultâneos |
| `MCP_BR_RATE_LIMIT_MIN_GAP` | `0.5` | 0.0–30.0 | Gap mínimo em segundos entre requests ao mesmo host. `0` desabilita |

## Privacidade e considerações

- **Fallback via `r.jina.ai`:** quando a OLX bloqueia requisições diretas, o servidor reenvia a URL pelo serviço público [r.jina.ai](https://r.jina.ai) para obter o conteúdo em markdown. Isso significa que **a Jina AI tem acesso ao log das URLs consultadas** durante o fallback. Para desabilitar:
  ```bash
  export MCP_BR_DISABLE_JINA=1
  ```
  Com a flag ativa, falhas de bypass retornam erro em vez de consultar terceiros. Toda resposta inclui o campo `fonte` (`olx`, `olx_jina`, `ml`) para que você saiba a origem dos dados.

- **Mercado Livre — Googlebot UA:** o scraper do ML usa `User-Agent: Googlebot/2.1` para contornar a página de challenge anti-bot. ML pode banir IPs que detectem o spoof; use moderadamente. Para desabilitar o spoof, defina `MCP_BR_ML_USER_AGENT` com um UA real (esperado: ML retornará challenge e a tool dará lista vazia).

- **Scraping de dados públicos:** este servidor consulta dados públicos da OLX e do Mercado Livre. Use com responsabilidade e respeite os termos de uso de cada site.

## Licença

MIT
