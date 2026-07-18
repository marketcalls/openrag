import io

import pytest
from docx import Document as DocxBuilder

from openrag.modules.documents.pipeline import IngestFailure, parse_bytes


def build_docx() -> bytes:
    document = DocxBuilder()
    document.add_heading("Flux Capacitor Manual", level=1)
    document.add_paragraph("The flux capacitor requires 1.21 gigawatts of power.")
    document.add_heading("Billing", level=1)
    document.add_paragraph("Invoice 0231 covers the plutonium delivery.")
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def test_parse_docx_extracts_blocks_with_kinds() -> None:
    blocks = parse_bytes(build_docx(), "manual.docx")
    text = " ".join(block.text for block in blocks)

    assert "1.21 gigawatts" in text
    assert "Invoice 0231" in text
    assert any(block.kind == "heading" for block in blocks)
    assert all(block.page >= 1 for block in blocks)


def test_parse_txt_fast_path() -> None:
    blocks = parse_bytes(b"para one\n\npara two", "notes.txt")

    assert [block.text for block in blocks] == ["para one", "para two"]
    assert all(block.kind == "text" and block.page == 1 for block in blocks)


def test_empty_file_fails_with_reason() -> None:
    with pytest.raises(IngestFailure, match="empty"):
        parse_bytes(b"", "empty.txt")


def test_unsupported_format_fails_with_reason() -> None:
    with pytest.raises(IngestFailure):
        parse_bytes(b"\x00\x01garbage", "weird.xyz")
