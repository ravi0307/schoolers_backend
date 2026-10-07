"""
Trip History data-model tests.

Covers the historical `trips` / `trip_students` foundation only - the read
APIs, reopen API and trip service are deliberately out of scope for this
phase. Runs against in-memory SQLite with foreign-key enforcement turned on
(the real deployment is Postgres; the constraints tested here are schema
guarantees, not server-specific).

Design decisions under test (see the migration block in db-migrations.sql):
  * direction is 'pickup' | 'drop' (existing transport vocabulary), not
    morning/evening.
  * vehicle + driver_name are immutable snapshots captured at creation;
    changing Route.vehicle or reassigning the route's pilot later must not
    rewrite the history row.
  * pilot_id references pilots.pilot_id (mapped from the JWT's staff_id via
    the existing Pilot.staff_id convention) so trips can be filtered by driver.
  * boarding_status uses the live model's word 'picked' (RouteStudent.status)
    plus 'did_not_board'; drop_status uses 'dropped' plus 'drop_not_recorded'.
  * boarding_stop_id / drop_stop_id stay nullable - RouteStudent has no stop
    assignment, so they are recorded only when explicitly chosen.
"""
import unittest
from datetime import date, datetime

from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from common.models import (
    Base,
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

TRIP_TABLES = [
    "users",
    "schools",
    "classes",
    "staff",
    "pilots",
    "routes",
    "route_stops",
    "route_students",
    "students",
    "trips",
    "trip_students",
]


def seed(db):
    """Insert rows in FK-dependency order so a fresh test database with
    PRAGMA foreign_keys=ON can accept them. SQLite/SQLAlchemy does not reorder
    ORM inserts by foreign key, so routes must exist before pilots can
    reference them."""
    db.add_all(
        [
            User(user_id=10, school_id=1, role="admin", username="admin1", password_hash="x"),
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
            Student(student_id=101, school_id=1, class_id=1, admission_no="ADM101", name="Aarav Rao"),
            Student(student_id=102, school_id=1, class_id=1, admission_no="ADM102", name="Anika Rao"),
            Student(student_id=201, school_id=2, class_id=2, admission_no="ADM201", name="Neha Cross"),
            Staff(staff_id=1, school_id=1, name="Ramesh", role="Driver", person_type="pilot"),
            Staff(staff_id=2, school_id=1, name="Suresh", role="Driver", person_type="pilot"),
        ]
    )
    db.commit()
    db.add_all(
        [
            Route(route_id=1, school_id=1, name="Route A", vehicle="KA-01-AB-1234"),
            Route(route_id=2, school_id=2, name="Route C", vehicle="KA-03-CC-1111"),
            RouteStop(stop_id=1, route_id=1, name="Koramangala", stop_time="07:45",
                      stop_type="pickup", stop_order=1),
        ]
    )
    db.commit()
    db.add_all(
        [
            Pilot(pilot_id=1, staff_id=1, route_id=1),
            Pilot(pilot_id=2, staff_id=2, route_id=None),
            RouteStudent(route_id=1, student_id=101, status="picked"),
        ]
    )
    db.commit()


def snapshot_for(db, route_id):
    """The values a trip captures at creation, mirroring what the future trip
    service will compute: Route.vehicle and the route's assigned driver."""
    route = db.query(Route).filter(Route.route_id == route_id).first()
    return {
        "school_id": route.school_id,
        "vehicle": route.vehicle,
        "driver_name": route.driver_name,
        "pilot_id": route.pilot.pilot_id if route.pilot is not None else None,
    }


def make_trip(db, route_id=1, trip_date=date(2026, 9, 1), direction="pickup", **overrides):
    snap = snapshot_for(db, route_id)
    trip = Trip(
        route_id=route_id,
        trip_date=trip_date,
        direction=direction,
        **snap,
        **overrides,
    )
    db.add(trip)
    db.commit()
    db.refresh(trip)
    return trip


def make_trip_student(db, trip, student_id, **overrides):
    row = TripStudent(trip_id=trip.trip_id, student_id=student_id, **overrides)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def tearDownModule():
    for engine in _ENGINES:
        engine.dispose()


class TripModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Same pattern as tests/test_transport_pickdrop.py: plain in-memory
        # sqlite keeps one connection per thread (SingletonThreadPool), so the
        # schema created here is reused by every test on this thread.
        cls.engine = create_engine("sqlite://")
        _ENGINES.append(cls.engine)

        @event.listens_for(cls.engine, "connect")
        def _fk_on(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        cls.Session = sessionmaker(bind=cls.engine, autoflush=False, future=True)
        Base.metadata.create_all(
            cls.engine, tables=[Base.metadata.tables[name] for name in TRIP_TABLES]
        )

    def setUp(self):
        # Reset rows between tests. PRAGMA foreign_keys is a no-op inside a
        # transaction, so the ON toggle must run only after the DELETE work is
        # committed; it stays OFF during the deletes so row order is irrelevant.
        self.db = self.Session()
        raw = self.engine.raw_connection()
        try:
            cur = raw.cursor()
            cur.execute("PRAGMA foreign_keys=OFF")
            for name in reversed(TRIP_TABLES):
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

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    # 1. Trip creation.
    def test_trip_creation_persists(self):
        trip = make_trip(self.db)
        self.assertIsNotNone(trip.trip_id)
        reloaded = self.db.query(Trip).filter(Trip.trip_id == trip.trip_id).first()
        self.assertIsNotNone(reloaded)
        self.assertEqual(reloaded.route_id, 1)

    # 2. Trip date.
    def test_trip_date_round_trips(self):
        day = date(2026, 9, 15)
        trip = make_trip(self.db, trip_date=day)
        reloaded = self.db.query(Trip).get(trip.trip_id)
        self.assertEqual(reloaded.trip_date, day)

    def test_trip_requires_a_date(self):
        with self.assertRaises(IntegrityError):
            trip = Trip(route_id=1, school_id=1, vehicle="X", trip_date=None)
            self.db.add(trip)
            self.db.commit()
        self.db.rollback()

    # 3. Trip direction.
    def test_direction_defaults_to_pickup(self):
        trip = make_trip(self.db)
        self.assertEqual(trip.direction, "pickup")

    def test_direction_accepts_drop(self):
        trip = make_trip(self.db, direction="drop")
        self.assertEqual(trip.direction, "drop")

    # 4. Route relationship.
    def test_trip_relates_to_route(self):
        trip = make_trip(self.db)
        route = self.db.query(Route).filter(Route.route_id == trip.route_id).first()
        self.assertEqual(route.name, "Route A")
        self.assertEqual(trip.school_id, route.school_id)

    # 5. Pilot relationship / staff -> pilot mapping.
    def test_pilot_mapping_snapshots_pilot_id_and_driver_name(self):
        trip = make_trip(self.db)
        # JWT carries linked_person_id = staff_id; the trip stores the pilot_id
        # resolved through the existing Pilot.staff_id linkage.
        pilot = self.db.query(Pilot).filter(Pilot.route_id == trip.route_id).first()
        self.assertEqual(pilot.staff_id, 1)
        self.assertEqual(trip.pilot_id, pilot.pilot_id)
        self.assertEqual(trip.driver_name, "Ramesh")

    def test_route_without_driver_keeps_pilot_snapshot_null(self):
        trip = make_trip(self.db, route_id=2)
        self.assertIsNone(trip.pilot_id)
        self.assertIsNone(trip.driver_name)

    # 6. Vehicle snapshot behavior.
    def test_vehicle_snapshot_survives_route_reassignment(self):
        trip = make_trip(self.db)
        route = self.db.query(Route).get(1)
        route.vehicle = "KA-99-NEW-BUS"
        pilot1 = self.db.query(Pilot).get(1)
        pilot1.route_id = None
        pilot2 = self.db.query(Pilot).get(2)
        pilot2.route_id = 1
        self.db.commit()

        reloaded = self.db.query(Trip).get(trip.trip_id)
        self.assertEqual(reloaded.vehicle, "KA-01-AB-1234")
        self.assertEqual(reloaded.driver_name, "Ramesh")
        self.assertEqual(reloaded.pilot_id, 1)

    # 7. Trip status lifecycle.
    def test_status_lifecycle_scheduled_to_in_progress_to_completed(self):
        trip = make_trip(self.db)
        self.assertEqual(trip.status, "scheduled")
        trip.status = "in_progress"
        trip.started_at = datetime(2026, 9, 1, 7, 30)
        self.db.commit()
        self.assertEqual(trip.status, "in_progress")
        trip.status = "completed"
        trip.ended_at = datetime(2026, 9, 1, 8, 30)
        self.db.commit()
        reloaded = self.db.query(Trip).get(trip.trip_id)
        self.assertEqual(reloaded.status, "completed")
        self.assertIsNotNone(reloaded.started_at)
        self.assertIsNotNone(reloaded.ended_at)

    # 8. TripStudent relationship.
    def test_trip_students_relationship_and_backref(self):
        trip = make_trip(self.db)
        make_trip_student(self.db, trip, 101)
        make_trip_student(self.db, trip, 102)
        reloaded = self.db.query(Trip).get(trip.trip_id)
        self.assertEqual({s.student_id for s in reloaded.students}, {101, 102})
        self.assertTrue(all(s.trip_id == trip.trip_id for s in reloaded.students))

    # 9. Boarding outcome.
    def test_boarding_outcome_picked_records_time(self):
        trip = make_trip(self.db)
        row = make_trip_student(
            self.db, trip, 101,
            boarding_status="picked",
            boarding_at=datetime(2026, 9, 1, 7, 45),
            boarding_stop_id=1,
        )
        self.assertEqual(row.boarding_status, "picked")
        self.assertIsNotNone(row.boarding_at)
        self.assertEqual(row.boarding_stop_id, 1)

    def test_boarding_outcome_did_not_board(self):
        trip = make_trip(self.db)
        row = make_trip_student(self.db, trip, 101, boarding_status="did_not_board")
        self.assertEqual(row.boarding_status, "did_not_board")
        self.assertIsNone(row.boarding_at)

    # 10. Drop outcome.
    def test_drop_outcome_records_status_and_time(self):
        trip = make_trip(self.db)
        row = make_trip_student(
            self.db, trip, 101,
            drop_status="dropped",
            drop_at=datetime(2026, 9, 1, 8, 25),
        )
        self.assertEqual(row.drop_status, "dropped")
        self.assertIsNotNone(row.drop_at)

    def test_drop_not_recorded_keeps_time_null(self):
        trip = make_trip(self.db)
        row = make_trip_student(
            self.db, trip, 101,
            boarding_status="picked",
            drop_status="drop_not_recorded",
        )
        self.assertEqual(row.drop_status, "drop_not_recorded")
        self.assertIsNone(row.drop_at)

    # 11. Historical TripStudent must not move when RouteStudent changes.
    def test_trip_student_unchanged_when_route_student_status_changes(self):
        from services.transport_service import repository as transport_repo

        trip = make_trip(self.db)
        make_trip_student(
            self.db, trip, 101,
            boarding_status="picked",
            drop_status="dropped",
        )
        transport_repo.update_student_status(self.db, 1, 101, "pending")

        row = self.db.query(TripStudent).filter(
            TripStudent.trip_id == trip.trip_id, TripStudent.student_id == 101
        ).first()
        self.assertEqual(row.boarding_status, "picked")
        self.assertEqual(row.drop_status, "dropped")
        current = self.db.query(RouteStudent).filter(
            RouteStudent.student_id == 101
        ).first()
        self.assertEqual(current.status, "pending")

    # 12. Multiple dates produce separate history.
    def test_multiple_dates_produce_separate_history(self):
        day1 = make_trip(self.db, trip_date=date(2026, 9, 1))
        day2 = make_trip(self.db, trip_date=date(2026, 9, 2))
        make_trip_student(self.db, day1, 101, boarding_status="picked")
        make_trip_student(self.db, day2, 101, boarding_status="did_not_board")

        rows = self.db.query(TripStudent).filter(TripStudent.student_id == 101).all()
        self.assertEqual(len(rows), 2)
        by_trip = {r.trip_id: r.boarding_status for r in rows}
        self.assertEqual(by_trip[day1.trip_id], "picked")
        self.assertEqual(by_trip[day2.trip_id], "did_not_board")

    # 13. Pickup and drop runs are distinguishable on the same day.
    def test_pickup_and_drop_runs_coexist_same_date(self):
        pickup = make_trip(self.db, direction="pickup")
        drop = make_trip(self.db, direction="drop")
        self.assertNotEqual(pickup.trip_id, drop.trip_id)
        trips = self.db.query(Trip).filter(
            Trip.route_id == 1, Trip.trip_date == date(2026, 9, 1)
        ).all()
        self.assertEqual({t.direction for t in trips}, {"pickup", "drop"})

    # 14. Cancellation fields.
    def test_cancellation_fields_recorded(self):
        trip = make_trip(self.db)
        trip.status = "cancelled"
        trip.cancelled_at = datetime(2026, 9, 1, 7, 10)
        trip.cancelled_by = 10
        trip.cancellation_reason = "Battery died"
        self.db.commit()

        reloaded = self.db.query(Trip).get(trip.trip_id)
        self.assertEqual(reloaded.status, "cancelled")
        self.assertIsNotNone(reloaded.cancelled_at)
        self.assertEqual(reloaded.cancelled_by, 10)
        self.assertEqual(reloaded.cancellation_reason, "Battery died")

    # 15. Reopen fields.
    def test_reopen_fields_recorded_after_cancel(self):
        trip = make_trip(self.db)
        trip.status = "in_progress"
        trip.cancelled_at = datetime(2026, 9, 1, 7, 10)
        trip.cancelled_by = 10
        trip.cancellation_reason = "Traffic"
        self.db.commit()

        trip.status = "in_progress"
        trip.reopened_at = datetime(2026, 9, 1, 7, 40)
        trip.reopened_by = 10
        trip.reopen_reason = "Vehicle fixed"
        self.db.commit()

        reloaded = self.db.query(Trip).get(trip.trip_id)
        self.assertEqual(reloaded.status, "in_progress")
        self.assertIsNotNone(reloaded.reopened_at)
        self.assertEqual(reloaded.reopened_by, 10)
        self.assertEqual(reloaded.reopen_reason, "Vehicle fixed")

    # 16. School scoping.
    def test_trips_are_scoped_to_a_school(self):
        own = make_trip(self.db, route_id=1)
        other = make_trip(self.db, route_id=2)
        self.assertEqual(own.school_id, 1)
        self.assertEqual(other.school_id, 2)
        mine = self.db.query(Trip).filter(Trip.school_id == 1).all()
        self.assertEqual([t.trip_id for t in mine], [own.trip_id])

    def test_trip_student_scopes_through_its_trip_and_student(self):
        trip = make_trip(self.db)
        row = make_trip_student(self.db, trip, 101)
        student = self.db.query(Student).get(row.student_id)
        self.assertEqual(student.school_id, 1)  # same school as the trip

    # 17. Constraints.
    def test_check_rejects_unknown_direction(self):
        with self.assertRaises(IntegrityError):
            self.db.add(Trip(route_id=1, school_id=1, trip_date=date(2026, 9, 1),
                             direction="morning", vehicle="X"))
            self.db.commit()
        self.db.rollback()

    def test_check_rejects_unknown_trip_status(self):
        with self.assertRaises(IntegrityError):
            trip = Trip(route_id=1, school_id=1, trip_date=date(2026, 9, 1),
                        vehicle="X", status="open")
            self.db.add(trip)
            self.db.commit()
        self.db.rollback()

    def test_check_rejects_legacy_boarding_value(self):
        trip = make_trip(self.db)
        with self.assertRaises(IntegrityError):
            self.db.add(TripStudent(trip_id=trip.trip_id, student_id=101,
                                    boarding_status="boarded"))
            self.db.commit()
        self.db.rollback()

    def test_check_rejects_unknown_drop_status(self):
        trip = make_trip(self.db)
        with self.assertRaises(IntegrityError):
            self.db.add(TripStudent(trip_id=trip.trip_id, student_id=101,
                                    drop_status="absent"))
            self.db.commit()
        self.db.rollback()

    def test_foreign_key_rejects_unknown_route(self):
        with self.assertRaises(IntegrityError):
            self.db.add(Trip(route_id=999, school_id=1, trip_date=date(2026, 9, 1),
                             vehicle="X"))
            self.db.commit()
        self.db.rollback()

    # 18. Uniqueness behavior.
    def test_unique_route_date_direction_blocks_duplicate_pickup(self):
        make_trip(self.db, direction="pickup")
        with self.assertRaises(IntegrityError):
            make_trip(self.db, direction="pickup")
        self.db.rollback()
        count = self.db.query(Trip).filter(
            Trip.route_id == 1, Trip.trip_date == date(2026, 9, 1)
        ).count()
        self.assertEqual(count, 1)

    def test_unique_route_date_direction_allows_different_direction(self):
        make_trip(self.db, direction="pickup")
        drop = make_trip(self.db, direction="drop")
        self.assertIsNotNone(drop.trip_id)

    def test_unique_trip_student_blocks_duplicate_participation(self):
        trip = make_trip(self.db)
        make_trip_student(self.db, trip, 101)
        with self.assertRaises(IntegrityError):
            make_trip_student(self.db, trip, 101)
        self.db.rollback()

    # 19/20. Enums used by the model (imports must stay in sync with CHECKs).
    def test_status_enums_match_the_checked_values(self):
        from common.enums import BoardingStatus, DropStatus, TripDirection, TripStatus

        self.assertEqual({e.value for e in TripDirection}, {"pickup", "drop"})
        self.assertEqual({e.value for e in TripStatus},
                         {"scheduled", "in_progress", "completed", "cancelled"})
        self.assertEqual({e.value for e in BoardingStatus},
                         {"pending", "picked", "did_not_board"})
        self.assertEqual({e.value for e in DropStatus},
                         {"pending", "dropped", "drop_not_recorded"})


if __name__ == "__main__":
    unittest.main()