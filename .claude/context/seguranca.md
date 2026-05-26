# Contexto: Segurança

Pacote PyPI público + MCP que aceita URLs de LLM. Surface de ataque real.

## SSRF — bloqueador hard

**Toda tool que aceita URL** valida via `_validar_url_<m>` antes de qualquer request:

```python
def _validar_url_olx(url: str) -> str:
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        raise ValueError(...)
    host = (p.hostname or "").lower()
    if not any(host == h.lstrip(".") or host.endswith(h) for h in ALLOWED_OLX_HOSTS):
        raise ValueError(...)
    return url
```

Rejeita: `169.254.169.254` (cloud metadata), `localhost`, `127.0.0.1`, `file://`, `ftp://`, `https://olx.com.br.evil.com/`.
Aceita: `https://sp.olx.com.br/...`, `https://www.olx.com.br/...`.

**Allowlists atuais:**
- OLX: `(".olx.com.br",)`
- ML: `(".mercadolivre.com.br", ".mercadolibre.com")`

## ReDoS — regex linear

`re.search(r'.*?', html, re.DOTALL)` é catastrophic backtracking em adversarial input. Substituir por classes de caracteres explícitas:

```python
# RUIM (vulnerável)
re.search(r'id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)

# BOM (linear, 1000x mais rápido em payload malicioso)
_NEXT_DATA_RE = re.compile(r'id="__NEXT_DATA__"[^>]{0,500}>([^<]+)</script>')
```

Bound também o input: `MAX_HTML_BYTES = 8 * 1024 * 1024`.

## OOM — cap de blob

`json.loads(blob)` em payload arbitrário pode alocar 100MB+. Limitar:

```python
if len(blob) > MAX_NEXT_DATA_BYTES:  # 5MB
    raise ValueError(...)
return json.loads(blob)
```

## Coerce defensivo do payload externo

Site comprometido pode injetar tipos errados no JSON. Toda extração passa por `_safe_*`:

```python
def _safe_str(v, max_len=500) -> str | None:
    if isinstance(v, str): return v[:max_len]
    if isinstance(v, (int, float, bool)): return str(v)[:max_len]
    return None  # dict/list = descarta
```

Aplicado em `_format_ad_summary`: limita strings, dicts, list count.

## Correlation IDs em erros

Nunca expor `type(e).__name__: {e}` ao caller — pode vazar paths, tokens. Padrão:

```python
err_id = uuid.uuid4().hex[:8]
logger.exception("Falha [%s]: %s", err_id, e)
return json.dumps({"erro": f"Falha (id={err_id})."})
```

## Rate limit interno

LLM em loop queima IP em segundos. `_RateGate` envolve toda chamada HTTP:
- `_rate_semaphore = asyncio.Semaphore(2)` (default)
- Gap mínimo por host: 0.5s (default)
- Configurável via `MCP_BR_RATE_LIMIT_*`

## Privacidade

- Fallback Jina envia URL completa para terceiro — documentado no README. Opt-out: `MCP_BR_DISABLE_JINA=1`.
- Googlebot UA spoofing é ético/legal cinza — opt-out: `MCP_BR_ML_USER_AGENT=<real-ua>`.

## SECURITY.md

Canal report: GitHub Security Advisories (preferencial) ou email. Fora de escopo: bloqueio OLX/ML, dados desatualizados.

## Dependências

Upper-bound fechado: `mcp<2.0`, `httpx[http2]<1.0`, `pydantic<3.0`. Sem isso, major bump quebra silenciosamente em `pip install`.
