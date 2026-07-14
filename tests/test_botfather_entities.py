import pytest

from piltover.app.bot_handlers.botfather.utils import apply_message_edit
from piltover.tl import MessageEntityBold


@pytest.mark.asyncio
async def test_apply_message_edit_clears_entities() -> None:
    from piltover.db.models import MessageContent

    content = await MessageContent.create(
        message="old",
        entities=[{"_": MessageEntityBold.tlid(), "offset": 0, "length": 3}],
    )

    apply_message_edit(content, message="plain text", entities=None)
    await content.save(update_fields=["message", "entities", "version"])

    await content.refresh_from_db()
    assert content.message == "plain text"
    assert content.entities is None