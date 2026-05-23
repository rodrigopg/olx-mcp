"""
MCP server para marketplaces brasileiros.

Tools disponíveis:
- olx_buscar_anuncios / olx_detalhe_anuncio — OLX Brasil (scraping __NEXT_DATA__)
- ml_buscar_anuncios — Mercado Livre Brasil (scraping HTML via UA Googlebot)
"""

import asyncio
import json
import logging
import os
import random
import re
import uuid
from datetime import datetime
from enum import Enum
from urllib.parse import urlparse

import httpx
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger("mcp_brazil_marketplaces")


def _env(name: str, default: str = "") -> str:
    """Lê env preferindo MCP_BR_<NAME>, com fallback ao legado OLX_MCP_<NAME>."""
    return os.getenv(f"MCP_BR_{name}", os.getenv(f"OLX_MCP_{name}", default))


_log_level = _env("LOG_LEVEL", "WARNING").upper()
if _log_level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
    logging.basicConfig(level=_log_level)
    logger.setLevel(_log_level)

# ---------------------------------------------------------------------------
# Feature flags via env
# ---------------------------------------------------------------------------

DISABLE_JINA = _env("DISABLE_JINA", "0").lower() in ("1", "true", "yes")
ALLOWED_OLX_HOSTS = (".olx.com.br",)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

BASE_URL = "https://www.olx.com.br"

# Pool de perfis de navegador para rotação anti-bloqueio
BROWSER_PROFILES = [
    {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "Sec-Ch-Ua-Platform": '"macOS"',
        "Sec-Ch-Ua-Mobile": "?0",
    },
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "Sec-Ch-Ua": '"Google Chrome";v="130", "Chromium";v="130", "Not?A_Brand";v="99"',
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Ch-Ua-Mobile": "?0",
    },
    {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
        "Sec-Ch-Ua": '"Chromium";v="129", "Not=A?Brand";v="8", "Google Chrome";v="129"',
        "Sec-Ch-Ua-Platform": '"Linux"',
        "Sec-Ch-Ua-Mobile": "?0",
    },
    {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:131.0) Gecko/20100101 Firefox/131.0",
        "Sec-Ch-Ua": None,
        "Sec-Ch-Ua-Platform": None,
        "Sec-Ch-Ua-Mobile": None,
    },
    {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Mobile/15E148 Safari/604.1",
        "Sec-Ch-Ua": None,
        "Sec-Ch-Ua-Platform": None,
        "Sec-Ch-Ua-Mobile": None,
    },
]


def _build_headers(
    profile: dict, referer: str = "https://www.google.com/", same_origin: bool = False
) -> dict:
    """Monta headers realistas a partir de um perfil de browser."""
    h = {
        "User-Agent": profile["User-Agent"],
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Referer": referer,
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin" if same_origin else "cross-site",
        "Sec-Fetch-User": "?1",
        "DNT": "1",
        "Priority": "u=0, i",
    }
    if profile.get("Sec-Ch-Ua"):
        h["Sec-Ch-Ua"] = profile["Sec-Ch-Ua"]
        h["Sec-Ch-Ua-Platform"] = profile["Sec-Ch-Ua-Platform"]
        h["Sec-Ch-Ua-Mobile"] = profile["Sec-Ch-Ua-Mobile"]
    return h


def _env_float(name: str, default: float, lo: float, hi: float) -> float:
    """Lê env (MCP_BR_<NAME>, fallback OLX_MCP_<NAME>) como float com clamp."""
    raw = _env(name)
    if not raw:
        return default
    try:
        v = float(raw)
    except ValueError:
        logger.warning("Env %s inválido (%r), usando default %s", name, raw, default)
        return default
    return max(lo, min(hi, v))


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    raw = _env(name)
    if not raw:
        return default
    try:
        v = int(raw)
    except ValueError:
        logger.warning("Env %s inválido (%r), usando default %s", name, raw, default)
        return default
    return max(lo, min(hi, v))


REQUEST_TIMEOUT = _env_float("REQUEST_TIMEOUT", 25.0, 1.0, 300.0)
HTTP2 = True  # Obrigatório: OLX retorna 403 em HTTP/1.1
MAX_RETRIES = _env_int("MAX_RETRIES", 4, 0, 20)
WARMUP_PROBABILITY = _env_float("WARMUP_PROBABILITY", 0.7, 0.0, 1.0)

# Rate limit por host: protege IP do operador contra loops do LLM que
# fariam queimar o IP em <30s. Semáforo limita concorrência;
# `_HOST_MIN_GAP` impõe atraso mínimo entre chamadas ao mesmo host.
RATE_LIMIT_CONCURRENCY = _env_int("RATE_LIMIT_CONCURRENCY", 2, 1, 16)
RATE_LIMIT_MIN_GAP = _env_float("RATE_LIMIT_MIN_GAP", 0.5, 0.0, 30.0)

_rate_semaphore = asyncio.Semaphore(RATE_LIMIT_CONCURRENCY)
_host_last_request: dict[str, float] = {}
_host_lock = asyncio.Lock()


async def _rate_limit(host: str) -> None:
    """Adquire slot global + força gap mínimo por host.

    Não é contador de janela deslizante — é um throttle simples
    suficiente para evitar que um LLM em loop dispare 100 requests/s.
    Operadores podem desabilitar via MCP_BR_RATE_LIMIT_MIN_GAP=0.
    """
    await _rate_semaphore.acquire()
    if RATE_LIMIT_MIN_GAP <= 0:
        return
    async with _host_lock:
        now = asyncio.get_event_loop().time()
        prev = _host_last_request.get(host, 0.0)
        wait = RATE_LIMIT_MIN_GAP - (now - prev)
        if wait > 0:
            await asyncio.sleep(wait)
        _host_last_request[host] = asyncio.get_event_loop().time()


def _rate_release() -> None:
    _rate_semaphore.release()


class _RateGate:
    """Context manager async: rate-limit por host com release garantido."""

    def __init__(self, url: str) -> None:
        self.host = _host_of(url)

    async def __aenter__(self) -> "_RateGate":
        await _rate_limit(self.host)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        _rate_release()


def _host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


ESTADOS = {
    "ac",
    "al",
    "ap",
    "am",
    "ba",
    "ce",
    "df",
    "es",
    "go",
    "ma",
    "mt",
    "ms",
    "mg",
    "pa",
    "pb",
    "pr",
    "pe",
    "pi",
    "rj",
    "rn",
    "rs",
    "ro",
    "rr",
    "sc",
    "sp",
    "se",
    "to",
}

# ---------------------------------------------------------------------------
# Inicialização
# ---------------------------------------------------------------------------

mcp = FastMCP("mcp_brazil_marketplaces")


# ---------------------------------------------------------------------------
# Modelos de entrada
# ---------------------------------------------------------------------------


class OrdenarPor(str, Enum):
    RELEVANCIA = "relevance"
    PRECO_MENOR = "price"
    MAIS_RECENTE = "date"


class BuscarAnunciosInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")

    query: str = Field(
        ...,
        description="Termo de busca. Ex: 'notebook', 'iphone 13', 'sofá 3 lugares'",
        min_length=1,
        max_length=200,
    )
    estado: str | None = Field(
        default=None,
        description=(
            "Sigla do estado brasileiro em minúsculas. Ex: 'sp', 'go', 'rj'. "
            "Se omitido, busca em todo o Brasil."
        ),
        min_length=2,
        max_length=2,
    )
    categoria: str | None = Field(
        default=None,
        description=(
            "Slug de categoria da OLX. Ex: 'informatica-e-acessorios', 'celulares', "
            "'imoveis', 'veiculos-e-pecas', 'eletrodomesticos'. "
            "Se omitido, busca em todas as categorias."
        ),
    )
    preco_min: int | None = Field(
        default=None,
        description="Preço mínimo em reais (inteiro). Ex: 500",
        ge=0,
    )
    preco_max: int | None = Field(
        default=None,
        description="Preço máximo em reais (inteiro). Ex: 3000",
        ge=0,
    )
    ordenar: OrdenarPor = Field(
        default=OrdenarPor.RELEVANCIA,
        description="Ordenação: 'relevance' (relevância), 'price' (menor preço), 'date' (mais recente)",
    )
    pagina: int = Field(
        default=1,
        description="Página de resultados (começa em 1)",
        ge=1,
        le=50,
    )


class DetalheAnuncioInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")

    url: str = Field(
        ...,
        description=(
            "URL completa do anúncio na OLX. "
            "Ex: 'https://sp.olx.com.br/sao-paulo-e-regiao/informatica/notebooks/notebook-abc-1234567890'"
        ),
        min_length=20,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validar_url_olx(url: str) -> str:
    """Garante que a URL é http(s) e aponta para um host *.olx.com.br.

    Previne SSRF — sem isso, um caller poderia forçar requests para
    169.254.169.254 (metadata cloud), localhost, ou hosts internos.
    """
    try:
        p = urlparse(url)
    except Exception as e:
        raise ValueError(f"URL inválida: {e}") from None

    if p.scheme not in ("http", "https"):
        raise ValueError(f"Esquema não permitido: {p.scheme!r}. Use http(s).")
    host = (p.hostname or "").lower()
    if not host:
        raise ValueError("URL sem hostname.")
    if not any(host == h.lstrip(".") or host.endswith(h) for h in ALLOWED_OLX_HOSTS):
        raise ValueError(f"Hostname não permitido: {host!r}. Apenas *.olx.com.br é aceito.")
    return url


def _build_search_url(params: BuscarAnunciosInput) -> str:
    """Monta a URL de busca da OLX com os filtros fornecidos."""
    estado = params.estado.lower() if params.estado else None
    if estado and estado not in ESTADOS:
        raise ValueError(f"Estado inválido: '{params.estado}'. Use siglas como 'sp', 'go', 'rj'.")

    # Path base
    regiao = f"estado-{estado}" if estado else "brasil"
    categoria = f"/{params.categoria}" if params.categoria else ""
    path = f"{BASE_URL}/{regiao}{categoria}"

    # Query string
    qp: dict[str, str] = {
        "q": params.query,
    }
    if params.pagina > 1:
        qp["o"] = str(params.pagina)

    if params.ordenar == OrdenarPor.PRECO_MENOR:
        qp["sp"] = "1"
    elif params.ordenar == OrdenarPor.MAIS_RECENTE:
        qp["sp"] = "2"

    if params.preco_min is not None:
        qp["ps"] = str(params.preco_min)
    if params.preco_max is not None:
        qp["pe"] = str(params.preco_max)

    qs = "&".join(f"{k}={v}" for k, v in qp.items())
    return f"{path}?{qs}"


MAX_HTML_BYTES = 8 * 1024 * 1024  # 8 MB — páginas reais OLX ficam <2MB
MAX_NEXT_DATA_BYTES = 5 * 1024 * 1024  # 5 MB — blob JSON #20

# Regex sem `.*?` em re.DOTALL — evita catastrophic backtracking em HTML
# adversarial. `[^<]*` é linear porque exclui `<`, garantindo que a
# engine não revisite caracteres já consumidos.
_NEXT_DATA_RE = re.compile(r'id="__NEXT_DATA__"[^>]{0,500}>([^<]+)</script>')


def _extract_next_data(html: str) -> dict:
    """Extrai o JSON embutido no __NEXT_DATA__ da página.

    Defesas:
    - HTML hard-cap (`MAX_HTML_BYTES`) p/ limitar trabalho da regex.
    - Regex linear (`[^<]+`) em vez de `.*?` p/ evitar ReDoS (#19).
    - JSON blob hard-cap (`MAX_NEXT_DATA_BYTES`) p/ evitar OOM (#20).
    """
    if len(html) > MAX_HTML_BYTES:
        html = html[:MAX_HTML_BYTES]

    match = _NEXT_DATA_RE.search(html)
    if not match:
        raise ValueError(
            "Não foi possível encontrar dados estruturados na página. A OLX pode estar bloqueando a requisição."
        )

    blob = match.group(1)
    if len(blob) > MAX_NEXT_DATA_BYTES:
        raise ValueError(f"Payload __NEXT_DATA__ excede limite ({len(blob)} > {MAX_NEXT_DATA_BYTES} bytes).")
    return json.loads(blob)


def _format_timestamp(ts: int) -> str:
    """Converte Unix timestamp para data legível."""
    try:
        return datetime.fromtimestamp(ts).strftime("%d/%m/%Y %H:%M")
    except Exception:
        return str(ts)


_STR_MAX = 500
_LIST_MAX = 50
_PROPS_MAX = 80


def _safe_str(v, max_len: int = _STR_MAX) -> str | None:
    """Coage para string com limite de tamanho; rejeita não-escalares."""
    if v is None:
        return None
    if isinstance(v, str):
        return v[:max_len]
    if isinstance(v, (int, float, bool)):
        return str(v)[:max_len]
    return None  # dict/list em campo string = payload malicioso, descarta


def _safe_dict(v) -> dict:
    return v if isinstance(v, dict) else {}


def _safe_list(v) -> list:
    return v if isinstance(v, list) else []


def _format_ad_summary(ad: dict) -> dict:
    """Normaliza um anúncio da listagem aplicando coerção defensiva.

    Site comprometido pode injetar JSON malicioso no __NEXT_DATA__
    (lists onde se espera string, dicts gigantes, etc.). Cada campo
    passa por `_safe_*` antes de chegar ao LLM (issue #29).
    """
    if not isinstance(ad, dict):
        return {}

    preco_raw = _safe_str(ad.get("priceValue")) or _safe_str(ad.get("price")) or ""
    loc = _safe_dict(ad.get("locationDetails"))
    images = _safe_list(ad.get("images"))[:_LIST_MAX]
    primeira_img = _safe_dict(images[0] if images else {}).get("original")

    propriedades_raw = _safe_list(ad.get("properties"))[:_PROPS_MAX]
    propriedades = {}
    for p in propriedades_raw:
        if not isinstance(p, dict):
            continue
        label = _safe_str(p.get("label"), max_len=100)
        value = _safe_str(p.get("value"))
        if label and value:
            propriedades[label] = value

    date_raw = ad.get("date")
    if isinstance(date_raw, int):
        data = _format_timestamp(date_raw)
    elif isinstance(date_raw, str):
        data = date_raw[:_STR_MAX]
    else:
        data = None

    list_id = ad.get("listId")
    if not isinstance(list_id, (int, str)):
        list_id = None

    municipality = _safe_str(loc.get("municipality")) or ""
    uf = _safe_str(loc.get("uf")) or ""
    localizacao = _safe_str(ad.get("location")) or f"{municipality} - {uf}".strip(" -")

    return {
        "id": list_id,
        "titulo": _safe_str(ad.get("subject")) or _safe_str(ad.get("title")),
        "preco": preco_raw,
        "categoria": _safe_str(ad.get("categoryName")) or _safe_str(ad.get("category")),
        "localizacao": localizacao,
        "bairro": _safe_str(loc.get("neighbourhood")),
        "data": data,
        "url": _safe_str(ad.get("friendlyUrl")) or _safe_str(ad.get("url")),
        "imagem": _safe_str(primeira_img) if primeira_img else None,
        "profissional": bool(ad.get("professionalAd", False)),
        "entrega_olx": bool(_safe_dict(ad.get("olxDelivery")).get("enabled", False)),
        "propriedades": propriedades,
    }


async def _fetch_with_evasion(url: str, referer_override: str | None = None) -> str:
    """
    GET com técnicas anti-bloqueio:
    - Rotação de perfil de browser (UA + sec-ch-ua coerentes)
    - Warm-up opcional na homepage para captar cookies (bm_*, ak_bmsc, etc)
    - Retry com backoff exponencial + jitter
    - Troca de perfil a cada falha 403/429
    - Fallback final: Google Web Cache
    Levanta httpx.HTTPStatusError se todas tentativas falharem.
    """
    last_exc: Exception | None = None

    async with _RateGate(url):
        for attempt in range(MAX_RETRIES):
            profile = random.choice(BROWSER_PROFILES)
            cookies = httpx.Cookies()

            try:
                async with httpx.AsyncClient(
                    follow_redirects=True,
                    timeout=REQUEST_TIMEOUT,
                    http2=HTTP2,
                    cookies=cookies,
                ) as client:
                    if attempt == 0 or random.random() < WARMUP_PROBABILITY:
                        try:
                            warm_headers = _build_headers(
                                profile, referer="https://www.google.com/", same_origin=False
                            )
                            await client.get(BASE_URL + "/", headers=warm_headers)
                            await asyncio.sleep(random.uniform(0.4, 1.2))
                        except Exception:
                            pass

                    ref = referer_override or (
                        BASE_URL + "/" if random.random() < 0.5 else "https://www.google.com/"
                    )
                    same_origin = ref.startswith(BASE_URL)
                    headers = _build_headers(profile, referer=ref, same_origin=same_origin)

                    resp = await client.get(url, headers=headers)
                    if resp.status_code in (403, 429):
                        last_exc = httpx.HTTPStatusError(
                            f"status {resp.status_code}", request=resp.request, response=resp
                        )
                        logger.debug("retry %s: status %s p/ %s", attempt, resp.status_code, url)
                        await asyncio.sleep((2**attempt) + random.uniform(0.3, 1.5))
                        continue
                    resp.raise_for_status()
                    return resp.text
            except httpx.HTTPStatusError as e:
                last_exc = e
                if e.response.status_code not in (403, 429, 503):
                    raise
                await asyncio.sleep((2**attempt) + random.uniform(0.3, 1.5))
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last_exc = e
                await asyncio.sleep((2**attempt) + random.uniform(0.2, 0.8))

    if last_exc:
        raise last_exc
    raise RuntimeError("Falha ao buscar URL após retries.")


async def _fetch_via_jina(url: str) -> str:
    """Fallback usando r.jina.ai como proxy reader. Retorna markdown."""
    proxy_url = f"https://r.jina.ai/{url}"
    logger.info("jina_fallback url=%s", url)
    async with _RateGate(proxy_url):
        async with httpx.AsyncClient(follow_redirects=True, timeout=REQUEST_TIMEOUT) as client:
            resp = await client.get(
                proxy_url,
                headers={"Accept": "text/markdown", "X-Return-Format": "markdown"},
            )
            resp.raise_for_status()
            return resp.text


def _parse_search_markdown(md: str, url_busca: str) -> dict:
    """Parser de fallback que extrai anúncios do markdown do r.jina.ai."""
    anuncios: list[dict] = []

    # Corta seção "Você pode gostar" para não misturar recomendações com busca real
    cut = re.search(r"##\s*Você pode gostar", md)
    md_busca = md[: cut.start()] if cut else md

    # Total: "1 - N de TOTAL resultados"
    total = 0
    mt = re.search(r"de\s+(\d+)\s+resultados?", md_busca)
    if mt:
        total = int(mt.group(1))
    md = md_busca

    # Cada anúncio aparece como: "## [Titulo](URL ...)" seguido de bloco até "Adicionar aos favoritos"
    pattern = re.compile(
        r'## \[([^\]]+)\]\((https?://[^\s)"]+)[^)]*\)\s*(.*?)Adicionar aos favoritos',
        re.DOTALL,
    )
    seen_ids: set[str] = set()
    for m in pattern.finditer(md):
        titulo = m.group(1).strip()
        link = m.group(2).strip()
        bloco = m.group(3)

        # Pular top_ads / anúncios patrocinados fora da busca real
        if "top_ads" in link:
            continue

        idm = re.search(r"/(\d{8,})(?:\?|$|-)", link)
        ad_id = idm.group(1) if idm else link
        if ad_id in seen_ids:
            continue
        seen_ids.add(ad_id)

        preco_m = re.search(r"R\$\s*([\d\.\,]+)", bloco)
        preco = f"R$ {preco_m.group(1)}" if preco_m else None

        # Localização: linha contendo cidade (heurística - antes de data)
        loc = None
        for ln in bloco.split("\n"):
            ln = ln.strip()
            if not ln or ln.startswith(
                ("![", "*", "Slide", "Ir para", "Entrega", "Pague", "Parcelamento", "em até", "Garantia")
            ):
                continue
            if re.match(r"^\d+\s+de\s+\w+", ln) or re.match(r"^\d{2}/\d{2}/\d{4}", ln):
                continue
            if "R$" in ln or "###" in ln:
                continue
            loc = ln
            break

        data_m = re.search(r"(\d+\s+de\s+\w+,\s*\d+:\d+|\d{2}/\d{2}/\d{4},?\s*\d+:\d+)", bloco)
        data = data_m.group(1) if data_m else None

        img_m = re.search(r"!\[[^\]]*\]\((https://img\.olx\.com\.br/[^\)]+)\)", bloco)
        imagem = img_m.group(1) if img_m else None

        anuncios.append(
            {
                "id": int(ad_id) if ad_id.isdigit() else ad_id,
                "titulo": titulo,
                "preco": preco,
                "localizacao": loc,
                "data": data,
                "url": link,
                "imagem": imagem,
                "_fonte": "jina_markdown",
            }
        )

    return {
        "total": total or len(anuncios),
        "pagina": 1,
        "por_pagina": len(anuncios),
        "url_busca": url_busca,
        "anuncios": anuncios,
        "_fonte": "jina_proxy_fallback",
    }


def _handle_http_error(e: Exception) -> str:
    """Formata erros HTTP de forma padronizada.

    Mensagens conhecidas (HTTP status, timeout, validação) são seguras:
    não contêm caminhos internos nem detalhes da stack. Para qualquer
    exceção desconhecida, geramos um correlation ID, logamos a stack
    completa internamente, e devolvemos apenas o ID ao caller — evita
    leak de paths, tokens e estado de runtime (issue #21).
    """
    if isinstance(e, httpx.HTTPStatusError):
        code = e.response.status_code
        if code == 404:
            return "Erro: página não encontrada (404). Verifique a URL ou os filtros informados."
        if code == 403:
            return "Erro: acesso negado (403). A OLX pode estar bloqueando requisições automatizadas."
        if code == 429:
            return "Erro: muitas requisições (429). Aguarde alguns segundos antes de tentar novamente."
        # body da resposta pode conter HTML longo — não propagar
        return f"Erro HTTP {code}: resposta inesperada do servidor."
    if isinstance(e, httpx.TimeoutException):
        return "Erro: timeout na requisição. Tente novamente."
    if isinstance(e, ValueError):
        return f"Erro de validação: {e}"
    err_id = uuid.uuid4().hex[:8]
    logger.exception("Erro inesperado [%s]: %s", err_id, e)
    return f"Erro inesperado (id={err_id}). Consulte os logs do servidor para detalhes."


# ---------------------------------------------------------------------------
# Ferramentas
# ---------------------------------------------------------------------------


@mcp.tool(
    name="olx_buscar_anuncios",
    annotations={
        "title": "Buscar Anúncios na OLX Brasil",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def olx_buscar_anuncios(params: BuscarAnunciosInput) -> str:
    """Busca anúncios públicos na OLX Brasil com filtros de texto, estado, categoria, preço e ordenação.

    Retorna lista paginada de anúncios com título, preço, localização, data, URL e propriedades extras.

    Args:
        params (BuscarAnunciosInput):
            - query (str): Termo de busca obrigatório.
            - estado (Optional[str]): Sigla do estado ('sp', 'go', etc.). Padrão: todo Brasil.
            - categoria (Optional[str]): Slug de categoria OLX. Ex: 'informatica-e-acessorios'.
            - preco_min (Optional[int]): Preço mínimo em reais.
            - preco_max (Optional[int]): Preço máximo em reais.
            - ordenar (str): 'relevance' | 'price' | 'date'. Padrão: 'relevance'.
            - pagina (int): Número da página (1–50). Padrão: 1.

    Returns:
        str: JSON com campos:
            - total (int): Total de anúncios encontrados.
            - pagina (int): Página atual.
            - por_pagina (int): Anúncios por página.
            - url_busca (str): URL utilizada na busca.
            - anuncios (list): Lista de anúncios com id, titulo, preco, categoria,
              localizacao, bairro, data, url, imagem, profissional, entrega_olx, propriedades.
    """
    try:
        url = _build_search_url(params)
    except ValueError as e:
        return json.dumps({"erro": str(e)}, ensure_ascii=False)

    logger.info(
        "olx_search query=%r estado=%s categoria=%s pagina=%s",
        params.query,
        params.estado,
        params.categoria,
        params.pagina,
    )
    html = None
    try:
        html = await _fetch_with_evasion(url)
    except Exception:
        if DISABLE_JINA:
            return json.dumps(
                {"erro": "Erro: acesso negado e fallback Jina desabilitado (MCP_BR_DISABLE_JINA=1)."},
                ensure_ascii=False,
            )
        # Fallback markdown via r.jina.ai
        try:
            md = await _fetch_via_jina(url)
            result = _parse_search_markdown(md, url)
            result["fonte"] = "olx_jina"
            return json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            return json.dumps({"erro": _handle_http_error(e)}, ensure_ascii=False)

    try:
        data = _extract_next_data(html)
        props = data["props"]["pageProps"]
    except Exception as e:
        err_id = uuid.uuid4().hex[:8]
        logger.exception("Falha ao extrair dados [%s]: %s", err_id, e)
        return json.dumps({"erro": f"Falha ao extrair dados (id={err_id})."}, ensure_ascii=False)

    ads_raw = props.get("ads", [])
    anuncios = [_format_ad_summary(ad) for ad in ads_raw if ad.get("listId")]
    for a in anuncios:
        a["fonte"] = "olx"

    result = {
        "fonte": "olx",
        "total": props.get("totalOfAds", 0),
        "pagina": props.get("pageIndex", params.pagina),
        "por_pagina": props.get("pageSize", len(anuncios)),
        "url_busca": url,
        "anuncios": anuncios,
    }
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool(
    name="olx_detalhe_anuncio",
    annotations={
        "title": "Obter Detalhes de Anúncio OLX",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def olx_detalhe_anuncio(params: DetalheAnuncioInput) -> str:
    """Obtém detalhes completos de um anúncio específico da OLX a partir da URL.

    Args:
        params (DetalheAnuncioInput):
            - url (str): URL completa do anúncio na OLX.

    Returns:
        str: JSON com campos:
            - id (int): ID do anúncio (list_id).
            - titulo (str): Título do anúncio.
            - descricao (str): Descrição completa (HTML removido).
            - preco (str): Preço formatado.
            - categoria (str): Categoria principal.
            - subcategoria (str): Subcategoria.
            - estado (str): UF do anúncio.
            - municipio (str): Cidade.
            - bairro (str): Bairro.
            - vendedor (str): Nome do vendedor.
            - profissional (bool): Se é anunciante profissional.
            - data (str): Data de publicação formatada.
            - imagens (list[str]): Lista de URLs das imagens.
            - propriedades (dict): Atributos específicos da categoria.
            - url (str): URL canônica do anúncio.
    """
    # SSRF guard: somente *.olx.com.br é permitido
    try:
        _validar_url_olx(params.url)
    except ValueError as e:
        return json.dumps({"erro": f"Erro de validação: {e}"}, ensure_ascii=False)

    used_jina = False
    try:
        html = await _fetch_with_evasion(params.url)
    except Exception:
        if DISABLE_JINA:
            return json.dumps(
                {"erro": "Erro: acesso negado e fallback Jina desabilitado (MCP_BR_DISABLE_JINA=1)."},
                ensure_ascii=False,
            )
        try:
            html = await _fetch_via_jina(params.url)
            used_jina = True
        except Exception as e:
            return json.dumps({"erro": _handle_http_error(e)}, ensure_ascii=False)

    # Se vier markdown do jina, parse simples
    if used_jina:
        try:
            titulo_m = re.search(r"^Title:\s*(.+)$", html, re.MULTILINE)
            preco_m = re.search(r"R\$\s*([\d\.\,]+)", html)
            desc_m = re.search(r"##\s*Descrição\s*(.+?)(?:##|\Z)", html, re.DOTALL | re.IGNORECASE)
            loc_m = re.search(r"##\s*Localiza[çc][ãa]o\s*(.+?)(?:##|\Z)", html, re.DOTALL | re.IGNORECASE)
            imgs = list(dict.fromkeys(re.findall(r"https://img\.olx\.com\.br/[^\s\)]+", html)))
            result = {
                "fonte": "olx_jina",
                "titulo": (titulo_m.group(1).strip() if titulo_m else None),
                "preco": (f"R$ {preco_m.group(1)}" if preco_m else None),
                "descricao": (desc_m.group(1).strip()[:2000] if desc_m else None),
                "localizacao": (loc_m.group(1).strip()[:300] if loc_m else None),
                "imagens": imgs[:10],
                "url": params.url,
            }
            return json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            err_id = uuid.uuid4().hex[:8]
            logger.exception("Falha ao parsear markdown [%s]: %s", err_id, e)
            return json.dumps({"erro": f"Falha ao parsear markdown (id={err_id})."}, ensure_ascii=False)

    try:
        # Extrai JSON de rastreamento embarcado no dataLayer (mais consistente para detalhes)
        ad_data: dict = {}

        # Tenta via __NEXT_DATA__
        try:
            data = _extract_next_data(html)
            props = data.get("props", {}).get("pageProps", {})
            ad_data = props.get("ad", props.get("adData", {}))
        except ValueError:
            pass

        # Fallback: extrai diretamente do blob de tracking embarcado na página
        if not ad_data:
            m = re.search(r'"adId":(\d+).*?"subject":"([^"]+)"', html)
            if not m:
                return json.dumps({"erro": "Não foi possível extrair dados do anúncio."}, ensure_ascii=False)

        # Extrai campos individuais com regex como complemento
        def _re(pattern: str, default: str = "") -> str:
            match = re.search(pattern, html)
            return match.group(1) if match else default

        list_id = _re(r'"listId":(\d+)')
        subject = _re(r'"subject":"([^"]+)"')
        description_raw = _re(r'"description":"([^"]{10,})"')
        description = re.sub(r"<[^>]+>", " ", description_raw).strip()
        price = _re(r'"price":"([^"]+)"')
        seller = _re(r'"sellerName":"([^"]+)"')
        state = _re(r'"state":"([A-Z]{2})"')
        municipality = _re(r'"municipality":"([^"]+)"')
        neighbourhood = _re(r'"neighbourhood":"([^"]+)"')
        main_category = _re(r'"mainCategory":"([^"]+)"')
        sub_category = _re(r'"subCategory":"([^"]+)"')
        ad_date_str = _re(r'"adDate":"([^"]+)"')
        professional = '"professionalAd":"1"' in html or '"professionalAd":true' in html

        # Imagens: coleta URLs únicas de alta resolução
        imgs_raw = re.findall(r'https://img\.olx\.com\.br/images/[^\s"\'&]+\.jpg', html)
        imgs = list(dict.fromkeys(imgs_raw))  # dedup mantendo ordem

        # Propriedades específicas da categoria
        props_matches = re.findall(r'"label":"([^"]+)","value":"([^"]+)"', html)
        propriedades = {label: value for label, value in props_matches if label not in ("Categoria",)}

        result = {
            "id": int(list_id) if list_id else None,
            "titulo": subject,
            "descricao": description,
            "preco": f"R$ {price}" if price and not price.startswith("R") else price,
            "categoria": main_category,
            "subcategoria": sub_category,
            "estado": state,
            "municipio": municipality,
            "bairro": neighbourhood,
            "vendedor": seller,
            "profissional": professional,
            "data": ad_date_str,
            "imagens": imgs[:10],
            "propriedades": propriedades,
            "url": params.url,
            "fonte": "olx",
        }
        return json.dumps(result, ensure_ascii=False, indent=2)

    except Exception as e:
        err_id = uuid.uuid4().hex[:8]
        logger.exception("Falha ao processar anúncio [%s]: %s", err_id, e)
        return json.dumps({"erro": f"Falha ao processar anúncio (id={err_id})."}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Mercado Livre — busca via scraping (Googlebot UA bypassa micro-landing)
# ---------------------------------------------------------------------------

ML_BASE = "https://lista.mercadolivre.com.br"

# UA Googlebot bypassa o micro-landing anti-bot do ML. Operadores que
# considerem o spoof inaceitável (risco ético/legal) podem sobrescrever
# via MCP_BR_ML_USER_AGENT — nesse caso o ML geralmente devolve a
# página de challenge e a tool retorna lista vazia, mas sem spoof.
_ML_DEFAULT_UA = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
ML_HEADERS = {
    "User-Agent": _env("ML_USER_AGENT") or _ML_DEFAULT_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}


class BuscarMLInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")

    query: str = Field(..., min_length=1, max_length=200, description="Termo de busca.")
    preco_min: int | None = Field(default=None, ge=0, description="Preço mínimo em R$.")
    preco_max: int | None = Field(default=None, ge=0, description="Preço máximo em R$.")
    estado: str | None = Field(
        default=None,
        min_length=2,
        max_length=2,
        description=(
            "AVISO: heurística best-effort. ML raramente expõe localização "
            "nos cards de listagem, então este filtro frequentemente retorna "
            "lista vazia mesmo quando há resultados no estado. Para resultados "
            "confiáveis por região, use ml_buscar_anuncios sem este campo e "
            "filtre pelo título/descrição do anúncio."
        ),
    )
    condicao: str | None = Field(
        default=None,
        description=(
            "'novo' ou 'usado'. AVISO: ML ignora o filtro via URL, então é "
            "aplicado pós-scraping por heurística no título do anúncio."
        ),
    )
    pagina: int = Field(default=1, ge=1, le=20)


def _build_ml_url(p: BuscarMLInput) -> tuple[str, list[str]]:
    """Monta URL de busca ML. Retorna (url, avisos)."""
    avisos: list[str] = []
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", p.query.strip().lower()).strip("-")
    base = f"{ML_BASE}/{slug}"
    filters = []
    if p.preco_min is not None or p.preco_max is not None:
        lo = p.preco_min if p.preco_min is not None else 0
        hi = p.preco_max if p.preco_max is not None else 0
        filters.append(f"_PriceRange_{lo}-{hi if hi else '*'}")
    if p.condicao:
        cond = {"novo": "2230284", "usado": "2230581"}.get(p.condicao.lower())
        if cond:
            # ML não respeita ITEM_CONDITION via slug em /lista — aplicamos
            # como filtro pós-scraping no título do anúncio.
            avisos.append("Filtro 'condicao' aplicado pós-scraping (heurística por título).")
    if p.pagina > 1:
        filters.append(f"_Desde_{(p.pagina - 1) * 50 + 1}")
    if filters:
        base += "".join(filters)
    return base, avisos


def _parse_ml_html(html: str) -> list[dict]:
    """Extrai cards de produto do HTML do Mercado Livre."""
    anuncios = []
    cards = re.findall(
        r'<(?:li|div)[^>]*class="[^"]*(?:ui-search-layout__item|poly-card)[^"]*".*?</(?:li|div)>',
        html,
        re.DOTALL,
    )
    for card in cards:
        title_m = re.search(
            r'class="poly-component__title[^"]*"[^>]*>([^<]+)<',
            card,
        )
        if not title_m:
            continue
        titulo = title_m.group(1).strip()

        link_m = re.search(
            r'href="(https?://(?:produto|articulo|www)\.mercado(?:livre|libre)\.com[^"]+)"',
            card,
        )
        link = link_m.group(1).replace("&amp;", "&").split("#")[0] if link_m else None

        preco_int = re.search(r"andes-money-amount__fraction[^>]*>([\d\.]+)<", card)
        preco_cents = re.search(r"andes-money-amount__cents[^>]*>([\d]+)<", card)
        preco = None
        if preco_int:
            val = preco_int.group(1).replace(".", "")
            preco = f"R$ {val}" + (f",{preco_cents.group(1)}" if preco_cents else "")

        # Frete
        frete = "Frete grátis" if "Frete grátis" in card or "frete grátis" in card else None

        # Local (raro nos cards de busca ML; geralmente só na pdp)
        loc_m = re.search(r"poly-component__location[^>]*>([^<]+)<", card)
        loc = loc_m.group(1).strip() if loc_m else None

        # Vendedor
        sel_m = re.search(r"poly-component__seller[^>]*>([^<]+)<", card)
        seller = sel_m.group(1).strip() if sel_m else None

        # Img
        img_m = re.search(r'<img[^>]+(?:src|data-src)="(https://http2\.mlstatic\.com/[^"]+)"', card)
        imagem = img_m.group(1) if img_m else None

        # Atributos (RAM, armazenamento etc) - poly-attributes_list
        attrs = re.findall(r"poly-attributes_list__item[^>]*>([^<]+)<", card)

        # ID extraído do MLB-<digits> na URL — campo comum a OLX/ML
        ad_id: str | int | None = None
        if link:
            m_id = re.search(r"MLB-?(\d+)", link)
            if m_id:
                ad_id = int(m_id.group(1))

        anuncios.append(
            {
                # campos comuns (schema unificado)
                "id": ad_id,
                "titulo": titulo,
                "preco": preco,
                "localizacao": loc,
                "data": None,  # ML não expõe data nos cards
                "url": link,
                "imagem": imagem,
                # campos específicos ML
                "frete": frete,
                "vendedor": seller,
                "atributos": [a.strip() for a in attrs if a.strip()],
            }
        )
    return anuncios


@mcp.tool(
    name="ml_buscar_anuncios",
    annotations={
        "title": "Buscar Anúncios no Mercado Livre Brasil",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def ml_buscar_anuncios(params: BuscarMLInput) -> str:
    """Busca anúncios no Mercado Livre Brasil.

    Args:
        params (BuscarMLInput):
            - query (str): Termo obrigatório.
            - preco_min/preco_max (Optional[int]): Faixa de preço em reais.
            - estado (Optional[str]): Filtro pós-scraping por sigla UF (heurística).
            - condicao (Optional[str]): 'novo' ou 'usado'.
            - pagina (int): Página (50 itens por página).

    Returns:
        str: JSON com lista de anúncios (titulo, preco, frete, atributos, url, imagem).
    """
    url, avisos = _build_ml_url(params)
    logger.info("ml_search query=%r condicao=%s pagina=%s", params.query, params.condicao, params.pagina)
    try:
        async with _RateGate(url):
            async with httpx.AsyncClient(
                follow_redirects=True, timeout=REQUEST_TIMEOUT, http2=HTTP2
            ) as client:
                resp = await client.get(url, headers=ML_HEADERS)
                resp.raise_for_status()
                html = resp.text
    except Exception as e:
        return json.dumps({"erro": _handle_http_error(e), "url_busca": url}, ensure_ascii=False)

    anuncios = _parse_ml_html(html)
    for a in anuncios:
        a["fonte"] = "ml"

    # Filtro condicao pós-scraping (ML não aceita via URL no /lista)
    if params.condicao:
        cond_low = params.condicao.lower()
        if cond_low == "usado":
            anuncios = [a for a in anuncios if "usado" in (a.get("titulo") or "").lower()]
        elif cond_low == "novo":
            anuncios = [a for a in anuncios if "usado" not in (a.get("titulo") or "").lower()]

    # Filtro estado (heurística pelo texto da localização)
    if params.estado:
        uf = params.estado.upper()
        antes = len(anuncios)
        anuncios = [a for a in anuncios if a.get("localizacao") and uf in a["localizacao"].upper()]
        if antes > 0 and not anuncios:
            avisos.append(
                "Filtro 'estado' não encontrou localização nos cards (raro no ML); "
                "considere remover o filtro."
            )

    return json.dumps(
        {
            "fonte": "ml",
            "total": len(anuncios),  # ML não expõe totalOfAds; reflete itens retornados
            "pagina": params.pagina,
            "por_pagina": len(anuncios),
            "url_busca": url,
            "avisos": avisos,
            "anuncios": anuncios,
        },
        ensure_ascii=False,
        indent=2,
    )


# ---------------------------------------------------------------------------
# Mercado Livre — detalhe
# ---------------------------------------------------------------------------

_ALLOWED_ML_HOSTS = (
    ".mercadolivre.com.br",
    ".mercadolibre.com",
)


def _validar_url_ml(url: str) -> str:
    """SSRF guard para Mercado Livre."""
    try:
        p = urlparse(url)
    except Exception as e:
        raise ValueError(f"URL inválida: {e}") from None
    if p.scheme not in ("http", "https"):
        raise ValueError(f"Esquema não permitido: {p.scheme!r}. Use http(s).")
    host = (p.hostname or "").lower()
    if not host:
        raise ValueError("URL sem hostname.")
    if not any(host.endswith(h) for h in _ALLOWED_ML_HOSTS):
        raise ValueError(f"Hostname não permitido: {host!r}. Apenas *.mercadolivre.com.br/mercadolibre.com.")
    return url


class DetalheMLInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")
    url: str = Field(
        ...,
        min_length=20,
        description=(
            "URL completa do anúncio no Mercado Livre. Ex: 'https://produto.mercadolivre.com.br/MLB-1234-...'"
        ),
    )


@mcp.tool(
    name="ml_detalhe_anuncio",
    annotations={
        "title": "Obter Detalhes de Anúncio no Mercado Livre",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def ml_detalhe_anuncio(params: DetalheMLInput) -> str:
    """Obtém detalhes de um anúncio do Mercado Livre a partir da URL.

    Args:
        params (DetalheMLInput):
            - url (str): URL completa do anúncio.

    Returns:
        str: JSON com campos comuns (id, titulo, preco, url, fonte) + descricao,
        imagens, vendedor. Em caso de erro: {"erro": "..."}.
    """
    try:
        _validar_url_ml(params.url)
    except ValueError as e:
        return json.dumps({"erro": f"Erro de validação: {e}"}, ensure_ascii=False)

    logger.info("ml_detail url=%s", params.url)
    try:
        async with _RateGate(params.url):
            async with httpx.AsyncClient(
                follow_redirects=True, timeout=REQUEST_TIMEOUT, http2=HTTP2
            ) as client:
                resp = await client.get(params.url, headers=ML_HEADERS)
                resp.raise_for_status()
                html = resp.text
    except Exception as e:
        return json.dumps({"erro": _handle_http_error(e)}, ensure_ascii=False)

    # ML retorna ~600KB; limitar contra OOM (#20)
    if len(html) > MAX_HTML_BYTES:
        html = html[:MAX_HTML_BYTES]

    try:
        # Title via meta og:title ou h1.ui-pdp-title
        title_m = re.search(r'class="ui-pdp-title"[^>]*>([^<]{1,500})<', html)
        titulo = title_m.group(1).strip() if title_m else None

        # Preço: primeiro andes-money-amount__fraction
        price_m = re.search(r'class="andes-money-amount__fraction"[^>]*>([\d\.]{1,12})<', html)
        preco = f"R$ {price_m.group(1)}" if price_m else None

        # ID do MLB
        id_m = re.search(r'"itemId":"MLB(\d+)"', html) or re.search(r"MLB-?(\d+)", params.url)
        ad_id = int(id_m.group(1)) if id_m else None

        # Vendedor
        seller_m = re.search(r'"nickname":"([^"]{1,80})"', html)
        vendedor = seller_m.group(1) if seller_m else None

        # Descrição (plainText)
        desc_m = re.search(r'"plainText":"((?:[^"\\]|\\.){30,2000})"', html)
        descricao = None
        if desc_m:
            descricao = desc_m.group(1).encode().decode("unicode_escape", errors="ignore")[:2000]

        # Imagens
        imgs_raw = re.findall(r'(https://http2\.mlstatic\.com/D_NQ_NP[^"\s]+\.(?:jpg|webp))', html)
        imagens = list(dict.fromkeys(imgs_raw))[:10]

        result = {
            "fonte": "ml",
            "id": ad_id,
            "titulo": titulo,
            "preco": preco,
            "vendedor": vendedor,
            "descricao": descricao,
            "imagens": imagens,
            "url": params.url,
        }
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        err_id = uuid.uuid4().hex[:8]
        logger.exception("Falha ao parsear ML detalhe [%s]: %s", err_id, e)
        return json.dumps({"erro": f"Falha ao parsear anúncio ML (id={err_id})."}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Entry point para `mcp-brazil-marketplaces` console_script e `python -m mcp_brazil_marketplaces`."""
    mcp.run()


if __name__ == "__main__":
    main()
