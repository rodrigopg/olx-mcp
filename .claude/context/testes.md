# Contexto: Testes

## Estrutura

- `tests/test_unit.py` — unit tests, **sem rede**. Rodam em CI matrix Python 3.10–3.13 em ~0.8s.
- `tests/integration/test_mcp_integration.py` — integração real OLX/ML/Jina. Ignorado pelo CI (`addopts = "--ignore=tests/integration"`). Rodar manual.
- `tests/__init__.py` vazio (pacote).

## Config pyproject.toml

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
addopts = "--ignore=tests/integration"
markers = ["integration: testes que dependem de rede"]
```

`asyncio_mode = "auto"` dispensa `@pytest.mark.asyncio` em testes async.

## Padrões usados

### SSRF guard (parametrize)

```python
@pytest.mark.parametrize("url", [
    "http://169.254.169.254/...",
    "https://olx.com.br.evil.com/x",
])
def test_reject_unsafe(self, url):
    with pytest.raises(ValueError):
        _validar_url_olx(url)
```

### Async tool test

```python
@pytest.mark.asyncio
async def test_tool_rejects_ssrf(self):
    r = await olx_detalhe_anuncio(DetalheAnuncioInput(url="..."))
    d = json.loads(r)
    assert "erro" in d
```

### Env via monkeypatch + reload

Env é lido no module load. Para testar override, `monkeypatch.setenv` + `importlib.reload(srv)`:

```python
def test_env_override(self, monkeypatch):
    import importlib
    monkeypatch.setenv("MCP_BR_TEST_FLOAT", "999")
    import mcp_brazil_marketplaces.server as srv
    importlib.reload(srv)
    try:
        assert srv._env_float("TEST_FLOAT", 1.0, 0.0, 10.0) == 10.0
    finally:
        importlib.reload(srv)  # cleanup
```

### Rate limit (timing)

```python
@pytest.mark.asyncio
async def test_min_gap_enforced(self, monkeypatch):
    import importlib, time
    monkeypatch.setenv("MCP_BR_RATE_LIMIT_MIN_GAP", "0.3")
    import mcp_brazil_marketplaces.server as srv
    importlib.reload(srv)
    t0 = time.monotonic()
    await srv._rate_limit("h"); srv._rate_release()
    await srv._rate_limit("h"); srv._rate_release()
    assert time.monotonic() - t0 >= 0.28
```

### Coerce defensivo

Cobertura mínima nova feature:
- input válido
- input vazio
- input tipo errado (lista onde se espera string)
- input gigante (truncamento)

### ReDoS

Padrão: gerar payload grande, medir tempo:

```python
import time
payload = '<script id="__NEXT_DATA__">' + "a" * 1_000_000
t0 = time.monotonic()
try: _extract_next_data(payload)
except ValueError: pass
assert time.monotonic() - t0 < 1.5
```

## Lint

`ruff check` + `ruff format --check` no CI. Config:

```toml
[tool.ruff]
line-length = 110
target-version = "py310"
[tool.ruff.lint]
select = ["E", "F", "W", "I", "B", "UP"]
ignore = ["E501"]
[tool.ruff.lint.per-file-ignores]
"tests/*" = ["B", "E731"]
```

`B` desligado em testes porque expressões `pytest.raises` quebram B018.

## Workflow obrigatório

**Toda mudança** (CLAUDE.md):
1. Issue GitHub antes (`gh issue create`)
2. Branch nova
3. Implementar + teste unitário cobrindo
4. `ruff check + format + pytest` verde
5. PR com `Closes #N`
6. Aguardar CI matrix verde
7. Squash merge + delete branch

## Erros conhecidos

- `ruff format` reformata `tests/test_unit.py` se classes vierem com `parametrize` em uma linha — aceitar.
- `B007` em loops de teste — adicionar `# noqa: B007` ou desligar.
- Reload de módulo entre testes pode vazar estado se env permanecer setado — sempre cleanup no `finally`.
