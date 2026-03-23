# CLAUDE.md — OLX MCP Server

## Project Overview

This is an **MCP (Model Context Protocol) server** that provides tools for searching and retrieving public listings from **OLX Brasil** (a major Brazilian online marketplace). It works by scraping the `__NEXT_DATA__` JSON embedded in OLX's Next.js pages.

**Language:** Python 3.10+
**Framework:** FastMCP (from the `mcp` package)
**Transport:** stdio (default MCP transport via `mcp.run()`)

---

## Repository Structure

```
olx-mcp/
├── server.py           # Entire application — all code lives here
├── requirements.txt    # Python dependencies
└── .venv/              # Virtualenv (created locally, not committed)
```

This is a single-file project. Do not create unnecessary new files.

---

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Running the Server

```bash
.venv/bin/python server.py
```

The server runs via stdio transport (default for MCP). To integrate with an MCP-compatible client (e.g., Claude Desktop), configure the client to spawn this process.

---

## Dependencies

```
mcp>=1.0.0           # Model Context Protocol SDK (provides FastMCP)
httpx[http2]>=0.27.0 # Async HTTP client — HTTP/2 is REQUIRED (OLX returns 403 on HTTP/1.1)
pydantic>=2.0.0      # Input validation via BaseModel
```

> **Critical:** Always install `httpx[http2]` (with the `[http2]` extra), not plain `httpx`. OLX enforces HTTP/2 and will return 403 otherwise.

---

## Architecture

### Code Sections (in order within `server.py`)

1. **Constants** (`BASE_URL`, `HEADERS`, `REQUEST_TIMEOUT`, `HTTP2`, `ESTADOS`)
   - Browser-mimicking headers are intentional and required for OLX to respond
   - `ESTADOS` is a set of valid Brazilian state abbreviations (lowercase, 2 chars)

2. **MCP instance** — `mcp = FastMCP("olx_mcp")`

3. **Input models** (Pydantic `BaseModel`)
   - `OrdenarPor`: Enum for sort order (`relevance`, `price`, `date`)
   - `BuscarAnunciosInput`: Parameters for search tool
   - `DetalheAnuncioInput`: URL parameter for detail tool

4. **Helper functions** (prefixed with `_`)
   - `_build_search_url()` — Constructs OLX search URL from params
   - `_extract_next_data()` — Parses `__NEXT_DATA__` JSON from HTML
   - `_format_timestamp()` — Unix timestamp → human-readable string
   - `_format_ad_summary()` — Normalizes a raw ad dict from search results
   - `_handle_http_error()` — Standardized error messages for HTTP/network failures

5. **MCP Tools** (decorated with `@mcp.tool(...)`)
   - `olx_buscar_anuncios` — Search listings
   - `olx_detalhe_anuncio` — Fetch full details of a single listing

6. **Entry point** — `if __name__ == "__main__": mcp.run()`

---

## MCP Tools

### `olx_buscar_anuncios`

Searches OLX Brasil for listings matching the given filters.

**Input (`BuscarAnunciosInput`):**
| Field | Type | Required | Description |
|---|---|---|---|
| `query` | str | Yes | Search term (1–200 chars) |
| `estado` | str | No | State abbreviation, e.g. `"sp"`, `"rj"` |
| `categoria` | str | No | OLX category slug, e.g. `"celulares"` |
| `preco_min` | int | No | Minimum price in BRL |
| `preco_max` | int | No | Maximum price in BRL |
| `ordenar` | OrdenarPor | No | Sort: `relevance`/`price`/`date` (default: `relevance`) |
| `pagina` | int | No | Page number 1–50 (default: 1) |

**Output:** JSON string with `total`, `pagina`, `por_pagina`, `url_busca`, and `anuncios` list.

### `olx_detalhe_anuncio`

Fetches complete details for a single listing by URL.

**Input (`DetalheAnuncioInput`):**
| Field | Type | Required | Description |
|---|---|---|---|
| `url` | str | Yes | Full OLX listing URL |

**Output:** JSON string with full listing data including description, seller, images, and category properties.

---

## Key Conventions

### Error Handling
- All tools return a JSON string. On error, the JSON is `{"erro": "...message..."}` — never raise exceptions to the MCP caller.
- `_handle_http_error()` centralizes error formatting for HTTP/network errors.
- Always use `ensure_ascii=False` in `json.dumps()` to preserve Portuguese characters.

### URL Construction
- Search URLs follow: `https://www.olx.com.br/estado-{uf}/{categoria}?q={query}&sf=1&o={page}&...`
- National searches (no state) use `/brasil/` instead of `/estado-{uf}/`
- Sort order is encoded as `sp=1` (price) or `sp=2` (date); relevance uses no `sp` param

### HTML Scraping Strategy
- Primary: extract `__NEXT_DATA__` JSON from a `<script id="__NEXT_DATA__">` tag
- Fallback (detail tool only): regex extraction of individual fields from raw HTML
- Image URLs are extracted via regex targeting `https://img.olx.com.br/images/...jpg`
- Deduplication of images uses `dict.fromkeys()` to preserve order

### Pydantic Models
- All models use `ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")`
- Input validation errors are caught and returned as `{"erro": ...}` JSON

### HTTP Client
- Always use `httpx.AsyncClient` (async) with `http2=True`, `follow_redirects=True`
- A single client instance is created per tool call (not shared/reused across calls)
- Timeout is `REQUEST_TIMEOUT = 20.0` seconds

---

## Adding New Features

### To add a new MCP tool:
1. Define a Pydantic input model (if needed) following the existing pattern
2. Implement an `async` function and decorate with `@mcp.tool(name="...", annotations={...})`
3. Return a JSON string (never raise exceptions)
4. Use `_handle_http_error()` in the `except` block for HTTP calls

### To add a new filter to search:
1. Add the field to `BuscarAnunciosInput` with appropriate `Field(...)` validation
2. Update `_build_search_url()` to include the new query parameter
3. Update the docstring in `olx_buscar_anuncios`

### Standard tool annotations to use:
```python
annotations={
    "readOnlyHint": True,       # This tool only reads data
    "destructiveHint": False,   # No destructive side effects
    "idempotentHint": True,     # Same input → same output
    "openWorldHint": True,      # Accesses external data (OLX)
}
```

---

## Testing

```bash
.venv/bin/python -m pytest test_mcp.py -v
```

When adding tests, use `pytest` with `pytest-asyncio` for async tool functions. Mock `httpx.AsyncClient` to avoid real HTTP calls.

---

## Known Limitations / Gotchas

- **OLX may block requests**: The scraping approach can break if OLX changes their HTML structure or rate-limits the server. The `__NEXT_DATA__` JSON structure is undocumented and may change.
- **403 without HTTP/2**: The `http2=True` flag is non-negotiable. Do not change this.
- **Detail tool uses dual strategy**: `__NEXT_DATA__` is tried first; if it fails, raw regex extraction is used as fallback. This makes the detail tool more resilient but also more fragile.
- **No authentication**: OLX is scraped as a public, unauthenticated visitor. Professional/paid listings may not be fully accessible.
- **Portuguese-only**: The server, field names, error messages, and docstrings are in Portuguese. Maintain this convention.

---

## Language & Style Conventions

- **All user-facing content is in Portuguese** (field names, error messages, tool descriptions, docstrings)
- Internal code (variable names, helper function names) uses English/Portuguese mix — follow existing patterns
- Use `async/await` for all I/O operations
- Keep the single-file structure; avoid splitting into modules unless the file grows significantly
- Section separators use `# ---` comment blocks as shown in the existing code
