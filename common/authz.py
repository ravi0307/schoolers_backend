"""Privileged-account authorization: the Master / Sub-Master hierarchy, plus
the school-level Admin / Sub-Admin rules for the Add Staff workflow.

Master ("master") and Sub-Master ("submaster") share one operational surface
— schools, features, notifications, support, cross-school reads — but every
form of privileged-account management stays exclusively with Master.

Admin ("admin") and Sub-Admin ("subadmin") share the school's operational
surface. A Sub-Admin is created through the ordinary Add Staff flow, not a
separate screen, so its restrictions are enforced around that workflow:

- ``role_allowed()`` extends the existing ``require_role(...)`` membership
  check so a Sub-Master is admitted wherever a Master is, and a Sub-Admin
  wherever an Admin is (operational inheritance), without editing every
  guarded router.
- ``assert_master_only()`` guards endpoints no Sub-Master may ever call.
- ``assert_can_manage_privileged()`` guards mutations of privileged accounts:
  the actor must be a Master and the target must be a Sub-Master (there is no
  Master-account mutation API at all).
- ``assert_can_assign_subadmin()`` guards granting the Sub-Admin role itself —
  Admin actors only, so a Sub-Admin can never mint another one (or itself).
- ``assert_can_manage_school_account()`` guards mutations of staff records
  whose linked login is privileged (Admin or Sub-Admin): a Sub-Admin actor is
  refused, including on its own staff row, so there is no self-modification
  path through Add Staff edit/remove.

Denials are logged with actor/target ids and the action — never secrets —
and the caller only ever sees a generic 403 detail, so error responses reveal
nothing about the account they targeted.
"""
import logging

from common.exceptions import ForbiddenError

logger = logging.getLogger("schoolers.authz")

MASTER_ROLE = "master"
SUBMASTER_ROLE = "submaster"
SUBADMIN_ROLE = "subadmin"
ADMIN_ROLE = "admin"
PRIVILEGED_ROLES = frozenset({MASTER_ROLE, SUBMASTER_ROLE})
# School-level privileged accounts: exactly the roles Add Staff may not
# mutate when the actor is a Sub-Admin (and the roles only Admin assigns).
SCHOOL_PRIVILEGED_ROLES = frozenset({ADMIN_ROLE, SUBADMIN_ROLE})
SCHOOL_ADMIN_ROLES = frozenset({ADMIN_ROLE, SUBADMIN_ROLE})

_GENERIC_DENIED = "Not permitted to manage privileged accounts"


def is_master_like(role: str | None) -> bool:
    """True for roles that operate above every school (Master or Sub-Master)."""
    return role in PRIVILEGED_ROLES


def is_school_admin(role: str | None) -> bool:
    """True for roles that hold the school Admin's operational surface."""
    return role in SCHOOL_ADMIN_ROLES


def role_allowed(role: str | None, allowed_roles) -> bool:
    """Membership rule for require_role: a Sub-Master inherits every
    capability granted to a Master, and a Sub-Admin every capability granted
    to an Admin — and nothing else."""
    if role in allowed_roles:
        return True
    if role == SUBMASTER_ROLE:
        return MASTER_ROLE in allowed_roles
    return role == SUBADMIN_ROLE and ADMIN_ROLE in allowed_roles


def _deny(logger_message: str) -> None:
    # Denied privileged operations leave a trace; the client message stays
    # generic so it cannot confirm or describe the targeted account.
    logger.warning(logger_message)
    raise ForbiddenError(_GENERIC_DENIED)


def assert_master_only(current_user, action: str) -> None:
    """Endpoint guard: only an authenticated Master may proceed."""
    if current_user.role != MASTER_ROLE:
        _deny(
            f"privileged-account {action} denied: actor={current_user.user_id} "
            f"role={current_user.role!r}"
        )


def assert_can_manage_privileged(current_user, target_role: str, action: str) -> None:
    """Mutation guard for privileged accounts.

    Only a Master may manage a Sub-Master. Master accounts have no mutation
    API at all (target_role == master is refused for everyone), and a
    Sub-Master may never manage any privileged account — including itself.
    """
    if current_user.role != MASTER_ROLE or target_role != SUBMASTER_ROLE:
        _deny(
            f"privileged-account {action} denied: actor={current_user.user_id} "
            f"role={current_user.role!r} target_role={target_role!r}"
        )


def assert_can_assign_subadmin(current_user, action: str) -> None:
    """Grant guard: only a school Admin may hand out the Sub-Admin role.

    A Sub-Admin actor is refused even for its own school (no self-escalation
    and no second Sub-Admin), and Master never reaches the staff endpoints at
    all, so this is the single place the assignment decision lives.
    """
    if current_user.role != ADMIN_ROLE:
        _deny(
            f"subadmin {action} denied: actor={current_user.user_id} "
            f"role={current_user.role!r}"
        )


def assert_can_manage_school_account(current_user, target_login_roles, action: str) -> None:
    """Mutation guard for staff records whose linked login is privileged.

    The target roles are read from the users rows linked to the staff record
    (never from the request). A Sub-Admin may not update or remove any staff
    account that resolves to an Admin or Sub-Admin login — its own included —
    so there is no path by which it edits, deactivates or deletes a privileged
    account through the staff workflows.
    """
    if current_user.role != SUBADMIN_ROLE:
        return
    hit = set(target_login_roles) & SCHOOL_PRIVILEGED_ROLES
    if hit:
        _deny(
            f"staff-account {action} denied: actor={current_user.user_id} "
            f"role={current_user.role!r} target_roles={sorted(hit)!r}"
        )
