import io
import zipfile
from pathlib import Path

import pytest
from starlette.datastructures import Headers, UploadFile

from openrag.core.config import Settings
from openrag.core.errors import PayloadTooLarge, UnsupportedMediaType
from openrag.modules.documents.uploads import quarantine_upload


def upload(filename: str, content: bytes, content_type: str) -> UploadFile:
    return UploadFile(
        file=io.BytesIO(content),
        filename=filename,
        headers=Headers({"content-type": content_type}),
    )


def ooxml(*entries: tuple[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", b"<Types/>")
        for name, value in entries:
            archive.writestr(name, value)
    return buffer.getvalue()


def settings(tmp_path: Path, *, max_upload_mb: int = 1) -> Settings:
    return Settings(
        _env_file=None,
        upload_quarantine_dir=str(tmp_path),
        max_upload_mb=max_upload_mb,
    )


async def test_text_upload_streams_hashes_and_cleans_quarantine(
    tmp_path: Path,
) -> None:
    source = upload("manual.txt", b"safe instructions", "text/plain")

    async with quarantine_upload(source, settings(tmp_path)) as quarantined:
        assert quarantined.filename == "manual.txt"
        assert quarantined.mime == "text/plain"
        assert quarantined.size_bytes == len(b"safe instructions")
        assert len(quarantined.content_hash) == 64
        assert quarantined.path.read_bytes() == b"safe instructions"
        assert quarantined.path.stat().st_mode & 0o777 == 0o600

    assert list(tmp_path.iterdir()) == []


async def test_upload_limit_is_enforced_while_streaming(tmp_path: Path) -> None:
    source = upload(
        "large.txt",
        b"x" * ((1024 * 1024) + 1),
        "text/plain",
    )

    with pytest.raises(PayloadTooLarge, match="1 MB"):
        async with quarantine_upload(source, settings(tmp_path)):
            raise AssertionError("oversized upload must not yield")

    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("filename", "content", "content_type"),
    [
        ("fake.pdf", b"not a pdf", "application/pdf"),
        ("manual.txt", b"hello\x00world", "text/plain"),
        ("manual.txt", b"\xff\xfe", "text/plain"),
        ("manual.exe", b"MZ", "application/octet-stream"),
        ("../manual.txt", b"safe", "text/plain"),
        ("manual.pdf", b"%PDF-1.7\n%%EOF", "text/plain"),
    ],
)
async def test_invalid_type_magic_or_filename_is_rejected(
    tmp_path: Path,
    filename: str,
    content: bytes,
    content_type: str,
) -> None:
    with pytest.raises(UnsupportedMediaType):
        async with quarantine_upload(
            upload(filename, content, content_type),
            settings(tmp_path),
        ):
            raise AssertionError("invalid upload must not yield")

    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("filename", "content_type", "marker", "canonical_mime"),
    [
        (
            "manual.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "word/document.xml",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        (
            "workbook.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "xl/workbook.xml",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
        (
            "briefing.pptx",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "ppt/presentation.xml",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ),
    ],
)
async def test_bounded_ooxml_formats_are_accepted(
    tmp_path: Path,
    filename: str,
    content_type: str,
    marker: str,
    canonical_mime: str,
) -> None:
    source = upload(filename, ooxml((marker, b"<document/>")), content_type)

    async with quarantine_upload(source, settings(tmp_path)) as quarantined:
        assert quarantined.mime == canonical_mime


async def test_ooxml_path_traversal_is_rejected(tmp_path: Path) -> None:
    source = upload(
        "manual.docx",
        ooxml(("word/document.xml", b"<document/>"), ("../escape", b"bad")),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    with pytest.raises(UnsupportedMediaType, match="unsafe archive"):
        async with quarantine_upload(source, settings(tmp_path)):
            raise AssertionError("unsafe archive must not yield")

    assert not (tmp_path.parent / "escape").exists()


async def test_ooxml_expansion_ratio_is_bounded(tmp_path: Path) -> None:
    source = upload(
        "manual.docx",
        ooxml(("word/document.xml", b"x" * 100_000)),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    configured = Settings(
        _env_file=None,
        upload_quarantine_dir=str(tmp_path),
        upload_archive_max_ratio=2,
    )

    with pytest.raises(UnsupportedMediaType, match="expansion limit"):
        async with quarantine_upload(source, configured):
            raise AssertionError("archive bomb must not yield")
