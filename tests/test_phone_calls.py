import pytest

from piltover.app.handlers.phone import _finalize_discard, _send_call_service_messages
from piltover.db.enums import CallDiscardReason, MessageType
from piltover.db.models import MessageRef, Peer, PhoneCall, State, User, UserAuthorization
from piltover.tl import UpdateNewMessage, UpdatePhoneCall
from tests.client import TestClient
from tests.test_phone import _protocol


@pytest.mark.asyncio
async def test_call_service_messages_created_for_both_peers() -> None:
    from os import urandom

    async with TestClient(phone_number="123456789") as client:
        caller = await User.get(phone_number=client.phone_number)
        callee = await User.create(phone_number="987654321", first_name="Callee")
        await State.create(user_id=callee.id, pts=0)
        auth = await UserAuthorization.filter(user_id=caller.id).first()
        assert auth is not None

        call = await PhoneCall.create(
            from_user_id=caller.id,
            from_sess_id=auth.id,
            to_user_id=callee.id,
            g_a_hash=urandom(32),
            protocol=_protocol().write(),
            discard_reason=CallDiscardReason.MISSED,
        )

        await _send_call_service_messages(call, CallDiscardReason.MISSED, requester_id=caller.id)

        caller_peer = await Peer.get(owner_id=caller.id, user_id=callee.id)
        callee_peer = await Peer.get(owner_id=callee.id, user_id=caller.id)

        caller_msg = await MessageRef.filter(peer=caller_peer).order_by("-id").select_related("content").first()
        callee_msg = await MessageRef.filter(peer=callee_peer).order_by("-id").select_related("content").first()

        assert caller_msg is not None
        assert callee_msg is not None
        assert caller_msg.content_id == callee_msg.content_id
        assert caller_msg.content.type is MessageType.SERVICE_PHONE_CALL


@pytest.mark.asyncio
async def test_finalize_discard_includes_service_message_in_response() -> None:
    from os import urandom

    async with TestClient(phone_number="123456789") as client:
        caller = await User.get(phone_number=client.phone_number)
        callee = await User.create(phone_number="987654322", first_name="Callee2")
        await State.create(user_id=callee.id, pts=0)
        auth = await UserAuthorization.filter(user_id=caller.id).first()
        assert auth is not None

        call = await PhoneCall.create(
            from_user_id=caller.id,
            from_sess_id=auth.id,
            to_user_id=callee.id,
            g_a_hash=urandom(32),
            protocol=_protocol().write(),
        )
        await call.fetch_related("from_user", "to_user")

        updates = await _finalize_discard(call, caller.id, CallDiscardReason.MISSED)

        update_types = {type(update) for update in updates.updates}
        assert UpdateNewMessage in update_types
        assert UpdatePhoneCall in update_types


@pytest.mark.asyncio
async def test_search_global_finds_call_history() -> None:
    from os import urandom

    from piltover.app.handlers.messages.history import search_global
    from piltover.tl import InputMessagesFilterPhoneCalls
    from piltover.tl.functions.messages import SearchGlobal

    async with TestClient(phone_number="123456789") as client:
        user = await User.get(phone_number=client.phone_number)
        other = await User.create(phone_number="987654323", first_name="CallHist")
        await State.create(user_id=other.id, pts=0)
        auth = await UserAuthorization.filter(user_id=user.id).first()
        assert auth is not None

        call = await PhoneCall.create(
            from_user_id=user.id,
            from_sess_id=auth.id,
            to_user_id=other.id,
            g_a_hash=urandom(32),
            protocol=_protocol().write(),
            discard_reason=CallDiscardReason.HANGUP,
        )
        await _send_call_service_messages(call, CallDiscardReason.HANGUP, requester_id=user.id)

        result = await search_global(
            SearchGlobal(
                q="",
                filter=InputMessagesFilterPhoneCalls(),
                min_date=0,
                max_date=0,
                offset_rate=0,
                offset_peer=None,
                offset_id=0,
                limit=50,
            ),
            user.id,
        )

        assert len(result.messages) >= 1