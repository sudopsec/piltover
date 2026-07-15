from contextlib import AsyncExitStack

import pytest
from pyrogram.errors import InviteHashInvalid, UserAlreadyParticipant, PeerIdInvalid, InviteHashExpired, \
    InviteRequestSent
from pyrogram.raw.functions.messages import ExportChatInvite
from pyrogram.raw.types import UpdateChannel, UpdateNewChannelMessage
from pyrogram.types import Chat, ChatPreview
from pyrogram.utils import get_channel_id

from tests.client import TestClient
from tests.conftest import ChannelWithClientsFactory, ClientFactory

PHOTO_COLOR = (0x00, 0xff, 0x00)


@pytest.mark.asyncio
async def test_export_chat_invite() -> None:
    async with TestClient(phone_number="123456789") as client:
        group = await client.create_group("idk", [])
        invite_link = await group.export_invite_link()

        assert invite_link.startswith("https://t.me/+")


@pytest.mark.asyncio
async def test_get_chat_invite_info() -> None:
    async with TestClient(phone_number="123456789") as client1, TestClient(phone_number="1234567890") as client2:
        group = await client1.create_group("idk", [])
        invite_link = await group.export_invite_link()
        assert invite_link.startswith("https://t.me/+")

        chat1 = await client1.get_chat(invite_link)
        assert chat1
        assert isinstance(chat1, Chat)
        assert chat1.id == group.id
        assert chat1.is_creator
        assert chat1.title == group.title

        chat2 = await client2.get_chat(invite_link)
        assert chat2
        assert isinstance(chat2, ChatPreview)
        assert chat2.title == group.title
        
        
@pytest.mark.asyncio
async def test_get_chat_invite_info_after_exporting_new_link_with_revoking_existing() -> None:
    async with TestClient(phone_number="123456789") as client1:
        group = await client1.create_group("idk", [])
        
        invite_link1 = await group.export_invite_link()
        chat = await client1.get_chat(invite_link1)
        assert chat
        assert isinstance(chat, Chat)
        assert chat.id == group.id
        assert chat.is_creator
        assert chat.title == group.title

        invite_link2 = await group.export_invite_link()
        with pytest.raises(InviteHashInvalid):
            await client1.get_chat(invite_link1)

        chat = await client1.get_chat(invite_link2)
        assert chat
        assert isinstance(chat, Chat)
        assert chat.id == group.id
        assert chat.is_creator
        assert chat.title == group.title
        

@pytest.mark.asyncio
async def test_supergroup_join_sees_prejoin_history() -> None:
    from piltover.db.models import Channel, ChatParticipant

    async with TestClient(phone_number="123456789") as client1, TestClient(phone_number="1234567890") as client2:
        group = await client1.create_supergroup("history group")
        await client1.send_message(group.id, "before join 1")
        await client1.send_message(group.id, "before join 2")

        invite_link = await group.export_invite_link()
        joined = await client2.join_chat(invite_link)
        assert joined.id == group.id

        db_channel = await Channel.get(id=Channel.norm_id(get_channel_id(group.id)))
        participant = await ChatParticipant.get(user_id=client2.me.id, channel_id=db_channel.id)
        assert participant.min_message_id is None

        messages = [m async for m in client2.get_chat_history(group.id)]
        texts = [m.text for m in messages if m.text]
        assert "before join 1" in texts
        assert "before join 2" in texts


@pytest.mark.asyncio
async def test_supergroup_join_sees_history_after_prehistory_disabled() -> None:
    from pyrogram.raw.functions.channels import TogglePreHistoryHidden
    from pyrogram.raw.types import InputPeerChannel

    from piltover.db.models import Channel, ChatParticipant

    async with TestClient(phone_number="123456789") as client1, TestClient(phone_number="1234567890") as client2:
        group = await client1.create_supergroup("history group 2")
        await client1.send_message(group.id, "old message")

        db_channel = await Channel.get(id=Channel.norm_id(get_channel_id(group.id)))
        peer = await client1.resolve_peer(group.id)
        assert isinstance(peer, InputPeerChannel)

        await client1.invoke(TogglePreHistoryHidden(channel=peer, enabled=True))
        await client1.invoke(TogglePreHistoryHidden(channel=peer, enabled=False))

        await db_channel.refresh_from_db()
        assert db_channel.hidden_prehistory is False
        assert db_channel.min_available_id is not None

        invite_link = await group.export_invite_link()
        await client2.join_chat(invite_link)

        participant = await ChatParticipant.get(user_id=client2.me.id, channel_id=db_channel.id)
        assert participant.min_message_id == db_channel.min_available_id

        messages = [m async for m in client2.get_chat_history(group.id)]
        texts = [m.text for m in messages if m.text]
        assert "old message" not in texts


@pytest.mark.asyncio
async def test_public_supergroup_join_sees_full_history() -> None:
    from faker import Faker
    from pyrogram.raw.functions.channels import TogglePreHistoryHidden
    from pyrogram.raw.types import InputPeerChannel

    from piltover.db.models import Channel, ChatParticipant

    faker = Faker()
    async with TestClient(phone_number="123456789") as client1, TestClient(phone_number="1234567890") as client2:
        group = await client1.create_supergroup("public history group")
        await client1.send_message(group.id, "before public")

        peer = await client1.resolve_peer(group.id)
        assert isinstance(peer, InputPeerChannel)
        await client1.invoke(TogglePreHistoryHidden(channel=peer, enabled=True))
        await client1.invoke(TogglePreHistoryHidden(channel=peer, enabled=False))

        username = faker.user_name()
        await client1.set_chat_username(group.id, username)

        db_channel = await Channel.get(id=Channel.norm_id(get_channel_id(group.id)))
        assert db_channel.min_available_id is not None

        await client2.join_chat(username)

        participant = await ChatParticipant.get(user_id=client2.me.id, channel_id=db_channel.id)
        assert participant.min_message_id is None

        messages = [m async for m in client2.get_chat_history(group.id)]
        texts = [m.text for m in messages if m.text]
        assert "before public" in texts


@pytest.mark.asyncio
async def test_join_chat_invite() -> None:
    async with TestClient(phone_number="123456789") as client1, TestClient(phone_number="1234567890") as client2:
        group = await client1.create_group("idk", [])
        invite_link = await group.export_invite_link()
        assert invite_link.startswith("https://t.me/+")

        with pytest.raises(UserAlreadyParticipant):
            await client1.join_chat(invite_link)

        with pytest.raises(PeerIdInvalid):
            await client2.send_message(group.id, "test message")

        chat2 = await client2.join_chat(invite_link)
        assert chat2.id == group.id
        assert await client2.send_message(chat2.id, "test message")


@pytest.mark.asyncio
async def test_get_exported_chat_invite_info() -> None:
    async with TestClient(phone_number="123456789") as client:
        group = await client.create_group("idk", [])
        invite_link = await group.export_invite_link()

        invite_info = await client.get_chat_invite_link(group.id, invite_link)
        assert invite_info
        assert not invite_info.is_revoked

        with pytest.raises(InviteHashExpired):
            await client.get_chat_invite_link(group.id, invite_link + "A")
        with pytest.raises(InviteHashExpired):
            await client.get_chat_invite_link(group.id, "invalid link")


@pytest.mark.asyncio
async def test_get_exported_chat_invites() -> None:
    async with TestClient(phone_number="123456789") as client:
        group = await client.create_group("idk", [])
        await group.export_invite_link()

        active_links = [link async for link in client.get_chat_admin_invite_links(group.id, "me", False)]
        revoked_links = [link async for link in client.get_chat_admin_invite_links(group.id, "me", True)]
        assert len(active_links) == 1
        assert len(revoked_links) == 0
        assert await client.get_chat_admin_invite_links_count(group.id, "me", False) == 1
        assert await client.get_chat_admin_invite_links_count(group.id, "me", True) == 0

        await group.export_invite_link()
        await group.export_invite_link()

        active_links = [link async for link in client.get_chat_admin_invite_links(group.id, "me", False)]
        revoked_links = [link async for link in client.get_chat_admin_invite_links(group.id, "me", True)]
        assert len(active_links) == 1
        assert len(revoked_links) == 2
        assert await client.get_chat_admin_invite_links_count(group.id, "me", False) == 1
        assert await client.get_chat_admin_invite_links_count(group.id, "me", True) == 2


@pytest.mark.asyncio
async def test_delete_revoked_exported_chat_invites() -> None:
    async with TestClient(phone_number="123456789") as client:
        group = await client.create_group("idk", [])
        await group.export_invite_link()

        assert await client.get_chat_admin_invite_links_count(group.id, "me", False) == 1
        assert await client.get_chat_admin_invite_links_count(group.id, "me", True) == 0

        await group.export_invite_link()
        await group.export_invite_link()

        assert await client.get_chat_admin_invite_links_count(group.id, "me", False) == 1
        assert await client.get_chat_admin_invite_links_count(group.id, "me", True) == 2

        await client.delete_chat_admin_invite_links(group.id, "me")

        assert await client.get_chat_admin_invite_links_count(group.id, "me", False) == 1
        assert await client.get_chat_admin_invite_links_count(group.id, "me", True) == 0


@pytest.mark.asyncio
async def test_get_chat_importers() -> None:
    async with TestClient(phone_number="123456789") as client1, TestClient(phone_number="1234567890") as client2:
        group = await client1.create_group("idk", [])
        invite_link = await group.export_invite_link()
        assert invite_link.startswith("https://t.me/+")

        assert [imp async for imp in client1.get_chat_invite_link_joiners(group.id, invite_link)] == []
        assert await client1.get_chat_invite_link_joiners_count(group.id, invite_link) == 0

        await client2.join_chat(invite_link)

        importers = [imp async for imp in client1.get_chat_invite_link_joiners(group.id, invite_link)]
        assert await client1.get_chat_invite_link_joiners_count(group.id, invite_link) == 1
        assert len(importers) == 1
        assert importers[0].user.id == client2.me.id
        assert not importers[0].pending


@pytest.mark.asyncio
async def test_send_multiple_requests_on_same_invite() -> None:
    async with TestClient(phone_number="123456789") as client1, TestClient(phone_number="1234567890") as client2:
        group = await client1.create_group("idk", [])
        r = await client1.invoke(
            ExportChatInvite(
                peer=await client1.resolve_peer(group.id),
                legacy_revoke_permanent=True,
                request_needed=True,
            )
        )
        invite_link = r.link

        assert [req async for req in client1.get_chat_join_requests(group.id)] == []

        for _ in range(10):
            with pytest.raises(InviteRequestSent):
                await client2.join_chat(invite_link)

        assert len([req async for req in client1.get_chat_join_requests(group.id)]) == 1
        joiner_id = [req async for req in client1.get_chat_join_requests(group.id)][0].user.id
        assert joiner_id == client2.me.id


@pytest.mark.asyncio
async def test_request_approve_invite() -> None:
    async with TestClient(phone_number="123456789") as client1, TestClient(phone_number="1234567890") as client2:
        group = await client1.create_group("idk", [])
        r = await client1.invoke(
            ExportChatInvite(
                peer=await client1.resolve_peer(group.id),
                legacy_revoke_permanent=True,
                request_needed=True,
            )
        )
        invite_link = r.link

        assert [req async for req in client1.get_chat_join_requests(group.id)] == []

        with pytest.raises(InviteRequestSent):
            await client2.join_chat(invite_link)

        assert len([req async for req in client1.get_chat_join_requests(group.id)]) == 1
        joiner_id = [req async for req in client1.get_chat_join_requests(group.id)][0].user.id
        assert joiner_id == client2.me.id

        with pytest.raises(PeerIdInvalid):
            await client2.send_message(group.id, "test message")

        assert await client1.approve_chat_join_request(group.id, joiner_id)
        assert await client2.send_message(group.id, "test message")


@pytest.mark.asyncio
async def test_request_dismiss_invite() -> None:
    async with TestClient(phone_number="123456789") as client1, TestClient(phone_number="1234567890") as client2:
        group = await client1.create_group("idk", [])
        r = await client1.invoke(
            ExportChatInvite(
                peer=await client1.resolve_peer(group.id),
                legacy_revoke_permanent=True,
                request_needed=True,
            )
        )
        invite_link = r.link

        assert [req async for req in client1.get_chat_join_requests(group.id)] == []

        with pytest.raises(InviteRequestSent):
            await client2.join_chat(invite_link)

        assert len([req async for req in client1.get_chat_join_requests(group.id)]) == 1
        joiner_id = [req async for req in client1.get_chat_join_requests(group.id)][0].user.id
        assert joiner_id == client2.me.id

        with pytest.raises(PeerIdInvalid):
            await client2.send_message(group.id, "test message")

        assert await client1.decline_chat_join_request(group.id, joiner_id)

        assert [req async for req in client1.get_chat_join_requests(group.id)] == []
        with pytest.raises(PeerIdInvalid):
            assert await client2.send_message(group.id, "test message")


@pytest.mark.asyncio
async def test_channel_invite_user(
        channel_with_clients: ChannelWithClientsFactory, client_with_auth: ClientFactory, exit_stack: AsyncExitStack,
) -> None:
    channel_id, (client1,) = await channel_with_clients(1, name="idk")
    client2 = await client_with_auth()
    await exit_stack.enter_async_context(client1)
    await exit_stack.enter_async_context(client2)
    channel = await client1.get_chat(get_channel_id(channel_id))

    invite_link = await channel.export_invite_link()
    await client2.join_chat(invite_link)
    await client2.expect_update(UpdateChannel)

    assert await client1.send_message(channel.id, "test message")
    await client1.expect_update(UpdateNewChannelMessage)
    await client2.expect_update(UpdateNewChannelMessage)
