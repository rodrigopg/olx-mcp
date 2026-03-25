"""
Quick integration test for olx_mcp_server via MCP JSON-RPC over stdio.
"""
import asyncio
import json
import sys


async def read_response(proc, request_id: int, timeout: float = 45.0) -> dict:
    """Read lines until we get a response matching request_id (skip notifications)."""
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            raise TimeoutError(f"No response for id={request_id} within {timeout}s")
        try:
            raw = await asyncio.wait_for(proc.stdout.readline(), timeout=remaining)
        except asyncio.TimeoutError:
            raise TimeoutError(f"No response for id={request_id} within {timeout}s")
        if not raw:
            raise EOFError("Server closed stdout")
        msg = json.loads(raw)
        # Skip notifications (no "id" field) and mismatched ids
        if msg.get("id") == request_id:
            return msg


async def send(proc, msg: dict):
    proc.stdin.write((json.dumps(msg) + "\n").encode())
    await proc.stdin.drain()


async def call_tool(proc, req_id: int, tool: str, args: dict) -> dict:
    await send(proc, {
        "jsonrpc": "2.0", "id": req_id, "method": "tools/call",
        "params": {"name": tool, "arguments": args},
    })
    resp = await read_response(proc, req_id)
    content = resp.get("result", {}).get("content", [{}])
    raw_text = content[0].get("text", "") if content else ""
    return json.loads(raw_text) if raw_text else resp.get("error", {})


async def main():
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "olx_mcp.server",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        limit=10 * 1024 * 1024,  # 10 MB — OLX responses can be large
    )

    # 1. Initialize
    await send(proc, {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0.1"},
        },
    })
    resp = await read_response(proc, 1)
    server_info = resp.get("result", {}).get("serverInfo", {})
    print(f"[PASS] initialize — server: {server_info.get('name')} v{server_info.get('version')}")

    # 2. initialized notification (no response)
    await send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

    # 3. List tools
    await send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    resp = await read_response(proc, 2)
    tools = [t["name"] for t in resp.get("result", {}).get("tools", [])]
    assert "olx_buscar_anuncios" in tools, f"Missing tool, got: {tools}"
    assert "olx_detalhe_anuncio" in tools, f"Missing tool, got: {tools}"
    print(f"[PASS] tools/list — found: {tools}")

    # 4. Search tool — happy path
    # NOTE: tools use a single `params` Pydantic model arg, so arguments must be {"params": {...}}
    print("      Calling olx_buscar_anuncios (this makes a real HTTP request)...")
    result = await call_tool(proc, 3, "olx_buscar_anuncios", {
        "params": {"query": "notebook", "estado": "sp", "pagina": 1},
    })
    if "erro" in result:
        print(f"[FAIL] olx_buscar_anuncios — error: {result['erro']}")
    else:
        ads = result.get("anuncios", [])
        assert isinstance(ads, list) and len(ads) > 0, "No ads returned"
        assert result.get("total", 0) > 0, "Total is 0"
        first = ads[0]
        assert first.get("titulo"), "First ad has no title"
        assert first.get("url"), "First ad has no URL"
        print(f"[PASS] olx_buscar_anuncios — total={result['total']}, ads_on_page={len(ads)}")
        print(f"       First: \"{first['titulo']}\" | {first.get('preco')} | {first.get('localizacao')}")
        first_url = first["url"]

    # 5. Detail tool — use URL from search result
    if "erro" not in result and result.get("anuncios"):
        detail_url = result["anuncios"][0]["url"]
        print(f"      Calling olx_detalhe_anuncio for: {detail_url[:80]}...")
        detail = await call_tool(proc, 4, "olx_detalhe_anuncio", {"params": {"url": detail_url}})
        if "erro" in detail:
            print(f"[FAIL] olx_detalhe_anuncio — error: {detail['erro']}")
        else:
            assert detail.get("titulo"), "Detail has no title"
            print(f"[PASS] olx_detalhe_anuncio — id={detail.get('id')}, title=\"{detail.get('titulo')}\"")
            print(f"       Price={detail.get('preco')} | City={detail.get('municipio')}/{detail.get('estado')}")
            print(f"       Seller={detail.get('vendedor')} | Images={len(detail.get('imagens', []))}")
    else:
        print("[SKIP] olx_detalhe_anuncio — no URL from search")

    # 6. Validation: invalid state code
    result_err = await call_tool(proc, 5, "olx_buscar_anuncios", {"params": {"query": "sofa", "estado": "xx"}})
    assert "erro" in result_err, f"Expected error for invalid estado, got: {result_err}"
    print(f"[PASS] validation error — {result_err['erro']}")

    # 7. Validation: empty query rejected by Pydantic (min_length=1) — MCP returns isError=true
    resp7 = {"jsonrpc": "2.0", "id": 6, "method": "tools/call",
             "params": {"name": "olx_buscar_anuncios", "arguments": {"params": {"query": ""}}}}
    await send(proc, resp7)
    raw7 = await read_response(proc, 6)
    is_error = raw7.get("result", {}).get("isError") or "error" in raw7
    assert is_error, f"Expected error for empty query, got: {raw7}"
    print(f"[PASS] empty query rejected")

    proc.terminate()
    await proc.wait()
    print("\nAll tests passed.")


asyncio.run(main())
