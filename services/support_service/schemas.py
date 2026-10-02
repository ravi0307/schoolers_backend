from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from common.enums import TicketStatus


def _clean_attachments(urls: list[str]) -> list[str]:
    """Keep stored URLs only: trimmed, non-empty, and capped in count."""
    cleaned = [url.strip() for url in urls if isinstance(url, str) and url.strip()]
    if len(cleaned) > 10:
        raise ValueError("A message can carry at most 10 attachments")
    return cleaned


class SupportTicketCreate(BaseModel):
    """Opening a ticket: a subject, the issue itself, and any screenshots/docs."""

    subject: str = Field(min_length=3, max_length=200)
    body: str = Field(min_length=1)
    attachments: list[str] = Field(default_factory=list)

    @field_validator("subject", "body")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @field_validator("attachments")
    @classmethod
    def _attachments(cls, urls: list[str]) -> list[str]:
        return _clean_attachments(urls)


class SupportMessageCreate(BaseModel):
    """A reply on an existing ticket."""

    body: str = Field(min_length=1)
    attachments: list[str] = Field(default_factory=list)

    @field_validator("body")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @field_validator("attachments")
    @classmethod
    def _attachments(cls, urls: list[str]) -> list[str]:
        return _clean_attachments(urls)


class SupportStatusUpdate(BaseModel):
    """The only field the master admin changes on a ticket."""

    status: TicketStatus


class SupportMessageRead(BaseModel):
    message_id: int
    author_user_id: int | None
    author_role: str
    author_name: str
    body: str
    attachments: list[str]
    created_at: datetime

    class Config:
        from_attributes = True


class SupportTicketRead(BaseModel):
    ticket_id: int
    school_id: int
    school_name: str | None
    created_by: int | None
    created_by_name: str
    subject: str
    status: str
    created_at: datetime | None
    updated_at: datetime | None
    message_count: int

    class Config:
        from_attributes = True


class SupportTicketDetail(SupportTicketRead):
    messages: list[SupportMessageRead]
