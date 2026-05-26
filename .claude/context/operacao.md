# Contexto: Operação

Tudo que afeta runtime: env vars, logging, rate limit, fallback.

## Variáveis de ambiente

Todos com prefixo `MCP_BR_*` (preferido) ou `OLX_MCP_*` (compat legado). Lidas via `_env(name)` ou `_env_float/_env_int` com clamp.

| Variável | Default | Range | Efeito |
|---|---|---|---|
| `MCP_BR_REQUEST_TIMEOUT` | 25.0 | 1.0–300.0 | Timeout HTTP segundos |
| `MCP_BR_MAX_RETRIES` | 4 | 0–20 | Tentativas no `_fetch_with_evasion` |
| `MCP_BR_WARMUP_PROBABILITY` | 0.7 | 0.0–1.0 | Chance warm-up homepage antes search |
| `MCP_BR_RATE_LIMIT_CONCURRENCY` | 2 | 1–16 | Semáforo global |
| `MCP_BR_RATE_LIMIT_MIN_GAP` | 0.5 | 0.0–30.0 | Gap mínimo por host (s). 0 = disabled |
| `MCP_BR_DISABLE_JINA` | 0 | 0/1 | Desabilita fallback `r.jina.ai` |
| `MCP_BR_LOG_LEVEL` | WARNING | DEBUG/INFO/WARNING/ERROR/CRITICAL | Logger `mcp_brazil_marketplaces` |
| `MCP_BR_ML_USER_AGENT` | (Googlebot) | qualquer string | Override UA do ML (perde bypass) |

Helpers internos:
```python
def _env(name, default=""):
    return os.getenv(f"MCP_BR_{name}", os.getenv(f"OLX_MCP_{name}", default))
```

## Logging

- Logger: `logging.getLogger("mcp_brazil_marketplaces")`
- Nível controlado por `MCP_BR_LOG_LEVEL` (chamada `logging.basicConfig` no module load se valor reconhecido)
- Eventos estruturados key=value:
  - `olx_search query=%r estado=%s categoria=%s pagina=%s`
  - `ml_search query=%r condicao=%s pagina=%s`
  - `ml_detail url=%s`
  - `jina_fallback url=%s`
  - `retry %s: status %s p/ %s` (debug)
- Erros: `logger.exception("<context> [%s]: %s", err_id, e)` com correlation ID

Para triagem prod:
```bash
MCP_BR_LOG_LEVEL=INFO mcp-brazil-marketplaces 2> mcp.log
grep jina_fallback mcp.log | wc -l  # quantas vezes Jina foi acionado
grep "id=" mcp.log  # correlacionar erros vistos pelo LLM
```

## Rate limit

Per-host throttle. Aplicado via `async with _RateGate(url):` antes de `httpx.AsyncClient`. Estado em:
- `_rate_semaphore` (asyncio.Semaphore) — limite global concorrência
- `_host_last_request` (dict[host, monotonic_time]) — gap mínimo
- `_host_lock` (asyncio.Lock) — exclusão mútua no check do gap

Defaults seguros p/ uso individual. Operadores agressivos podem aumentar:
```bash
MCP_BR_RATE_LIMIT_CONCURRENCY=4 MCP_BR_RATE_LIMIT_MIN_GAP=0.2 mcp-brazil-marketplaces
```

## Fallback chain OLX

1. `_fetch_with_evasion` — até `MAX_RETRIES` tentativas com perfis rotativos + warm-up
2. Se falha: `_fetch_via_jina` (a menos que `DISABLE_JINA=1`)
3. Se Jina falha também: retorna `{"erro": ...}` com texto humano

Marcado no campo `fonte` da resposta: `olx` (direto) ou `olx_jina` (proxy markdown).

## Fallback chain ML

1. `_fetch_with_retries` — até `MAX_RETRIES` tentativas com mesmo UA Googlebot
2. Se falha: retorna erro. **Não tenta Jina** (cookie wall).

## Comandos diagnóstico

```bash
# Smoke test direto sem MCP
python -c "
import asyncio
from mcp_brazil_marketplaces.server import olx_buscar_anuncios, BuscarAnunciosInput
print(asyncio.run(olx_buscar_anuncios(BuscarAnunciosInput(query='iphone', estado='sp')))[:500])
"

# Dry-run config
python -c "from mcp_brazil_marketplaces.server import REQUEST_TIMEOUT, MAX_RETRIES, RATE_LIMIT_MIN_GAP; print(REQUEST_TIMEOUT, MAX_RETRIES, RATE_LIMIT_MIN_GAP)"

# Verificar console_script instalado
which mcp-brazil-marketplaces
mcp-brazil-marketplaces < /dev/null  # nao roda; aguarda stdin MCP
```

## Erros operacionais comuns

- **`403 Forbidden`** em todas requests OLX: IP do operador foi listado. Esperar 1h ou trocar IP.
- **`Erro: acesso negado e fallback Jina desabilitado`**: ajustar `MCP_BR_DISABLE_JINA=0` ou aceitar a falha.
- **`Erro inesperado (id=XXX)`**: ver logs do servidor com mesmo ID; nunca aparece no LLM.
- **ML retorna sempre lista vazia**: micro-landing detectou. Verificar `MCP_BR_ML_USER_AGENT` não foi sobrescrito.
- **Latência alta repentina**: rate limit acumulou; verificar `MCP_BR_RATE_LIMIT_MIN_GAP`.
