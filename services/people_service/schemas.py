from datetime import date
from pydantic import BaseModel


class TeacherCreate(BaseModel):
    name: str
    role_title: str
    phone: str
    email: str | None = None


class TeacherUpdate(BaseModel):
    name: str | None = None
    role_title: str | None = None
    phone: str | None = None
    email: str | None = None
    attendance_status: str | None = None


class TeacherRead(BaseModel):
    teacher_id: int
    school_id: int
    name: str
    role_title: str
    phone: str
    email: str | None
    attendance_status: str

    class Config:
        from_attributes = True


class StaffCreate(BaseModel):
    name: str
    role: str
    phone: str | None = None


class StaffUpdate(BaseModel):
    name: str | None = None
    role: str | None = None
    phone: str | None = None


class StaffRead(BaseModel):
    staff_id: int
    school_id: int
    name: str
    role: str
    phone: str | None

    class Config:
        from_attributes = True


class ParentCreate(BaseModel):
    name: str
    phone: str
    email: str | None = None


class ParentRead(BaseModel):
    parent_id: int
    school_id: int
    name: str
    phone: str
    email: str | None

    class Config:
        from_attributes = True


class StudentCreate(BaseModel):
    class_id: int
    admission_no: str | None = None
    name: str
    date_of_birth: date | None = None
    gender: str | None = None
    parent_id: int | None = None  # optionally link on create


class StudentUpdate(BaseModel):
    class_id: int | None = None
    admission_no: str | None = None
    name: str | None = None
    date_of_birth: date | None = None
    gender: str | None = None
    present_today: bool | None = None


class StudentRead(BaseModel):
    student_id: int
    school_id: int
    class_id: int
    admission_no: str
    name: str
    date_of_birth: date | None
    gender: str | None
    present_today: bool

    class Config:
        from_attributes = True


class TeacherClassSubjectCreate(BaseModel):
    teacher_id: int
    class_id: int
    subject_id: int
    is_class_teacher: bool = False
