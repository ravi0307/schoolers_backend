"""
Central access-control matrix (module, action) -> allowed roles.
This mirrors the table in FASTAPI_MODULES.md. Routers can use this via
`check_permission()` for a single source of truth instead of scattering
`if role == ...` checks, though most routers here use the simpler
`require_role()` dependency directly for clarity.
"""
from common.exceptions import ForbiddenError

PERMISSIONS: dict[str, dict[str, set[str]]] = {
    # Master and Sub-Master share one operational surface; privileged-account
    # management is enforced by common.authz, not by this matrix.
    # Sub-Admin mirrors every Admin entry (school-level operational parity);
    # it never appears in master-only sets, and the Add Staff assignment /
    # privileged-target restrictions live in common.authz.
    "schools":        {"read": {"admin", "subadmin", "master", "submaster"}, "write": {"master", "submaster"}},
    "academics":      {"read": {"parent", "teacher", "admin", "subadmin"}, "write": {"admin", "subadmin"}},
    "people":         {"read": {"parent", "teacher", "admin", "subadmin"}, "write": {"admin", "subadmin"}},
    "attendance":     {"read": {"parent", "teacher", "admin", "subadmin"}, "write": {"teacher", "admin", "subadmin"}},
    "marks":          {"read": {"parent", "teacher", "admin", "subadmin"}, "write": {"teacher", "admin", "subadmin"}},
    "timetable":      {"read": {"parent", "teacher", "admin", "subadmin"}, "write": {"teacher", "admin", "subadmin"}},
    "transport":      {"read": {"parent", "admin", "subadmin"}, "write": {"admin", "subadmin", "pilot"}},
    "leave":          {"read": {"admin", "subadmin"}, "write": {"parent", "teacher", "staff", "pilot", "admin", "subadmin"}},
    "communication":  {"read": {"parent", "teacher", "admin", "pilot", "subadmin"}, "write": {"teacher", "admin", "pilot", "subadmin"}},
    "barter":         {"read": {"parent"}, "write": {"parent"}},
    "activities":     {"read": {"parent"}, "write": {"admin", "subadmin"}},
    "website":        {"read": {"admin", "subadmin"}, "write": {"admin", "subadmin"}},
    "notifications":  {"read": {"admin", "subadmin"}, "write": {"master", "submaster"}},
    "reports":        {"read": {"admin", "subadmin", "master", "submaster"}, "write": set()},
}


def check_permission(module: str, action: str, role: str) -> None:
    allowed = PERMISSIONS.get(module, {}).get(action, set())
    if role not in allowed:
        raise ForbiddenError(f"Role '{role}' cannot {action} on module '{module}'")
