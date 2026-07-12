from __future__ import annotations

import asyncio
import json
from typing import Any

from loguru import logger

from piltover.app.utils.group_calls import handle_sfu_speaking_callback


async def _read_http_request(reader: asyncio.StreamReader) -> tuple[str, str, dict[str, str], bytes]:
    request_line = await reader.readline()
    if not request_line:
        raise ValueError("empty request")

    parts = request_line.decode("latin1").strip().split()
    if len(parts) < 2:
        raise ValueError(f"invalid request line: {request_line!r}")

    method, path = parts[0], parts[1]
    headers: dict[str, str] = {}
    while True:
        line = await reader.readline()
        if line in (b"\r\n", b"\n", b""):
            break
        if b":" not in line:
            continue
        name, value = line.decode("latin1").split(":", 1)
        headers[name.strip().lower()] = value.strip()

    content_length = int(headers.get("content-length", "0") or 0)
    body = await reader.readexactly(content_length) if content_length else b""
    return method, path, headers, body


def _http_response(status: int, body: dict[str, Any] | None = None) -> bytes:
    payload = b""
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
    headers = [
        f"HTTP/1.1 {status}",
        f"Content-Length: {len(payload)}",
        "Content-Type: application/json",
        "Connection: close",
        "",
        "",
    ]
    return "\r\n".join(headers).encode("ascii") + payload


async def _handle_connection(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        method, path, _headers, body = await _read_http_request(reader)
        if method == "POST" and path == "/api/group-call-speaking":
            data = json.loads(body.decode("utf-8") or "{}")
            room_id = int(data["roomId"])
            peer_id = int(data["peerId"])
            await handle_sfu_speaking_callback(room_id, peer_id)
            writer.write(_http_response(200, {"success": True}))
        else:
            writer.write(_http_response(404))
        await writer.drain()
    except Exception as exc:
        logger.opt(exception=exc).warning("SFU callback request failed")
        try:
            writer.write(_http_response(400, {"success": False}))
            await writer.drain()
        except Exception:
            pass
    finally:
        writer.close()
        await writer.wait_closed()


async def start_sfu_callback_server(host: str, port: int) -> asyncio.Server:
    server = await asyncio.start_server(_handle_connection, host, port)
    logger.info("SFU speaking callback server listening on http://{}:{}/api/group-call-speaking", host, port)
    return server