from __future__ import annotations

import hashlib
from typing import Any
from uuid import uuid4

from piltover.app.utils.utils import process_reply_markup
from piltover.context import request_ctx
from piltover.db.enums import FileType, MediaType
from piltover.db.models import File, MessageMedia, UploadingFile, UploadingFilePart, User
from piltover.exceptions import ErrorRpc
from piltover.tl import DocumentAttributeFilename

UploadedFile = tuple[str, bytes, str | None]


async def resolve_bot_api_file(
        bot_user: User, file_id: str | int | None, uploaded: UploadedFile | None,
        *, default_mime: str, file_type: FileType,
) -> File:
    if uploaded is not None:
        return await _store_uploaded_file(bot_user, uploaded, default_mime=default_mime, file_type=file_type)

    if file_id is None:
        raise ErrorRpc(error_code=400, error_message="Bad Request: file is required")

    file_id_int = int(file_id)
    file = await File.get_or_none(id=file_id_int)
    if file is None:
        raise ErrorRpc(error_code=400, error_message="Bad Request: invalid file_id")
    return file


async def _store_uploaded_file(
        bot_user: User, uploaded: UploadedFile, *, default_mime: str, file_type: FileType,
) -> File:
    _filename, content, content_type = uploaded
    if not content:
        raise ErrorRpc(error_code=400, error_message="Bad Request: empty file")

    storage = request_ctx.get().storage
    upload_id = str(uuid4())
    uploading = await UploadingFile.create(user=bot_user, file_id=upload_id, mime=content_type or default_mime)
    await UploadingFilePart.create(file=uploading, part_id=0, size=len(content))
    await storage.save_part(uploading.physical_id, 0, content, True)

    attributes = []
    if _filename:
        attributes.append(DocumentAttributeFilename(file_name=_filename))

    return await uploading.finalize_upload(
        storage, content_type or default_mime, attributes, file_type=file_type,
    )


async def make_message_media(file: File, *, media_type: MediaType | None = None) -> MessageMedia:
    if media_type is None:
        media_type = MediaType.PHOTO if file.type is FileType.PHOTO else MediaType.DOCUMENT
    return await MessageMedia.create(type=media_type, file=file)


def file_unique_id(file: File) -> str:
    digest = hashlib.sha256(f"{file.id}:{file.physical_id}".encode()).hexdigest()
    return digest[:32]


def file_to_bot_api(file: File) -> dict[str, Any]:
    result: dict[str, Any] = {
        "file_id": str(file.id),
        "file_unique_id": file_unique_id(file),
        "file_size": file.size,
    }
    if file.width and file.height:
        result["width"] = file.width
        result["height"] = file.height
    if file.duration:
        result["duration"] = int(file.duration) if file.type is not FileType.DOCUMENT_AUDIO else file.duration
    if file.filename:
        result["file_name"] = file.filename
    if file.mime_type:
        result["mime_type"] = file.mime_type
    if file.performer:
        result["performer"] = file.performer
    if file.title:
        result["title"] = file.title
    return result


async def serialize_media_field(file: File, media: MessageMedia) -> dict[str, Any] | None:
    from piltover.db.enums import FileType as FT

    base = [file_to_bot_api(file)]
    field: dict[str, Any]

    if file.type is FT.PHOTO:
        field = {"photo": base}
    elif file.type is FT.DOCUMENT_GIF:
        field = {"animation": base[0]}
    elif file.type is FT.DOCUMENT_VIDEO:
        field = {"video": base[0]}
    elif file.type is FT.DOCUMENT_AUDIO:
        field = {"audio": base[0]}
    elif file.type is FT.DOCUMENT_VOICE:
        field = {"voice": base[0]}
    elif file.type is FT.DOCUMENT_VIDEO_NOTE:
        field = {"video_note": base[0]}
    elif file.type is FT.DOCUMENT_STICKER:
        field = {"sticker": base[0]}
    else:
        field = {"document": base[0]}

    if media.spoiler:
        field["has_media_spoiler"] = True
    return field


async def process_outgoing_reply_markup(bot_user: User, params: dict[str, Any]) -> bytes | None:
    from piltover.app.utils.bot_api.markup import parse_reply_markup

    raw = params.get("reply_markup")
    if raw is None:
        return None
    markup = parse_reply_markup(raw)
    processed = await process_reply_markup(markup, bot_user)
    return processed.write() if processed is not None else None


def pick_uploaded_file(params: dict[str, Any], *field_names: str) -> UploadedFile | None:
    files = params.get("_files")
    if not isinstance(files, dict):
        return None
    for name in field_names:
        if name in files:
            return files[name]
    return None