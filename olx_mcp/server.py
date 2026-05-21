"""
OLX Brasil MCP Server
Busca anúncios públicos da OLX Brasil via scraping do __NEXT_DATA__.
"""

import asyncio
import json
import os
import random
import re
from datetime import datetime
from typing import Optional
from enum import Enum
from urllib.parse import urlparse

import httpx
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field, ConfigDict

# ---------------------------------------------------------------------------
# Feature flags via env
# ---------------------------------------------------------------------------

DISABLE_JINA = os.getenv("OLX_MCP_DISABLE_JINA", "0").lower() in ("1", "true", "yes")
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


def _build_headers(profile: dict, referer: str = "https://www.google.com/", same_origin: bool = False) -> dict:
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


REQUEST_TIMEOUT = 25.0
HTTP2 = True  # Obrigatório: OLX retorna 403 em HTTP/1.1
MAX_RETRIES = 4
WARMUP_PROBABILITY = 0.7  # chance de fazer warm-up homepage antes da busca

ESTADOS = {
    "ac", "al", "ap", "am", "ba", "ce", "df", "es", "go",
    "ma", "mt", "ms", "mg", "pa", "pb", "pr", "pe", "pi",
    "rj", "rn", "rs", "ro", "rr", "sc", "sp", "se", "to",
}

# ---------------------------------------------------------------------------
# Inicialização
# ---------------------------------------------------------------------------

mcp = FastMCP("olx_mcp")


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
    estado: Optional[str] = Field(
        default=None,
        description=(
            "Sigla do estado brasileiro em minúsculas. Ex: 'sp', 'go', 'rj'. "
            "Se omitido, busca em todo o Brasil."
        ),
        min_length=2,
        max_length=2,
    )
    categoria: Optional[str] = Field(
        default=None,
        description=(
            "Slug de categoria da OLX. Ex: 'informatica-e-acessorios', 'celulares', "
            "'imoveis', 'veiculos-e-pecas', 'eletrodomesticos'. "
            "Se omitido, busca em todas as categorias."
        ),
    )
    preco_min: Optional[int] = Field(
        default=None,
        description="Preço mínimo em reais (inteiro). Ex: 500",
        ge=0,
    )
    preco_max: Optional[int] = Field(
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
        raise ValueError(
            f"Hostname não permitido: {host!r}. Apenas *.olx.com.br é aceito."
        )
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


def _extract_next_data(html: str) -> dict:
    """Extrai o JSON embutido no __NEXT_DATA__ da página."""
    match = re.search(
        r'id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        html,
        re.DOTALL,
    )
    if not match:
        raise ValueError("Não foi possível encontrar dados estruturados na página. A OLX pode estar bloqueando a requisição.")
    return json.loads(match.group(1))


def _format_timestamp(ts: int) -> str:
    """Converte Unix timestamp para data legível."""
    try:
        return datetime.fromtimestamp(ts).strftime("%d/%m/%Y %H:%M")
    except Exception:
        return str(ts)


def _format_ad_summary(ad: dict) -> dict:
    """Normaliza um anúncio da listagem para campos essenciais."""
    preco_raw = ad.get("priceValue") or ad.get("price", "")
    loc = ad.get("locationDetails", {})
    return {
        "id": ad.get("listId"),
        "titulo": ad.get("subject") or ad.get("title"),
        "preco": preco_raw,
        "categoria": ad.get("categoryName") or ad.get("category"),
        "localizacao": ad.get("location") or f"{loc.get('municipality', '')} - {loc.get('uf', '')}".strip(" -"),
        "bairro": loc.get("neighbourhood"),
        "data": _format_timestamp(ad["date"]) if isinstance(ad.get("date"), int) else ad.get("date"),
        "url": ad.get("friendlyUrl") or ad.get("url"),
        "imagem": (ad.get("images") or [{}])[0].get("original"),
        "profissional": ad.get("professionalAd", False),
        "entrega_olx": ad.get("olxDelivery", {}).get("enabled", False),
        "propriedades": {p["label"]: p["value"] for p in ad.get("properties", []) if p.get("value")},
    }


async def _fetch_with_evasion(url: str, referer_override: Optional[str] = None) -> str:
    """
    GET com técnicas anti-bloqueio:
    - Rotação de perfil de browser (UA + sec-ch-ua coerentes)
    - Warm-up opcional na homepage para captar cookies (bm_*, ak_bmsc, etc)
    - Retry com backoff exponencial + jitter
    - Troca de perfil a cada falha 403/429
    - Fallback final: Google Web Cache
    Levanta httpx.HTTPStatusError se todas tentativas falharem.
    """
    last_exc: Optional[Exception] = None

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
                # Warm-up: visita homepage para coletar cookies de sessão / anti-bot
                if attempt == 0 or random.random() < WARMUP_PROBABILITY:
                    try:
                        warm_headers = _build_headers(profile, referer="https://www.google.com/", same_origin=False)
                        await client.get(BASE_URL + "/", headers=warm_headers)
                        await asyncio.sleep(random.uniform(0.4, 1.2))
                    except Exception:
                        pass  # warm-up best-effort

                ref = referer_override or (BASE_URL + "/" if random.random() < 0.5 else "https://www.google.com/")
                same_origin = ref.startswith(BASE_URL)
                headers = _build_headers(profile, referer=ref, same_origin=same_origin)

                resp = await client.get(url, headers=headers)
                if resp.status_code in (403, 429):
                    last_exc = httpx.HTTPStatusError(
                        f"status {resp.status_code}", request=resp.request, response=resp
                    )
                    await asyncio.sleep((2 ** attempt) + random.uniform(0.3, 1.5))
                    continue
                resp.raise_for_status()
                return resp.text
        except httpx.HTTPStatusError as e:
            last_exc = e
            if e.response.status_code not in (403, 429, 503):
                raise
            await asyncio.sleep((2 ** attempt) + random.uniform(0.3, 1.5))
        except (httpx.TimeoutException, httpx.TransportError) as e:
            last_exc = e
            await asyncio.sleep((2 ** attempt) + random.uniform(0.2, 0.8))

    if last_exc:
        raise last_exc
    raise RuntimeError("Falha ao buscar URL após retries.")


async def _fetch_via_jina(url: str) -> str:
    """Fallback usando r.jina.ai como proxy reader. Retorna markdown."""
    proxy_url = f"https://r.jina.ai/{url}"
    async with httpx.AsyncClient(follow_redirects=True, timeout=REQUEST_TIMEOUT) as client:
        resp = await client.get(proxy_url, headers={"Accept": "text/markdown", "X-Return-Format": "markdown"})
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
            if not ln or ln.startswith(("![", "*", "Slide", "Ir para", "Entrega", "Pague", "Parcelamento", "em até", "Garantia")):
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

        anuncios.append({
            "id": int(ad_id) if ad_id.isdigit() else ad_id,
            "titulo": titulo,
            "preco": preco,
            "localizacao": loc,
            "data": data,
            "url": link,
            "imagem": imagem,
            "_fonte": "jina_markdown",
        })

    return {
        "total": total or len(anuncios),
        "pagina": 1,
        "por_pagina": len(anuncios),
        "url_busca": url_busca,
        "anuncios": anuncios,
        "_fonte": "jina_proxy_fallback",
    }


def _handle_http_error(e: Exception) -> str:
    """Formata erros HTTP de forma padronizada."""
    if isinstance(e, httpx.HTTPStatusError):
        code = e.response.status_code
        if code == 404:
            return "Erro: página não encontrada (404). Verifique a URL ou os filtros informados."
        if code == 403:
            return "Erro: acesso negado (403). A OLX pode estar bloqueando requisições automatizadas."
        if code == 429:
            return "Erro: muitas requisições (429). Aguarde alguns segundos antes de tentar novamente."
        return f"Erro HTTP {code}: {e.response.text[:200]}"
    if isinstance(e, httpx.TimeoutException):
        return "Erro: timeout na requisição. Tente novamente."
    if isinstance(e, ValueError):
        return f"Erro de validação: {e}"
    return f"Erro inesperado: {type(e).__name__}: {e}"


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

    html = None
    try:
        html = await _fetch_with_evasion(url)
    except Exception:
        if DISABLE_JINA:
            return json.dumps({"erro": "Erro: acesso negado e fallback Jina desabilitado (OLX_MCP_DISABLE_JINA=1)."}, ensure_ascii=False)
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
        return json.dumps({"erro": f"Falha ao extrair dados: {e}"}, ensure_ascii=False)

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
            return json.dumps({"erro": "Erro: acesso negado e fallback Jina desabilitado (OLX_MCP_DISABLE_JINA=1)."}, ensure_ascii=False)
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
            return json.dumps({"erro": f"Falha ao parsear markdown: {e}"}, ensure_ascii=False)

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
        return json.dumps({"erro": f"Falha ao processar anúncio: {type(e).__name__}: {e}"}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Mercado Livre — busca via scraping (Googlebot UA bypassa micro-landing)
# ---------------------------------------------------------------------------

ML_BASE = "https://lista.mercadolivre.com.br"
ML_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}


class BuscarMLInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")

    query: str = Field(..., min_length=1, max_length=200, description="Termo de busca.")
    preco_min: Optional[int] = Field(default=None, ge=0, description="Preço mínimo em R$.")
    preco_max: Optional[int] = Field(default=None, ge=0, description="Preço máximo em R$.")
    estado: Optional[str] = Field(
        default=None, min_length=2, max_length=2,
        description="Sigla estado p/ filtrar resultados após scraping (heurística por texto)."
    )
    condicao: Optional[str] = Field(
        default=None, description="'novo' ou 'usado'. Aplica filtro ITEM_CONDITION."
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
            avisos.append(
                "Filtro 'condicao' aplicado pós-scraping (heurística por título)."
            )
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
        html, re.DOTALL,
    )
    for card in cards:
        title_m = re.search(
            r'class="poly-component__title[^"]*"[^>]*>([^<]+)<', card,
        )
        if not title_m:
            continue
        titulo = title_m.group(1).strip()

        link_m = re.search(
            r'href="(https?://(?:produto|articulo|www)\.mercado(?:livre|libre)\.com[^"]+)"',
            card,
        )
        link = link_m.group(1).replace("&amp;", "&").split("#")[0] if link_m else None

        preco_int = re.search(r'andes-money-amount__fraction[^>]*>([\d\.]+)<', card)
        preco_cents = re.search(r'andes-money-amount__cents[^>]*>([\d]+)<', card)
        preco = None
        if preco_int:
            val = preco_int.group(1).replace(".", "")
            preco = f"R$ {val}" + (f",{preco_cents.group(1)}" if preco_cents else "")

        # Frete
        frete = "Frete grátis" if "Frete grátis" in card or "frete grátis" in card else None

        # Local (raro nos cards de busca ML; geralmente só na pdp)
        loc_m = re.search(r'poly-component__location[^>]*>([^<]+)<', card)
        loc = loc_m.group(1).strip() if loc_m else None

        # Vendedor
        sel_m = re.search(r'poly-component__seller[^>]*>([^<]+)<', card)
        seller = sel_m.group(1).strip() if sel_m else None

        # Img
        img_m = re.search(r'<img[^>]+(?:src|data-src)="(https://http2\.mlstatic\.com/[^"]+)"', card)
        imagem = img_m.group(1) if img_m else None

        # Atributos (RAM, armazenamento etc) - poly-attributes_list
        attrs = re.findall(r'poly-attributes_list__item[^>]*>([^<]+)<', card)

        anuncios.append({
            "titulo": titulo,
            "preco": preco,
            "frete": frete,
            "vendedor": seller,
            "localizacao": loc,
            "atributos": [a.strip() for a in attrs if a.strip()],
            "imagem": imagem,
            "url": link,
        })
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
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=REQUEST_TIMEOUT, http2=HTTP2) as client:
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

    return json.dumps({
        "fonte": "ml",
        "total_retornados": len(anuncios),
        "pagina": params.pagina,
        "url_busca": url,
        "avisos": avisos,
        "anuncios": anuncios,
    }, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Entry point para `olx-mcp` console_script e `python -m olx_mcp`."""
    mcp.run()


if __name__ == "__main__":
    main()
