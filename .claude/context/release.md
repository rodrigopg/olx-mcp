# Contexto: Release

## PyPI Trusted Publishing (OIDC)

Configurado — **não usar token API**. Workflow `release.yml` publica em tag `v*` via OIDC do GitHub Actions.

**Estado:**
- PyPI Trusted Publisher: owner `rodrigopg`, repo `mcp-brazil-marketplaces`, workflow `release.yml`, environment `pypi`
- GitHub environment `pypi` criado, restrito a tags `v*`
- Token API antigo revogado

## Cortar release

```bash
# 1. Bump versão em DOIS lugares (devem bater)
sed -i '' 's/version = "0.3.0"/version = "0.4.0"/' pyproject.toml
sed -i '' 's/__version__ = "0.3.0"/__version__ = "0.4.0"/' mcp_brazil_marketplaces/__init__.py

# 2. Atualizar CHANGELOG.md (mover Unreleased → versão nova, abrir Unreleased)

# 3. Commit + tag + push
git add pyproject.toml mcp_brazil_marketplaces/__init__.py CHANGELOG.md
git commit -m "release: v0.4.0"
git tag v0.4.0
git push && git push --tags
```

Workflow `release.yml` builda, valida `tag == pyproject.version`, publica via OIDC. GitHub Release **não é criado automaticamente** — fazer manualmente com `gh release create`:

```bash
gh release create v0.4.0 --title "v0.4.0" --notes "$(awk '/^## \[0.4.0\]/{flag=1; next} /^## \[/{flag=0} flag' CHANGELOG.md)" dist/*.whl dist/*.tar.gz
```

## Branch protection

`main` protegida via API nativa (repo público + free plan funciona):
- 5 required status checks: `test (3.10)`, `test (3.11)`, `test (3.12)`, `test (3.13)`, `build`
- PR obrigatório (mesmo com 0 approvals)
- `enforce_admins: true` — admin também precisa de PR
- `allow_force_pushes: false`, `allow_deletions: false`

Owner não consegue push direto. Único caminho: branch + PR + CI verde + squash merge.

## CI workflow `ci.yml`

Roda em push/PR para `main`:
- Matrix Py 3.10–3.13: install `[dev]`, `ruff check`, `ruff format --check`, `pytest -v`
- Job `build`: depende de `test`, builda wheel + sdist, smoke install

## Workflow `release.yml`

Dispara em `push: tags: ["v*"]`. Jobs:
- `build`: checkout, build wheel/sdist, valida `tag.lstrip('v') == pyproject_version`, upload artifact
- `publish`: needs `build`, environment `pypi`, `id-token: write`, baixa artifact, usa `pypa/gh-action-pypi-publish@release/v1`

## Restrições da branch protection p/ workflow files

`GITHUB_TOKEN` não tem `workflows: write`. Modificar `.github/workflows/*.yml` por workflow falha. Solução: editar via PR humano.

## Comandos GitHub úteis

```bash
# Ver state branch protection
gh api repos/rodrigopg/mcp-brazil-marketplaces/branches/main/protection | jq

# Listar milestones
gh api repos/rodrigopg/mcp-brazil-marketplaces/milestones | jq '.[].title'

# Atribuir milestone via número
gh api repos/.../issues/N --method PATCH -F milestone=M

# Wait CI até completar
until gh pr checks N 2>&1 | grep -qE "(pass|fail)" && ! gh pr checks N | grep -q pending; do sleep 15; done
```

## Tokens KeePass

- Database `pessoal` (`~/My Drive/Personal/Chaves/Personal.kdbx`)
- Entry: `/Tokens/PyPI - mcp-brazil-marketplaces` — agora **obsoleto** (token revogado, Trusted Publishing ativo). Pode deletar.
- Login pypi.org: `/Passwords/pypi.org (rodrigopg)` — username `rodrigopg`

## 2FA PyPI

TOTP obrigatório no painel web. **Não está salvo no KeePass**. App autenticador no celular. Recovery codes — verificar se foram guardados.
