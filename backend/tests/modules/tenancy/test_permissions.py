from typing import get_args

from openrag.modules.tenancy.permissions import (
    ALL_PERMISSIONS,
    BUILTIN_ROLE_TEMPLATES,
    PermissionCode,
)

EXPECTED_PERMISSIONS = frozenset(
    {
        "audit.read",
        "chat.use",
        "document.approve",
        "document.read",
        "document.upload",
        "model.configure",
        "rag.evaluate",
        "role.manage",
        "user.manage",
        "workspace.manage",
        "workspace.read_all",
    }
)

EXPECTED_TEMPLATES = {
    "administrator": {
        "name": "Administrator",
        "description": "Manage organization users, roles, workspaces and knowledge.",
        "permissions": EXPECTED_PERMISSIONS,
    },
    "hse_manager": {
        "name": "HSE Manager",
        "description": "Manage and approve HSE knowledge in assigned workspaces.",
        "permissions": frozenset(
            {"chat.use", "document.read", "document.upload", "document.approve"}
        ),
    },
    "engineer": {
        "name": "Engineer",
        "description": "Use chat and contribute knowledge in assigned workspaces.",
        "permissions": frozenset({"chat.use", "document.read", "document.upload"}),
    },
    "user": {
        "name": "User",
        "description": "Use grounded chat and read assigned workspace knowledge.",
        "permissions": frozenset({"chat.use", "document.read"}),
    },
}


def test_permission_vocabulary_is_exact() -> None:
    assert frozenset(get_args(PermissionCode)) == EXPECTED_PERMISSIONS
    assert ALL_PERMISSIONS == EXPECTED_PERMISSIONS


def test_builtin_template_metadata_and_permissions_are_exact() -> None:
    assert set(BUILTIN_ROLE_TEMPLATES) == set(EXPECTED_TEMPLATES)
    for key, expected in EXPECTED_TEMPLATES.items():
        template = BUILTIN_ROLE_TEMPLATES[key]
        assert template.key == key
        assert template.name == expected["name"]
        assert template.description == expected["description"]
        assert template.permissions == expected["permissions"]


def test_builtin_templates_only_use_known_permissions() -> None:
    for template in BUILTIN_ROLE_TEMPLATES.values():
        assert template.permissions
        assert template.permissions <= ALL_PERMISSIONS


def test_platform_superadmin_is_not_an_organization_role() -> None:
    assert "superadmin" not in BUILTIN_ROLE_TEMPLATES
    assert {"administrator", "hse_manager", "engineer", "user"} == set(
        BUILTIN_ROLE_TEMPLATES
    )
