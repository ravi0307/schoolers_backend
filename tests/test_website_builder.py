import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import ValidationError

from common.exceptions import NotFoundError
from services.website_service.schemas import WebsiteBuilderContent

ROOT = Path(__file__).resolve().parents[1]
SERVICE_DIR = ROOT / "services" / "website_service"
BARE_MODULES = ("repository", "router", "schemas")


class WebsiteBuilderSchemaTests(unittest.TestCase):
    def test_canvas_document_round_trips_editor_node_fields(self):
        content = WebsiteBuilderContent.model_validate({
            "school_name": "Sunrise School",
            "canvas_size": {"width": 1200, "height": 1900},
            "nodes": [{
                "id": "site-header",
                "anchorId": "top",
                "type": "header",
                "title": "School header",
                "x": 0,
                "y": 0,
                "width": 100,
                "height": 9,
                "labels": [{"id": "about", "text": "About Us", "anchorId": "about"}],
            }],
            "testimonials": [{"id": "live-1", "name": "Parent", "role": "Parent", "quote": "Great school"}],
            "pending_testimonials": [],
        })

        serialized = content.as_json()
        self.assertEqual(serialized["canvas_size"], {"width": 1200, "height": 1900})
        self.assertEqual(serialized["nodes"][0]["anchorId"], "top")
        self.assertEqual(serialized["nodes"][0]["labels"][0]["anchorId"], "about")
        self.assertEqual(serialized["testimonials"][0]["id"], "live-1")

    def test_canvas_document_rejects_invalid_dimensions_and_node_types(self):
        with self.assertRaises(ValidationError):
            WebsiteBuilderContent.model_validate({
                "school_name": "Sunrise School",
                "canvas_size": {"width": 500, "height": 1900},
                "nodes": [],
                "testimonials": [],
                "pending_testimonials": [],
            })

        with self.assertRaises(ValidationError):
            WebsiteBuilderContent.model_validate({
                "school_name": "Sunrise School",
                "canvas_size": {"width": 1200, "height": 1900},
                "nodes": [{
                    "id": "invalid",
                    "type": "unknown",
                    "x": 0,
                    "y": 0,
                    "width": 100,
                    "height": 100,
                }],
                "testimonials": [],
                "pending_testimonials": [],
            })


class WebsiteBuilderPersistenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.saved_modules = {name: sys.modules.pop(name, None) for name in BARE_MODULES}
        sys.path.insert(0, str(SERVICE_DIR))
        import repository as website_repository  # noqa: PLC0415
        import router as website_router  # noqa: PLC0415

        cls.repository = website_repository
        cls.router = website_router

    @classmethod
    def tearDownClass(cls):
        sys.path.remove(str(SERVICE_DIR))
        for name in BARE_MODULES:
            sys.modules.pop(name, None)
            if cls.saved_modules[name] is not None:
                sys.modules[name] = cls.saved_modules[name]

    def test_save_draft_creates_and_updates_one_school_record(self):
        content = {"school_name": "Sunrise School", "nodes": []}
        db = _FakeSession()

        site = self.repository.save_draft(db, 17, content)

        self.assertEqual(site.school_id, 17)
        self.assertEqual(site.draft, content)
        self.assertIsNone(site.published)
        self.assertEqual(db.commit_count, 1)
        self.assertEqual(db.refresh_count, 1)

        replacement = {"school_name": "Sunrise Academy", "nodes": [{"id": "hero"}]}
        updated = self.repository.save_draft(db, 17, replacement)
        self.assertIs(updated, site)
        self.assertEqual(updated.draft, replacement)
        self.assertEqual(db.commit_count, 2)

    def test_publish_snapshots_the_current_draft_and_public_read_hides_unpublished(self):
        draft = {"school_name": "Sunrise School", "nodes": [{"id": "hero"}]}
        db = _FakeSession(SimpleNamespace(
            school_id=17,
            draft=draft,
            published=None,
            published_at=None,
        ))

        self.assertIsNone(self.repository.get_published_site(db, 17))
        published = self.repository.publish_site(db, 17)
        self.assertIsNotNone(published.published_at)
        self.assertEqual(published.published, draft)
        self.assertIsNot(published.published, published.draft)
        self.assertIs(self.repository.get_published_site(db, 17), published)

        published.draft["nodes"].append({"id": "later-edit"})
        self.assertEqual(published.published["nodes"], [{"id": "hero"}])

    def test_public_handlers_render_only_the_published_snapshot(self):
        site = SimpleNamespace(
            school_id=17,
            draft={"school_name": "Sunrise Public School", "nodes": [{"id": "draft"}]},
            published={
                "school_name": "Sunrise Public School",
                "canvas_size": {"width": 1200, "height": 1900},
                "nodes": [{"id": "live"}],
                "testimonials": [],
            },
            published_at=datetime.now(timezone.utc),
        )
        db = _FakeSession(site)

        with patch.object(self.router.repo, "get_published_site", return_value=site):
            response = self.router.public_site(17, db)
        self.assertEqual(response.school_id, 17)
        self.assertEqual(response.nodes, [{"id": "live"}])

        with patch.object(self.router.repo, "find_published_site_by_slug", return_value=site) as find:
            response = self.router.public_site_by_name("sunrise-public-school", db)
        find.assert_called_once_with(db, "sunrise-public-school")
        self.assertEqual(response.school_name, "Sunrise Public School")

        with patch.object(self.router.repo, "get_published_site", return_value=None):
            with self.assertRaises(NotFoundError):
                self.router.public_site(17, db)

    def test_admin_draft_and_publish_handlers_use_the_callers_school(self):
        payload = WebsiteBuilderContent.model_validate({
            "school_name": "Sunrise School",
            "canvas_size": {"width": 1200, "height": 1900},
            "nodes": [],
            "testimonials": [],
            "pending_testimonials": [],
        })
        current_user = SimpleNamespace(user_id=1, role="admin", school_id=17)
        site = SimpleNamespace(
            school_id=17,
            draft=payload.as_json(),
            published=None,
            modified_at=datetime.now(timezone.utc),
            published_at=None,
        )
        db = object()

        with patch.object(self.router.repo, "save_draft", return_value=site) as save:
            state = self.router.save_builder_draft(payload, db, 17, current_user)
        save.assert_called_once_with(db, 17, payload.as_json())
        self.assertEqual(state.draft["school_name"], "Sunrise School")

        site.published = payload.as_json()
        site.published_at = datetime.now(timezone.utc)
        with patch.object(self.router.repo, "publish_site", return_value=site) as publish:
            response = self.router.publish_builder(db, 17, current_user)
        publish.assert_called_once_with(db, 17)
        self.assertEqual(response.school_id, 17)
        self.assertEqual(response.nodes, [])

        with patch.object(self.router.repo, "publish_site", return_value=None):
            with self.assertRaises(NotFoundError):
                self.router.publish_builder(db, 17, current_user)

    def test_multi_tenant_get_endpoint_returns_school_builder_state_and_blocks_other_schools(self):
        current_user = SimpleNamespace(user_id=1, role="admin", school_id=17)
        site = SimpleNamespace(
            school_id=17,
            draft={"school_name": "Sunrise School", "nodes": []},
            published={"school_name": "Sunrise School", "nodes": [{"id": "live"}]},
            modified_at=datetime.now(timezone.utc),
            published_at=datetime.now(timezone.utc),
        )
        db = object()

        with patch.object(self.router.repo, "get_site", return_value=site) as get_site:
            state = self.router.get_builder_for_school(17, db, 17, current_user)
        get_site.assert_called_once_with(db, 17)
        self.assertEqual(state.draft["school_name"], "Sunrise School")
        self.assertEqual(state.published["nodes"], [{"id": "live"}])

        with patch.object(self.router.repo, "get_site") as get_site:
            with self.assertRaises(NotFoundError):
                self.router.get_builder_for_school(18, db, 17, current_user)
        get_site.assert_not_called()

    def test_upload_rejects_mismatched_image_signatures_and_unknown_asset_names(self):
        matches = self.router._matches_image_signature
        self.assertTrue(matches("image/jpeg", b"\xff\xd8\xffdata", b"\xff\xd8\xff"))
        self.assertTrue(matches("image/png", b"\x89PNG\r\n\x1a\ndata", b"\x89PNG\r\n\x1a\n"))
        self.assertTrue(matches("image/gif", b"GIF89adata", (b"GIF87a", b"GIF89a")))
        self.assertTrue(matches("image/webp", b"RIFFxxxxWEBPdata", b"RIFF"))
        self.assertFalse(matches("image/webp", b"RIFFxxxxNOPEdata", b"RIFF"))
        self.assertFalse(matches("image/png", b"<svg></svg>", b"\x89PNG\r\n\x1a\n"))
        self.assertIsNotNone(self.router.WEBSITE_ASSET_NAME.fullmatch("website_17_20261010_123456_123456.jpg"))
        self.assertIsNone(self.router.WEBSITE_ASSET_NAME.fullmatch("image_20261010_123456.jpg"))


class _FakeQuery:
    def __init__(self, db):
        self.db = db

    def filter(self, *_conditions):
        return self

    def first(self):
        return self.db.site


class _FakeSession:
    def __init__(self, site=None):
        self.site = site
        self.commit_count = 0
        self.refresh_count = 0

    def query(self, _model):
        return _FakeQuery(self)

    def add(self, site):
        self.site = site

    def commit(self):
        self.commit_count += 1

    def refresh(self, _site):
        self.refresh_count += 1
