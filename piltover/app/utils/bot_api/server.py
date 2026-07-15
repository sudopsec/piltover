from __future__ import annotations

import asyncio
import json
import re
from email.parser import BytesParser
from email.policy import HTTP
from typing import Any
from urllib.parse import parse_qs, unquote
from loguru import logger

from piltover.app.utils.bot_api.auth import resolve_bot_token
from piltover.app.utils.bot_api.methods import dispatch_method
from piltover.app.utils.bot_api.response import api_error, http_response
from piltover.db.enums import FileType
from piltover.db.models import File

_BOT_PATH_RE = re.compile(r"^/bot([^/]+)/([^/]+)/?$")
_FILE_PATH_RE = re.compile(r"^/file/bot([^/]+)/(.+?)/?$")


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


def _parse_multipart(content_type: str, body: bytes) -> tuple[dict[str, Any], dict[str, tuple[str, bytes, str | None]]]:
    msg = BytesParser(policy=HTTP).parsebytes(
        f"Content-Type: {content_type}\r\n\r\n".encode("ascii") + body
    )
    params: dict[str, Any] = {}
    files: dict[str, tuple[str, bytes, str | None]] = {}

    if not msg.is_multipart():
        return params, files

    for part in msg.iter_parts():
        disposition = part.get("Content-Disposition", "")
        if "form-data" not in disposition:
            continue

        name_match = re.search(r'name="([^"]*)"', disposition)
        if not name_match:
            continue
        name = name_match.group(1)
        payload = part.get_payload(decode=True) or b""
        filename_match = re.search(r'filename="([^"]*)"', disposition)
        if filename_match:
            files[name] = (filename_match.group(1), payload, part.get_content_type())
        else:
            params[name] = payload.decode("utf-8", errors="surrogateescape")

    return params, files


def _parse_params(
        http_method: str, headers: dict[str, str], path: str, body: bytes,
) -> dict[str, Any]:
    params: dict[str, Any] = {}

    if "?" in path:
        _, query = path.split("?", 1)
        for key, values in parse_qs(query, keep_blank_values=True).items():
            params[key] = values[-1] if len(values) == 1 else values

    content_type = headers.get("content-type", "")
    if http_method in ("POST", "PUT", "PATCH") and body:
        if "application/json" in content_type:
            data = json.loads(body.decode("utf-8") or "{}")
            if isinstance(data, dict):
                params.update(data)
        elif "multipart/form-data" in content_type:
            fields, files = _parse_multipart(content_type, body)
            params.update(fields)
            if files:
                params["_files"] = files
        elif "application/x-www-form-urlencoded" in content_type or not content_type:
            for key, values in parse_qs(body.decode("utf-8"), keep_blank_values=True).items():
                params[key] = values[-1] if len(values) == 1 else values

    return params


async def _serve_file(token: str, file_path: str) -> bytes:
    resolved = await resolve_bot_token(token)
    if resolved is None:
        return http_response(api_error("Unauthorized", error_code=401))

    file_id_str = file_path.split(".", 1)[0]
    if not file_id_str.isdigit():
        return http_response(api_error("Not Found", error_code=404))

    file = await File.get_or_none(id=int(file_id_str), type__not=FileType.ENCRYPTED)
    if file is None:
        return http_response(api_error("Not Found", error_code=404))

    from piltover.app.app import app
    if app._worker is None:
        return http_response(api_error("Internal Server Error", error_code=500))

    storage = app._worker._storage
    if file.type is FileType.PHOTO:
        component = storage.photos
    else:
        component = storage.documents

    location = await component.get_location(file.physical_id)
    try:
        with open(location, "rb") as fh:
            data = fh.read()
    except OSError:
        return http_response(api_error("Not Found", error_code=404))

    headers = [
        "HTTP/1.1 200 OK",
        f"Content-Length: {len(data)}",
        f"Content-Type: {file.mime_type or 'application/octet-stream'}",
        "Connection: close",
        "",
        "",
    ]
    return "\r\n".join(headers).encode("ascii") + data


async def _handle_bot_api_request(http_method: str, path: str, headers: dict[str, str], body: bytes) -> bytes:
    path_only = path.split("?", 1)[0]
    path_only = unquote(path_only)

    file_match = _FILE_PATH_RE.match(path_only)
    if file_match is not None:
        if http_method != "GET":
            return http_response(api_error("Method not allowed", error_code=405))
        return await _serve_file(file_match.group(1), file_match.group(2))

    match = _BOT_PATH_RE.match(path_only)
    if match is None:
        return http_response(api_error("Not Found", error_code=404))

    token, api_method = match.group(1), match.group(2)
    resolved = await resolve_bot_token(token)
    if resolved is None:
        return http_response(api_error("Unauthorized", error_code=401))

    bot, bot_user = resolved
    params = _parse_params(http_method, headers, path, body)

    if http_method not in ("GET", "POST"):
        return http_response(api_error("Method not allowed", error_code=405))

    try:
        result = await dispatch_method(bot, bot_user, api_method, params)
    except Exception as exc:
        logger.opt(exception=exc).error("Bot API method {} failed for bot {}", api_method, bot_user.id)
        return http_response(api_error("Internal Server Error", error_code=500))

    return http_response(result)


async def _handle_connection(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        http_method, path, headers, body = await _read_http_request(reader)
        writer.write(await _handle_bot_api_request(http_method, path, headers, body))
        await writer.drain()
    except Exception as exc:
        logger.opt(exception=exc).warning("Bot API request failed")
        try:
            writer.write(http_response(api_error("Bad Request", error_code=400)))
            await writer.drain()
        except Exception:
            pass
    finally:
        writer.close()
        await writer.wait_closed()


async def start_bot_api_server(host: str, port: int) -> asyncio.Server:
    server = await asyncio.start_server(_handle_connection, host, port)
    logger.info("Bot API server listening on http://{}:{}/bot<token>/<method>", host, port)
    return server