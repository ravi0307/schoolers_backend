from pydantic import BaseModel


class NotificationCreate(BaseModel):
    type: str  # 'Dues' | 'Activation' | 'General'
    message: str


class NotificationRead(BaseModel):
    notification_id: int
    school_id: int
    type: str
    message: str
    status: str

    class Config:
        from_attributes = True
