from datetime import datetime

from pydantic import BaseModel


class MediaCreate(BaseModel):
    class_id: int | None = None
    title: str
    file_url: str
    media_kind: str


class MediaRead(BaseModel):
    media_id: int
    school_id: int
    class_id: int | None
    title: str
    posted_by: str
    icon: str | None
    file_url: str | None
    media_kind: str | None
    # Exposed so the UI can decide which items the signed-in user may manage.
    # It is a user id, not staff_id: teachers manage their own uploads by
    # account. Legacy rows are NULL and therefore stay admin-only.
    uploader_user_id: int | None = None
    created_at: datetime | None

    class Config:
        from_attributes = True