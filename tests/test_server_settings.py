import asyncio

import pytest
from pyrogram.raw.types import UpdateNewMessage

from piltover.app.bot_handlers.adminbot.actions_server import toggle_config_action
from piltover.app.bot_handlers.adminbot.utils import send_bot_message
from piltover.app.utils.server_settings import get_server_settings, is_bot_enabled, toggle_server_setting
from piltover.db.enums import PeerType
from piltover.db.models import Peer, State, User
from tests.client import TestClient


@pytest.mark.asyncio
async def test_toggle_verifybot_setting() -> None:
    settings = await get_server_settings()
    settings.verifybot_enabled = True
    await settings.save(update_fields=["verifybot_enabled"])

    updated = await toggle_server_setting("verifybot_enabled")
    assert updated is not None
    assert updated.verifybot_enabled is False

    assert await is_bot_enabled("verifybot") is False
    assert await is_bot_enabled("stars") is True


@pytest.mark.asyncio
async def test_admin_config_toggle_verifybot() -> None:
    settings = await get_server_settings()
    settings.verifybot_enabled = True
    await settings.save(update_fields=["verifybot_enabled"])

    admin = await User.create(phone_number="900000101", first_name="CfgAdmin", admin=True)
    await State.create(user=admin)
    bot = await User.filter(username__username="admin", system=True).first()
    assert bot is not None

    peer, _ = await Peer.get_or_create(
        owner=admin, user_id=bot.id, defaults={"type": PeerType.USER},
    )
    menu = await send_bot_message(peer, "menu", None)

    answer = await toggle_config_action(peer, menu, "verifybot")
    assert answer.alert is False

    settings = await get_server_settings()
    assert settings.verifybot_enabled is False


@pytest.mark.asyncio
async def test_verifybot_disabled_does_not_reply() -> None:
    settings = await get_server_settings()
    settings.verifybot_enabled = False
    await settings.save(update_fields=["verifybot_enabled"])

    async with TestClient(phone_number="123456789") as client:
        bot = await client.get_users("verifybot")
        await client.send_message(bot.id, "/start")
        await client.expect_update(UpdateNewMessage)

        with pytest.raises(asyncio.TimeoutError):
            await client.expect_update(UpdateNewMessage, timeout_=0.3)


@pytest.mark.asyncio
async def test_stars_bot_disabled_does_not_reply() -> None:
    settings = await get_server_settings()
    settings.stars_bot_enabled = False
    await settings.save(update_fields=["stars_bot_enabled"])

    async with TestClient(phone_number="123456789") as client:
        bot = await client.get_users("stars")
        await client.send_message(bot.id, "/start")
        await client.expect_update(UpdateNewMessage)

        with pytest.raises(asyncio.TimeoutError):
            await client.expect_update(UpdateNewMessage, timeout_=0.3)