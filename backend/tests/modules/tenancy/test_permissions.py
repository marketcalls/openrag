from openrag.modules.tenancy.permissions import (
    ALL_PERMISSIONS,
    BUILTIN_ROLE_TEMPLATES,
)


def test_builtin_templates_only_use_known_permissions() -> None:
    for template in BUILTIN_ROLE_TEMPLATES.values():
        assert template.permissions
        assert template.permissions <= ALL_PERMISSIONS


def test_platform_superadmin_is_not_an_organization_role() -> None:
    assert "superadmin" not in BUILTIN_ROLE_TEMPLATES
    assert {"administrator", "hse_manager", "engineer", "user"} == set(
        BUILTIN_ROLE_TEMPLATES
    )
