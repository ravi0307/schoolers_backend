from sqlalchemy import func
from sqlalchemy.orm import Session

from common.models import (
    Parent,
    School,
    Staff,
    SupportTicket,
    SupportTicketMessage,
    User,
)

# Statuses the master admin may set. "Open" is the state a ticket is born in,
# so it is listed here only to reject a no-op PATCH that tries to reopen.
STATUSES = ("Open", "In progress", "Assigned", "Completed", "Cancelled")


def author_name(db: Session, user) -> str:
    """The writer's display name, resolved from their linked person row.

    Mirrors auth_service.display_name_for without importing across services:
    a staff-linked account (admin/teacher/pilot/staff) reads Staff.name, a
    parent reads Parent.name, and anything else falls back to the username.
    """
    row = db.query(User).filter(User.user_id == user.user_id).first()
    if row is None:
        return "Unknown"
    if row.linked_person_id:
        if row.role == "parent":
            person = db.query(Parent).filter(Parent.parent_id == row.linked_person_id).first()
        else:
            person = db.query(Staff).filter(Staff.staff_id == row.linked_person_id).first()
        if person is not None and person.name:
            return person.name
    return row.username


def can_view(current_user, ticket: SupportTicket) -> bool:
    """The master sees every ticket; an admin sees only their own school's."""
    return current_user.role == "master" or ticket.school_id == current_user.school_id


def _message_dict(message: SupportTicketMessage) -> dict:
    return {
        "message_id": message.message_id,
        "author_user_id": message.author_user_id,
        "author_role": message.author_role,
        "author_name": message.author_name,
        "body": message.body,
        "attachments": list(message.attachments or []),
        "created_at": message.created_at,
    }


def _summary_dict(ticket: SupportTicket, school_name: str | None, message_count: int) -> dict:
    return {
        "ticket_id": ticket.ticket_id,
        "school_id": ticket.school_id,
        "school_name": school_name,
        "created_by": ticket.created_by,
        "created_by_name": ticket.created_by_name,
        "subject": ticket.subject,
        "status": ticket.status,
        "created_at": ticket.created_at,
        "updated_at": ticket.modified_at,
        "message_count": message_count,
    }


def create_ticket(db: Session, current_user, data: dict) -> SupportTicket:
    """Open a ticket and record its description as the first message."""
    name = author_name(db, current_user)
    ticket = SupportTicket(
        school_id=current_user.school_id,
        created_by=current_user.user_id,
        created_by_name=name,
        subject=data["subject"],
        status="Open",
    )
    db.add(ticket)
    db.flush()

    db.add(
        SupportTicketMessage(
            ticket_id=ticket.ticket_id,
            author_user_id=current_user.user_id,
            author_role=current_user.role,
            author_name=name,
            body=data["body"],
            attachments=list(data.get("attachments") or []),
        )
    )
    db.commit()
    db.refresh(ticket)
    return ticket


def list_tickets(
    db: Session,
    status: str | None = None,
    school_id: int | None = None,
) -> list[dict]:
    """Newest-first tickets, optionally narrowed by status and/or school."""
    count_sq = (
        db.query(func.count(SupportTicketMessage.message_id))
        .filter(SupportTicketMessage.ticket_id == SupportTicket.ticket_id)
        .correlate(SupportTicket)
        .scalar_subquery()
    )
    q = (
        db.query(SupportTicket, School.name, count_sq.label("message_count"))
        .join(School, School.school_id == SupportTicket.school_id)
    )
    if status:
        q = q.filter(SupportTicket.status == status)
    if school_id is not None:
        q = q.filter(SupportTicket.school_id == school_id)
    rows = q.order_by(SupportTicket.created_at.desc(), SupportTicket.ticket_id.desc()).all()
    return [_summary_dict(ticket, name, count) for ticket, name, count in rows]


def get_ticket(db: Session, ticket_id: int) -> SupportTicket | None:
    return db.query(SupportTicket).filter(SupportTicket.ticket_id == ticket_id).first()


def ticket_summary(db: Session, ticket: SupportTicket) -> dict:
    name = (
        db.query(School.name).filter(School.school_id == ticket.school_id).scalar()
    )
    count = (
        db.query(func.count(SupportTicketMessage.message_id))
        .filter(SupportTicketMessage.ticket_id == ticket.ticket_id)
        .scalar()
    )
    return _summary_dict(ticket, name, count or 0)


def ticket_detail(db: Session, ticket: SupportTicket) -> dict:
    data = ticket_summary(db, ticket)
    messages = (
        db.query(SupportTicketMessage)
        .filter(SupportTicketMessage.ticket_id == ticket.ticket_id)
        .order_by(SupportTicketMessage.created_at, SupportTicketMessage.message_id)
        .all()
    )
    data["messages"] = [_message_dict(m) for m in messages]
    return data


def add_message(db: Session, ticket: SupportTicket, current_user, data: dict) -> dict:
    message = SupportTicketMessage(
        ticket_id=ticket.ticket_id,
        author_user_id=current_user.user_id,
        author_role=current_user.role,
        author_name=author_name(db, current_user),
        body=data["body"],
        attachments=list(data.get("attachments") or []),
    )
    db.add(message)
    # A reply is conversation only: the status is moved deliberately by the
    # master via PATCH, so replying never silently changes the ticket's state.
    db.commit()
    db.refresh(message)
    return _message_dict(message)


def set_status(db: Session, ticket: SupportTicket, status: str) -> SupportTicket:
    ticket.status = status
    db.commit()
    db.refresh(ticket)
    return ticket
