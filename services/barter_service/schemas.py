from pydantic import BaseModel


class BarterCreate(BaseModel):
    title: str
    price: str
    icon: str | None = None
    listed_by: str


class BarterUpdate(BaseModel):
    title: str | None = None
    price: str | None = None
    icon: str | None = None


class BarterRead(BaseModel):
    listing_id: int
    school_id: int
    title: str
    price: str
    icon: str | None
    listed_by: str

    class Config:
        from_attributes = True
