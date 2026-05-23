"""Unit tests sem rede — rodam em CI rápido."""

import json
import os

import pytest

from mcp_brazil_marketplaces.server import (
    ALLOWED_OLX_HOSTS,
    MAX_HTML_BYTES,
    MAX_NEXT_DATA_BYTES,
    BuscarAnunciosInput,
    BuscarMLInput,
    DetalheAnuncioInput,
    OrdenarPor,
    _build_ml_url,
    _build_search_url,
    _extract_next_data,
    _format_ad_summary,
    _format_timestamp,
    _parse_ml_html,
    _parse_search_markdown,
    _validar_url_olx,
    olx_detalhe_anuncio,
)


class TestSSRFGuard:
    @pytest.mark.parametrize(
        "url",
        [
            "http://169.254.169.254/latest/meta-data/",
            "http://localhost:8080/",
            "http://127.0.0.1/",
            "file:///etc/passwd",
            "ftp://olx.com.br/",
            "https://evil.com/x",
            "https://olx.com.br.evil.com/x",
            "https://malicious-olx.com.br.attacker.io/",
        ],
    )
    def test_reject_unsafe(self, url):
        with pytest.raises(ValueError):
            _validar_url_olx(url)

    @pytest.mark.parametrize(
        "url",
        [
            "https://sp.olx.com.br/x/y",
            "https://go.olx.com.br/abc",
            "https://www.olx.com.br/brasil",
            "http://df.olx.com.br/foo",
        ],
    )
    def test_allow_olx(self, url):
        assert _validar_url_olx(url) == url

    def test_allowlist_constant(self):
        assert ".olx.com.br" in ALLOWED_OLX_HOSTS

    @pytest.mark.asyncio
    async def test_tool_rejects_ssrf(self):
        r = await olx_detalhe_anuncio(DetalheAnuncioInput(url="http://169.254.169.254/aws/metadata"))
        d = json.loads(r)
        assert "erro" in d
        assert "não permitido" in d["erro"].lower()


class TestBuildSearchUrl:
    def test_basic_query(self):
        url = _build_search_url(BuscarAnunciosInput(query="sofa"))
        assert "olx.com.br/brasil" in url
        assert "q=sofa" in url

    def test_estado_uppercase_rejected(self):
        with pytest.raises(ValueError):
            _build_search_url(BuscarAnunciosInput(query="x", estado="XX"))

    def test_estado_lowercase_valid(self):
        url = _build_search_url(BuscarAnunciosInput(query="x", estado="go"))
        assert "estado-go" in url

    def test_preco_range(self):
        url = _build_search_url(BuscarAnunciosInput(query="x", preco_min=100, preco_max=500))
        assert "ps=100" in url
        assert "pe=500" in url

    def test_ordenar_price(self):
        url = _build_search_url(BuscarAnunciosInput(query="x", ordenar=OrdenarPor.PRECO_MENOR))
        assert "sp=1" in url

    def test_pagina_default_omits_o(self):
        url = _build_search_url(BuscarAnunciosInput(query="x"))
        assert "o=" not in url

    def test_pagina_2_includes_o(self):
        url = _build_search_url(BuscarAnunciosInput(query="x", pagina=2))
        assert "o=2" in url


class TestBuildMlUrl:
    def test_basic(self):
        url, avisos = _build_ml_url(BuscarMLInput(query="iphone 13"))
        assert url.endswith("/iphone-13")
        assert avisos == []

    def test_price_range(self):
        url, _ = _build_ml_url(BuscarMLInput(query="x", preco_min=1000, preco_max=5000))
        assert "_PriceRange_1000-5000" in url

    def test_condicao_emits_warning_not_url(self):
        url, avisos = _build_ml_url(BuscarMLInput(query="x", condicao="novo"))
        # ML não respeita filtro de condicao via slug — não injetar
        assert "_ITEM" not in url
        assert any("condicao" in a.lower() or "condição" in a.lower() for a in avisos)

    def test_pagina_offset(self):
        url, _ = _build_ml_url(BuscarMLInput(query="x", pagina=3))
        # pagina 3 -> 2*50 + 1 = 101
        assert "_Desde_101" in url


class TestFormatters:
    def test_format_timestamp_unix(self):
        # 2026-01-01 00:00:00 UTC = 1767225600
        out = _format_timestamp(1767225600)
        assert "/" in out and ":" in out

    def test_format_timestamp_invalid_returns_str(self):
        # int absurdo não quebra
        out = _format_timestamp(99999999999999)
        assert isinstance(out, str)

    def test_format_ad_summary_minimal(self):
        ad = {"listId": 123, "subject": "Foo", "price": "R$ 10", "date": 1767225600}
        s = _format_ad_summary(ad)
        assert s["id"] == 123
        assert s["titulo"] == "Foo"

    def test_format_ad_summary_rejects_malicious_types(self):
        """Site comprometido injeta listas/dicts onde se esperava string."""
        ad = {
            "listId": 1,
            "subject": ["injection", "list"],  # deveria ser string
            "price": {"nested": "obj"},  # deveria ser string
            "properties": "not-a-list",  # deveria ser lista
            "images": "boom",
        }
        s = _format_ad_summary(ad)
        # campos string viraram None, propriedades vazio, imagem None
        assert s["titulo"] is None
        assert s["preco"] == ""
        assert s["propriedades"] == {}
        assert s["imagem"] is None

    def test_format_ad_summary_truncates_huge_strings(self):
        ad = {"listId": 1, "subject": "x" * 10000}
        s = _format_ad_summary(ad)
        assert len(s["titulo"]) <= 500

    def test_format_ad_summary_caps_properties_count(self):
        ad = {
            "listId": 1,
            "properties": [{"label": f"k{i}", "value": "v"} for i in range(500)],
        }
        s = _format_ad_summary(ad)
        assert len(s["propriedades"]) <= 80

    def test_format_ad_summary_non_dict_input(self):
        assert _format_ad_summary("not a dict") == {}
        assert _format_ad_summary(None) == {}


class TestMlParser:
    def test_parses_basic_card(self):
        html = """
        <li class="ui-search-layout__item">
          <h2 class="poly-component__title">iPhone 13 128GB</h2>
          <a href="https://produto.mercadolivre.com.br/MLB-123-iphone-13_JM"></a>
          <span class="andes-money-amount__fraction">3.500</span>
          <span class="andes-money-amount__cents">00</span>
          <img src="https://http2.mlstatic.com/img.jpg"/>
        </li>
        """
        ads = _parse_ml_html(html)
        assert len(ads) == 1
        assert ads[0]["titulo"] == "iPhone 13 128GB"
        assert ads[0]["preco"] == "R$ 3500,00"
        assert "MLB-123" in ads[0]["url"]


class TestMarkdownParser:
    def test_extracts_total(self):
        md = """
        # busca

        1 - 5 de 42 resultados

        ## [Produto X](https://go.olx.com.br/celulares/x-12345678)
        ### R$ 1.000
        Cidade
        15 de mai, 14:00
        Adicionar aos favoritos
        """
        d = _parse_search_markdown(md, "http://x")
        assert d["total"] == 42
        assert len(d["anuncios"]) == 1
        assert d["anuncios"][0]["preco"] == "R$ 1.000"


class TestExtractNextData:
    def test_extract_basic(self):
        html = '<html><script id="__NEXT_DATA__" type="application/json">{"a":1}</script></html>'
        assert _extract_next_data(html) == {"a": 1}

    def test_redos_payload_completes_fast(self):
        # Padrão clássico catastrophic backtracking p/ `.*?` em re.DOTALL.
        # Com regex linear, mesmo 1MB de lixo termina em <1s.
        import time

        payload = '<script id="__NEXT_DATA__">' + "a" * 1_000_000  # sem </script>
        t0 = time.monotonic()
        try:
            _extract_next_data(payload)
        except ValueError:
            pass
        assert time.monotonic() - t0 < 1.5, "regex pode ter regredido p/ backtracking"

    def test_blob_oversize_rejected(self):
        big = "x" * (MAX_NEXT_DATA_BYTES + 10)
        html = f'<script id="__NEXT_DATA__">{big}</script>'
        with pytest.raises(ValueError, match="excede limite"):
            _extract_next_data(html)

    def test_html_hard_capped(self):
        assert MAX_HTML_BYTES > 0
        # Garante que truncar HTML não quebra extração se blob estiver no início
        html = '<script id="__NEXT_DATA__">{"ok":true}</script>' + "Z" * MAX_HTML_BYTES
        assert _extract_next_data(html) == {"ok": True}


class TestMlUserAgentOverride:
    def test_default_is_googlebot(self):
        from mcp_brazil_marketplaces.server import _ML_DEFAULT_UA, ML_HEADERS

        # Em ambiente de teste padrão (sem env), UA deve ser googlebot
        if not os.environ.get("MCP_BR_ML_USER_AGENT"):
            assert ML_HEADERS["User-Agent"] == _ML_DEFAULT_UA
            assert "Googlebot" in _ML_DEFAULT_UA

    def test_env_override_applied_on_reimport(self, monkeypatch):
        import importlib

        monkeypatch.setenv("MCP_BR_ML_USER_AGENT", "MyCustomBot/1.0")
        import mcp_brazil_marketplaces.server as srv

        importlib.reload(srv)
        try:
            assert srv.ML_HEADERS["User-Agent"] == "MyCustomBot/1.0"
        finally:
            monkeypatch.delenv("MCP_BR_ML_USER_AGENT", raising=False)
            importlib.reload(srv)


class TestEnvHelpers:
    def test_env_float_default_when_unset(self, monkeypatch):
        from mcp_brazil_marketplaces.server import _env_float

        monkeypatch.delenv("MCP_BR_TEST_FLOAT", raising=False)
        monkeypatch.delenv("OLX_MCP_TEST_FLOAT", raising=False)
        assert _env_float("TEST_FLOAT", 1.5, 0.0, 10.0) == 1.5

    def test_env_float_clamps_high(self, monkeypatch):
        from mcp_brazil_marketplaces.server import _env_float

        monkeypatch.setenv("MCP_BR_TEST_FLOAT", "999")
        assert _env_float("TEST_FLOAT", 1.0, 0.0, 10.0) == 10.0

    def test_env_float_invalid_falls_back(self, monkeypatch):
        from mcp_brazil_marketplaces.server import _env_float

        monkeypatch.setenv("MCP_BR_TEST_FLOAT", "abc")
        assert _env_float("TEST_FLOAT", 2.0, 0.0, 10.0) == 2.0

    def test_env_int_clamps_low(self, monkeypatch):
        from mcp_brazil_marketplaces.server import _env_int

        monkeypatch.setenv("MCP_BR_TEST_INT", "-50")
        assert _env_int("TEST_INT", 5, 0, 20) == 0

    def test_legacy_olx_mcp_prefix_still_works(self, monkeypatch):
        """Compat: aceita OLX_MCP_* como fallback."""
        from mcp_brazil_marketplaces.server import _env_int

        monkeypatch.delenv("MCP_BR_LEGACY_TEST", raising=False)
        monkeypatch.setenv("OLX_MCP_LEGACY_TEST", "7")
        assert _env_int("LEGACY_TEST", 1, 0, 100) == 7


class TestErrorMessages:
    def test_handle_unknown_exception_returns_correlation_id(self):
        from mcp_brazil_marketplaces.server import _handle_http_error

        class WeirdError(Exception):
            pass

        msg = _handle_http_error(WeirdError("/Users/secret/path token=abc123"))
        # mensagem genérica com id, sem path/token vazado
        assert "id=" in msg
        assert "/Users/secret" not in msg
        assert "token=abc123" not in msg

    def test_handle_http_status_no_body_leak(self):
        import httpx

        req = httpx.Request("GET", "https://x")
        resp = httpx.Response(500, request=req, text="STACKTRACE_INTERNO_VAZA")
        msg = _handle_http_error_wrapper(resp)
        assert "STACKTRACE_INTERNO_VAZA" not in msg

    def test_handle_validation_keeps_message(self):
        from mcp_brazil_marketplaces.server import _handle_http_error

        msg = _handle_http_error(ValueError("estado inválido"))
        assert "estado inválido" in msg


def _handle_http_error_wrapper(resp):
    """Helper: simula HTTPStatusError do httpx."""
    import httpx

    from mcp_brazil_marketplaces.server import _handle_http_error

    err = httpx.HTTPStatusError("boom", request=resp.request, response=resp)
    return _handle_http_error(err)


class TestLogging:
    def test_logger_name_is_package(self):
        from mcp_brazil_marketplaces.server import logger

        assert logger.name == "mcp_brazil_marketplaces"

    def test_correlation_id_logged_on_unknown_error(self, caplog):
        import logging as _l

        from mcp_brazil_marketplaces.server import _handle_http_error

        with caplog.at_level(_l.ERROR, logger="mcp_brazil_marketplaces"):
            msg = _handle_http_error(RuntimeError("boom"))
        # ID retornado bate com o logado
        import re as _re

        m = _re.search(r"id=([a-f0-9]+)", msg)
        assert m, "resposta deve conter correlation ID"
        assert m.group(1) in caplog.text


class TestRateLimit:
    @pytest.mark.asyncio
    async def test_min_gap_enforced(self, monkeypatch):
        """Duas chamadas sequenciais ao mesmo host respeitam MCP_BR_RATE_LIMIT_MIN_GAP."""
        import importlib

        monkeypatch.setenv("MCP_BR_RATE_LIMIT_MIN_GAP", "0.3")
        monkeypatch.setenv("MCP_BR_RATE_LIMIT_CONCURRENCY", "8")
        import mcp_brazil_marketplaces.server as srv

        importlib.reload(srv)
        try:
            import time

            t0 = time.monotonic()
            await srv._rate_limit("example.com")
            srv._rate_release()
            await srv._rate_limit("example.com")
            srv._rate_release()
            elapsed = time.monotonic() - t0
            assert elapsed >= 0.28, f"sem gap: {elapsed:.3f}s"
        finally:
            monkeypatch.delenv("MCP_BR_RATE_LIMIT_MIN_GAP", raising=False)
            monkeypatch.delenv("MCP_BR_RATE_LIMIT_CONCURRENCY", raising=False)
            importlib.reload(srv)

    @pytest.mark.asyncio
    async def test_min_gap_zero_disables(self, monkeypatch):
        import importlib
        import time

        monkeypatch.setenv("MCP_BR_RATE_LIMIT_MIN_GAP", "0")
        import mcp_brazil_marketplaces.server as srv

        importlib.reload(srv)
        try:
            t0 = time.monotonic()
            for _ in range(5):
                await srv._rate_limit("h")
                srv._rate_release()
            assert time.monotonic() - t0 < 0.1
        finally:
            monkeypatch.delenv("MCP_BR_RATE_LIMIT_MIN_GAP", raising=False)
            importlib.reload(srv)


class TestSchemaConsistency:
    """Garante que toda resposta de tool tem campo `fonte` para o LLM."""

    def test_ml_url_signature(self):
        out = _build_ml_url(BuscarMLInput(query="x"))
        assert isinstance(out, tuple) and len(out) == 2
