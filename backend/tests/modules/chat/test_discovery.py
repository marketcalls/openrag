from datetime import datetime
from uuid import uuid4

import pytest

from openrag.modules.chat.models import Chat
from openrag.modules.chat.service import (
    _decode_chat_cursor,
    _encode_chat_cursor,
    derive_chat_title,
)


def test_chat_title_is_normalized_and_bounded() -> None:
    assert derive_chat_title("  Build\n a   revenue dashboard ") == ("Build a revenue dashboard")
    title = derive_chat_title("word " * 40)
    assert len(title) <= 80
    assert title.endswith("…")


def test_chat_cursor_round_trips_ordering_identity() -> None:
    updated_at = datetime(2026, 7, 20, 12, 30, 0)
    chat = Chat(
        id=uuid4(),
        org_id=uuid4(),
        workspace_id=uuid4(),
        user_id=uuid4(),
        updated_at=updated_at,
    )

    assert _decode_chat_cursor(_encode_chat_cursor(chat)) == (
        updated_at,
        chat.id,
    )
    with pytest.raises(ValueError, match="cursor"):
        _decode_chat_cursor("not-a-cursor")
