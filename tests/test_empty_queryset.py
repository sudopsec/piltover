import pytest

from piltover.db.models import Channel
from piltover.db.utils.awaitable_none_queryset import EmptyQuerySet


@pytest.mark.asyncio
async def test_empty_queryset_get_or_none_returns_none() -> None:
    result = await EmptyQuerySet(Channel).get_or_none()
    assert result is None