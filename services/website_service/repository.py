import re
from copy import deepcopy
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from common.models import WebsiteBuilderSite


def get_site(db: Session, school_id: int) -> WebsiteBuilderSite | None:
    return db.query(WebsiteBuilderSite).filter(
        WebsiteBuilderSite.school_id == school_id
    ).first()


def save_draft(db: Session, school_id: int, content: dict) -> WebsiteBuilderSite:
    site = get_site(db, school_id)
    if site is None:
        site = WebsiteBuilderSite(school_id=school_id, draft=content)
        db.add(site)
    else:
        site.draft = content
    db.commit()
    db.refresh(site)
    return site


def publish_site(db: Session, school_id: int) -> WebsiteBuilderSite | None:
    site = get_site(db, school_id)
    if site is None:
        return None
    site.published = deepcopy(site.draft)
    site.published_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(site)
    return site


def get_published_site(db: Session, school_id: int) -> WebsiteBuilderSite | None:
    site = get_site(db, school_id)
    return site if site and site.published is not None else None


def find_published_site_by_slug(db: Session, slug: str) -> WebsiteBuilderSite | None:
    expected_slug = normalize_slug(slug)
    if not expected_slug:
        return None
    school_name = WebsiteBuilderSite.published["school_name"].astext
    school_slug = func.lower(
        func.btrim(
            func.regexp_replace(school_name, "[^a-zA-Z0-9]+", "-", "g"),
            "-",
        )
    )
    return db.query(WebsiteBuilderSite).filter(
        WebsiteBuilderSite.published.is_not(None),
        school_slug == expected_slug,
    ).first()


def normalize_slug(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
