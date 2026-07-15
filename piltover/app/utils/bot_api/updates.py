from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from time import time
from typing import Any

import httpx
from loguru import logger

from piltover.app.utils.bot_api.serialize import (
    callback_query_to_bot_api, message_to_bot_api, pre_checkout_query_to_bot_api,
)
from piltover.db.models import BotPrecheckoutQuery, CallbackQuery, MessageRef, Peer, User

_UPDATE_TYPE_KEYS = {
    "message": "message",
    "callback_query": "callback_query",
    "pre_checkout_query": "pre_checkout_query",
}


@dataclass
class _BotWebhookState:
    url: str = ""
    has_custom_certificate: bool = False
    pending_update_count: int = 0
    ip_address: str | None = None
    last_error_date: int | None = None
    last_error_message: str | None = None
    max_connections: int | None = None
    allowed_updates: list[str] | None = None
    secret_token: str | None = None


@dataclass
class _BotUpdatesState:
    updates: list[dict[str, Any]] = field(default_factory=list)
    next_update_id: int = 1
    waiters: list[asyncio.Event] = field(default_factory=list)
    webhook: _BotWebhookState = field(default_factory=_BotWebhookState)
    webhook_pending: list[dict[str, Any]] = field(default_factory=list)
    polling_allowed_updates: list[str] | None = None


class BotApiUpdatesStore:
    MAX_UPDATES_PER_BOT = 10_000
    MAX_UPDATE_AGE_SECONDS = 24 * 60 * 60
    WEBHOOK_TIMEOUT_SECONDS = 10.0
    WEBHOOK_MAX_RETRIES = 3

    def __init__(self) -> None:
        self._bots: dict[int, _BotUpdatesState] = {}

    def _state(self, bot_id: int) -> _BotUpdatesState:
        if bot_id not in self._bots:
            self._bots[bot_id] = _BotUpdatesState()
        return self._bots[bot_id]

    def has_webhook(self, bot_id: int) -> bool:
        return bool(self._state(bot_id).webhook.url)

    def get_webhook_info(self, bot_id: int) -> dict[str, Any]:
        webhook = self._state(bot_id).webhook
        state = self._state(bot_id)
        pending = len(state.webhook_pending)
        if not webhook.url:
            pending = len(state.updates)
        result: dict[str, Any] = {
            "url": webhook.url,
            "has_custom_certificate": webhook.has_custom_certificate,
            "pending_update_count": pending,
        }
        if webhook.ip_address:
            result["ip_address"] = webhook.ip_address
        if webhook.last_error_date is not None:
            result["last_error_date"] = webhook.last_error_date
        if webhook.last_error_message:
            result["last_error_message"] = webhook.last_error_message
        if webhook.max_connections is not None:
            result["max_connections"] = webhook.max_connections
        if webhook.allowed_updates is not None:
            result["allowed_updates"] = webhook.allowed_updates
        return result

    def set_webhook(
            self, bot_id: int, url: str, *, drop_pending_updates: bool = False,
            allowed_updates: list[str] | None = None, max_connections: int | None = None,
            ip_address: str | None = None, secret_token: str | None = None,
    ) -> None:
        state = self._state(bot_id)
        state.webhook.url = url
        state.webhook.allowed_updates = allowed_updates
        state.webhook.max_connections = max_connections
        state.webhook.ip_address = ip_address
        state.webhook.secret_token = secret_token
        if drop_pending_updates:
            state.updates.clear()
            state.webhook_pending.clear()
            state.webhook.pending_update_count = 0

    def delete_webhook(self, bot_id: int, *, drop_pending_updates: bool = False) -> None:
        state = self._state(bot_id)
        state.webhook = _BotWebhookState()
        state.polling_allowed_updates = None
        if drop_pending_updates:
            state.updates.clear()
            state.webhook_pending.clear()

    def _prune_old_updates(self, state: _BotUpdatesState) -> None:
        now = int(time())
        state.updates = [
            update for update in state.updates
            if now - update.get("_created_at", now) < self.MAX_UPDATE_AGE_SECONDS
        ]
        if len(state.updates) > self.MAX_UPDATES_PER_BOT:
            state.updates = state.updates[-self.MAX_UPDATES_PER_BOT:]

    def _notify_waiters(self, state: _BotUpdatesState) -> None:
        waiters = state.waiters
        state.waiters = []
        for event in waiters:
            event.set()

    def _update_type(self, update: dict[str, Any]) -> str | None:
        for update_type, key in _UPDATE_TYPE_KEYS.items():
            if key in update:
                return update_type
        return None

    def _is_update_allowed(
            self, allowed: list[str] | None, update: dict[str, Any],
    ) -> bool:
        if not allowed:
            return True
        update_type = self._update_type(update)
        return update_type is not None and update_type in allowed

    def _public_update(self, update: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in update.items() if k != "_created_at"}

    async def _deliver_webhook(self, bot_id: int, url: str, update: dict[str, Any]) -> None:
        state = self._state(bot_id)
        payload = json.dumps(self._public_update(update), ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if state.webhook.secret_token:
            headers["X-Telegram-Bot-Api-Secret-Token"] = state.webhook.secret_token

        for attempt in range(self.WEBHOOK_MAX_RETRIES):
            try:
                async with httpx.AsyncClient(timeout=self.WEBHOOK_TIMEOUT_SECONDS) as client:
                    response = await client.post(url, content=payload, headers=headers)
                if response.status_code == 200:
                    state.webhook.last_error_date = None
                    state.webhook.last_error_message = None
                    if update in state.webhook_pending:
                        state.webhook_pending.remove(update)
                    return
                state.webhook.last_error_date = int(time())
                state.webhook.last_error_message = f"Wrong HTTP status: {response.status_code}"
                logger.warning(
                    "Bot API webhook for bot {} returned status {} (attempt {})",
                    bot_id, response.status_code, attempt + 1,
                )
            except Exception as exc:
                state.webhook.last_error_date = int(time())
                state.webhook.last_error_message = str(exc)
                logger.opt(exception=exc).warning(
                    "Bot API webhook delivery failed for bot {} (attempt {})", bot_id, attempt + 1,
                )

            if attempt < self.WEBHOOK_MAX_RETRIES - 1:
                await asyncio.sleep(0.5 * (2 ** attempt))

        if update not in state.webhook_pending:
            state.webhook_pending.append(update)

    async def _enqueue_update(self, bot_user: User, update_body: dict[str, Any]) -> None:
        state = self._state(bot_user.id)
        update = {
            "update_id": state.next_update_id,
            **update_body,
            "_created_at": int(time()),
        }
        state.next_update_id += 1
        self._prune_old_updates(state)

        if state.webhook.url:
            if self._is_update_allowed(state.webhook.allowed_updates, update):
                state.webhook_pending.append(update)
                asyncio.create_task(self._deliver_webhook(bot_user.id, state.webhook.url, update))
            return

        if not self._is_update_allowed(state.polling_allowed_updates, update):
            return

        state.updates.append(update)
        self._notify_waiters(state)

    async def enqueue_incoming_message(self, bot_user: User, peer: Peer, message: MessageRef) -> None:
        await self._enqueue_update(bot_user, {
            "message": await message_to_bot_api(bot_user, peer, message),
        })

    async def enqueue_callback_query(self, bot_user: User, query: CallbackQuery) -> None:
        await self._enqueue_update(bot_user, {
            "callback_query": await callback_query_to_bot_api(bot_user, query),
        })

    async def enqueue_pre_checkout_query(self, bot_user: User, query: BotPrecheckoutQuery) -> None:
        await self._enqueue_update(bot_user, {
            "pre_checkout_query": await pre_checkout_query_to_bot_api(query),
        })

    async def get_updates(
            self, bot_id: int, *, offset: int | None = None, limit: int = 100, timeout: int = 0,
            allowed_updates: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        if self.has_webhook(bot_id):
            raise _BotApiConflict("can't use getUpdates while webhook is active")

        state = self._state(bot_id)
        if allowed_updates is not None:
            state.polling_allowed_updates = allowed_updates
        limit = max(1, min(limit, 100))

        if offset is not None:
            state.updates = [update for update in state.updates if update["update_id"] >= offset]

        if not state.updates and timeout > 0:
            event = asyncio.Event()
            state.waiters.append(event)
            try:
                await asyncio.wait_for(event.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                pass
            finally:
                if event in state.waiters:
                    state.waiters.remove(event)

        pending = state.updates[:limit]
        state.updates = state.updates[limit:]
        return [self._public_update(update) for update in pending]


class _BotApiConflict(Exception):
    pass


bot_api_updates = BotApiUpdatesStore()