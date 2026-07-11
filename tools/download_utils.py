import argparse
import functools
import inspect
import sys
from contextlib import asynccontextmanager
from hashlib import sha256
from os import stat
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
from types import SimpleNamespace
from typing import AsyncGenerator, AsyncIterator, Callable, cast

from loguru import logger
from pyrogram import Client, StopTransmission, raw
from pyrogram.client import log
from pyrogram.crypto import aes
from pyrogram.errors import FloodWait

try:
    from pyrogram.errors import CDNFileHashMismatch
except ImportError:
    from pyrogram.errors import SecurityError

    class CDNFileHashMismatch(SecurityError):
        @classmethod
        def check(cls, condition: bool, message: str) -> None:
            if not condition:
                raise cls(message)

try:
    from pyrogram.errors import FloodPremiumWait
except ImportError:
    FloodPremiumWait = FloodWait

try:
    from pyrogram.errors import VolumeLocNotFound
except ImportError:
    from pyrogram.errors.exceptions.bad_request_400 import VolumeLocNotFound
from pyrogram.file_id import ThumbnailSource, FileType, FileId
from pyrogram.raw.functions.auth import ExportAuthorization, ImportAuthorization
from pyrogram.raw.functions.upload import GetCdnFile, GetCdnFileHashes, GetFile, ReuploadCdnFile
from pyrogram.raw.types import Document, InputDocumentFileLocation, InputPhotoFileLocation, PhotoPathSize, PhotoSize
from pyrogram.raw.types.upload import File, FileCdnRedirect
from pyrogram.session import Auth, Session


class DownloadClientArgs(SimpleNamespace):
    api_id: int
    api_hash: str
    data_dir: Path
    session_name: str


# Use a separate Pyrogram session per downloader so parallel runs do not fight over
# auth.importAuthorization / tmp sessions on the same account connection.
DEFAULT_SESSION_BY_SCRIPT = {
    "reactions": "tg_reactions",
    "languages": "tg_languages",
    "chat_themes": "tg_chat_themes",
    "stickersets": "tg_stickersets",
    "emoji_groups": "tg_emoji_groups",
    "peer_colors": "tg_peer_colors",
}


def add_download_client_args(parser: argparse.ArgumentParser, *, default_session: str = "telegram") -> None:
    parser.add_argument("--api-id", required=False, type=int, help="Telegram api id")
    parser.add_argument("--api-hash", required=False, type=str, help="Telegram api hash")
    parser.add_argument(
        "--data-dir", type=Path,
        help="Path to data directory where downloaded assets and session files are stored",
        default=Path("./data").resolve(),
    )
    parser.add_argument(
        "--session-name", type=str, default=default_session,
        help=(
            "Pyrogram session name (file <workdir>/<name>.session). "
            "Use different names for parallel download scripts or separate accounts."
        ),
    )


def make_download_client(args: DownloadClientArgs, *, cached_media: bool = False) -> Client:
    client_cls = ClientCachedMediaSessions if cached_media else Client
    return client_cls(
        name=args.session_name,
        api_id=args.api_id,
        api_hash=args.api_hash,
        workdir=str(args.data_dir / "secrets"),
    )


@asynccontextmanager
async def download_client(
        args: DownloadClientArgs, *, cached_media: bool = False,
) -> AsyncIterator[Client]:
    async with make_download_client(args, cached_media=cached_media) as client:
        yield client


class ClientCachedMediaSessions(Client):
    """Pyrogram Client that prefers CDN downloads and reuses media DC sessions."""

    async def _get_media_session(self, dc_id: int) -> Session:
        async with self.media_sessions_lock:
            if dc_id not in self.media_sessions:
                session = Session(
                    self,
                    dc_id,
                    await Auth(self, dc_id, await self.storage.test_mode()).create()
                    if dc_id != await self.storage.dc_id()
                    else await self.storage.auth_key(),
                    await self.storage.test_mode(),
                    is_media=True,
                )
                self.media_sessions[dc_id] = session
                await session.start()
                if dc_id != await self.storage.dc_id():
                    exported_auth = await self.invoke(ExportAuthorization(dc_id=dc_id))
                    await session.invoke(
                        ImportAuthorization(id=exported_auth.id, bytes=exported_auth.bytes),
                    )
            return self.media_sessions[dc_id]

    async def _get_cdn_session(self, dc_id: int) -> Session:
        session = Session(
            self,
            dc_id,
            await Auth(self, dc_id, await self.storage.test_mode()).create(),
            await self.storage.test_mode(),
            is_media=True,
            is_cdn=True,
        )
        await session.start()
        return session

    async def get_file(
        self,
        file_id: FileId,
        file_size: int = 0,
        limit: int = 0,
        offset: int = 0,
        progress: Callable | None = None,
        progress_args: tuple = (),
    ) -> AsyncGenerator[bytes, None] | None:
        async with self.get_file_semaphore:
            file_type = file_id.file_type

            if file_type == FileType.CHAT_PHOTO:
                raise RuntimeError("Chat photos are not supported!")
            if file_type == FileType.PHOTO:
                location = InputPhotoFileLocation(
                    id=file_id.media_id,
                    access_hash=file_id.access_hash,
                    file_reference=file_id.file_reference,
                    thumb_size=file_id.thumbnail_size,
                )
            else:
                location = InputDocumentFileLocation(
                    id=file_id.media_id,
                    access_hash=file_id.access_hash,
                    file_reference=file_id.file_reference,
                    thumb_size=file_id.thumbnail_size,
                )

            current = 0
            total = abs(limit) or (1 << 31) - 1
            chunk_size = 1024 * 1024
            offset_bytes = abs(offset) * chunk_size
            dc_id = file_id.dc_id

            try:
                session = await self._get_media_session(dc_id)

                r = await session.invoke(
                    GetFile(
                        location=location,
                        offset=offset_bytes,
                        limit=chunk_size,
                        cdn_supported=True,
                    ),
                    sleep_threshold=30,
                )

                if isinstance(r, File):
                    while True:
                        chunk = cast(bytes, r.bytes)
                        yield chunk

                        current += 1
                        offset_bytes += chunk_size

                        if progress:
                            func = functools.partial(
                                progress,
                                min(offset_bytes, file_size) if file_size != 0 else offset_bytes,
                                file_size,
                                *progress_args,
                            )
                            if inspect.iscoroutinefunction(progress):
                                await func()
                            else:
                                await self.loop.run_in_executor(self.executor, func)

                        if len(chunk) < chunk_size or current >= total:
                            break

                        r = await session.invoke(
                            GetFile(
                                location=location,
                                offset=offset_bytes,
                                limit=chunk_size,
                                cdn_supported=True,
                            ),
                            sleep_threshold=30,
                        )

                elif isinstance(r, FileCdnRedirect):
                    cdn_session = await self._get_cdn_session(r.dc_id)
                    try:
                        while True:
                            r2 = await cdn_session.invoke(
                                GetCdnFile(
                                    file_token=r.file_token,
                                    offset=offset_bytes,
                                    limit=chunk_size,
                                ),
                            )

                            if isinstance(r2, raw.types.upload.CdnFileReuploadNeeded):
                                try:
                                    await session.invoke(
                                        ReuploadCdnFile(
                                            file_token=r.file_token,
                                            request_token=r2.request_token,
                                        ),
                                    )
                                except VolumeLocNotFound:
                                    break
                                continue

                            chunk = cast(bytes, r2.bytes)
                            decrypted_chunk = await self.loop.run_in_executor(
                                self.executor,
                                aes.ctr256_decrypt,
                                chunk,
                                r.encryption_key,
                                bytearray(r.encryption_iv[:-4] + (offset_bytes // 16).to_bytes(4, "big")),
                            )

                            hashes = await session.invoke(
                                GetCdnFileHashes(
                                    file_token=r.file_token,
                                    offset=offset_bytes,
                                ),
                            )

                            def _check_all_hashes() -> None:
                                for i, h in enumerate(hashes):
                                    cdn_chunk = decrypted_chunk[h.limit * i: h.limit * (i + 1)]
                                    CDNFileHashMismatch.check(
                                        h.hash == sha256(cdn_chunk).digest(),
                                        "h.hash == sha256(cdn_chunk).digest()",
                                    )

                            await self.loop.run_in_executor(self.executor, _check_all_hashes)
                            yield decrypted_chunk

                            current += 1
                            offset_bytes += chunk_size

                            if progress:
                                func = functools.partial(
                                    progress,
                                    min(offset_bytes, file_size) if file_size != 0 else offset_bytes,
                                    file_size,
                                    *progress_args,
                                )
                                if inspect.iscoroutinefunction(progress):
                                    await func()
                                else:
                                    await self.loop.run_in_executor(self.executor, func)

                            if len(chunk) < chunk_size or current >= total:
                                break
                    finally:
                        await cdn_session.stop()
            except StopTransmission:
                raise
            except (FloodWait, FloodPremiumWait):
                raise
            except Exception as e:
                log.exception(e)


def doc_to_fileid(doc: Document, thumb: PhotoSize | None = None) -> FileId:
    return FileId(
        major=FileId.MAJOR,
        minor=FileId.MINOR,
        file_type=FileType.DOCUMENT if thumb is None else FileType.THUMBNAIL,
        dc_id=doc.dc_id,
        file_reference=doc.file_reference,
        media_id=doc.id,
        access_hash=doc.access_hash,

        thumbnail_source=None if thumb is None else ThumbnailSource.THUMBNAIL,
        thumbnail_file_type=None if thumb is None else FileType.STICKER,
        thumbnail_size="" if thumb is None else thumb.type,
    )


async def download_document(client: Client, idx: int, doc: Document, out_dir: Path) -> None:
    file_dir = out_dir / "files"
    file_name = f"{doc.id}-{idx}.{doc.mime_type.split('/')[-1]}"
    file_path = file_dir / file_name

    if not file_path.exists() or stat(file_path).st_size != doc.size:
        await client.handle_download(
            (
                doc_to_fileid(doc),
                file_dir,
                file_name,
                False,
                doc.size,
                None,
                (),
            )
        )

    for thumb in doc.thumbs:
        if isinstance(thumb, PhotoPathSize):
            with open(out_dir / f"files/{doc.id}-{idx}-thumb-{thumb.type}.bin", "wb") as f:
                f.write(thumb.bytes)
        elif isinstance(thumb, PhotoSize):
            thumb_name = f"{doc.id}-{idx}-thumb-{thumb.type}.{doc.mime_type.split('/')[-1]}"
            thumb_path = file_dir / thumb_name
            if not thumb_path.exists() or stat(thumb_path).st_size != thumb.size:
                await client.handle_download(
                    (
                        doc_to_fileid(doc, thumb),
                        file_dir,
                        thumb_name,
                        False,
                        thumb.size,
                        None,
                        (),
                    )
                )
        else:
            logger.warning(f"Unknown thumb type: {thumb}")
