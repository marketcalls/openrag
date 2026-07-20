from openrag.modules.models.models import Model


def test_utility_model_is_measured_and_globally_unique() -> None:
    table = Model.__table__
    assert table.c.is_utility.nullable is False
    assert str(table.c.is_utility.server_default.arg) == "false"  # type: ignore[union-attr]

    constraints = {
        constraint.name: str(constraint.sqltext)
        for constraint in table.constraints
        if hasattr(constraint, "sqltext")
    }
    utility_check = constraints["ck_models_utility_measured"]
    assert "enabled" in utility_check
    assert "probe_status = 'passed'" in utility_check
    assert "supports_chat_completion" in utility_check
    assert "supports_streaming" in utility_check

    utility_index = next(
        index for index in table.indexes if index.name == "uq_models_single_utility"
    )
    assert utility_index.unique is True
    assert str(utility_index.dialect_options["postgresql"]["where"]) == "is_utility"
