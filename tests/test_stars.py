import pytest

from piltover.app.utils import stars_manager as stars
from piltover.app.utils.stars_manager import STARS_CURRENCY, _pack_invoice_static, make_invoice_buy_markup
from piltover.db.enums import MediaType, PeerType, StarsTransactionPeerType
from piltover.db.models import Bot, MessageMedia, MessageRef, Peer, StarsTransaction, State, User, Username, \
    UserStarsBalance
from piltover.tl import InputInvoiceMessage, InputPeerUser, MessageMediaInvoice, UpdateNewMessage, UpdateEditMessage, MessageMediaInvoice as TLMessageMediaInvoice
from piltover.utils.users_chats_channels import UsersChatsChannels


async def _create_shop_bot_invoice(payer: User, *, stars: int = 10, payload: bytes = b"invoice-payload") -> tuple[User, MessageRef]:
    bot = await User.create(phone_number=None, first_name="Shop", bot=True, system=False)
    await Username.create(user=bot, username="shop_test_bot")
    await State.create(user=bot)
    await Bot.create(owner=payer, bot=bot)
    await Peer.create(owner=bot, type=PeerType.SELF, user=bot)

    payer_peer, _ = await Peer.get_or_create(owner=payer, user_id=bot.id, type=PeerType.USER)
    invoice_tl = MessageMediaInvoice(
        title="Premium access",
        description="One month",
        currency=STARS_CURRENCY,
        total_amount=stars,
        start_param="",
    )
    media = await MessageMedia.create(
        type=MediaType.INVOICE,
        static_data=_pack_invoice_static(invoice_tl, payload),
    )
    buy_markup = make_invoice_buy_markup(STARS_CURRENCY, stars)
    messages = await MessageRef.create_for_peer(
        payer_peer, bot, opposite=True,
        media=media, reply_markup=buy_markup.write(),
    )
    return bot, messages[payer_peer]


@pytest.mark.asyncio
async def test_grant_stars_records_inbound_transaction(client_with_auth) -> None:
    client = await client_with_auth()
    user = await User.get(phone_number=client.phone_number)

    await stars.grant_stars(user.id, 50, title="Test Bonus")

    tx = await StarsTransaction.filter(user_id=user.id).first()
    assert tx is not None
    assert tx.inbound is True
    assert tx.stars_amount == 50
    assert tx.peer_type is StarsTransactionPeerType.PEER
    assert tx.peer_user_id is not None

    ucc = UsersChatsChannels()
    from piltover.app.utils.stars_manager import get_stars_bot_user_id
    from piltover.db.models.stars_transaction import StarsTransactionRenderContext

    render_ctx = StarsTransactionRenderContext(stars_bot_user_id=await get_stars_bot_user_id())
    tl_tx = tx.to_tl(ucc, render_ctx)
    assert tl_tx.stars.amount == 50
    users, _, _ = await ucc.resolve()
    assert users


@pytest.mark.asyncio
async def test_spend_stars_records_outbound_transaction(client_with_auth) -> None:
    client = await client_with_auth()
    user = await User.get(phone_number=client.phone_number)

    await stars.grant_stars(user.id, 100)
    await stars.spend_stars(
        user.id,
        25,
        peer_type=StarsTransactionPeerType.PEER,
        title="Test Spend",
        peer_user_id=user.id,
    )

    outbound = await StarsTransaction.filter(user_id=user.id, inbound=False).first()
    assert outbound is not None
    assert outbound.stars_amount == 25

    ucc = UsersChatsChannels()
    tl_tx = outbound.to_tl(ucc)
    assert tl_tx.stars.amount == -25


@pytest.mark.asyncio
async def test_fetch_transactions_filters_direction(client_with_auth) -> None:
    client = await client_with_auth()
    user = await User.get(phone_number=client.phone_number)

    await stars.grant_stars(user.id, 10)
    await stars.spend_stars(
        user.id,
        3,
        peer_type=StarsTransactionPeerType.API,
        title="Spend",
    )

    inbound_rows, _ = await stars.fetch_transactions(
        user.id, inbound=True, outbound=False, ascending=False, offset="", limit=50,
    )
    outbound_rows, _ = await stars.fetch_transactions(
        user.id, inbound=False, outbound=True, ascending=False, offset="", limit=50,
    )

    assert len(inbound_rows) == 1
    assert inbound_rows[0].inbound is True
    assert len(outbound_rows) == 1
    assert outbound_rows[0].inbound is False


@pytest.mark.asyncio
async def test_fetch_transactions_by_id_preserves_order(client_with_auth) -> None:
    client = await client_with_auth()
    user = await User.get(phone_number=client.phone_number)

    await stars.grant_stars(user.id, 5)
    await stars.grant_stars(user.id, 15)

    all_rows = await StarsTransaction.filter(user_id=user.id).order_by("-date")
    assert len(all_rows) == 2

    rows = await stars.fetch_transactions_by_id(
        user.id,
        [all_rows[1].transaction_id, all_rows[0].transaction_id],
    )
    assert [row.transaction_id for row in rows] == [
        all_rows[1].transaction_id,
        all_rows[0].transaction_id,
    ]


@pytest.mark.asyncio
async def test_bot_stars_payment_transfers_balance_and_records_transactions(
        client_with_auth,
) -> None:
    async def _approve_precheckout(_query) -> bool:
        return True

    from piltover.app.app import app
    from piltover.app.bot_handlers import bots
    from piltover.context import RequestContext, request_ctx
    from piltover.db.models import UserAuthorization

    bots.PRECHECKOUT_QUERY_HANDLERS["shop_test_bot"] = _approve_precheckout
    try:
        client = await client_with_auth()
        payer = await User.get(phone_number=client.phone_number)
        await stars.grant_stars(payer.id, 100)

        bot, invoice_message = await _create_shop_bot_invoice(payer, stars=10)
        auth = await UserAuthorization.get(user_id=payer.id)
        access_hash = User.make_access_hash(payer.id, auth.id, bot.id)
        invoice = InputInvoiceMessage(
            peer=InputPeerUser(user_id=bot.id, access_hash=access_hash),
            msg_id=invoice_message.id,
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

            assert payer_balance.amount == 90
            assert payment_updates is not None
            new_message_updates = [u for u in payment_updates.updates if isinstance(u, UpdateNewMessage)]
            edit_message_updates = [u for u in payment_updates.updates if isinstance(u, UpdateEditMessage)]
            assert len(new_message_updates) == 1
            assert len(edit_message_updates) == 1
            edited_invoice = edit_message_updates[0].message.content.media
            assert isinstance(edited_invoice, TLMessageMediaInvoice)
            assert edited_invoice.receipt_msg_id == new_message_updates[0].message.id

            assert set(updated_user_ids) == {payer.id, bot.id}

            bot_balance = await UserStarsBalance.get_or_create_for(bot.id)
            assert bot_balance.amount == stars.bot_net_stars(10)

            payer_outbound = await StarsTransaction.get(user_id=payer.id, inbound=False)
            receipt = await stars.get_payment_receipt(
                payer.id,
                InputPeerUser(user_id=bot.id, access_hash=access_hash),
                edited_invoice.receipt_msg_id,
            )
            assert receipt.transaction_id == payer_outbound.transaction_id
            bot_inbound = await StarsTransaction.get(user_id=bot.id, inbound=True)

            assert payer_outbound.stars_amount == 10
            assert bot_inbound.stars_amount == stars.bot_net_stars(10)
            assert payer_outbound.peer_type is StarsTransactionPeerType.PEER
            assert payer_outbound.peer_user_id == bot.id
            assert payer_outbound.msg_id == invoice_message.id
            assert payer_outbound.bot_payload == b"invoice-payload"

            assert bot_inbound.peer_type is StarsTransactionPeerType.PEER
            assert bot_inbound.peer_user_id == payer.id
            assert bot_inbound.msg_id == invoice_message.id
            assert bot_inbound.bot_payload == b"invoice-payload"

            ucc = UsersChatsChannels()
            payer_tl = payer_outbound.to_tl(ucc)
            assert payer_tl.stars.amount == -10
            users, _, _ = await ucc.resolve()
            assert users
        finally:
            request_ctx.reset(ctx_token)
    finally:
        bots.PRECHECKOUT_QUERY_HANDLERS.pop("shop_test_bot", None)


@pytest.mark.asyncio
async def test_get_stars_status_includes_recent_history(client_with_auth) -> None:
    client = await client_with_auth()
    user = await User.get(phone_number=client.phone_number)

    await stars.grant_stars(user.id, 7)

    status = await stars.build_stars_status(
        user.id,
        history=(await stars.fetch_transactions(
            user.id, inbound=True, outbound=True, ascending=False, offset="", limit=5,
        ))[0],
    )
    assert status.history is not None
    assert len(status.history) == 1
    assert status.history[0].stars.amount == 7


@pytest.mark.asyncio
async def test_bot_net_stars_applies_telegram_commission() -> None:
    assert stars.bot_net_stars(10) == 9
    assert stars.bot_net_stars(100) == 92


@pytest.mark.asyncio
async def test_dialogs_serialize_after_bot_payment(client_with_auth) -> None:
    async def _approve_precheckout(_query) -> bool:
        return True

    from io import BytesIO

    from piltover.app.app import app
    from piltover.app.bot_handlers import bots
    from piltover.app.handlers.messages.dialogs import format_dialogs
    from piltover.context import RequestContext, request_ctx
    from piltover.db.models import Dialog, UserAuthorization
    from piltover.tl import TLObject
    from piltover.tl.serialization_context import SerializationContext
    from piltover.tl.types.messages import Dialogs, DialogsSlice

    bots.PRECHECKOUT_QUERY_HANDLERS["shop_test_bot"] = _approve_precheckout
    try:
        client = await client_with_auth()
        payer = await User.get(phone_number=client.phone_number)
        await stars.grant_stars(payer.id, 100)

        bot, invoice_message = await _create_shop_bot_invoice(payer, stars=10)
        auth = await UserAuthorization.get(user_id=payer.id)
        access_hash = User.make_access_hash(payer.id, auth.id, bot.id)
        invoice = InputInvoiceMessage(
            peer=InputPeerUser(user_id=bot.id, access_hash=access_hash),
            msg_id=invoice_message.id,
        )

        assert app._worker is not None
        ctx_token = request_ctx.set(RequestContext(
            0, None, 0, 0, invoice, 201, auth.id, payer.id, app._worker, app._worker._storage,
        ))
        try:
            form = await stars.create_payment_form(payer.id, invoice)
            await stars.complete_payment_form(payer.id, form.form_id, invoice)

            dialog = await Dialog.get(owner_id=payer.id, peer_id=invoice_message.peer_id).prefetch_related("peer")
            dialogs_tl = await format_dialogs(Dialog, Dialogs, DialogsSlice, payer.id, [dialog])

            ctx = SerializationContext(auth_id=auth.id, user_id=payer.id, layer=201)
            dialogs_tl.write(ctx)
            for message in dialogs_tl.messages:
                data = message.write(ctx)
                TLObject.read(BytesIO(data))

            status = await stars.build_stars_status(
                payer.id,
                history=(await stars.fetch_transactions(
                    payer.id, inbound=True, outbound=True, ascending=False, offset="", limit=5,
                ))[0],
            )
            status_data = status.write(ctx)
            TLObject.read(BytesIO(status_data))
        finally:
            request_ctx.reset(ctx_token)
    finally:
        bots.PRECHECKOUT_QUERY_HANDLERS.pop("shop_test_bot", None)


@pytest.mark.asyncio
async def test_stars_transaction_to_tl_serializes_without_optional_pair_error(client_with_auth) -> None:
    client = await client_with_auth()
    user = await User.get(phone_number=client.phone_number)
    await stars.grant_stars(user.id, 3)

    tx = await StarsTransaction.filter(user_id=user.id).first()
    assert tx is not None

    ucc = UsersChatsChannels()
    tl_tx = tx.to_tl(ucc)
    tl_tx.write()


@pytest.mark.asyncio
async def test_bot_payment_form_is_single_use(client_with_auth) -> None:
    async def _approve_precheckout(_query) -> bool:
        return True

    from piltover.app.app import app
    from piltover.app.bot_handlers import bots
    from piltover.context import RequestContext, request_ctx
    from piltover.db.models import UserAuthorization
    from piltover.exceptions import ErrorRpc

    bots.PRECHECKOUT_QUERY_HANDLERS["shop_test_bot"] = _approve_precheckout
    try:
        client = await client_with_auth()
        payer = await User.get(phone_number=client.phone_number)
        await stars.grant_stars(payer.id, 100)

        bot, invoice_message = await _create_shop_bot_invoice(payer, stars=5)
        auth = await UserAuthorization.get(user_id=payer.id)
        access_hash = User.make_access_hash(payer.id, auth.id, bot.id)
        invoice = InputInvoiceMessage(
            peer=InputPeerUser(user_id=bot.id, access_hash=access_hash),
            msg_id=invoice_message.id,
        )

        assert app._worker is not None
        ctx_token = request_ctx.set(RequestContext(
            0, None, 0, 0, invoice, 201, auth.id, payer.id, app._worker, app._worker._storage,
        ))
        try:
            form = await stars.create_payment_form(payer.id, invoice)
            await stars.complete_payment_form(payer.id, form.form_id, invoice)

            with pytest.raises(ErrorRpc) as duplicate_exc:
                await stars.complete_payment_form(payer.id, form.form_id, invoice)
            assert duplicate_exc.value.error_message == "FORM_SUBMIT_DUPLICATE"

            with pytest.raises(ErrorRpc) as paid_exc:
                await stars.create_payment_form(payer.id, invoice)
            assert paid_exc.value.error_message == "MEDIA_ALREADY_PAID"
        finally:
            request_ctx.reset(ctx_token)
    finally:
        bots.PRECHECKOUT_QUERY_HANDLERS.pop("shop_test_bot", None)


@pytest.mark.asyncio
async def test_stars_transaction_uses_client_compatible_constructor(client_with_auth) -> None:
    from io import BytesIO

    from piltover.tl import TLObject
    from piltover.tl.serialization_context import SerializationContext

    client = await client_with_auth()
    user = await User.get(phone_number=client.phone_number)
    await stars.grant_stars(user.id, 4)

    tx = await StarsTransaction.filter(user_id=user.id).first()
    assert tx is not None

    ucc = UsersChatsChannels()
    tl_tx = tx.to_tl(ucc)
    ctx = SerializationContext(auth_id=0, user_id=user.id, layer=201)
    data = tl_tx.write(ctx)
    assert data.startswith(b"\xb0\x9e\x65\x13")
    parsed = TLObject.read(BytesIO(data))
    assert parsed.stars.amount == 4


@pytest.mark.asyncio
async def test_stars_status_serializes_for_layer_201(client_with_auth) -> None:
    from io import BytesIO

    from piltover.tl import TLObject
    from piltover.tl.serialization_context import SerializationContext

    client = await client_with_auth()
    user = await User.get(phone_number=client.phone_number)

    await stars.grant_stars(user.id, 11)

    status = await stars.build_stars_status(
        user.id,
        history=(await stars.fetch_transactions(
            user.id, inbound=True, outbound=True, ascending=False, offset="", limit=50,
        ))[0],
    )
    assert status.history is not None
    assert len(status.history) == 1
    assert status.users == []
    assert status.chats == []

    ctx = SerializationContext(auth_id=0, user_id=user.id, layer=201)
    status_data = status.write(ctx)
    parsed = TLObject.read(BytesIO(status_data))
    assert parsed.history is not None
    assert len(parsed.history) == 1
    assert parsed.users == []
    assert parsed.chats == []
    assert parsed.history[0].stars.amount == 11
    assert parsed.history[0].write(ctx).startswith(b"\xb0\x9e\x65\x13")


@pytest.mark.asyncio
async def test_fetch_transactions_pagination_is_stable_for_same_timestamp(client_with_auth) -> None:
    client = await client_with_auth()
    user = await User.get(phone_number=client.phone_number)

    await stars.grant_stars(user.id, 1)
    await stars.grant_stars(user.id, 2)

    rows, next_offset = await stars.fetch_transactions(
        user.id, inbound=True, outbound=False, ascending=False, offset="", limit=1,
    )
    assert len(rows) == 1
    assert next_offset is not None

    page_two, next_offset_2 = await stars.fetch_transactions(
        user.id, inbound=True, outbound=False, ascending=False, offset=next_offset, limit=1,
    )
    assert len(page_two) == 1
    assert page_two[0].transaction_id != rows[0].transaction_id
    assert next_offset_2 is None