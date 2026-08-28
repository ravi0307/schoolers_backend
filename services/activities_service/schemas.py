from pydantic import BaseModel


class ActivityCreate(BaseModel):
    tag: str
    title: str
    description: str | None = None


class ActivityRead(BaseModel):
    activity_id: int
    school_id: int
    tag: str
    title: str
    description: str | None

    class Config:
        from_attributes = True
