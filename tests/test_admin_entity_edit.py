import pytest

from piltover.app.bot_handlers.adminbot.callback_handler import adminbot_callback_query_handler
from piltover.app.bot_handlers.adminbot.text_handler import AdminBotTextHandler
from piltover.app.bot_handlers.adminbot.utils import send_bot_message
from piltover.app.utils.admin_entity_edit import apply_user_field_value
from piltover.db.enums import AdminBotState
from piltover.db.models import AdminBotUserState, Peer, User, Username

from tests.client import TestClient


@pytest.mark.asyncio
async def test_apply_user_field_value_name() -> None:
    user = await User.create(phone_number="900100100", first_name="Old")
    error = await apply_user_field_value(user, "name", "NewName")
    assert error is None
    await user.refresh_from_db()
    assert user.first_name == "NewName"


@pytest.mark.asyncio
async def test_apply_user_field_value_clear_lastname() -> None:
    user = await User.create(phone_number="900100101", first_name="Test", last_name="Last")
    error = await apply_user_field_value(user, "lastname", "", clear=True)
    assert error is None
    await user.refresh_from_db()
    assert user.last_name is None


@pytest.mark.asyncio
async def test_apply_user_field_value_username() -> None:
    user = await User.create(phone_number="900100102", first_name="Test")
    error = await apply_user_field_value(user, "username", "testuser123")
    assert error is None
    username = await Username.get_or_none(user_id=user.id)
    assert username is not None
    assert username.username == "testuser123"


@pytest.mark.asyncio
async def test_admin_user_settings_flow() -> None:
    target = await User.create(phone_number="900100103", first_name="Before")

    async with TestClient(phone_number="123456789") as client:
        admin_user = await User.get(phone_number=client.phone_number)
        admin_user.admin = True
        await admin_user.save(update_fields=["admin"])

        bot = await client.get_users("admin")
        peer = await Peer.get(owner_id=admin_user.id, user_id=bot.id)
        menu = await send_bot_message(peer, "menu", None)

        answer = await adminbot_callback_query_handler(
            peer, menu, f"adm:user:set:{target.id}:u0".encode(),
        )
        assert answer is not None

        await AdminBotUserState.set_state(
            admin_user.id,
            AdminBotState.WAIT_ENTITY_EDIT,
            f"user:name:{target.id}:u0:{menu.id}".encode(),
        )
        text_msg = await send_bot_message(peer, "After", None)
        await AdminBotTextHandler._entity_edit(peer, text_msg, await AdminBotUserState.get(user_id=admin_user.id))

    await target.refresh_from_db()
    assert target.first_name == "After"