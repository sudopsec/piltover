from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from piltover.app.bot_handlers.typetestbot.catalog import builders as b
from piltover.db.enums import MessageType
from piltover.db.models import MessageRef, Peer
from piltover.tl.base import MessageActionInst
from piltover.tl import (
    KeyboardButton,
    KeyboardButtonBuy,
    KeyboardButtonCallback,
    KeyboardButtonGame,
    KeyboardButtonRow,
    MessageActionAttachMenuBotAllowed_151,
    MessageActionBoostApply,
    MessageActionBotAllowed,
    MessageActionChannelCreate,
    MessageActionChannelMigrateFrom,
    MessageActionChatAddUser,
    MessageActionChatCreate,
    MessageActionChatDeletePhoto,
    MessageActionChatDeleteUser,
    MessageActionChatEditPhoto,
    MessageActionChatEditTitle,
    MessageActionChatJoinedByLink,
    MessageActionChatJoinedByRequest,
    MessageActionChatMigrateTo,
    MessageActionContactSignUp,
    MessageActionCustomAction,
    MessageActionEmpty,
    MessageActionGameScore,
    MessageActionGeoProximityReached,
    MessageActionGiftCode,
    MessageActionGiftPremium,
    MessageActionGiftStars,
    MessageActionGiveawayLaunch,
    MessageActionGiveawayResults,
    MessageActionGroupCall,
    MessageActionGroupCallScheduled,
    MessageActionHistoryClear,
    MessageActionInviteToGroupCall,
    MessageActionPaidMessagesPrice,
    MessageActionPaidMessagesRefunded,
    MessageActionPaymentRefunded,
    MessageActionPaymentSent,
    MessageActionPaymentSentMe,
    MessageActionPhoneCall,
    MessageActionPinMessage,
    MessageActionPrizeStars,
    MessageActionRequestedPeer,
    MessageActionRequestedPeerSentMe,
    MessageActionStarGift,
    MessageActionStarGiftUnique,
    MessageActionScreenshotTaken,
    MessageActionSetChatTheme,
    MessageActionSetChatWallPaper,
    MessageActionSetMessagesTTL,
    MessageActionSuggestProfilePhoto,
    MessageActionTopicCreate,
    MessageActionTopicEdit,
    MessageActionWebViewDataSent,
    MessageActionWebViewDataSentMe,
    MessageEntityBankCard,
    MessageEntityBlockquote,
    MessageEntityBold,
    MessageEntityBotCommand,
    MessageEntityCashtag,
    MessageEntityCode,
    MessageEntityCustomEmoji,
    MessageEntityEmail,
    MessageEntityHashtag,
    MessageEntityItalic,
    MessageEntityMention,
    MessageEntityMentionName,
    MessageEntityPhone,
    MessageEntityPre,
    MessageEntitySpoiler,
    MessageEntityStrike,
    MessageEntityTextUrl,
    MessageEntityUnderline,
    MessageEntityUnknown,
    MessageEntityUrl,
    PeerUser,
    PhoneCallDiscardReasonMissed,
    RequestedPeerUser,
    ReplyInlineMarkup,
    ReplyKeyboardMarkup,
    SendMessageCancelAction,
    SendMessageRecordAudioAction,
    SendMessageRecordVideoAction,
    SendMessageTypingAction,
    SendMessageUploadDocumentAction,
    SendMessageUploadPhotoAction,
)
from piltover.app.utils.stars_manager import STARS_CURRENCY, make_invoice_buy_markup

Handler = Callable[[Peer], Awaitable[MessageRef]]
ActionHandler = Callable[[Peer, MessageRef], Awaitable[MessageRef]]


@dataclass(frozen=True)
class Specimen:
    key: bytes
    label: str
    category: str
    impossible: bool


# --- regular ---

async def reg_plain(peer: Peer) -> MessageRef:
    return await b.send_regular(peer, "regular/plain")


async def reg_empty(peer: Peer) -> MessageRef:
    return await b.send_regular(peer, "regular/empty", message="")


async def reg_null(peer: Peer) -> MessageRef:
    return await b.send_regular(peer, "regular/null", message=None)


async def reg_caption(peer: Peer) -> MessageRef:
    return await b.send_regular(peer, "regular/caption+media", media=await b.catalog_dice())


async def reg_media_only(peer: Peer) -> MessageRef:
    return await b.send_regular(peer, "regular/media-only", message="", media=await b.catalog_dice())


async def reg_document(peer: Peer) -> MessageRef:
    return await b.send_regular(peer, "regular/document", media=await b.catalog_document())


async def reg_photo(peer: Peer) -> MessageRef:
    return await b.send_regular(peer, "regular/photo", media=await b.catalog_photo())


async def reg_poll(peer: Peer) -> MessageRef:
    return await b.send_regular(peer, "regular/poll", media=await b.catalog_poll())


async def reg_contact(peer: Peer) -> MessageRef:
    return await b.send_regular(peer, "regular/contact", media=await b.catalog_contact())


async def reg_geo(peer: Peer) -> MessageRef:
    return await b.send_regular(peer, "regular/geo", media=await b.catalog_geo())


async def reg_dice(peer: Peer) -> MessageRef:
    return await b.send_regular(peer, "regular/dice", message="", media=await b.catalog_dice())


async def reg_invoice(peer: Peer) -> MessageRef:
    return await b.send_regular(peer, "regular/invoice", message="", media=await b.catalog_invoice())


async def reg_invoice_cap(peer: Peer) -> MessageRef:
    return await b.send_regular(
        peer, "regular/invoice+caption",
        media=await b.catalog_invoice(2, b"catalog/inv-cap"),
    )


async def reg_scheduled(peer: Peer) -> MessageRef:
    from piltover.db.models import MessageRef as MR
    messages = await MR.create_for_peer(
        peer, peer.user_id, opposite=False,
        message="[regular/scheduled-type]",
        type=MessageType.SCHEDULED,
        scheduled_date=datetime_in_future(),
    )
    return messages[peer]


def datetime_in_future():
    from datetime import datetime, timedelta, UTC
    return datetime.now(UTC) + timedelta(hours=1)


# --- entities ---

def _ent_handler(name: str, text: str, tl_type: type, **extra) -> Handler:
    async def handler(peer: Peer) -> MessageRef:
        prefix = f"[entity/{name}] "
        message = prefix + text
        entity_extra = dict(extra)
        if tl_type is MessageEntityMentionName:
            entity_extra["user_id"] = peer.owner_id
        return await b.send_bot_message(
            peer,
            message,
            entities=[b.entity_at(message, len(prefix), text, tl_type, **entity_extra)],
        )
    return handler


def _init_entity_handlers() -> dict[bytes, Handler]:
    handlers: dict[bytes, Handler] = {}
    specimens: list[tuple[str, str, type, dict]] = [
        ("unknown", "word", MessageEntityUnknown, {}),
        ("mention", "@user", MessageEntityMention, {}),
        ("hashtag", "#tag", MessageEntityHashtag, {}),
        ("bot_command", "/cmd", MessageEntityBotCommand, {}),
        ("url", "https://t.me", MessageEntityUrl, {}),
        ("email", "a@b.co", MessageEntityEmail, {}),
        ("bold", "bold", MessageEntityBold, {}),
        ("italic", "italic", MessageEntityItalic, {}),
        ("code", "code", MessageEntityCode, {}),
        ("pre", "block", MessageEntityPre, {"language": "py"}),
        ("text_url", "link", MessageEntityTextUrl, {"url": "https://t.me"}),
        ("mention_name", "@you", MessageEntityMentionName, {}),
        ("phone", "+12345", MessageEntityPhone, {}),
        ("cashtag", "$USD", MessageEntityCashtag, {}),
        ("underline", "line", MessageEntityUnderline, {}),
        ("strike", "strike", MessageEntityStrike, {}),
        ("bank_card", "4111111111111111", MessageEntityBankCard, {}),
        ("spoiler", "secret", MessageEntitySpoiler, {}),
        ("custom_emoji", "😀", MessageEntityCustomEmoji, {"document_id": 1}),
        ("blockquote", "quoted text", MessageEntityBlockquote, {}),
    ]
    for name, text, tl_type, extra in specimens:
        key = f"cat:ent:{name}".encode()
        handlers[key] = _ent_handler(name, text, tl_type, **extra)
    return handlers


# --- bot actions ---

def _make_action_handler(done: str, action_fn) -> ActionHandler:
    async def handler(peer: Peer, menu_message: MessageRef) -> MessageRef:
        await action_fn(peer)
        from piltover.app.bot_handlers.typetestbot.catalog.pages import page_action_feedback
        return await page_action_feedback(peer, menu_message, done)
    return handler


async def _act_typing(peer: Peer) -> None:
    await b.send_bot_typing(peer, SendMessageTypingAction())


async def _act_record_audio(peer: Peer) -> None:
    await b.send_bot_typing(peer, SendMessageRecordAudioAction())


async def _act_record_video(peer: Peer) -> None:
    await b.send_bot_typing(peer, SendMessageRecordVideoAction())


async def _act_upload_photo(peer: Peer) -> None:
    await b.send_bot_typing(peer, SendMessageUploadPhotoAction(progress=0))


async def _act_upload_document(peer: Peer) -> None:
    await b.send_bot_typing(peer, SendMessageUploadDocumentAction(progress=0))


async def _act_cancel(peer: Peer) -> None:
    await b.send_bot_typing(peer, SendMessageCancelAction())


async def _act_incoming_call(peer: Peer) -> None:
    await b.send_incoming_call(peer)


_ACTION_ITEMS: list[tuple[bytes, str, str, Callable[[Peer], Awaitable[None]]]] = [
    (b"cat:act:typing", "act/typing", "Typing indicator sent", _act_typing),
    (b"cat:act:record_audio", "act/record_audio", "Recording audio…", _act_record_audio),
    (b"cat:act:record_video", "act/record_video", "Recording video…", _act_record_video),
    (b"cat:act:upload_photo", "act/upload_photo", "Uploading photo…", _act_upload_photo),
    (b"cat:act:upload_doc", "act/upload_document", "Uploading document…", _act_upload_document),
    (b"cat:act:cancel", "act/cancel_typing", "Typing cancelled", _act_cancel),
    (b"cat:act:call", "act/incoming_call", "Incoming call sent", _act_incoming_call),
]

ACTION_SPECIMENS: list[tuple[bytes, str]] = [(key, label) for key, label, _, _ in _ACTION_ITEMS]

ACTION_HANDLERS: dict[bytes, ActionHandler] = {
    key: _make_action_handler(done, fn)
    for key, _, done, fn in _ACTION_ITEMS
}


# --- flags ---

async def flg_mentioned(peer: Peer) -> MessageRef:
    msg = await b.send_regular(peer, "flag/mentioned")
    await b.mark_mentioned(peer, msg)
    return msg


async def flg_pinned(peer: Peer) -> MessageRef:
    msg = await b.send_regular(peer, "flag/pinned")
    await b.mark_pinned(msg)
    return msg


async def flg_scheduled(peer: Peer) -> MessageRef:
    msg = await b.send_regular(peer, "flag/from_scheduled")
    await b.mark_from_scheduled(msg)
    return msg


async def flg_edit_hide(peer: Peer) -> MessageRef:
    msg = await b.send_regular(peer, "flag/edit_hide")
    await b.mark_edit_hide(msg)
    return msg


async def flg_noforwards(peer: Peer) -> MessageRef:
    return await b.send_regular(peer, "flag/noforwards", no_forwards=True)


async def flg_via_bot(peer: Peer) -> MessageRef:
    return await b.send_regular(peer, "flag/via_bot", via_bot_id=peer.user_id)


async def flg_ttl(peer: Peer) -> MessageRef:
    return await b.send_regular(peer, "flag/ttl_period", ttl_period_days=1)


async def flg_spoiler_media(peer: Peer) -> MessageRef:
    media = await b.catalog_photo()
    media.spoiler = True
    await media.save(update_fields=["spoiler"])
    return await b.send_regular(peer, "flag/media_spoiler", media=media)


# --- service actions ---

def _make_svc_handler(action, msg_type: MessageType) -> Handler:
    async def handler(peer: Peer) -> MessageRef:
        resolved = action(peer) if callable(action) else action
        return await b.send_service(peer, resolved, msg_type)
    return handler


def _build_service_handlers() -> tuple[list[Specimen], dict[bytes, Handler]]:
    items: list[tuple[str, str, object, MessageType]] = [
        ("empty", "svc/empty", MessageActionEmpty(), MessageType.SERVICE_PIN_MESSAGE),
        ("chat_create", "svc/chat_create", lambda peer: MessageActionChatCreate(
            title="Cat", users=[peer.owner_id, peer.user_id],
        ), MessageType.SERVICE_CHAT_CREATE),
        ("chat_edit_title", "svc/chat_edit_title", MessageActionChatEditTitle(title="New"), MessageType.SERVICE_CHAT_EDIT_TITLE),
        ("chat_edit_photo", "svc/chat_edit_photo", MessageActionChatEditPhoto(photo=stub_photo()), MessageType.SERVICE_CHAT_EDIT_PHOTO),
        ("chat_del_photo", "svc/chat_del_photo", MessageActionChatDeletePhoto(), MessageType.SERVICE_CHAT_EDIT_PHOTO),
        ("chat_add_user", "svc/chat_add_user", lambda peer: MessageActionChatAddUser(users=[peer.owner_id]), MessageType.SERVICE_CHAT_USER_ADD),
        ("chat_del_user", "svc/chat_del_user", lambda peer: MessageActionChatDeleteUser(user_id=peer.owner_id), MessageType.SERVICE_CHAT_USER_DEL),
        ("joined_link", "svc/joined_link", lambda peer: MessageActionChatJoinedByLink(inviter_id=peer.owner_id), MessageType.SERVICE_CHAT_USER_INVITE_JOIN),
        ("joined_req", "svc/joined_req", MessageActionChatJoinedByRequest(), MessageType.SERVICE_CHAT_USER_REQUEST_JOIN),
        ("chan_create", "svc/chan_create", MessageActionChannelCreate(title="Ch"), MessageType.SERVICE_CHANNEL_CREATE),
        ("migrate_to", "svc/migrate_to", MessageActionChatMigrateTo(channel_id=1), MessageType.SERVICE_CHAT_MIGRATE_TO),
        ("migrate_from", "svc/migrate_from", MessageActionChannelMigrateFrom(title="Old", chat_id=1), MessageType.SERVICE_CHAT_MIGRATE_FROM),
        ("pin", "svc/pin", MessageActionPinMessage(), MessageType.SERVICE_PIN_MESSAGE),
        ("history_clear", "svc/history_clear", MessageActionHistoryClear(), MessageType.SERVICE_PIN_MESSAGE),
        ("game_score", "svc/game_score", MessageActionGameScore(game_id=1, score=100), MessageType.SERVICE_PIN_MESSAGE),
        ("pay_sent", "svc/payment_sent", MessageActionPaymentSent(currency=STARS_CURRENCY, total_amount=1), MessageType.SERVICE_PAYMENT),
        ("pay_sent_me", "svc/payment_sent_me", MessageActionPaymentSentMe(
            currency=STARS_CURRENCY, total_amount=1, payload=b"", charge=stub_payment_charge(),
        ), MessageType.SERVICE_PAYMENT),
        ("phone", "svc/phone_call", MessageActionPhoneCall(
            call_id=1, reason=PhoneCallDiscardReasonMissed(),
        ), MessageType.SERVICE_PHONE_CALL),
        ("screenshot", "svc/screenshot", MessageActionScreenshotTaken(), MessageType.SERVICE_PIN_MESSAGE),
        ("custom", "svc/custom", MessageActionCustomAction(message="catalog"), MessageType.SERVICE_PIN_MESSAGE),
        ("bot_allowed", "svc/bot_allowed", MessageActionBotAllowed(domain="typetestbot"), MessageType.SERVICE_PIN_MESSAGE),
        ("attach_menu", "svc/attach_menu", MessageActionAttachMenuBotAllowed_151(), MessageType.SERVICE_PIN_MESSAGE),
        ("contact_signup", "svc/contact_signup", MessageActionContactSignUp(), MessageType.SERVICE_PIN_MESSAGE),
        ("geo_prox", "svc/geo_proximity", lambda peer: MessageActionGeoProximityReached(
            from_id=PeerUser(user_id=peer.owner_id), to_id=PeerUser(user_id=peer.user_id), distance=10,
        ), MessageType.SERVICE_PIN_MESSAGE),
        ("group_call", "svc/group_call", MessageActionGroupCall(call=stub_group_call()), MessageType.SERVICE_GROUP_CALL),
        ("invite_call", "svc/invite_call", lambda peer: MessageActionInviteToGroupCall(
            call=stub_group_call(), users=[peer.owner_id],
        ), MessageType.SERVICE_GROUP_CALL),
        ("set_ttl", "svc/set_ttl", MessageActionSetMessagesTTL(period=86400), MessageType.SERVICE_CHAT_UPDATE_TTL),
        ("call_sched", "svc/call_scheduled", MessageActionGroupCallScheduled(call=stub_group_call(), schedule_date=0), MessageType.SERVICE_GROUP_CALL),
        ("set_theme", "svc/set_theme", MessageActionSetChatTheme(emoticon="🎨"), MessageType.SERVICE_PIN_MESSAGE),
        ("webview_me", "svc/webview_me", MessageActionWebViewDataSentMe(text="t", data="d"), MessageType.SERVICE_PIN_MESSAGE),
        ("webview", "svc/webview", MessageActionWebViewDataSent(text="t"), MessageType.SERVICE_PIN_MESSAGE),
        ("gift_prem", "svc/gift_premium", MessageActionGiftPremium(currency=STARS_CURRENCY, amount=1, months=1), MessageType.SERVICE_PIN_MESSAGE),
        ("topic_create", "svc/topic_create", MessageActionTopicCreate(title="T", icon_color=0x6FB9F0), MessageType.SERVICE_TOPIC_CREATE),
        ("topic_edit", "svc/topic_edit", MessageActionTopicEdit(title="Renamed"), MessageType.SERVICE_TOPIC_EDIT),
        ("suggest_photo", "svc/suggest_photo", MessageActionSuggestProfilePhoto(photo=stub_photo()), MessageType.SERVICE_PIN_MESSAGE),
        ("wallpaper", "svc/wallpaper", MessageActionSetChatWallPaper(wallpaper=stub_wallpaper()), MessageType.SERVICE_CHAT_UPDATE_WALLPAPER),
        ("gift_code", "svc/gift_code", MessageActionGiftCode(months=1, slug="code"), MessageType.SERVICE_PIN_MESSAGE),
        ("giveaway", "svc/giveaway", MessageActionGiveawayLaunch(), MessageType.SERVICE_PIN_MESSAGE),
        ("giveaway_res", "svc/giveaway_res", MessageActionGiveawayResults(winners_count=1, unclaimed_count=0), MessageType.SERVICE_PIN_MESSAGE),
        ("boost", "svc/boost", MessageActionBoostApply(boosts=1), MessageType.SERVICE_PIN_MESSAGE),
        ("gift_stars", "svc/gift_stars", MessageActionGiftStars(currency=STARS_CURRENCY, amount=1, stars=1), MessageType.SERVICE_PIN_MESSAGE),
        ("prize_stars", "svc/prize_stars", lambda peer: MessageActionPrizeStars(
            stars=1, transaction_id="x", boost_peer=PeerUser(user_id=peer.owner_id), giveaway_msg_id=1,
        ), MessageType.SERVICE_PIN_MESSAGE),
        ("refunded", "svc/refunded", lambda peer: MessageActionPaymentRefunded(
            peer=PeerUser(user_id=peer.owner_id), currency=STARS_CURRENCY, total_amount=1, charge=stub_payment_charge(),
        ), MessageType.SERVICE_PAYMENT),
        ("req_peer", "svc/requested_peer", lambda peer: MessageActionRequestedPeer(
            button_id=1, peers=[PeerUser(user_id=peer.owner_id)],
        ), MessageType.SERVICE_PIN_MESSAGE),
        ("req_peer_me", "svc/req_peer_me", lambda peer: MessageActionRequestedPeerSentMe(
            button_id=1, peers=[RequestedPeerUser(user_id=peer.owner_id)],
        ), MessageType.SERVICE_PIN_MESSAGE),
        ("paid_refund", "svc/paid_refund", MessageActionPaidMessagesRefunded(count=1, stars=1), MessageType.SERVICE_PIN_MESSAGE),
        ("paid_price", "svc/paid_price", MessageActionPaidMessagesPrice(stars=1), MessageType.SERVICE_PIN_MESSAGE),
        ("star_gift", "svc/star_gift", MessageActionStarGift(gift=b.stub_star_gift()), MessageType.SERVICE_PIN_MESSAGE),
        ("star_gift_u", "svc/star_gift_u", MessageActionStarGiftUnique(gift=b.stub_star_gift()), MessageType.SERVICE_PIN_MESSAGE),
    ]
    specimens: list[Specimen] = []
    handlers: dict[bytes, Handler] = {}
    for slug, label, action, mtype in items:
        key = f"cat:svc:{slug}".encode()
        specimens.append(Specimen(key, label, "service", False))
        handlers[key] = _make_svc_handler(action, mtype)
    return specimens, handlers


def stub_photo():
    return b.stub_photo()


def stub_wallpaper():
    return b.stub_wallpaper()


def stub_group_call():
    return b.stub_group_call()


def stub_payment_charge():
    return b.stub_payment_charge()


# --- user-as-sender ---

async def usr_plain(peer: Peer) -> MessageRef:
    await b.inject_user(peer, "user/plain")
    return await b.send_bot_message(peer, "catalog: user/plain sent")


async def usr_inline(peer: Peer) -> MessageRef:
    await b.inject_user(
        peer, "user/inline",
        reply_markup=ReplyInlineMarkup(rows=[
            KeyboardButtonRow(buttons=[KeyboardButtonCallback(text="cb", data=b"ping")]),
        ]).write(),
    )
    return await b.send_bot_message(peer, "catalog: user/inline sent")


async def usr_reply(peer: Peer) -> MessageRef:
    await b.inject_user(
        peer, "user/reply_kb",
        reply_markup=ReplyKeyboardMarkup(
            resize=True,
            rows=[KeyboardButtonRow(buttons=[KeyboardButton(text="A")])],
        ).write(),
    )
    return await b.send_bot_message(peer, "catalog: user/reply_kb sent")


async def usr_invoice(peer: Peer) -> MessageRef:
    amount = 3
    await b.inject_user(
        peer, "user/invoice",
        message="",
        media=await b.catalog_invoice(amount, b"catalog/usr-inv"),
        reply_markup=make_invoice_buy_markup(STARS_CURRENCY, amount).write(),
    )
    return await b.send_bot_message(peer, "catalog: user/invoice+Buy sent")


async def usr_buy_plain(peer: Peer) -> MessageRef:
    await b.inject_user(
        peer, "user/buy_plain",
        reply_markup=ReplyInlineMarkup(rows=[
            KeyboardButtonRow(buttons=[KeyboardButtonBuy(text="Pay ⭐ 1")]),
        ]).write(),
    )
    return await b.send_bot_message(peer, "catalog: user/buy_plain sent")


async def usr_dice(peer: Peer) -> MessageRef:
    await b.inject_user(peer, "user/dice", message="", media=await b.catalog_dice())
    return await b.send_bot_message(peer, "catalog: user/dice sent")


# --- notifications ---

async def notif_popup(peer: Peer) -> MessageRef:
    await b.send_notification(peer, popup=True, message="**catalog/notif/popup**")
    return await b.send_bot_message(peer, "catalog: UpdateServiceNotification popup")


async def notif_silent(peer: Peer) -> MessageRef:
    await b.send_notification(peer, popup=False, message="catalog/notif/silent")
    return await b.send_bot_message(peer, "catalog: UpdateServiceNotification silent")


# --- impossible ---

async def imp_svc_invoice(peer: Peer) -> MessageRef:
    from piltover.db.models import MessageRef as MR
    messages = await MR.create_for_peer(
        peer, peer.user_id, opposite=False,
        type=MessageType.SERVICE_PIN_MESSAGE,
        extra_info=MessageActionPinMessage().write(),
        media=await b.catalog_invoice(1, b"catalog/imp-svc-inv"),
        message="impossible/svc+invoice_media",
    )
    return messages[peer]


async def imp_svc_markup(peer: Peer) -> MessageRef:
    from piltover.db.models import MessageRef as MR
    messages = await MR.create_for_peer(
        peer, peer.user_id, opposite=False,
        type=MessageType.SERVICE_PIN_MESSAGE,
        extra_info=MessageActionPinMessage().write(),
        reply_markup=ReplyInlineMarkup(rows=[
            KeyboardButtonRow(buttons=[KeyboardButtonCallback(text="?!", data=b"ping")]),
        ]).write(),
    )
    return messages[peer]


async def imp_wrong_slot(peer: Peer) -> MessageRef:
    return await b.send_service(
        peer,
        MessageActionCustomAction(message="impossible/wrong_type_slot"),
        MessageType.SERVICE_PIN_MESSAGE,
    )


async def imp_regular_svc_bytes(peer: Peer) -> MessageRef:
    from piltover.db.models import MessageRef as MR
    messages = await MR.create_for_peer(
        peer, peer.user_id, opposite=False,
        message="impossible/regular+action_bytes",
        type=MessageType.REGULAR,
        extra_info=MessageActionPinMessage().write(),
    )
    return messages[peer]


async def imp_user_svc_pay(peer: Peer) -> MessageRef:
    await b.inject_user_service(
        peer,
        MessageActionPaymentSent(currency=STARS_CURRENCY, total_amount=9),
        MessageType.SERVICE_PAYMENT,
    )
    return await b.send_bot_message(peer, "catalog: impossible/user+svc_payment")


async def imp_user_svc_custom(peer: Peer) -> MessageRef:
    await b.inject_user_service(peer, MessageActionCustomAction(message="user service?!"))
    return await b.send_bot_message(peer, "catalog: impossible/user+svc_custom")


async def imp_null_buy(peer: Peer) -> MessageRef:
    markup = ReplyInlineMarkup(rows=[KeyboardButtonRow(buttons=[KeyboardButtonBuy(text="Buy")])])
    return await b.send_regular(
        peer, "impossible/null+Buy",
        message=None,
        reply_markup=markup.write(),
    )


async def imp_inv_mismatch(peer: Peer) -> MessageRef:
    amount = 5
    markup = ReplyInlineMarkup(rows=[KeyboardButtonRow(buttons=[KeyboardButtonBuy(text="Pay ⭐ 999")])])
    return await b.send_regular(
        peer, "impossible/invoice+mismatch_buy",
        media=await b.catalog_invoice(amount, b"catalog/imp-mis"),
        reply_markup=markup.write(),
    )


async def imp_all_flags(peer: Peer) -> MessageRef:
    msg = await b.send_regular(
        peer, "impossible/all_flags",
        no_forwards=True, via_bot_id=peer.user_id, ttl_period_days=1,
    )
    await b.mark_mentioned(peer, msg)
    await b.mark_pinned(msg)
    await b.mark_from_scheduled(msg)
    await b.mark_edit_hide(msg)
    return msg


async def imp_kitchen(peer: Peer) -> MessageRef:
    markup = ReplyInlineMarkup(rows=[
        KeyboardButtonRow(buttons=[
            KeyboardButtonBuy(text="Buy"),
            KeyboardButtonGame(text="Game"),
            KeyboardButtonCallback(text="2FA", data=b"pwd_ok", requires_password=True),
        ]),
    ])
    return await b.send_regular(
        peer, "impossible/kitchen_sink",
        media=await b.catalog_dice(),
        ttl_period_days=1,
        no_forwards=True,
        reply_markup=markup.write(),
    )


async def imp_user_kitchen(peer: Peer) -> MessageRef:
    amount = 7
    await b.inject_user(
        peer, "impossible/user_kitchen",
        media=await b.catalog_invoice(amount, b"catalog/imp-usr-k"),
        reply_markup=ReplyInlineMarkup(rows=[
            KeyboardButtonRow(buttons=[
                KeyboardButtonBuy(text="Pay ⭐ 999"),
                KeyboardButtonGame(text="Game"),
            ]),
        ]).write(),
    )
    return await b.send_bot_message(peer, "catalog: impossible/user_kitchen sent")


async def imp_scheduled_live(peer: Peer) -> MessageRef:
    from piltover.db.models import MessageRef as MR
    messages = await MR.create_for_peer(
        peer, peer.user_id, opposite=False,
        message="impossible/scheduled_type_delivered",
        type=MessageType.SCHEDULED,
        scheduled_date=datetime_in_future(),
    )
    return messages[peer]


async def imp_mention_no_ent(peer: Peer) -> MessageRef:
    msg = await b.send_regular(peer, "impossible/mentioned_no_entity")
    await b.mark_mentioned(peer, msg)
    return msg


async def imp_bad_entities(peer: Peer) -> MessageRef:
    return await b.send_bot_message(
        peer,
        "[impossible/bad_entities] tiny",
        entities=[
            b.entity_dict(MessageEntityBold, 0, 500),
            b.entity_dict(MessageEntitySpoiler, 3, 999),
        ],
    )


REGULAR_SPECIMENS: list[tuple[bytes, str, Handler]] = [
    (b"cat:reg:plain", "reg/plain", reg_plain),
    (b"cat:reg:empty", "reg/empty", reg_empty),
    (b"cat:reg:null", "reg/null", reg_null),
    (b"cat:reg:caption", "reg/caption+media", reg_caption),
    (b"cat:reg:media_only", "reg/media-only", reg_media_only),
    (b"cat:reg:document", "reg/document", reg_document),
    (b"cat:reg:photo", "reg/photo", reg_photo),
    (b"cat:reg:poll", "reg/poll", reg_poll),
    (b"cat:reg:contact", "reg/contact", reg_contact),
    (b"cat:reg:geo", "reg/geo", reg_geo),
    (b"cat:reg:dice", "reg/dice", reg_dice),
    (b"cat:reg:invoice", "reg/invoice", reg_invoice),
    (b"cat:reg:invoice_cap", "reg/invoice+cap", reg_invoice_cap),
    (b"cat:reg:scheduled", "reg/scheduled", reg_scheduled),
]

FLAG_SPECIMENS: list[tuple[bytes, str, Handler]] = [
    (b"cat:flg:mentioned", "flg/mentioned", flg_mentioned),
    (b"cat:flg:pinned", "flg/pinned", flg_pinned),
    (b"cat:flg:scheduled", "flg/from_scheduled", flg_scheduled),
    (b"cat:flg:edit_hide", "flg/edit_hide", flg_edit_hide),
    (b"cat:flg:noforwards", "flg/noforwards", flg_noforwards),
    (b"cat:flg:via_bot", "flg/via_bot", flg_via_bot),
    (b"cat:flg:ttl", "flg/ttl", flg_ttl),
    (b"cat:flg:spoiler", "flg/media_spoiler", flg_spoiler_media),
]

USER_SPECIMENS: list[tuple[bytes, str, Handler]] = [
    (b"cat:usr:plain", "usr/plain", usr_plain),
    (b"cat:usr:inline", "usr/inline", usr_inline),
    (b"cat:usr:reply", "usr/reply_kb", usr_reply),
    (b"cat:usr:invoice", "usr/invoice", usr_invoice),
    (b"cat:usr:buy_plain", "usr/buy_plain", usr_buy_plain),
    (b"cat:usr:dice", "usr/dice", usr_dice),
]

NOTIF_SPECIMENS: list[tuple[bytes, str, Handler]] = [
    (b"cat:notif:popup", "notif/popup", notif_popup),
    (b"cat:notif:silent", "notif/silent", notif_silent),
]

IMPOSSIBLE_SPECIMENS: list[tuple[bytes, str, Handler]] = [
    (b"cat:imp:svc_inv", "imp/svc+invoice", imp_svc_invoice),
    (b"cat:imp:svc_kb", "imp/svc+markup", imp_svc_markup),
    (b"cat:imp:wrong", "imp/wrong_slot", imp_wrong_slot),
    (b"cat:imp:reg_svc", "imp/reg+action", imp_regular_svc_bytes),
    (b"cat:imp:usr_pay", "imp/usr+svc_pay", imp_user_svc_pay),
    (b"cat:imp:usr_svc", "imp/usr+svc", imp_user_svc_custom),
    (b"cat:imp:null_buy", "imp/null+Buy", imp_null_buy),
    (b"cat:imp:mismatch", "imp/inv_mismatch", imp_inv_mismatch),
    (b"cat:imp:all_flg", "imp/all_flags", imp_all_flags),
    (b"cat:imp:kitchen", "imp/kitchen", imp_kitchen),
    (b"cat:imp:usr_kit", "imp/usr_kitchen", imp_user_kitchen),
    (b"cat:imp:sched", "imp/scheduled", imp_scheduled_live),
    (b"cat:imp:mention", "imp/mention_no_ent", imp_mention_no_ent),
    (b"cat:imp:bad_ent", "imp/bad_entities", imp_bad_entities),
]

_SERVICE_SPECIMENS, _SERVICE_HANDLERS = _build_service_handlers()
_ENTITY_HANDLERS = _init_entity_handlers()


def all_specimens() -> list[Specimen]:
    items: list[Specimen] = []
    for key, label, _ in REGULAR_SPECIMENS:
        items.append(Specimen(key, label, "regular", False))
    for key, label in ((k, l) for k, l, _ in FLAG_SPECIMENS):
        items.append(Specimen(key, label, "flags", False))
    for key, label in ((k, l) for k, l, _ in USER_SPECIMENS):
        items.append(Specimen(key, label, "user", False))
    for key, label in ((k, l) for k, l, _ in NOTIF_SPECIMENS):
        items.append(Specimen(key, label, "notif", False))
    for key, label in ACTION_SPECIMENS:
        items.append(Specimen(key, label, "actions", False))
    items.extend(_SERVICE_SPECIMENS)
    for key in _ENTITY_HANDLERS:
        items.append(Specimen(key, key.decode().split(":")[-1], "entities", False))
    for key, label, _ in IMPOSSIBLE_SPECIMENS:
        items.append(Specimen(key, label, "impossible", True))
    return items


def all_handlers() -> dict[bytes, Handler]:
    handlers: dict[bytes, Handler] = {}
    for key, _, fn in (
        *REGULAR_SPECIMENS, *FLAG_SPECIMENS, *USER_SPECIMENS, *NOTIF_SPECIMENS, *IMPOSSIBLE_SPECIMENS,
    ):
        handlers[key] = fn
    handlers.update(_SERVICE_HANDLERS)
    handlers.update(_ENTITY_HANDLERS)
    return handlers


CATALOG_HANDLERS = all_handlers()