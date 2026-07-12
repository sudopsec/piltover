from __future__ import annotations

import asyncio
import json
import random
import time
from datetime import datetime, UTC
from os import urandom
from secrets import token_hex
from typing import Any, cast

import httpx
from loguru import logger
from tortoise.transactions import in_transaction

from piltover.config import SYSTEM_CONFIG

from piltover.db.enums import ChatAdminRights, MessageType, PeerType
from piltover.db.models import (
    Channel, Chat, ChatBase, ChatParticipant, DefaultGroupCallJoinAs, GroupCall, GroupCallParticipant, MessageRef,
    Peer, User,
)
from piltover.db.models.group_call_participant import ADMIN_VOLUME_MUTE_THRESHOLD, DEFAULT_GROUP_CALL_VOLUME
from piltover.exceptions import ErrorRpc, Unreachable
from piltover.tl import DataJSON, InputGroupCall, MessageActionGroupCall, PeerChannel, PeerChat, PeerUser, Updates
from piltover.tl.base import InputPeer as TLInputPeerBase
from piltover.tl.types.phone import JoinAsPeers


def sfu_http_base() -> str:
    return SYSTEM_CONFIG.group_call_sfu.api_url.strip().rstrip("/")


async def resolve_chat_or_channel(user_id: int, input_peer: TLInputPeerBase) -> tuple[Chat | Channel, Peer]:
    peer = await Peer.from_input_peer_raise(
        user_id, input_peer, peer_types=(PeerType.CHAT, PeerType.CHANNEL), allow_migrated_chat=True,
    )
    if peer.type is PeerType.CHAT:
        return cast(Chat, peer.chat), peer
    if peer.type is PeerType.CHANNEL:
        return cast(Channel, peer.channel), peer
    raise Unreachable


async def ensure_can_manage_call(user_id: int, chat_or_channel: ChatBase) -> ChatParticipant:
    participant = await chat_or_channel.get_participant_raise(user_id, "CHAT_ADMIN_REQUIRED")
    if chat_or_channel.creator_id == user_id:
        return participant
    if isinstance(chat_or_channel, Chat) and participant.is_admin:
        return participant
    if not chat_or_channel.admin_has_permission(participant, ChatAdminRights.MANAGE_CALL):
        raise ErrorRpc(error_code=403, error_message="CHAT_ADMIN_REQUIRED")
    return participant


def resolve_join_muted(request_muted: bool, group_call: GroupCall) -> bool:
    if group_call.join_muted:
        return True
    return request_muted


_SPEAKING_BROADCAST_INTERVAL = 0.25
_last_speaking_broadcast: dict[tuple[int, int], float] = {}
_last_sfu_pause_state: dict[tuple[int, int], bool] = {}


def clear_speaking_state(group_call_id: int, user_id: int) -> None:
    _last_speaking_broadcast.pop((group_call_id, user_id), None)
    _last_sfu_pause_state.pop((group_call_id, user_id), None)


async def resolve_group_call_for_speaking(
        user_id: int,
        peer: Peer,
) -> tuple[Chat | Channel, GroupCall] | None:
    if Peer.is_chat(peer):
        chat = peer.chat or await Chat.get(id=peer.chat_id)
        group_call = await get_active_group_call(chat)
        if group_call is not None:
            return chat, group_call
    elif Peer.is_channel(peer):
        channel = peer.channel or await Channel.get(id=peer.channel_id)
        if channel.supergroup:
            group_call = await get_active_group_call(channel)
            if group_call is not None:
                return channel, group_call

    participant = await GroupCallParticipant.filter(
        user_id=user_id, left=False, group_call__discarded_at__isnull=True,
    ).select_related("group_call", "group_call__chat", "group_call__channel").first()
    if participant is None:
        return None
    group_call = participant.group_call
    if group_call.started_at is None:
        return None
    if group_call.chat_id is not None:
        chat = group_call.chat or await Chat.get(id=group_call.chat_id)
        return chat, group_call
    channel = group_call.channel or await Channel.get(id=group_call.channel_id)
    return channel, group_call


async def handle_sfu_speaking_callback(group_call_id: int, user_id: int) -> None:
    group_call = await GroupCall.get_or_none(id=group_call_id)
    if group_call is None or group_call.discarded_at is not None or group_call.started_at is None:
        return

    participant = await GroupCallParticipant.get_or_none(
        group_call=group_call, user_id=user_id, left=False,
    )
    if participant is None:
        return

    if group_call.chat_id is not None:
        chat_or_channel = group_call.chat or await Chat.get(id=group_call.chat_id)
    else:
        chat_or_channel = group_call.channel or await Channel.get(id=group_call.channel_id)

    logger.info(
        "GroupCall speaking SFU callback call={} user={} source={}",
        group_call_id,
        user_id,
        participant.source,
    )
    await notify_group_call_speaking(user_id, chat_or_channel, group_call)


async def notify_group_call_speaking(
        user_id: int,
        chat_or_channel: Chat | Channel,
        group_call: GroupCall,
) -> None:
    await group_call.refresh_from_db(fields=["discarded_at", "started_at", "version", "participants_version"])
    if group_call.discarded_at is not None or group_call.started_at is None:
        return

    participant = await GroupCallParticipant.get_or_none(
        group_call=group_call, user_id=user_id, left=False,
    )
    if participant is None or participant.is_admin_muted():
        return
    if participant.muted:
        return

    now = time.monotonic()
    throttle_key = (group_call.id, user_id)
    last_sent = _last_speaking_broadcast.get(throttle_key)
    if last_sent is not None and now - last_sent < _SPEAKING_BROADCAST_INTERVAL:
        return
    _last_speaking_broadcast[throttle_key] = now

    import piltover.app.utils.updates_manager as upd

    await upd.group_call_speaking_update(chat_or_channel, group_call, participant, speaker_user_id=user_id)


async def get_active_group_call(chat_or_channel: Chat | Channel) -> GroupCall | None:
    if isinstance(chat_or_channel, Chat):
        return await GroupCall.get_active_for_chat(chat_or_channel)
    return await GroupCall.get_active_for_channel(chat_or_channel)


def group_call_chat_tl_id(chat_or_channel: Chat | Channel) -> int:
    return chat_or_channel.make_id()


async def discard_active_call(chat_or_channel: Chat | Channel) -> GroupCall | None:
    group_call = await get_active_group_call(chat_or_channel)
    if group_call is None:
        return None
    group_call.discarded_at = datetime.now(UTC)
    group_call.version += 1
    await group_call.save(update_fields=["discarded_at", "version"])
    await GroupCallParticipant.filter(group_call=group_call, left=False).update(left=True)
    await close_group_call_room(group_call.id)
    return group_call


def gen_invite_hash() -> str:
    return urandom(16).hex()


async def resolve_join_as(user_id: int, join_as: TLInputPeerBase) -> tuple[int | None, int | None]:
    if Peer.input_is_self(user_id, join_as):
        return user_id, None

    peer = await Peer.from_input_peer_raise(user_id, join_as, peer_types=(PeerType.USER, PeerType.CHANNEL))
    if peer.type is PeerType.USER:
        return peer.user_id, None
    if peer.type is PeerType.CHANNEL:
        participant = await peer.channel.get_participant_raise(user_id)
        if not peer.channel.admin_has_permission(participant, ChatAdminRights.POST_MESSAGES):
            raise ErrorRpc(error_code=403, error_message="CHAT_ADMIN_REQUIRED")
        return None, peer.channel_id
    raise ErrorRpc(error_code=400, error_message="JOIN_AS_PEER_INVALID")


async def get_join_as_peers(user_id: int, chat_or_channel: Chat | Channel) -> JoinAsPeers:
    user = await User.get(id=user_id)
    peers = [PeerUser(user_id=user_id)]
    users = [await user.to_tl()]
    channels: list[Channel] = []

    if isinstance(chat_or_channel, Channel) and chat_or_channel.supergroup:
        send_as_channels = await Channel.filter(
            channel=True, deleted=False, creator_id=user_id, supergroup=False,
            chatparticipants__user_id=user_id, chatparticipants__left=False,
        )
        for channel in send_as_channels:
            if channel.id == chat_or_channel.id:
                continue
            peers.append(PeerChannel(channel_id=channel.make_id()))
            channels.append(channel)

    return JoinAsPeers(
        peers=peers,
        chats=await Channel.to_tl_bulk(channels),
        users=users,
    )


async def get_default_join_as_peer(user_id: int, chat_or_channel: Chat | Channel) -> PeerUser | PeerChannel | None:
    if isinstance(chat_or_channel, Chat):
        default = await DefaultGroupCallJoinAs.get_or_none(user_id=user_id, chat=chat_or_channel)
    else:
        default = await DefaultGroupCallJoinAs.get_or_none(user_id=user_id, channel=chat_or_channel)
    if default is None:
        return None
    if default.join_as_channel_id is not None:
        return PeerChannel(channel_id=Channel.make_id_from(default.join_as_channel_id))
    if default.join_as_user_id is not None:
        return PeerUser(user_id=default.join_as_user_id)
    return None


async def save_default_join_as(user_id: int, chat_or_channel: Chat | Channel, join_as: TLInputPeerBase) -> None:
    join_as_user_id, join_as_channel_id = await resolve_join_as(user_id, join_as)
    defaults = {"join_as_user_id": join_as_user_id, "join_as_channel_id": join_as_channel_id}
    if isinstance(chat_or_channel, Chat):
        await DefaultGroupCallJoinAs.update_or_create(user_id=user_id, chat=chat_or_channel, defaults=defaults)
    else:
        await DefaultGroupCallJoinAs.update_or_create(user_id=user_id, channel=chat_or_channel, defaults=defaults)


async def allocate_source(group_call: GroupCall) -> int:
    source = group_call.next_source
    group_call.next_source += 1
    await group_call.save(update_fields=["next_source"])
    return source


def allocate_random_ssrc() -> int:
    return random.randint(1_000_000, 9_999_999)


async def ensure_unique_ssrc(
        group_call: GroupCall,
        ssrc: int | None,
        *,
        user_id: int,
) -> int:
    taken = GroupCallParticipant.filter(group_call=group_call, left=False).exclude(user_id=user_id)

    if ssrc is not None:
        if await taken.filter(source=ssrc).exists():
            raise ErrorRpc(error_code=400, error_message="GROUPCALL_SSRC_DUPLICATE_MUCH")
        return ssrc

    for _ in range(10):
        candidate = allocate_random_ssrc()
        if not await taken.filter(source=candidate).exists():
            return candidate
    return await allocate_source(group_call)


def _parse_client_params(client_params: DataJSON) -> dict[str, Any]:
    try:
        payload = json.loads(client_params.data)
    except (json.JSONDecodeError, TypeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return payload


def _extract_client_ssrc(payload: dict[str, Any], fallback: int) -> int:
    ssrc = payload.get("ssrc")
    if ssrc is None:
        return fallback
    try:
        return int(ssrc)
    except (TypeError, ValueError):
        return fallback


def parse_join_client_params(client_params: DataJSON) -> tuple[dict[str, Any], int | None]:
    payload = _parse_client_params(client_params)
    if "ssrc" not in payload:
        return payload, None
    ssrc = _extract_client_ssrc(payload, 0)
    return payload, ssrc if ssrc != 0 else None


def _transport_params(payload: dict[str, Any]) -> dict[str, Any]:
    transport = payload.get("transport")
    if isinstance(transport, dict):
        return transport
    return payload


def _extract_client_fingerprints(payload: dict[str, Any]) -> list[dict[str, str]]:
    source = _transport_params(payload)
    fingerprints = source.get("fingerprints") or []
    if not isinstance(fingerprints, list):
        return []

    result: list[dict[str, str]] = []
    for fp in fingerprints:
        if not isinstance(fp, dict):
            continue
        value = fp.get("fingerprint") or fp.get("value") or ""
        if not value:
            continue
        result.append({
            "algorithm": fp.get("hash") or fp.get("algorithm") or "sha-256",
            "value": value,
        })
    return result


def _candidate_ip(ip: str) -> str:
    sfu = SYSTEM_CONFIG.group_call_sfu
    if ip in ("0.0.0.0", ""):
        return sfu.public_ip
    if ip == "127.0.0.1" and sfu.public_ip not in ("127.0.0.1", "localhost"):
        return sfu.public_ip
    return ip


def _server_setup_for_client(client_setup: str) -> str:
    value = client_setup.lower()
    if value == "active":
        return "passive"
    if value == "passive":
        return "active"
    # actpass: tgcalls client is DTLS server; answer with active.
    return "active"


def _client_setup_from_payload(payload: dict[str, Any]) -> str:
    source = _transport_params(payload)
    fingerprints = source.get("fingerprints") or []
    if not isinstance(fingerprints, list) or not fingerprints:
        return "actpass"
    primary = next(
        (fp for fp in fingerprints if str(fp.get("hash", "")).lower() == "sha-256"),
        fingerprints[0] if fingerprints else None,
    )
    if not isinstance(primary, dict):
        return "actpass"
    return str(primary.get("setup", "actpass")).lower()


def _client_uses_nested_transport(payload: dict[str, Any] | None) -> bool:
    return isinstance(payload, dict) and isinstance(payload.get("transport"), dict)


def _default_ssrc_groups(ssrc: int, payload: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        groups = payload.get("ssrc-groups") or payload.get("ssrcGroups")
        if isinstance(groups, list) and groups:
            return groups
    return [{"semantics": "default", "sources": [ssrc]}]


def _build_telegram_connection_fields(
        transport: dict[str, Any],
        *,
        client_setup: str = "actpass",
) -> dict[str, Any]:
    ice = transport.get("iceParameters") or {}
    dtls = transport.get("dtlsParameters") or {}
    candidates = transport.get("iceCandidates") or []

    fingerprints = dtls.get("fingerprints") or []
    primary = next(
        (fp for fp in fingerprints if str(fp.get("algorithm", "")).lower() == "sha-256"),
        fingerprints[0] if fingerprints else None,
    )

    return {
        "ufrag": ice.get("usernameFragment", ""),
        "pwd": ice.get("password", ""),
        "fingerprints": [{
            "hash": primary.get("algorithm", "sha-256"),
            "fingerprint": primary.get("value", ""),
            "setup": _server_setup_for_client(client_setup),
        }] if primary else [],
        "candidates": [{
            "component": "1",
            "foundation": candidate.get("foundation", "1"),
            "ip": _candidate_ip(str(candidate.get("ip", ""))),
            "port": str(candidate.get("port", "")),
            "priority": str(candidate.get("priority", "")),
            "protocol": candidate.get("protocol", "udp"),
            "type": candidate.get("type", "host"),
            "generation": "0",
            "network": "1",
            "id": str(index + 1),
            **({"tcptype": candidate["tcpType"]} if candidate.get("protocol") == "tcp" and candidate.get("tcpType") else {}),
        } for index, candidate in enumerate(candidates) if isinstance(candidate, dict)],
    }


def _telegram_connection_from_transport(
        transport: dict[str, Any],
        ssrc: int,
        *,
        client_setup: str = "actpass",
        client_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    fields = _build_telegram_connection_fields(transport, client_setup=client_setup)
    connection: dict[str, Any] = {"transport": fields, "ssrc": ssrc}
    connection["ssrc-groups"] = _default_ssrc_groups(ssrc, client_payload)
    return connection


async def _get_dtls_fingerprint() -> str:
    sfu = SYSTEM_CONFIG.group_call_sfu
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{sfu_http_base()}/api/dtls-fingerprint")
            response.raise_for_status()
            data = response.json()
            return str(data.get("value", ""))
    except httpx.HTTPError as exc:
        logger.warning("Failed to fetch DTLS fingerprint from SFU: {}", exc)
        return ""


async def _static_fallback_connection(
        ssrc: int,
        *,
        client_setup: str = "actpass",
        client_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    sfu = SYSTEM_CONFIG.group_call_sfu
    fingerprint = await _get_dtls_fingerprint()
    if not fingerprint:
        fingerprint = "00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00"

    fields = {
        "ufrag": token_hex(4),
        "pwd": token_hex(11),
        "fingerprints": [{
            "hash": "sha-256",
            "fingerprint": fingerprint,
            "setup": _server_setup_for_client(client_setup),
        }],
        "candidates": [{
            "component": "1",
            "foundation": "1",
            "ip": sfu.public_ip,
            "port": str(sfu.rtc_port),
            "priority": "2130706431",
            "protocol": "udp",
            "type": "host",
            "generation": "0",
            "network": "1",
            "id": "1",
        }],
    }
    connection: dict[str, Any] = {"transport": fields, "ssrc": ssrc}
    connection["ssrc-groups"] = _default_ssrc_groups(ssrc, client_payload)
    return connection


async def close_group_call_room(group_call_id: int) -> None:
    sfu = SYSTEM_CONFIG.group_call_sfu
    if not sfu.enabled:
        return
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.delete(f"{sfu_http_base()}/api/rooms/{group_call_id}")
    except httpx.HTTPError as exc:
        logger.warning("Failed to close group call SFU room {}: {}", group_call_id, exc)


def normalize_admin_volume(volume: int) -> int:
    if volume <= ADMIN_VOLUME_MUTE_THRESHOLD:
        return 0
    return volume


def is_admin_volume_silent(participant: GroupCallParticipant) -> bool:
    return participant.is_admin_volume_silent()


def normalize_admin_mute_db_state(participant: GroupCallParticipant) -> list[str]:
    """Convert legacy volume=0 + volume_by_admin rows to muted_by_admin semantics."""
    if not participant.is_admin_volume_silent() or participant.muted_by_admin:
        return []
    logger.info(
        "GroupCallMute normalize legacy silent volume call={} user={} before={}",
        participant.group_call_id,
        participant.user_id,
        participant.format_mute_debug(),
    )
    participant.muted = True
    participant.muted_by_admin = True
    participant.volume = DEFAULT_GROUP_CALL_VOLUME
    participant.volume_by_admin = False
    return ["muted", "muted_by_admin", "volume", "volume_by_admin"]


def should_pause_sfu_audio(participant: GroupCallParticipant) -> bool:
    if participant.muted or participant.is_admin_muted():
        return True
    return False


async def sync_sfu_participant_audio_state(
        group_call_id: int,
        participant: GroupCallParticipant,
) -> None:
    sfu = SYSTEM_CONFIG.group_call_sfu
    if not sfu.enabled:
        return
    paused = should_pause_sfu_audio(participant)
    state_key = (group_call_id, participant.user_id)
    if _last_sfu_pause_state.get(state_key) == paused:
        return
    _last_sfu_pause_state[state_key] = paused
    try:
        async with httpx.AsyncClient(timeout=1.0) as client:
            await client.post(
                f"{sfu_http_base()}/api/participant-state",
                json={
                    "roomId": str(group_call_id),
                    "peerId": str(participant.user_id),
                    "paused": paused,
                },
            )
    except httpx.HTTPError as exc:
        logger.warning(
            "Failed to sync SFU audio state call={} user={} paused={}: {}",
            group_call_id, participant.user_id, paused, exc,
        )


def schedule_sfu_participant_audio_state(
        group_call_id: int,
        participant: GroupCallParticipant,
) -> None:
    asyncio.create_task(sync_sfu_participant_audio_state(group_call_id, participant))


async def leave_sfu_peer(group_call_id: int, user_id: int) -> None:
    sfu = SYSTEM_CONFIG.group_call_sfu
    if not sfu.enabled:
        return
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"{sfu_http_base()}/api/leave",
                json={"roomId": str(group_call_id), "peerId": str(user_id)},
            )
    except httpx.HTTPError as exc:
        logger.warning("Failed to remove peer {} from SFU room {}: {}", user_id, group_call_id, exc)


async def build_connection_params(
        client_params: DataJSON,
        *,
        group_call_id: int,
        user_id: int,
        source: int,
) -> DataJSON:
    payload = _parse_client_params(client_params)
    client_setup = _client_setup_from_payload(payload)
    sfu = SYSTEM_CONFIG.group_call_sfu

    if not sfu.enabled:
        return DataJSON(data=json.dumps(payload))

    media_ssrc = _extract_client_ssrc(payload, source)
    payload_types = payload.get("payload-types") or payload.get("payloadTypes") or []
    logger.info(
        "JoinGroupCall SFU request call={} user={} source={} media_ssrc={} keys={} payload_types={}",
        group_call_id,
        user_id,
        source,
        media_ssrc,
        list(payload.keys()),
        len(payload_types) if isinstance(payload_types, list) else 0,
    )

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            base = sfu_http_base()
            logger.debug("SFU HTTP base: {}", base)
            join_response = await client.post(
                f"{base}/api/join",
                json={
                    "roomId": str(group_call_id),
                    "peerId": str(user_id),
                    "ssrc": media_ssrc,
                    "clientParams": payload,
                },
            )
            join_response.raise_for_status()
            connection = join_response.json()
            connection_format = "nested" if isinstance(connection, dict) and "transport" in connection else "flat"
            connection_keys = list(connection.keys()) if isinstance(connection, dict) else []
            logger.info(
                "SFU join call={} user={} ssrc={} format={} keys={}",
                group_call_id,
                user_id,
                media_ssrc,
                connection_format,
                connection_keys,
            )
            return DataJSON(data=json.dumps(connection))
    except httpx.HTTPError as exc:
        logger.error(
            "SFU transport create failed for call={} user={} source={}: {}",
            group_call_id, user_id, source, exc,
        )
        fallback = await _static_fallback_connection(
            media_ssrc,
            client_setup=client_setup,
            client_payload=payload,
        )
        return DataJSON(data=json.dumps(fallback))


async def create_group_call(
        user_id: int,
        chat_or_channel: Chat | Channel,
        *,
        title: str | None = None,
        schedule_date: datetime | None = None,
) -> GroupCall:
    async with in_transaction():
        await discard_active_call(chat_or_channel)
        if title is None:
            title = chat_or_channel.name
        group_call = await GroupCall.create(
            creator_id=user_id,
            chat=chat_or_channel if isinstance(chat_or_channel, Chat) else None,
            channel=chat_or_channel if isinstance(chat_or_channel, Channel) else None,
            title=title,
            schedule_date=schedule_date,
            started_at=None if schedule_date is not None else datetime.now(UTC),
        )
    return group_call


async def join_group_call(
        user_id: int,
        group_call: GroupCall,
        join_as: TLInputPeerBase,
        *,
        muted: bool,
        video_stopped: bool,
        invite_hash: str | None,
        client_ssrc: int | None = None,
) -> tuple[GroupCallParticipant, bool]:
    if group_call.discarded_at is not None:
        raise ErrorRpc(error_code=403, error_message="GROUPCALL_FORBIDDEN")
    if group_call.started_at is None:
        raise ErrorRpc(error_code=400, error_message="GROUPCALL_INVALID")

    join_as_user_id, join_as_channel_id = await resolve_join_as(user_id, join_as)
    if invite_hash is not None and invite_hash != group_call.invite_hash:
        raise ErrorRpc(error_code=400, error_message="HASH_INVALID")

    muted = resolve_join_muted(muted, group_call)
    source = await ensure_unique_ssrc(group_call, client_ssrc, user_id=user_id)

    participant = await GroupCallParticipant.get_or_none(group_call=group_call, user_id=user_id)
    created = False
    if participant is None:
        participant = await GroupCallParticipant.create(
            group_call=group_call,
            user_id=user_id,
            join_as_user_id=join_as_user_id,
            join_as_channel_id=join_as_channel_id,
            source=source,
            muted=muted,
            video_stopped=video_stopped,
        )
        created = True
    else:
        participant.left = False
        normalize_fields = normalize_admin_mute_db_state(participant)
        if participant.muted_by_admin or participant.is_admin_volume_silent():
            participant.muted = True
        else:
            participant.muted = muted
        participant.video_stopped = video_stopped
        participant.join_as_user_id = join_as_user_id
        participant.join_as_channel_id = join_as_channel_id
        update_fields = [
            "left", "muted", "video_stopped", "join_as_user_id", "join_as_channel_id",
            *normalize_fields,
        ]
        if participant.source != source:
            participant.source = source
            update_fields.append("source")
        await participant.save(update_fields=update_fields)

    await group_call.bump_participants_version()
    return participant, created


async def resolve_group_call_participant(
        editor_user_id: int,
        group_call: GroupCall,
        participant_peer: TLInputPeerBase,
) -> GroupCallParticipant:
    if Peer.input_is_self(editor_user_id, participant_peer):
        participant = await GroupCallParticipant.get_or_none(
            group_call=group_call, user_id=editor_user_id, left=False,
        )
        if participant is None:
            raise ErrorRpc(error_code=400, error_message="PARTICIPANT_JOIN_MISSING")
        return participant

    peer = await Peer.from_input_peer_raise(
        editor_user_id, participant_peer, peer_types=(PeerType.USER, PeerType.CHANNEL),
    )
    if peer.type is PeerType.CHANNEL:
        participant = await GroupCallParticipant.get_or_none(
            group_call=group_call, join_as_channel_id=peer.channel_id, left=False,
        )
    else:
        participant = await GroupCallParticipant.get_or_none(
            group_call=group_call, user_id=peer.user_id, left=False,
        )
    if participant is None:
        raise ErrorRpc(error_code=400, error_message="PARTICIPANT_JOIN_MISSING")
    return participant


async def edit_group_call_participant(
        editor_user_id: int,
        group_call: GroupCall,
        chat_or_channel: Chat | Channel,
        participant_peer: TLInputPeerBase,
        *,
        muted: bool | None,
        volume: int | None,
        raise_hand: bool | None,
        video_stopped: bool | None,
        video_paused: bool | None,
) -> tuple[GroupCallParticipant, bool]:
    if group_call.discarded_at is not None:
        raise ErrorRpc(error_code=403, error_message="GROUPCALL_FORBIDDEN")

    participant = await resolve_group_call_participant(editor_user_id, group_call, participant_peer)
    editing_self = participant.user_id == editor_user_id
    logger.info(
        "GroupCallMute edit start call={} editor={} target={} editing_self={} "
        "req(muted={} volume={} raise_hand={} video_stopped={} video_paused={}) before={}",
        group_call.id,
        editor_user_id,
        participant.user_id,
        editing_self,
        muted,
        volume,
        raise_hand,
        video_stopped,
        video_paused,
        participant.format_mute_debug(),
    )

    if not editing_self:
        await ensure_can_manage_call(editor_user_id, chat_or_channel)

    if volume is not None and not 0 <= volume <= 20000:
        raise ErrorRpc(error_code=400, error_message="USER_VOLUME_INVALID")

    update_fields: list[str] = []

    if volume is not None:
        if editing_self:
            raise ErrorRpc(error_code=400, error_message="USER_VOLUME_INVALID")
        normalized = normalize_admin_volume(volume)
        if normalized == 0:
            # Telegram shows admin mute icon, not a 0% volume slider.
            participant.muted = True
            participant.muted_by_admin = True
            participant.volume = DEFAULT_GROUP_CALL_VOLUME
            participant.volume_by_admin = False
            update_fields.extend(["muted", "muted_by_admin", "volume", "volume_by_admin"])
        else:
            participant.volume = normalized
            participant.volume_by_admin = normalized != DEFAULT_GROUP_CALL_VOLUME
            update_fields.extend(["volume", "volume_by_admin"])
            if participant.muted_by_admin or participant.is_admin_volume_silent():
                participant.muted = False
                participant.muted_by_admin = False
                if normalized == DEFAULT_GROUP_CALL_VOLUME:
                    participant.volume_by_admin = False
                    update_fields.append("volume_by_admin")
                update_fields.extend(["muted", "muted_by_admin"])

    if muted is not None:
        if editing_self and not muted and not participant.can_self_unmute_participant():
            logger.debug(
                "Ignoring self-unmute for call={} user={} muted_by_admin={} volume_by_admin={} volume={}",
                group_call.id, participant.user_id, participant.muted_by_admin,
                participant.volume_by_admin, participant.volume,
            )
        else:
            participant.muted = muted
            update_fields.append("muted")
            if editing_self:
                if not muted:
                    participant.muted_by_admin = False
                    update_fields.append("muted_by_admin")
            elif muted:
                participant.muted_by_admin = True
                participant.volume = DEFAULT_GROUP_CALL_VOLUME
                participant.volume_by_admin = False
                update_fields.extend(["muted_by_admin", "volume", "volume_by_admin"])
            else:
                participant.muted_by_admin = False
                participant.volume = DEFAULT_GROUP_CALL_VOLUME
                participant.volume_by_admin = False
                update_fields.extend(["muted_by_admin", "volume", "volume_by_admin"])

    if raise_hand is not None:
        if not raise_hand and editing_self and participant.is_admin_muted():
            logger.debug(
                "Ignoring raise_hand=false while admin-muted call={} user={}",
                group_call.id, participant.user_id,
            )
        elif raise_hand:
            rating = int(datetime.now(UTC).timestamp() * 1000)
            if participant.raise_hand_rating is not None:
                rating = max(rating, participant.raise_hand_rating + 1)
            participant.raise_hand_rating = rating
            update_fields.append("raise_hand_rating")
        else:
            participant.raise_hand_rating = None
            update_fields.append("raise_hand_rating")

    if video_stopped is not None:
        participant.video_stopped = video_stopped
        update_fields.append("video_stopped")
    elif video_paused is not None:
        participant.video_stopped = video_paused
        update_fields.append("video_stopped")

    update_fields.extend(normalize_admin_mute_db_state(participant))

    if not update_fields:
        logger.info(
            "GroupCallMute edit noop call={} editor={} target={} after={}",
            group_call.id,
            editor_user_id,
            participant.user_id,
            participant.format_mute_debug(),
        )
        if (
            muted is not None and editing_self and not muted
            and not participant.can_self_unmute_participant()
        ):
            schedule_sfu_participant_audio_state(group_call.id, participant)
        return participant, False

    await participant.save(update_fields=list(dict.fromkeys(update_fields)))
    await group_call.bump_participants_version()
    if muted is not None or volume is not None:
        schedule_sfu_participant_audio_state(group_call.id, participant)
    logger.info(
        "GroupCallMute edit done call={} editor={} target={} version={} "
        "saved_fields={} sfu_paused={} after={}",
        group_call.id,
        editor_user_id,
        participant.user_id,
        group_call.version,
        list(dict.fromkeys(update_fields)),
        should_pause_sfu_audio(participant),
        participant.format_mute_debug(),
    )
    return participant, True


async def leave_group_call(group_call: GroupCall, user_id: int, source: int) -> GroupCallParticipant | None:
    participant = await GroupCallParticipant.get_or_none(group_call=group_call, user_id=user_id, left=False)
    if participant is None:
        return None
    if participant.source != source:
        logger.debug(
            "LeaveGroupCall source mismatch call={} user={} client_source={} db_source={}",
            group_call.id, user_id, source, participant.source,
        )
    participant.left = True
    await participant.save(update_fields=["left"])
    await group_call.bump_participants_version()
    clear_speaking_state(group_call.id, user_id)
    asyncio.create_task(leave_sfu_peer(group_call.id, user_id))
    return participant


async def discard_group_call_if_empty(group_call: GroupCall) -> bool:
    if await group_call.participants_count() > 0:
        return False
    if group_call.discarded_at is not None:
        return False
    group_call.discarded_at = datetime.now(UTC)
    group_call.version += 1
    await group_call.save(update_fields=["discarded_at", "version"])
    await close_group_call_room(group_call.id)
    return True


async def build_phone_group_call(group_call: GroupCall, limit: int, self_user_id: int | None = None):
    from piltover.tl.types.phone import GroupCall as PhoneGroupCall

    participants = await GroupCallParticipant.filter(
        group_call=group_call, left=False,
    ).select_related("user", "join_as_user", "join_as_channel").order_by("joined_at").limit(limit)

    users: dict[int, User] = {}
    channels: dict[int, Channel] = {}
    for participant in participants:
        users[participant.user_id] = participant.user
        if participant.join_as_user_id is not None:
            users[participant.join_as_user_id] = participant.join_as_user
        if participant.join_as_channel_id is not None:
            channels[participant.join_as_channel_id] = participant.join_as_channel

    return PhoneGroupCall(
        call=await group_call.to_tl(),
        participants=[
            participant.to_tl(self_user_id=self_user_id, just_joined=False, versioned=False)
            for participant in participants
        ],
        participants_next_offset="",
        chats=await Channel.to_tl_bulk(channels.values()),
        users=await User.to_tl_bulk(users.values()),
    )


def input_group_call_for_full(group_call: GroupCall | None) -> InputGroupCall | None:
    if group_call is None or group_call.discarded_at is not None:
        return None
    return group_call.to_input()


async def get_service_message_peer(chat_or_channel: Chat | Channel, author_user_id: int) -> Peer:
    if isinstance(chat_or_channel, Channel):
        peer = await Peer.get(channel_id=chat_or_channel.id)
        peer.channel = chat_or_channel
        return peer
    peer = await Peer.get(owner_id=author_user_id, chat_id=chat_or_channel.id)
    peer.chat = chat_or_channel
    return peer


async def send_group_call_service_message(
        chat_or_channel: Chat | Channel,
        group_call: GroupCall,
        author_user_id: int,
        *,
        duration: int | None = None,
) -> Updates | None:
    import piltover.app.utils.updates_manager as upd
    from piltover.app.handlers.messages.sending import send_message_internal

    action = MessageActionGroupCall(
        call=group_call.to_input(),
        duration=duration,
    )
    user = await User.get(id=author_user_id).only("id")
    user.bot = False
    peer = await get_service_message_peer(chat_or_channel, author_user_id)

    if isinstance(chat_or_channel, Channel):
        messages = await MessageRef.create_for_peer(
            peer, author_user_id,
            type=MessageType.SERVICE_GROUP_CALL,
            extra_info=action.write(),
            opposite=False,
        )
        return await upd.send_message_channel(author_user_id, chat_or_channel, messages[peer])

    return await send_message_internal(
        user, peer, None, None, False,
        author=author_user_id,
        type=MessageType.SERVICE_GROUP_CALL,
        extra_info=action.write(),
    )