"""
Central access-control matrix (module, action) -> allowed roles.
This mirrors the table in FASTAPI_MODULES.md. Routers can use this via
`check_permission()` for a single source of truth instead of scattering
`if role == ...` checks, though most routers here use the simpler
`require_role()` dependency directly for clarity.
"""
from common.exceptions import ForbiddenError

PERMISSIONS: dict[str, dict[str, set[str]]] = {
    "schools":        {"read": {"admin", "master"}, "write": {"master"}},
    "academics":      {"read": {"parent", "teacher", "admin"}, "write": {"admin"}},
    "people":         {"read": {"parent", "teacher", "admin"}, "write": {"admin"}},
    "attendance":     {"read": {"parent", "teacher", "admin"}, "write": {"teacher", "admin"}},
    "marks":          {"read": {"parent", "teacher", "admin"}, "write": {"teacher", "admin"}},
    "timetable":      {"read": {"parent", "teacher", "admin"}, "write": {"teacher", "admin"}},
    "transport":      {"read": {"parent", "admin"}, "write": {"admin", "pilot"}},
    "leave":          {"read": {"admin"}, "write": {"parent", "teacher", "staff", "pilot", "admin"}},
    "communication":  {"read": {"parent", "teacher", "admin", "pilot"}, "write": {"teacher", "admin", "pilot"}},
    "barter":         {"read": {"parent"}, "write": {"parent"}},
    "activities":     {"read": {"parent"}, "write": {"admin"}},
    "website":        {"read": {"admin"}, "write": {"admin"}},
    "notifications":  {"read": {"admin"}, "write": {"master"}},
    "reports":        {"read": {"admin", "master"}, "write": set()},
}


def check_permission(module: str, action: str, role: str) -> None:
    allowed = PERMISSIONS.get(module, {}).get(action, set())
    if role not in allowed:
        raise ForbiddenError(f"Role '{role}' cannot {action} on module '{module}'")
