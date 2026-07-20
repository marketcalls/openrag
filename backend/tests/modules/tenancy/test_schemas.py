import pytest
from pydantic import ValidationError

from openrag.modules.tenancy.schemas import WorkspacePatch


def test_workspace_patch_accepts_exactly_one_configuration_change() -> None:
    assert WorkspacePatch(enrichment_enabled=True).enrichment_enabled is True
    assert WorkspacePatch(default_model_id=None).default_model_id is None

    with pytest.raises(ValidationError, match="exactly one workspace setting"):
        WorkspacePatch()
    with pytest.raises(ValidationError, match="exactly one workspace setting"):
        WorkspacePatch(default_model_id=None, enrichment_enabled=True)
