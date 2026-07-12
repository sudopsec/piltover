from __future__ import annotations

import asyncio
from datetime import datetime, UTC

from loguru import logger

import piltover.app.utils.updates_manager as upd
from piltover.app.utils.updates_manager import UpdatesWithDefaults
from piltover.app.utils.group_calls import (
    build_connection_params, build_phone_group_call, close_group_call_room,
    create_group_call, edit_group_call_participant, ensure_can_manage_call, gen_invite_hash,
    discard_group_call_if_empty, get_join_as_peers, join_group_call, leave_group_call, parse_join_client_params,
    resolve_chat_or_channel, save_default_join_as, send_group_call_service_message,
    schedule_sfu_participant_audio_state,
)
from piltover.db.models import Channel, GroupCall, GroupCallParticipant, User
from piltover.enums import ReqHandlerFlags
from piltover.exceptions import ErrorRpc
from piltover.tl import DataJSON, IntVector, Updates, UpdateGroupCall, UpdateGroupCallConnection, UpdateGroupCallParticipants
from piltover.tl.functions.phone import (
    CheckGroupCall, CreateGroupCall, DiscardGroupCall, EditGroupCallParticipant, EditGroupCallTitle,
    ExportGroupCallInvite, GetGroupCall, GetGroupCallJoinAs, GetGroupParticipants, JoinGroupCall,
    JoinGroupCall_133, LeaveGroupCall, SaveDefaultGroupCallJoinAs, StartScheduledGroupCall,
    ToggleGroupCallSettings,
)
from piltover.tl.types.phone import ExportedGroupCallInvite, GroupParticipants
from piltover.worker import MessageHandler

handler = MessageHandler("phone")


def _make_updates(chat_or_channel, *update_lists: list) -> Updates:
    updates = []
    users = []
    chats = []
    for upd_list in update_lists:
        if not upd_list:
            continue
        item = upd_list[0]
        if item is None:
            continue
        updates.extend(item.updates)
        users.extend(item.users)
        chats.extend(item.chats)
    return Updates(
        updates=updates,
        users=users,
        chats=chats,
        date=int(datetime.now(UTC).timestamp()),
        seq=0,
    )


@handler.on_request(GetGroupCallJoinAs, ReqHandlerFlags.BOT_NOT_ALLOWED | ReqHandlerFlags.DONT_FETCH_USER)
async def get_group_call_join_as(request: GetGroupCallJoinAs, user_id: int):
    chat_or_channel, _ = await resolve_chat_or_channel(user_id, request.peer)
    return await get_join_as_peers(user_id, chat_or_channel)


@handler.on_request(SaveDefaultGroupCallJoinAs, ReqHandlerFlags.BOT_NOT_ALLOWED | ReqHandlerFlags.DONT_FETCH_USER)
async def save_default_group_call_join_as(request: SaveDefaultGroupCallJoinAs, user_id: int) -> bool:
    chat_or_channel, _ = await resolve_chat_or_channel(user_id, request.peer)
    await save_default_join_as(user_id, chat_or_channel, request.join_as)
    if hasattr(chat_or_channel, "supergroup"):
        await upd.update_channel_for_user(chat_or_channel, user_id)
    return True


@handler.on_request(CreateGroupCall, ReqHandlerFlags.BOT_NOT_ALLOWED | ReqHandlerFlags.DONT_FETCH_USER)
async def create_group_call_handler(request: CreateGroupCall, user_id: int) -> Updates:
    chat_or_channel, _ = await resolve_chat_or_channel(user_id, request.peer)
    await ensure_can_manage_call(user_id, chat_or_channel)

    schedule_date = None
    if request.schedule_date is not None:
        schedule_date = datetime.fromtimestamp(request.schedule_date, UTC)

    group_call = await create_group_call(
        user_id, chat_or_channel, title=request.title, schedule_date=schedule_date,
    )
    if group_call.started_at is not None:
        call_update, _ = await asyncio.gather(
            upd.group_call_update(chat_or_channel, group_call),
            send_group_call_service_message(chat_or_channel, group_call, user_id),
        )
    else:
        call_update = await upd.group_call_update(chat_or_channel, group_call)
    return _make_updates(chat_or_channel, [call_update])


@handler.on_request(DiscardGroupCall, ReqHandlerFlags.BOT_NOT_ALLOWED | ReqHandlerFlags.DONT_FETCH_USER)
async def discard_group_call(request: DiscardGroupCall, user_id: int) -> Updates:
    group_call = await GroupCall.get_from_input_raise(request.call)
    if group_call.channel_id is not None:
        from piltover.db.models import Channel
        chat_or_channel = await Channel.get(id=group_call.channel_id)
    else:
        from piltover.db.models import Chat
        chat_or_channel = await Chat.get(id=group_call.chat_id)
    await ensure_can_manage_call(user_id, chat_or_channel)

    group_call.discarded_at = datetime.now(UTC)
    group_call.version += 1
    await group_call.save(update_fields=["discarded_at", "version"])
    await GroupCallParticipant.filter(group_call=group_call, left=False).update(left=True)
    await close_group_call_room(group_call.id)

    reference = group_call.started_at or group_call.created_at
    duration = int((group_call.discarded_at - reference).total_seconds())
    await send_group_call_service_message(chat_or_channel, group_call, user_id, duration=duration)

    call_update = await upd.group_call_update(chat_or_channel, group_call)
    return _make_updates(chat_or_channel, [call_update])


@handler.on_request(StartScheduledGroupCall, ReqHandlerFlags.BOT_NOT_ALLOWED | ReqHandlerFlags.DONT_FETCH_USER)
async def start_scheduled_group_call(request: StartScheduledGroupCall, user_id: int) -> Updates:
    group_call = await GroupCall.get_from_input_raise(request.call)
    if group_call.started_at is not None:
        raise ErrorRpc(error_code=400, error_message="GROUPCALL_ALREADY_STARTED")

    if group_call.channel_id is not None:
        from piltover.db.models import Channel
        chat_or_channel = await Channel.get(id=group_call.channel_id)
    else:
        from piltover.db.models import Chat
        chat_or_channel = await Chat.get(id=group_call.chat_id)
    await ensure_can_manage_call(user_id, chat_or_channel)

    group_call.started_at = datetime.now(UTC)
    group_call.version += 1
    await group_call.save(update_fields=["started_at", "version"])

    call_update, _ = await asyncio.gather(
        upd.group_call_update(chat_or_channel, group_call),
        send_group_call_service_message(chat_or_channel, group_call, user_id),
    )
    return _make_updates(chat_or_channel, [call_update])


@handler.on_request(JoinGroupCall_133, ReqHandlerFlags.BOT_NOT_ALLOWED | ReqHandlerFlags.DONT_FETCH_USER)
@handler.on_request(JoinGroupCall, ReqHandlerFlags.BOT_NOT_ALLOWED | ReqHandlerFlags.DONT_FETCH_USER)
async def join_group_call_handler(request: JoinGroupCall | JoinGroupCall_133, user_id: int) -> Updates:
    group_call = await GroupCall.get_from_input_raise(request.call)
    if group_call.channel_id is not None:
        from piltover.db.models import Channel
        chat_or_channel = await Channel.get(id=group_call.channel_id)
    else:
        from piltover.db.models import Chat
        chat_or_channel = await Chat.get(id=group_call.chat_id)

    client_params, client_ssrc = parse_join_client_params(request.params)

    participant, just_joined = await join_group_call(
        user_id,
        group_call,
        request.join_as,
        muted=request.muted,
        video_stopped=request.video_stopped,
        invite_hash=getattr(request, "invite_hash", None),
        client_ssrc=client_ssrc,
    )
    await participant.fetch_related("user")

    schedule_sfu_participant_audio_state(group_call.id, participant)

    async def _load_active_participants() -> list[GroupCallParticipant]:
        return list(await GroupCallParticipant.filter(
            group_call=group_call, left=False,
        ).select_related("user", "join_as_user", "join_as_channel").order_by("joined_at"))

    _, all_participants = await asyncio.gather(
        upd.group_call_participants_update_with_call_rpc(
            chat_or_channel, group_call, [participant],
            exclude_user_ids=[user_id], just_joined=just_joined,
            participant_versioned=False,
        ),
        _load_active_participants(),
    )

    user_ids: set[int] = set()
    for call_participant in all_participants:
        user_ids.add(call_participant.user_id)
        if call_participant.join_as_user_id is not None:
            user_ids.add(call_participant.join_as_user_id)

    participants_count = len(all_participants)
    connection_params, users_tl, call_tl, chat_tl = await asyncio.gather(
        build_connection_params(
            request.params,
            group_call_id=group_call.id,
            user_id=user_id,
            source=participant.source,
        ),
        User.to_tl_bulk(await User.filter(id__in=user_ids)),
        group_call.to_tl(participants_count=participants_count),
        chat_or_channel.to_tl(),
    )

    return UpdatesWithDefaults(
        updates=[
            UpdateGroupCall(
                chat_id=chat_or_channel.make_id(),
                call=call_tl,
            ),
            UpdateGroupCallParticipants(
                call=group_call.to_input(),
                participants=[
                    call_participant.to_tl(
                        self_user_id=user_id,
                        just_joined=call_participant.user_id == user_id and just_joined,
                        versioned=False,
                    )
                    for call_participant in all_participants
                ],
                version=group_call.version,
            ),
            UpdateGroupCallConnection(params=connection_params),
        ],
        users=users_tl,
        chats=[chat_tl],
    )


@handler.on_request(EditGroupCallParticipant, ReqHandlerFlags.BOT_NOT_ALLOWED | ReqHandlerFlags.DONT_FETCH_USER)
async def edit_group_call_participant_handler(request: EditGroupCallParticipant, user_id: int) -> Updates:
    group_call = await GroupCall.get_from_input_raise(request.call)
    if group_call.channel_id is not None:
        from piltover.db.models import Channel
        chat_or_channel = await Channel.get(id=group_call.channel_id)
    else:
        from piltover.db.models import Chat
        chat_or_channel = await Chat.get(id=group_call.chat_id)

    participant, changed = await edit_group_call_participant(
        user_id,
        group_call,
        chat_or_channel,
        request.participant,
        muted=request.muted,
        volume=request.volume,
        raise_hand=request.raise_hand,
        video_stopped=request.video_stopped,
        video_paused=request.video_paused,
    )
    await participant.fetch_related("user", "join_as_user")

    if not changed:
        editing_self = participant.user_id == user_id
        reassert_admin_mute = (
            editing_self
            and request.muted is False
            and not participant.can_self_unmute_participant()
        )
        logger.info(
            "GroupCallMute edit noop response editor={} call={} version={} reassert={} {}",
            user_id,
            group_call.id,
            group_call.version,
            reassert_admin_mute,
            participant.format_mute_debug(self_user_id=user_id, versioned=False),
        )
        participants_update = await upd.group_call_participants_update(
            chat_or_channel, group_call, [participant], self_user_id=user_id,
            broadcast=reassert_admin_mute,
            to_participants_only=False,
            participant_versioned=False,
        )
        return _make_updates(chat_or_channel, [participants_update])

    # versioned=false: Telegram applies mute/volume/hand edits unconditionally
    # (see core.telegram.org/api/group-calls#applying-group-call-updates). With
    # versioned=true, clients can skip the update on version gaps and keep stale
    # can_self_unmute=true (red mic instead of admin-mute person icon).
    # Admin edits exclude the editor (RPC already has their view). Self-edits
    # (e.g. raise hand while admin-muted) must also reach the editor via broadcast.
    editing_self = participant.user_id == user_id
    participants_update = await upd.group_call_participants_update(
        chat_or_channel, group_call, [participant], self_user_id=user_id,
        exclude_user_ids=None if editing_self else [user_id],
        to_participants_only=False,
        participant_versioned=False,
    )
    logger.info(
        "GroupCallMute edit response editor={} call={} version={} {}",
        user_id,
        group_call.id,
        group_call.version,
        participant.format_mute_debug(self_user_id=user_id, versioned=False),
    )
    return _make_updates(chat_or_channel, [participants_update])


@handler.on_request(LeaveGroupCall, ReqHandlerFlags.BOT_NOT_ALLOWED | ReqHandlerFlags.DONT_FETCH_USER)
async def leave_group_call_handler(request: LeaveGroupCall, user_id: int) -> Updates:
    group_call = await GroupCall.get_from_input_raise(request.call)
    if group_call.channel_id is not None:
        from piltover.db.models import Channel
        chat_or_channel = await Channel.get(id=group_call.channel_id)
    else:
        from piltover.db.models import Chat
        chat_or_channel = await Chat.get(id=group_call.chat_id)

    participant = await leave_group_call(group_call, user_id, request.source)
    if participant is None:
        participant = await GroupCallParticipant.get_or_none(group_call=group_call, user_id=user_id)
        if participant is None:
            return Updates(updates=[], users=[], chats=[], date=int(datetime.now(UTC).timestamp()), seq=0)
        await participant.fetch_related("user")
        participants_update = await upd.group_call_participants_update(
            chat_or_channel, group_call, [participant], self_user_id=user_id,
            broadcast=False, participant_versioned=False,
        )
        return _make_updates(chat_or_channel, [participants_update])

    await participant.fetch_related("user")
    if await discard_group_call_if_empty(group_call):
        call_update = await upd.group_call_update(chat_or_channel, group_call)
        return _make_updates(chat_or_channel, [call_update])

    await upd.group_call_participants_update_with_call_rpc(
        chat_or_channel, group_call, [participant],
        exclude_user_ids=[user_id], just_joined=False,
        participant_versioned=False,
    )
    participants_update = await upd.group_call_participants_update(
        chat_or_channel, group_call, [participant],
        self_user_id=user_id, broadcast=False, participant_versioned=False,
    )
    return _make_updates(chat_or_channel, [participants_update])


@handler.on_request(GetGroupCall, ReqHandlerFlags.BOT_NOT_ALLOWED | ReqHandlerFlags.DONT_FETCH_USER)
async def get_group_call(request: GetGroupCall, user_id: int):
    group_call = await GroupCall.get_from_input_raise(request.call)
    return await build_phone_group_call(group_call, request.limit, self_user_id=user_id)


async def _group_participants_response(
        group_call: GroupCall,
        participants: list[GroupCallParticipant],
        *,
        user_id: int,
        count: int | None = None,
) -> GroupParticipants:
    users: dict[int, User] = {}
    channels: dict[int, Channel] = {}
    for participant in participants:
        users[participant.user_id] = participant.user
        if participant.join_as_user_id is not None:
            users[participant.join_as_user_id] = participant.join_as_user
        if participant.join_as_channel_id is not None:
            channels[participant.join_as_channel_id] = participant.join_as_channel

    if count is None:
        count = len(participants)

    return GroupParticipants(
        count=count,
        participants=[
            participant.to_tl(self_user_id=user_id, versioned=False)
            for participant in participants
        ],
        next_offset="",
        chats=await Channel.to_tl_bulk(channels.values()) if channels else [],
        users=await User.to_tl_bulk(users.values()),
        version=group_call.version,
    )


@handler.on_request(GetGroupParticipants, ReqHandlerFlags.BOT_NOT_ALLOWED | ReqHandlerFlags.DONT_FETCH_USER)
async def get_group_participants(request: GetGroupParticipants, user_id: int) -> GroupParticipants:
    from piltover.tl import PeerChannel, PeerUser

    group_call = await GroupCall.get_from_input_any(request.call)
    if group_call is None:
        raise ErrorRpc(error_code=400, error_message="GROUPCALL_INVALID")
    if group_call.discarded_at is not None:
        return GroupParticipants(
            count=0,
            participants=[],
            next_offset="",
            chats=[],
            users=[],
            version=group_call.version,
        )

    active_count = await group_call.participants_count()
    query = GroupCallParticipant.filter(group_call=group_call).select_related(
        "user", "join_as_user", "join_as_channel",
    )

    if request.sources:
        # Source lookup must include left participants — the client resolves SSRCs
        # after leave and stops retrying only once it gets left=true for that source.
        participants = list(
            await query.filter(source__in=request.sources).order_by("joined_at").limit(request.limit)
        )
        return await _group_participants_response(
            group_call, participants, user_id=user_id, count=active_count,
        )

    query = query.filter(left=False)

    if request.ids:
        peer_user_ids: list[int] = []
        peer_channel_ids: list[int] = []
        for peer in request.ids:
            if isinstance(peer, PeerUser):
                peer_user_ids.append(peer.user_id)
            elif isinstance(peer, PeerChannel):
                peer_channel_ids.append((peer.channel_id - 1) // 2)

        from tortoise.expressions import Q

        peer_q = Q()
        if peer_user_ids:
            peer_q |= Q(user_id__in=peer_user_ids) | Q(join_as_user_id__in=peer_user_ids)
        if peer_channel_ids:
            peer_q |= Q(join_as_channel_id__in=peer_channel_ids)
        if peer_q:
            query = query.filter(peer_q)

    participants = list(await query.order_by("joined_at").limit(request.limit))
    count = len(participants) if len(participants) < request.limit else active_count
    return await _group_participants_response(
        group_call, participants, user_id=user_id, count=count,
    )


@handler.on_request(CheckGroupCall, ReqHandlerFlags.BOT_NOT_ALLOWED | ReqHandlerFlags.DONT_FETCH_USER)
async def check_group_call(request: CheckGroupCall, user_id: int) -> IntVector:
    group_call = await GroupCall.get_from_input_raise(request.call)
    joined = await GroupCallParticipant.get_or_none(group_call=group_call, user_id=user_id, left=False)
    if joined is None:
        raise ErrorRpc(error_code=400, error_message="GROUPCALL_JOIN_MISSING")

    existing = set(await GroupCallParticipant.filter(
        group_call=group_call, left=False, source__in=request.sources,
    ).values_list("source", flat=True))
    return IntVector([source for source in request.sources if source in existing])


@handler.on_request(ToggleGroupCallSettings, ReqHandlerFlags.BOT_NOT_ALLOWED | ReqHandlerFlags.DONT_FETCH_USER)
async def toggle_group_call_settings(request: ToggleGroupCallSettings, user_id: int) -> Updates:
    group_call = await GroupCall.get_from_input_raise(request.call)
    if group_call.channel_id is not None:
        from piltover.db.models import Channel
        chat_or_channel = await Channel.get(id=group_call.channel_id)
    else:
        from piltover.db.models import Chat
        chat_or_channel = await Chat.get(id=group_call.chat_id)
    await ensure_can_manage_call(user_id, chat_or_channel)

    update_fields = ["version"]
    if request.join_muted is not None:
        group_call.join_muted = request.join_muted
        update_fields.append("join_muted")
    if request.reset_invite_hash:
        group_call.invite_hash = gen_invite_hash()
        update_fields.append("invite_hash")
    group_call.version += 1
    await group_call.save(update_fields=update_fields)

    call_update = await upd.group_call_update(chat_or_channel, group_call)
    return _make_updates(chat_or_channel, [call_update])


@handler.on_request(EditGroupCallTitle, ReqHandlerFlags.BOT_NOT_ALLOWED | ReqHandlerFlags.DONT_FETCH_USER)
async def edit_group_call_title(request: EditGroupCallTitle, user_id: int) -> Updates:
    group_call = await GroupCall.get_from_input_raise(request.call)
    if group_call.channel_id is not None:
        from piltover.db.models import Channel
        chat_or_channel = await Channel.get(id=group_call.channel_id)
    else:
        from piltover.db.models import Chat
        chat_or_channel = await Chat.get(id=group_call.chat_id)
    await ensure_can_manage_call(user_id, chat_or_channel)

    group_call.title = request.title
    group_call.version += 1
    await group_call.save(update_fields=["title", "version"])
    call_update = await upd.group_call_update(chat_or_channel, group_call)
    return _make_updates(chat_or_channel, [call_update])


@handler.on_request(ExportGroupCallInvite, ReqHandlerFlags.BOT_NOT_ALLOWED | ReqHandlerFlags.DONT_FETCH_USER)
async def export_group_call_invite(request: ExportGroupCallInvite, user_id: int) -> ExportedGroupCallInvite:
    group_call = await GroupCall.get_from_input_raise(request.call)
    if request.can_self_unmute:
        if group_call.invite_hash is None:
            group_call.invite_hash = gen_invite_hash()
            await group_call.save(update_fields=["invite_hash"])
        return ExportedGroupCallInvite(link=f"https://t.me/c/{group_call.id}/{group_call.invite_hash}")
    return ExportedGroupCallInvite(link=f"https://t.me/c/{group_call.id}")