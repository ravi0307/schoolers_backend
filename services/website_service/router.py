import re

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from common.config import settings
from common.database import get_db
from common.dependencies import CurrentUser, require_role, require_school_scope
from common.exceptions import AppError, NotFoundError
from common.storage import (
    media_type_for_filename,
    resolve_upload_path,
    save_image,
)
import repository as repo
from schemas import PublishedWebsite, WebsiteBuilderContent, WebsiteBuilderState

router = APIRouter(prefix="/website", tags=["website-builder"])
public_router = APIRouter(prefix="/public/sites", tags=["public-website"])

WEBSITE_IMAGE_TYPES = {
    "image/jpeg": b"\xff\xd8\xff",
    "image/png": b"\x89PNG\r\n\x1a\n",
    "image/gif": (b"GIF87a", b"GIF89a"),
    "image/webp": b"RIFF",
}
WEBSITE_ASSET_NAME = re.compile(r"website_\d+_\d{8}_\d{6}_\d+\.(?:jpg|png|gif|webp)")


def _site_state(site) -> WebsiteBuilderState:
    return WebsiteBuilderState(
        draft=site.draft if site else None,
        updated_at=site.modified_at if site else None,
        published_at=site.published_at if site else None,
    )


def _published_response(site) -> PublishedWebsite:
    content = site.published
    return PublishedWebsite(
        school_id=site.school_id,
        school_name=content["school_name"],
        canvas_size=content["canvas_size"],
        nodes=content["nodes"],
        testimonials=content["testimonials"],
        published_at=site.published_at,
    )


@router.get("/builder", response_model=WebsiteBuilderState)
def get_builder(
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    return _site_state(repo.get_site(db, school_id))


@router.put("/builder/draft", response_model=WebsiteBuilderState)
def save_builder_draft(
    payload: WebsiteBuilderContent,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    content = payload.as_json()
    if len(str(content).encode("utf-8")) > 1_000_000:
        raise AppError("Website content must be 1 MB or smaller")
    return _site_state(repo.save_draft(db, school_id, content))


@router.post("/builder/publish", response_model=PublishedWebsite)
def publish_builder(
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    site = repo.publish_site(db, school_id)
    if site is None:
        raise NotFoundError("Save a website draft before publishing")
    return _published_response(site)


@router.post("/builder/assets")
async def upload_builder_asset(
    file: UploadFile = File(...),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    content_type = file.content_type or ""
    signature = WEBSITE_IMAGE_TYPES.get(content_type)
    if signature is None:
        raise AppError("Only JPEG, PNG, GIF, and WebP images are allowed")
    data = await file.read(settings.UPLOAD_MAX_BYTES + 1)
    if len(data) > settings.UPLOAD_MAX_BYTES:
        raise AppError("Image must be 5 MB or smaller")
    if not _matches_image_signature(content_type, data, signature):
        raise AppError("The uploaded file does not match its image type")
    try:
        filename = save_image(data, content_type, filename_prefix=f"website_{school_id}")
    except ValueError as exc:
        raise AppError(str(exc)) from exc
    return {"url": f"/api/v1/website/builder/assets/{filename}"}


@router.get("/builder/assets/{filename}")
def serve_builder_asset(filename: str):
    if not WEBSITE_ASSET_NAME.fullmatch(filename):
        raise NotFoundError("Image not found")
    try:
        path = resolve_upload_path(filename)
    except FileNotFoundError:
        raise NotFoundError("Image not found") from None
    return FileResponse(path, media_type=media_type_for_filename(filename))


def _matches_image_signature(content_type: str, data: bytes, signature: bytes | tuple[bytes, ...]) -> bool:
    if content_type == "image/webp":
        return len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP"
    signatures = signature if isinstance(signature, tuple) else (signature,)
    return any(data.startswith(item) for item in signatures)


@public_router.get("/by-name/{school_name}", response_model=PublishedWebsite)
def public_site_by_name(school_name: str, db: Session = Depends(get_db)):
    site = repo.find_published_site_by_slug(db, school_name)
    if site is None:
        raise NotFoundError("This school has not published a website yet")
    return _published_response(site)


@public_router.get("/{school_id}", response_model=PublishedWebsite)
def public_site(school_id: int, db: Session = Depends(get_db)):
    site = repo.get_published_site(db, school_id)
    if site is None:
        raise NotFoundError("This school has not published a website yet")
    return _published_response(site)
