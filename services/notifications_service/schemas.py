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
    email_recipients: list[str] = []

    class Config:
        from_attributes = True

    @classmethod
    def from_notification(cls, notification: object) -> "NotificationRead":
        recipients = getattr(notification, "_email_recipients", [])
        return cls(
            notification_id=notification.notification_id,
            school_id=notification.school_id,
            type=notification.type,
            message=notification.message,
            status=notification.status,
            email_recipients=recipients,
        )
