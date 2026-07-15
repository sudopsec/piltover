from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from piltover.app.utils.bot_api.params import parse_json_object
from piltover.app.utils.bot_api.peers import resolve_bot_api_peer
from piltover.app.utils.bot_api.response import api_error
from piltover.db.models import MessageRef, Peer, User


@dataclass(slots=True)
class ResolvedReply:
    message_id: int | None
    reply_to: MessageRef | None = None
    top_msg_id: int | None = None


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes")
    return bool(value)


def parse_reply_parameters(params: dict[str, Any]) -> dict[str, Any] | None:
    legacy_id = params.get("reply_to_message_id")
    if legacy_id is not None:
        return {"message_id": int(legacy_id)}

    raw = params.get("reply_parameters")
    if raw is None:
        return None

    return parse_json_object(raw, field_name="reply_parameters")


def parse_message_thread_id(params: dict[str, Any]) -> int | None:
    thread_id = params.get("message_thread_id")
    if thread_id is None:
        return None
    return int(thread_id)


async def resolve_outgoing_reply(
        bot_user: User,
        destination_peer: Peer,
        params: dict[str, Any],
) -> ResolvedReply | dict[str, Any]:
    top_msg_id = parse_message_thread_id(params)
    reply_params = parse_reply_parameters(params)
    if reply_params is None:
        return ResolvedReply(None, top_msg_id=top_msg_id)

    if "message_id" not in reply_params:
        return api_error("Bad Request: reply_parameters message_id is required")

    message_id = int(reply_params["message_id"])
    allow_without = _parse_bool(reply_params.get("allow_sending_without_reply", False))

    lookup_peer = destination_peer
    if (reply_chat_id := reply_params.get("chat_id")) is not None:
        resolved = await resolve_bot_api_peer(bot_user, reply_chat_id)
        if resolved is None:
            return api_error("Bad Request: chat not found")
        lookup_peer = resolved

    reply_to = await MessageRef.get_or_none(
        peer=lookup_peer, id=message_id,
    ).select_related("content", "reply_to", "top_message")
    if reply_to is None:
        if allow_without:
            return ResolvedReply(None, top_msg_id=top_msg_id)
        return api_error("Bad Request: replied message not found")

    if lookup_peer.id != destination_peer.id:
        same_content = await MessageRef.get_or_none(
            peer=destination_peer, content_id=reply_to.content_id,
        ).select_related("content", "reply_to", "top_message")
        if same_content is not None:
            reply_to = same_content
        elif not allow_without:
            return api_error("Bad Request: replied message not found")

    return ResolvedReply(message_id=reply_to.id, reply_to=reply_to, top_msg_id=top_msg_id)


def reply_parameters_to_bot_api(peer: Peer, message: MessageRef) -> dict[str, Any] | None:
    if message.reply_to_id is None:
        return None

    from piltover.app.utils.bot_api.peers import peer_to_bot_api_chat_id

    result: dict[str, Any] = {
        "message_id": message.reply_to_id,
        "chat_id": peer_to_bot_api_chat_id(peer),
    }
    if message.top_message_id is not None:
        result["message_thread_id"] = message.top_message_id
    return result