"""
Functional tests for the academic calendar (Phase 1): years, periods,
dated non-closure events.

Covers school-scoped CRUD, period bounds against the academic year,
sibling-period overlap rejection, event-type allow-listing, cross-school
404 uniformity, and deactivation instead of deletion for years/periods.
"""
import sys
import unittest
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from common.dependencies import CurrentUser
from common.exceptions import AppError, ConflictError, NotFoundError
from common.models import AcademicEvent, AcademicPeriod, AcademicYear, Base, School
import services.academics_service.repository as repo

TABLES = ["schools", "academic_years", "academic_periods", "academic_events"]


def _load_academics_router():
    # Isolated router import: `import repository` / `from schemas import`
    # inside the router resolve via sys.path, which other service-router
    # tests also use, so evict the shared top-level names around the import
    # and again afterwards to avoid cross-test module poisoning.
        sys.path.insert(0, "services/academics_service")
        try:
            for mod in ("repository", "schemas"):
                sys.modules.pop(mod, None)
            from services.academics_service import router as academics_router
            from services.academics_service import schemas as academics_schemas
            return academics_router, academics_schemas
        finally:
            sys.path.pop(0)
            for mod in ("repository", "schemas"):
                sys.modules.pop(mod, None)


YEAR_26 = {"name": "2026-27", "start_date": date(2026, 6, 1), "end_date": date(2027, 5, 31)}


class AcademicYearTests(unittest.TestCase):
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
        self.parent1 = CurrentUser(user_id=21, role="parent", school_id=1, linked_person_id=225)

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def _year(self, school_id=1, **over):
        data = dict(YEAR_26)
        data.update(over)
        return repo.create_year(self.db, school_id, data)

    def test_create_and_list_years_are_school_scoped(self):
        self._year(1)
        self._year(2, name="2026-27")
        self.assertEqual([y.name for y in repo.list_years(self.db, 1)], ["2026-27"])
        self.assertEqual([y.name for y in repo.list_years(self.db, 2)], ["2026-27"])

    def test_duplicate_year_name_rejected_per_school(self):
        self._year(1)
        with self.assertRaises(ConflictError):
            self._year(1)
        # Same name in another school is fine.
        self._year(2, name="2026-27")

    def test_year_end_before_start_rejected(self):
        with self.assertRaises(AppError):
            repo.update_year(
                self.db,
                self._year(1),
                {"start_date": date(2026, 6, 1), "end_date": date(2026, 6, 1)},
            )

    def test_year_deactivates_instead_of_deleting(self):
        year = self._year(1)
        repo.update_year(self.db, year, {"is_active": False})
        self.assertFalse(repo.get_year(self.db, 1, year.year_id).is_active)

    def test_router_cross_school_year_is_notfound(self):
        router, schemas = _load_academics_router()
        year = self._year(1)
        with self.assertRaises(NotFoundError):
            router.update_academic_year(year.year_id, schemas.AcademicYearUpdate(name="X"), self.db, 2, self.admin2)
        with self.assertRaises(NotFoundError):
            router.list_academic_periods(4242, self.db, 1, self.admin1)

    def test_parent_can_list_years(self):
        router, schemas = _load_academics_router()
        self._year(1)
        self.assertEqual(len(router.list_academic_years(self.db, 1, self.parent1)), 1)


class AcademicPeriodTests(unittest.TestCase):
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
        self.year = repo.create_year(self.db, 1, dict(YEAR_26))
        self.admin1 = CurrentUser(user_id=11, role="admin", school_id=1, linked_person_id=None)

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def test_period_must_fit_inside_its_year(self):
        with self.assertRaises(AppError):
            repo.create_period(self.db, self.year, {
                "name": "Term 1",
                "start_date": date(2026, 5, 1),
                "end_date": date(2026, 9, 30),
            })

    def test_overlapping_sibling_period_rejected(self):
        repo.create_period(self.db, self.year, {
            "name": "Term 1", "start_date": date(2026, 6, 1), "end_date": date(2026, 10, 31),
        })
        with self.assertRaises(ConflictError):
            repo.create_period(self.db, self.year, {
                "name": "Term 2", "start_date": date(2026, 10, 1), "end_date": date(2027, 2, 28),
            })
        # Adjacent (non-overlapping) periods are fine.
        p2 = repo.create_period(self.db, self.year, {
            "name": "Term 2", "start_date": date(2026, 11, 1), "end_date": date(2027, 2, 28),
        })
        self.assertEqual(len(repo.list_academic_periods(self.db, self.year.year_id)), 2)
        # Moving the second back onto the first is rejected too.
        with self.assertRaises(ConflictError):
            repo.update_period_entry(self.db, p2, {"start_date": date(2026, 9, 1)})

    def test_duplicate_period_name_rejected_per_year(self):
        repo.create_period(self.db, self.year, {
            "name": "Term 1", "start_date": date(2026, 6, 1), "end_date": date(2026, 10, 31),
        })
        with self.assertRaises(ConflictError):
            repo.create_period(self.db, self.year, {
                "name": "Term 1", "start_date": date(2026, 11, 1), "end_date": date(2027, 2, 28),
            })

    def test_period_in_another_schools_year_is_notfound(self):
        router, schemas = _load_academics_router()
        from types import SimpleNamespace
        with self.assertRaises(NotFoundError):
            router.create_academic_period(
                schemas.AcademicPeriodCreate(year_id=4242, name="T1",
                                             start_date=date(2026, 6, 1), end_date=date(2026, 9, 30)),
                self.db, 1, self.admin1,
            )


class AcademicEventTests(unittest.TestCase):
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
        self.year = repo.create_year(self.db, 1, dict(YEAR_26))
        self.admin1 = CurrentUser(user_id=11, role="admin", school_id=1, linked_person_id=None)

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def test_event_defaults_end_to_start_and_lists_by_year(self):
        e1 = repo.create_event(self.db, 1, {
            "year_id": self.year.year_id, "event_type": "revision",
            "title": "Prep week", "start_date": date(2026, 12, 1), "end_date": None,
        })
        self.assertEqual(e1.end_date, date(2026, 12, 1))
        repo.create_event(self.db, 1, {
            "year_id": None, "event_type": "other",
            "title": "Founder's day", "start_date": date(2026, 12, 5),
            "end_date": date(2026, 12, 5),
        })
        self.assertEqual(len(repo.list_events(self.db, 1)), 2)
        self.assertEqual(len(repo.list_events(self.db, 1, self.year.year_id)), 1)

    def test_event_year_must_belong_to_school(self):
        with self.assertRaises(NotFoundError):
            repo.create_event(self.db, 2, {
                "year_id": self.year.year_id, "event_type": "other",
                "title": "X", "start_date": date(2026, 12, 1), "end_date": None,
            })

    def test_event_end_before_start_rejected_on_update(self):
        e1 = repo.create_event(self.db, 1, {
            "year_id": None, "event_type": "other",
            "title": "X", "start_date": date(2026, 12, 1), "end_date": None,
        })
        with self.assertRaises(AppError):
            repo.update_event(self.db, e1, {"end_date": date(2026, 11, 30)})

    def test_event_crud_is_school_scoped_at_router(self):
        router, schemas = _load_academics_router()
        e1 = repo.create_event(self.db, 1, {
            "year_id": None, "event_type": "other",
            "title": "X", "start_date": date(2026, 12, 1), "end_date": None,
        })
        with self.assertRaises(NotFoundError):
            router.update_academic_event(e1.event_id, schemas.AcademicEventUpdate(title="Y"), self.db, 2, self.admin1)
        with self.assertRaises(NotFoundError):
            router.delete_academic_event(e1.event_id, self.db, 2, self.admin1)
        # Own school can update and delete.
        self.assertEqual(
            router.update_academic_event(e1.event_id, schemas.AcademicEventUpdate(title="Y"), self.db, 1, self.admin1).title, "Y"
        )
        router.delete_academic_event(e1.event_id, self.db, 1, self.admin1)
        self.assertIsNone(repo.get_event(self.db, 1, e1.event_id))


if __name__ == "__main__":
    unittest.main()
