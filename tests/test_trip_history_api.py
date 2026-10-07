"""
Trip History read-API tests (Phase 2).

Covers the read-only history endpoints in transport_service:
  GET  /api/v1/trips                        admin history list + filters
  GET  /api/v1/trips/{trip_id}              admin trip detail (404 cross-school)
  GET  /api/v1/trips/mine                   pilot history (identity from JWT only)
  GET  /api/v1/trips/children/{student_id}  parent history for one owned child

Runs against in-memory SQLite with foreign-key enforcement on (same harness as
test_trip_history_model.py) and executes the router handlers directly (the
media-test convention) with the same bare-import of the transport service.
"""
import ast
import sys
import unittest
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from common.dependencies import CurrentUser
from common.exceptions import AppError, ForbiddenError, NotFoundError
from common.models import (
    Base,
    Parent,
    ParentStudent,
    Pilot,
    Route,
    RouteStop,
    School,
    SchoolClass,
    Staff,
    Student,
    Trip,
    TripStudent,
    User,
)

_ENGINES = []

ROOT = Path(__file__).resolve().parent.parent
ROUTER_SOURCE = ROOT / "services" / "transport_service" / "router.py"
GATEWAY_SOURCE = ROOT / "gateway" / "main.py"

TRIP_TABLES = [
    "route_students",
    "trip_students",
    "trips",
    "parent_student",
    "students",
    "route_stops",
    "routes",
    "parents",
    "pilots",
    "staff",
    "classes",
    "schools",
    "users",
]


def seed(db):
    """Insert rows in FK-dependency order for a fresh FK-enforcing database."""
    db.add_all(
        [
            User(user_id=10, school_id=1, role="admin", username="admin1", password_hash="x"),
            User(user_id=11, school_id=2, role="admin", username="admin2", password_hash="x"),
            User(user_id=12, school_id=1, role="pilot", username="pilot1", password_hash="x"),
            User(user_id=13, school_id=1, role="pilot", username="pilot2", password_hash="x"),
            User(user_id=14, school_id=2, role="pilot", username="pilot3", password_hash="x"),
            User(user_id=15, school_id=1, role="pilot", username="pilot4", password_hash="x"),
            User(user_id=16, school_id=1, role="parent", username="parent1", password_hash="x"),
            User(user_id=17, school_id=1, role="parent", username="parent2", password_hash="x"),
            User(user_id=18, school_id=2, role="parent", username="parent3", password_hash="x"),
            User(user_id=19, school_id=1, role="parent", username="parent4", password_hash="x"),
            School(school_id=1, name="School One", address="1 Main Rd", pincode="560001",
                   city="Bengaluru", state="KA", country="India",
                   primary_contact="+9199", primary_email="one@example.com"),
            School(school_id=2, name="School Two", address="2 Main Rd", pincode="560002",
                   city="Bengaluru", state="KA", country="India",
                   primary_contact="+9198", primary_email="two@example.com"),
        ]
    )
    db.commit()
    db.add_all(
        [
            SchoolClass(class_id=1, school_id=1, name="Grade 5"),
            SchoolClass(class_id=2, school_id=2, name="Grade 6"),
            Staff(staff_id=1, school_id=1, name="Ramesh", role="Driver", person_type="pilot"),
            Staff(staff_id=2, school_id=1, name="Suresh", role="Driver", person_type="pilot"),
            Staff(staff_id=3, school_id=2, name="Cross", role="Driver", person_type="pilot"),
            Staff(staff_id=4, school_id=1, name="Nobody", role="Driver", person_type="pilot"),
            Student(student_id=101, school_id=1, class_id=1, admission_no="ADM101", name="Aarav Rao"),
            Student(student_id=102, school_id=1, class_id=1, admission_no="ADM102", name="Anika Rao"),
            Student(student_id=103, school_id=1, class_id=1, admission_no="ADM103", name="Rohit Rao"),
            Student(student_id=104, school_id=1, class_id=1, admission_no="ADM104", name="Deepti Nair"),
            Student(student_id=201, school_id=2, class_id=2, admission_no="ADM201", name="Neha Cross"),
            Route(route_id=1, school_id=1, name="Route A", vehicle="KA-01-AB-1234"),
            Route(route_id=2, school_id=1, name="Route B", vehicle="KA-01-CD-5678"),
            Route(route_id=3, school_id=2, name="Route C", vehicle="KA-03-AB-9999"),
            RouteStop(stop_id=1, route_id=1, name="Koramangala", stop_time="07:45",
                      stop_type="pickup", stop_order=1),
            RouteStop(stop_id=2, route_id=1, name="Indiranagar", stop_time="08:00",
                      stop_type="drop", stop_order=2),
            RouteStop(stop_id=3, route_id=3, name="MG Road", stop_time="07:45",
                      stop_type="pickup", stop_order=1),
            Parent(parent_id=10, school_id=1, name="Mrs. Rao", phone="1"),
            Parent(parent_id=11, school_id=1, name="Mr. Nair", phone="2"),
            Parent(parent_id=20, school_id=2, name="Ms. Cross", phone="3"),
            Parent(parent_id=12, school_id=1, name="Mrs. Nair", phone="4"),
        ]
    )
    db.commit()
    db.add_all(
        [
            Pilot(pilot_id=1, staff_id=1, route_id=1),
            Pilot(pilot_id=2, staff_id=2, route_id=2),
            Pilot(pilot_id=3, staff_id=3, route_id=3),
            ParentStudent(parent_id=10, student_id=101, relationship_="Mother"),
            ParentStudent(parent_id=10, student_id=102, relationship_="Mother"),
            ParentStudent(parent_id=11, student_id=103, relationship_="Father"),
            ParentStudent(parent_id=12, student_id=104, relationship_="Mother"),
            ParentStudent(parent_id=20, student_id=201, relationship_="Mother"),
        ]
    )
    db.commit()


def seed_trips(db):
    db.add_all(
        [
            Trip(trip_id=1, school_id=1, route_id=1, trip_date=date(2026, 9, 1), direction="pickup",
                 status="completed", pilot_id=1, driver_name="Ramesh", vehicle="KA-01-AB-1234",
                 started_at=datetime(2026, 9, 1, 7, 30), ended_at=datetime(2026, 9, 1, 8, 45)),
            Trip(trip_id=2, school_id=1, route_id=1, trip_date=date(2026, 9, 1), direction="drop",
                 status="completed", pilot_id=1, driver_name="Ramesh", vehicle="KA-01-AB-1234"),
            Trip(trip_id=3, school_id=1, route_id=1, trip_date=date(2026, 9, 2), direction="pickup",
                 status="in_progress", pilot_id=1, driver_name="Ramesh", vehicle="KA-01-AB-1234",
                 started_at=datetime(2026, 9, 2, 7, 30)),
            Trip(trip_id=4, school_id=1, route_id=1, trip_date=date(2026, 9, 3), direction="pickup",
                 status="cancelled", pilot_id=1, driver_name="Ramesh", vehicle="KA-01-AB-1234",
                 cancelled_at=datetime(2026, 9, 3, 6, 0), cancelled_by=10, cancellation_reason="Rain"),
            Trip(trip_id=5, school_id=1, route_id=2, trip_date=date(2026, 9, 4), direction="pickup",
                 status="completed", pilot_id=2, driver_name="Suresh", vehicle="KA-01-CD-5678"),
            Trip(trip_id=6, school_id=1, route_id=2, trip_date=date(2026, 8, 30), direction="pickup",
                 status="completed", pilot_id=2, driver_name="Suresh", vehicle="KA-01-CD-5678"),
            Trip(trip_id=7, school_id=2, route_id=3, trip_date=date(2026, 9, 5), direction="pickup",
                 status="completed", pilot_id=3, driver_name="Cross", vehicle="KA-03-AB-9999"),
            Trip(trip_id=8, school_id=1, route_id=1, trip_date=date(2026, 9, 6), direction="pickup",
                 status="scheduled", pilot_id=1, driver_name="Ramesh", vehicle="KA-01-AB-1234"),
        ]
    )
    db.commit()
    db.add_all(
        [
            TripStudent(trip_id=1, student_id=101, boarding_status="picked",
                        boarding_at=datetime(2026, 9, 1, 7, 40), boarding_stop_id=1,
                        drop_status="dropped", drop_at=datetime(2026, 9, 1, 8, 35), drop_stop_id=2),
            TripStudent(trip_id=1, student_id=102, boarding_status="did_not_board",
                        drop_status="pending"),
            TripStudent(trip_id=3, student_id=101, boarding_status="pending", drop_status="pending"),
            TripStudent(trip_id=3, student_id=102, boarding_status="pending", drop_status="pending"),
            TripStudent(trip_id=5, student_id=101, boarding_status="picked", drop_status="pending"),
            TripStudent(trip_id=5, student_id=103, boarding_status="pending", drop_status="pending"),
            TripStudent(trip_id=7, student_id=201, boarding_status="picked", drop_status="pending"),
        ]
    )
    db.commit()


def tearDownModule():
    for engine in _ENGINES:
        engine.dispose()


class TripHistoryApiTests(unittest.TestCase):
    """Router handlers executed directly (media-test convention)."""

    _saved = None

    @classmethod
    def setUpClass(cls):
        service_dir = str(ROOT / "services" / "transport_service")
        sys.path.insert(0, service_dir)
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

        @event.listens_for(cls.engine, "connect")
        def _fk_on(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        Base.metadata.create_all(
            cls.engine, tables=[Base.metadata.tables[name] for name in TRIP_TABLES]
        )
        cls.Session = sessionmaker(bind=cls.engine, autoflush=False, future=True)

    @classmethod
    def tearDownClass(cls):
        for name, mod in cls._saved.items():
            if mod is not None:
                sys.modules[name] = mod
        sys.path.pop(0)

    def setUp(self):
        # PRAGMA foreign_keys is a no-op inside a transaction, so the ON toggle
        # runs only after the DELETE work is committed (same as the model tests).
        self.db = self.Session()
        raw = self.engine.raw_connection()
        try:
            cur = raw.cursor()
            cur.execute("PRAGMA foreign_keys=OFF")
            for name in TRIP_TABLES:
                cur.execute('DELETE FROM "{}"'.format(name))
            cur.close()
            raw.commit()
            cur = raw.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()
        finally:
            raw.commit()
            raw.close()
        seed(self.db)
        seed_trips(self.db)

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def _admin1(self):
        return CurrentUser(user_id=10, role="admin", school_id=1, linked_person_id=None)

    def _admin2(self):
        return CurrentUser(user_id=11, role="admin", school_id=2, linked_person_id=None)

    def _pilot_staff(self, staff_id):
        return CurrentUser(user_id=900 + staff_id, role="pilot", school_id=1, linked_person_id=staff_id)

    def _pilot3_school2(self):
        return CurrentUser(user_id=914, role="pilot", school_id=2, linked_person_id=3)

    def _parent(self, linked_person_id):
        return CurrentUser(user_id=700 + linked_person_id, role="parent", school_id=1, linked_person_id=linked_person_id)

    def _parent3_school2(self):
        return CurrentUser(user_id=720, role="parent", school_id=2, linked_person_id=20)

    # --- Admin list -------------------------------------------------------

    def test_admin_lists_all_history_newest_first(self):
        rows = self.router.list_trips(db=self.db, school_id=1, current_user=self._admin1())
        self.assertEqual([r["trip_id"] for r in rows], [8, 5, 4, 3, 2, 1, 6])
        by_id = {r["trip_id"]: r for r in rows}
        self.assertEqual(by_id[1]["route_name"], "Route A")
        self.assertEqual(by_id[1]["driver_name"], "Ramesh")
        self.assertEqual(by_id[1]["vehicle"], "KA-01-AB-1234")
        self.assertEqual(by_id[5]["route_name"], "Route B")

    def test_admin_only_sees_own_school_trips(self):
        rows1 = self.router.list_trips(db=self.db, school_id=1, current_user=self._admin1())
        self.assertNotIn(7, [r["trip_id"] for r in rows1])
        rows2 = self.router.list_trips(db=self.db, school_id=2, current_user=self._admin2())
        self.assertEqual([r["trip_id"] for r in rows2], [7])

    def test_admin_filters_by_route(self):
        rows = self.router.list_trips(db=self.db, school_id=1, route_id=2, current_user=self._admin1())
        self.assertEqual([r["trip_id"] for r in rows], [5, 6])

    def test_admin_filters_by_pilot(self):
        rows = self.router.list_trips(db=self.db, school_id=1, pilot_id=2, current_user=self._admin1())
        self.assertEqual([r["trip_id"] for r in rows], [5, 6])

    def test_admin_filters_by_direction(self):
        rows = self.router.list_trips(db=self.db, school_id=1, direction="drop", current_user=self._admin1())
        self.assertEqual([r["trip_id"] for r in rows], [2])

    def test_admin_filters_by_status(self):
        rows = self.router.list_trips(db=self.db, school_id=1, status="cancelled", current_user=self._admin1())
        self.assertEqual([r["trip_id"] for r in rows], [4])
        rows = self.router.list_trips(db=self.db, school_id=1, status="completed", current_user=self._admin1())
        self.assertEqual([r["trip_id"] for r in rows], [5, 2, 1, 6])

    def test_admin_filters_by_date_range(self):
        rows = self.router.list_trips(
            db=self.db, school_id=1, from_date=date(2026, 9, 3), to_date=date(2026, 9, 4),
            current_user=self._admin1(),
        )
        self.assertEqual([r["trip_id"] for r in rows], [5, 4])

    def test_admin_empty_history_returns_empty_list(self):
        rows = self.router.list_trips(db=self.db, school_id=2, status="cancelled", current_user=self._admin2())
        self.assertEqual(rows, [])

    def test_admin_outcome_summary_is_deterministic(self):
        rows = {r["trip_id"]: r for r in self.router.list_trips(db=self.db, school_id=1, current_user=self._admin1())}
        self.assertEqual(rows[1]["outcome_summary"], "1 picked · 1 did not board · 1 dropped")
        self.assertEqual(rows[3]["outcome_summary"], "")
        self.assertEqual(rows[8]["outcome_summary"], "")

    def test_admin_list_rows_validate_against_response_schema(self):
        from services.transport_service.schemas import AdminTripRead

        rows = self.router.list_trips(db=self.db, school_id=1, current_user=self._admin1())
        for row in rows:
            model = AdminTripRead(**row)
            self.assertEqual(model.trip_id, row["trip_id"])

    # --- Admin detail -----------------------------------------------------

    def test_admin_trip_detail_includes_students_and_reopen_fields(self):
        detail = self.router.get_trip_detail(db=self.db, school_id=1, trip_id=1, current_user=self._admin1())
        self.assertEqual(detail["status"], "completed")
        self.assertEqual([s["student_name"] for s in detail["students"]], ["Aarav Rao", "Anika Rao"])
        aarav = detail["students"][0]
        self.assertEqual(aarav["boarding_status"], "picked")
        self.assertEqual(aarav["drop_status"], "dropped")
        self.assertIsNone(detail["cancelled_at"])
        self.assertIsNone(detail["reopen_reason"])

    def test_admin_detail_has_cancellation_history(self):
        detail = self.router.get_trip_detail(db=self.db, school_id=1, trip_id=4, current_user=self._admin1())
        self.assertEqual(detail["status"], "cancelled")
        self.assertEqual(detail["cancelled_by"], 10)
        self.assertEqual(detail["cancellation_reason"], "Rain")

    def test_admin_detail_validates_against_response_schema(self):
        from services.transport_service.schemas import AdminTripDetailRead

        model = AdminTripDetailRead(**self.router.get_trip_detail(db=self.db, school_id=1, trip_id=1, current_user=self._admin1()))
        self.assertEqual(len(model.students), 2)
        self.assertEqual(model.outcome_summary, "1 picked · 1 did not board · 1 dropped")

    def test_admin_cannot_get_other_school_trip(self):
        with self.assertRaises(NotFoundError):
            self.router.get_trip_detail(db=self.db, school_id=1, trip_id=7, current_user=self._admin1())

    def test_admin_detail_unknown_trip_is_not_found(self):
        with self.assertRaises(NotFoundError):
            self.router.get_trip_detail(db=self.db, school_id=1, trip_id=999, current_user=self._admin1())

    # --- Pilot /trips/mine ------------------------------------------------

    def test_pilot_lists_only_own_trips(self):
        rows = self.router.my_trips(db=self.db, school_id=1, current_user=self._pilot_staff(1))
        self.assertEqual([r["trip_id"] for r in rows], [8, 4, 3, 2, 1])

    def test_pilot_never_sees_other_pilots_trips(self):
        rows = self.router.my_trips(db=self.db, school_id=1, current_user=self._pilot_staff(1))
        self.assertNotIn(5, [r["trip_id"] for r in rows])
        self.assertNotIn(6, [r["trip_id"] for r in rows])
        rows2 = self.router.my_trips(db=self.db, school_id=1, current_user=self._pilot_staff(2))
        self.assertEqual([r["trip_id"] for r in rows2], [5, 6])

    def test_pilot_rows_validate_against_response_schema(self):
        from services.transport_service.schemas import TripSummaryRead

        for row in self.router.my_trips(db=self.db, school_id=1, current_user=self._pilot_staff(1)):
            model = TripSummaryRead(**row)
            self.assertFalse(hasattr(model, "outcome_summary"))

    def test_pilot_date_filter(self):
        rows = self.router.my_trips(db=self.db, school_id=1, from_date=date(2026, 9, 3), current_user=self._pilot_staff(1))
        self.assertEqual([r["trip_id"] for r in rows], [8, 4])
        rows = self.router.my_trips(
            db=self.db, school_id=1, from_date=date(2026, 9, 3), to_date=date(2026, 9, 3),
            current_user=self._pilot_staff(1),
        )
        self.assertEqual([r["trip_id"] for r in rows], [4])

    def test_pilot_mine_has_no_identity_query_param(self):
        source = ROUTER_SOURCE.read_text()
        signature = source.split("def my_trips(", 1)[1].split("):", 1)[0]
        self.assertNotIn("pilot_id", signature, "pilot identity must come from the JWT, not the query string")

    def test_pilot_with_no_linked_pilot_gets_empty_history(self):
        rows = self.router.my_trips(db=self.db, school_id=1, current_user=self._pilot_staff(4))
        self.assertEqual(rows, [])
        user_none = CurrentUser(user_id=915, role="pilot", school_id=1, linked_person_id=None)
        self.assertEqual(self.router.my_trips(db=self.db, school_id=1, current_user=user_none), [])

    def test_pilot_is_school_scoped(self):
        user = CurrentUser(user_id=914, role="pilot", school_id=2, linked_person_id=3)
        rows = self.router.my_trips(db=self.db, school_id=2, current_user=user)
        self.assertEqual([r["trip_id"] for r in rows], [7])

    # --- Parent /trips/children/{student_id} ------------------------------

    def test_parent_sees_own_childs_completed_trips(self):
        rows = self.router.parent_child_trips(db=self.db, school_id=1, student_id=101, current_user=self._parent(10))
        self.assertEqual([r["trip_id"] for r in rows], [5, 1])
        t1 = rows[1]
        self.assertEqual(t1["route_name"], "Route A")
        self.assertEqual(t1["driver_name"], "Ramesh")
        self.assertEqual(t1["boarding_status"], "picked")
        self.assertEqual(t1["boarding_stop_name"], "Koramangala")
        self.assertEqual(t1["drop_status"], "dropped")
        self.assertEqual(t1["drop_stop_name"], "Indiranagar")

    def test_parent_each_child_gets_own_rows(self):
        child101 = self.router.parent_child_trips(db=self.db, school_id=1, student_id=101, current_user=self._parent(10))
        child102 = self.router.parent_child_trips(db=self.db, school_id=1, student_id=102, current_user=self._parent(10))
        self.assertEqual([r["trip_id"] for r in child101], [5, 1])
        self.assertEqual([r["trip_id"] for r in child102], [1])
        self.assertEqual(child102[0]["boarding_status"], "did_not_board")

    def test_parent_response_never_contains_sibling_rows(self):
        rows = self.router.parent_child_trips(db=self.db, school_id=1, student_id=103, current_user=self._parent(11))
        self.assertEqual(len(rows), 1)
        row = rows[0]
        # Trip 5 also carries student 101 (picked); the parent of 103 must only
        # ever see 103's own snapshot, i.e. 'pending'.
        self.assertEqual(row["boarding_status"], "pending")

    def test_parent_sees_completed_trips_only(self):
        rows = self.router.parent_child_trips(db=self.db, school_id=1, student_id=101, current_user=self._parent(10))
        self.assertNotIn(3, [r["trip_id"] for r in rows])  # in_progress
        self.assertNotIn(4, [r["trip_id"] for r in rows])  # cancelled

    def test_parent_other_parents_child_is_refused(self):
        with self.assertRaises(ForbiddenError):
            self.router.parent_child_trips(db=self.db, school_id=1, student_id=103, current_user=self._parent(10))

    def test_parent_arbitrary_student_id_is_refused(self):
        with self.assertRaises(ForbiddenError):
            self.router.parent_child_trips(db=self.db, school_id=1, student_id=999, current_user=self._parent(10))

    def test_parent_cross_school_child_is_refused(self):
        with self.assertRaises(ForbiddenError):
            self.router.parent_child_trips(db=self.db, school_id=1, student_id=201, current_user=self._parent(10))
        with self.assertRaises(ForbiddenError):
            self.router.parent_child_trips(db=self.db, school_id=2, student_id=101, current_user=self._parent3_school2())

    def test_parent_date_range_filter(self):
        rows = self.router.parent_child_trips(
            db=self.db, school_id=1, student_id=101, from_date=date(2026, 9, 4),
            current_user=self._parent(10),
        )
        self.assertEqual([r["trip_id"] for r in rows], [5])

    def test_parent_empty_history_returns_empty_list(self):
        rows = self.router.parent_child_trips(db=self.db, school_id=1, student_id=104, current_user=self._parent(12))
        self.assertEqual(rows, [])
        rows = self.router.parent_child_trips(
            db=self.db, school_id=1, student_id=101, from_date=date(2026, 10, 1),
            current_user=self._parent(10),
        )
        self.assertEqual(rows, [])

    def test_parent_rows_validate_against_response_schema(self):
        from services.transport_service.schemas import ParentTripRead

        for row in self.router.parent_child_trips(db=self.db, school_id=1, student_id=101, current_user=self._parent(10)):
            model = ParentTripRead(**row)
            self.assertIn(model.boarding_status, ("picked", "did_not_board", "pending"))

    # --- Input validation -------------------------------------------------

    def test_invalid_date_range_is_rejected(self):
        with self.assertRaises(AppError):
            self.router.list_trips(
                db=self.db, school_id=1, from_date=date(2026, 9, 4), to_date=date(2026, 9, 1),
                current_user=self._admin1(),
            )

    def test_invalid_status_is_rejected(self):
        with self.assertRaises(AppError):
            self.router.list_trips(db=self.db, school_id=1, status="bogus", current_user=self._admin1())

    def test_invalid_direction_is_rejected(self):
        with self.assertRaises(AppError):
            self.router.list_trips(db=self.db, school_id=1, direction="bogus", current_user=self._admin1())

    def test_cross_school_filter_ids_are_rejected(self):
        with self.assertRaises(AppError):
            self.router.list_trips(db=self.db, school_id=1, route_id=3, current_user=self._admin1())
        with self.assertRaises(AppError):
            self.router.list_trips(db=self.db, school_id=1, pilot_id=3, current_user=self._admin1())

    def test_route_filter_with_valid_but_unmatched_id_is_not_an_error(self):
        rows = self.router.list_trips(db=self.db, school_id=1, route_id=1, current_user=self._admin1())
        self.assertGreater(len(rows), 0)

    # --- Guard rails (read-only phase + phase-3 write surface) ------------

    def test_trips_router_surface_is_exactly_get_plus_lifecycle_writes(self):
        """GET-only guard from Phase 2, extended for Phase 3: PUT/DELETE stay
        forbidden and the write surface is exactly the lifecycle endpoints."""
        source = ROUTER_SOURCE.read_text()
        for method in ("put", "delete"):
            self.assertNotIn("trips_router.{}(".format(method), source)
        expected_writes = {
            ("trips_router.post", '""'),
            ("trips_router.post", '"/{trip_id}/start"'),
            ("trips_router.post", '"/{trip_id}/complete"'),
            ("trips_router.post", '"/{trip_id}/cancel"'),
            ("trips_router.post", '"/{trip_id}/reopen"'),
            ("trips_router.patch", '"/{trip_id}/students/{student_id}"'),
        }
        for verb, path in sorted(expected_writes):
            self.assertIn("{}({}".format(verb, path), source)

    def _trips_router_decorator_paths(self, source):
        tree = ast.parse(source)
        paths = []
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                for dec in node.decorator_list:
                    call = dec
                    if isinstance(dec, ast.Call) and getattr(dec.func, "attr", None) == "get" \
                            and getattr(dec.func, "value", None) is not None \
                            and getattr(dec.func.value, "id", None) == "trips_router":
                        paths.append(ast.literal_eval(dec.args[0]))
        return paths

    def test_trips_router_declares_role_guards(self):
        source = ROUTER_SOURCE.read_text()
        tree = ast.parse(source)
        expected = {
            "list_trips": "admin",
            "my_trips": "pilot",
            "parent_child_trips": "parent",
            "get_trip_detail": "admin",
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in expected:
                defaults = [ast.unparse(d) for d in node.args.defaults]
                guard = "require_role('{}')".format(expected[node.name])
                self.assertTrue(
                    any(guard in d for d in defaults),
                    "{} must guard current_user with {}".format(node.name, guard),
                )

    def test_mine_is_registered_before_wildcard_detail(self):
        paths = self._trips_router_decorator_paths(ROUTER_SOURCE.read_text())
        self.assertIn("/mine", paths)
        self.assertIn("/{trip_id}", paths)
        self.assertLess(paths.index("/mine"), paths.index("/{trip_id}"))

    def test_gateway_routes_trips_to_transport(self):
        import gateway.main as gateway_main

        self.assertEqual(gateway_main.ROUTE_MAP["trips"], "transport")


def User_(user_id, school_id, role, username, password_hash):
    from common.models import User

    return User(user_id=user_id, school_id=school_id, role=role, username=username, password_hash=password_hash)