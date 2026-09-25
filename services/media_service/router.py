"""
Schoolers Media Service — the school photo/video gallery.

Admins and teachers upload photos and short videos; parents can browse the
gallery read-only. Files are stored through the shared storage helpers and
served back under /media/files/{filename} so <img>/<video> tags render them
without needing an Authorization header (mirrors the schools uploads route).
"""
from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from common.database import get_db
from common.dependencies import require_role, require_school_scope, CurrentUser
from common.exceptions import AppError, NotFoundError
from common.storage import ALLOWED_VIDEO_TYPES, media_type_for_filename, resolve_upload_path, save_media
import repository as repo
from schemas import MediaRead

router = APIRouter(prefix="/media", tags=["media"])


def _media_kind_for(content_type: str) -> str:
    return "video" if content_type in ALLOWED_VIDEO_TYPES else "image"


@router.post("", response_model=MediaRead, status_code=201)
async def upload_media(
    title: str = Form(...),
    class_id: int | None = Form(default=None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("teacher", "admin")),
):
    content_type = file.content_type or ""
    data = await file.read()
    try:
        filename = save_media(data, content_type)
    except ValueError as exc:
        raise AppError(str(exc)) from exc

    return repo.create_media(
        db,
        school_id,
        {
            "title": title,
            "posted_by": repo.resolve_poster_name(db, current_user),
            "class_id": class_id,
            "file_url": f"/api/v1/media/files/{filename}",
            "media_kind": _media_kind_for(content_type),
        },
    )


@router.get("", response_model=list[MediaRead])
def list_media(
    class_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("parent", "teacher", "admin")),
):
    return repo.list_media(db, school_id, class_id)


@router.get("/files/{filename}")
def serve_media_file(filename: str):
    try:
        path = resolve_upload_path(filename)
    except FileNotFoundError:
        raise NotFoundError("File not found") from None
    return FileResponse(path, media_type=media_type_for_filename(filename))


@router.delete("/{media_id}", response_model=MediaRead)
def delete_media(
    media_id: int,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    return repo.delete_media(db, school_id, media_id)