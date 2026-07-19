from openrag.modules.documents.pipeline import PageBlock, chunk_blocks


def block(text: str, page: int = 1, kind: str = "text") -> PageBlock:
    return PageBlock(page=page, text=text, kind=kind)


def test_empty_input_returns_empty() -> None:
    chunks, spans = chunk_blocks([])

    assert chunks == []
    assert spans == []


def test_single_short_block_is_one_chunk() -> None:
    chunks, spans = chunk_blocks([block("hello world", page=3)])

    assert len(chunks) == 1
    assert (chunks[0].text, chunks[0].page_start, chunks[0].page_end) == (
        "hello world",
        3,
        3,
    )
    assert [(span.page_number, span.text) for span in spans] == [(3, "hello world")]


def test_long_text_splits_with_overlap() -> None:
    words = " ".join(f"word{index}" for index in range(1200))

    chunks, _ = chunk_blocks(
        [block(words)],
        target_chars=2000,
        overlap_ratio=0.15,
    )

    assert len(chunks) >= 3
    assert all(len(chunk.text) <= 2600 for chunk in chunks)
    tail_words = chunks[0].text.split()[-10:]
    assert " ".join(tail_words) in chunks[1].text


def test_table_is_kept_whole_when_oversized() -> None:
    table = ("| a | b |\n" * 400).strip()

    chunks, _ = chunk_blocks(
        [block("intro"), block(table, kind="table"), block("outro")]
    )

    assert len([chunk for chunk in chunks if chunk.text == table]) == 1


def test_heading_starts_new_chunk_after_substantial_buffer() -> None:
    chunks, _ = chunk_blocks(
        [
            block("x" * 1200),
            block("Chapter Two", kind="heading"),
            block("more text"),
        ]
    )

    assert len(chunks) == 2
    assert chunks[1].text.startswith("Chapter Two")


def test_indices_are_sequential_and_pages_are_tracked() -> None:
    chunks, _ = chunk_blocks(
        [
            block("a", page=1),
            block("b" * 3000, page=2, kind="table"),
            block("c", page=3),
        ]
    )

    assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))
    assert chunks[0].page_start == 1


def test_multi_page_chunk_becomes_exact_page_local_evidence_spans() -> None:
    chunks, spans = chunk_blocks(
        [
            PageBlock(
                page=1,
                text="page one evidence",
                kind="text",
                section_path=("Scope",),
            ),
            PageBlock(
                page=2,
                text="page two evidence",
                kind="text",
                section_path=("Scope",),
            ),
        ],
        target_chars=2000,
    )

    assert len(chunks) == 1
    assert (chunks[0].page_start, chunks[0].page_end) == (1, 2)
    assert [(span.page_number, span.text) for span in spans] == [
        (1, "page one evidence"),
        (2, "page two evidence"),
    ]
    encoded = chunks[0].text.encode("utf-8")
    for span in spans:
        assert encoded[span.artifact_byte_start : span.artifact_byte_end].decode() == span.text


def test_evidence_spans_split_on_section_even_within_one_page() -> None:
    chunks, spans = chunk_blocks(
        [
            PageBlock(1, "first", "text", section_path=("Overview",)),
            PageBlock(1, "second", "text", section_path=("Controls",)),
        ]
    )

    assert len(chunks) == 1
    assert [(span.section_path, span.text) for span in spans] == [
        (("Overview",), "first"),
        (("Controls",), "second"),
    ]


def test_unicode_evidence_uses_utf8_byte_ranges() -> None:
    chunks, spans = chunk_blocks(
        [
            PageBlock(1, "café", "text"),
            PageBlock(2, "安全", "text"),
        ]
    )

    encoded = chunks[0].text.encode("utf-8")
    assert [
        encoded[span.artifact_byte_start : span.artifact_byte_end].decode("utf-8")
        for span in spans
    ] == ["café", "安全"]
