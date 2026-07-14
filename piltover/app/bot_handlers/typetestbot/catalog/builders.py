from __future__ import annotations

from datetime import datetime, UTC
from time import time
from uuid import uuid4

import piltover.app.utils.updates_manager as upd
from piltover.app.utils.formatable_text_with_entities import FormatableTextWithEntities
from piltover.app.utils.stars_manager import STARS_CURRENCY, _pack_invoice_static, make_invoice_buy_markup
from piltover.db.enums import FileType, MediaType, MessageType
from piltover.db.models import File, MessageMedia, MessageMention, MessageRef, Peer, Poll, PollAnswer
from piltover.session import SessionManager
from piltover.tl import (
    Document,
    DocumentAttributeFilename,
    GeoPoint,
    InputGroupCall,
    MessageMediaContact,
    MessageMediaDice,
    MessageMediaGeo,
    MessageMediaInvoice,
    MessageMediaPhoto,
    MessageMediaEmpty,
    PaymentCharge,
    PhoneCallProtocol,
    Photo,
    PhotoSize,
    TextWithEntities,
    UpdateServiceNotification,
    WallPaper,
    objects,
)
from piltover.tl.base import MessageActionInst, ReplyMarkup
from piltover.app.utils.updates_manager import UpdatesWithDefaults
from piltover.app.bot_handlers.typetestbot.common import send_bot_message


def entity_dict(tl_type: type, offset: int, length: int, **extra) -> dict:
    return {"_": tl_type.tlid(), "offset": offset, "length": length, **extra}


def entity_for_substring(
        message: str, substring: str, tl_type: type, *, start: int = 0, **extra,
) -> dict:
    pos = message.index(substring, start)
    return entity_at(message, pos, substring, tl_type, **extra)


def entity_at(message: str, pos: int, text: str, tl_type: type, **extra) -> dict:
    from piltover.app.utils.formatable_text_with_entities import build_u8_to_u16

    u8_to_u16 = build_u8_to_u16(message)
    end = pos + len(text)
    return entity_dict(tl_type, u8_to_u16[pos], u8_to_u16[end] - u8_to_u16[pos], **extra)


async def send_service(peer: Peer, action: MessageActionInst, msg_type: MessageType | None = None) -> MessageRef:
    from piltover.app.bot_handlers.typetestbot.common import send_service_message
    return await send_service_message(peer, action, msg_type or MessageType.SERVICE_PIN_MESSAGE)


async def send_regular(peer: Peer, label: str, **kwargs) -> MessageRef:
    return await send_bot_message(peer, f"[{label}]", **kwargs)


async def inject_user(peer: Peer, label: str, **kwargs) -> MessageRef:
    from piltover.app.bot_handlers.typetestbot.common import inject_as_user
    messages = await MessageRef.create_for_peer(
        peer, peer.owner_id, opposite=False,
        message=kwargs.pop("message", f"[USER:{label}]"),
        **kwargs,
    )
    user_message = messages[peer]
    await upd.send_message(peer.owner_id, {peer: user_message}, False)
    return user_message


async def inject_user_service(peer: Peer, action: MessageActionInst, msg_type: MessageType | None = None) -> MessageRef:
    from piltover.app.bot_handlers.typetestbot.common import inject_service_as_user
    return await inject_service_as_user(peer, action, msg_type or MessageType.SERVICE_PIN_MESSAGE)


async def send_notification(peer: Peer, *, popup: bool, message: str) -> None:
    text, entity_dicts = FormatableTextWithEntities(message).format()
    entities = []
    for ent in entity_dicts:
        tl_id = ent.pop("_")
        entities.append(objects[tl_id](**ent))
        ent["_"] = tl_id
    await SessionManager.send(
        UpdatesWithDefaults(updates=[
            UpdateServiceNotification(
                popup=popup,
                inbox_date=int(time()) if popup else None,
                type_=f"CATALOG_{int(time())}",
                message=text,
                media=MessageMediaEmpty(),
                entities=entities,
            ),
        ]),
        peer.owner_id,
    )


async def mark_mentioned(peer: Peer, message: MessageRef) -> None:
    await MessageMention.get_or_create(user_id=peer.owner_id, message_id=message.content_id)


async def mark_pinned(message: MessageRef) -> None:
    message.pinned = True
    await message.save(update_fields=["pinned"])


async def mark_edit_hide(message: MessageRef) -> None:
    message.content.edit_hide = True
    message.content.edit_date = datetime.now(UTC)
    message.content.version += 1
    await message.content.save(update_fields=["edit_hide", "edit_date", "version"])


async def mark_from_scheduled(message: MessageRef) -> None:
    message.content.scheduled_date = datetime.now(UTC)
    message.content.version += 1
    await message.content.save(update_fields=["scheduled_date", "version"])


async def catalog_document() -> MessageMedia:
    file = await File.create(
        mime_type="text/plain",
        size=12,
        type=FileType.DOCUMENT,
        filename="catalog.txt",
        constant_access_hash=0xCA7A106,
        constant_file_ref=uuid4(),
    )
    return await MessageMedia.create(type=MediaType.DOCUMENT, file=file)


async def catalog_photo() -> MessageMedia:
    file = await File.create(
        mime_type="image/jpeg",
        size=128,
        type=FileType.PHOTO,
        constant_access_hash=0xCA7A107,
        constant_file_ref=uuid4(),
        photo_sizes=[{"type": "m", "w": 100, "h": 100, "size": 128}],
    )
    return await MessageMedia.create(type=MediaType.PHOTO, file=file)


async def catalog_poll() -> MessageMedia:
    poll = await Poll.create(
        quiz=False,
        public_voters=False,
        multiple_choices=False,
        question="Catalog poll?",
        question_entities=[],
    )
    await PollAnswer.bulk_create([
        PollAnswer(poll=poll, text="A", entities=[], option=b"\x00\x00\x00\x00\x00\x00\x00\x00", correct=False),
        PollAnswer(poll=poll, text="B", entities=[], option=b"\x00\x00\x00\x00\x00\x00\x00\x01", correct=False),
    ])
    return await MessageMedia.create(type=MediaType.POLL, poll=poll)


async def catalog_contact() -> MessageMedia:
    return await MessageMedia.create(
        type=MediaType.CONTACT,
        static_data=MessageMediaContact(
            phone_number="+10000000000",
            first_name="Catalog",
            last_name="Contact",
            vcard="",
            user_id=0,
        ).write(),
    )


async def catalog_geo() -> MessageMedia:
    return await MessageMedia.create(
        type=MediaType.GEOPOINT,
        static_data=MessageMediaGeo(
            geo=GeoPoint(long=0.0, lat=0.0, access_hash=0, accuracy_radius=1),
        ).write(),
    )


async def catalog_dice() -> MessageMedia:
    return await MessageMedia.create(
        type=MediaType.DICE,
        static_data=MessageMediaDice(value=3, emoticon="🎲").write(),
    )


async def catalog_invoice(amount: int = 1, param: bytes = b"catalog/invoice") -> MessageMedia:
    invoice_tl = MessageMediaInvoice(
        title=f"{amount} Stars",
        description="Catalog invoice",
        currency=STARS_CURRENCY,
        total_amount=amount,
        start_param="catalog",
    )
    return await MessageMedia.create(
        type=MediaType.INVOICE,
        static_data=_pack_invoice_static(invoice_tl, param),
    )


def stub_photo() -> Photo:
    return Photo(
        id=1, access_hash=0, file_reference=b"", date=0,
        sizes=[PhotoSize(type_="m", w=1, h=1, size=1)], dc_id=1,
    )


def stub_wallpaper() -> WallPaper:
    return WallPaper(
        id=1, access_hash=0, slug="catalog",
        document=Document(
            id=1, access_hash=0, file_reference=b"", date=0,
            mime_type="image/jpeg", size=1, dc_id=1,
            attributes=[DocumentAttributeFilename(file_name="wp.jpg")],
        ),
    )


def stub_group_call() -> InputGroupCall:
    return InputGroupCall(id=1, access_hash=0)


def stub_payment_charge() -> PaymentCharge:
    return PaymentCharge(id="catalog", provider_charge_id="catalog")


def stub_star_gift():
    from piltover.tl import StarGift
    doc = Document(
        id=1, access_hash=0, file_reference=b"", date=0,
        mime_type="application/x-tgsticker", size=1, dc_id=1,
        attributes=[DocumentAttributeFilename(file_name="gift.tgs")],
    )
    return StarGift(id=1, sticker=doc, stars=1, convert_stars=1)


def stub_text_entities(text: str = "") -> TextWithEntities:
    return TextWithEntities(text=text, entities=[])


def stub_call_protocol() -> bytes:
    return PhoneCallProtocol(
        udp_p2p=True,
        udp_reflector=True,
        min_layer=92,
        max_layer=92,
        library_versions=["11.0.0"],
    ).write()


async def send_bot_typing(peer: Peer, action) -> None:
    from piltover.db.models import User
    from piltover.tl import UpdateUserTyping

    bot = await User.get(id=peer.user_id)
    await SessionManager.send(
        UpdatesWithDefaults(
            updates=[UpdateUserTyping(user_id=peer.user_id, action=action)],
            users=[await bot.to_tl()],
        ),
        peer.owner_id,
    )


async def send_incoming_call(peer: Peer) -> None:
    from os import urandom

    from piltover.db.models import PhoneCall, UserAuthorization

    from_sess = await UserAuthorization.first()
    if from_sess is None:
        raise RuntimeError("No user authorization available for catalog phone call demo")

    call = await PhoneCall.create(
        from_user_id=peer.user_id,
        from_sess_id=from_sess.id,
        to_user_id=peer.owner_id,
        g_a_hash=urandom(32),
        protocol=stub_call_protocol(),
    )
    call = await PhoneCall.get(id=call.id).select_related("from_user", "to_user")
    await upd.phone_call_update(peer.owner_id, call)