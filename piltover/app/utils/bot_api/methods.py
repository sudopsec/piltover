from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from piltover.app.handlers.messages.sending import send_message_internal
from piltover.context import RequestContext, request_ctx
from piltover.app.utils.bot_api.response import api_error, api_ok
from piltover.app.utils.bot_api.serialize import message_to_bot_api, private_chat_to_bot_api, user_to_bot_api
from piltover.app.utils.bot_api.updates import _BotApiConflict, bot_api_updates
from piltover.db.enums import PeerType
from piltover.db.models import Bot, MessageRef, Peer, User, Username
from piltover.exceptions import ErrorRpc
from piltover.tl import UpdateNewMessage
from piltover.tl.types.messages import BotCallbackAnswer as MessagesBotCallbackAnswer


async def dispatch_method(bot: Bot, bot_user: User, method: str, params: dict[str, Any]) -> dict[str, Any]:
    method_lower = method.lower()

    if method_lower == "getme":
        return api_ok(await user_to_bot_api(bot_user, for_get_me=True))

    if method_lower == "getupdates":
        return await _get_updates(bot_user, params)

    if method_lower == "sendmessage":
        return await _send_message(bot_user, params)

    if method_lower == "editmessagetext":
        return await _edit_message_text(bot_user, params)

    if method_lower == "deletemessage":
        return await _delete_message(bot_user, params)

    if method_lower == "getchat":
        return await _get_chat(bot_user, params)

    if method_lower == "answercallbackquery":
        return await _answer_callback_query(bot_user, params)

    if method_lower == "answerprecheckoutquery":
        return await _answer_pre_checkout_query(bot_user, params)

    if method_lower == "setwebhook":
        return _set_webhook(bot_user, params)

    if method_lower == "deletewebhook":
        return _delete_webhook(bot_user, params)

    if method_lower == "getwebhookinfo":
        return api_ok(bot_api_updates.get_webhook_info(bot_user.id))

    return api_error(f"Not Found: method {method} not found", error_code=404)


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes")
    return bool(value)


def _parse_int(value: Any, default: int) -> int:
    if value is None:
        return default
    return int(value)


async def _get_updates(bot_user: User, params: dict[str, Any]) -> dict[str, Any]:
    offset = params.get("offset")
    if offset is not None:
        offset = int(offset)
    limit = _parse_int(params.get("limit"), 100)
    timeout = _parse_int(params.get("timeout"), 0)

    try:
        updates = await bot_api_updates.get_updates(
            bot_user.id, offset=offset, limit=limit, timeout=timeout,
        )
    except _BotApiConflict:
        return api_error("Conflict: can't use getUpdates while webhook is active", error_code=409)

    return api_ok(updates)


async def _resolve_chat_peer(bot_user: User, chat_id: Any) -> Peer | None:
    if isinstance(chat_id, str):
        username = chat_id[1:] if chat_id.startswith("@") else chat_id
        resolved = await Username.get_or_none(username=username).select_related("user")
        if resolved is None or resolved.user_id is None:
            return None
        chat_id = resolved.user_id

    chat_id = int(chat_id)
    return await Peer.get_or_create_for_user(
        bot_user.id, chat_id, select_related=("user", "user__username"),
    )


def _worker_context(bot_user: User, auth_id: int):
    from piltover.app.app import app

    if app._worker is None:
        return None
    return request_ctx.set(RequestContext(
        0, None, 0, 0, None, 201, auth_id, bot_user.id,
        app._worker, app._worker._storage,
    ))


async def _send_message(bot_user: User, params: dict[str, Any]) -> dict[str, Any]:
    chat_id = params.get("chat_id")
    text = params.get("text")
    if chat_id is None:
        return api_error("Bad Request: chat_id is required")
    if text is None:
        return api_error("Bad Request: text is required")

    peer = await _resolve_chat_peer(bot_user, chat_id)
    if peer is None:
        return api_error("Bad Request: chat not found")

    if peer.type is not PeerType.USER:
        return api_error("Bad Request: only private chats are supported")

    reply_to_message_id = params.get("reply_to_message_id")
    if reply_to_message_id is None:
        reply_params = params.get("reply_parameters")
        if isinstance(reply_params, dict):
            reply_to_message_id = reply_params.get("message_id")

    from piltover.db.models import UserAuthorization

    auth = await UserAuthorization.get_or_none(user_id=bot_user.id)
    ctx_token = _worker_context(bot_user, auth.id if auth is not None else 0)
    if ctx_token is None:
        return api_error("Internal error: worker is not available", error_code=500)

    try:
        updates = await send_message_internal(
            user=bot_user,
            peer=peer,
            random_id=None,
            reply_to_message_id=int(reply_to_message_id) if reply_to_message_id is not None else None,
            clear_draft=False,
            author=bot_user,
            text=str(text),
            opposite=True,
        )
    except ErrorRpc as exc:
        return api_error(f"Bad Request: {exc.error_message}", error_code=exc.error_code)
    finally:
        request_ctx.reset(ctx_token)

    for update in updates.updates:
        if isinstance(update, UpdateNewMessage):
            message_ref = await MessageRef.get(id=update.message.id).select_related(
                "content", "content__author", "peer", "peer__user",
            )
            return api_ok(await message_to_bot_api(bot_user, message_ref.peer, message_ref))

    message_ref = await MessageRef.filter(peer=peer).order_by("-id").first().select_related(
        "content", "content__author", "peer", "peer__user",
    )
    if message_ref is None:
        return api_error("Internal error: message was not created", error_code=500)
    return api_ok(await message_to_bot_api(bot_user, message_ref.peer, message_ref))


async def _edit_message_text(bot_user: User, params: dict[str, Any]) -> dict[str, Any]:
    chat_id = params.get("chat_id")
    message_id = params.get("message_id")
    text = params.get("text")
    if chat_id is None:
        return api_error("Bad Request: chat_id is required")
    if message_id is None:
        return api_error("Bad Request: message_id is required")
    if text is None:
        return api_error("Bad Request: text is required")

    peer = await _resolve_chat_peer(bot_user, chat_id)
    if peer is None:
        return api_error("Bad Request: chat not found")
    if peer.type is not PeerType.USER:
        return api_error("Bad Request: only private chats are supported")

    message = await MessageRef.get_or_none(id=int(message_id), peer=peer).select_related("content")
    if message is None or message.content.author_id != bot_user.id:
        return api_error("Bad Request: message to edit not found")

    if message.content.message == text:
        return api_error("Bad Request: message is not modified")

    import piltover.app.utils.updates_manager as upd

    from piltover.app.utils.utils import process_message_entities

    message.content.message = str(text)
    message.content.entities = await process_message_entities(str(text), None, bot_user.id)
    message.content.edit_date = datetime.now(UTC)
    message.content.version += 1
    await message.content.save(update_fields=["message", "entities", "edit_date", "version"])

    opposite_peer = await Peer.get_or_create_for_user(peer.user_id, bot_user.id)
    refs = await MessageRef.filter(
        content_id=message.content_id,
        peer_id__in=[peer.id, opposite_peer.id],
    ).select_related("content", "content__author", "peer", "peer__user")

    await upd.edit_message(bot_user.id, {ref.peer: ref for ref in refs})
    return api_ok(await message_to_bot_api(bot_user, peer, message))


async def _delete_message(bot_user: User, params: dict[str, Any]) -> dict[str, Any]:
    chat_id = params.get("chat_id")
    message_id = params.get("message_id")
    if chat_id is None:
        return api_error("Bad Request: chat_id is required")
    if message_id is None:
        return api_error("Bad Request: message_id is required")

    peer = await _resolve_chat_peer(bot_user, chat_id)
    if peer is None:
        return api_error("Bad Request: chat not found")
    if peer.type is not PeerType.USER:
        return api_error("Bad Request: only private chats are supported")

    message = await MessageRef.get_or_none(id=int(message_id), peer=peer)
    if message is None:
        return api_error("Bad Request: message can't be deleted")

    import piltover.app.utils.updates_manager as upd

    content_id = message.content_id
    peer_id = message.peer_id
    all_messages = await MessageRef.filter(content_id=content_id).select_related("peer")
    messages_by_owner: dict[int, list[int]] = defaultdict(list)
    for ref in all_messages:
        if ref.peer.owner_id is not None:
            messages_by_owner[ref.peer.owner_id].append(ref.id)

    await MessageRef.filter(content_id=content_id).delete()
    await Peer.sync_last_message_bulk([peer_id, *(ref.peer_id for ref in all_messages)])
    await upd.delete_messages(bot_user.id, messages_by_owner)
    return api_ok(True)


async def _get_chat(bot_user: User, params: dict[str, Any]) -> dict[str, Any]:
    chat_id = params.get("chat_id")
    if chat_id is None:
        return api_error("Bad Request: chat_id is required")

    peer = await _resolve_chat_peer(bot_user, chat_id)
    if peer is None:
        return api_error("Bad Request: chat not found")
    if peer.type is not PeerType.USER:
        return api_error("Bad Request: only private chats are supported")

    return api_ok(await private_chat_to_bot_api(peer))


async def _answer_callback_query(bot_user: User, params: dict[str, Any]) -> dict[str, Any]:
    query_id = params.get("callback_query_id")
    if query_id is None:
        return api_error("Bad Request: callback_query_id is required")

    from piltover.app.app import app

    if app._worker is None:
        return api_error("Internal error: worker is not available", error_code=500)

    answer = MessagesBotCallbackAnswer(
        alert=_parse_bool(params.get("show_alert")),
        has_url=params.get("url") is not None,
        native_ui=True,
        message=str(params["text"]) if params.get("text") is not None else None,
        url=str(params["url"]) if params.get("url") is not None else None,
        cache_time=_parse_int(params.get("cache_time"), 0),
    )
    await app._worker.pubsub.notify(
        topic=f"bot-callback-query/{int(query_id)}",
        data=answer.write(),
    )
    return api_ok(True)


async def _answer_pre_checkout_query(bot_user: User, params: dict[str, Any]) -> dict[str, Any]:
    query_id = params.get("pre_checkout_query_id")
    if query_id is None:
        return api_error("Bad Request: pre_checkout_query_id is required")

    from piltover.app.app import app

    if app._worker is None:
        return api_error("Internal error: worker is not available", error_code=500)

    ok = _parse_bool(params.get("ok", True))
    if ok:
        data = b"1"
    else:
        data = str(params.get("error_message") or "PAYMENT_FAILED").encode("utf-8")

    await app._worker.pubsub.notify(
        topic=f"bot-precheckout-query/{int(query_id)}",
        data=data,
    )
    return api_ok(True)


def _set_webhook(bot_user: User, params: dict[str, Any]) -> dict[str, Any]:
    url = params.get("url")
    if url is None:
        return api_error("Bad Request: url is required")

    allowed_updates = params.get("allowed_updates")
    if isinstance(allowed_updates, str):
        import json
        allowed_updates = json.loads(allowed_updates)

    bot_api_updates.set_webhook(
        bot_user.id,
        str(url),
        drop_pending_updates=_parse_bool(params.get("drop_pending_updates")),
        allowed_updates=allowed_updates,
        max_connections=int(params["max_connections"]) if params.get("max_connections") is not None else None,
        ip_address=str(params["ip_address"]) if params.get("ip_address") is not None else None,
    )
    return api_ok(True)


def _delete_webhook(bot_user: User, params: dict[str, Any]) -> dict[str, Any]:
    bot_api_updates.delete_webhook(
        bot_user.id,
        drop_pending_updates=_parse_bool(params.get("drop_pending_updates")),
    )
    return api_ok(True)