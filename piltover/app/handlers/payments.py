from piltover.app.utils import stars_manager as stars
import piltover.app.utils.updates_manager as upd
from piltover.db.models import UserStarsBalance
from piltover.enums import ReqHandlerFlags
from piltover.tl import StarsTopupOption, TLObjectVector, StatsGraphError, StarsAmount, StarsRevenueStatus, UpdateStarsBalance
from piltover.tl.functions.payments import (
    GetStarsStatus, GetStarsStatusTon, GetStarsSubscriptions, GetStarsTransactions,
    GetStarsTransactions_181, GetStarsTransactions_182, GetStarsTransactionsByID,
    GetStarsTransactionsByIDTon, GetStarsTopupOptions, GetPaymentForm, SendPaymentForm,
    SendStarsForm, ValidateRequestedInfo, GetStarsRevenueStats, GetPaymentReceipt,
)
from piltover.tl.types.payments import StarsStatus, PaymentResult, ValidatedRequestedInfo, StarsRevenueStats, PaymentReceiptStars
from piltover.worker import MessageHandler

handler = MessageHandler("payments")

_TOPUP_OPTIONS = TLObjectVector([
    StarsTopupOption(stars=50, currency="USD", amount=99),
    StarsTopupOption(stars=100, currency="USD", amount=179),
    StarsTopupOption(stars=250, currency="USD", amount=399),
    StarsTopupOption(stars=500, currency="USD", amount=749),
    StarsTopupOption(stars=1000, currency="USD", amount=1399),
])


async def _get_stars_status(peer: object, user_id: int) -> StarsStatus:
    wallet_user_id = await stars.ensure_wallet_user_id(user_id, peer)
    history, _ = await stars.fetch_transactions(
        wallet_user_id,
        inbound=True,
        outbound=True,
        ascending=False,
        offset="",
        limit=5,
    )
    return await stars.build_stars_status(wallet_user_id, history=history)


@handler.on_request(GetStarsStatus, ReqHandlerFlags.BOT_NOT_ALLOWED)
async def get_stars_status(request: GetStarsStatus, user_id: int) -> StarsStatus:
    return await _get_stars_status(request.peer, user_id)


@handler.on_request(GetStarsStatusTon, ReqHandlerFlags.BOT_NOT_ALLOWED)
async def get_stars_status_ton(request: GetStarsStatusTon, user_id: int) -> StarsStatus:
    return await _get_stars_status(request.peer, user_id)


async def _stars_transactions_status(
        wallet_user_id: int,
        *,
        inbound: bool,
        outbound: bool,
        ascending: bool,
        offset: str,
        limit: int,
        subscription_id: str | None = None,
) -> StarsStatus:
    history, next_offset = await stars.fetch_transactions(
        wallet_user_id,
        inbound=inbound,
        outbound=outbound,
        ascending=ascending,
        offset=offset,
        limit=limit,
        subscription_id=subscription_id,
    )
    return await stars.build_stars_status(wallet_user_id, history=history, next_offset=next_offset)


@handler.on_request(GetStarsTransactions, ReqHandlerFlags.BOT_NOT_ALLOWED)
async def get_stars_transactions(request: GetStarsTransactions, user_id: int) -> StarsStatus:
    wallet_user_id = await stars.ensure_wallet_user_id(user_id, request.peer)
    return await _stars_transactions_status(
        wallet_user_id,
        inbound=request.inbound,
        outbound=request.outbound,
        ascending=request.ascending,
        offset=request.offset,
        limit=request.limit,
        subscription_id=request.subscription_id,
    )


@handler.on_request(GetStarsTransactions_181, ReqHandlerFlags.BOT_NOT_ALLOWED)
async def get_stars_transactions_181(request: GetStarsTransactions_181, user_id: int) -> StarsStatus:
    wallet_user_id = await stars.ensure_wallet_user_id(user_id, request.peer)
    return await _stars_transactions_status(
        wallet_user_id,
        inbound=request.inbound,
        outbound=request.outbound,
        ascending=False,
        offset=request.offset,
        limit=50,
    )


@handler.on_request(GetStarsTransactions_182, ReqHandlerFlags.BOT_NOT_ALLOWED)
async def get_stars_transactions_182(request: GetStarsTransactions_182, user_id: int) -> StarsStatus:
    wallet_user_id = await stars.ensure_wallet_user_id(user_id, request.peer)
    return await _stars_transactions_status(
        wallet_user_id,
        inbound=request.inbound,
        outbound=request.outbound,
        ascending=request.ascending,
        offset=request.offset,
        limit=request.limit,
    )


async def _get_stars_transactions_by_id(peer: object, user_id: int, transaction_ids: list[str]) -> StarsStatus:
    wallet_user_id = await stars.ensure_wallet_user_id(user_id, peer)
    history = await stars.fetch_transactions_by_id(wallet_user_id, transaction_ids)
    return await stars.build_stars_status(wallet_user_id, history=history)


@handler.on_request(GetStarsTransactionsByID, ReqHandlerFlags.BOT_NOT_ALLOWED)
async def get_stars_transactions_by_id(request: GetStarsTransactionsByID, user_id: int) -> StarsStatus:
    return await _get_stars_transactions_by_id(
        request.peer, user_id, [item.id for item in request.id],
    )


@handler.on_request(GetStarsTransactionsByIDTon, ReqHandlerFlags.BOT_NOT_ALLOWED)
async def get_stars_transactions_by_id_ton(request: GetStarsTransactionsByIDTon, user_id: int) -> StarsStatus:
    return await _get_stars_transactions_by_id(
        request.peer, user_id, [item.id for item in request.id],
    )


@handler.on_request(GetStarsSubscriptions, ReqHandlerFlags.BOT_NOT_ALLOWED)
async def get_stars_subscriptions(request: GetStarsSubscriptions, user_id: int) -> StarsStatus:
    wallet_user_id = await stars.ensure_wallet_user_id(user_id, request.peer)
    return await stars.build_stars_status(
        wallet_user_id,
        subscriptions=[],
        subscriptions_next_offset=None,
    )


@handler.on_request(GetStarsTopupOptions, ReqHandlerFlags.BOT_NOT_ALLOWED)
async def get_stars_topup_options() -> list[StarsTopupOption]:
    return _TOPUP_OPTIONS


@handler.on_request(GetStarsRevenueStats, ReqHandlerFlags.BOT_NOT_ALLOWED)
async def get_stars_revenue_stats() -> StarsRevenueStats:
    zero = StarsAmount(amount=0, nanos=0)
    return StarsRevenueStats(
        revenue_graph=StatsGraphError(error="no stats"),
        status=StarsRevenueStatus(
            current_balance=zero,
            available_balance=zero,
            overall_revenue=zero,
        ),
        usd_rate=1.0,
    )


@handler.on_request(GetPaymentForm, ReqHandlerFlags.BOT_NOT_ALLOWED)
async def get_payment_form(request: GetPaymentForm, user_id: int) -> object:
    return await stars.create_payment_form(user_id, request.invoice)


@handler.on_request(ValidateRequestedInfo, ReqHandlerFlags.BOT_NOT_ALLOWED)
async def validate_requested_info() -> ValidatedRequestedInfo:
    return ValidatedRequestedInfo()


async def _finish_stars_payment(user_id: int, form_id: int, invoice: object) -> PaymentResult:
    _balance, updated_user_ids, payment_updates = await stars.complete_payment_form(user_id, form_id, invoice)

    payer_balance = await UserStarsBalance.get_or_create_for(user_id)
    result_updates = payment_updates or upd.UpdatesWithDefaults(updates=[])
    result_updates.updates.append(UpdateStarsBalance(balance=payer_balance.to_stars_amount()))

    await upd.update_stars_balance(user_id, payer_balance.to_stars_amount())

    for updated_user_id in updated_user_ids:
        if updated_user_id == user_id:
            continue
        recipient_balance = await UserStarsBalance.get_or_create_for(updated_user_id)
        await upd.update_stars_balance(updated_user_id, recipient_balance.to_stars_amount())

    return PaymentResult(updates=result_updates)


@handler.on_request(GetPaymentReceipt, ReqHandlerFlags.BOT_NOT_ALLOWED)
async def get_payment_receipt(request: GetPaymentReceipt, user_id: int) -> PaymentReceiptStars:
    return await stars.get_payment_receipt(user_id, request.peer, request.msg_id)


@handler.on_request(SendPaymentForm, ReqHandlerFlags.BOT_NOT_ALLOWED)
async def send_payment_form(request: SendPaymentForm, user_id: int) -> PaymentResult:
    return await _finish_stars_payment(user_id, request.form_id, request.invoice)


@handler.on_request(SendStarsForm, ReqHandlerFlags.BOT_NOT_ALLOWED)
async def send_stars_form(request: SendStarsForm, user_id: int) -> PaymentResult:
    return await _finish_stars_payment(user_id, request.form_id, request.invoice)