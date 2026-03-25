# olx-mcp

MCP server para buscar anúncios públicos da [OLX Brasil](https://www.olx.com.br) via scraping do `__NEXT_DATA__`.

## Instalação

```bash
pip install olx-mcp
```

## Configuração no Claude Desktop

Edite `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "olx-mcp": {
      "command": "olx-mcp"
    }
  }
}
```

## Configuração no Claude Code

```bash
claude mcp add olx-mcp olx-mcp
```

## Ferramentas

### `olx_buscar_anuncios`

Busca anúncios com filtros.

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

Retorna detalhes completos de um anúncio pela URL.

| Parâmetro | Tipo | Obrigatório | Descrição |
|---|---|---|---|
| `url` | string | Sim | URL completa do anúncio na OLX |

## Exemplos de uso

> Busque notebooks usados em São Paulo por até R$ 2000, ordenados por menor preço.

> Me dê os detalhes do anúncio: https://sp.olx.com.br/...

## Desenvolvimento

```bash
git clone https://github.com/rodrigopg/olx-mcp
cd olx-mcp
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

## Aviso

Este servidor faz scraping de dados públicos da OLX Brasil. Use com responsabilidade e respeite os termos de uso do site.

## Licença

MIT
