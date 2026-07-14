import pytest
from pyrogram.raw.types import UpdateNewMessage, MessageMediaInvoice, ReplyInlineMarkup, KeyboardButtonBuy
from pyrogram.types import Message as PyroMessage

from piltover.app.utils import stars_manager as stars
from piltover.context import RequestContext, request_ctx
from piltover.db.enums import StarsTransactionPeerType
from piltover.db.models import User, UserStarsBalance, StarsTransaction, UserAuthorization
from piltover.tl import InputInvoiceMessage, InputPeerUser, UpdateNewMessage as TLUpdateNewMessage
from piltover.tl.types.internal import MessageToFormatContent
from tests.client import TestClient


@pytest.mark.asyncio
async def test_test_bot_ping_command() -> None:
    async with TestClient(phone_number="123456789") as client:
        test_bot = await client.get_users("test_bot")

        await client.send_message(test_bot.id, "/ping")

        user_message = await client.expect_update(UpdateNewMessage)
        bot_message = await client.expect_update(UpdateNewMessage)

        if user_message.message.from_id.user_id != client.me.id:
            user_message, bot_message = bot_message, user_message

        assert user_message.message.from_id.user_id == client.me.id
        assert user_message.message.message == "/ping"

        assert bot_message.message.from_id.user_id == test_bot.id
        assert bot_message.message.message == "Pong"


@pytest.mark.asyncio
async def test_premiumbot_start_and_status() -> None:
    async with TestClient(phone_number="123456789") as client:
        premium_bot = await client.get_users("premiumbot")

        await client.send_message(premium_bot.id, "/start")

        user_message = await client.expect_update(UpdateNewMessage)
        bot_message = await client.expect_update(UpdateNewMessage)

        if user_message.message.from_id.user_id != client.me.id:
            user_message, bot_message = bot_message, user_message

        assert "Telegram Premium" in bot_message.message.message

        await client.send_message(premium_bot.id, "/start status")

        user_message = await client.expect_update(UpdateNewMessage)
        bot_message = await client.expect_update(UpdateNewMessage)

        if user_message.message.from_id.user_id != client.me.id:
            user_message, bot_message = bot_message, user_message

        assert "do not have" in bot_message.message.message.lower()


@pytest.mark.asyncio
async def test_stars_pay_bot_start_shows_invoice_buttons() -> None:
    async with TestClient(phone_number="123456789") as client:
        stars_pay_bot = await client.get_users("stars_pay")

        await client.send_message(stars_pay_bot.id, "/start")

        user_message = await client.expect_update(UpdateNewMessage)
        bot_message = await client.expect_update(UpdateNewMessage)

        if user_message.message.from_id.user_id != client.me.id:
            user_message, bot_message = bot_message, user_message

        assert "Stars Pay Test Bot" in bot_message.message.message
        parsed = await PyroMessage._parse(client, bot_message.message, {}, {})
        assert parsed.reply_markup is not None
        assert len(parsed.reply_markup.inline_keyboard) == 2


@pytest.mark.asyncio
async def test_stars_pay_bot_invoice_payment() -> None:
    async with TestClient(phone_number="123456789") as client:
        payer = await User.get(phone_number=client.phone_number)
        stars_pay_bot = await client.get_users("stars_pay")
        await stars.grant_stars(payer.id, 50)

        await client.send_message(stars_pay_bot.id, "/invoice 10")

        invoice_update = None
        for _ in range(5):
            update = await client.expect_update(UpdateNewMessage)
            if isinstance(update.message.media, MessageMediaInvoice):
                invoice_update = update
                break

        assert invoice_update is not None
        assert invoice_update.message.media.currency == "XTR"
        assert invoice_update.message.media.total_amount == 10
        assert isinstance(invoice_update.message.reply_markup, ReplyInlineMarkup)
        assert isinstance(invoice_update.message.reply_markup.rows[0].buttons[0], KeyboardButtonBuy)

        from piltover.app.app import app

        auth = await UserAuthorization.get(user_id=payer.id)
        access_hash = User.make_access_hash(payer.id, auth.id, stars_pay_bot.id)
        invoice = InputInvoiceMessage(
            peer=InputPeerUser(user_id=stars_pay_bot.id, access_hash=access_hash),
            msg_id=invoice_update.message.id,
        )

        assert app._worker is not None
        ctx_token = request_ctx.set(RequestContext(
            0, None, 0, 0, invoice, 201, auth.id, payer.id, app._worker, app._worker._storage,
        ))
        try:
            form = await stars.create_payment_form(payer.id, invoice)
            payer_balance, updated_user_ids, payment_updates = await stars.complete_payment_form(
                payer.id, form.form_id, invoice,
            )

            assert payment_updates is not None
            success_messages = [
                u.message.content.message
                for u in payment_updates.updates
                if isinstance(u, TLUpdateNewMessage)
                and isinstance(u.message.content, MessageToFormatContent)
                and u.message.content.message
            ]
            assert any("Payment successful" in text for text in success_messages)

            assert payer_balance.amount == 40
            assert set(updated_user_ids) == {payer.id, stars_pay_bot.id}

            bot_balance = await UserStarsBalance.get_or_create_for(stars_pay_bot.id)
            assert bot_balance.amount == stars.bot_net_stars(10)

            outbound = await StarsTransaction.get(user_id=payer.id, inbound=False)
            assert outbound.stars_amount == 10
            assert outbound.peer_type is StarsTransactionPeerType.PEER
            assert outbound.peer_user_id == stars_pay_bot.id
        finally:
            request_ctx.reset(ctx_token)
