# olx-mcp

MCP server para buscar anúncios públicos da [OLX Brasil](https://www.olx.com.br) e do [Mercado Livre Brasil](https://www.mercadolivre.com.br) — com bypass automático de bloqueios anti-bot (rotação de User-Agent, warm-up de cookies, retry com backoff, fallback via `r.jina.ai`, Googlebot UA para o Mercado Livre).

## Instalação rápida (zero clone, zero venv)

Use [`uv`](https://docs.astral.sh/uv/) — instale uma vez:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Depois rode direto:

```bash
uvx olx-mcp
```

Ou via `pip` tradicional:

```bash
pip install olx-mcp
olx-mcp
```

## Configuração no Claude Desktop

Cole o bloco abaixo em `claude_desktop_config.json`:

- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
- **Linux:** `~/.config/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "olx": {
      "command": "uvx",
      "args": ["olx-mcp"]
    }
  }
}
```

Se preferir `pip` em vez de `uvx`:

```json
{
  "mcpServers": {
    "olx": {
      "command": "olx-mcp"
    }
  }
}
```

Reinicie o Claude Desktop. As ferramentas `olx_buscar_anuncios`, `olx_detalhe_anuncio` e `ml_buscar_anuncios` ficam disponíveis.

## Configuração no Claude Code / Cursor / Continue

**Claude Code (CLI):**

```bash
claude mcp add olx -- uvx olx-mcp
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

### Diferenças entre as tools

| Aspecto | OLX | Mercado Livre |
|---|---|---|
| Páginas máx. | 50 | 20 |
| Ordenação | `relevance` \| `price` \| `date` | Não suportada (ML não aceita via URL pública) |
| Filtro `condicao` | N/A | Heurística pós-scraping no título |
| Filtro `estado` | Nativo na URL | Heurística pós-scraping (frequentemente vazio) |
| Detalhe de anúncio | `olx_detalhe_anuncio` | Não disponível ainda ([#13](https://github.com/rodrigopg/olx-mcp/issues/13)) |

Os limites de página diferem porque cada site retorna ~50 itens por página por padrão e a profundidade útil é menor no ML (resultados ficam ruins após a página 20).

## Exemplos de uso

> Busque iPhones usados em São Paulo por até R$ 2000, ordenados por menor preço.

> Procure Google Pixel 10 Pro XL na OLX e no Mercado Livre. Monte uma tabela comparativa.

> Me dê os detalhes do anúncio: https://sp.olx.com.br/...

## Desenvolvimento

```bash
git clone https://github.com/rodrigopg/olx-mcp
cd olx-mcp
python -m venv .venv
.venv/bin/pip install -e .
```

Rodar o servidor localmente:

```bash
.venv/bin/olx-mcp
# ou
.venv/bin/python -m olx_mcp
```

## Privacidade e considerações

- **Fallback via `r.jina.ai`:** quando a OLX bloqueia requisições diretas, o servidor reenvia a URL pelo serviço público [r.jina.ai](https://r.jina.ai) para obter o conteúdo em markdown. Isso significa que **a Jina AI tem acesso ao log das URLs consultadas** durante o fallback. Para desabilitar:
  ```bash
  export OLX_MCP_DISABLE_JINA=1
  ```
  Com a flag ativa, falhas de bypass retornam erro em vez de consultar terceiros. Toda resposta inclui o campo `fonte` (`olx`, `olx_jina`, `ml`) para que você saiba a origem dos dados.

- **Mercado Livre — Googlebot UA:** o scraper do ML usa `User-Agent: Googlebot/2.1` para contornar a página de challenge anti-bot. ML pode banir IPs que detectem o spoof; use moderadamente.

- **Scraping de dados públicos:** este servidor consulta dados públicos da OLX e do Mercado Livre. Use com responsabilidade e respeite os termos de uso de cada site.

## Licença

MIT
