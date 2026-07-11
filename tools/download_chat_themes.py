import argparse
import json
import shutil
from asyncio import get_event_loop
from pathlib import Path

from typing import cast

from loguru import logger
from pyrogram import Client

from pyrogram.raw.core import TLObject
from pyrogram.raw.functions.account import GetChatThemes
from pyrogram.raw.types import Theme, ThemeSettings, WallPaper
from pyrogram.raw.types.account import Themes

from download_utils import (
    DEFAULT_SESSION_BY_SCRIPT, DownloadClientArgs, add_download_client_args, download_client, download_document,
)


async def extract_chat_themes(client: Client, out_dir: Path) -> None:
    themes: Themes = await client.invoke(GetChatThemes(hash=0))
    logger.info(f"Got {len(themes.themes)} themes")
    for idx, theme in enumerate(cast(list[Theme], themes.themes)):
        logger.info(f"Downloading theme \"{theme.title}\"")
        for settings in cast(list[ThemeSettings], theme.settings):
            if settings.wallpaper is not None:
                await download_document(client, idx, cast(WallPaper, settings.wallpaper).document, out_dir)

        with open(out_dir / f"{idx}.json", "w") as f:
            json.dump(theme, f, indent=4, default=TLObject.default, ensure_ascii=False)


async def main() -> None:
    parser = argparse.ArgumentParser()
    add_download_client_args(parser, default_session=DEFAULT_SESSION_BY_SCRIPT["chat_themes"])
    args = parser.parse_args(namespace=DownloadClientArgs())

    out_dir = args.data_dir / "chat_themes"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    with open(out_dir / ".gitignore", "w") as f:
        f.write("*\n")

    async with download_client(args, cached_media=True) as client:
        await extract_chat_themes(client, out_dir)


if __name__ == "__main__":
    get_event_loop().run_until_complete(main())
