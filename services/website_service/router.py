from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from common.database import get_db
from common.dependencies import require_role, require_school_scope, CurrentUser
from common.exceptions import AppError, NotFoundError
from common.storage import (
    ALLOWED_IMAGE_TYPES,
    media_type_for_filename,
    resolve_upload_path,
    save_image,
)
import repository as repo
from schemas import (
    WebsiteSettingsUpdate, WebsiteSettingsRead,
    WebsitePageUpsert, WebsitePageRead,
    TestimonialCreate, TestimonialRead, PublicSiteRead,
)

router = APIRouter(prefix="/website", tags=["website"])

VALID_SLUGS = {"home", "about", "academics", "admissions", "contact"}


# ---- Admin-authenticated editing ----
@router.get("/settings", response_model=WebsiteSettingsRead)
def get_settings(
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    settings = repo.get_settings_any(db, school_id)
    if not settings:
        raise NotFoundError("Website settings not yet created for this school")
    return settings


@router.put("/settings", response_model=WebsiteSettingsRead)
def update_settings(
    payload: WebsiteSettingsUpdate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    return repo.upsert_settings(db, school_id, payload.model_dump(exclude_unset=True), default_name="My School")


@router.get("/pages/{slug}", response_model=WebsitePageRead)
def get_page(
    slug: str,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    page = repo.get_page(db, school_id, slug)
    if not page:
        raise NotFoundError(f"Page '{slug}' not yet created")
    return page


@router.put("/pages/{slug}", response_model=WebsitePageRead)
def upsert_page(
    slug: str,
    payload: WebsitePageUpsert,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    if slug not in VALID_SLUGS:
        raise NotFoundError(f"Unknown page slug '{slug}'")
    return repo.upsert_page(db, school_id, slug, payload.model_dump(exclude_unset=True))


@router.get("/testimonials", response_model=list[TestimonialRead])
def list_testimonials(
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    return repo.list_testimonials(db, school_id)


@router.post("/testimonials", response_model=TestimonialRead, status_code=201)
def add_testimonial(
    payload: TestimonialCreate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    return repo.add_testimonial(db, school_id, payload.model_dump())


@router.delete("/testimonials/{testimonial_id}", status_code=204)
def delete_testimonial(
    testimonial_id: int,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    repo.delete_testimonial(db, school_id, testimonial_id)


@router.post("/go-live", response_model=PublicSiteRead)
def go_live(
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    site = repo.publish_site(db, school_id)
    if not site:
        raise NotFoundError("Website settings not yet created for this school")
    return site


@router.post("/uploads")
async def upload_image(
    file: UploadFile = File(...),
    current_user: CurrentUser = Depends(require_role("admin", "teacher", "master")),
):
    content_type = file.content_type or ""
    if content_type not in ALLOWED_IMAGE_TYPES:
        raise AppError("Only JPEG, PNG, GIF, WebP, and SVG images are allowed")
    data = await file.read()
    try:
        filename = save_image(data, content_type)
    except ValueError as exc:
        raise AppError(str(exc)) from exc
    return {"url": f"/api/v1/website/uploads/{filename}", "filename": filename}


@router.get("/uploads/{filename}")
def serve_upload(filename: str):
    try:
        path = resolve_upload_path(filename)
    except FileNotFoundError:
        raise NotFoundError("File not found") from None
    return FileResponse(path, media_type=media_type_for_filename(filename))


# ---- Public, unauthenticated read-only site ----
public_router = APIRouter(prefix="/public/sites", tags=["public-website"])


def _site_response(db: Session, settings) -> dict:
    site = repo.site_payload(db, settings.school_id, settings)
    return {
        "settings": WebsiteSettingsRead.model_validate(site["settings"]),
        "pages": {
            slug: WebsitePageRead.model_validate(page)
            for slug, page in site["pages"].items()
        },
        "testimonials": [
            TestimonialRead.model_validate(testimonial)
            for testimonial in site["testimonials"]
        ],
    }


@public_router.get("/by-name/{school_name}")
def public_site_by_name(school_name: str, db: Session = Depends(get_db)):
    settings = repo.find_settings_by_slug(db, school_name)
    if not settings:
        raise NotFoundError("This school has not published a website yet")
    return _site_response(db, settings)


@public_router.get("/{school_id}")
def public_site(school_id: int, db: Session = Depends(get_db)):
    settings = repo.get_settings(db, school_id)
    if not settings:
        raise NotFoundError("This school has not published a website yet")
    return _site_response(db, settings)
