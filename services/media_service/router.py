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
from common.storage import (
    ALLOWED_VIDEO_TYPES,
    delete_stored_media,
    media_type_for_filename,
    resolve_upload_path,
    save_media,
)
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
            # Ownership is what later lets this uploader edit or remove the row,
            # so it is recorded here from the session and never from the client.
            "uploader_user_id": current_user.user_id,
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


@router.patch("/{media_id}", response_model=MediaRead)
async def update_media(
    media_id: int,
    title: str | None = Form(default=None),
    class_id: int | None = Form(default=None),
    set_class_id: bool = Form(default=False),
    file: UploadFile | None = File(default=None),
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("teacher", "admin")),
):
    """Edit gallery metadata, optionally replacing the file.

    Role gates which endpoints exist; the ownership check inside
    get_media_for_write is what stops a teacher reaching a colleague's row, so
    it stays there rather than being duplicated per handler.
    """
    media = repo.get_media_for_write(db, school_id, media_id, current_user)

    if title is not None and not title.strip():
        raise AppError("Title cannot be empty")
    if not (title is not None or set_class_id or file is not None):
        raise AppError("Nothing to update")

    new_file_url = None
    media_kind = None
    if file is not None and file.filename:
        content_type = file.content_type or ""
        data = await file.read()
        try:
            filename = save_media(data, content_type)
        except ValueError as exc:
            raise AppError(str(exc)) from exc
        new_file_url = f"/api/v1/media/files/{filename}"
        media_kind = _media_kind_for(content_type)

    previous_file_url = media.file_url
    updated = repo.update_media(
        db,
        media_id,
        media,
        title=title,
        class_id=class_id,
        set_class_id=set_class_id,
        file_url=new_file_url,
        media_kind=media_kind,
    )
    # Only unlink after the row is committed and no longer points at the old
    # file, so a failure here can never leave a dangling reference.
    if new_file_url is not None:
        delete_stored_media(previous_file_url)
    return updated


@router.delete("/{media_id}", response_model=MediaRead)
def delete_media(
    media_id: int,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("teacher", "admin")),
):
    # Soft delete, so the file is intentionally left on disk: an admin can still
    # restore or inspect the entry, and unreferenced files are a storage-cleanup
    # concern rather than a correctness one.
    return repo.delete_media(db, school_id, media_id, current_user)