from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from common.database import get_db
from common.dependencies import CurrentUser, require_role
from common.exceptions import ForbiddenError, NotFoundError
import repository as repo
from schemas import (
    SupportMessageCreate,
    SupportMessageRead,
    SupportStatusUpdate,
    SupportTicketCreate,
    SupportTicketDetail,
    SupportTicketRead,
)

router = APIRouter(prefix="/support", tags=["support"])


@router.post("/tickets", response_model=SupportTicketDetail, status_code=201)
def create_ticket(
    payload: SupportTicketCreate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    """A school admin raises an issue about the portal.

    Only an admin opens a ticket, and it is pinned to their own school from
    the session rather than any request field, so a ticket cannot be filed
    against another school.
    """
    if current_user.school_id is None:
        raise ForbiddenError("This account is not linked to a school")
    ticket = repo.create_ticket(db, current_user, payload.model_dump())
    return repo.ticket_detail(db, ticket)


@router.get("/tickets", response_model=list[SupportTicketRead])
def list_tickets(
    status: str | None = Query(default=None),
    school_id: int | None = Query(
        default=None,
        description="Master-only: narrow the inbox to one school.",
    ),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("admin", "master")),
):
    """The caller's queue: an admin's own school, or every school for master."""
    if current_user.role == "master":
        return repo.list_tickets(db, status=status, school_id=school_id)
    if current_user.school_id is None:
        return []
    return repo.list_tickets(db, status=status, school_id=current_user.school_id)


@router.get("/tickets/{ticket_id}", response_model=SupportTicketDetail)
def get_ticket(
    ticket_id: int,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("admin", "master")),
):
    """One ticket and its whole message trail.

    Another school's ticket is a 404, not a 403: an admin must not be able to
    learn that a guessed ticket id exists, matching the rest of the platform.
    """
    ticket = repo.get_ticket(db, ticket_id)
    if ticket is None or not repo.can_view(current_user, ticket):
        raise NotFoundError("Ticket not found")
    return repo.ticket_detail(db, ticket)


@router.post(
    "/tickets/{ticket_id}/messages",
    response_model=SupportMessageRead,
    status_code=201,
)
def add_message(
    ticket_id: int,
    payload: SupportMessageCreate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("admin", "master")),
):
    """Add a turn to the ticket's conversation, from either party."""
    ticket = repo.get_ticket(db, ticket_id)
    if ticket is None or not repo.can_view(current_user, ticket):
        raise NotFoundError("Ticket not found")
    return repo.add_message(db, ticket, current_user, payload.model_dump())


@router.patch("/tickets/{ticket_id}/status", response_model=SupportTicketRead)
def set_status(
    ticket_id: int,
    payload: SupportStatusUpdate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("master")),
):
    """Move a ticket between Open / In progress / Assigned / Completed / Cancelled."""
    ticket = repo.get_ticket(db, ticket_id)
    if ticket is None:
        raise NotFoundError("Ticket not found")
    repo.set_status(db, ticket, payload.status.value)
    return repo.ticket_summary(db, ticket)
