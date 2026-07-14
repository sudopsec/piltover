import pytest

from piltover.app.bot_handlers.adminbot.callback_handler import adminbot_callback_query_handler
from piltover.app.bot_handlers.adminbot.utils import send_bot_message
from piltover.app.utils.admin_sessions import kick_all_user_sessions

from piltover.db.models import Peer, User, UserAuthorization, UserStarsBalance
from tests.client import TestClient


@pytest.mark.asyncio
async def test_admin_user_page_preserves_list_page() -> None:
    users = [
        await User.create(phone_number=f"90010000{i}", first_name=f"User{i}", admin=False)
        for i in range(10)
    ]
    target = users[-1]

    async with TestClient(phone_number="123456789") as client:
        admin_user = await User.get(phone_number=client.phone_number)
        admin_user.admin = True
        await admin_user.save(update_fields=["admin"])

        bot = await client.get_users("admin")
        peer = await Peer.get(owner_id=admin_user.id, user_id=bot.id)
        menu = await send_bot_message(peer, "menu", None)

        await adminbot_callback_query_handler(peer, menu, b"adm:users:1")
        await adminbot_callback_query_handler(peer, menu, f"adm:user:{target.id}:u1".encode())

        answer = await adminbot_callback_query_handler(peer, menu, b"adm:users:1")
        assert answer is not None


@pytest.mark.asyncio
async def test_admin_set_stars_balance() -> None:
    target = await User.create(phone_number="900000020", first_name="StarsTarget")

    async with TestClient(phone_number="123456789") as client:
        admin_user = await User.get(phone_number=client.phone_number)
        admin_user.admin = True
        await admin_user.save(update_fields=["admin"])

        bot = await client.get_users("admin")
        peer = await Peer.get(owner_id=admin_user.id, user_id=bot.id)
        menu = await send_bot_message(peer, "menu", None)

        answer = await adminbot_callback_query_handler(
            peer, menu, f"adm:act:stars:set:{target.id}:42:u0".encode(),
        )
        assert answer is not None
        assert "42" in (answer.message or "")

        balance = await UserStarsBalance.get_or_create_for(target.id)
        assert balance.amount == 42

        answer = await adminbot_callback_query_handler(
            peer, menu, f"adm:act:stars:set:{target.id}:0:u0".encode(),
        )
        assert answer is not None
        await balance.refresh_from_db()
        assert balance.amount == 0


@pytest.mark.asyncio
async def test_admin_kick_sessions() -> None:
    async with TestClient(phone_number="900000021") as client:
        target = await User.get(phone_number=client.phone_number)
        assert await UserAuthorization.filter(user_id=target.id).count() >= 1
        target_id = target.id

    async with TestClient(phone_number="123456789") as client:
        admin_user = await User.get(phone_number=client.phone_number)
        admin_user.admin = True
        await admin_user.save(update_fields=["admin"])

        bot = await client.get_users("admin")
        peer = await Peer.get(owner_id=admin_user.id, user_id=bot.id)
        menu = await send_bot_message(peer, "menu", None)

        answer = await adminbot_callback_query_handler(
            peer, menu, f"adm:act:kick:{target_id}:u0".encode(),
        )
        assert answer is not None
        assert "kicked" in (answer.message or "").lower()

    assert await UserAuthorization.filter(user_id=target_id).count() == 0


@pytest.mark.asyncio
async def test_builtin_bots_are_verified() -> None:
    for username in ("admin", "spambot", "verifybot", "botfather"):
        bot = await User.filter(username__username=username, system=True).first()
        if bot is not None:
            assert bot.verified is True