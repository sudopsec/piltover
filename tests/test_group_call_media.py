from datetime import datetime, UTC

import pytest

from piltover.app.utils.group_calls import (
    create_group_call,
    detect_join_stream_kind,
    join_group_call,
    join_group_call_presentation,
    join_group_call_video,
    leave_group_call_presentation,
    participant_active_sources,
    resolve_join_muted,
)
from piltover.db.models import Channel, Chat, GroupCall, GroupCallParticipant, State, User
from piltover.tl import InputPeerSelf


def _started_at() -> datetime:
    return datetime.now(UTC)


async def _make_user(phone: str) -> User:
    user = await User.create(phone_number=phone, first_name="Test")
    await State.create(user=user)
    return user


@pytest.mark.asyncio
async def test_create_group_call_broadcast_channel_join_muted() -> None:
    owner = await _make_user("900100001")
    channel = await Channel.create(name="Live stream", creator=owner, channel=True, supergroup=False)
    group_call = await create_group_call(owner.id, channel)
    assert group_call.join_muted is True


@pytest.mark.asyncio
async def test_create_group_call_supergroup_not_join_muted() -> None:
    owner = await _make_user("900100001b")
    channel = await Channel.create(name="Discussion SG", creator=owner, channel=False, supergroup=True)
    group_call = await create_group_call(owner.id, channel)
    assert group_call.join_muted is False


@pytest.mark.asyncio
async def test_create_group_call_chat_not_join_muted() -> None:
    owner = await _make_user("900100002")
    chat = await Chat.create(name="VC chat", creator=owner)
    group_call = await create_group_call(owner.id, chat)
    assert group_call.join_muted is False


@pytest.mark.asyncio
async def test_resolve_join_muted_for_channel_call() -> None:
    owner = await _make_user("900100003")
    channel = await Channel.create(name="Muted broadcast", creator=owner, channel=True, supergroup=False)
    group_call = await GroupCall.create(
        creator=owner, channel=channel, title="call", started_at=_started_at(),
        join_muted=True,
    )
    assert resolve_join_muted(False, group_call) is True


@pytest.mark.asyncio
async def test_join_broadcast_channel_participant_muted() -> None:
    owner = await _make_user("900100003b")
    member = await _make_user("900100003c")
    channel = await Channel.create(name="Stream", creator=owner, channel=True, supergroup=False)
    group_call = await create_group_call(owner.id, channel)

    participant, _ = await join_group_call(
        member.id,
        group_call,
        InputPeerSelf(),
        muted=False,
        video_stopped=True,
        invite_hash=None,
        client_ssrc=555555,
    )
    assert group_call.join_muted is True
    assert participant.muted is True


@pytest.mark.asyncio
async def test_detect_join_stream_kind() -> None:
    audio_payload = {"payload-types": [{"name": "opus", "id": 111, "clockrate": 48000, "channels": 2}]}
    video_payload = {"payload-types": [{"name": "VP8", "id": 96, "clockrate": 90000}]}
    assert detect_join_stream_kind(audio_payload) == "audio"
    assert detect_join_stream_kind(video_payload) == "video"
    assert detect_join_stream_kind(video_payload, is_presentation=True) == "presentation"


@pytest.mark.asyncio
async def test_join_group_call_video_and_presentation() -> None:
    owner = await _make_user("900100004")
    member = await _make_user("900100005")
    channel = await Channel.create(name="Media SG", creator=owner, channel=False, supergroup=True)
    group_call = await GroupCall.create(
        creator=owner, channel=channel, title="call", started_at=_started_at(),
        join_muted=False,
    )

    participant, _ = await join_group_call(
        member.id,
        group_call,
        InputPeerSelf(),
        muted=False,
        video_stopped=True,
        invite_hash=None,
        client_ssrc=111111,
    )
    assert participant.muted is False

    video_payload = {
        "payload-types": [{"name": "VP8", "id": 96, "clockrate": 90000}],
        "ssrc-groups": [{"semantics": "FID", "sources": [222222, 222223]}],
    }
    participant = await join_group_call_video(
        member.id,
        group_call,
        client_ssrc=222222,
        client_payload=video_payload,
        video_stopped=False,
    )
    assert participant.video_source == 222222
    assert participant.video_stopped is False
    assert participant.video_endpoint is not None

    presentation_payload = {
        "payload-types": [{"name": "VP8", "id": 97, "clockrate": 90000}],
        "ssrc-groups": [{"semantics": "default", "sources": [333333]}],
    }
    participant = await join_group_call_presentation(
        member.id,
        group_call,
        client_ssrc=333333,
        client_payload=presentation_payload,
    )
    assert participant.presentation_source == 333333
    assert participant.presentation_endpoint is not None

    tl = participant.to_tl(self_user_id=member.id)
    assert tl.video is not None
    assert tl.presentation is not None
    assert tl.video.audio_source == participant.source

    sources = participant_active_sources(participant)
    assert sources == {111111, 222222, 333333}

    participant = await leave_group_call_presentation(member.id, group_call)
    tl = participant.to_tl(self_user_id=member.id)
    assert tl.presentation is None
    assert participant.presentation_source is None


@pytest.mark.asyncio
async def test_participant_to_tl_without_media() -> None:
    owner = await _make_user("900100006")
    channel = await Channel.create(name="Audio only", creator=owner, channel=False, supergroup=True)
    group_call = await GroupCall.create(
        creator=owner, channel=channel, title="call", started_at=_started_at(),
    )
    participant = await GroupCallParticipant.create(
        group_call=group_call,
        user=owner,
        source=444444,
        muted=False,
    )
    tl = participant.to_tl(self_user_id=owner.id)
    assert tl.video is None
    assert tl.presentation is None