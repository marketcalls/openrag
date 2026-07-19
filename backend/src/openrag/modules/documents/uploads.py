"""Bounded upload quarantine and content-type validation boundary."""

import asyncio
import codecs
import hashlib
import os
import tempfile
import zipfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from fastapi import UploadFile

from openrag.core.config import Settings
from openrag.core.errors import PayloadTooLarge, UnsupportedMediaType

_OCTET_STREAM = "application/octet-stream"
_TEXT_EXTENSIONS = frozenset({".txt", ".md", ".csv"})
_CANONICAL_MIME = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}
_CLAIMED_MIME = {
    ".pdf": frozenset({"application/pdf", _OCTET_STREAM}),
    ".txt": frozenset({"text/plain", _OCTET_STREAM}),
    ".md": frozenset({"text/markdown", "text/plain", _OCTET_STREAM}),
    ".csv": frozenset({"text/csv", "application/csv", "text/plain", _OCTET_STREAM}),
    ".docx": frozenset(
        {
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/zip",
            _OCTET_STREAM,
        }
    ),
    ".xlsx": frozenset(
        {
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/zip",
            _OCTET_STREAM,
        }
    ),
    ".pptx": frozenset(
        {
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "application/zip",
            _OCTET_STREAM,
        }
    ),
}
_OOXML_MARKER = {
    ".docx": "word/document.xml",
    ".xlsx": "xl/workbook.xml",
    ".pptx": "ppt/presentation.xml",
}


@dataclass(frozen=True, slots=True)
class QuarantinedUpload:
    filename: str
    mime: str
    size_bytes: int
    content_hash: str = field(repr=False)
    path: Path = field(repr=False)


def _validated_identity(upload: UploadFile) -> tuple[str, str, str]:
    filename = upload.filename or ""
    if (
        not filename
        or len(filename) > 255
        or filename != Path(filename).name
        or "/" in filename
        or "\\" in filename
        or any(ord(character) < 32 for character in filename)
    ):
        raise UnsupportedMediaType("upload filename is invalid")
    suffix = Path(filename).suffix.lower()
    if suffix not in _CANONICAL_MIME:
        raise UnsupportedMediaType("file type is not supported")
    claimed = (upload.content_type or _OCTET_STREAM).split(";", 1)[0].strip().lower()
    if claimed not in _CLAIMED_MIME[suffix]:
        raise UnsupportedMediaType("declared media type does not match file type")
    return filename, suffix, _CANONICAL_MIME[suffix]


def _validate_text(path: Path) -> None:
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    try:
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                if b"\x00" in chunk:
                    raise UnsupportedMediaType("text upload contains binary data")
                decoder.decode(chunk)
            decoder.decode(b"", final=True)
    except UnicodeDecodeError as exc:
        raise UnsupportedMediaType("text upload is not valid UTF-8") from exc


def _validate_pdf(path: Path) -> None:
    with path.open("rb") as source:
        header = source.read(8)
        source.seek(max(0, path.stat().st_size - 2048))
        trailer = source.read()
    if not header.startswith(b"%PDF-") or b"%%EOF" not in trailer:
        raise UnsupportedMediaType("PDF signature is invalid")


def _validate_archive_member(name: str) -> None:
    pure = PurePosixPath(name)
    if (
        not name
        or name.startswith("/")
        or "\\" in name
        or ".." in pure.parts
    ):
        raise UnsupportedMediaType("office upload contains an unsafe archive path")


def _validate_ooxml(path: Path, suffix: str, settings: Settings) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > settings.upload_archive_max_entries:
                raise UnsupportedMediaType("office upload exceeds archive entry limit")
            total_uncompressed = 0
            total_compressed = 0
            names: set[str] = set()
            for entry in entries:
                _validate_archive_member(entry.filename)
                if entry.flag_bits & 0x1:
                    raise UnsupportedMediaType("encrypted office uploads are not supported")
                total_uncompressed += entry.file_size
                total_compressed += entry.compress_size
                names.add(entry.filename)
                if (
                    entry.file_size
                    > max(entry.compress_size, 1)
                    * settings.upload_archive_max_ratio
                ):
                    raise UnsupportedMediaType("office upload exceeds expansion limit")
            if total_uncompressed > settings.upload_archive_max_uncompressed_mb * 1024 * 1024:
                raise UnsupportedMediaType("office upload exceeds expansion limit")
            if total_uncompressed > max(total_compressed, 1) * settings.upload_archive_max_ratio:
                raise UnsupportedMediaType("office upload exceeds expansion limit")
            if "[Content_Types].xml" not in names or _OOXML_MARKER[suffix] not in names:
                raise UnsupportedMediaType("office file signature is invalid")
    except zipfile.BadZipFile as exc:
        raise UnsupportedMediaType("office file signature is invalid") from exc


def _validate_content(path: Path, suffix: str, settings: Settings) -> None:
    if suffix in _TEXT_EXTENSIONS:
        _validate_text(path)
    elif suffix == ".pdf":
        _validate_pdf(path)
    else:
        _validate_ooxml(path, suffix, settings)


@asynccontextmanager
async def quarantine_upload(
    upload: UploadFile,
    settings: Settings,
) -> AsyncIterator[QuarantinedUpload]:
    """Stream, bound, hash, validate, yield, and always erase a request upload."""

    filename, suffix, canonical_mime = _validated_identity(upload)
    quarantine = Path(settings.upload_quarantine_dir)
    await asyncio.to_thread(quarantine.mkdir, parents=True, exist_ok=True, mode=0o700)
    await asyncio.to_thread(os.chmod, quarantine, 0o700)
    descriptor, raw_path = tempfile.mkstemp(prefix=".openrag-upload-", dir=quarantine)
    os.close(descriptor)
    path = Path(raw_path)
    os.chmod(path, 0o600)
    maximum = settings.max_upload_mb * 1024 * 1024
    chunk_size = settings.upload_stream_chunk_kb * 1024
    digest = hashlib.sha256()
    size = 0
    target = await asyncio.to_thread(path.open, "wb")
    try:
        while True:
            chunk = await upload.read(chunk_size)
            if not chunk:
                break
            size += len(chunk)
            if size > maximum:
                raise PayloadTooLarge(
                    f"file exceeds {settings.max_upload_mb} MB limit"
                )
            digest.update(chunk)
            await asyncio.to_thread(target.write, chunk)
        await asyncio.to_thread(target.flush)
        await asyncio.to_thread(os.fsync, target.fileno())
        await asyncio.to_thread(target.close)
        if size == 0:
            raise UnsupportedMediaType("upload is empty")
        await asyncio.to_thread(_validate_content, path, suffix, settings)
        yield QuarantinedUpload(
            filename=filename,
            mime=canonical_mime,
            size_bytes=size,
            content_hash=digest.hexdigest(),
            path=path,
        )
    finally:
        if not target.closed:
            await asyncio.to_thread(target.close)
        await asyncio.to_thread(path.unlink, missing_ok=True)
        await upload.close()
