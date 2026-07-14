import hashlib
import json
from datetime import datetime, UTC
from typing import cast

from loguru import logger
from tortoise.expressions import Q
from tortoise.transactions import in_transaction

import piltover.app.utils.updates_manager as upd
from piltover.context import request_ctx
from piltover.db.enums import PeerType, PrivacyRuleKeyType, CallDiscardReason, MessageType, CALL_DISCARD_REASON_TO_TL
from piltover.db.models import MessageRef, User, Peer, PrivacyRule, UserAuthorization, PhoneCall
from piltover.enums import ReqHandlerFlags
from piltover.exceptions import ErrorRpc
from piltover.tl import DataJSON, Updates, PhoneCallDiscardReasonDisconnect, PhoneCallProtocol, MessageActionPhoneCall
from piltover.tl.functions.phone import GetCallConfig, RequestCall, DiscardCall, AcceptCall, ConfirmCall, \
    RequestCall_133, ReceivedCall, SendSignalingData
from piltover.tl.types.phone import PhoneCall as PhonePhoneCall
from piltover.worker import MessageHandler

handler = MessageHandler("phone")

SUPPORTED_LIBRARY_VERSIONS = {
    "2.4.4",
    "2.7.7",
    "4.0.0",
    "5.0.0",
    "7.0.0",
    "8.0.0",
    "9.0.0",
    "11.0.0",
    "12.0.0",
    "13.0.0",
}
_DH_BYTES_LEN = 256
CALL_CONFIG = json.dumps({
    "enable_vp8_encoder": True,
    "enable_vp8_decoder": True,
    "enable_vp9_encoder": True,
    "enable_vp9_decoder": True,
    "enable_h265_encoder": True,
    "enable_h265_decoder": True,
    "enable_h264_encoder": True,
    "enable_h264_decoder": True,
    "audio_frame_size": 60,
    "jitter_min_delay_60": 2,
    "jitter_max_delay_60": 10,
    "jitter_max_slots_60": 20,
    "jitter_losses_to_reset": 20,
    "jitter_resync_threshold": 0.5,
    "audio_congestion_window": 1024,
    "audio_max_bitrate": 20000,
    "audio_max_bitrate_edge": 16000,
    "audio_max_bitrate_gprs": 8000,
    "audio_max_bitrate_saving": 8000,
    "audio_init_bitrate": 16000,
    "audio_init_bitrate_edge": 8000,
    "audio_init_bitrate_gprs": 8000,
    "audio_init_bitrate_saving": 8000,
    "audio_bitrate_step_incr": 1000,
    "audio_bitrate_step_decr": 1000,
    "use_system_ns": True,
    "use_system_aec": True,
    "force_tcp": False,
    "jitter_initial_delay_60": 2,
    "adsp_good_impls": "(Qualcomm Fluence)",
    "bad_call_rating": True,
    "use_ios_vpio_agc": False,
    "use_tcp": False,
    "rtc_servers": [
        {"host": "stun.l.google.com", "port": 19302, "turn": False, "stun": True},
        {"host": "stun1.l.google.com", "port": 19302, "turn": False, "stun": True},
        {"host": "stun2.l.google.com", "port": 19302, "turn": False, "stun": True},
        {"host": "stun3.l.google.com", "port": 19302, "turn": False, "stun": True},
        {"host": "stun4.l.google.com", "port": 19302, "turn": False, "stun": True},
    ],
    "audio_medium_fec_bitrate": 20000,
    "audio_medium_fec_multiplier": 0.1,
    "audio_strong_fec_bitrate": 7000
})


@handler.on_request(GetCallConfig, ReqHandlerFlags.BOT_NOT_ALLOWED)
async def get_call_config() -> DataJSON:
    return DataJSON(data=CALL_CONFIG)


def _normalize_library_versions(versions: list[str]) -> list[str]:
    supported = [version for version in versions if version in SUPPORTED_LIBRARY_VERSIONS]
    if supported:
        return supported
    return ["2.4.4"]


def _pick_merged_library_versions(a: PhoneCallProtocol, b: PhoneCallProtocol, *, final: bool) -> list[str]:
    merged = list(set(a.library_versions) & set(b.library_versions) & SUPPORTED_LIBRARY_VERSIONS)
    if not merged:
        merged = _normalize_library_versions(list(set(a.library_versions) | set(b.library_versions)))
    if not final:
        return merged

    versions_num = [tuple(map(int, version.split("."))) for version in merged]
    versions_num.sort()
    return [".".join(map(str, versions_num[-1]))]


def _check_protocol(protocol: PhoneCallProtocol) -> None:
    if not protocol.udp_p2p and not protocol.udp_reflector:
        raise ErrorRpc(error_code=400, error_message="CALL_PROTOCOL_FLAGS_INVALID")

    if protocol.min_layer > protocol.max_layer:
        raise ErrorRpc(error_code=400, error_message="CALL_PROTOCOL_LAYER_INVALID")
    if protocol.min_layer < 65 or protocol.max_layer > 200:
        raise ErrorRpc(error_code=400, error_message="CALL_PROTOCOL_LAYER_INVALID")

    protocol.library_versions = _normalize_library_versions(protocol.library_versions)


def _merge_protocols(a: PhoneCallProtocol, b: PhoneCallProtocol, final: bool = False) -> PhoneCallProtocol:
    merged_min_layer = max(a.min_layer, b.min_layer)
    merged_max_layer = min(a.max_layer, b.max_layer)
    if merged_min_layer > merged_max_layer:
        raise ErrorRpc(error_code=406, error_message="CALL_PROTOCOL_COMPAT_LAYER_INVALID")

    versions = _pick_merged_library_versions(a, b, final=final)
    if not versions:
        raise ErrorRpc(error_code=406, error_message="CALL_PROTOCOL_COMPAT_LAYER_INVALID")

    return PhoneCallProtocol(
        udp_p2p=a.udp_p2p or b.udp_p2p,
        udp_reflector=a.udp_reflector or b.udp_reflector,
        min_layer=merged_min_layer,
        max_layer=merged_max_layer,
        library_versions=versions,
    )


def _validate_dh_bytes(data: bytes, error_message: str) -> None:
    if not data or len(data) > _DH_BYTES_LEN:
        raise ErrorRpc(error_code=400, error_message=error_message)


async def _call_sessions_for_user(user_id: int) -> list[int]:
    return cast(
        list[int],
        await UserAuthorization.filter(user_id=user_id, allow_call_requests=True).values_list("id", flat=True),
    )


async def _get_call(
        user_id: int, auth_id: int, call_id: int, access_hash: int, *, active_only: bool = True,
) -> PhoneCall | None:
    query = PhoneCall.filter(id=call_id, access_hash=access_hash)
    if active_only:
        query = query.filter(discard_reason__isnull=True)
    return await query.filter(
        Q(from_user_id=user_id, from_sess_id=auth_id) | Q(to_user_id=user_id),
    ).select_related("from_user", "to_user", "from_user__username", "to_user__username").first()


async def _discard_stale_outgoing_calls(from_user_id: int, to_user_id: int) -> None:
    stale_calls = await PhoneCall.filter(
        from_user_id=from_user_id,
        to_user_id=to_user_id,
        discard_reason__isnull=True,
        started_at__isnull=True,
    ).select_related("from_user", "to_user")
    for call in stale_calls:
        call.discard_reason = CallDiscardReason.HANGUP
        await call.save(update_fields=["discard_reason"])
        await _send_call_service_messages(call, CallDiscardReason.HANGUP, requester_id=from_user_id)
        callee_sessions = await _call_sessions_for_user(call.to_user_id)
        await upd.phone_call_update(call.from_user_id, call, [call.from_sess_id])
        if callee_sessions:
            await upd.phone_call_update(call.to_user_id, call, callee_sessions)


def _call_action_bytes(call: PhoneCall, reason: CallDiscardReason) -> bytes:
    return MessageActionPhoneCall(
        call_id=call.id,
        reason=CALL_DISCARD_REASON_TO_TL[reason],
        duration=call.duration,
    ).write()


async def _send_call_service_messages(
        call: PhoneCall, reason: CallDiscardReason, *, requester_id: int | None = None,
) -> Updates | None:
    await call.fetch_related("from_user", "to_user")

    peer = await Peer.get_or_create_for_user(call.from_user_id, call.to_user, select_related=("user",))

    messages = await MessageRef.create_for_peer(
        peer,
        call.from_user_id,
        opposite=True,
        type=MessageType.SERVICE_PHONE_CALL,
        extra_info=_call_action_bytes(call, reason),
    )

    by_owner = {message_peer.owner_id: message_peer for message_peer in messages}
    requester_updates = None
    if requester_id is not None and requester_id in by_owner:
        requester_updates = await upd.send_message(
            requester_id, {by_owner[requester_id]: messages[by_owner[requester_id]]}, False,
        )

    for owner_id, message_peer in by_owner.items():
        if owner_id == requester_id:
            continue
        await upd.send_message(None, {message_peer: messages[message_peer]}, False)

    return requester_updates


async def _finalize_discard(
        call: PhoneCall, user_id: int, reason: CallDiscardReason, *, send_service_message: bool = True,
) -> Updates:
    first_discard = call.discard_reason is None
    if first_discard:
        call.discard_reason = reason
        if call.started_at:
            call.duration = int((datetime.now(UTC) - call.started_at).total_seconds())
        await call.save(update_fields=["discard_reason", "duration"])

    service_updates = None
    if first_discard and send_service_message:
        service_updates = await _send_call_service_messages(call, reason, requester_id=user_id)

    if call.to_sess_id is None:
        target_authorizations = await _call_sessions_for_user(call.to_user_id)
    else:
        target_authorizations = [call.to_sess_id]

    await upd.phone_call_update(call.to_user_id, call, target_authorizations)
    result = await upd.phone_call_update(user_id, call, [call.from_sess_id])
    if service_updates is not None:
        upd.merge_updates(result, service_updates)
    return result


async def _phone_call_response(call: PhoneCall, *users: User) -> PhonePhoneCall:
    return PhonePhoneCall(
        phone_call=call.to_tl(),
        users=[await user.to_tl() for user in users],
    )


@handler.on_request(RequestCall_133, ReqHandlerFlags.BOT_NOT_ALLOWED)
@handler.on_request(RequestCall, ReqHandlerFlags.BOT_NOT_ALLOWED)
async def request_call(request: RequestCall | RequestCall_133, user: User) -> PhonePhoneCall:
    _check_protocol(request.protocol)

    if len(request.g_a_hash) != 32:
        raise ErrorRpc(error_code=400, error_message="G_A_HASH_INVALID")

    peer = await Peer.from_input_peer_raise(
        user, request.user_id, "USER_ID_INVALID", peer_types=(PeerType.USER,),
        select_related=("user",),
    )
    if peer.user.bot or peer.user.system:
        raise ErrorRpc(error_code=400, error_message="USER_ID_INVALID")
    if peer.blocked_at:
        raise ErrorRpc(error_code=403, error_message="USER_IS_BLOCKED")
    if not await PrivacyRule.has_access_to(user, peer.user, PrivacyRuleKeyType.PHONE_CALL):
        raise ErrorRpc(error_code=403, error_message="USER_PRIVACY_RESTRICTED")

    auth_id = cast(int, request_ctx.get().auth_id)
    this_auth = await UserAuthorization.get(user=user, id=auth_id)
    target_authorizations = await _call_sessions_for_user(peer.user_id)

    await _discard_stale_outgoing_calls(user.id, peer.user_id)

    missed = not target_authorizations
    call = await PhoneCall.create(
        from_user=user,
        from_sess=this_auth,
        to_user=peer.user,
        to_sess=None,
        g_a_hash=request.g_a_hash,
        discard_reason=CallDiscardReason.MISSED if missed else None,
        protocol=request.protocol.write(),
    )
    await call.fetch_related("from_user", "to_user")

    logger.info(f"Sending phone call update to authorizations: {target_authorizations}")

    if missed:
        await _send_call_service_messages(call, CallDiscardReason.MISSED, requester_id=user.id)

    await upd.phone_call_update(user.id, call, [auth_id])
    if target_authorizations:
        await upd.phone_call_update(peer.user_id, call, target_authorizations)

    return await _phone_call_response(call, user, peer.user)


@handler.on_request(DiscardCall, ReqHandlerFlags.BOT_NOT_ALLOWED | ReqHandlerFlags.DONT_FETCH_USER)
async def discard_call(request: DiscardCall, user_id: int) -> Updates:
    ctx = request_ctx.get()
    call = await _get_call(user_id, ctx.auth_id, request.peer.id, request.peer.access_hash, active_only=False)
    if call is None:
        raise ErrorRpc(error_code=400, error_message="CALL_PEER_INVALID")

    if call.discard_reason is not None:
        return await upd.phone_call_update(user_id, call, [call.from_sess_id])

    if call.to_sess_id is None:
        if user_id == call.from_user_id:
            reason = CallDiscardReason.MISSED
        else:
            reason = CallDiscardReason.BUSY
    elif isinstance(request.reason, PhoneCallDiscardReasonDisconnect):
        reason = CallDiscardReason.DISCONNECT
    else:
        reason = CallDiscardReason.HANGUP

    return await _finalize_discard(call, user_id, reason)


@handler.on_request(AcceptCall, ReqHandlerFlags.BOT_NOT_ALLOWED)
async def accept_call(request: AcceptCall, user: User) -> PhonePhoneCall:
    _validate_dh_bytes(request.g_b, "G_B_INVALID")
    _check_protocol(request.protocol)

    ctx = request_ctx.get()
    async with in_transaction():
        call = await PhoneCall.select_for_update().filter(
            to_user=user, id=request.peer.id, access_hash=request.peer.access_hash, discard_reason__isnull=True,
        ).select_related("from_user", "to_user").first()
        if call is None:
            raise ErrorRpc(error_code=400, error_message="CALL_PEER_INVALID")

        if call.g_b is not None:
            raise ErrorRpc(error_code=400, error_message="CALL_ALREADY_ACCEPTED")
        if call.to_sess_id is not None and call.to_sess_id != ctx.auth_id:
            raise ErrorRpc(error_code=400, error_message="CALL_ALREADY_ACCEPTED")

        call.to_sess = await UserAuthorization.get(user=user, id=ctx.auth_id)
        call.g_b = request.g_b
        call.protocol = _merge_protocols(call.protocol_tl_raise(), request.protocol).write()
        await call.save(update_fields=["to_sess_id", "g_b", "protocol"])

    target_authorizations = await _call_sessions_for_user(call.to_user_id)

    await upd.phone_call_update(user.id, call, target_authorizations)
    await upd.phone_call_update(call.from_user_id, call, [call.from_sess_id])

    return await _phone_call_response(call, user, call.from_user)


@handler.on_request(ConfirmCall, ReqHandlerFlags.BOT_NOT_ALLOWED)
async def confirm_call(request: ConfirmCall, user: User) -> PhonePhoneCall:
    _validate_dh_bytes(request.g_a, "G_A_INVALID")
    _check_protocol(request.protocol)

    call = await PhoneCall.get_or_none(
        from_user=user, id=request.peer.id, access_hash=request.peer.access_hash, discard_reason__isnull=True,
    ).select_related("to_user", "from_user")
    if call is None:
        raise ErrorRpc(error_code=400, error_message="CALL_PEER_INVALID")
    if call.g_b is None:
        raise ErrorRpc(error_code=400, error_message="CALL_PEER_INVALID")
    if hashlib.sha256(request.g_a).digest() != call.g_a_hash:
        raise ErrorRpc(error_code=400, error_message="G_A_INVALID")
    if call.g_a is not None:
        return await _phone_call_response(call, user, call.to_user)

    async with in_transaction():
        call = await PhoneCall.select_for_update().filter(
            from_user=user, id=request.peer.id, access_hash=request.peer.access_hash, discard_reason__isnull=True,
        ).select_related("to_user", "from_user").first()
        if call is None:
            raise ErrorRpc(error_code=400, error_message="CALL_PEER_INVALID")
        if call.g_a is not None:
            return await _phone_call_response(call, user, call.to_user)

        call.g_a = request.g_a
        call.key_fp = request.key_fingerprint
        call.protocol = _merge_protocols(call.protocol_tl_raise(), request.protocol, True).write()
        call.started_at = datetime.now(UTC)
        await call.save(update_fields=["g_a", "key_fp", "protocol", "started_at"])

    await upd.phone_call_update(user.id, call, [call.from_sess_id])
    if call.to_sess_id is not None:
        await upd.phone_call_update(call.to_user_id, call, [call.to_sess_id])

    return await _phone_call_response(call, user, call.to_user)


@handler.on_request(ReceivedCall, ReqHandlerFlags.BOT_NOT_ALLOWED)
async def received_call(request: ReceivedCall, user: User) -> bool:
    ctx = request_ctx.get()
    call = await PhoneCall.get_or_none(
        to_user=user,
        id=request.peer.id,
        access_hash=request.peer.access_hash,
        discard_reason__isnull=True,
        g_b__isnull=True,
    ).select_related("from_user", "to_user")
    if call is None:
        return True

    if call.to_sess_id is None:
        call.to_sess = await UserAuthorization.get(user=user, id=ctx.auth_id)
        await call.save(update_fields=["to_sess_id"])
        other_sessions = [
            session_id
            for session_id in await _call_sessions_for_user(user.id)
            if session_id != ctx.auth_id
        ]
        if other_sessions:
            await upd.phone_call_update(user.id, call, other_sessions)
    elif call.to_sess_id != ctx.auth_id:
        return True

    return True


@handler.on_request(SendSignalingData, ReqHandlerFlags.BOT_NOT_ALLOWED)
async def send_signaling_data(request: SendSignalingData, user: User) -> bool:
    if not request.data:
        return True

    ctx = request_ctx.get()
    call = await _get_call(user.id, ctx.auth_id, request.peer.id, request.peer.access_hash)
    if call is None or call.discard_reason is not None:
        return True

    if user.id == call.from_user_id:
        session_id = call.to_sess_id
    else:
        session_id = call.from_sess_id

    if session_id is None:
        return True

    await upd.phone_signaling_update(session_id, call.id, request.data)
    return True