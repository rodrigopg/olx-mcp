"""
OLX Brasil MCP Server
Busca anúncios públicos da OLX Brasil via scraping do __NEXT_DATA__.
"""

import json
import re
from datetime import datetime
from typing import Optional
from enum import Enum

import httpx
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field, ConfigDict

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

BASE_URL = "https://www.olx.com.br"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.olx.com.br/",
    "Cache-Control": "no-cache",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Ch-Ua": '"Not A(Brand";v="99", "Google Chrome";v="121", "Chromium";v="121"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"macOS"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
}
REQUEST_TIMEOUT = 20.0
HTTP2 = True  # Obrigatório: a OLX retorna 403 em HTTP/1.1 (requer pacote httpx[http2])

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
        "sf": "1",
        "o": str(params.pagina),
    }

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

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=REQUEST_TIMEOUT, http2=HTTP2) as client:
            resp = await client.get(url, headers=HEADERS)
            resp.raise_for_status()
            html = resp.text
    except Exception as e:
        return json.dumps({"erro": _handle_http_error(e)}, ensure_ascii=False)

    try:
        data = _extract_next_data(html)
        props = data["props"]["pageProps"]
    except Exception as e:
        return json.dumps({"erro": f"Falha ao extrair dados: {e}"}, ensure_ascii=False)

    ads_raw = props.get("ads", [])
    anuncios = [_format_ad_summary(ad) for ad in ads_raw if ad.get("listId")]

    result = {
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
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=REQUEST_TIMEOUT, http2=HTTP2) as client:
            resp = await client.get(params.url, headers=HEADERS)
            resp.raise_for_status()
            html = resp.text
    except Exception as e:
        return json.dumps({"erro": _handle_http_error(e)}, ensure_ascii=False)

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
        }
        return json.dumps(result, ensure_ascii=False, indent=2)

    except Exception as e:
        return json.dumps({"erro": f"Falha ao processar anúncio: {type(e).__name__}: {e}"}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
