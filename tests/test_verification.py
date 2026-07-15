import pytest
from pyrogram.raw.types import UpdateNewMessage

from piltover.app.bot_handlers.verifybot.callback_handler import verifybot_callback_query_handler
from piltover.app.bot_handlers.verifybot.utils import send_bot_message
from piltover.app.utils import verification
from piltover.db.models import Bot, Channel, Peer, User
from tests.client import TestClient



@pytest.mark.asyncio
async def test_set_user_support_updates_tl() -> None:
    from io import BytesIO
    from time import time

    from piltover.cache import Cache
    from piltover.db.enums import PrivacyRuleKeyType
    from piltover.db.models import Presence
    from piltover.tl import TLObject, UserStatusEmpty, UserStatusOnline
    from piltover.tl.serialization_context import ContextValues, SerializationContext
    from piltover.tl.to_format.user import UserToFormat

    user = await User.create(phone_number="9998887776", first_name="Support", last_name="User")
    await verification.set_user_support(user, True)
    await user.refresh_from_db()

    assert user.support is True
    await Cache.obj.delete(user._cache_key())
    user_tl = await user.to_tl()
    assert isinstance(user_tl, UserToFormat)
    assert user_tl.support is True
    assert user_tl.last_seen is None

    regular_tl = UserToFormat(
        id=2,
        first_name="Regular",
        last_name="User",
        lang_code="en",
        last_seen=int(time()),
        support=False,
    )
    ctx_values = ContextValues()
    ctx_values.privacyrules[2] = {
        PrivacyRuleKeyType.PHONE_NUMBER: True,
        PrivacyRuleKeyType.PROFILE_PHOTO: True,
        PrivacyRuleKeyType.STATUS_TIMESTAMP: True,
    }
    ctx = SerializationContext(auth_id=1, user_id=3, layer=200, values=ctx_values)
    regular_user = TLObject.read(BytesIO(regular_tl.write(ctx)))
    assert isinstance(regular_user.status, UserStatusOnline)

    support_tl = UserToFormat(
        id=user.id,
        first_name=user.first_name,
        last_name=user.last_name,
        lang_code=user.lang_code,
        last_seen=int(time()),
        support=True,
    )
    ctx_values.privacyrules[user.id] = ctx_values.privacyrules[2]
    support_user = TLObject.read(BytesIO(support_tl.write(ctx)))
    assert isinstance(support_user.status, UserStatusEmpty)

    with pytest.raises(RuntimeError, match="support user"):
        await Presence.update_to_now(user)


@pytest.mark.asyncio
async def test_set_user_verified_updates_tl() -> None:
    async with TestClient(phone_number="123456789") as client:
        user = await User.get(phone_number=client.phone_number)
        await verification.set_user_verified(user, True)
        await user.refresh_from_db()

        assert user.verified is True
        user_tl = await user.to_tl()
        from piltover.tl.to_format.user import UserToFormat
        assert isinstance(user_tl, UserToFormat)
        assert user_tl.verified is True


@pytest.mark.asyncio
async def test_set_channel_verified_updates_tl() -> None:
    async with TestClient(phone_number="123456789") as client:
        user = await User.get(phone_number=client.phone_number)
        db_channel = await Channel.create(name="verified test channel", creator=user, channel=True)
        await verification.set_channel_verified(db_channel, True)
        await db_channel.refresh_from_db()

        assert db_channel.verified is True
        channel_tl = await db_channel.to_tl()
        from piltover.tl.to_format.channel import ChannelToFormat
        assert isinstance(channel_tl, ChannelToFormat)
        assert channel_tl.verified is True


@pytest.mark.asyncio
async def test_verifybot_start() -> None:
    async with TestClient(phone_number="123456789") as client:
        bot = await client.get_users("verifybot")
        await client.send_message(bot.id, "/start")

        user_message = await client.expect_update(UpdateNewMessage)
        bot_message = await client.expect_update(UpdateNewMessage)

        if user_message.message.from_id.user_id != client.me.id:
            user_message, bot_message = bot_message, user_message

        assert "Verification Bot" in bot_message.message.message


@pytest.mark.asyncio
async def test_verifybot_verify_self() -> None:
    async with TestClient(phone_number="123456789") as client:
        user = await User.get(phone_number=client.phone_number)
        user.verified = False
        await user.save(update_fields=["verified"])

        bot = await client.get_users("verifybot")
        peer = await Peer.get(owner_id=user.id, user_id=bot.id)
        menu = await send_bot_message(peer, "menu", None)

        answer = await verifybot_callback_query_handler(peer, menu, b"act:v:u:0")
        assert answer is not None
        assert "granted" in (answer.message or "").lower()

        await user.refresh_from_db()
        assert user.verified is True

        answer = await verifybot_callback_query_handler(peer, menu, b"act:uv:u:0")
        assert answer is not None
        assert "removed" in (answer.message or "").lower()

        await user.refresh_from_db()
        assert user.verified is False


@pytest.mark.asyncio
async def test_verifybot_cannot_verify_foreign_bot() -> None:
    from piltover.db.enums import PeerType

    async with TestClient(phone_number="123456789") as client:
        owner = await User.get(phone_number=client.phone_number)
        foreign_bot = await User.create(phone_number=None, first_name="Foreign Bot", bot=True)
        await Bot.create(owner=owner, bot=foreign_bot)

        other = await User.create(phone_number="9876543210", first_name="Other")
        verify_bot_user = await client.get_users("verifybot")
        peer, _ = await Peer.get_or_create(
            owner=other, user_id=verify_bot_user.id, defaults={"type": PeerType.USER},
        )
        menu = await send_bot_message(peer, "menu", None)

        answer = await verifybot_callback_query_handler(
            peer, menu, f"act:v:u:{foreign_bot.id}".encode(),
        )
        assert answer is not None
        assert answer.alert is True