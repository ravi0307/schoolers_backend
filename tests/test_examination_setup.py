"""
Functional tests for examination setup (Phase 2): per-school marking
system, examination types, versioned grade scales.

Covers school isolation, duplicate-name rejection, the switch-confirmation
gate (409 with impact counts), copy-on-write scale versioning, and the
cross-school 404 uniformity on the new routers.
"""
import sys
import unittest
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from common.dependencies import CurrentUser
from common.exceptions import ConflictError, ForbiddenError, NotFoundError
from common.models import (
    Base, ExaminationType, GradeLevel, GradeScale, Mark, School, Student, Subject,
)
import services.academics_service.repository as acad_repo
import services.schools_service.repository as schools_repo

TABLES = [
    "schools", "examination_types", "grade_scales", "grade_levels",
    "students", "subjects", "marks",
]


def _load_router(service_dir, package):
    # Isolated router import (see test_academic_calendar for why).
    sys.path.insert(0, service_dir)
    try:
        for mod in ("repository", "schemas"):
            sys.modules.pop(mod, None)
        router_mod = __import__(f"{package}.router", fromlist=["x"])
        schemas_mod = __import__(f"{package}.schemas", fromlist=["x"])
        return router_mod, schemas_mod
    finally:
        sys.path.pop(0)
        for mod in ("repository", "schemas"):
            sys.modules.pop(mod, None)


class MarkingSystemTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite://")
        Base.metadata.create_all(
            cls.engine,
            tables=[Base.metadata.tables[name] for name in TABLES],
        )
        cls.Session = sessionmaker(bind=cls.engine, autoflush=False, future=True)

    def setUp(self):
        self.db: Session = self.Session()
        for name in TABLES:
            self.db.execute(Base.metadata.tables[name].delete())
        self.db.commit()
        self.db.add_all([
            School(school_id=1, name="Green Valley", address="A", pincode="1",
                   city="C", state="S", primary_contact="P", primary_email="e@x.io",
                   examinations_enabled=True),
            School(school_id=2, name="Blue Horizon", address="A", pincode="1",
                   city="C", state="S", primary_contact="P", primary_email="e@x.io",
                   examinations_enabled=True),
        ])
        self.db.commit()
        self.admin1 = CurrentUser(user_id=11, role="admin", school_id=1, linked_person_id=None)
        self.admin2 = CurrentUser(user_id=12, role="admin", school_id=2, linked_person_id=None)
        self.admin2 = CurrentUser(user_id=12, role="admin", school_id=2, linked_person_id=None)
        self.master = CurrentUser(user_id=1, role="master", school_id=None, linked_person_id=None)
        self.teacher1 = CurrentUser(user_id=31, role="teacher", school_id=1, linked_person_id=7)

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def _school(self, school_id):
        return self.db.query(School).filter(School.school_id == school_id).one()

    def test_default_marking_system_is_marks(self):
        self.assertEqual(self._school(1).marking_system, "marks")

    def test_switch_without_marks_needs_no_confirmation(self):
        school = schools_repo.set_marking_system(self.db, self._school(1), "grades")
        self.assertEqual(school.marking_system, "grades")

    def test_switch_with_marks_requires_confirmation(self):
        self.db.add_all([
            Student(student_id=1, school_id=1, class_id=1, admission_no="A1", name="Riya"),
            Subject(subject_id=3, school_id=1, name="Maths"),
            Mark(student_id=1, subject_id=3, term="Term 1", score=80, max_marks=100, result_format="marks",
                             workflow_status="published", assessment_status="present",
                             is_withheld=False),
        ])
        self.db.commit()
        with self.assertRaises(ConflictError) as ctx:
            schools_repo.set_marking_system(self.db, self._school(1), "grades")
        self.assertIn("1 existing marks", str(ctx.exception))
        school = schools_repo.set_marking_system(self.db, self._school(1), "grades", confirm=True)
        self.assertEqual(school.marking_system, "grades")

    def test_same_value_switch_is_noop_even_with_marks(self):
        self.db.add_all([
            Student(student_id=1, school_id=1, class_id=1, admission_no="A1", name="Riya"),
            Subject(subject_id=3, school_id=1, name="Maths"),
            Mark(student_id=1, subject_id=3, term="Term 1", score=80, max_marks=100, result_format="marks",
                             workflow_status="published", assessment_status="present",
                             is_withheld=False),
        ])
        self.db.commit()
        school = schools_repo.set_marking_system(self.db, self._school(1), "marks")
        self.assertEqual(school.marking_system, "marks")

    def test_marking_system_router_guards_and_scoping(self):
        router, schemas = _load_router("services/schools_service", "services.schools_service")
        # Other school's admin cannot touch school 1.
        with self.assertRaises(ForbiddenError):
            router.update_marking_system(
                1, schemas.MarkingSystemUpdate(marking_system="grades"),
                self.db, self.admin2,
            )
        # Own admin can switch an empty school without confirmation.
        school = router.update_marking_system(
            1, schemas.MarkingSystemUpdate(marking_system="grades"),
            self.db, self.admin1,
        )
        self.assertEqual(school.marking_system, "grades")
        # Master can switch any school.
        school = router.update_marking_system(
            2, schemas.MarkingSystemUpdate(marking_system="grades"),
            self.db, self.master,
        )
        self.assertEqual(school.marking_system, "grades")
        # Unknown school is 404, not 403.
        with self.assertRaises(NotFoundError):
            router.update_marking_system(
                4242, schemas.MarkingSystemUpdate(marking_system="grades"),
                self.db, self.master,
            )

    def test_marking_system_schema_rejects_unknown_values(self):
        from pydantic import ValidationError
        router, schemas = _load_router("services/schools_service", "services.schools_service")
        with self.assertRaises(ValidationError):
            schemas.MarkingSystemUpdate(marking_system="stars")


class ExaminationTypeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite://")
        Base.metadata.create_all(
            cls.engine,
            tables=[Base.metadata.tables[name] for name in TABLES],
        )
        cls.Session = sessionmaker(bind=cls.engine, autoflush=False, future=True)

    def setUp(self):
        self.db: Session = self.Session()
        for name in TABLES:
            self.db.execute(Base.metadata.tables[name].delete())
        self.db.commit()
        self.admin1 = CurrentUser(user_id=11, role="admin", school_id=1, linked_person_id=None)
        self.db.add_all([
            School(school_id=1, name="Green Valley", address="A", pincode="1",
                   city="C", state="S", primary_contact="P", primary_email="e@x.io",
                   examinations_enabled=True),
            School(school_id=2, name="Blue Horizon", address="A", pincode="1",
                   city="C", state="S", primary_contact="P", primary_email="e@x.io",
                   examinations_enabled=True),
        ])
        self.db.commit()
        self.admin2 = CurrentUser(user_id=12, role="admin", school_id=2, linked_person_id=None)

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def test_type_crud_is_school_scoped(self):
        t1 = acad_repo.create_examination_type(
            self.db, 1, {"name": "Half-Yearly", "description": "Term 1", "default_max_marks": 100}
        )
        self.assertEqual(t1.default_max_marks, 100)
        # Same name in another school is fine.
        acad_repo.create_examination_type(self.db, 2, {"name": "Half-Yearly"})
        with self.assertRaises(ConflictError):
            acad_repo.create_examination_type(self.db, 1, {"name": "Half-Yearly"})
        self.assertEqual(len(acad_repo.list_examination_types(self.db, 1)), 1)

    def test_type_max_marks_must_be_positive(self):
        router, schemas = _load_router("services/academics_service", "services.academics_service")
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            schemas.ExaminationTypeCreate(name="Unit", default_max_marks=0)

    def test_type_toggle_and_cross_school_404(self):
        router, schemas = _load_router("services/academics_service", "services.academics_service")
        t1 = acad_repo.create_examination_type(self.db, 1, {"name": "Unit Test"})
        acad_repo.update_examination_type(self.db, t1, {"is_active": False})
        self.assertFalse(acad_repo.get_examination_type(self.db, 1, t1.type_id).is_active)
        with self.assertRaises(NotFoundError):
            router.update_examination_type(
                t1.type_id, schemas.ExaminationTypeUpdate(name="X"), self.db, 2, self.admin2
            )


class GradeScaleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite://")
        Base.metadata.create_all(
            cls.engine,
            tables=[Base.metadata.tables[name] for name in TABLES],
        )
        cls.Session = sessionmaker(bind=cls.engine, autoflush=False, future=True)

    def setUp(self):
        self.db: Session = self.Session()
        for name in TABLES:
            self.db.execute(Base.metadata.tables[name].delete())
        self.db.commit()
        self.admin1 = CurrentUser(user_id=11, role="admin", school_id=1, linked_person_id=None)
        self.db.add_all([
            School(school_id=1, name="Green Valley", address="A", pincode="1",
                   city="C", state="S", primary_contact="P", primary_email="e@x.io",
                   examinations_enabled=True),
            School(school_id=2, name="Blue Horizon", address="A", pincode="1",
                   city="C", state="S", primary_contact="P", primary_email="e@x.io",
                   examinations_enabled=True),
        ])
        self.db.commit()
        self.admin2 = CurrentUser(user_id=12, role="admin", school_id=2, linked_person_id=None)

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def _scale_with_levels(self, school_id=1, name="Alpha"):
        scale = acad_repo.create_grade_scale(self.db, school_id, {"name": name})
        for label, rank in (("A+", 1), ("A", 2), ("B", 3)):
            acad_repo.create_grade_level(
                self.db, scale.scale_id, {"label": label, "rank": rank}
            )
        return scale

    def test_scale_names_are_school_scoped(self):
        self._scale_with_levels(1)
        with self.assertRaises(ConflictError):
            acad_repo.create_grade_scale(self.db, 1, {"name": "Alpha"})
        self._scale_with_levels(2, name="Alpha")

    def test_level_labels_unique_per_scale_and_bands_ordered(self):
        from pydantic import ValidationError
        router, schemas = _load_router("services/academics_service", "services.academics_service")
        scale = self._scale_with_levels(1)
        with self.assertRaises(ConflictError):
            acad_repo.create_grade_level(self.db, scale.scale_id, {"label": "A", "rank": 9})
        with self.assertRaises(ValidationError):
            schemas.GradeLevelCreate(label="C", min_score=50, max_score=40)

    def test_new_version_copies_levels_and_freezes_old(self):
        scale = self._scale_with_levels(1)
        nxt = acad_repo.new_grade_scale_version(self.db, scale)
        self.assertEqual(nxt.version, scale.version + 1)
        self.assertEqual(nxt.supersedes_id, scale.scale_id)
        self.assertFalse(acad_repo.get_grade_scale(self.db, 1, scale.scale_id).is_active)
        self.assertEqual(
            sorted(l.label for l in acad_repo.list_grade_levels(self.db, nxt.scale_id)),
            ["A", "A+", "B"],
        )
        # Old and new versions coexist; listings show newest first.
        names = [(s.name, s.version) for s in acad_repo.list_grade_scales(self.db, 1)]
        self.assertEqual(names, [("Alpha", 2), ("Alpha", 1)])

    def test_level_crud_is_school_scoped_at_router(self):
        router, schemas = _load_router("services/academics_service", "services.academics_service")
        scale = self._scale_with_levels(1)
        level = acad_repo.list_grade_levels(self.db, scale.scale_id)[0]
        with self.assertRaises(NotFoundError):
            router.update_grade_level(
                level.level_id, schemas.GradeLevelUpdate(label="Z"), self.db, 2, self.admin2
            )
        with self.assertRaises(NotFoundError):
            router.delete_grade_level(level.level_id, self.db, 2, self.admin2)
        updated = router.update_grade_level(
            level.level_id, schemas.GradeLevelUpdate(description="Top"), self.db, 1, self.admin1
        )
        self.assertEqual(updated.description, "Top")


if __name__ == "__main__":
    unittest.main()
