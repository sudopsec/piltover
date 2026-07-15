from __future__ import annotations

from datetime import datetime, UTC
from time import time

from tortoise.expressions import F, Q
from tortoise.transactions import in_transaction

from piltover.context import request_ctx
from piltover.db.enums import StarsPaymentPurpose, StarsTransactionPeerType, MediaType, MessageType
from piltover.db.models import (
    UserStarsBalance, StarsTransaction, StarsPaymentForm, User, Peer, MessageRef,
    BotPrecheckoutQuery, Username,
)
from piltover.db.enums import PeerType
from piltover.db.models.stars_transaction import StarsTransactionRenderContext
from piltover.exceptions import ErrorRpc
from piltover.tl import (
    StarsAmount, InputInvoiceStars, InputStorePaymentStarsTopup, InputStorePaymentStarsGift,
    InputInvoiceMessage, Invoice, LabeledPrice, MessageMediaInvoice, PaymentCharge,
    MessageActionPaymentSentMe, MessageActionPaymentSent,
    KeyboardButtonBuy, KeyboardButtonRow, ReplyInlineMarkup, UpdateNewMessage, Updates,
)
from piltover.tl.types.payments import PaymentForm, PaymentFormStars, StarsStatus, PaymentReceiptStars
import piltover.app.utils.updates_manager as upd
from piltover.utils.users_chats_channels import UsersChatsChannels

SYSTEM_STARS_BOT_ID = 777000
STARS_CURRENCY = "XTR"
STARS_PAYMENT_URL = "https://example.org"
PRECHECKOUT_TIMEOUT_SECONDS = 10
STARS_USD_SELL_RATE_X1000 = 1410
STARS_USD_WITHDRAW_RATE_X1000 = 1300

_stars_bot_user_id: int | None = None


async def get_stars_bot_user_id() -> int:
    global _stars_bot_user_id
    if _stars_bot_user_id is None:
        username = await Username.get_or_none(username="stars")
        user_id = username.user_id if username is not None else None
        if user_id is None:
            raise RuntimeError("Stars bot is not configured")
        _stars_bot_user_id = user_id
    return _stars_bot_user_id

def bot_net_stars(gross_stars: int) -> int:
    if gross_stars <= 0:
        return 0
    return gross_stars * STARS_USD_WITHDRAW_RATE_X1000 // STARS_USD_SELL_RATE_X1000


async def ensure_wallet_user_id(user_id: int, peer: object) -> int:
    peer_type, peer_owner_id = Peer.type_and_id_from_input_raise(user_id, peer)
    if peer_type is not PeerType.SELF:
        raise ErrorRpc(error_code=400, error_message="PEER_ID_INVALID")
    return peer_owner_id


async def _stars_render_ctx() -> StarsTransactionRenderContext:
    try:
        stars_bot_user_id = await get_stars_bot_user_id()
    except RuntimeError:
        stars_bot_user_id = None
    return StarsTransactionRenderContext(stars_bot_user_id=stars_bot_user_id)


async def build_stars_status(
        wallet_user_id: int,
        *,
        history: list[StarsTransaction] | None = None,
        next_offset: str | None = None,
        subscriptions: list | None = None,
        subscriptions_next_offset: str | None = None,
) -> StarsStatus:
    balance = await UserStarsBalance.get_or_create_for(wallet_user_id)

    history_tl = None
    users: list = []
    chats: list = []
    if history is not None:
        ucc = UsersChatsChannels()
        render_ctx = await _stars_render_ctx()
        history_tl = [tx.to_tl(ucc, render_ctx) for tx in history]
        users, chats, _ = await ucc.resolve()

    return StarsStatus(
        balance=balance.to_stars_amount(),
        history=history_tl,
        next_offset=next_offset,
        subscriptions=subscriptions,
        subscriptions_next_offset=subscriptions_next_offset,
        chats=chats,
        users=users,
    )


async def fetch_transactions(
        wallet_user_id: int,
        *,
        inbound: bool,
        outbound: bool,
        ascending: bool,
        offset: str,
        limit: int,
        subscription_id: str | None = None,
) -> tuple[list[StarsTransaction], str | None]:
    if subscription_id is not None:
        return [], None

    if limit <= 0:
        limit = 50
    else:
        limit = min(limit, 50)
    query = StarsTransaction.filter(user_id=wallet_user_id)
    if inbound and not outbound:
        query = query.filter(inbound=True)
    elif outbound and not inbound:
        query = query.filter(inbound=False)

    if offset:
        offset_tx = await StarsTransaction.get_or_none(transaction_id=offset, user_id=wallet_user_id)
        if offset_tx is not None:
            if ascending:
                query = query.filter(
                    Q(date__gt=offset_tx.date)
                    | (Q(date=offset_tx.date) & Q(transaction_id__gt=offset_tx.transaction_id))
                )
            else:
                query = query.filter(
                    Q(date__lt=offset_tx.date)
                    | (Q(date=offset_tx.date) & Q(transaction_id__lt=offset_tx.transaction_id))
                )

    order = ("date", "transaction_id") if ascending else ("-date", "-transaction_id")
    rows = await query.order_by(*order).limit(limit + 1)
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_offset = rows[-1].transaction_id if has_more and rows else None
    return rows, next_offset


async def fetch_transactions_by_id(wallet_user_id: int, transaction_ids: list[str]) -> list[StarsTransaction]:
    if not transaction_ids:
        return []
    rows = await StarsTransaction.filter(
        user_id=wallet_user_id,
        transaction_id__in=transaction_ids,
    ).all()
    order = {tx_id: idx for idx, tx_id in enumerate(transaction_ids)}
    rows.sort(key=lambda row: order.get(row.transaction_id, len(transaction_ids)))
    return rows


def _invoice_for_stars(stars: int) -> Invoice:
    return Invoice(
        currency=STARS_CURRENCY,
        prices=[LabeledPrice(label="Stars", amount=stars)],
    )


async def _parse_stars_invoice(
        user_id: int, invoice: object,
) -> tuple[StarsPaymentPurpose, int, str, int, int | None]:
    if not isinstance(invoice, InputInvoiceStars):
        raise ErrorRpc(error_code=400, error_message="INVOICE_INVALID")

    purpose = invoice.purpose
    if isinstance(purpose, InputStorePaymentStarsTopup):
        if purpose.stars <= 0:
            raise ErrorRpc(error_code=400, error_message="STARS_AMOUNT_INVALID")
        return StarsPaymentPurpose.TOPUP, purpose.stars, purpose.currency, purpose.amount, None

    if isinstance(purpose, InputStorePaymentStarsGift):
        if purpose.stars <= 0:
            raise ErrorRpc(error_code=400, error_message="STARS_AMOUNT_INVALID")
        gift_peer = await Peer.query_from_input_user_or_raise(user_id, purpose.user_id).only("user_id")
        return StarsPaymentPurpose.GIFT, purpose.stars, purpose.currency, purpose.amount, gift_peer.user_id

    raise ErrorRpc(error_code=400, error_message="INVOICE_INVALID")


def _invoice_for_fiat(currency: str, amount: int) -> Invoice:
    return Invoice(
        currency=currency,
        prices=[LabeledPrice(label="Stars", amount=amount)],
    )


async def create_payment_form(user_id: int, invoice: object) -> PaymentForm | PaymentFormStars:
    if isinstance(invoice, InputInvoiceMessage):
        return await _create_bot_payment_form(user_id, invoice)
    purpose, stars, currency, amount, gift_user_id = await _parse_stars_invoice(user_id, invoice)

    if purpose is StarsPaymentPurpose.GIFT:
        if gift_user_id is None or not await User.filter(id=gift_user_id).exists():
            raise ErrorRpc(error_code=400, error_message="USER_ID_INVALID")
        title = "Gift Telegram Stars"
        description = f"Gift {stars} Telegram Stars"
    else:
        title = "Top Up Telegram Stars"
        description = f"Buy {stars} Telegram Stars"

    form = await StarsPaymentForm.create_form(
        user_id=user_id,
        purpose=purpose,
        stars=stars,
        currency=currency,
        amount=amount,
        gift_user_id=gift_user_id,
    )

    if currency == STARS_CURRENCY:
        return PaymentFormStars(
            form_id=form.id,
            bot_id=SYSTEM_STARS_BOT_ID,
            title=title,
            description=description,
            invoice=_invoice_for_stars(stars),
            users=[],
        )

    return PaymentForm(
        form_id=form.id,
        bot_id=SYSTEM_STARS_BOT_ID,
        title=title,
        description=description,
        invoice=_invoice_for_fiat(currency, amount),
        provider_id=SYSTEM_STARS_BOT_ID,
        url=STARS_PAYMENT_URL,
        users=[],
    )


async def grant_stars(
        user_id: int,
        stars: int,
        *,
        title: str = "Stars Bonus",
        description: str | None = None,
) -> UserStarsBalance:
    stars_bot_id = await get_stars_bot_user_id()
    balance, _ = await _credit_stars(
        user_id,
        stars,
        inbound=True,
        peer_type=StarsTransactionPeerType.PEER,
        title=title,
        description=description or f"Received {stars} Telegram Stars",
        peer_user_id=stars_bot_id,
    )
    return balance


async def set_stars_balance(
        user_id: int,
        amount: int,
        *,
        title: str = "Admin adjustment",
        description: str | None = None,
) -> UserStarsBalance:
    if amount < 0:
        raise ErrorRpc(error_code=400, error_message="STARS_AMOUNT_INVALID")

    stars_bot_id = await get_stars_bot_user_id()
    async with in_transaction():
        balance = await UserStarsBalance.filter(user_id=user_id).select_for_update().first()
        if balance is None:
            balance = await UserStarsBalance.create(user_id=user_id, amount=0, nanos=0)

        current = balance.amount
        if current == amount:
            return balance

        if amount > current:
            balance.amount = amount
            await balance.save(update_fields=["amount"])
            await StarsTransaction.create(
                transaction_id=StarsTransaction.gen_id(),
                user_id=user_id,
                stars_amount=amount - current,
                inbound=True,
                date=int(time()),
                peer_type=StarsTransactionPeerType.PEER,
                peer_user_id=stars_bot_id,
                title=title,
                description=description or f"Balance set to {amount} stars",
            )
        else:
            balance.amount = amount
            await balance.save(update_fields=["amount"])
            await StarsTransaction.create(
                transaction_id=StarsTransaction.gen_id(),
                user_id=user_id,
                stars_amount=current - amount,
                inbound=False,
                date=int(time()),
                peer_type=StarsTransactionPeerType.PEER,
                peer_user_id=stars_bot_id,
                title=title,
                description=description or f"Balance set to {amount} stars",
            )

    return balance


async def spend_stars(
        user_id: int,
        stars: int,
        *,
        peer_type: StarsTransactionPeerType,
        title: str,
        description: str | None = None,
        gift: bool = False,
        peer_user_id: int | None = None,
) -> UserStarsBalance:
    balance, _ = await _debit_stars(
        user_id,
        stars,
        peer_type=peer_type,
        title=title,
        description=description,
        gift=gift,
        peer_user_id=peer_user_id,
    )
    return balance


def _invoice_total_amount(invoice: Invoice) -> int:
    return sum(price.amount for price in invoice.prices)


def make_invoice_buy_markup(currency: str, total_amount: int) -> ReplyInlineMarkup:
    if currency == STARS_CURRENCY:
        text = f"Pay ⭐ {total_amount}"
    else:
        text = f"Pay {total_amount} {currency}"
    return ReplyInlineMarkup(rows=[
        KeyboardButtonRow(buttons=[KeyboardButtonBuy(text=text)]),
    ])


def ensure_invoice_reply_markup(
        currency: str, total_amount: int, reply_markup: ReplyInlineMarkup | None,
) -> ReplyInlineMarkup:
    if reply_markup is not None and reply_markup.rows:
        first_button = reply_markup.rows[0].buttons[0] if reply_markup.rows[0].buttons else None
        if isinstance(first_button, KeyboardButtonBuy):
            return reply_markup
    return make_invoice_buy_markup(currency, total_amount)


def _pack_invoice_static(invoice_tl: MessageMediaInvoice, payload: bytes) -> bytes:
    invoice_bytes = invoice_tl.write()
    return len(invoice_bytes).to_bytes(4, "little", signed=False) + invoice_bytes + payload


def _unpack_invoice_static(data: bytes) -> tuple[MessageMediaInvoice, bytes]:
    from io import BytesIO

    if len(data) < 4:
        raise ErrorRpc(error_code=400, error_message="INVOICE_INVALID")

    invoice_len = int.from_bytes(data[:4], "little", signed=False)
    invoice_end = 4 + invoice_len
    if invoice_end > len(data):
        raise ErrorRpc(error_code=400, error_message="INVOICE_INVALID")

    invoice_bytes = data[4:invoice_end]
    payload = data[invoice_end:]
    return MessageMediaInvoice.read(BytesIO(invoice_bytes)), payload


def _invoice_is_paid(invoice_media: MessageMediaInvoice) -> bool:
    return invoice_media.receipt_msg_id is not None


async def _load_bot_invoice_message(user_id: int, invoice: InputInvoiceMessage) -> tuple[MessageRef, MessageMediaInvoice, bytes, int]:
    peer = await Peer.from_input_peer_raise(user_id, invoice.peer)
    message = await MessageRef.get_(
        invoice.msg_id, peer, prefetch=("content__media", "content__author"),
    )
    if message is None or not message.content.author.bot:
        raise ErrorRpc(error_code=400, error_message="MESSAGE_ID_INVALID")

    media = message.content.media
    if media is None or media.type is not MediaType.INVOICE or media.static_data is None:
        raise ErrorRpc(error_code=400, error_message="INVOICE_INVALID")

    invoice_media, payload = _unpack_invoice_static(media.static_data)
    if invoice_media.currency != STARS_CURRENCY:
        raise ErrorRpc(error_code=400, error_message="CURRENCY_TOTAL_AMOUNT_INVALID")
    if _invoice_is_paid(invoice_media):
        raise ErrorRpc(error_code=400, error_message="MEDIA_ALREADY_PAID")

    return message, invoice_media, payload, message.content.author_id


async def _raise_if_invoice_already_paid(user_id: int, message_id: int) -> None:
    if await StarsTransaction.filter(user_id=user_id, msg_id=message_id, inbound=False).exists():
        raise ErrorRpc(error_code=400, error_message="FORM_SUBMIT_DUPLICATE")


async def _create_bot_payment_form(user_id: int, invoice: InputInvoiceMessage) -> PaymentFormStars:
    message, invoice_media, payload, bot_user_id = await _load_bot_invoice_message(user_id, invoice)
    stars = invoice_media.total_amount

    form = await StarsPaymentForm.create_form(
        user_id=user_id,
        purpose=StarsPaymentPurpose.BOT_INVOICE,
        stars=stars,
        currency=invoice_media.currency,
        amount=stars,
        bot_user_id=bot_user_id,
        message_id=message.id,
        payload=payload,
    )

    ucc = UsersChatsChannels()
    ucc.add_user(bot_user_id)
    users, _, _ = await ucc.resolve()

    return PaymentFormStars(
        form_id=form.id,
        bot_id=bot_user_id,
        title=invoice_media.title,
        description=invoice_media.description,
        invoice=Invoice(
            currency=invoice_media.currency,
            prices=[LabeledPrice(label=invoice_media.title, amount=stars)],
        ),
        users=users,
    )


async def _await_bot_precheckout(
        bot_user_id: int, payer_user_id: int, payload: bytes, currency: str, total_amount: int,
) -> None:
    bot = await User.get(id=bot_user_id).only("id", "system")
    if bot.system:
        return

    import piltover.app.bot_handlers.bots as builtin_bots
    import piltover.app.utils.updates_manager as upd
    from piltover.utils.snowflake import Snowflake

    query = await BotPrecheckoutQuery.create(
        id=Snowflake.make_id(),
        user_id=payer_user_id,
        bot_id=bot_user_id,
        payload=payload,
        currency=currency,
        total_amount=total_amount,
    )

    try:
        if await builtin_bots.try_process_precheckout_query(query):
            return

        ctx = request_ctx.get()
        pubsub = ctx.worker.pubsub
        topic = f"bot-precheckout-query/{query.id}"
        await pubsub.listen(topic, None)
        await upd.bot_precheckout_query(bot_user_id, query)

        result = await pubsub.listen(topic, PRECHECKOUT_TIMEOUT_SECONDS)
        if result is None:
            raise ErrorRpc(error_code=400, error_message="BOT_PRECHECKOUT_TIMEOUT")
        if result != b"1":
            raise ErrorRpc(error_code=400, error_message=result.decode("utf-8", errors="replace") or "PAYMENT_FAILED")
    finally:
        await BotPrecheckoutQuery.filter(id=query.id).delete()


def _extract_new_message_id(updates: Updates) -> int:
    for update in updates.updates:
        if isinstance(update, UpdateNewMessage):
            return update.message.id
    raise RuntimeError("payment service message update is missing")


async def _update_invoice_receipt(
        invoice_message: MessageRef, receipt_msg_id: int, payer: User,
) -> Updates:
    media = invoice_message.content.media
    if media is None or media.static_data is None:
        raise ErrorRpc(error_code=400, error_message="INVOICE_INVALID")

    invoice_tl, invoice_payload = _unpack_invoice_static(media.static_data)
    updated_invoice = MessageMediaInvoice(
        title=invoice_tl.title,
        description=invoice_tl.description,
        currency=invoice_tl.currency,
        total_amount=invoice_tl.total_amount,
        start_param=invoice_tl.start_param,
        shipping_address_requested=invoice_tl.shipping_address_requested,
        test=invoice_tl.test,
        photo=invoice_tl.photo,
        receipt_msg_id=receipt_msg_id,
        extended_media=getattr(invoice_tl, "extended_media", None),
    )
    media.static_data = _pack_invoice_static(updated_invoice, invoice_payload)
    content = invoice_message.content
    content.edit_date = datetime.now(UTC)
    content.edit_hide = True
    content.version += 1
    async with in_transaction():
        await media.save(update_fields=["static_data"])
        await content.save(update_fields=["edit_date", "edit_hide", "version"])
        await MessageRef.filter(content_id=content.id).update(version=F("version") + 1)

    return await upd.edit_message(payer.id, {invoice_message.peer: invoice_message})


async def _send_bot_payment_messages(
        payer: User, bot_user_id: int, payer_peer: Peer, invoice_message: MessageRef,
        stars: int, payload: bytes, charge_id: str, title: str,
) -> Updates:
    from piltover.app.bot_handlers import bots
    from piltover.app.handlers.messages.sending import send_message_internal

    charge = PaymentCharge(id=charge_id, provider_charge_id=charge_id)
    bot_user = await User.get(id=bot_user_id).only("id", "bot", "system")

    payment_updates = await send_message_internal(
        payer, payer_peer, None, invoice_message.id, False, author=bot_user_id,
        opposite=False,
        type=MessageType.SERVICE_PAYMENT,
        extra_info=MessageActionPaymentSent(
            currency=STARS_CURRENCY,
            total_amount=stars,
            invoice_slug=None,
        ).write(),
    )

    receipt_msg_id = _extract_new_message_id(payment_updates)
    invoice_edit_updates = await _update_invoice_receipt(invoice_message, receipt_msg_id, payer)
    result = upd.merge_updates(payment_updates, invoice_edit_updates)

    if not bot_user.system:
        bot_peer, _ = await Peer.get_or_create(owner=bot_user, user_id=payer.id, type=PeerType.USER)
        await bot_peer.fetch_related("user", "user__username")
        await send_message_internal(
            bot_user, bot_peer, None, None, False, author=payer.id,
            opposite=False,
            type=MessageType.SERVICE_PAYMENT,
            extra_info=MessageActionPaymentSentMe(
                currency=STARS_CURRENCY,
                total_amount=stars,
                payload=payload or b"",
                charge=charge,
            ).write(),
        )

    success_updates = await bots.try_notify_payment_success(
        bot_user_id, payer, payer_peer, stars, title,
    )
    if success_updates is not None:
        result = upd.merge_updates(result, success_updates)

    return result


async def _credit_stars(
        wallet_user_id: int,
        stars: int,
        *,
        inbound: bool,
        peer_type: StarsTransactionPeerType,
        title: str,
        description: str | None = None,
        gift: bool = False,
        peer_user_id: int | None = None,
        msg_id: int | None = None,
        bot_payload: bytes | None = None,
) -> tuple[UserStarsBalance, StarsTransaction]:
    async with in_transaction():
        balance = await UserStarsBalance.filter(user_id=wallet_user_id).select_for_update().first()
        if balance is None:
            balance = await UserStarsBalance.create(user_id=wallet_user_id, amount=0, nanos=0)

        balance.amount += stars
        await balance.save(update_fields=["amount"])

        tx = await StarsTransaction.create(
            transaction_id=StarsTransaction.gen_id(),
            user_id=wallet_user_id,
            stars_amount=stars,
            inbound=inbound,
            date=int(time()),
            peer_type=peer_type,
            peer_user_id=peer_user_id,
            title=title,
            description=description,
            gift=gift,
            msg_id=msg_id,
            bot_payload=bot_payload,
        )

    return balance, tx


async def _transfer_stars(
        from_user_id: int,
        to_user_id: int,
        stars: int,
        *,
        from_peer_type: StarsTransactionPeerType,
        to_peer_type: StarsTransactionPeerType,
        title: str,
        from_description: str | None = None,
        to_description: str | None = None,
        from_peer_user_id: int | None = None,
        to_peer_user_id: int | None = None,
        msg_id: int | None = None,
        bot_payload: bytes | None = None,
        recipient_stars: int | None = None,
) -> tuple[UserStarsBalance, UserStarsBalance, StarsTransaction, StarsTransaction]:
    if stars <= 0:
        raise ErrorRpc(error_code=400, error_message="STARS_AMOUNT_INVALID")

    credited_stars = stars if recipient_stars is None else recipient_stars
    if credited_stars <= 0 or credited_stars > stars:
        raise ErrorRpc(error_code=400, error_message="STARS_AMOUNT_INVALID")

    async with in_transaction():
        payer_balance = await UserStarsBalance.filter(user_id=from_user_id).select_for_update().first()
        if payer_balance is None or payer_balance.amount < stars:
            raise ErrorRpc(error_code=400, error_message="BALANCE_TOO_LOW")

        recipient_balance = await UserStarsBalance.filter(user_id=to_user_id).select_for_update().first()
        if recipient_balance is None:
            recipient_balance = await UserStarsBalance.create(user_id=to_user_id, amount=0, nanos=0)

        payer_balance.amount -= stars
        recipient_balance.amount += credited_stars
        await payer_balance.save(update_fields=["amount"])
        await recipient_balance.save(update_fields=["amount"])

        now = int(time())
        outbound_tx = await StarsTransaction.create(
            transaction_id=StarsTransaction.gen_id(),
            user_id=from_user_id,
            stars_amount=stars,
            inbound=False,
            date=now,
            peer_type=from_peer_type,
            peer_user_id=from_peer_user_id,
            title=title,
            description=from_description,
            msg_id=msg_id,
            bot_payload=bot_payload,
        )
        inbound_tx = await StarsTransaction.create(
            transaction_id=StarsTransaction.gen_id(),
            user_id=to_user_id,
            stars_amount=credited_stars,
            inbound=True,
            date=now,
            peer_type=to_peer_type,
            peer_user_id=to_peer_user_id,
            title=title,
            description=to_description,
            msg_id=msg_id,
            bot_payload=bot_payload,
        )

    return payer_balance, recipient_balance, outbound_tx, inbound_tx


async def _debit_stars(
        wallet_user_id: int,
        stars: int,
        *,
        peer_type: StarsTransactionPeerType,
        title: str,
        description: str | None = None,
        gift: bool = False,
        peer_user_id: int | None = None,
        msg_id: int | None = None,
        bot_payload: bytes | None = None,
) -> tuple[UserStarsBalance, StarsTransaction]:
    if stars <= 0:
        raise ErrorRpc(error_code=400, error_message="STARS_AMOUNT_INVALID")

    async with in_transaction():
        balance = await UserStarsBalance.filter(user_id=wallet_user_id).select_for_update().first()
        if balance is None or balance.amount < stars:
            raise ErrorRpc(error_code=400, error_message="BALANCE_TOO_LOW")

        balance.amount -= stars
        await balance.save(update_fields=["amount"])

        tx = await StarsTransaction.create(
            transaction_id=StarsTransaction.gen_id(),
            user_id=wallet_user_id,
            stars_amount=stars,
            inbound=False,
            date=int(time()),
            peer_type=peer_type,
            peer_user_id=peer_user_id,
            title=title,
            description=description,
            gift=gift,
            msg_id=msg_id,
            bot_payload=bot_payload,
        )

    return balance, tx


async def complete_payment_form(
        user_id: int, form_id: int, invoice: object,
) -> tuple[UserStarsBalance, list[int], Updates | None]:
    if isinstance(invoice, InputInvoiceMessage):
        return await _complete_bot_payment_form(user_id, form_id, invoice)

    purpose, stars, _currency, _amount, gift_user_id = await _parse_stars_invoice(user_id, invoice)

    form = await StarsPaymentForm.get_or_none(id=form_id, user_id=user_id)
    if form is None:
        raise ErrorRpc(error_code=400, error_message="FORM_EXPIRED")
    if form.is_expired():
        await form.delete()
        raise ErrorRpc(error_code=400, error_message="FORM_EXPIRED")
    if form.purpose != purpose or form.stars != stars:
        raise ErrorRpc(error_code=400, error_message="INVOICE_INVALID")
    if purpose is StarsPaymentPurpose.GIFT and form.gift_user_id != gift_user_id:
        raise ErrorRpc(error_code=400, error_message="INVOICE_INVALID")

    await form.delete()

    updated_user_ids: list[int] = []
    paid_with_stars = form.currency == STARS_CURRENCY

    if purpose is StarsPaymentPurpose.TOPUP:
        if paid_with_stars:
            raise ErrorRpc(error_code=400, error_message="INVOICE_INVALID")
        balance, _ = await _credit_stars(
            user_id,
            stars,
            inbound=True,
            peer_type=StarsTransactionPeerType.FRAGMENT,
            title="Stars Top-Up",
            description=f"Purchased {stars} Telegram Stars",
        )
        updated_user_ids.append(user_id)
        return balance, updated_user_ids, None

    assert gift_user_id is not None
    if paid_with_stars:
        payer_balance, _ = await _debit_stars(
            user_id,
            stars,
            peer_type=StarsTransactionPeerType.PEER,
            title="Stars Gift",
            description=f"Gifted {stars} Telegram Stars",
            gift=True,
            peer_user_id=gift_user_id,
        )
        updated_user_ids.append(user_id)
    else:
        payer_balance = await UserStarsBalance.get_or_create_for(user_id)

    recipient_balance, _ = await _credit_stars(
        gift_user_id,
        stars,
        inbound=True,
        peer_type=StarsTransactionPeerType.PEER,
        title="Stars Gift",
        description=f"Received {stars} Telegram Stars",
        gift=True,
        peer_user_id=user_id,
    )
    updated_user_ids.append(gift_user_id)
    return payer_balance if paid_with_stars else recipient_balance, updated_user_ids, None


async def get_payment_receipt(user_id: int, peer: object, msg_id: int) -> PaymentReceiptStars:
    peer_obj = await Peer.from_input_peer_raise(user_id, peer)
    receipt = await MessageRef.get_or_none(
        id=msg_id, peer_id=peer_obj.id,
    ).select_related("content__media", "content__author", "reply_to__content__media", "peer")
    if receipt is None or receipt.content.type is not MessageType.SERVICE_PAYMENT:
        raise ErrorRpc(error_code=400, error_message="MESSAGE_ID_INVALID")
    if receipt.reply_to_id is None:
        raise ErrorRpc(error_code=400, error_message="MESSAGE_ID_INVALID")

    invoice_message = receipt.reply_to
    if invoice_message is None \
            or invoice_message.content.media is None \
            or invoice_message.content.media.type is not MediaType.INVOICE \
            or invoice_message.content.media.static_data is None:
        raise ErrorRpc(error_code=400, error_message="MESSAGE_ID_INVALID")

    tx = await StarsTransaction.get_or_none(
        user_id=user_id, msg_id=invoice_message.id, inbound=False,
    )
    if tx is None:
        raise ErrorRpc(error_code=400, error_message="MESSAGE_ID_INVALID")

    invoice_media, _ = _unpack_invoice_static(invoice_message.content.media.static_data)
    ucc = UsersChatsChannels()
    ucc.add_user(invoice_message.content.author_id)
    users, _, _ = await ucc.resolve()

    return PaymentReceiptStars(
        date=tx.date,
        bot_id=invoice_message.content.author_id,
        title=invoice_media.title,
        description=invoice_media.description,
        photo=invoice_media.photo,
        invoice=Invoice(
            currency=invoice_media.currency,
            prices=[LabeledPrice(label=invoice_media.title, amount=invoice_media.total_amount)],
        ),
        currency=invoice_media.currency,
        total_amount=invoice_media.total_amount,
        transaction_id=tx.transaction_id,
        users=users,
    )


async def _complete_bot_payment_form(
        user_id: int, form_id: int, invoice: InputInvoiceMessage,
) -> tuple[UserStarsBalance, list[int], Updates]:
    form = await StarsPaymentForm.get_or_none(id=form_id, user_id=user_id)
    if form is None:
        await _raise_if_invoice_already_paid(user_id, invoice.msg_id)
        raise ErrorRpc(error_code=400, error_message="FORM_EXPIRED")

    message, invoice_media, payload, bot_user_id = await _load_bot_invoice_message(user_id, invoice)
    stars = invoice_media.total_amount
    if form.is_expired():
        await form.delete()
        raise ErrorRpc(error_code=400, error_message="FORM_EXPIRED")
    if form.purpose is not StarsPaymentPurpose.BOT_INVOICE:
        raise ErrorRpc(error_code=400, error_message="INVOICE_INVALID")
    if form.stars != stars or form.bot_user_id != bot_user_id or form.message_id != message.id:
        raise ErrorRpc(error_code=400, error_message="INVOICE_INVALID")
    if form.payload is not None and form.payload != payload:
        raise ErrorRpc(error_code=400, error_message="INVOICE_INVALID")

    await _await_bot_precheckout(bot_user_id, user_id, payload, invoice_media.currency, stars)
    await form.delete()

    payer = await User.get(id=user_id).only("id", "bot", "first_name")
    payer_peer = await Peer.from_input_peer_raise(user_id, invoice.peer)
    await payer_peer.fetch_related("user", "user__username")
    message.peer = payer_peer

    net_stars = bot_net_stars(stars)
    payer_balance, _, outbound_tx, _ = await _transfer_stars(
        user_id,
        bot_user_id,
        stars,
        from_peer_type=StarsTransactionPeerType.PEER,
        to_peer_type=StarsTransactionPeerType.PEER,
        title=invoice_media.title,
        from_description=invoice_media.description,
        to_description=invoice_media.description,
        from_peer_user_id=bot_user_id,
        to_peer_user_id=user_id,
        msg_id=message.id,
        bot_payload=payload or None,
        recipient_stars=net_stars,
    )

    payment_updates = await _send_bot_payment_messages(
        payer, bot_user_id, payer_peer, message, stars, payload, outbound_tx.transaction_id,
        invoice_media.title,
    )

    return payer_balance, [user_id, bot_user_id], payment_updates