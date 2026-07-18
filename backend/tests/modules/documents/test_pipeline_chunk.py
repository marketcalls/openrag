from openrag.modules.documents.pipeline import Chunk, PageBlock, chunk_blocks


def block(text: str, page: int = 1, kind: str = "text") -> PageBlock:
    return PageBlock(page=page, text=text, kind=kind)


def test_empty_input_returns_empty() -> None:
    assert chunk_blocks([]) == []


def test_single_short_block_is_one_chunk() -> None:
    chunks = chunk_blocks([block("hello world", page=3)])

    assert chunks == [Chunk(text="hello world", page=3, chunk_index=0)]


def test_long_text_splits_with_overlap() -> None:
    words = " ".join(f"word{index}" for index in range(1200))

    chunks = chunk_blocks(
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

    chunks = chunk_blocks(
        [block("intro"), block(table, kind="table"), block("outro")]
    )

    assert len([chunk for chunk in chunks if chunk.text == table]) == 1


def test_heading_starts_new_chunk_after_substantial_buffer() -> None:
    chunks = chunk_blocks(
        [
            block("x" * 1200),
            block("Chapter Two", kind="heading"),
            block("more text"),
        ]
    )

    assert len(chunks) == 2
    assert chunks[1].text.startswith("Chapter Two")


def test_indices_are_sequential_and_pages_are_tracked() -> None:
    chunks = chunk_blocks(
        [
            block("a", page=1),
            block("b" * 3000, page=2, kind="table"),
            block("c", page=3),
        ]
    )

    assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))
    assert chunks[0].page == 1
