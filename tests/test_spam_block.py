from io import BytesIO

import pytest
from pyrogram.errors import PeerFlood, RPCError
from pyrogram.raw.functions.channels import CreateChannel
from pyrogram.raw.functions.messages import CreateChat
from pyrogram.raw.functions.phone import RequestCall
from pyrogram.raw.types import InputUser, PhoneCallProtocol, UpdateNewMessage

from piltover.app.bot_handlers.adminbot.callback_handler import adminbot_callback_query_handler
from piltover.app.bot_handlers.adminbot.utils import send_bot_message
from piltover.app.handlers.users import _PEER_FULL_USER_ONLY
from piltover.app.utils.spam_block import check_spam_blocked_creation, check_user_spam_blocked, peer_has_incoming_contact, \
    set_user_spam_blocked, user_spam_blocked
from piltover.db.enums import PeerType
from piltover.db.models import Chat, ChatParticipant, MessageRef, Peer, User, UserAuthorization
from piltover.exceptions import ErrorRpc
from piltover.tl import Int
from piltover.tl.serialization_context import SerializationContext
from piltover.tl.to_format import UserToFormat
from tests.client import TestClient


@pytest.mark.asyncio
async def test_spambot_shows_clear_status() -> None:
    async with TestClient(phone_number="123456789") as client:
        bot = await client.get_users("spambot")
        await client.send_message(bot.id, "/start")

        user_message = await client.expect_update(UpdateNewMessage)
        bot_message = await client.expect_update(UpdateNewMessage)

        if user_message.message.from_id.user_id != client.me.id:
            user_message, bot_message = bot_message, user_message

        assert "no limits" in bot_message.message.message.lower()


@pytest.mark.asyncio
async def test_spambot_shows_limited_status() -> None:
    async with TestClient(phone_number="123456789") as client:
        user = await User.get(phone_number=client.phone_number)
        await set_user_spam_blocked(user, True)

        bot = await client.get_users("spambot")
        await client.send_message(bot.id, "/start")

        user_message = await client.expect_update(UpdateNewMessage)
        bot_message = await client.expect_update(UpdateNewMessage)

        if user_message.message.from_id.user_id != client.me.id:
            user_message, bot_message = bot_message, user_message

        assert "limited" in bot_message.message.message.lower()

        await set_user_spam_blocked(user, False)


def _user_flags(data: bytes) -> int:
    stream = BytesIO(data)
    Int.read(stream)
    return Int.read(stream)


@pytest.mark.asyncio
async def test_spam_blocked_self_user_has_restricted_tl_flags() -> None:
    user = UserToFormat(id=42, first_name="Blocked", lang_code="en", spam_blocked=True)
    ctx = SerializationContext(auth_id=1, user_id=42, layer=200)
    flags = _user_flags(user.write(ctx))

    assert flags & (1 << 10)
    assert flags & (1 << 18)
    assert b"spam" in user.write(ctx)
    assert b"all" in user.write(ctx)


@pytest.mark.asyncio
async def test_spam_blocked_not_visible_to_other_users_in_tl() -> None:
    user = UserToFormat(id=42, first_name="Blocked", lang_code="en", spam_blocked=True)
    ctx = SerializationContext(auth_id=1, user_id=99, layer=200)
    flags = _user_flags(user.write(ctx))

    assert not (flags & (1 << 10))
    assert not (flags & (1 << 18))


@pytest.mark.asyncio
async def test_check_user_spam_blocked_blocks_regular_peer() -> None:
    blocked = await User.create(phone_number="900000010", first_name="Blocked", spam_blocked=True)
    target = await User.create(phone_number="900000011", first_name="Target", bot=False)
    peer = await Peer.create(owner_id=blocked.id, user_id=target.id, type=PeerType.USER)
    await peer.fetch_related("user")

    with pytest.raises(ErrorRpc) as exc:
        await check_user_spam_blocked(blocked, peer)
    assert exc.value.error_message == "PEER_FLOOD"
    assert exc.value.error_code == 400


@pytest.mark.asyncio
async def test_check_user_spam_blocked_allows_any_bot() -> None:
    blocked = await User.create(phone_number="900000012", first_name="Blocked", spam_blocked=True)
    spambot = await User.filter(username__username="spambot", system=True).first()
    assert spambot is not None
    peer = await Peer.create(owner_id=blocked.id, user_id=spambot.id, type=PeerType.USER)
    await peer.fetch_related("user", "user__username")

    await check_user_spam_blocked(blocked, peer)

    other_bot = await User.create(phone_number=None, first_name="Helper", bot=True)
    other_bot_peer = await Peer.create(owner_id=blocked.id, user_id=other_bot.id, type=PeerType.USER)
    await check_user_spam_blocked(blocked, other_bot_peer)


@pytest.mark.asyncio
async def test_check_user_spam_blocked_allows_reply_to_incoming() -> None:
    blocked = await User.create(phone_number="900000013", first_name="Blocked", spam_blocked=True)
    target = await User.create(phone_number="900000014", first_name="Target", bot=False)
    peer = await Peer.create(owner_id=blocked.id, user_id=target.id, type=PeerType.USER)
    await peer.fetch_related("user")

    incoming = await MessageRef.create_for_peer(peer, target, opposite=True, message="hi")
    incoming_ref = incoming[peer]

    await check_user_spam_blocked(blocked, peer, reply_to_message_id=incoming_ref.id)


@pytest.mark.asyncio
async def test_check_user_spam_blocked_allows_follow_up_without_reply() -> None:
    blocked = await User.create(phone_number="900000023", first_name="Blocked", spam_blocked=True)
    target = await User.create(phone_number="900000024", first_name="Target", bot=False)
    peer = await Peer.create(owner_id=blocked.id, user_id=target.id, type=PeerType.USER)
    await peer.fetch_related("user")

    await MessageRef.create_for_peer(peer, target, opposite=True, message="hi")
    assert await peer_has_incoming_contact(peer, blocked.id)

    await check_user_spam_blocked(blocked, peer)


@pytest.mark.asyncio
async def test_check_user_spam_blocked_allows_call_back_after_incoming_call() -> None:
    from hashlib import sha1
    from os import urandom

    from piltover.db.models import AuthKey, PhoneCall, UserAuthorization
    from piltover.tl import Long

    blocked = await User.create(phone_number="900000025", first_name="Blocked", spam_blocked=True)
    target = await User.create(phone_number="900000026", first_name="Target", bot=False)
    peer = await Peer.create(owner_id=blocked.id, user_id=target.id, type=PeerType.USER)
    await peer.fetch_related("user")

    async def _make_auth(user: User) -> UserAuthorization:
        key = urandom(256)
        key_id = Long.read_bytes(sha1(key).digest()[-8:])
        auth_key = await AuthKey.create(id=key_id, auth_key=key)
        return await UserAuthorization.create(user=user, key=auth_key, ip="0.0.0.0", allow_call_requests=True)

    target_auth = await _make_auth(target)
    blocked_auth = await _make_auth(blocked)
    await PhoneCall.create(
        from_user=target, from_sess=target_auth, to_user=blocked, to_sess=blocked_auth,
        g_a_hash=b"\x01" * 32, protocol=b"",
    )

    assert await peer_has_incoming_contact(peer, blocked.id)
    await check_user_spam_blocked(blocked, peer)


@pytest.mark.asyncio
async def test_check_user_spam_blocked_blocks_cold_message() -> None:
    blocked = await User.create(phone_number="900000015", first_name="Blocked", spam_blocked=True)
    target = await User.create(phone_number="900000016", first_name="Target", bot=False)
    peer = await Peer.create(owner_id=blocked.id, user_id=target.id, type=PeerType.USER)
    await peer.fetch_related("user")

    with pytest.raises(ErrorRpc) as exc:
        await check_user_spam_blocked(blocked, peer)
    assert exc.value.error_message == "PEER_FLOOD"


@pytest.mark.asyncio
async def test_check_user_spam_blocked_blocks_reply_to_own_message() -> None:
    blocked = await User.create(phone_number="900000017", first_name="Blocked", spam_blocked=True)
    target = await User.create(phone_number="900000018", first_name="Target", bot=False)
    peer = await Peer.create(owner_id=blocked.id, user_id=target.id, type=PeerType.USER)
    await peer.fetch_related("user")

    outgoing = await MessageRef.create_for_peer(peer, blocked, opposite=True, message="hey")
    outgoing_ref = outgoing[peer]

    with pytest.raises(ErrorRpc) as exc:
        await check_user_spam_blocked(blocked, peer, reply_to_message_id=outgoing_ref.id)
    assert exc.value.error_message == "PEER_FLOOD"


@pytest.mark.asyncio
async def test_user_spam_blocked_reads_from_db_when_not_prefetched() -> None:
    user = await User.create(phone_number="900000021", first_name="Blocked", spam_blocked=True)
    partial = await User.get(id=user.id).only("id")

    assert await user_spam_blocked(partial) is True


def test_get_full_user_prefetches_spam_blocked() -> None:
    assert "user__spam_blocked" in _PEER_FULL_USER_ONLY


@pytest.mark.asyncio
async def test_check_user_spam_blocked_allows_admin_group() -> None:
    blocked = await User.create(phone_number="900000019", first_name="Blocked", spam_blocked=True)
    chat = await Chat.create(name="Admin group", creator_id=blocked.id, participants_count=1)
    peer = await Peer.create(owner_id=blocked.id, chat_id=chat.id, type=PeerType.CHAT)
    await ChatParticipant.create(user_id=blocked.id, chat=chat, chat_channel_id=chat.make_id())

    await check_user_spam_blocked(blocked, peer)


@pytest.mark.asyncio
async def test_check_spam_blocked_creation_blocks_new_chat() -> None:
    blocked = await User.create(phone_number="900000020", first_name="Blocked", spam_blocked=True)

    with pytest.raises(ErrorRpc) as exc:
        await check_spam_blocked_creation(blocked)
    assert exc.value.error_message == "USER_RESTRICTED"


@pytest.mark.asyncio
async def test_spam_blocked_can_request_call_after_incoming_message() -> None:
    async with TestClient(phone_number="123456789") as client1, TestClient(phone_number="1234567890") as client2:
        await client1.resolve_user(client2, False)
        await client2.resolve_user(client1, False)

        caller = await User.get(phone_number=client1.phone_number)
        target = await User.get(phone_number=client2.phone_number)
        await set_user_spam_blocked(caller, True)

        await client2.send_message(client1.me.id, "stranger says hi")

        auth = await UserAuthorization.filter(user_id=caller.id).first()
        assert auth is not None
        access_hash = User.make_access_hash(caller.id, auth.id, target.id)

        result = await client1.invoke(RequestCall(
            user_id=InputUser(user_id=target.id, access_hash=access_hash),
            random_id=54322,
            g_a_hash=b"\x00" * 32,
            protocol=PhoneCallProtocol(
                udp_p2p=True, udp_reflector=True, min_layer=92, max_layer=92, library_versions=["2.7.7"],
            ),
        ))
        assert result is not None

        await set_user_spam_blocked(caller, False)


@pytest.mark.asyncio
async def test_spam_blocked_can_reply_after_incoming_message() -> None:
    async with TestClient(phone_number="123456789") as client1, TestClient(phone_number="1234567890") as client2:
        await client1.resolve_user(client2, False)
        await client2.resolve_user(client1, False)

        await client2.send_message(client1.me.id, "hello from stranger")

        user = await User.get(phone_number=client1.phone_number)
        await set_user_spam_blocked(user, True)

        message = await client1.send_message(client2.me.id, "allowed reply")
        assert message.text == "allowed reply"

        await set_user_spam_blocked(user, False)


@pytest.mark.asyncio
async def test_spam_blocked_cannot_request_call() -> None:
    async with TestClient(phone_number="123456789") as client1, TestClient(phone_number="1234567890") as client2:
        caller = await User.get(phone_number=client1.phone_number)
        target = await User.get(phone_number=client2.phone_number)
        await set_user_spam_blocked(caller, True)

        auth = await UserAuthorization.filter(user_id=caller.id).first()
        assert auth is not None
        access_hash = User.make_access_hash(caller.id, auth.id, target.id)
        await Peer.create(owner_id=caller.id, user_id=target.id, type=PeerType.USER)

        with pytest.raises(PeerFlood):
            await client1.invoke(RequestCall(
                user_id=InputUser(user_id=target.id, access_hash=access_hash),
                random_id=54321,
                g_a_hash=b"\x00" * 32,
                protocol=PhoneCallProtocol(
                    udp_p2p=True, udp_reflector=True, min_layer=92, max_layer=92, library_versions=["2.7.7"],
                ),
            ))

        await set_user_spam_blocked(caller, False)


@pytest.mark.asyncio
async def test_spam_blocked_send_message_returns_peer_flood() -> None:
    async with TestClient(phone_number="123456789") as client1, TestClient(phone_number="1234567890") as client2:
        await client2.set_username("spam_block_target")

        user = await User.get(phone_number=client1.phone_number)
        await set_user_spam_blocked(user, True)

        with pytest.raises(PeerFlood):
            await client1.send_message("spam_block_target", "blocked cold message")

        await set_user_spam_blocked(user, False)


@pytest.mark.asyncio
async def test_spam_blocked_cannot_create_chat_or_channel() -> None:
    async with TestClient(phone_number="123456789") as client:
        user = await User.get(phone_number=client.phone_number)
        await set_user_spam_blocked(user, True)

        with pytest.raises(RPCError) as exc:
            await client.invoke(CreateChat(users=[InputUser(user_id=user.id, access_hash=0)], title="blocked"))
        assert exc.value.ID == "USER_RESTRICTED"

        with pytest.raises(RPCError) as exc:
            await client.invoke(CreateChannel(title="blocked", about="", megagroup=True))
        assert exc.value.ID == "USER_RESTRICTED"

        await set_user_spam_blocked(user, False)


@pytest.mark.asyncio
async def test_admin_toggle_spam_block() -> None:
    target = await User.create(phone_number="900000012", first_name="SpamTarget", spam_blocked=False)

    async with TestClient(phone_number="123456789") as client:
        admin_user = await User.get(phone_number=client.phone_number)
        admin_user.admin = True
        await admin_user.save(update_fields=["admin"])

        bot = await client.get_users("admin")
        peer = await Peer.get(owner_id=admin_user.id, user_id=bot.id)
        menu = await send_bot_message(peer, "menu", None)

        answer = await adminbot_callback_query_handler(
            peer, menu, f"adm:act:spam:{target.id}:u0".encode(),
        )
        assert answer is not None
        assert "применён" in (answer.message or "").lower()

        await target.refresh_from_db()
        assert target.spam_blocked is True