import argparse
import json
import shutil
from asyncio import get_event_loop
from pathlib import Path


from loguru import logger
from pyrogram import Client

from download_utils import DEFAULT_SESSION_BY_SCRIPT, DownloadClientArgs, add_download_client_args, download_client

from pyrogram.raw.core import TLObject
from pyrogram.raw.functions.langpack import GetLanguages, GetLangPack
from pyrogram.raw.types import LangPackLanguage, LangPackDifference


class ArgsNamespace(DownloadClientArgs):
    platform: str


async def extract_languages(client: Client, out_dir: Path, platform: str) -> None:
    platform_dir = out_dir / platform

    languages: list[LangPackLanguage] = await client.invoke(GetLanguages(lang_pack=platform))
    logger.info(f"Got {len(languages)} languages for platform \"{platform}\"")
    for language in languages:
        lang_dir = platform_dir / language.lang_code
        lang_dir.mkdir(parents=True, exist_ok=True)

        with open(lang_dir / "info.json", "w") as f:
            json.dump(language, f, indent=4, default=TLObject.default, ensure_ascii=False)

        diff: LangPackDifference = await client.invoke(GetLangPack(lang_pack=platform, lang_code=language.lang_code))
        logger.info(
            f"Got {len(diff.strings)} strings for language \"{language.lang_code}\" for platform \"{platform}\""
        )
        with open(lang_dir / "strings.json", "w") as f:
            json.dump(diff.strings, f, indent=4, default=TLObject.default, ensure_ascii=False)


async def main() -> None:
    parser = argparse.ArgumentParser()
    add_download_client_args(parser, default_session=DEFAULT_SESSION_BY_SCRIPT["languages"])
    parser.add_argument("--platform", type=str, help="Platform (e.g. android, tdesktop)", default="android")
    args = parser.parse_args(namespace=ArgsNamespace())

    out_dir = args.data_dir / "languages"
    platform_dir = out_dir / args.platform
    if platform_dir.exists():
        shutil.rmtree(out_dir)

    platform_dir.mkdir(parents=True, exist_ok=True)

    with open(out_dir / ".gitignore", "w") as f:
        f.write("*\n")

    async with download_client(args) as client:
        await extract_languages(client, out_dir, args.platform)


if __name__ == "__main__":
    get_event_loop().run_until_complete(main())
