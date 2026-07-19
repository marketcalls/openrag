from sqlalchemy import inspect

from openrag.modules.embeddings.models import EmbeddingProfile


def test_embedding_profile_model_exposes_immutable_identity_fields() -> None:
    columns = {column.name: column for column in inspect(EmbeddingProfile).columns}

    assert {
        "id",
        "created_at",
        "name",
        "provider_kind",
        "model_name",
        "dimension",
        "max_input_tokens",
        "batch_size",
        "config_digest",
        "enabled",
        "created_by",
    } <= set(columns)
    assert columns["config_digest"].unique is True
    assert columns["created_by"].nullable is False
