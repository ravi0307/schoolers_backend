from pydantic import BaseModel


class BroadcastCreate(BaseModel):
    scope: str  # 'school' | 'class' | 'pilot'
    class_id: int | None = None
    from_name: str
    message: str


class BroadcastRead(BaseModel):
    broadcast_id: int
    school_id: int
    class_id: int | None
    scope: str
    from_name: str
    message: str

    class Config:
        from_attributes = True


class MediaCreate(BaseModel):
    class_id: int | None = None
    title: str
    posted_by: str
    icon: str | None = None


class MediaRead(BaseModel):
    media_id: int
    school_id: int
    class_id: int | None
    title: str
    posted_by: str
    icon: str | None

    class Config:
        from_attributes = True
