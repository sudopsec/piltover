from __future__ import annotations

from time import time

from tortoise.transactions import in_transaction

from piltover.db.enums import StarsPaymentPurpose, StarsTransactionPeerType
from piltover.db.models import UserStarsBalance, StarsTransaction, StarsPaymentForm, User, Peer
from piltover.db.enums import PeerType
from piltover.exceptions import ErrorRpc
from piltover.tl import (
    StarsAmount, InputInvoiceStars, InputStorePaymentStarsTopup, InputStorePaymentStarsGift,
    Invoice, LabeledPrice, PaymentFormStars,
)
from piltover.tl.types.payments import StarsStatus
from piltover.utils.users_chats_channels import UsersChatsChannels

SYSTEM_STARS_BOT_ID = 777000
STARS_CURRENCY = "XTR"


async def ensure_wallet_user_id(user_id: int, peer: object) -> int:
    peer_type, peer_owner_id = Peer.type_and_id_from_input_raise(user_id, peer)
    if peer_type is not PeerType.SELF:
        raise ErrorRpc(error_code=400, error_message="PEER_ID_INVALID")
    return peer_owner_id


async def build_stars_status(
        wallet_user_id: int,
        *,
        history: list[StarsTransaction] | None = None,
        next_offset: str | None = None,
        subscriptions: list | None = None,
        subscriptions_next_offset: str | None = None,
) -> StarsStatus:
    balance = await UserStarsBalance.get_or_create_for(wallet_user_id)
    users: list = []
    chats: list = []

    history_tl = None
    if history is not None:
        ucc = UsersChatsChannels()
        history_tl = [tx.to_tl(ucc) for tx in history]
        users, chats_list, channels = await ucc.resolve()
        chats = [*chats_list, *channels]

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

    limit = min(max(limit, 1), 50)
    query = StarsTransaction.filter(user_id=wallet_user_id)
    if inbound and not outbound:
        query = query.filter(inbound=True)
    elif outbound and not inbound:
        query = query.filter(inbound=False)

    if offset:
        offset_tx = await StarsTransaction.get_or_none(transaction_id=offset, user_id=wallet_user_id)
        if offset_tx is not None:
            if ascending:
                query = query.filter(date__gt=offset_tx.date)
            else:
                query = query.filter(date__lt=offset_tx.date)

    order = "date" if ascending else "-date"
    rows = await query.order_by(order).limit(limit + 1)
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_offset = rows[-1].transaction_id if has_more and rows else None
    return rows, next_offset


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


async def create_payment_form(user_id: int, invoice: object) -> PaymentFormStars:
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

    return PaymentFormStars(
        form_id=form.id,
        bot_id=SYSTEM_STARS_BOT_ID,
        title=title,
        description=description,
        invoice=_invoice_for_stars(stars),
        users=[],
    )


async def grant_stars(
        user_id: int,
        stars: int,
        *,
        title: str = "Stars Bonus",
        description: str | None = None,
) -> UserStarsBalance:
    balance, _ = await _credit_stars(
        user_id,
        stars,
        inbound=True,
        peer_type=StarsTransactionPeerType.API,
        title=title,
        description=description or f"Received {stars} Telegram Stars",
    )
    return balance


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
        )

    return balance, tx


async def complete_payment_form(user_id: int, form_id: int, invoice: object) -> tuple[UserStarsBalance, list[int]]:
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
    if purpose is StarsPaymentPurpose.TOPUP:
        balance, _ = await _credit_stars(
            user_id,
            stars,
            inbound=True,
            peer_type=StarsTransactionPeerType.FRAGMENT,
            title="Stars Top-Up",
            description=f"Purchased {stars} Telegram Stars",
        )
        updated_user_ids.append(user_id)
        return balance, updated_user_ids

    assert gift_user_id is not None
    balance, _ = await _credit_stars(
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
    return balance, updated_user_ids