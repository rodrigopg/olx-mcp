# mcp-brazil-marketplaces

MCP server (FastMCP/stdio) que expõe tools para buscar anúncios em marketplaces brasileiros (OLX, Mercado Livre). Python 3.10+, publicado no PyPI.

## Carregar contexto antes de trabalhar

Leia o arquivo correspondente à área da tarefa antes de tocar código:

| Área | Arquivo |
|------|---------|
| Estrutura do `server.py`, padrões de tool, schema unificado, adicionar marketplace novo | `.claude/context/arquitetura.md` |
| Bypass de bloqueios OLX/ML, fallback Jina, perfis browser, micro-landing | `.claude/context/anti-bloqueio.md` |
| SSRF, ReDoS, OOM, coerce defensivo, correlation IDs, allowlists | `.claude/context/seguranca.md` |
| Pytest layout, async, monkeypatch env, ruff config, padrões de teste | `.claude/context/testes.md` |
| Trusted Publishing, branch protection, cortar release, GitHub Actions | `.claude/context/release.md` |
| Env vars `MCP_BR_*`, logging, rate limit, diagnóstico prod | `.claude/context/operacao.md` |

Se a tarefa tocar múltiplas áreas, leia os arquivos em paralelo.

## Regras inegociáveis

- **Toda mudança = issue + teste + lint verde.** Sem exceções, mesmo "ajustinho de 2 linhas". Issue antes do código, PR fecha via `Closes #N`.
- **Nunca push direto em `main`.** Branch + PR + CI matrix 3.10–3.13 verde + squash merge.
- **Nunca raise exceção para o caller MCP.** Tools sempre retornam `json.dumps(...)`; erros viram `{"erro": "..."}` com correlation ID via `_handle_http_error`.
- **Nunca aceitar URL externa sem `_validar_url_<m>`.** SSRF é bloqueador hard. Allowlist por hostname.
- **Nunca usar `.*?` em `re.DOTALL` sobre input externo.** Classes de caracteres explícitas (`[^<]+`) + cap de tamanho.
- **Nunca recriar regex em loop.** `re.compile` no module level.
- **Toda chamada HTTP via `_RateGate`.** Sem isso = queima de IP em loop LLM.
- **Toda resposta tem campo `fonte`** (`olx`, `olx_jina`, `ml`). LLM precisa saber a origem.
- **Não usar `selenium`/`playwright` em runtime do MCP.** Pesado demais para stdio.

## Checklist antes de abrir PR

- [ ] Issue GitHub aberta + número no commit (`Closes #N`)
- [ ] Branch nova (não trabalhar em `main`)
- [ ] Teste unitário em `tests/test_unit.py` cobrindo o caminho mudado
- [ ] `.venv/bin/ruff check .` — sem erros
- [ ] `.venv/bin/ruff format --check .` — sem reformatação pendente
- [ ] `.venv/bin/pytest -v` — todos verdes
- [ ] CHANGELOG.md atualizado (seção `[Unreleased]`)
- [ ] README/context files atualizados se feature adicionou env var ou tool
- [ ] PR aberto com descrição + Test plan + `Closes #N`
- [ ] CI matrix verde (3.10/3.11/3.12/3.13 + build)
- [ ] Squash merge + delete branch

## Comandos essenciais

```bash
# Setup dev
python -m venv .venv
.venv/bin/pip install -e ".[dev]"

# Loop dev
.venv/bin/ruff check . --fix
.venv/bin/ruff format .
.venv/bin/pytest -v

# Rodar server local (stdio)
.venv/bin/mcp-brazil-marketplaces

# Cortar release (v0.X.Y) — bump em pyproject.toml + __init__.py + CHANGELOG, depois:
git commit -am "release: v0.X.Y" && git tag v0.X.Y && git push --tags
# Workflow release.yml publica via Trusted Publishing automaticamente

# Integração (manual, depende de rede)
.venv/bin/python tests/integration/test_mcp_integration.py
```

## Roadmap

31 issues abertas em 5 milestones (`v0.4` Discoverability → `v1.0` Maturidade + `future`). Ver [ROADMAP.md](ROADMAP.md) ou [milestones do GitHub](https://github.com/rodrigopg/mcp-brazil-marketplaces/milestones).
