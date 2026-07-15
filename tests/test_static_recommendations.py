import pytest

from piltover.app.handlers.stubs import get_channel_recommendations
from piltover.app.utils.channel_recommendations import get_random_public_broadcast_channels
from piltover.app.utils.test_sponsored_messages import _stable_sponsored_random_id, build_channel_sponsored_messages
from piltover.app.utils.utils import is_username_valid
from piltover.db.models import Channel, User, Username
from piltover.tl import InputChannel
from piltover.tl.functions.channels import GetChannelRecommendations
from piltover.tl.to_format import ChannelToFormat


def test_is_username_valid_allows_single_character() -> None:
    assert is_username_valid("a")
    assert is_username_valid("x")
    assert not is_username_valid("")
    assert not is_username_valid("a" * 33)


async def _create_public_channel(owner: User, name: str, username: str) -> Channel:
    channel = await Channel.create(name=name, creator=owner, channel=True, supergroup=False)
    await Username.create(channel=channel, username=username)
    return channel


@pytest.mark.asyncio
async def test_random_public_channel_recommendations() -> None:
    owner = await User.create(phone_number="900200100", first_name="Owner")
    created = [
        await _create_public_channel(owner, f"Channel {i}", f"reco_test_{i}")
        for i in range(6)
    ]

    result = await get_random_public_broadcast_channels(limit=5)
    assert len(result) == 5
    assert {channel.id for channel in result}.issubset({channel.id for channel in created})

    excluded = await get_random_public_broadcast_channels(exclude_id=created[0].id, limit=5)
    assert all(channel.id != created[0].id for channel in excluded)
    assert len(excluded) == 5


@pytest.mark.asyncio
async def test_get_channel_recommendations_handler() -> None:
    owner = await User.create(phone_number="900200101", first_name="Owner")
    current = await _create_public_channel(owner, "Current", "reco_current")
    for i in range(5):
        await _create_public_channel(owner, f"Other {i}", f"reco_other_{i}")

    result = await get_channel_recommendations(
        GetChannelRecommendations(channel=None), user_id=owner.id,
    )
    assert len(result.chats) == 5
    assert all(isinstance(chat, ChannelToFormat) for chat in result.chats)
    assert all(chat.broadcast for chat in result.chats)

    excluded_result = await get_channel_recommendations(
        GetChannelRecommendations(
            channel=InputChannel(channel_id=current.make_id(), access_hash=0),
        ),
        user_id=owner.id,
    )
    assert len(excluded_result.chats) == 5
    assert all(chat.id != current.id for chat in excluded_result.chats)


def test_stable_sponsored_random_id_is_deterministic() -> None:
    first = _stable_sponsored_random_id(1, 2, 0)
    second = _stable_sponsored_random_id(1, 2, 0)
    third = _stable_sponsored_random_id(1, 3, 0)
    assert first == second
    assert first != third


@pytest.mark.asyncio
async def test_build_channel_sponsored_messages() -> None:
    owner = await User.create(phone_number="900200102", first_name="Owner")
    viewing = await _create_public_channel(owner, "Viewing", "reco_view")
    await _create_public_channel(owner, "Promoted", "reco_promo")

    result = await build_channel_sponsored_messages(viewing)
    assert result.posts_between == 5
    assert len(result.messages) >= 1
    assert result.messages[0].title == "Piltover Test Ad"
    assert result.messages[0].sponsor_info == "Реклама"
    assert len(result.chats) >= 1

    result_again = await build_channel_sponsored_messages(viewing)
    assert result_again.messages[0].random_id == result.messages[0].random_id