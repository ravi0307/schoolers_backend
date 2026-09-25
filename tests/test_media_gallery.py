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
from common.models import Base, Media, Staff, Teacher
from common.storage import (
    ALLOWED_MEDIA_TYPES,
    ALLOWED_VIDEO_TYPES,
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
                file_url="/api/v1/media/files/image_a.jpg",
                media_kind="image",
            ),
            Media(
                media_id=2,
                school_id=1,
                class_id=5,
                title="Science fair setup",
                posted_by="Mr. Tripathi",
                file_url="/api/v1/media/files/video_a.mp4",
                media_kind="video",
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
            Teacher(teacher_id=7, school_id=1, name="Mr. Tripathi", role_title="Teacher", phone="12345"),
        ]
    )
    db.commit()


def tearDownModule():
    for engine in _ENGINES:
        engine.dispose()


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

    def test_list_media_scopes_to_school_and_skips_inactive(self):
        rows = repo.list_media(self.session, 1)
        ids = {m.media_id for m in rows}
        self.assertEqual(ids, {1, 2})
        self.assertNotIn(3, ids)
        self.assertNotIn(4, ids)

    def test_list_media_filters_by_class(self):
        rows = repo.list_media(self.session, 1, class_id=5)
        self.assertEqual([m.media_id for m in rows], [2])

    def test_delete_media_soft_deletes_in_school(self):
        removed = repo.delete_media(self.session, 1, 1)
        self.assertFalse(removed.is_active)
        remaining = {m.media_id for m in repo.list_media(self.session, 1)}
        self.assertNotIn(1, remaining)

    def test_delete_media_rejects_other_school(self):
        with self.assertRaises(NotFoundError):
            repo.delete_media(self.session, 1, 3)

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
        repo.delete_media(self.session, 1, 1)
        with self.assertRaises(NotFoundError):
            repo.delete_media(self.session, 1, 1)

    def test_resolve_poster_inactive_teacher_raises(self):
        teacher = self.session.query(Teacher).filter_by(teacher_id=7).one()
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

    def test_endpoints_cover_upload_list_serve_delete(self):
        routes = self._routes()
        self.assertIn(("post", ""), routes)
        self.assertIn(("get", ""), routes)
        self.assertIn(("get", "/files/{filename}"), routes)
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
        self.assertEqual(upload_roles, {("teacher", "admin")})
        self.assertTrue(upload_scope)

        list_roles, list_scope = self._dep_roles(self._route_by("get", ""))
        self.assertEqual(list_roles, {("parent", "teacher", "admin")})
        self.assertTrue(list_scope)

        delete_roles, delete_scope = self._dep_roles(self._route_by("delete", "/{media_id}"))
        self.assertEqual(delete_roles, {("admin",)})
        self.assertTrue(delete_scope)

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


class FakeUpload:
    """Minimal stand-in for FastAPI's UploadFile — only what the handler reads."""

    def __init__(self, data, content_type):
        self._data = data
        self.content_type = content_type

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

    def test_sequential_multi_upload_lands_every_file_in_one_gallery(self):
        # The frontend uploads a multi-selection one file at a time against this
        # same endpoint; a batch of sequential calls must each persist and all
        # appear in the listing afterwards.
        with tempfile.TemporaryDirectory() as tmp:
            with patch("common.storage.get_upload_dir", return_value=Path(tmp)):
                first = asyncio.run(self._upload(b"\x89PNG\r\n\x1a\n", "image/png", title="Family picnic"))
                second = asyncio.run(self._upload(b"\x00" * 32, "video/mp4", title="Sports parade"))
                self.assertNotEqual(first.media_id, second.media_id)
                self.assertTrue(first.file_url.endswith(".png"))
                self.assertTrue(second.file_url.endswith(".mp4"))
                rows = self.router.list_media(
                    class_id=None,
                    db=self.session,
                    school_id=1,
                    current_user=CurrentUser(user_id=3, role="parent", school_id=1),
                )
                titles = [m.title for m in rows]
                self.assertIn("Family picnic", titles)
                self.assertIn("Sports parade", titles)
                kinds = {m.media_kind for m in rows if m.title in ("Family picnic", "Sports parade")}
                self.assertEqual(kinds, {"image", "video"})

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
        self.assertEqual(ids, {1, 2})

    def test_list_handler_filters_by_class(self):
        rows = self.router.list_media(class_id=5, db=self.session, school_id=1,
                                      current_user=CurrentUser(user_id=3, role="parent", school_id=1))
        self.assertEqual([m.media_id for m in rows], [2])

    def test_delete_handler_soft_deletes_and_second_delete_404s(self):
        admin = CurrentUser(user_id=4, role="admin", school_id=1)
        removed = self.router.delete_media(media_id=1, db=self.session, school_id=1, current_user=admin)
        self.assertFalse(removed.is_active)
        with self.assertRaises(NotFoundError):
            self.router.delete_media(media_id=1, db=self.session, school_id=1, current_user=admin)

    def test_serve_handler_rejects_traversal(self):
        with self.assertRaises(NotFoundError):
            self.router.serve_media_file("../.env")

    def test_kind_mapping_tracks_content_type(self):
        self.assertEqual(self.router._media_kind_for("image/png"), "image")
        self.assertEqual(self.router._media_kind_for("video/mp4"), "video")


if __name__ == "__main__":
    unittest.main()