import pytest
from pyrogram.raw.types import UpdateNewMessage

from piltover.app.bot_handlers.adminbot.callback_handler import adminbot_callback_query_handler
from piltover.app.bot_handlers.adminbot.utils import send_bot_message
from piltover.app.utils.spam_block import check_user_spam_blocked, set_user_spam_blocked
from piltover.db.enums import PeerType
from piltover.db.models import Peer, User
from piltover.exceptions import ErrorRpc
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


@pytest.mark.asyncio
async def test_check_user_spam_blocked_blocks_regular_peer() -> None:
    blocked = await User.create(phone_number="900000010", first_name="Blocked", spam_blocked=True)
    target = await User.create(phone_number="900000011", first_name="Target", bot=False)
    peer = await Peer.create(owner_id=blocked.id, user_id=target.id, type=PeerType.USER)
    await peer.fetch_related("user")

    with pytest.raises(ErrorRpc) as exc:
        await check_user_spam_blocked(blocked, peer)
    assert exc.value.error_message == "USER_RESTRICTED"


@pytest.mark.asyncio
async def test_check_user_spam_blocked_allows_spambot() -> None:
    blocked = await User.create(phone_number="900000012", first_name="Blocked", spam_blocked=True)
    spambot = await User.filter(username__username="spambot", system=True).first()
    assert spambot is not None
    peer = await Peer.create(owner_id=blocked.id, user_id=spambot.id, type=PeerType.USER)
    await peer.fetch_related("user", "user__username")

    await check_user_spam_blocked(blocked, peer)


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
        assert "applied" in (answer.message or "").lower()

        await target.refresh_from_db()
        assert target.spam_blocked is True