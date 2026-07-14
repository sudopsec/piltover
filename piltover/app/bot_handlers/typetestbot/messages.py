"""Backward-compatible alias — use catalog module."""

from piltover.app.bot_handlers.typetestbot.catalog import page_catalog
from piltover.app.bot_handlers.typetestbot.catalog.registry import CATALOG_HANDLERS

page_messages = page_catalog
MESSAGE_DEMO_HANDLERS = CATALOG_HANDLERS