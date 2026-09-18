from pydantic import BaseModel


class WebsiteSettingsUpdate(BaseModel):
    school_name: str | None = None
    tagline: str | None = None
    nav_links: str | None = None
    font_family: str | None = None
    font_size: str | None = None
    accent_color: str | None = None
    icon_url: str | None = None
    footer_address: str | None = None
    footer_phone: str | None = None
    footer_email: str | None = None
    footer_copyright: str | None = None


class WebsiteSettingsRead(BaseModel):
    school_id: int
    school_name: str
    tagline: str | None
    nav_links: str
    font_family: str
    font_size: str
    accent_color: str
    icon_url: str | None
    footer_address: str | None
    footer_phone: str | None
    footer_email: str | None
    footer_copyright: str | None
    is_active: bool

    class Config:
        from_attributes = True


class WebsitePageUpsert(BaseModel):
    banner_url: str | None = None
    heading: str
    subheading: str | None = None
    body: str | None = None
    extra_json: dict | None = None


class WebsitePageRead(BaseModel):
    page_id: int
    school_id: int
    slug: str
    banner_url: str | None
    heading: str
    subheading: str | None
    body: str | None
    extra_json: dict | None

    class Config:
        from_attributes = True


class TestimonialCreate(BaseModel):
    name: str
    role: str
    quote: str


class TestimonialRead(BaseModel):
    testimonial_id: int
    school_id: int
    name: str
    role: str
    quote: str

    class Config:
        from_attributes = True


class PublicSiteRead(BaseModel):
    settings: WebsiteSettingsRead
    pages: dict[str, WebsitePageRead]
    testimonials: list[TestimonialRead]
