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

    class Config:
        from_attributes = True