from __future__ import annotations

import inspect
import json
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from piltover.app.handlers.messages.sending import send_message_internal
from piltover.app.utils.bot_api.entities import bot_api_entities_to_tl, parse_entities_param
from piltover.app.utils.bot_api.parse_mode import parse_text_mode
from piltover.app.utils.bot_api.media import (
    make_dice_media, make_message_media, pick_uploaded_file, process_outgoing_reply_markup, resolve_bot_api_file,
)
from piltover.app.utils.bot_api.errors import translate_exception
from piltover.app.utils.bot_api.response import api_error, api_ok
from piltover.app.utils.bot_api.peers import peer_is_writable, resolve_bot_api_peer
from piltover.app.utils.bot_api.reply import resolve_outgoing_reply
from piltover.app.utils.bot_api.serialize import (
    bot_command_to_bot_api, get_file_result, message_to_bot_api, peer_to_bot_api, user_to_bot_api,
)
from piltover.app.utils.bot_api.updates import bot_api_updates
from piltover.app.utils.utils import _validate_message_entities, process_message_entities
from piltover.context import RequestContext, request_ctx
from piltover.db.enums import FileType, MediaType, PeerType
from piltover.db.models import Bot, BotCommand, ChatParticipant, File, MessageFwdHeader, MessageRef, Peer, User, UserAuthorization
from piltover.session import SessionManager
from piltover.tl import (
    SendMessageCancelAction, SendMessageRecordAudioAction, SendMessageRecordRoundAction,
    SendMessageRecordVideoAction, SendMessageTypingAction, SendMessageUploadAudioAction,
    SendMessageUploadDocumentAction, SendMessageUploadPhotoAction, SendMessageUploadRoundAction,
    SendMessageUploadVideoAction, UpdateChannelUserTyping, UpdateChatUserTyping, UpdateNewMessage,
    UpdateUserTyping,
)
import piltover.app.utils.updates_manager as upd


_CHAT_ACTIONS = {
    "typing": SendMessageTypingAction(),
    "upload_photo": SendMessageUploadPhotoAction(progress=0),
    "record_video": SendMessageRecordVideoAction(),
    "upload_video": SendMessageUploadVideoAction(progress=0),
    "record_voice": SendMessageRecordAudioAction(),
    "upload_voice": SendMessageUploadAudioAction(progress=0),
    "upload_document": SendMessageUploadDocumentAction(progress=0),
    "upload_video_note": SendMessageUploadRoundAction(progress=0),
    "record_video_note": SendMessageRecordRoundAction(),
    "cancel": SendMessageCancelAction(),
}


async def dispatch_method(bot: Bot, bot_user: User, method: str, params: dict[str, Any]) -> dict[str, Any]:
    method_lower = method.lower()

    handlers = {
        "getme": lambda: _get_me(bot_user),
        "getupdates": lambda: _get_updates(bot_user, params),
        "sendmessage": lambda: _send_message(bot_user, params),
        "sendphoto": lambda: _send_media(bot_user, params, "photo", FileType.PHOTO, MediaType.PHOTO),
        "senddocument": lambda: _send_media(bot_user, params, "document", FileType.DOCUMENT, MediaType.DOCUMENT),
        "sendvideo": lambda: _send_media(bot_user, params, "video", FileType.DOCUMENT_VIDEO, MediaType.DOCUMENT),
        "sendaudio": lambda: _send_media(bot_user, params, "audio", FileType.DOCUMENT_AUDIO, MediaType.DOCUMENT),
        "sendvoice": lambda: _send_media(bot_user, params, "voice", FileType.DOCUMENT_VOICE, MediaType.DOCUMENT),
        "sendvideonote": lambda: _send_media(
            bot_user, params, "video_note", FileType.DOCUMENT_VIDEO_NOTE, MediaType.DOCUMENT,
        ),
        "sendanimation": lambda: _send_media(bot_user, params, "animation", FileType.DOCUMENT_GIF, MediaType.DOCUMENT),
        "senddice": lambda: _send_dice(bot_user, params),
        "editmessagetext": lambda: _edit_message_text(bot_user, params),
        "editmessagecaption": lambda: _edit_message_caption(bot_user, params),
        "editmessagereplymarkup": lambda: _edit_message_reply_markup(bot_user, params),
        "deletemessage": lambda: _delete_message(bot_user, params),
        "forwardmessage": lambda: _forward_message(bot_user, params),
        "getchat": lambda: _get_chat(bot_user, params),
        "getfile": lambda: _get_file(params),
        "sendchataction": lambda: _send_chat_action(bot_user, params),
        "setmycommands": lambda: _set_my_commands(bot_user, params),
        "getmycommands": lambda: _get_my_commands(bot_user, params),
        "deletemycommands": lambda: _delete_my_commands(bot_user, params),
        "answercallbackquery": lambda: _answer_callback_query(bot_user, params),
        "answerprecheckoutquery": lambda: _answer_pre_checkout_query(bot_user, params),
        "setwebhook": lambda: _set_webhook(bot_user, params),
        "deletewebhook": lambda: _delete_webhook(bot_user, params),
        "getwebhookinfo": lambda: api_ok(bot_api_updates.get_webhook_info(bot_user.id)),
    }

    handler = handlers.get(method_lower)
    if handler is None:
        return api_error(f"Not Found: method {method} not found", error_code=404)

    try:
        result = handler()
        if inspect.isawaitable(result):
            return await result
        return result
    except Exception as exc:
        translated = translate_exception(exc)
        if translated is not None:
            return translated
        return api_error("Internal Server Error", error_code=500)


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes")
    return bool(value)


def _parse_int(value: Any, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        raise ValueError("invalid integer value")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid integer value") from exc


def _require_int(value: Any, *, field_name: str) -> int:
    if value is None:
        raise ValueError(f"{field_name} is required")
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer") from exc


async def _get_me(bot_user: User) -> dict[str, Any]:
    return api_ok(await user_to_bot_api(bot_user, for_get_me=True))


async def _get_updates(bot_user: User, params: dict[str, Any]) -> dict[str, Any]:
    offset = params.get("offset")
    if offset is not None:
        offset = int(offset)
    limit = _parse_int(params.get("limit"), 100)
    timeout = _parse_int(params.get("timeout"), 0)

    allowed_updates = params.get("allowed_updates")
    if isinstance(allowed_updates, str):
        allowed_updates = json.loads(allowed_updates)

    updates = await bot_api_updates.get_updates(
        bot_user.id, offset=offset, limit=limit, timeout=timeout, allowed_updates=allowed_updates,
    )
    return api_ok(updates)


async def _require_writable_peer(bot_user: User, chat_id: Any) -> Peer | dict[str, Any]:
    peer = await resolve_bot_api_peer(bot_user, chat_id)
    if peer is None:
        return api_error("Bad Request: chat not found")
    if not await peer_is_writable(bot_user, peer):
        return api_error("Bad Request: bot is not allowed to send messages in this chat", error_code=403)
    return peer


def _worker_context(bot_user: User, auth_id: int):
    from piltover.app.runtime import get_worker

    worker = get_worker()
    if worker is None:
        return None
    return request_ctx.set(RequestContext(
        0, None, 0, 0, None, 201, auth_id, bot_user.id,
        worker, worker._storage,
    ))


async def _resolve_reply(
        bot_user: User, peer: Peer, params: dict[str, Any],
) -> tuple[int | None, MessageRef | None, int | None] | dict[str, Any]:
    resolved = await resolve_outgoing_reply(bot_user, peer, params)
    if isinstance(resolved, dict):
        return resolved
    return resolved.message_id, resolved.reply_to, resolved.top_msg_id


async def _prepare_outgoing_text(
        bot_user: User,
        params: dict[str, Any],
        text: str | None,
        *,
        mode_field: str = "parse_mode",
        entities_field: str = "entities",
) -> tuple[str | None, list[dict] | None] | dict[str, Any]:
    parse_mode = params.get(mode_field)
    raw_entities = params.get(entities_field)

    if raw_entities is not None and parse_mode:
        return api_error("Bad Request: can't parse entities: parse_mode and entities are mutually exclusive")

    prepared_text = text
    if parse_mode and prepared_text:
        prepared_text, tl_entities = parse_text_mode(prepared_text, str(parse_mode))
        return prepared_text, await _validate_message_entities(prepared_text, tl_entities, bot_user.id)

    if raw_entities is not None:
        entities_list = parse_entities_param(raw_entities, field_name=entities_field)
        tl_entities = bot_api_entities_to_tl(entities_list or [])
        return prepared_text, await _validate_message_entities(prepared_text, tl_entities, bot_user.id)

    return prepared_text, await process_message_entities(prepared_text, None, bot_user.id)


async def _extract_sent_message(bot_user: User, peer: Peer, updates) -> dict[str, Any]:
    for update in updates.updates:
        if isinstance(update, UpdateNewMessage):
            message_ref = await MessageRef.get(id=update.message.id).select_related(
                "content", "content__author", "peer", "peer__user", "content__media", "content__media__file",
            )
            return api_ok(await message_to_bot_api(bot_user, message_ref.peer, message_ref))

    message_ref = await MessageRef.filter(peer=peer).order_by("-id").first().select_related(
        "content", "content__author", "peer", "peer__user", "content__media", "content__media__file",
    )
    if message_ref is None:
        return api_error("Internal error: message was not created", error_code=500)
    return api_ok(await message_to_bot_api(bot_user, message_ref.peer, message_ref))


async def _send_message(bot_user: User, params: dict[str, Any]) -> dict[str, Any]:
    chat_id = params.get("chat_id")
    text = params.get("text")
    if chat_id is None:
        return api_error("Bad Request: chat_id is required")
    if text is None:
        return api_error("Bad Request: text is required")

    peer = await _require_writable_peer(bot_user, chat_id)
    if isinstance(peer, dict):
        return peer

    reply = await _resolve_reply(bot_user, peer, params)
    if isinstance(reply, dict):
        return reply
    reply_to_message_id, reply_to, top_msg_id = reply

    auth = await UserAuthorization.get_or_none(user_id=bot_user.id)
    ctx_token = _worker_context(bot_user, auth.id if auth is not None else 0)
    if ctx_token is None:
        return api_error("Internal error: worker is not available", error_code=500)

    try:
        prepared = await _prepare_outgoing_text(bot_user, params, str(text))
        if isinstance(prepared, dict):
            return prepared
        text, entities = prepared
        reply_markup = await process_outgoing_reply_markup(bot_user, params)
        updates = await send_message_internal(
            user=bot_user,
            peer=peer,
            random_id=None,
            reply_to_message_id=reply_to_message_id,
            clear_draft=False,
            author=bot_user,
            text=text,
            entities=entities,
            reply_markup=reply_markup,
            opposite=True,
            top_msg_id=top_msg_id,
            reply_to=reply_to,
        )
    finally:
        request_ctx.reset(ctx_token)

    return await _extract_sent_message(bot_user, peer, updates)


async def _send_media(
        bot_user: User, params: dict[str, Any], field_name: str,
        file_type: FileType, media_type: MediaType,
) -> dict[str, Any]:
    chat_id = params.get("chat_id")
    if chat_id is None:
        return api_error("Bad Request: chat_id is required")

    peer = await _require_writable_peer(bot_user, chat_id)
    if isinstance(peer, dict):
        return peer

    reply = await _resolve_reply(bot_user, peer, params)
    if isinstance(reply, dict):
        return reply
    reply_to_message_id, reply_to, top_msg_id = reply

    uploaded = pick_uploaded_file(params, field_name)
    file_ref = params.get(field_name) if uploaded is None else None

    mime_defaults = {
        FileType.PHOTO: "image/jpeg",
        FileType.DOCUMENT: "application/octet-stream",
        FileType.DOCUMENT_VIDEO: "video/mp4",
        FileType.DOCUMENT_AUDIO: "audio/mpeg",
        FileType.DOCUMENT_VOICE: "audio/ogg",
        FileType.DOCUMENT_VIDEO_NOTE: "video/mp4",
        FileType.DOCUMENT_GIF: "video/mp4",
    }

    auth = await UserAuthorization.get_or_none(user_id=bot_user.id)
    ctx_token = _worker_context(bot_user, auth.id if auth is not None else 0)
    if ctx_token is None:
        return api_error("Internal error: worker is not available", error_code=500)

    try:
        file = await resolve_bot_api_file(
            bot_user, file_ref, uploaded,
            default_mime=mime_defaults.get(file_type, "application/octet-stream"),
            file_type=file_type,
        )
        media = await make_message_media(file, media_type=media_type)
        caption = params.get("caption")
        prepared = await _prepare_outgoing_text(
            bot_user, params, str(caption) if caption else None, mode_field="caption_parse_mode", entities_field="caption_entities",
        )
        if isinstance(prepared, dict):
            return prepared
        caption, entities = prepared
        reply_markup = await process_outgoing_reply_markup(bot_user, params)

        updates = await send_message_internal(
            user=bot_user,
            peer=peer,
            random_id=None,
            reply_to_message_id=reply_to_message_id,
            clear_draft=False,
            author=bot_user,
            text=caption,
            entities=entities,
            media=media,
            reply_markup=reply_markup,
            opposite=True,
            top_msg_id=top_msg_id,
            reply_to=reply_to,
        )
    finally:
        request_ctx.reset(ctx_token)

    return await _extract_sent_message(bot_user, peer, updates)


async def _send_dice(bot_user: User, params: dict[str, Any]) -> dict[str, Any]:
    chat_id = params.get("chat_id")
    if chat_id is None:
        return api_error("Bad Request: chat_id is required")

    peer = await _require_writable_peer(bot_user, chat_id)
    if isinstance(peer, dict):
        return peer

    reply = await _resolve_reply(bot_user, peer, params)
    if isinstance(reply, dict):
        return reply
    reply_to_message_id, reply_to, top_msg_id = reply

    emoji = params.get("emoji")
    if emoji is None:
        emoji = "\U0001F3B2"
    elif not isinstance(emoji, str) or not emoji:
        return api_error("Bad Request: invalid dice emoji")

    auth = await UserAuthorization.get_or_none(user_id=bot_user.id)
    ctx_token = _worker_context(bot_user, auth.id if auth is not None else 0)
    if ctx_token is None:
        return api_error("Internal error: worker is not available", error_code=500)

    try:
        media = await make_dice_media(emoji)
        reply_markup = await process_outgoing_reply_markup(bot_user, params)
        updates = await send_message_internal(
            user=bot_user,
            peer=peer,
            random_id=None,
            reply_to_message_id=reply_to_message_id,
            clear_draft=False,
            author=bot_user,
            text=None,
            media=media,
            reply_markup=reply_markup,
            opposite=True,
            top_msg_id=top_msg_id,
            reply_to=reply_to,
        )
    finally:
        request_ctx.reset(ctx_token)

    return await _extract_sent_message(bot_user, peer, updates)


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

    peer = await resolve_bot_api_peer(bot_user, chat_id)
    if peer is None:
        return api_error("Bad Request: chat not found")

    message = await MessageRef.get_or_none(
        id=_require_int(message_id, field_name="message_id"), peer=peer,
    ).select_related("content")
    if message is None or message.content.author_id != bot_user.id:
        return api_error("Bad Request: message to edit not found")

    prepared = await _prepare_outgoing_text(bot_user, params, str(text))
    if isinstance(prepared, dict):
        return prepared
    text, entities = prepared

    if message.content.message == text and message.content.entities == entities:
        return api_error("Bad Request: message is not modified")

    message.content.message = text
    message.content.entities = entities
    message.content.edit_date = datetime.now(UTC)
    message.content.edit_hide = True
    message.content.version += 1
    await message.content.save(update_fields=["message", "entities", "edit_date", "edit_hide", "version"])

    opposite_peer = await Peer.get_or_create_for_user(peer.user_id, bot_user.id)
    refs = await MessageRef.filter(
        content_id=message.content_id,
        peer_id__in=[peer.id, opposite_peer.id],
    ).select_related("content", "content__author", "peer", "peer__user")

    await upd.edit_message(bot_user.id, {ref.peer: ref for ref in refs})
    return api_ok(await message_to_bot_api(bot_user, peer, message))


async def _edit_message_caption(bot_user: User, params: dict[str, Any]) -> dict[str, Any]:
    chat_id = params.get("chat_id")
    message_id = params.get("message_id")
    caption = params.get("caption")
    if chat_id is None:
        return api_error("Bad Request: chat_id is required")
    if message_id is None:
        return api_error("Bad Request: message_id is required")

    peer = await resolve_bot_api_peer(bot_user, chat_id)
    if peer is None:
        return api_error("Bad Request: chat not found")

    message = await MessageRef.get_or_none(
        id=_require_int(message_id, field_name="message_id"), peer=peer,
    ).select_related("content")
    if message is None or message.content.author_id != bot_user.id:
        return api_error("Bad Request: message to edit not found")
    if message.content.media_id is None:
        return api_error("Bad Request: there is no caption in the message to edit")

    new_caption = "" if caption is None else str(caption)
    prepared = await _prepare_outgoing_text(
        bot_user, params, new_caption or None, mode_field="caption_parse_mode", entities_field="caption_entities",
    )
    if isinstance(prepared, dict):
        return prepared
    new_caption, entities = prepared

    if message.content.message == (new_caption or None) and message.content.entities == entities:
        return api_error("Bad Request: message is not modified")

    message.content.message = new_caption or None
    message.content.entities = entities
    message.content.edit_date = datetime.now(UTC)
    message.content.edit_hide = True
    message.content.version += 1
    await message.content.save(update_fields=["message", "entities", "edit_date", "edit_hide", "version"])

    opposite_peer = await Peer.get_or_create_for_user(peer.user_id, bot_user.id)
    refs = await MessageRef.filter(
        content_id=message.content_id,
        peer_id__in=[peer.id, opposite_peer.id],
    ).select_related("content", "content__author", "peer", "peer__user")

    await upd.edit_message(bot_user.id, {ref.peer: ref for ref in refs})
    return api_ok(await message_to_bot_api(bot_user, peer, message))


async def _edit_message_reply_markup(bot_user: User, params: dict[str, Any]) -> dict[str, Any]:
    chat_id = params.get("chat_id")
    message_id = params.get("message_id")
    if chat_id is None:
        return api_error("Bad Request: chat_id is required")
    if message_id is None:
        return api_error("Bad Request: message_id is required")

    peer = await resolve_bot_api_peer(bot_user, chat_id)
    if peer is None:
        return api_error("Bad Request: chat not found")

    message = await MessageRef.get_or_none(
        id=_require_int(message_id, field_name="message_id"), peer=peer,
    ).select_related("content")
    if message is None or message.content.author_id != bot_user.id:
        return api_error("Bad Request: message to edit not found")

    reply_markup = await process_outgoing_reply_markup(bot_user, params)
    message.content.reply_markup = reply_markup
    message.content.invalidate_reply_markup_cache()
    message.content.edit_date = datetime.now(UTC)
    message.content.edit_hide = True
    message.content.version += 1
    await message.content.save(update_fields=["reply_markup", "edit_date", "edit_hide", "version"])

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

    peer = await resolve_bot_api_peer(bot_user, chat_id)
    if peer is None:
        return api_error("Bad Request: chat not found")

    message = await MessageRef.get_or_none(
        id=_require_int(message_id, field_name="message_id"), peer=peer,
    )
    if message is None:
        return api_error("Bad Request: message can't be deleted")

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


async def _forward_message(bot_user: User, params: dict[str, Any]) -> dict[str, Any]:
    chat_id = params.get("chat_id")
    message_id = params.get("message_id")
    from_chat_id = params.get("from_chat_id", chat_id)
    if chat_id is None or message_id is None:
        return api_error("Bad Request: chat_id and message_id are required")

    to_peer = await resolve_bot_api_peer(bot_user, chat_id)
    from_peer = await resolve_bot_api_peer(bot_user, from_chat_id)
    if to_peer is None or from_peer is None:
        return api_error("Bad Request: chat not found")
    if not await peer_is_writable(bot_user, to_peer):
        return api_error("Bad Request: bot is not allowed to send messages in this chat", error_code=403)

    src_msg = await MessageRef.get_or_none(
        id=_require_int(message_id, field_name="message_id"), peer=from_peer,
    ).select_related(
        "content", "content__media", "content__media__file",
    )
    if src_msg is None:
        return api_error("Bad Request: message to forward not found")
    if src_msg.content.no_forwards:
        return api_error("Bad Request: message can't be forwarded")

    auth = await UserAuthorization.get_or_none(user_id=bot_user.id)
    ctx_token = _worker_context(bot_user, auth.id if auth is not None else 0)
    if ctx_token is None:
        return api_error("Internal error: worker is not available", error_code=500)

    try:
        author = await User.get(id=src_msg.content.author_id)
        fwd_header = await MessageFwdHeader.create(
            from_user=author,
            from_name=author.first_name,
            date=src_msg.content.date,
            saved_out=False,
        )
        media = None
        if src_msg.content.media_id is not None:
            await src_msg.content.fetch_related("media")
            media = src_msg.content.media

        updates = await send_message_internal(
            user=bot_user,
            peer=to_peer,
            random_id=None,
            reply_to_message_id=None,
            clear_draft=False,
            author=bot_user,
            text=src_msg.content.message,
            entities=src_msg.content.entities,
            media=media,
            fwd_header=fwd_header,
            opposite=True,
        )
    finally:
        request_ctx.reset(ctx_token)

    return await _extract_sent_message(bot_user, to_peer, updates)


async def _get_chat(bot_user: User, params: dict[str, Any]) -> dict[str, Any]:
    chat_id = params.get("chat_id")
    if chat_id is None:
        return api_error("Bad Request: chat_id is required")

    peer = await resolve_bot_api_peer(bot_user, chat_id)
    if peer is None:
        return api_error("Bad Request: chat not found")

    return api_ok(await peer_to_bot_api(peer))


async def _get_file(params: dict[str, Any]) -> dict[str, Any]:
    file_id = params.get("file_id")
    if file_id is None:
        return api_error("Bad Request: file_id is required")

    file = await File.get_or_none(id=_require_int(file_id, field_name="file_id"))
    if file is None:
        return api_error("Bad Request: invalid file_id")

    return api_ok(await get_file_result(file))


async def _send_chat_action(bot_user: User, params: dict[str, Any]) -> dict[str, Any]:
    chat_id = params.get("chat_id")
    action_name = params.get("action")
    if chat_id is None:
        return api_error("Bad Request: chat_id is required")
    if action_name is None:
        return api_error("Bad Request: action is required")

    action = _CHAT_ACTIONS.get(str(action_name))
    if action is None:
        return api_error("Bad Request: invalid action")

    peer = await resolve_bot_api_peer(bot_user, chat_id)
    if peer is None:
        return api_error("Bad Request: chat not found")

    if peer.type is PeerType.USER:
        opposite_peers = await peer.get_opposite()
        if not opposite_peers:
            return api_ok(True)
        await SessionManager.send(
            upd.UpdatesWithDefaults(
                updates=[UpdateUserTyping(user_id=bot_user.id, action=action)],
                users=[await bot_user.to_tl()],
            ),
            user_id=[other.owner_id for other in opposite_peers],
        )
    elif peer.type is PeerType.CHAT:
        await peer.fetch_related("chat")
        await SessionManager.send(
            upd.UpdatesWithDefaults(
                updates=[UpdateChatUserTyping(
                    chat_id=peer.chat.make_id(),
                    from_id=bot_user.to_tl_peer(),
                    action=action,
                )],
                users=[await bot_user.to_tl()],
                chats=[await peer.chat.to_tl()],
            ),
            user_id=await ChatParticipant.filter(chat_id=peer.chat_id, left=False).values_list("user_id", flat=True),
        )
    elif peer.type is PeerType.CHANNEL:
        await peer.fetch_related("channel")
        await SessionManager.send(
            upd.UpdatesWithDefaults(
                updates=[UpdateChannelUserTyping(
                    channel_id=peer.channel.make_id(),
                    from_id=bot_user.to_tl_peer(),
                    action=action,
                )],
                users=[await bot_user.to_tl()],
                chats=[await peer.channel.to_tl()],
            ),
            user_id=await ChatParticipant.filter(channel_id=peer.channel_id, left=False).values_list("user_id", flat=True),
        )
    return api_ok(True)


async def _set_my_commands(bot_user: User, params: dict[str, Any]) -> dict[str, Any]:
    commands = params.get("commands")
    if commands is None:
        return api_error("Bad Request: commands is required")
    if isinstance(commands, str):
        commands = json.loads(commands)
    if not isinstance(commands, list):
        return api_error("Bad Request: commands must be an array")

    await BotCommand.filter(bot_id=bot_user.id).delete()
    to_create = []
    for item in commands[:100]:
        if not isinstance(item, dict):
            continue
        command = str(item.get("command", "")).lstrip("/")[:32]
        description = str(item.get("description", ""))[:256]
        if not command or not description:
            continue
        to_create.append(BotCommand(bot_id=bot_user.id, name=command, description=description))

    if to_create:
        await BotCommand.bulk_create(to_create)

    return api_ok(True)


async def _get_my_commands(bot_user: User, params: dict[str, Any]) -> dict[str, Any]:
    commands = await BotCommand.filter(bot_id=bot_user.id)
    return api_ok([await bot_command_to_bot_api(cmd) for cmd in commands])


async def _delete_my_commands(bot_user: User, params: dict[str, Any]) -> dict[str, Any]:
    await BotCommand.filter(bot_id=bot_user.id).delete()
    return api_ok(True)


async def _answer_callback_query(bot_user: User, params: dict[str, Any]) -> dict[str, Any]:
    query_id = params.get("callback_query_id")
    if query_id is None:
        return api_error("Bad Request: callback_query_id is required")

    from piltover.app.runtime import get_worker
    from piltover.tl.types.messages import BotCallbackAnswer as MessagesBotCallbackAnswer

    worker = get_worker()
    if worker is None:
        return api_error("Internal error: worker is not available", error_code=500)

    answer = MessagesBotCallbackAnswer(
        alert=_parse_bool(params.get("show_alert")),
        has_url=params.get("url") is not None,
        native_ui=True,
        message=str(params["text"]) if params.get("text") is not None else None,
        url=str(params["url"]) if params.get("url") is not None else None,
        cache_time=_parse_int(params.get("cache_time"), 0),
    )
    await worker.pubsub.notify(
        topic=f"bot-callback-query/{_require_int(query_id, field_name='callback_query_id')}",
        data=answer.write(),
    )
    return api_ok(True)


async def _answer_pre_checkout_query(bot_user: User, params: dict[str, Any]) -> dict[str, Any]:
    query_id = params.get("pre_checkout_query_id")
    if query_id is None:
        return api_error("Bad Request: pre_checkout_query_id is required")

    from piltover.app.runtime import get_worker

    worker = get_worker()
    if worker is None:
        return api_error("Internal error: worker is not available", error_code=500)

    ok = _parse_bool(params.get("ok", True))
    data = b"1" if ok else str(params.get("error_message") or "PAYMENT_FAILED").encode("utf-8")

    await worker.pubsub.notify(
        topic=f"bot-precheckout-query/{_require_int(query_id, field_name='pre_checkout_query_id')}",
        data=data,
    )
    return api_ok(True)


def _set_webhook(bot_user: User, params: dict[str, Any]) -> dict[str, Any]:
    url = params.get("url")
    if url is None:
        return api_error("Bad Request: url is required")

    allowed_updates = params.get("allowed_updates")
    if isinstance(allowed_updates, str):
        allowed_updates = json.loads(allowed_updates)

    secret_token = params.get("secret_token")
    if secret_token is not None:
        secret_token = str(secret_token)
        if len(secret_token) < 1 or len(secret_token) > 256:
            return api_error("Bad Request: secret_token must be 1-256 characters")

    bot_api_updates.set_webhook(
        bot_user.id,
        str(url),
        drop_pending_updates=_parse_bool(params.get("drop_pending_updates")),
        allowed_updates=allowed_updates,
        max_connections=(
            _require_int(params["max_connections"], field_name="max_connections")
            if params.get("max_connections") is not None else None
        ),
        ip_address=str(params["ip_address"]) if params.get("ip_address") is not None else None,
        secret_token=secret_token,
    )
    return api_ok(True)


def _delete_webhook(bot_user: User, params: dict[str, Any]) -> dict[str, Any]:
    bot_api_updates.delete_webhook(
        bot_user.id,
        drop_pending_updates=_parse_bool(params.get("drop_pending_updates")),
    )
    return api_ok(True)


