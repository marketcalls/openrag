from sqlalchemy import inspect

from openrag.modules.embeddings.models import EmbeddingDeployment, EmbeddingProfile


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


def test_embedding_deployment_model_tracks_atomic_generation_cutover() -> None:
    columns = {column.name: column for column in inspect(EmbeddingDeployment).columns}

    assert {
        "id",
        "created_at",
        "profile_id",
        "generation_id",
        "status",
        "requested_by",
        "activated_by",
        "activated_at",
        "failure_code",
        "total_versions",
        "completed_versions",
        "failed_versions",
        "scan_complete",
        "scan_cursor_document_version_id",
        "lease_owner",
        "lease_token",
        "lease_expires_at",
        "attempts",
        "updated_at",
    } <= set(columns)
    assert columns["generation_id"].unique is True
    assert columns["profile_id"].nullable is False
    assert columns["requested_by"].nullable is False
