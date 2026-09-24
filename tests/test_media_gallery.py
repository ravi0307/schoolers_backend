"""
Tests for the school photo/video gallery service.

Runs against an in-memory SQLite database and pounds the media_service
repository directly (the router keeps the project's bare `import repository`
convention, so endpoints are contract-checked here via AST instead).
"""
import ast
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from common.dependencies import CurrentUser
from common.exceptions import ForbiddenError, NotFoundError
from common.models import Base, Media, Staff, Teacher
from common.storage import (
    ALLOWED_MEDIA_TYPES,
    ALLOWED_VIDEO_TYPES,
    save_media,
)
import services.media_service.repository as repo

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


class MediaGalleryRepositoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite://")
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


if __name__ == "__main__":
    unittest.main()