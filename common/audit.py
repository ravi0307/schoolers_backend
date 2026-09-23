"""
Request-scoped audit stamping for the `modified_by` / `modified_at` columns
every domain table carries (via AuditColumnsMixin in common.models).

Instead of threading the acting user through every repository, the id is
carried in a ContextVar that the auth dependency sets per request, and a
SQLAlchemy before-flush listener stamps each created/updated row before the
SQL is emitted. Timestamps themselves come from the database:
`server_default=func.now()` for inserts and `onupdate=func.now()` for the
update statement, so nothing here has to set the clock.

Bulk `Query.update()` calls bypass the ORM event; those call sites use
`bulk_modified_columns()` to add the same values to their value dict.
"""
from contextvars import ContextVar
from datetime import datetime, timezone

from sqlalchemy import event
from sqlalchemy.orm import Session

_current_actor_id: ContextVar[int | None] = ContextVar("audit_current_actor_id", default=None)


def set_current_actor(user_id: int | None) -> None:
    """Bind the acting user for the rest of this request's context."""
    _current_actor_id.set(user_id)


def current_actor() -> int | None:
    return _current_actor_id.get()


def _naive_utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def bulk_modified_columns() -> dict[str, object]:
    """Audit values for bulk `Query.update()` calls that bypass the ORM event."""
    return {
        "modified_by": _current_actor_id.get(),
        "modified_at": _naive_utcnow(),
    }


@event.listens_for(Session, "before_flush")
def _stamp_modified(session: Session, flush_context, instances) -> None:
    actor = _current_actor_id.get()
    if actor is None:
        return
    for obj in list(session.new) + list(session.dirty):
        if hasattr(obj, "modified_by"):
            # Session.new entries are inserts: the DB default sets modified_at.
            obj.modified_by = actor