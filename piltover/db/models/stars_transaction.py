from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from tortoise import Model, fields

from piltover.db import models
from piltover.db.enums import StarsTransactionPeerType

from piltover.tl import (
    StarsAmount, StarsTransaction as TLStarsTransaction, StarsTransactionPeer,
    StarsTransactionPeerFragment, StarsTransactionPeerAppStore, StarsTransactionPeerPlayMarket,
    StarsTransactionPeerPremiumBot, StarsTransactionPeerAds, StarsTransactionPeerAPI,
    StarsTransactionPeerUnsupported, PeerUser,
)
from piltover.utils.users_chats_channels import UsersChatsChannels


@dataclass(slots=True)
class StarsTransactionRenderContext:
    stars_bot_user_id: int | None = None


class StarsTransaction(Model):
    transaction_id: str = fields.CharField(max_length=64, primary_key=True)
    user: models.User = fields.ForeignKeyField("models.User", related_name="stars_transactions")
    stars_amount: int = fields.BigIntField()
    stars_nanos: int = fields.IntField(default=0)
    inbound: bool = fields.BooleanField()
    date: int = fields.IntField()
    peer_type: StarsTransactionPeerType = fields.IntEnumField(StarsTransactionPeerType)
    peer_user: models.User | None = fields.ForeignKeyField(
        "models.User", null=True, default=None, related_name="stars_transactions_as_peer",
    )
    title: str | None = fields.CharField(max_length=256, null=True, default=None)
    description: str | None = fields.CharField(max_length=512, null=True, default=None)
    gift: bool = fields.BooleanField(default=False)
    refund: bool = fields.BooleanField(default=False)
    msg_id: int | None = fields.IntField(null=True, default=None)
    bot_payload: bytes | None = fields.BinaryField(null=True, default=None)

    user_id: int
    peer_user_id: int | None

    @staticmethod
    def gen_id() -> str:
        return uuid4().hex

    def to_stars_amount(self) -> StarsAmount:
        signed_amount = self.stars_amount if self.inbound else -self.stars_amount
        signed_nanos = self.stars_nanos if self.inbound else -self.stars_nanos
        return StarsAmount(amount=signed_amount, nanos=signed_nanos)

    def _resolve_peer_user_id(self, ctx: StarsTransactionRenderContext | None) -> int | None:
        if self.peer_user_id is not None:
            return self.peer_user_id
        if self.peer_type is StarsTransactionPeerType.API and ctx is not None:
            return ctx.stars_bot_user_id
        return None

    def _peer_tl(
            self, ucc: UsersChatsChannels, ctx: StarsTransactionRenderContext | None = None,
    ) -> StarsTransactionPeer | StarsTransactionPeerFragment | StarsTransactionPeerAppStore | StarsTransactionPeerPlayMarket | StarsTransactionPeerPremiumBot | StarsTransactionPeerAds | StarsTransactionPeerAPI:
        peer_user_id = self._resolve_peer_user_id(ctx)
        match self.peer_type:
            case StarsTransactionPeerType.FRAGMENT:
                return StarsTransactionPeerFragment()
            case StarsTransactionPeerType.APP_STORE:
                return StarsTransactionPeerAppStore()
            case StarsTransactionPeerType.PLAY_MARKET:
                return StarsTransactionPeerPlayMarket()
            case StarsTransactionPeerType.PREMIUM_BOT:
                if peer_user_id is not None:
                    ucc.add_user(peer_user_id)
                    return StarsTransactionPeer(peer=PeerUser(user_id=peer_user_id))
                return StarsTransactionPeerPremiumBot()
            case StarsTransactionPeerType.ADS:
                return StarsTransactionPeerAds()
            case StarsTransactionPeerType.API:
                if peer_user_id is not None:
                    ucc.add_user(peer_user_id)
                    return StarsTransactionPeer(peer=PeerUser(user_id=peer_user_id))
                return StarsTransactionPeerAPI()
            case StarsTransactionPeerType.PEER:
                if peer_user_id is None:
                    return StarsTransactionPeerUnsupported()
                ucc.add_user(peer_user_id)
                return StarsTransactionPeer(peer=PeerUser(user_id=peer_user_id))
        return StarsTransactionPeerFragment()

    def to_tl(
            self, ucc: UsersChatsChannels, ctx: StarsTransactionRenderContext | None = None,
    ) -> TLStarsTransaction:
        title = self.title or "Telegram Stars"
        description = self.description or title
        transaction_date = None
        transaction_url = None
        if self.msg_id is not None:
            transaction_date = self.date
            transaction_url = f"https://t.me/$/stars/transactions/{self.transaction_id}"
        return TLStarsTransaction(
            id=self.transaction_id,
            stars=self.to_stars_amount(),
            date=self.date,
            peer=self._peer_tl(ucc, ctx),
            title=title,
            description=description,
            gift=self.gift,
            refund=self.refund,
            transaction_date=transaction_date,
            transaction_url=transaction_url,
            msg_id=self.msg_id,
            bot_payload=self.bot_payload,
        )