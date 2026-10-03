"""
Tests for the school photo/video gallery service.

Runs against an in-memory SQLite database and pounds the media_service
repository directly. The router keeps the project's bare `import repository`
convention, so its request handlers are executed end-to-end here (like the
auth-service tests) and its decorator surface is pinned via AST.
"""
import asyncio
import ast
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from common.config import settings
from common.dependencies import CurrentUser
from common.exceptions import AppError, ForbiddenError, NotFoundError
from common.models import Base, Media, Staff
from common.storage import (
    ALLOWED_MEDIA_TYPES,
    ALLOWED_VIDEO_TYPES,
    delete_stored_media,
    media_type_for_filename,
    resolve_upload_path,
    save_media,
)
import services.media_service.repository as repo
from services.media_service.schemas import MediaRead

_ENGINES = []

ROOT = Path(__file__).resolve().parent.parent
ROUTER_SOURCE = ROOT / "services" / "media_service" / "router.py"

# The full metadata includes Postgres-only JSONB columns (website_pages) that
# SQLite cannot render; mirror the other repo tests by creating just the
# tables the gallery exercises (plus their FK dependencies).
GALLERY_TABLES = [
    t
    for t in Base.metadata.sorted_tables
    if not any(col.type.__class__.__name__ == "JSONB" for col in t.columns)
]


def seed(db: Session):
    db.add_all(
        [
            Media(
                media_id=1,
                school_id=1,
                class_id=None,
                title="Annual day practice",
                posted_by="Ms. Dora",
                uploader_user_id=4,
                file_url="/api/v1/media/files/image_a.jpg",
                media_kind="image",
            ),
            Media(
                media_id=2,
                school_id=1,
                class_id=5,
                title="Science fair setup",
                posted_by="Mr. Tripathi",
                uploader_user_id=7,
                file_url="/api/v1/media/files/video_a.mp4",
                media_kind="video",
            ),
            # Predates uploader_user_id: must stay admin-only, never guessed at.
            Media(
                media_id=5,
                school_id=1,
                title="Legacy unattributed upload",
                posted_by="Mr. Tripathi",
                uploader_user_id=None,
                file_url="/api/v1/media/files/image_legacy.jpg",
                media_kind="image",
            ),
            Media(
                media_id=3,
                school_id=2,
                title="Other school",
                posted_by="S2 Admin",
                file_url="/api/v1/media/files/image_b.jpg",
                media_kind="image",
            ),
            Media(
                media_id=4,
                school_id=1,
                title="Removed entry",
                posted_by="Ms. Dora",
                is_active=False,
                file_url="/api/v1/media/files/image_c.jpg",
                media_kind="image",
            ),
            Staff(staff_id=9, school_id=1, name="Ms. Dora", role="Other"),
            Staff(staff_id=7, school_id=1, name="Mr. Tripathi", role="Teacher",
                  person_type="teacher", phone="12345"),
        ]
    )
    db.commit()


def tearDownModule():
    for engine in _ENGINES:
        engine.dispose()


def _teacher(user_id):
    return CurrentUser(user_id=user_id, role="teacher", school_id=1, linked_person_id=7)


def _admin(user_id=4):
    return CurrentUser(user_id=user_id, role="admin", school_id=1)


def _other_school_admin():
    return CurrentUser(user_id=8, role="admin", school_id=2)


class MediaGalleryRepositoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite://")
        _ENGINES.append(cls.engine)
        cls.Session = sessionmaker(bind=cls.engine)

    def setUp(self):
        Base.metadata.drop_all(self.engine, tables=GALLERY_TABLES)
        Base.metadata.create_all(self.engine, tables=GALLERY_TABLES)
        self.session = self.Session()
        seed(self.session)

    def tearDown(self):
        self.session.close()

    def test_create_media_persists_file_and_kind(self):
        created = repo.create_media(
            self.session,
            1,
            {
                "title": "Fun day",
                "posted_by": "Ms. Dora",
                "class_id": None,
                "file_url": "/api/v1/media/files/image_x.jpg",
                "media_kind": "image",
            },
        )
        self.assertEqual(created.school_id, 1)
        self.assertEqual(created.title, "Fun day")
        self.assertEqual(created.file_url, "/api/v1/media/files/image_x.jpg")
        self.assertEqual(created.media_kind, "image")

    def test_media_read_surfaces_created_at(self):
        """MediaRead exposes created_at so gallery albums can display timestamps."""
        created = repo.create_media(
            self.session,
            1,
            {
                "title": "Timestamped photo",
                "posted_by": "Ms. Dora",
                "class_id": None,
                "file_url": "/api/v1/media/files/img.png",
                "media_kind": "image",
            },
        )
        read = MediaRead.model_validate(created)
        self.assertIsNotNone(read.created_at)
        self.assertIsInstance(read.created_at, datetime)

    def test_list_media_scopes_to_school_and_skips_inactive(self):
        rows = repo.list_media(self.session, 1)
        ids = {m.media_id for m in rows}
        self.assertEqual(ids, {1, 2, 5})
        self.assertNotIn(3, ids)
        self.assertNotIn(4, ids)

    def test_list_media_filters_by_class(self):
        rows = repo.list_media(self.session, 1, class_id=5)
        self.assertEqual([m.media_id for m in rows], [2])

    def test_delete_media_soft_deletes_in_school(self):
        removed = repo.delete_media(self.session, 1, 1, _admin())
        self.assertFalse(removed.is_active)
        remaining = {m.media_id for m in repo.list_media(self.session, 1)}
        self.assertNotIn(1, remaining)

    def test_delete_media_rejects_other_school(self):
        with self.assertRaises(NotFoundError):
            repo.delete_media(self.session, 1, 3, _admin())

    def test_staff_can_delete_own_upload(self):
        removed = repo.delete_media(self.session, 1, 2, _teacher(7))
        self.assertFalse(removed.is_active)
        self.assertEqual(removed.media_id, 2)

    def test_staff_cannot_delete_colleagues_upload(self):
        """The core staff rule: ownership, not role, decides."""
        with self.assertRaises(ForbiddenError):
            repo.delete_media(self.session, 1, 1, _teacher(7))
        self.assertTrue(self.session.get(Media, 1).is_active)

    def test_admin_can_delete_any_media_in_own_school(self):
        removed = repo.delete_media(self.session, 1, 2, _admin())
        self.assertFalse(removed.is_active)

    def test_admin_cannot_delete_media_in_other_school(self):
        """school_id filtering must win over the admin role."""
        with self.assertRaises(NotFoundError):
            repo.delete_media(self.session, 1, 3, _other_school_admin())

    def test_staff_cannot_delete_legacy_unattributed_media(self):
        """Rows with no uploader stay admin-only instead of being guessed at."""
        with self.assertRaises(ForbiddenError):
            repo.delete_media(self.session, 1, 5, _teacher(7))
        self.assertTrue(self.session.get(Media, 5).is_active)

    def test_staff_can_edit_own_upload(self):
        media = repo.get_media_for_write(self.session, 1, 2, _teacher(7))
        updated = repo.update_media(self.session, 2, media, title="Science fair winners")
        self.assertEqual(updated.title, "Science fair winners")

    def test_staff_cannot_edit_colleagues_upload(self):
        with self.assertRaises(ForbiddenError):
            repo.get_media_for_write(self.session, 1, 1, _teacher(7))

    def test_admin_can_edit_any_media_in_own_school(self):
        media = repo.get_media_for_write(self.session, 1, 2, _admin())
        updated = repo.update_media(self.session, 2, media, title="Corrected caption")
        self.assertEqual(updated.title, "Corrected caption")

    def test_update_media_clears_class_only_when_requested(self):
        """None is a real value (clear the class), so edits are explicit."""
        media = repo.get_media_for_write(self.session, 1, 2, _admin())
        repo.update_media(self.session, 2, media, title="Class kept")
        self.assertEqual(self.session.get(Media, 2).class_id, 5)

        media = repo.get_media_for_write(self.session, 1, 2, _admin())
        repo.update_media(self.session, 2, media, class_id=None, set_class_id=True)
        self.assertIsNone(self.session.get(Media, 2).class_id)

    def test_get_media_for_write_rejects_inactive_row(self):
        with self.assertRaises(NotFoundError):
            repo.get_media_for_write(self.session, 1, 4, _admin())

    def test_media_read_exposes_uploader_user_id(self):
        read = MediaRead.model_validate(self.session.get(Media, 2))
        self.assertEqual(read.uploader_user_id, 7)
        self.assertIsNone(MediaRead.model_validate(self.session.get(Media, 5)).uploader_user_id)

    def test_resolve_poster_admin_staff_name(self):
        current_user = CurrentUser(user_id=1, role="admin", school_id=1, linked_person_id=9)
        self.assertEqual(repo.resolve_poster_name(self.session, current_user), "Ms. Dora")

    def test_resolve_poster_admin_fallback(self):
        current_user = CurrentUser(user_id=1, role="admin", school_id=1)
        self.assertEqual(repo.resolve_poster_name(self.session, current_user), "School Admin")

    def test_resolve_poster_teacher_name(self):
        current_user = CurrentUser(user_id=1, role="teacher", school_id=1, linked_person_id=7)
        self.assertEqual(repo.resolve_poster_name(self.session, current_user), "Mr. Tripathi")

    def test_resolve_poster_unlinked_teacher_raises(self):
        current_user = CurrentUser(user_id=1, role="teacher", school_id=1)
        with self.assertRaises(ForbiddenError):
            repo.resolve_poster_name(self.session, current_user)

    def test_list_media_orders_newest_first(self):
        base = datetime(2026, 9, 1, 9, 0)
        self.session.add_all(
            [
                Media(media_id=10, school_id=1, title="Oldest", posted_by="X",
                      file_url="/api/v1/media/files/image_a.jpg", media_kind="image",
                      created_at=base),
                Media(media_id=11, school_id=1, title="Middle", posted_by="X",
                      file_url="/api/v1/media/files/image_b.jpg", media_kind="image",
                      created_at=base + timedelta(minutes=5)),
                Media(media_id=12, school_id=1, title="Newest", posted_by="X",
                      file_url="/api/v1/media/files/video_c.mp4", media_kind="video",
                      created_at=base + timedelta(minutes=10)),
            ]
        )
        self.session.commit()
        titles = [m.title for m in repo.list_media(self.session, 1)]
        self.assertLess(titles.index("Newest"), titles.index("Middle"))
        self.assertLess(titles.index("Middle"), titles.index("Oldest"))

    def test_delete_media_is_idempotent(self):
        repo.delete_media(self.session, 1, 1, _admin())
        with self.assertRaises(NotFoundError):
            repo.delete_media(self.session, 1, 1, _admin())

    def test_resolve_poster_inactive_teacher_raises(self):
        teacher = self.session.query(Staff).filter_by(staff_id=7).one()
        teacher.is_active = False
        self.session.commit()
        current_user = CurrentUser(user_id=1, role="teacher", school_id=1, linked_person_id=7)
        with self.assertRaises(ForbiddenError):
            repo.resolve_poster_name(self.session, current_user)

    def test_resolve_poster_inactive_staff_falls_back(self):
        staff = self.session.query(Staff).filter_by(staff_id=9).one()
        staff.is_active = False
        self.session.commit()
        current_user = CurrentUser(user_id=1, role="admin", school_id=1, linked_person_id=9)
        self.assertEqual(repo.resolve_poster_name(self.session, current_user), "School Admin")


class MediaStorageTests(unittest.TestCase):
    def test_video_and_image_types_are_allowed(self):
        self.assertIn("video/mp4", ALLOWED_VIDEO_TYPES)
        self.assertIn("video/webm", ALLOWED_VIDEO_TYPES)
        self.assertIn("video/quicktime", ALLOWED_VIDEO_TYPES)
        self.assertIn("image/jpeg", ALLOWED_MEDIA_TYPES)
        self.assertIn("image/png", ALLOWED_MEDIA_TYPES)

    def test_save_media_stores_video_and_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("common.storage.get_upload_dir", return_value=Path(tmp)):
                video_name = save_media(b"\x00" * 16, "video/mp4", filename_prefix="sports")
                image_name = save_media(b"\x00" * 16, "image/png", filename_prefix="sports")
                self.assertTrue(video_name.startswith("video_sports_"))
                self.assertTrue(video_name.endswith(".mp4"))
                self.assertTrue(image_name.startswith("image_sports_"))
                self.assertTrue(image_name.endswith(".png"))
                self.assertTrue((Path(tmp) / video_name).is_file())
                self.assertTrue((Path(tmp) / image_name).is_file())

    def test_save_media_rejects_unknown_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("common.storage.get_upload_dir", return_value=Path(tmp)):
                with self.assertRaises(ValueError):
                    save_media(b"\x00" * 16, "application/pdf", filename_prefix="sports")

    def test_save_media_maps_mov_quicktime(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("common.storage.get_upload_dir", return_value=Path(tmp)):
                name = save_media(b"\x00" * 8, "video/quicktime", filename_prefix="clip")
                self.assertTrue(name.startswith("video_clip_"))
                self.assertTrue(name.endswith(".mov"))

    def test_save_media_rejects_oversize_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("common.storage.get_upload_dir", return_value=Path(tmp)):
                with self.assertRaisesRegex(ValueError, "5 MB"):
                    save_media(b"\x00" * (settings.UPLOAD_MAX_BYTES + 1), "image/png")

    def test_save_media_sanitizes_custom_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("common.storage.get_upload_dir", return_value=Path(tmp)):
                name = save_media(b"\x00" * 4, "video/mp4", filename_prefix="My School!!")
                self.assertRegex(name, r"^video_My_School_[\d_]+\.mp4$")

    def test_media_type_for_filename_maps_extensions(self):
        self.assertEqual(media_type_for_filename("a.jpg"), "image/jpeg")
        self.assertEqual(media_type_for_filename("a.png"), "image/png")
        self.assertEqual(media_type_for_filename("a.mp4"), "video/mp4")
        self.assertEqual(media_type_for_filename("a.mov"), "video/quicktime")
        self.assertIsNone(media_type_for_filename("a.txt"))

    def test_resolve_upload_path_rejects_traversal_and_separators(self):
        for bad in ("../x.png", "dir/x.png", "a/../b.png", "a\\b.png", "..%2Fx.png"):
            with self.assertRaises(FileNotFoundError):
                resolve_upload_path(bad)

    def test_delete_stored_media_removes_referenced_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("common.storage.get_upload_dir", return_value=Path(tmp)):
                name = save_media(b"\x00" * 4, "image/png")
                self.assertTrue(delete_stored_media(f"/api/v1/media/files/{name}"))
                self.assertFalse((Path(tmp) / name).exists())

    def test_delete_stored_media_is_forgiving(self):
        """Cleanup runs after the row is committed, so it must never raise."""
        with tempfile.TemporaryDirectory() as tmp:
            with patch("common.storage.get_upload_dir", return_value=Path(tmp)):
                # Already gone, never ours, a traversal attempt, and no url.
                self.assertFalse(delete_stored_media("/api/v1/media/files/missing.png"))
                self.assertFalse(delete_stored_media("/uploads/school/logo.png"))
                self.assertFalse(delete_stored_media("/api/v1/media/files/../.env"))
                self.assertFalse(delete_stored_media(None))
                self.assertFalse(delete_stored_media(""))

    def test_media_read_schema_exposes_file_fields(self):
        row = Media(media_id=5, school_id=1, class_id=None, title="T", posted_by="P",
                    file_url="/api/v1/media/files/image_x.png", media_kind="image")
        view = MediaRead.model_validate(row)
        self.assertEqual(view.media_id, 5)
        self.assertEqual(view.file_url, "/api/v1/media/files/image_x.png")
        self.assertEqual(view.media_kind, "image")
        self.assertIsNone(view.class_id)


class MediaRouterContractTests(unittest.TestCase):
    def _routes(self):
        tree = ast.parse(ROUTER_SOURCE.read_text(encoding="utf-8"))
        routes = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for dec in node.decorator_list:
                    if (
                        isinstance(dec, ast.Call)
                        and isinstance(dec.func, ast.Attribute)
                        and getattr(dec.func.value, "id", None) == "router"
                    ):
                        path = dec.args[0].value if dec.args and isinstance(dec.args[0], ast.Constant) else None
                        routes.append((dec.func.attr, path))
        return routes

    def test_endpoints_cover_upload_list_serve_update_delete(self):
        routes = self._routes()
        self.assertIn(("post", ""), routes)
        self.assertIn(("get", ""), routes)
        self.assertIn(("get", "/files/{filename}"), routes)
        self.assertIn(("patch", "/{media_id}"), routes)
        self.assertIn(("delete", "/{media_id}"), routes)

    def test_gateway_forwards_media_segment_to_media_service(self):
        gateway = (ROOT / "gateway" / "main.py").read_text(encoding="utf-8")
        self.assertIn('"media": "media"', gateway)
        config = (ROOT / "common" / "config.py").read_text(encoding="utf-8")
        self.assertIn('"media": "http://127.0.0.1:8016"', config)

    def test_communication_service_no_longer_owns_media(self):
        router = (ROOT / "services" / "communication_service" / "router.py").read_text(encoding="utf-8")
        self.assertNotIn('router.post("/media"', router)
        self.assertNotIn('router.get("/media"', router)

    def _route_by(self, method, path):
        tree = ast.parse(ROUTER_SOURCE.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for dec in node.decorator_list:
                    if (
                        isinstance(dec, ast.Call)
                        and isinstance(dec.func, ast.Attribute)
                        and getattr(dec.func.value, "id", None) == "router"
                    ):
                        p = dec.args[0].value if dec.args and isinstance(dec.args[0], ast.Constant) else None
                        if dec.func.attr == method and p == path:
                            return node
        self.fail(f"route {method} {path} not found")

    def _dep_roles(self, func):
        roles, school_scope = set(), False
        for node in ast.walk(func):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Depends":
                arg = node.args[0] if node.args else None
                if isinstance(arg, ast.Name) and arg.id == "require_school_scope":
                    school_scope = True
                elif isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name):
                    if arg.func.id == "require_role":
                        roles.add(tuple(a.value for a in arg.args if isinstance(a, ast.Constant)))
        return roles, school_scope

    def test_role_guards_are_pinned_per_endpoint(self):
        upload_roles, upload_scope = self._dep_roles(self._route_by("post", ""))
        self.assertEqual(upload_roles, {("teacher", "admin", "staff")})
        self.assertTrue(upload_scope)

        list_roles, list_scope = self._dep_roles(self._route_by("get", ""))
        self.assertEqual(list_roles, {("parent", "teacher", "admin", "staff")})
        self.assertTrue(list_scope)

        delete_roles, delete_scope = self._dep_roles(self._route_by("delete", "/{media_id}"))
        self.assertEqual(delete_roles, {("teacher", "admin", "staff")})
        self.assertTrue(delete_scope)

        update_roles, update_scope = self._dep_roles(self._route_by("patch", "/{media_id}"))
        self.assertEqual(update_roles, {("teacher", "admin", "staff")})
        self.assertTrue(update_scope)

    def test_parent_is_excluded_from_writes(self):
        """Parents browse the gallery read-only; they must not reach a write."""
        write_routes = {("post", ""), ("patch", "/{media_id}"), ("delete", "/{media_id}")}
        for method, path in self._routes():
            if (method, path) not in write_routes:
                continue
            roles, _ = self._dep_roles(self._route_by(method, path))
            for allowed in roles:
                self.assertNotIn("parent", allowed, f"{method} {path} must exclude parents")

    def test_upload_forms_and_responses_are_pinned(self):
        serve = self._route_by("get", "/files/{filename}")
        deps = [n for n in ast.walk(serve)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "Depends"]
        self.assertEqual(deps, [], "served media files must stay browser-loadable without a token")

        src = ROUTER_SOURCE.read_text(encoding="utf-8")
        self.assertIn('title: str = Form(...)', src)
        self.assertIn('class_id: int | None = Form(default=None)', src)
        self.assertIn('file: UploadFile = File(...)', src)
        self.assertIn('class_id: int | None = Query(default=None)', src)
        self.assertIn('@router.post("", response_model=MediaRead, status_code=201)', src)
        self.assertIn('@router.get("", response_model=list[MediaRead])', src)
        self.assertIn('@router.delete("/{media_id}", response_model=MediaRead)', src)
        self.assertIn('@router.patch("/{media_id}", response_model=MediaRead)', src)
        self.assertIn('file: UploadFile | None = File(default=None)', src)

    def test_upload_records_uploader_from_session(self):
        """Ownership must come from the authenticated user, not the request."""
        src = ROUTER_SOURCE.read_text(encoding="utf-8")
        self.assertIn('"uploader_user_id": current_user.user_id', src)
        # upload_media must not accept uploader_user_id as a client-supplied field.
        upload_body = src.split("def upload_media", 1)[1].split("@router.get", 1)[0]
        self.assertNotIn("uploader_user_id: ", upload_body)


class FakeUpload:
    """Minimal stand-in for FastAPI's UploadFile — only what the handler reads."""

    def __init__(self, data, content_type, filename="upload"):
        self._data = data
        self.content_type = content_type
        self.filename = filename

    async def read(self):
        return self._data


class MediaRouterHandlerTests(unittest.TestCase):
    """Execute the media router's request handlers end-to-end (validation and
    guards happen in FastAPI at call time; handlers are invoked directly here,
    mirroring how the auth-service tests drive their router)."""

    @classmethod
    def setUpClass(cls):
        # The service uses bare `import repository` / `from schemas import ...`,
        # which collide with top-level names left by other services in the same
        # test run — evict and restore them around a fresh import.
        sys.path.insert(0, str(ROOT / "services" / "media_service"))
        cls._saved = {name: sys.modules.pop(name, None) for name in ("router", "schemas", "repository")}
        try:
            import router  # noqa: F401
            cls.router = router
        except Exception:
            for name, mod in cls._saved.items():
                if mod is not None:
                    sys.modules[name] = mod
            raise
        cls.engine = create_engine("sqlite://")
        _ENGINES.append(cls.engine)
        cls.Session = sessionmaker(bind=cls.engine)

    @classmethod
    def tearDownClass(cls):
        for name, mod in cls._saved.items():
            if mod is not None:
                sys.modules[name] = mod
        sys.path.pop(0)

    def setUp(self):
        Base.metadata.drop_all(self.engine, tables=GALLERY_TABLES)
        Base.metadata.create_all(self.engine, tables=GALLERY_TABLES)
        self.session = self.Session()
        seed(self.session)

    def tearDown(self):
        self.session.close()

    def _upload(self, data, content_type, title="Sports day", class_id=None, role="teacher"):
        user = CurrentUser(user_id=7, role=role, school_id=1, linked_person_id=7 if role == "teacher" else 9)
        return self.router.upload_media(
            title=title,
            class_id=class_id,
            file=FakeUpload(data, content_type),
            db=self.session,
            school_id=1,
            current_user=user,
        )

    def test_upload_image_round_trips_to_file_and_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("common.storage.get_upload_dir", return_value=Path(tmp)):
                media = asyncio.run(self._upload(b"\x89PNG\r\n\x1a\n", "image/png"))
                self.assertEqual(media.title, "Sports day")
                self.assertEqual(media.media_kind, "image")
                self.assertEqual(media.posted_by, "Mr. Tripathi")
                self.assertTrue(media.file_url.startswith("/api/v1/media/files/image_gallery_"))
                self.assertTrue(media.file_url.endswith(".png"))
                self.assertTrue((Path(tmp) / Path(media.file_url).name).is_file())

                response = self.router.serve_media_file(Path(media.file_url).name)
                self.assertEqual(response.media_type, "image/png")
                self.assertTrue(Path(response.path).is_file())

    def test_upload_video_sets_kind_and_serves_video_mime(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("common.storage.get_upload_dir", return_value=Path(tmp)):
                media = asyncio.run(self._upload(b"\x00" * 32, "video/quicktime", title="Assembly clip"))
                self.assertEqual(media.media_kind, "video")
                self.assertTrue(media.file_url.endswith(".mov"))
                response = self.router.serve_media_file(Path(media.file_url).name)
                self.assertEqual(response.media_type, "video/quicktime")

    def test_upload_persists_optional_class(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("common.storage.get_upload_dir", return_value=Path(tmp)):
                media = asyncio.run(self._upload(b"\x00" * 8, "image/png", class_id=5))
                self.assertEqual(media.class_id, 5)

    def test_upload_rejects_non_media_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("common.storage.get_upload_dir", return_value=Path(tmp)):
                with self.assertRaises(AppError):
                    asyncio.run(self._upload(b"%PDF-1.4", "application/pdf"))

    def test_upload_rejects_oversize_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("common.storage.get_upload_dir", return_value=Path(tmp)):
                with self.assertRaisesRegex(AppError, "5 MB"):
                    asyncio.run(self._upload(b"\x00" * (settings.UPLOAD_MAX_BYTES + 1), "image/png"))

    def test_list_handler_scopes_to_school(self):
        by_parent = CurrentUser(user_id=3, role="parent", school_id=1)
        rows = self.router.list_media(class_id=None, db=self.session, school_id=1, current_user=by_parent)
        ids = {m.media_id for m in rows}
        self.assertEqual(ids, {1, 2, 5})

    def test_list_handler_filters_by_class(self):
        rows = self.router.list_media(class_id=5, db=self.session, school_id=1,
                                      current_user=CurrentUser(user_id=3, role="parent", school_id=1))
        self.assertEqual([m.media_id for m in rows], [2])

    def test_delete_handler_soft_deletes_and_second_delete_404s(self):
        admin = _admin()
        removed = self.router.delete_media(media_id=1, db=self.session, school_id=1, current_user=admin)
        self.assertFalse(removed.is_active)
        with self.assertRaises(NotFoundError):
            self.router.delete_media(media_id=1, db=self.session, school_id=1, current_user=admin)

    def test_upload_records_uploader_user_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("common.storage.get_upload_dir", return_value=Path(tmp)):
                media = asyncio.run(self._upload(b"\x00" * 8, "image/png"))
                self.assertEqual(media.uploader_user_id, 7)
                self.assertEqual(MediaRead.model_validate(media).uploader_user_id, 7)

    def test_update_handler_allows_owner_and_refuses_colleague(self):
        owner = _teacher(7)
        updated = asyncio.run(self.router.update_media(
            media_id=2, title="Sports week", class_id=None, set_class_id=False,
            file=None, db=self.session, school_id=1, current_user=owner,
        ))
        self.assertEqual(updated.title, "Sports week")

        with self.assertRaises(ForbiddenError):
            asyncio.run(self.router.update_media(
                media_id=1, title="Not mine", class_id=None, set_class_id=False,
                file=None, db=self.session, school_id=1, current_user=owner,
            ))

    def test_update_handler_allows_admin_on_colleagues_media(self):
        updated = asyncio.run(self.router.update_media(
            media_id=1, title="Fixed by admin", class_id=None, set_class_id=False,
            file=None, db=self.session, school_id=1, current_user=_admin(),
        ))
        self.assertEqual(updated.title, "Fixed by admin")

    def test_update_handler_rejects_empty_title_and_no_op(self):
        with self.assertRaises(AppError):
            asyncio.run(self.router.update_media(
                media_id=2, title="   ", class_id=None, set_class_id=False,
                file=None, db=self.session, school_id=1, current_user=_teacher(7),
            ))
        with self.assertRaises(AppError):
            asyncio.run(self.router.update_media(
                media_id=2, title=None, class_id=None, set_class_id=False,
                file=None, db=self.session, school_id=1, current_user=_teacher(7),
            ))

    def test_update_handler_replaces_file_and_removes_superseded_file(self):
        """The replaced file is unlinked only after the row stops pointing at it."""
        with tempfile.TemporaryDirectory() as tmp:
            with patch("common.storage.get_upload_dir", return_value=Path(tmp)):
                original = asyncio.run(self._upload(b"\x00" * 8, "image/png", title="Before"))
                old_name = Path(original.file_url).name
                self.assertTrue((Path(tmp) / old_name).is_file())

                updated = asyncio.run(self.router.update_media(
                    media_id=original.media_id, title="After", class_id=None,
                    set_class_id=False, file=FakeUpload(b"\x00" * 16, "video/mp4"),
                    db=self.session, school_id=1, current_user=_teacher(7),
                ))
                new_name = Path(updated.file_url).name
                self.assertNotEqual(new_name, old_name)
                self.assertEqual(updated.media_kind, "video")
                self.assertTrue((Path(tmp) / new_name).is_file())
                self.assertFalse((Path(tmp) / old_name).exists(), "old file must be unlinked")

    def test_update_handler_keeps_file_when_only_caption_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("common.storage.get_upload_dir", return_value=Path(tmp)):
                original = asyncio.run(self._upload(b"\x00" * 8, "image/png"))
                name = Path(original.file_url).name
                updated = asyncio.run(self.router.update_media(
                    media_id=original.media_id, title="Caption only", class_id=None,
                    set_class_id=False, file=None, db=self.session, school_id=1,
                    current_user=_teacher(7),
                ))
                self.assertEqual(updated.file_url, original.file_url)
                self.assertTrue((Path(tmp) / name).is_file())

    def test_update_handler_rejects_bad_replacement_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("common.storage.get_upload_dir", return_value=Path(tmp)):
                media = asyncio.run(self._upload(b"\x00" * 8, "image/png"))
                with self.assertRaises(AppError):
                    asyncio.run(self.router.update_media(
                        media_id=media.media_id, title=None, class_id=None,
                        set_class_id=False, file=FakeUpload(b"%PDF", "application/pdf"),
                        db=self.session, school_id=1, current_user=_teacher(7),
                    ))

    def test_update_handler_cannot_cross_school(self):
        with self.assertRaises(NotFoundError):
            asyncio.run(self.router.update_media(
                media_id=3, title="Reach across", class_id=None, set_class_id=False,
                file=None, db=self.session, school_id=1, current_user=_admin(),
            ))

    def test_serve_handler_rejects_traversal(self):
        with self.assertRaises(NotFoundError):
            self.router.serve_media_file("../.env")

    def test_kind_mapping_tracks_content_type(self):
        self.assertEqual(self.router._media_kind_for("image/png"), "image")
        self.assertEqual(self.router._media_kind_for("video/mp4"), "video")


if __name__ == "__main__":
    unittest.main()