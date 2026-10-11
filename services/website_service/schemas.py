from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CanvasSize(BaseModel):
    width: int = Field(ge=650, le=5000)
    height: int = Field(ge=560, le=5000)


class BuilderLabel(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str = Field(min_length=1, max_length=100)
    text: str = Field(default="", max_length=80)
    anchorId: str = Field(default="", max_length=80)


class BannerSlide(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str = Field(min_length=1, max_length=100)
    imageUrl: str = Field(default="", max_length=2000)
    title: str = Field(default="", max_length=200)
    subtitle: str = Field(default="", max_length=500)


class SchoolIdentityData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schoolName: str = Field(default="Blue Horizon Academy", max_length=120)
    tagline: str = Field(default="A place to learn and grow", max_length=240)
    logoUrl: str = Field(default="", max_length=2000)


class BuilderNode(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str = Field(min_length=1, max_length=100)
    anchorId: str = Field(default="", max_length=80)
    type: Literal["header", "footer", "testimonial", "testimonials", "banner", "contact", "center", "school-profile", "school-identity"]
    title: str = Field(default="Content", max_length=120)
    x: float = Field(ge=0, le=100)
    y: float = Field(ge=0, le=100)
    width: float = Field(gt=0, le=100)
    height: float = Field(gt=0, le=100)
    html: str = Field(default="", max_length=50000)
    boxBgColor: str | None = Field(
        default=None,
        max_length=80,
        pattern=r"^(#[0-9a-fA-F]{3,8}|rgba?\(\s*[\d.]+%?\s*,\s*[\d.]+%?\s*,\s*[\d.]+%?(?:\s*,\s*(?:0|1|0?\.\d+))?\s*\))$",
    )
    backgroundColor: str = Field(
        default="#ffffff",
        max_length=80,
        pattern=r"^(#[0-9a-fA-F]{3,8}|rgba?\(\s*[\d.]+%?\s*,\s*[\d.]+%?\s*,\s*[\d.]+%?(?:\s*,\s*(?:0|1|0?\.\d+))?\s*\))$",
    )
    backgroundOpacity: float = Field(default=1, ge=0, le=1)
    data: SchoolIdentityData | None = None
    slides: list[BannerSlide] = Field(default_factory=list, max_length=20)
    labels: list[BuilderLabel] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def require_school_identity_data(self):
        if self.type == "school-identity" and self.data is None:
            raise ValueError("School identity nodes require school name, tagline, and logo data")
        return self


class BuilderTestimonial(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str = Field(min_length=1, max_length=100)
    name: str = Field(default="School community", max_length=120)
    role: str = Field(default="", max_length=120)
    quote: str = Field(default="", max_length=2000)


class WebsiteBuilderContent(BaseModel):
    school_name: str = Field(min_length=1, max_length=150)
    canvas_size: CanvasSize
    canvas_background: str = Field(
        default="#ffffff",
        max_length=80,
        pattern=r"^(#[0-9a-fA-F]{3,8}|rgba?\(\s*[\d.]+%?\s*,\s*[\d.]+%?\s*,\s*[\d.]+%?(?:\s*,\s*(?:0|1|0?\.\d+))?\s*\))$",
    )
    nodes: list[BuilderNode] = Field(max_length=100)
    testimonials: list[BuilderTestimonial] = Field(max_length=500)
    pending_testimonials: list[BuilderTestimonial] = Field(max_length=500)

    def as_json(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, exclude_none=True)


class WebsiteBuilderState(BaseModel):
    draft: dict[str, Any] | None
    published: dict[str, Any] | None = None
    updated_at: datetime | None
    published_at: datetime | None


class WebsiteQueryCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=120)
    email: str = Field(
        min_length=3,
        max_length=254,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    )
    message: str = Field(min_length=1, max_length=5000)


class WebsiteQueryRead(BaseModel):
    query_id: int
    school_id: int
    name: str
    email: str
    message: str
    created_at: datetime


class PublishedWebsite(BaseModel):
    school_id: int
    school_name: str
    canvas_size: dict[str, int]
    canvas_background: str = "#ffffff"
    nodes: list[dict[str, Any]]
    testimonials: list[dict[str, Any]]
    published_at: datetime
