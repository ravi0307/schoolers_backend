from typing import Literal
from datetime import datetime

from pydantic import BaseModel, Field, model_validator


class BroadcastCreate(BaseModel):
    scope: Literal["school", "class", "route", "pilot"]
    class_id: int | None = None
    route_id: int | None = None
    role_name: str = Field(min_length=1)
    sender_name: str = Field(min_length=1)
    message: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_scope_target(self):
        if self.scope == "class" and self.class_id is None:
            raise ValueError("class_id is required for class broadcasts")
        if self.scope == "route" and self.route_id is None:
            raise ValueError("route_id is required for route broadcasts")
        if self.scope != "class" and self.class_id is not None:
            raise ValueError("class_id is only supported for class broadcasts")
        if self.scope != "route" and self.route_id is not None:
            raise ValueError("route_id is only supported for route broadcasts")
        return self


class BroadcastUpdate(BaseModel):
    message: str = Field(min_length=1)
    created_at: datetime | None = None


class BroadcastRead(BaseModel):
    broadcast_id: int
    school_id: int
    class_id: int | None
    route_id: int | None
    scope: str
    role_name: str
    sender_name: str
    message: str
    created_at: datetime

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
