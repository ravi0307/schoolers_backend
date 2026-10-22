"""Privileged-account authorization: the Master / Sub-Master hierarchy.

Master ("master") and Sub-Master ("submaster") share one operational surface
— schools, features, notifications, support, cross-school reads — but every
form of privileged-account management stays exclusively with Master.

This module is the single source of those rules:

- ``role_allowed()`` extends the existing ``require_role(...)`` membership
  check so a Sub-Master is admitted wherever a Master is (operational
  inheritance), without editing every master-guarded router.
- ``assert_master_only()`` guards endpoints no Sub-Master may ever call.
- ``assert_can_manage_privileged()`` guards mutations of privileged accounts:
  the actor must be a Master and the target must be a Sub-Master (there is no
  Master-account mutation API at all).

Denials are logged with actor/target ids and the action — never secrets —
and the caller only ever sees a generic 403 detail, so error responses reveal
nothing about the account they targeted.
"""
import logging

from common.exceptions import ForbiddenError

logger = logging.getLogger("schoolers.authz")

MASTER_ROLE = "master"
SUBMASTER_ROLE = "submaster"
PRIVILEGED_ROLES = frozenset({MASTER_ROLE, SUBMASTER_ROLE})

_GENERIC_DENIED = "Not permitted to manage privileged accounts"


def is_master_like(role: str | None) -> bool:
    """True for roles that operate above every school (Master or Sub-Master)."""
    return role in PRIVILEGED_ROLES


def role_allowed(role: str | None, allowed_roles) -> bool:
    """Membership rule for require_role: a Sub-Master inherits every
    capability granted to a Master, and nothing else."""
    if role in allowed_roles:
        return True
    return role == SUBMASTER_ROLE and MASTER_ROLE in allowed_roles


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
