#!/usr/bin/env python3
"""Simple proxy that serves HTML and proxies WebSocket to LiveKit."""

import asyncio
import aiohttp
from aiohttp import web, WSMsgType
import os

LIVEKIT_HTTP = "http://127.0.0.1:7880"
LIVEKIT_WS = "ws://127.0.0.1:7880"
HTML_FILE = os.path.join(os.path.dirname(__file__), "livekit-test.html")
PORT = 7881


async def handle_index(request):
    """Serve the test HTML page."""
    return web.FileResponse(HTML_FILE)


async def websocket_proxy(request):
    """Proxy WebSocket to LiveKit."""
    ws_client = web.WebSocketResponse()
    await ws_client.prepare(request)

    # Build LiveKit URL with full path and query string
    path = request.path
    if request.query_string:
        path = f"{path}?{request.query_string}"

    livekit_url = f"{LIVEKIT_WS}{path}"
    print(f"Proxying WebSocket: {livekit_url}")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(livekit_url) as ws_livekit:

                async def client_to_livekit():
                    try:
                        async for msg in ws_client:
                            if msg.type == WSMsgType.TEXT:
                                await ws_livekit.send_str(msg.data)
                            elif msg.type == WSMsgType.BINARY:
                                await ws_livekit.send_bytes(msg.data)
                            elif msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                                break
                    except Exception as e:
                        print(f"client_to_livekit error: {e}")

                async def livekit_to_client():
                    try:
                        async for msg in ws_livekit:
                            if msg.type == WSMsgType.TEXT:
                                await ws_client.send_str(msg.data)
                            elif msg.type == WSMsgType.BINARY:
                                await ws_client.send_bytes(msg.data)
                            elif msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                                break
                    except Exception as e:
                        print(f"livekit_to_client error: {e}")

                await asyncio.gather(
                    client_to_livekit(),
                    livekit_to_client(),
                    return_exceptions=True
                )
    except Exception as e:
        print(f"WebSocket proxy error: {e}")

    return ws_client


async def http_proxy(request):
    """Proxy HTTP requests to LiveKit."""
    path = request.path
    if request.query_string:
        path = f"{path}?{request.query_string}"

    livekit_url = f"{LIVEKIT_HTTP}{path}"

    async with aiohttp.ClientSession() as session:
        async with session.request(
            request.method,
            livekit_url,
            headers=request.headers,
            data=await request.read()
        ) as resp:
            body = await resp.read()
            return web.Response(
                body=body,
                status=resp.status,
                headers=resp.headers
            )


async def handle_request(request):
    """Route request to appropriate handler."""
    # Serve HTML at root
    if request.path == "/" and "upgrade" not in request.headers.get("connection", "").lower():
        return await handle_index(request)

    # WebSocket upgrade
    if request.headers.get("upgrade", "").lower() == "websocket":
        return await websocket_proxy(request)

    # Other HTTP requests proxy to LiveKit
    return await http_proxy(request)


def main():
    app = web.Application()
    app.router.add_route("*", "/{path:.*}", handle_request)
    app.router.add_route("*", "/", handle_request)

    print(f"LiveKit Proxy starting on port {PORT}")
    print(f"HTML page: http://127.0.0.1:{PORT}/")
    print(f"Proxying to LiveKit: {LIVEKIT_HTTP}")

    web.run_app(app, host="0.0.0.0", port=PORT, print=None)


if __name__ == "__main__":
    main()
