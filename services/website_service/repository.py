from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy import func

from common.models import WebsiteSettings, WebsitePage, WebsiteTestimonial


def get_settings(db: Session, school_id: int) -> WebsiteSettings | None:
    return db.query(WebsiteSettings).filter(WebsiteSettings.school_id == school_id, WebsiteSettings.is_active.is_(True)).first()


def get_settings_any(db: Session, school_id: int) -> WebsiteSettings | None:
    return db.query(WebsiteSettings).filter(WebsiteSettings.school_id == school_id).first()


# URL-safe slug derived from a school's website name, e.g. "Sunrise Public
# School" -> "sunrise-public-school". Must stay in sync with the frontend slug
# helper (lowercase alphanumerics joined by single hyphens).
def _normalized_slug(column):
    return func.lower(func.btrim(func.regexp_replace(column, "[^a-zA-Z0-9]+", "-", "g"), "-"))


def find_settings_by_slug(db: Session, slug: str) -> WebsiteSettings | None:
    return db.query(WebsiteSettings).filter(
        _normalized_slug(WebsiteSettings.school_name) == (slug or "").lower(),
        WebsiteSettings.is_active.is_(True),
    ).first()


def upsert_settings(db: Session, school_id: int, data: dict, default_name: str) -> WebsiteSettings:
    settings = get_settings_any(db, school_id)
    if settings:
        for k, v in data.items():
            if v is not None:
                setattr(settings, k, v)
    else:
        settings = WebsiteSettings(school_id=school_id, school_name=data.get("school_name") or default_name, **{
            k: v for k, v in data.items() if k != "school_name"
        })
        db.add(settings)
    db.commit()
    db.refresh(settings)
    return settings


def get_page(db: Session, school_id: int, slug: str) -> WebsitePage | None:
    return db.query(WebsitePage).filter(WebsitePage.school_id == school_id, WebsitePage.slug == slug, WebsitePage.is_active.is_(True)).first()


def list_pages(db: Session, school_id: int) -> list[WebsitePage]:
    return db.query(WebsitePage).filter(WebsitePage.school_id == school_id, WebsitePage.is_active.is_(True)).all()


def upsert_page(db: Session, school_id: int, slug: str, data: dict) -> WebsitePage:
    page = get_page(db, school_id, slug)
    if page:
        for k, v in data.items():
            if v is not None:
                setattr(page, k, v)
    else:
        page = WebsitePage(school_id=school_id, slug=slug, **data)
        db.add(page)
    db.commit()
    db.refresh(page)
    return page


def list_testimonials(db: Session, school_id: int) -> list[WebsiteTestimonial]:
    return db.query(WebsiteTestimonial).filter(WebsiteTestimonial.school_id == school_id, WebsiteTestimonial.is_active.is_(True)).all()


def add_testimonial(db: Session, school_id: int, data: dict) -> WebsiteTestimonial:
    t = WebsiteTestimonial(school_id=school_id, **data)
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def delete_testimonial(db: Session, school_id: int, testimonial_id: int) -> None:
    testimonial = db.query(WebsiteTestimonial).filter(
        WebsiteTestimonial.school_id == school_id,
        WebsiteTestimonial.testimonial_id == testimonial_id,
        WebsiteTestimonial.is_active.is_(True),
    ).first()
    if testimonial:
        testimonial.is_active = False
    db.commit()


def site_payload(db: Session, school_id: int, settings: WebsiteSettings) -> dict:
    pages = {page.slug: page for page in list_pages(db, school_id)}
    testimonials = list_testimonials(db, school_id)
    return {
        "settings": settings,
        "pages": pages,
        "testimonials": testimonials,
    }


def publish_site(db: Session, school_id: int) -> dict | None:
    settings = get_settings_any(db, school_id)
    if not settings:
        return None

    if not settings.is_active:
        settings.is_active = True
        db.commit()
        db.refresh(settings)

    return site_payload(db, school_id, settings)
