# OLX MCP Server

MCP server que busca anúncios públicos da OLX Brasil via scraping do `__NEXT_DATA__`.

## Estrutura

```
server.py          # Servidor MCP (único arquivo de lógica)
requirements.txt   # Dependências Python
.venv/             # Virtualenv com dependências instaladas
```

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Executar

```bash
.venv/bin/python server.py
```

## Ferramentas expostas

| Ferramenta | Descrição |
|---|---|
| `olx_buscar_anuncios` | Busca com filtros: query, estado, categoria, preço, ordenação, página |
| `olx_detalhe_anuncio` | Detalhes completos de um anúncio pela URL |

## Gotchas

- **HTTP/2 é obrigatório**: a OLX retorna 403 em HTTP/1.1. O pacote `httpx[http2]` (com `h2`) é necessário — não substituir por `httpx` simples.
- **`__NEXT_DATA__`**: a extração depende do JSON embutido pela OLX via Next.js. Se a OLX mudar o layout, este é o primeiro ponto a verificar.
- O servidor usa `FastMCP` do pacote `mcp` — entrada point é `mcp.run()` no `__main__`.
