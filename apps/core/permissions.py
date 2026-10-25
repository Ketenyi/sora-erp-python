"""Role-based access control ported from the PHP ``Permission`` class.

The legacy app resolves access twice: role names stored in ``user_roles`` and
module roles derived from a fixed role-name mapping. ``user_module_roles``
rows (managed from Settings > Multi-role manager) take precedence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import wraps

from django.core.exceptions import PermissionDenied
from django.utils import timezone

ADMIN_ROLES = {"admin", "super_admin", "system_admin"}

MODULES = (
    "hr",
    "sales",
    "accounts",
    "inventory",
    "purchasing",
    "crm",
    "projects",
    "helpdesk",
    "assets",
    "pos",
    "settings",
)

ROLE_MODULE_MAP = {
    # HR
    "hr_admin": ("hr", "admin"),
    "hr_manager": ("hr", "admin"),
    "hr_viewer": ("hr", "viewer"),
    "employee_base": ("hr", "employee"),
    "employee": ("hr", "employee"),
    # Sales
    "sales_admin": ("sales", "admin"),
    "sales_manager": ("sales", "admin"),
    "sales_rep": ("sales", "rep"),
    "sales": ("sales", "rep"),
    # Accounts
    "accounts_admin": ("accounts", "admin"),
    "finance_admin": ("accounts", "admin"),
    "accounts_viewer": ("accounts", "viewer"),
    "finance_viewer": ("accounts", "viewer"),
    "accountant": ("accounts", "admin"),
    # Inventory
    "inventory_admin": ("inventory", "admin"),
    "inventory_clerk": ("inventory", "clerk"),
    "inventory_viewer": ("inventory", "viewer"),
    # Purchasing
    "procurement_admin": ("purchasing", "admin"),
    "purchasing_admin": ("purchasing", "admin"),
    "procurement_viewer": ("purchasing", "viewer"),
    "purchasing_viewer": ("purchasing", "viewer"),
    # CRM
    "crm_admin": ("crm", "admin"),
    "crm_rep": ("crm", "rep"),
    # Projects
    "project_admin": ("projects", "admin"),
    "projects_admin": ("projects", "admin"),
    "project_manager": ("projects", "admin"),
    "project_member": ("projects", "rep"),
    "project_rep": ("projects", "rep"),
    "projects_rep": ("projects", "rep"),
    "engineer": ("projects", "rep"),
    "project_viewer": ("projects", "viewer"),
    "projects_viewer": ("projects", "viewer"),
}

# Fallbacks for the legacy users.role column. The original app only mapped
# granular role names, which left accounts created with role="sales" without
# module access; these fallbacks make the ported app usable out of the box.
PRIMARY_ROLE_FALLBACKS = {
    "manager": {
        "sales": "admin",
        "crm": "admin",
        "accounts": "viewer",
        "inventory": "viewer",
        "purchasing": "viewer",
        "hr": "viewer",
        "projects": "admin",
        "helpdesk": "admin",
        "assets": "admin",
        "pos": "admin",
    },
    "sales": {"sales": "rep", "crm": "rep", "pos": "rep"},
    "accountant": {"accounts": "admin", "purchasing": "viewer", "projects": "viewer"},
    "viewer": {module: "viewer" for module in MODULES},
    "employee": {"hr": "employee", "projects": "rep"},
    "hr_manager": {"hr": "admin", "projects": "viewer"},
}

IMPLIED_ACTIONS = {
    "admin": {"view", "create", "edit", "delete", "approve", "export", "manage"},
    "rep": {"view", "create", "edit", "export"},
    "clerk": {"view", "create", "edit", "export"},
    "viewer": {"view", "export"},
    "employee": {"view"},
}

GRANT_ROLE_ACTIONS = {
    "admin": {"view", "create", "edit", "delete", "approve", "export", "manage"},
    "manager": {"view", "create", "edit", "delete", "approve", "export"},
    "editor": {"view", "create", "edit", "export"},
    "approver": {"view", "approve", "export"},
    "viewer": {"view", "export"},
}
GRANT_WRITE_ROLES = {"admin", "manager", "editor"}


@dataclass
class Access:
    user: object
    role_names: set[str] = field(default_factory=set)
    roles: list[dict] = field(default_factory=list)
    module_roles: dict[str, str] = field(default_factory=dict)
    permissions: dict[str, set[str]] = field(default_factory=dict)
    grants: list[dict] = field(default_factory=list)
    is_admin: bool = False

    def module_role(self, module: str) -> str | None:
        return self.module_roles.get(module)

    def _module_grants(self, module: str, submodule: str | None = None) -> list[dict]:
        rows = [row for row in self.grants if row["module"] == module and row["is_active"]]
        if submodule is not None:
            rows = [
                row
                for row in rows
                if not row["submodule"] or row["submodule"] == submodule
            ]
        return rows

    def has_grant(self, module: str) -> bool:
        return any(row["is_active"] for row in self.grants if row["module"] == module)

    def submodule_role(self, module: str, submodule: str) -> str | None:
        if self.is_admin:
            return "admin"
        rows = self._module_grants(module, submodule)
        if not rows:
            return self.module_roles.get(module) if not self.has_grant(module) else None
        order = {"admin": 5, "manager": 4, "editor": 3, "approver": 2, "viewer": 1}
        return max(rows, key=lambda row: order.get(row["role"], 0))["role"]

    def can(self, module: str, action: str, submodule: str | None = None) -> bool:
        if self.is_admin:
            return True
        rows = self._module_grants(module, submodule)
        if rows:
            return any(action in GRANT_ROLE_ACTIONS.get(row["role"], set()) for row in rows)
        if self.has_grant(module):
            # A module-level grant exists but not for this submodule.
            return False
        role = self.module_roles.get(module)
        if role == "admin":
            return True
        if action in self.permissions.get(module, set()):
            return True
        if role and action in IMPLIED_ACTIONS.get(role, set()):
            return True
        return False

    def has_module(self, module: str) -> bool:
        if self.is_admin:
            return True
        if self.has_grant(module):
            return True
        return self.module_roles.get(module) is not None

    def has_submodule(self, module: str, submodule: str) -> bool:
        if self.is_admin:
            return True
        if not self.has_grant(module):
            return self.has_module(module)
        return bool(self._module_grants(module, submodule))

    def module_scope(self, module: str) -> str:
        """Return the widest scope granted for a module."""
        if self.is_admin:
            return "tenant"
        rows = self._module_grants(module)
        scopes = {row["scope"] for row in rows}
        if not scopes or "tenant" in scopes:
            return "tenant"
        if "assigned" in scopes:
            return "assigned"
        return "project"

    def grant_project_ids(self, module: str) -> set[int]:
        return {
            row["project_id"]
            for row in self._module_grants(module)
            if row["scope"] == "project" and row["project_id"]
        }

    def has_grant_role(self, module: str, roles) -> bool:
        roles = {roles} if isinstance(roles, str) else set(roles)
        return any(
            row["role"] in roles for row in self._module_grants(module)
        )

    def can_access_project(self, project_id: int, module: str = "projects") -> bool:
        if self.is_admin:
            return True
        if self.has_grant(module):
            scope = self.module_scope(module)
            if scope == "tenant":
                return True
            allowed = self.allowed_project_ids(module)
            return allowed is None or project_id in allowed
        return self.has_module(module)

    def allowed_project_ids(self, module: str = "projects") -> set[int] | None:
        """None means every project in the workspace is visible."""
        if self.is_admin:
            return None
        if not self.has_grant(module):
            return None
        if self.module_scope(module) == "tenant":
            return None
        allowed = self.grant_project_ids(module)
        if self.module_scope(module) == "assigned":
            employee_id = getattr(self.user, "employee_id", None)
            if employee_id:
                from apps.core.models import Project, ProjectTeam

                allowed |= set(
                    ProjectTeam.objects.filter(employee_id=employee_id)
                    .exclude(left_at__lt=timezone.localdate())
                    .values_list("project_id", flat=True)
                )
                allowed |= set(
                    Project.objects.filter(manager_id=employee_id).values_list("id", flat=True)
                )
        return allowed

    # Template-friendly flags
    @property
    def can_view_hr(self) -> bool:
        return self.has_module("hr")

    @property
    def can_view_sales(self) -> bool:
        return self.has_module("sales")

    @property
    def can_view_crm(self) -> bool:
        return self.has_module("crm")

    @property
    def can_view_accounts(self) -> bool:
        return self.has_module("accounts")

    @property
    def can_view_inventory(self) -> bool:
        return self.has_module("inventory")

    @property
    def can_view_purchasing(self) -> bool:
        return self.has_module("purchasing")

    @property
    def can_view_projects(self) -> bool:
        return self.has_module("projects")

    @property
    def can_view_helpdesk(self) -> bool:
        return self.has_module("helpdesk")

    @property
    def can_view_assets(self) -> bool:
        return self.has_module("assets")

    @property
    def can_view_pos(self) -> bool:
        return self.has_module("pos")

    @property
    def can_view_settings(self) -> bool:
        return self.is_admin or self.has_module("settings")

    @property
    def is_hr_admin(self) -> bool:
        return (
            self.is_admin
            or self.module_roles.get("hr") == "admin"
            or self.has_grant_role("hr", {"admin", "manager", "editor"})
        )

    @property
    def is_hr_viewer(self) -> bool:
        return self.module_roles.get("hr") == "viewer" or self.has_grant_role(
            "hr", {"viewer", "approver", "editor"}
        )

    @property
    def is_employee(self) -> bool:
        return self.module_roles.get("hr") == "employee"

    @property
    def is_sales_admin(self) -> bool:
        return (
            self.is_admin
            or self.module_roles.get("sales") == "admin"
            or self.has_grant_role("sales", GRANT_WRITE_ROLES)
        )

    @property
    def is_sales_rep(self) -> bool:
        return self.module_roles.get("sales") == "rep"

    @property
    def is_accounts_admin(self) -> bool:
        return (
            self.is_admin
            or self.module_roles.get("accounts") == "admin"
            or self.has_grant_role("accounts", GRANT_WRITE_ROLES)
        )

    @property
    def is_accounts_viewer(self) -> bool:
        return self.module_roles.get("accounts") == "viewer" or self.has_grant_role(
            "accounts", {"viewer", "approver", "editor"}
        )

    @property
    def is_inventory_admin(self) -> bool:
        return (
            self.is_admin
            or self.module_roles.get("inventory") == "admin"
            or self.has_grant_role("inventory", GRANT_WRITE_ROLES)
        )

    @property
    def is_inventory_clerk(self) -> bool:
        return self.module_roles.get("inventory") == "clerk"

    @property
    def is_inventory_viewer(self) -> bool:
        return self.module_roles.get("inventory") == "viewer"

    @property
    def is_purchasing_admin(self) -> bool:
        return (
            self.is_admin
            or self.module_roles.get("purchasing") == "admin"
            or self.has_grant_role("purchasing", GRANT_WRITE_ROLES)
        )

    @property
    def is_purchasing_viewer(self) -> bool:
        return self.module_roles.get("purchasing") == "viewer"

    @property
    def is_crm_admin(self) -> bool:
        return (
            self.is_admin
            or self.module_roles.get("crm") == "admin"
            or self.has_grant_role("crm", GRANT_WRITE_ROLES)
        )

    @property
    def is_crm_rep(self) -> bool:
        return self.module_roles.get("crm") == "rep"

    @property
    def is_projects_admin(self) -> bool:
        return (
            self.is_admin
            or self.module_roles.get("projects") == "admin"
            or self.has_grant_role("projects", GRANT_WRITE_ROLES)
        )

    @property
    def is_projects_viewer(self) -> bool:
        return self.module_roles.get("projects") == "viewer"

    @property
    def is_helpdesk_admin(self) -> bool:
        return (
            self.is_admin
            or self.module_roles.get("helpdesk") == "admin"
            or self.has_grant_role("helpdesk", GRANT_WRITE_ROLES)
        )

    @property
    def is_assets_admin(self) -> bool:
        return (
            self.is_admin
            or self.module_roles.get("assets") == "admin"
            or self.has_grant_role("assets", GRANT_WRITE_ROLES)
        )

    @property
    def is_pos_admin(self) -> bool:
        return (
            self.is_admin
            or self.module_roles.get("pos") == "admin"
            or self.has_grant_role("pos", GRANT_WRITE_ROLES)
        )


def _empty_access(user) -> Access:
    return Access(user=user)


def get_access(user) -> Access:
    if user is None or not getattr(user, "is_authenticated", False):
        return _empty_access(user)

    cached = getattr(user, "_sora_access", None)
    if cached is not None:
        return cached

    try:
        from apps.core.models import Permission, Role, RolePermission, UserModuleRole, UserRole
        from apps.core.models import UserPermission

        user_roles = list(
            UserRole.objects.filter(user_id=user.id).values_list("role_id", flat=True)
        )
        roles = list(
            Role.objects.filter(id__in=user_roles).values("id", "name", "display_name")
        )
        if not roles and user.role:
            roles = [{"id": None, "name": user.role, "display_name": user.role.title()}]

        role_names = {str(role["name"]).lower() for role in roles}
        is_admin = bool(role_names & ADMIN_ROLES) or user.role in ADMIN_ROLES

        module_roles: dict[str, str] = {}
        if is_admin:
            module_roles = {module: "admin" for module in MODULES}
        else:
            for role_name in role_names:
                mapping = ROLE_MODULE_MAP.get(role_name)
                if mapping:
                    module, role_value = mapping
                    module_roles[module] = role_value
            fallback = PRIMARY_ROLE_FALLBACKS.get(user.role or "", {})
            for module, role_value in fallback.items():
                module_roles.setdefault(module, role_value)

            # Explicit module assignments win over derived roles.
            for assignment in UserModuleRole.objects.filter(user_id=user.id).values(
                "module_name", "role_name"
            ):
                module = assignment["module_name"]
                role_name = (assignment["role_name"] or "").lower()
                normalized = role_name.split("_")[-1] if "_" in role_name else role_name
                if normalized in {"viewer", "clerk", "rep", "employee", "admin"}:
                    module_roles[module] = normalized
                elif role_name in ADMIN_ROLES:
                    module_roles[module] = "admin"

        grants: list[dict] = []
        if not is_admin:
            from apps.core.models import ModuleAccessGrant
            from apps.core.tenancy import ALL_TENANTS, current_tenant

            tenant = current_tenant()
            if tenant is ALL_TENANTS:
                grant_qs = ModuleAccessGrant.objects.unscoped()
            elif tenant is not None:
                grant_qs = ModuleAccessGrant.objects
            else:
                grant_qs = None
            if grant_qs is not None:
                grants = list(
                    grant_qs.filter(user_id=user.id, is_active=True).values(
                        "module",
                        "submodule",
                        "role",
                        "scope",
                        "project_id",
                        "is_active",
                    )
                )

        permissions: dict[str, set[str]] = {}
        role_ids = [role["id"] for role in roles if role["id"]]
        if role_ids:
            rows = RolePermission.objects.filter(role_id__in=role_ids).values_list(
                "permission_id", flat=True
            )
            perms = Permission.objects.filter(id__in=list(rows)).values_list(
                "module", "action"
            )
            for module, action in perms:
                permissions.setdefault(module, set()).add(action)

        extra = UserPermission.objects.filter(user_id=user.id, granted=1).values_list(
            "permission_id", flat=True
        )
        if extra:
            for module, action in Permission.objects.filter(id__in=list(extra)).values_list(
                "module", "action"
            ):
                permissions.setdefault(module, set()).add(action)

        access = Access(
            user=user,
            role_names=role_names,
            roles=roles,
            module_roles=module_roles,
            permissions=permissions,
            grants=grants,
            is_admin=is_admin,
        )
    except Exception:
        # During first boot (before migrations) or if RBAC tables are absent,
        # fall back to the primary role so the app remains usable.
        is_admin = user.role in ADMIN_ROLES
        access = Access(
            user=user,
            role_names={user.role} if user.role else set(),
            roles=[],
            module_roles={m: "admin" for m in MODULES} if is_admin else PRIMARY_ROLE_FALLBACKS.get(user.role or "", {}),
            permissions={},
            is_admin=is_admin,
        )

    user._sora_access = access
    return access


# ---------------------------------------------------------------------------
# Helpers used by the custom User model and by views/templates
# ---------------------------------------------------------------------------
def user_has_perm(user, module: str, action: str) -> bool:
    return get_access(user).can(module, action)


def visible_projects(user, queryset=None):
    """Filter a project queryset to the projects this user may see."""
    from apps.core.models import Project

    queryset = queryset if queryset is not None else Project.objects.all()
    allowed = get_access(user).allowed_project_ids("projects")
    if allowed is not None:
        queryset = queryset.filter(id__in=allowed)
    return queryset


def can_access_project(user, project_id: int) -> bool:
    return get_access(user).can_access_project(project_id, "projects")


def user_module_role(user, module: str) -> str | None:
    return get_access(user).module_role(module)


def user_is_admin(user) -> bool:
    return get_access(user).is_admin


# ---------------------------------------------------------------------------
# Decorators
# ---------------------------------------------------------------------------
def module_access_required(*modules: str):
    """Allow access when the user can view at least one of the modules."""

    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            access = get_access(request.user)
            if not any(access.has_module(module) for module in modules):
                raise PermissionDenied("You do not have access to this module.")
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


def permission_required(module: str, action: str = "view", submodule: str | None = None):
    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if not get_access(request.user).can(module, action, submodule=submodule):
                raise PermissionDenied("You do not have permission to perform this action.")
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


def submodule_access_required(module: str, submodule: str, action: str = "view"):
    return permission_required(module, action, submodule=submodule)


def admin_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not get_access(request.user).is_admin:
            raise PermissionDenied("Administrator access required.")
        return view(request, *args, **kwargs)

    return wrapper
