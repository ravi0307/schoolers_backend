from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


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


class BuilderNode(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str = Field(min_length=1, max_length=100)
    anchorId: str = Field(default="", max_length=80)
    type: Literal["header", "footer", "testimonial", "testimonials", "banner", "contact", "center"]
    title: str = Field(default="Content", max_length=120)
    x: float = Field(ge=0, le=100)
    y: float = Field(ge=0, le=100)
    width: float = Field(gt=0, le=100)
    height: float = Field(gt=0, le=100)
    html: str = Field(default="", max_length=50000)
    slides: list[BannerSlide] = Field(default_factory=list, max_length=20)
    labels: list[BuilderLabel] = Field(default_factory=list, max_length=100)


class BuilderTestimonial(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str = Field(min_length=1, max_length=100)
    name: str = Field(default="School community", max_length=120)
    role: str = Field(default="", max_length=120)
    quote: str = Field(default="", max_length=2000)


class WebsiteBuilderContent(BaseModel):
    school_name: str = Field(min_length=1, max_length=150)
    canvas_size: CanvasSize
    nodes: list[BuilderNode] = Field(max_length=100)
    testimonials: list[BuilderTestimonial] = Field(max_length=500)
    pending_testimonials: list[BuilderTestimonial] = Field(max_length=500)

    def as_json(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True)


class WebsiteBuilderState(BaseModel):
    draft: dict[str, Any] | None
    updated_at: datetime | None
    published_at: datetime | None


class PublishedWebsite(BaseModel):
    school_id: int
    school_name: str
    canvas_size: dict[str, int]
    nodes: list[dict[str, Any]]
    testimonials: list[dict[str, Any]]
    published_at: datetime
