"""
Trip lifecycle + historical TripStudent recording tests (Phase 3).

Executes the write endpoints of the transport service directly (the repo's
media-test convention) against in-memory SQLite with foreign keys enforced:

  POST   /api/v1/trips                              create (admin / owning pilot)
  POST   /api/v1/trips/{trip_id}/start              scheduled -> in_progress
  POST   /api/v1/trips/{trip_id}/complete           in_progress -> completed
  POST   /api/v1/trips/{trip_id}/cancel             {scheduled,in_progress} -> cancelled
  POST   /api/v1/trips/{trip_id}/reopen             cancelled -> in_progress (admin only)
  PATCH  /api/v1/trips/{trip_id}/students/{student_id}   boarding/drop snapshot

The lifecycle matrix lives in repository.apply_transition:
  scheduled  -> in_progress | cancelled
  in_progress -> completed  | cancelled
  cancelled  -> in_progress     (reopen, admin only)
  completed  -> (nothing)

Design rules under test:
  * Trip creation snapshots vehicle/driver/pilot from the route's own records;
    a duplicate (route, trip_date, direction) is a 409 Conflict.
  * The TripStudent roster is copied from RouteStudent at creation and is then
    the historical source of truth - later RouteStudent moves leave it alone.
  * Student outcomes are recorded only while the trip is in_progress. Nothing
    is auto-finalized when the trip completes.
  * Timestamps are server-side; the client never supplies them.
  * Existing pick/drop endpoints (/routes/mine, PATCH .../status) are unchanged.
"""
import ast
import sys
import unittest
from datetime import date

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from pydantic import ValidationError

from common.dependencies import CurrentUser
from common.exceptions import AppError, ConflictError, ForbiddenError, NotFoundError
from common.models import (
    Base,
    Parent,
    ParentStudent,
    Pilot,
    Route,
    RouteStudent,
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

ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent
ROUTER_SOURCE = ROOT / "services" / "transport_service" / "router.py"

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
    """Insert rows in FK-dependency order (PRAGMA foreign_keys is ON)."""
    db.add_all(
        [
            User(user_id=1, school_id=1, role="admin", username="admin1", password_hash="x"),
            User(user_id=2, school_id=2, role="admin", username="admin2", password_hash="x"),
            User(user_id=3, school_id=1, role="pilot", username="pilot1", password_hash="x"),
            User(user_id=4, school_id=1, role="pilot", username="pilot2", password_hash="x"),
            User(user_id=5, school_id=2, role="pilot", username="pilot3", password_hash="x"),
            User(user_id=6, school_id=1, role="parent", username="parent1", password_hash="x"),
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
            Student(student_id=101, school_id=1, class_id=1, admission_no="ADM101", name="Aarav Rao"),
            Student(student_id=102, school_id=1, class_id=1, admission_no="ADM102", name="Anika Rao"),
            Student(student_id=103, school_id=1, class_id=1, admission_no="ADM103", name="Rohit Rao"),
            Student(student_id=104, school_id=1, class_id=1, admission_no="ADM104", name="On No Route"),
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
        ]
    )
    db.commit()
    db.add_all(
        [
            Pilot(pilot_id=1, staff_id=1, route_id=1),
            Pilot(pilot_id=2, staff_id=2, route_id=2),
            Pilot(pilot_id=3, staff_id=3, route_id=3),
            ParentStudent(parent_id=10, student_id=101, relationship_="Mother"),
            RouteStudent(route_id=1, student_id=101),
            RouteStudent(route_id=1, student_id=102),
            RouteStudent(route_id=2, student_id=103),
            RouteStudent(route_id=3, student_id=201),
        ]
    )
    db.commit()


def tearDownModule():
    for engine in _ENGINES:
        engine.dispose()


class TripLifecycleTests(unittest.TestCase):
    """Router write handlers executed directly (media-test convention)."""

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
        self.admin1 = CurrentUser(user_id=1, role="admin", school_id=1, linked_person_id=None)
        self.admin2 = CurrentUser(user_id=2, role="admin", school_id=2, linked_person_id=None)
        self.pilot1 = CurrentUser(user_id=3, role="pilot", school_id=1, linked_person_id=1)
        self.pilot2 = CurrentUser(user_id=4, role="pilot", school_id=1, linked_person_id=2)
        self.pilot3 = CurrentUser(user_id=5, role="pilot", school_id=2, linked_person_id=3)
        self.parent1 = CurrentUser(user_id=6, role="parent", school_id=1, linked_person_id=10)

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    # --- quick handlers ---------------------------------------------------

    def _create(self, by=None, route_id=1, trip_date=date(2026, 9, 1), direction="pickup"):
        from services.transport_service.schemas import TripCreate

        by = by or self.admin1
        return self.router.create_trip(
            payload=TripCreate(route_id=route_id, trip_date=trip_date, direction=direction),
            db=self.db, school_id=by.school_id, current_user=by,
        )

    def _start(self, trip_id, by=None):
        by = by or self.admin1
        return self.router.start_trip(trip_id=trip_id, db=self.db, school_id=by.school_id, current_user=by)

    def _complete(self, trip_id, by=None):
        by = by or self.admin1
        return self.router.complete_trip(trip_id=trip_id, db=self.db, school_id=by.school_id, current_user=by)

    def _cancel(self, trip_id, reason="Rain", by=None):
        from services.transport_service.schemas import TripCancel

        by = by or self.admin1
        return self.router.cancel_trip(
            trip_id=trip_id, payload=TripCancel(cancellation_reason=reason),
            db=self.db, school_id=by.school_id, current_user=by,
        )

    def _reopen(self, trip_id, reason="Weather cleared", by=None):
        from services.transport_service.schemas import TripReopen

        by = by or self.admin1
        return self.router.reopen_trip(
            trip_id=trip_id, payload=TripReopen(reopen_reason=reason),
            db=self.db, school_id=by.school_id, current_user=by,
        )

    def _update(self, trip_id, student_id, payload, by=None):
        from services.transport_service.schemas import TripStudentUpdate

        by = by or self.admin1
        return self.router.update_trip_student(
            trip_id=trip_id, student_id=student_id, payload=TripStudentUpdate(**payload),
            db=self.db, school_id=by.school_id, current_user=by,
        )

    def _db_trip(self, trip_id):
        return self.db.query(Trip).filter(Trip.trip_id == trip_id).first()

    # --- CREATION (1-9) ----------------------------------------------------

    def test_admin_can_create_trip(self):
        detail = self._create()
        self.assertEqual(detail["status"], "scheduled")
        self.assertIsNotNone(detail["trip_id"])

    def test_owning_pilot_can_create_trip(self):
        detail = self._create(by=self.pilot1)
        self.assertEqual(detail["route_id"], 1)
        self.assertEqual(detail["trip_id"], self._db_trip(detail["trip_id"]).trip_id)

    def test_pilot_cannot_create_trip_for_other_route(self):
        with self.assertRaises(ForbiddenError):
            self._create(by=self.pilot2, route_id=1)

    def test_route_snapshot(self):
        detail = self._create(route_id=2)
        self.assertEqual(detail["route_id"], 2)
        self.assertEqual(detail["route_name"], "Route B")

    def test_pilot_snapshot(self):
        detail = self._create(route_id=1)
        self.assertEqual(detail["pilot_id"], 1)

    def test_driver_name_snapshot(self):
        detail = self._create(route_id=1)
        self.assertEqual(detail["driver_name"], "Ramesh")

    def test_vehicle_snapshot(self):
        detail = self._create(route_id=1)
        self.assertEqual(detail["vehicle"], "KA-01-AB-1234")
        # Changing the route's vehicle afterwards must not rewrite the snapshot.
        route = self.db.query(Route).filter(Route.route_id == 1).one()
        route.vehicle = "KA-99-NEW"
        self.db.commit()
        reloaded = self.router.get_trip_detail(db=self.db, school_id=1, trip_id=detail["trip_id"], current_user=self.admin1)
        self.assertEqual(reloaded["vehicle"], "KA-01-AB-1234")

    def test_direction_and_date(self):
        detail = self._create(direction="drop", trip_date=date(2026, 9, 5))
        self.assertEqual(detail["direction"], "drop")
        self.assertEqual(detail["trip_date"], date(2026, 9, 5))

    def test_duplicate_trip_rejected(self):
        self._create()
        with self.assertRaises(ConflictError):
            self._create()
        # A second direction or a second day is NOT a duplicate.
        self._create(direction="drop")
        self._create(trip_date=date(2026, 9, 2))

    # --- STUDENTS (10-12) --------------------------------------------------

    def test_route_students_populate_roster_at_creation(self):
        detail = self._create(route_id=1)
        self.assertEqual([s["student_id"] for s in detail["students"]], [101, 102])
        rows = self.db.query(TripStudent).filter(TripStudent.trip_id == detail["trip_id"]).all()
        self.assertEqual(sorted(r.student_id for r in rows), [101, 102])
        for r in rows:
            self.assertEqual(r.boarding_status, "pending")
            self.assertEqual(r.drop_status, "pending")

    def test_historical_roster_survives_route_moves(self):
        trip = self._create(route_id=1)
        # Move student 102 from route 1 to route 2 a day later.
        self.db.query(RouteStudent).filter(RouteStudent.route_id == 1, RouteStudent.student_id == 102).delete()
        self.db.add(RouteStudent(route_id=2, student_id=102))
        self.db.commit()
        rows = self.db.query(TripStudent).filter(TripStudent.trip_id == trip["trip_id"]).all()
        self.assertEqual(sorted(r.student_id for r in rows), [101, 102], "old trips keep their roster")
        # A new trip for route 2 reflects the moved child.
        detail2 = self._create(route_id=2)
        self.assertEqual([s["student_id"] for s in detail2["students"]], [102, 103])

    def test_student_not_on_route_is_not_inserted(self):
        detail = self._create(route_id=1)
        roster = [s["student_id"] for s in detail["students"]]
        self.assertNotIn(103, roster)   # 103 is on route 2
        self.assertNotIn(104, roster)   # 104 is on no route

    # --- START (13-15) -----------------------------------------------------

    def test_start_goes_in_progress(self):
        detail = self._create()
        started = self._start(detail["trip_id"])
        self.assertEqual(started["status"], "in_progress")

    def test_start_records_started_at(self):
        detail = self._create()
        started = self._start(detail["trip_id"])
        self.assertIsNotNone(started["started_at"])

    def test_invalid_start_rejected(self):
        trip = self._create()
        id_ = trip["trip_id"]
        self._start(id_)
        self._complete(id_)
        with self.assertRaises(AppError):
            self._start(id_)  # completed -> in_progress is not allowed

    def test_pilot_start_own_trip_and_admin_start_any(self):
        created = self._create(route_id=1)
        started = self._start(created["trip_id"], by=self.pilot1)
        self.assertEqual(started["status"], "in_progress")
        created2 = self._create(route_id=2)
        started2 = self._start(created2["trip_id"], by=self.admin1)
        self.assertEqual(started2["status"], "in_progress")

    # --- BOARDING (16-21) --------------------------------------------------

    def _started_trip(self, route_id=1):
        detail = self._create(route_id=route_id)
        self._start(detail["trip_id"])
        return detail

    def test_boarding_pending_to_picked(self):
        trip = self._started_trip()
        row = self._update(trip["trip_id"], 101, {"boarding_status": "picked"})
        self.assertEqual(row["boarding_status"], "picked")
        db_row = self.db.query(TripStudent).filter(
            TripStudent.trip_id == trip["trip_id"], TripStudent.student_id == 101
        ).one()
        self.assertEqual(db_row.boarding_status, "picked")

    def test_boarding_timestamp_recorded(self):
        trip = self._started_trip()
        row = self._update(trip["trip_id"], 101, {"boarding_status": "picked"})
        self.assertIsNotNone(row["boarding_at"])

    def test_boarding_stop_recorded_when_supplied(self):
        trip = self._started_trip()
        row = self._update(trip["trip_id"], 101, {"boarding_status": "picked", "boarding_stop_id": 1})
        self.assertEqual(row["boarding_stop_id"], 1)

    def test_boarding_stop_not_derived(self):
        trip = self._started_trip()
        row = self._update(trip["trip_id"], 101, {"boarding_status": "did_not_board"})
        self.assertIsNone(row["boarding_stop_id"], "no stop is fabricated when none is chosen")

    def test_invalid_boarded_stop_rejected(self):
        trip = self._started_trip()
        for bad in (2, 3, 999):  # a drop-type stop, another route's stop, and a missing stop
            with self.assertRaises(AppError):
                self._update(trip["trip_id"], 101, {"boarding_status": "picked", "boarding_stop_id": bad})
            with self.assertRaises(AppError):
                self._update(trip["trip_id"], 101, {"boarding_stop_id": bad})

    def test_invalid_boarding_transition_rejected(self):
        trip = self._started_trip()
        self._update(trip["trip_id"], 101, {"boarding_status": "picked"})
        with self.assertRaises(AppError):
            self._update(trip["trip_id"], 101, {"boarding_status": "did_not_board"})  # already picked

    # --- DROP (22-25) ------------------------------------------------------

    def test_drop_pending_to_dropped(self):
        trip = self._started_trip()
        row = self._update(trip["trip_id"], 101, {"drop_status": "dropped"})
        self.assertEqual(row["drop_status"], "dropped")

    def test_drop_timestamp_recorded(self):
        trip = self._started_trip()
        row = self._update(trip["trip_id"], 101, {"drop_status": "dropped"})
        self.assertIsNotNone(row["drop_at"])

    def test_drop_stop_recorded_when_supplied(self):
        trip = self._started_trip()
        row = self._update(trip["trip_id"], 101, {"drop_status": "dropped", "drop_stop_id": 2})
        self.assertEqual(row["drop_stop_id"], 2)

    def test_invalid_drop_stop_rejected(self):
        trip = self._started_trip()
        with self.assertRaises(AppError):
            self._update(trip["trip_id"], 101, {"drop_status": "dropped", "drop_stop_id": 1})  # pickup-type
        with self.assertRaises(AppError):
            self._update(trip["trip_id"], 101, {"drop_status": "dropped", "drop_stop_id": 3})  # route 3

    def test_drop_not_recorded_explicit(self):
        trip = self._started_trip()
        row = self._update(trip["trip_id"], 101, {"drop_status": "drop_not_recorded"})
        self.assertEqual(row["drop_status"], "drop_not_recorded")

    # --- LIFECYCLE GUARDS ON STUDENT UPDATES (20-21) -----------------------

    def test_student_update_rejected_when_trip_not_in_progress(self):
        trip = self._create()
        with self.assertRaises(AppError):
            self._update(trip["trip_id"], 101, {"boarding_status": "picked"})  # still scheduled

    def test_unauthorized_pilot_cannot_update_students(self):
        trip = self._started_trip(route_id=1)
        with self.assertRaises(ForbiddenError):
            self._update(trip["trip_id"], 101, {"boarding_status": "picked"}, by=self.pilot2)
        with self.assertRaises(NotFoundError):
            self._update(trip["trip_id"], 103, {"boarding_status": "picked"}, by=self.admin1)  # not on roster

    # --- COMPLETE (26-28) --------------------------------------------------

    def test_complete_goes_completed(self):
        trip = self._started_trip()
        done = self._complete(trip["trip_id"])
        self.assertEqual(done["status"], "completed")

    def test_complete_records_ended_at(self):
        trip = self._started_trip()
        done = self._complete(trip["trip_id"])
        self.assertIsNotNone(done["ended_at"])

    def test_complete_does_not_finalize_pending_outcomes(self):
        trip = self._started_trip()
        done = self._complete(trip["trip_id"])
        rows = {s["student_id"]: s for s in done["students"]}
        self.assertEqual(rows[101]["boarding_status"], "pending")
        self.assertEqual(rows[101]["drop_status"], "pending")

    def test_invalid_transition_to_complete_rejected(self):
        trip = self._create()
        with self.assertRaises(AppError):
            self._complete(trip["trip_id"])  # scheduled -> completed

    # --- CANCEL (29-32) ----------------------------------------------------

    def test_cancel_live_trip_succeeds(self):
        trip = self._started_trip()
        cancelled = self._cancel(trip["trip_id"])
        self.assertEqual(cancelled["status"], "cancelled")

    def test_cancel_scheduled_trip_succeeds(self):
        trip = self._create()
        cancelled = self._cancel(trip["trip_id"])
        self.assertEqual(cancelled["status"], "cancelled")

    def test_cancel_records_audit_fields(self):
        trip = self._started_trip()
        cancelled = self._cancel(trip["trip_id"], reason="Heavy rain", by=self.admin1)
        self.assertIsNotNone(cancelled["cancelled_at"])
        self.assertEqual(cancelled["cancelled_by"], 1)          # the authenticated actor
        self.assertEqual(cancelled["cancellation_reason"], "Heavy rain")

    def test_pilot_can_cancel_own_trip(self):
        trip = self._started_trip()
        cancelled = self._cancel(trip["trip_id"], reason="Route blocked", by=self.pilot1)
        self.assertEqual(cancelled["cancelled_by"], 3)

    def test_unauthorized_cancellation_rejected(self):
        trip = self._started_trip(route_id=1)
        with self.assertRaises(ForbiddenError):
            self._cancel(trip["trip_id"], by=self.pilot2)

    def test_invalid_cancellation_rejected(self):
        trip = self._started_trip()
        self._complete(trip["trip_id"])
        with self.assertRaises(AppError):
            self._cancel(trip["trip_id"])  # completed -> cancelled

    # --- REOPEN (33-37) ----------------------------------------------------

    def test_reopen_cancelled_trip_in_progress(self):
        trip = self._create()
        self._cancel(trip["trip_id"])
        reopened = self._reopen(trip["trip_id"])
        self.assertEqual(reopened["status"], "in_progress")

    def test_reopen_records_audit_fields(self):
        trip = self._create()
        self._cancel(trip["trip_id"])
        reopened = self._reopen(trip["trip_id"], reason="Rain stopped", by=self.admin1)
        self.assertIsNotNone(reopened["reopened_at"])
        self.assertEqual(reopened["reopened_by"], 1)
        self.assertEqual(reopened["reopen_reason"], "Rain stopped")

    def test_unauthorized_reopen_rejected(self):
        trip = self._create()
        self._cancel(trip["trip_id"])
        with self.assertRaises(ForbiddenError):
            self._reopen(trip["trip_id"], by=self.pilot1)

    def test_completed_trip_cannot_reopen(self):
        trip = self._started_trip()
        self._complete(trip["trip_id"])
        with self.assertRaises(AppError):
            self._reopen(trip["trip_id"])

    def test_reopen_keeps_same_trip_row(self):
        trip = self._create()
        original_id = trip["trip_id"]
        self._cancel(original_id)
        reopened = self._reopen(original_id)
        self.assertEqual(reopened["trip_id"], original_id)
        count = self.db.query(Trip).filter(
            Trip.route_id == 1, Trip.trip_date == date(2026, 9, 1), Trip.direction == "pickup"
        ).count()
        self.assertEqual(count, 1, "reopen must not create a second trip")

    # --- SECURITY (38-42) --------------------------------------------------

    def test_pilot_cannot_operate_another_pilots_trip(self):
        trip1 = self._started_trip(route_id=1)
        trip2 = self._started_trip(route_id=2)
        with self.assertRaises(ForbiddenError):
            self._start(trip1["trip_id"], by=self.pilot2)
        with self.assertRaises(ForbiddenError):
            self._complete(trip1["trip_id"], by=self.pilot2)
        with self.assertRaises(ForbiddenError):
            self._cancel(trip1["trip_id"], by=self.pilot2)
        with self.assertRaises(ForbiddenError):
            self._complete(trip2["trip_id"], by=self.pilot1)  # route 2 belongs to pilot 2

    def test_cross_school_access_denied(self):
        trip1 = self._started_trip(route_id=1)   # school 1
        with self.assertRaises(NotFoundError):
            self._start(trip1["trip_id"], by=self.admin2)
        with self.assertRaises(NotFoundError):
            self._complete(trip1["trip_id"], by=self.pilot3)
        with self.assertRaises(NotFoundError):
            self._create(route_id=3, by=self.admin1)  # route 3 belongs to school 2
        self._create(route_id=3, by=self.admin2)

    def test_parent_cannot_write_trip_lifecycle(self):
        with self.assertRaises(ForbiddenError):
            self._create(by=self.parent1)
        trip = self._create()
        with self.assertRaises(ForbiddenError):
            self._start(trip["trip_id"], by=self.parent1)
        self._start(trip["trip_id"])
        with self.assertRaises(ForbiddenError):
            self._complete(trip["trip_id"], by=self.parent1)
        with self.assertRaises(ForbiddenError):
            self._cancel(trip["trip_id"], by=self.parent1)

    def test_parent_cannot_mutate_trip_student(self):
        trip = self._started_trip()
        with self.assertRaises(ForbiddenError):
            self._update(trip["trip_id"], 101, {"boarding_status": "picked"}, by=self.parent1)

    def test_admin_school_isolation_enforced(self):
        with self.assertRaises(NotFoundError):
            self.router.get_trip_detail(db=self.db, school_id=2, trip_id=self._create()["trip_id"], current_user=self.admin2)

    # --- COMPATIBILITY (43-45) ----------------------------------------------

    def test_routes_mine_unchanged(self):
        rows = self.router.my_pickdrop_status(db=self.db, school_id=1, current_user=self.parent1)
        self.assertIn(101, [r["student_id"] for r in rows])

    def test_routestudent_patch_unchanged_and_does_not_create_history(self):
        from services.transport_service.schemas import RouteStudentStatusUpdate

        before = self.db.query(Trip).count()
        result = self.router.update_pickup_drop_status(
            route_id=1, student_id=101,
            payload=RouteStudentStatusUpdate(status="picked"),
            db=self.db, current_user=self.pilot1,
        )
        self.assertEqual(result["status"], "picked")
        live = self.db.query(RouteStudent).filter(RouteStudent.route_id == 1, RouteStudent.student_id == 101).one()
        self.assertEqual(live.status, "picked")
        self.assertEqual(self.db.query(Trip).count(), before, "no trip/trip_student rows are created by the live PATCH")
        self.assertEqual(self.db.query(TripStudent).count(), 0)

    # --- Guard rails --------------------------------------------------------

    def test_write_endpoints_are_guarded_admin_or_pilot(self):
        source = ROUTER_SOURCE.read_text()
        tree = ast.parse(source)
        write_functions = {
            "create_trip": {"admin", "pilot"},
            "start_trip": {"admin", "pilot"},
            "complete_trip": {"admin", "pilot"},
            "cancel_trip": {"admin", "pilot"},
            "reopen_trip": {"admin"},
            "update_trip_student": {"admin", "pilot"},
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in write_functions:
                defaults = [ast.unparse(d) for d in node.args.defaults]
                roles = set()
                for d in defaults:
                    for call in ast.walk(ast.parse(d)):
                        if isinstance(call, ast.Call) and getattr(getattr(call, "func", None), "id", None) == "require_role":
                            roles = {ast.literal_eval(a) for a in call.args}
                self.assertFalse("parent" in roles, f"{node.name} must not allow parent")
                self.assertEqual(roles, write_functions[node.name], node.name)

    def test_invalid_direction_is_a_validation_error(self):
        from services.transport_service.schemas import TripCreate

        with self.assertRaises(ValidationError):
            TripCreate(route_id=1, trip_date=date(2026, 9, 1), direction="bogus")

    def test_route_created_by_pilot_snapshot_matches_own_identity(self):
        trip = self._create(route_id=2, by=self.pilot2)
        self.assertEqual(trip["pilot_id"], 2)
        self.assertEqual(trip["driver_name"], "Suresh")