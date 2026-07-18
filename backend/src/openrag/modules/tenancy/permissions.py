from dataclasses import dataclass
from typing import Literal

PermissionCode = Literal[
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
]

ALL_PERMISSIONS: frozenset[str] = frozenset(
    PermissionCode.__args__  # type: ignore[attr-defined]
)


@dataclass(frozen=True)
class RoleTemplate:
    key: str
    name: str
    description: str
    permissions: frozenset[str]


BUILTIN_ROLE_TEMPLATES = {
    "administrator": RoleTemplate(
        key="administrator",
        name="Administrator",
        description="Manage organization users, roles, workspaces and knowledge.",
        permissions=ALL_PERMISSIONS,
    ),
    "hse_manager": RoleTemplate(
        key="hse_manager",
        name="HSE Manager",
        description="Manage and approve HSE knowledge in assigned workspaces.",
        permissions=frozenset(
            {"chat.use", "document.read", "document.upload", "document.approve"}
        ),
    ),
    "engineer": RoleTemplate(
        key="engineer",
        name="Engineer",
        description="Use chat and contribute knowledge in assigned workspaces.",
        permissions=frozenset({"chat.use", "document.read", "document.upload"}),
    ),
    "user": RoleTemplate(
        key="user",
        name="User",
        description="Use grounded chat and read assigned workspace knowledge.",
        permissions=frozenset({"chat.use", "document.read"}),
    ),
}
