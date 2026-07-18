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
class PermissionDefinition:
    code: PermissionCode
    label: str
    group: str
    description: str


PERMISSION_CATALOG: tuple[PermissionDefinition, ...] = (
    PermissionDefinition(
        "audit.read",
        "View audit trail",
        "Governance",
        "Review immutable security and administration events.",
    ),
    PermissionDefinition(
        "chat.use",
        "Use grounded chat",
        "Knowledge",
        "Ask questions against authorized workspace knowledge.",
    ),
    PermissionDefinition(
        "document.approve",
        "Approve documents",
        "Knowledge",
        "Approve governed document versions for retrieval.",
    ),
    PermissionDefinition(
        "document.read",
        "Read documents",
        "Knowledge",
        "View authorized documents and their cited evidence.",
    ),
    PermissionDefinition(
        "document.upload",
        "Upload documents",
        "Knowledge",
        "Add documents for background extraction and indexing.",
    ),
    PermissionDefinition(
        "model.configure",
        "Configure AI models",
        "AI operations",
        "Manage organization model and retrieval profiles.",
    ),
    PermissionDefinition(
        "rag.evaluate",
        "Evaluate RAG quality",
        "AI operations",
        "Run and review grounded-answer quality evaluations.",
    ),
    PermissionDefinition(
        "role.manage",
        "Manage roles",
        "Administration",
        "Create roles and replace organization role bindings.",
    ),
    PermissionDefinition(
        "user.manage",
        "Manage users",
        "Administration",
        "Invite, activate, and deactivate organization users.",
    ),
    PermissionDefinition(
        "workspace.manage",
        "Manage workspaces",
        "Administration",
        "Create workspaces and manage their membership.",
    ),
    PermissionDefinition(
        "workspace.read_all",
        "Read all workspaces",
        "Governance",
        "Read every workspace in the organization.",
    ),
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
        permissions=frozenset({"chat.use", "document.read", "document.upload", "document.approve"}),
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
