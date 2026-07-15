from piltover.enums import ReqHandlerFlags
from piltover.tl import EmojiList, StatsGraphError
from piltover.tl.functions.account import GetCollectibleEmojiStatuses, GetContactSignUpNotification, \
    SetContactSignUpNotification, GetChannelRestrictedStatusEmojis
from piltover.tl.functions.bots import GetPopularAppBots, GetBotRecommendations
from piltover.app.utils.channel_recommendations import get_random_public_broadcast_channels
from piltover.db.models import Channel
from piltover.tl.functions.channels import GetChannelRecommendations, GetChannelRecommendations_167
from piltover.tl.functions.contacts import GetSponsoredPeers
from piltover.tl.functions.premium import GetBoostsStatus, GetMyBoosts, GetBoostsList
from piltover.tl.functions.stats import GetBroadcastRevenueStats
from piltover.tl.types.account import EmojiStatuses
from piltover.tl.types.bots import PopularAppBots
from piltover.tl.types.contacts import SponsoredPeers
from piltover.tl.types.messages import Chats
from piltover.tl.types.premium import BoostsStatus, MyBoosts, BoostsList
from piltover.tl.types.stats import BroadcastRevenueStats
from piltover.tl.types.users import Users
from piltover.worker import MessageHandler

handler = MessageHandler("stubs")

MAX_I32 = 2 ** 31 - 1
MAX_I64 = 2 ** 63 - 1


NOBOT_NOAUTH = ReqHandlerFlags.BOT_NOT_ALLOWED | ReqHandlerFlags.AUTH_NOT_REQUIRED


@handler.on_request(GetBoostsStatus, ReqHandlerFlags.AUTH_NOT_REQUIRED)
async def get_boosts_status() -> BoostsStatus:  # pragma: no cover
    return BoostsStatus(
        level=MAX_I32,
        current_level_boosts=MAX_I32,
        boosts=MAX_I32,
        boost_url="http://unreachable.local/"
    )


@handler.on_request(GetMyBoosts, ReqHandlerFlags.AUTH_NOT_REQUIRED)
async def get_my_boosts() -> MyBoosts:  # pragma: no cover
    return MyBoosts(my_boosts=[], chats=[], users=[])


@handler.on_request(GetCollectibleEmojiStatuses, ReqHandlerFlags.AUTH_NOT_REQUIRED)
async def get_collectible_emoji_statuses() -> EmojiStatuses:  # pragma: no cover
    return EmojiStatuses(
        hash=0,
        statuses=[],
    )


@handler.on_request(GetBoostsList, ReqHandlerFlags.AUTH_NOT_REQUIRED)
async def get_boosts_list() -> BoostsList:  # pragma: no cover
    return BoostsList(
        count=MAX_I32,
        boosts=[],
        users=[],
    )


@handler.on_request(GetContactSignUpNotification, NOBOT_NOAUTH)
async def get_contact_sign_up_notification() -> bool:  # pragma: no cover
    return False


@handler.on_request(SetContactSignUpNotification, NOBOT_NOAUTH)
async def set_contact_sign_up_notification() -> bool:  # pragma: no cover
    return False


@handler.on_request(GetPopularAppBots, NOBOT_NOAUTH)
async def get_popular_app_bots() -> PopularAppBots:  # pragma: no cover
    return PopularAppBots(users=[])


@handler.on_request(GetChannelRestrictedStatusEmojis, NOBOT_NOAUTH)
async def get_channel_restricted_status_emojis() -> EmojiList:  # pragma: no cover
    return EmojiList(hash=0, document_id=[])


@handler.on_request(GetSponsoredPeers, NOBOT_NOAUTH)
async def get_sponsored_peers() -> SponsoredPeers:  # pragma: no cover
    return SponsoredPeers(peers=[], users=[], chats=[])


@handler.on_request(GetBotRecommendations, NOBOT_NOAUTH)
async def get_bot_recommendations() -> Users:  # pragma: no cover
    return Users(users=[])


@handler.on_request(GetBroadcastRevenueStats, NOBOT_NOAUTH)
async def get_broadcast_revenue_stats() -> BroadcastRevenueStats:  # pragma: no cover
    return BroadcastRevenueStats(
        top_hours_graph=StatsGraphError(error="no stats"),
        revenue_graph=StatsGraphError(error="no stats"),
        balances=StatsGraphError(error="no stats"),
        usd_rate=1.0,
    )


@handler.on_request(GetChannelRecommendations, NOBOT_NOAUTH | ReqHandlerFlags.DONT_FETCH_USER)
@handler.on_request(GetChannelRecommendations_167, NOBOT_NOAUTH | ReqHandlerFlags.DONT_FETCH_USER)
async def get_channel_recommendations(
        request: GetChannelRecommendations | GetChannelRecommendations_167, user_id: int,
) -> Chats:
    exclude_id = None
    if request.channel is not None:
        exclude_id = Channel.norm_id(request.channel.channel_id)
    channels = await get_random_public_broadcast_channels(exclude_id=exclude_id)
    return Chats(chats=await Channel.to_tl_bulk(channels))
