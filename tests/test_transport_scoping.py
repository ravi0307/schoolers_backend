"""Transport data must never cross a school boundary.

route_stops and route_students carry no school_id of their own — they are
scoped only by route_id. Routes, vehicles and pilots were already filtered
by school, but the stop and roster handlers took a bare route_id and never
checked who owned it, so any caller could read or write another school's
stops and students by walking route_id upwards.

These tests pin the guarantee at both layers: the repository lookups refuse a
foreign school, and every route-keyed handler refuses a foreign route_id.
"""
import sys
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from common.database import Base
from common.dependencies import CurrentUser
from common.models import Route, RouteStop, RouteStudent, School, SchoolClass, Student

ROOT = Path(__file__).resolve().parents[1]

# Every microservice uses bare `import repository` / `from schemas import ...`,
# so importing the transport router means putting its directory on sys.path.
# That makes the bare names `router`, `repository` and `schemas` resolve to the
# transport modules and collide with any other service's identically named
# modules in the same pytest process -- which breaks unrelated suites (the
# attendance router does `from schemas import AttendanceMarkBulk`).
#
# So: pop the bare names, import, then put the previous ones back. Same
# approach as test_audit_trail.py.
_BARE = ("router", "repository", "schemas")

SCHOOL_A = 1
SCHOOL_B = 2


class TransportSchoolScopingTests(unittest.TestCase):
    """Route, stop and roster lookups are refused across schools."""

    @classmethod
    def setUpClass(cls):
        cls._saved = {name: sys.modules.pop(name, None) for name in _BARE}
        sys.path.insert(0, str(ROOT / "services" / "transport_service"))
        import repository as repo  # noqa: PLC0415  (deliberately late)
        import router as transport_router  # noqa: PLC0415

        cls.repo = repo
        cls.transport_router = transport_router

        cls.engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
        Base.metadata.create_all(
            cls.engine,
            tables=[
                Base.metadata.tables[name]
                for name in ("schools", "classes", "students", "routes", "route_stops", "route_students")
            ],
        )
        cls.Session = sessionmaker(bind=cls.engine, autoflush=False, future=True)
        db = cls.Session()
        db.add_all(
            [
                School(school_id=SCHOOL_A, name="Green Valley", address="a", pincode="1",
                       city="c", state="s", primary_contact="p", primary_email="e"),
                School(school_id=SCHOOL_B, name="Blue Horizon", address="a", pincode="1",
                       city="c", state="s", primary_contact="p", primary_email="e"),
                # Same first name in both schools on purpose: a name-only assertion
                # would pass even if the wrong school's row came back.
                SchoolClass(class_id=5, school_id=SCHOOL_A, name="Grade 5"),
                SchoolClass(class_id=6, school_id=SCHOOL_B, name="Grade 6"),
                Student(student_id=1, school_id=SCHOOL_A, class_id=5, name="Aarav Sharma",
                        admission_no="ADM1001", is_active=True),
                Student(student_id=6, school_id=SCHOOL_B, class_id=6, name="Aarav Sharma",
                        admission_no="ADM2001", is_active=True),
                # School B student on NO route at all. add_student has a second
                # guard ("already assigned to another route"), so a routed
                # student would be rejected for the wrong reason and the test
                # would pass without the school filter doing any work.
                Student(student_id=7, school_id=SCHOOL_B, class_id=6, name="Unrouted Child",
                        admission_no="ADM2002", is_active=True),
                Route(route_id=1, school_id=SCHOOL_A, name="Route 1", vehicle="V1", is_active=True),
                Route(route_id=3, school_id=SCHOOL_B, name="Route 3", vehicle="V3", is_active=True),
                RouteStop(stop_id=9, route_id=1, name="Koramangala", stop_time="07:45",
                          stop_type="pickup", stop_order=1),
                RouteStop(stop_id=19, route_id=3, name="Whitefield", stop_time="06:30",
                          stop_type="pickup", stop_order=1),
                RouteStudent(id=1, route_id=1, student_id=1, status="pending"),
                RouteStudent(id=4, route_id=3, student_id=6, status="pending"),
            ]
        )
        db.commit()
        db.close()

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()
        sys.path.remove(str(ROOT / "services" / "transport_service"))
        for name in _BARE:
            sys.modules.pop(name, None)
        for name, mod in cls._saved.items():
            if mod is not None:
                sys.modules[name] = mod

    def setUp(self):
        self.db = self.Session()
        self.addRoute = CurrentUser(user_id=1, role="admin", school_id=SCHOOL_A)
        self.otherSchool = CurrentUser(user_id=2, role="admin", school_id=SCHOOL_B)

    def tearDown(self):
        self.db.close()

    # -- route ---------------------------------------------------------------

    def test_get_route_returns_own_route(self):
        self.assertIsNotNone(self.repo.get_route(self.db, SCHOOL_A, 1))

    def test_get_route_refuses_another_schools_route(self):
        self.assertIsNone(self.repo.get_route(self.db, SCHOOL_A, 3))

    # -- stops ---------------------------------------------------------------

    def test_get_stop_returns_own_stop(self):
        self.assertIsNotNone(self.repo.get_stop(self.db, SCHOOL_A, 9))

    def test_get_stop_refuses_another_schools_stop(self):
        # The regression: this used to take only stop_id.
        self.assertIsNone(self.repo.get_stop(self.db, SCHOOL_A, 19))

    def test_remove_stop_leaves_another_schools_stop_untouched(self):
        self.repo.remove_stop(self.db, SCHOOL_A, 19)
        still_there = (
            self.db.query(RouteStop).filter(RouteStop.stop_id == 19).first()
        )
        self.assertIsNotNone(still_there, "school A deleted school B's stop")

    # -- roster --------------------------------------------------------------

    def test_add_student_refuses_another_schools_student(self):
        # The regression: add_student filtered on student_id and is_active only,
        # so school A could attach school B's student to its own route.
        # Student 7 is deliberately on no route, so the "already assigned"
        # guard cannot mask a missing school check.
        from common.exceptions import ConflictError

        with self.assertRaises(ConflictError) as ctx:
            self.repo.add_student(self.db, SCHOOL_A, 1, 7)
        self.assertIn("not found", ctx.exception.message.lower())
        self.assertEqual(
            self.db.query(RouteStudent).filter(RouteStudent.student_id == 7).count(),
            0,
            "a cross-school student was written to the roster",
        )

    def test_add_student_accepts_own_student(self):
        created = self.repo.add_student(self.db, SCHOOL_A, 1, 1)
        self.assertEqual(created["admission_no"], "ADM1001")

    # -- handler layer -------------------------------------------------------

    def test_owned_route_raises_for_another_schools_route(self):
        from common.exceptions import NotFoundError

        with self.assertRaises(NotFoundError):
            self.transport_router._owned_route(self.db, SCHOOL_A, 3)

    def test_owned_route_returns_own_route(self):
        self.assertEqual(self.transport_router._owned_route(self.db, SCHOOL_A, 1).route_id, 1)

    def test_every_route_keyed_handler_checks_school_scope(self):
        """The eight handlers that used to trust route_id must all inject
        require_school_scope and resolve the route through _owned_route."""
        import inspect

        handlers = [
            "add_stop", "list_stops", "update_stop", "remove_stop",
            "add_student_to_route", "remove_student_from_route",
            "list_route_students", "update_pickup_drop_status",
        ]
        for name in handlers:
            with self.subTest(handler=name):
                fn = getattr(self.transport_router, name)
                src = inspect.getsource(fn)
                self.assertIn(
                    "require_school_scope", src,
                    f"{name} does not inject require_school_scope",
                )
                # Stops are keyed by stop_id, so they must use the scoped
                # lookup rather than _owned_route.
                if name in ("update_stop", "remove_stop"):
                    self.assertIn("repo.get_stop(db, school_id", src)
                else:
                    self.assertIn("_owned_route", src)

    def test_read_handlers_do_not_leak_another_schools_roster(self):
        """list_route_students for a foreign route must raise, not return rows."""
        from common.exceptions import NotFoundError

        with self.assertRaises(NotFoundError):
            self.transport_router.list_route_students(
                3, self.db, SCHOOL_A, self.addRoute
            )


if __name__ == "__main__":
    unittest.main()
