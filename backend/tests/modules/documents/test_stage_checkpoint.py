from uuid import UUID

import pytest

from openrag.modules.documents.stages import parse_stage_checkpoint


def test_reindex_checkpoint_is_a_distinct_bounded_pipeline() -> None:
    generation = UUID("566e45b0-051c-4d86-87b3-6a528c7935c2")

    checkpoint = parse_stage_checkpoint(f"embed:reindex:1:{generation.hex}")

    assert checkpoint.pipeline_kind == "reindex"
    assert checkpoint.authority_generation_id == generation
    assert checkpoint.for_stage("authority_upsert") == (
        f"authority_upsert:reindex:1:{generation.hex}"
    )


@pytest.mark.parametrize("kind", ["migration", "legacy", "re-index"])
def test_checkpoint_rejects_unregistered_pipeline_kinds(kind: str) -> None:
    with pytest.raises(ValueError, match="stage_checkpoint_invalid"):
        parse_stage_checkpoint(f"parse:{kind}:1:{'a' * 32}")
