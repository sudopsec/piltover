import argparse
import json
import shutil
from asyncio import get_event_loop
from pathlib import Path

from typing import cast

from loguru import logger
from pyrogram import Client
from pyrogram.raw.core import TLObject
from pyrogram.raw.functions.messages import GetStickerSet
from pyrogram.raw.types import InputStickerSetID, InputStickerSetAnimatedEmoji, \
    InputStickerSetDice, InputStickerSetAnimatedEmojiAnimations, InputStickerSetEmojiGenericAnimations, \
    InputStickerSetEmojiDefaultStatuses, InputStickerSetEmojiDefaultTopicIcons, Document, InputStickerSetShortName
from pyrogram.raw.types.messages import StickerSet as MessagesStickerSet

from download_utils import (
    DEFAULT_SESSION_BY_SCRIPT, DownloadClientArgs, add_download_client_args, download_client, download_document,
)

InputStickerSet = InputStickerSetID | InputStickerSetAnimatedEmoji | InputStickerSetDice \
                  | InputStickerSetAnimatedEmojiAnimations | InputStickerSetEmojiGenericAnimations \
                  | InputStickerSetEmojiDefaultStatuses | InputStickerSetEmojiDefaultTopicIcons \
                  | InputStickerSetShortName


to_download = [
    ("animated_emoji", InputStickerSetAnimatedEmoji()),
    ("dice_basketball", InputStickerSetDice(emoticon="\U0001F3C0")),
    ("dice_die", InputStickerSetDice(emoticon="\U0001F3B2")),
    ("dice_target", InputStickerSetDice(emoticon="\U0001F3AF")),
    ("dice_football1", InputStickerSetDice(emoticon="\u26bd")),
    ("dice_football2", InputStickerSetDice(emoticon="\u26bd\ufe0f")),
    ("dice_slotmachine", InputStickerSetDice(emoticon="\U0001F3B0")),
    ("dice_bowling", InputStickerSetDice(emoticon="\U0001F3B3")),
    ("emoji_animations", InputStickerSetAnimatedEmojiAnimations()),
    ("generic_animations", InputStickerSetEmojiGenericAnimations()),
    ("user_statuses", InputStickerSetEmojiDefaultStatuses()),
    ("topic_icons", InputStickerSetEmojiDefaultTopicIcons()),
    ("emoji_categories", InputStickerSetShortName(short_name="EmojiCategories")),
    ("restricted_emoji", InputStickerSetShortName(short_name="RestrictedEmoji")),
]


class ArgsNamespace(DownloadClientArgs):
    clean: bool


async def download_stickerset(client: Client, out_dir: Path, stickerset: InputStickerSet, set_type: str) -> None:
    info_file = out_dir / "set.json"

    existing_hash = 0
    if info_file.exists():
        with open(info_file) as f:
            set_info = json.load(f)
        existing_hash = set_info["set"]["hash"]

    sticker_set: MessagesStickerSet = await client.invoke(GetStickerSet(stickerset=stickerset, hash=0))

    if existing_hash == sticker_set.set.hash:
        logger.success(f"Stickerset {set_type!r} is already up-to-date")
        return

    logger.success(f"Got {len(sticker_set.documents)} stickers for stickerset {set_type!r}")
    for idx, doc in enumerate(cast(list[Document], sticker_set.documents)):
        logger.info(f"Downloading sticker {doc.id}")
        await download_document(client, idx, doc, out_dir)

    with open(info_file, "w") as f:
        json.dump(sticker_set, f, indent=4, default=TLObject.default, ensure_ascii=False)


async def main() -> None:
    parser = argparse.ArgumentParser()
    add_download_client_args(parser, default_session=DEFAULT_SESSION_BY_SCRIPT["stickersets"])
    parser.add_argument("--clean", action="store_true", help="Clean target directory before downloading")
    args = parser.parse_args(namespace=ArgsNamespace())

    out_dir = args.data_dir / "stickersets"
    if args.clean and out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    with open(out_dir / ".gitignore", "w") as f:
        f.write("*\n")

    async with download_client(args, cached_media=True) as client:
        for set_type, input_set in to_download:
            set_out_dir = out_dir / set_type
            set_out_dir.mkdir(parents=True, exist_ok=True)
            await download_stickerset(client, set_out_dir, input_set, set_type)


if __name__ == "__main__":
    get_event_loop().run_until_complete(main())
