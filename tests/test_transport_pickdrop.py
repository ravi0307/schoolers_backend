"""
Tests for the parent-facing pick/drop status endpoint in transport_service.

Runs against an in-memory SQLite database, executes the router handler
end-to-end (same bare-import convention as the other service tests) and
pins the parent-only role guard via AST.
"""
import ast
import sys
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from common.dependencies import CurrentUser
from common.models import (
    Base,
    Parent,
    ParentStudent,
    Route,
    RouteStudent,
    School,
    SchoolClass,
    Student,
)

_ENGINES = []

ROOT = Path(__file__).resolve().parent.parent
ROUTER_SOURCE = ROOT / "services" / "transport_service" / "router.py"

TRANSPORT_TABLES = [
    t
    for t in Base.metadata.sorted_tables
    if not any(col.type.__class__.__name__ == "JSONB" for col in t.columns)
]


def seed(db):
    db.add_all(
        [
            School(
                school_id=1,
                name="School One",
                address="1 Main Rd",
                pincode="560001",
                city="Bengaluru",
                state="KA",
                country="India",
                primary_contact="+9199",
                primary_email="one@example.com",
            ),
            School(
                school_id=2,
                name="School Two",
                address="2 Main Rd",
                pincode="560001",
                city="Bengaluru",
                state="KA",
                country="India",
                primary_contact="+9198",
                primary_email="two@example.com",
            ),
            SchoolClass(class_id=5, school_id=1, name="Grade 5"),
            SchoolClass(class_id=6, school_id=2, name="Grade 6"),
            Parent(parent_id=10, school_id=1, name="Mrs. Rao", phone="1"),
            Parent(parent_id=11, school_id=1, name="Mr. Nair", phone="2"),
            Parent(parent_id=20, school_id=2, name="Ms. Cross", phone="3"),
            Student(student_id=101, school_id=1, class_id=5, admission_no="ADM101", name="Aarav Rao"),
            Student(student_id=102, school_id=1, class_id=5, admission_no="ADM102", name="Anika Rao"),
            Student(student_id=103, school_id=1, class_id=5, admission_no="ADM103", name="Rohit Rao", is_active=True),
            Student(student_id=104, school_id=1, class_id=5, admission_no="ADM104", name="Dormant", is_active=False),
            Student(student_id=105, school_id=2, class_id=6, admission_no="ADM105", name="Neha Cross"),
            ParentStudent(parent_id=10, student_id=101, relationship_="Mother"),
            ParentStudent(parent_id=10, student_id=102, relationship_="Mother"),
            ParentStudent(parent_id=10, student_id=103, relationship_="Mother"),
            ParentStudent(parent_id=10, student_id=104, relationship_="Mother"),
            # Cross-school child linked to the same parent.
            ParentStudent(parent_id=10, student_id=105, relationship_="Mother"),
            Route(route_id=1, school_id=1, name="Route A", vehicle="KA-01-AB-1234", driver_name="Ramesh", status="Scheduled"),
            Route(route_id=2, school_id=1, name="Route B", vehicle="KA-02-BB-9999", driver_name="Suresh", status="Cancelled", is_active=False),
            Route(route_id=3, school_id=2, name="Route C", vehicle="KA-03-CC-1111", driver_name="Dinesh", status="Scheduled"),
            RouteStudent(route_id=1, student_id=101, status="picked"),
            # Rohit is assigned only to the inactive Route B.
            RouteStudent(route_id=2, student_id=103, status="pending"),
            RouteStudent(route_id=3, student_id=105, status="dropped"),
        ]
    )
    db.commit()


def tearDownModule():
    for engine in _ENGINES:
        engine.dispose()


class ParentPickdropRepositoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite://")
        _ENGINES.append(cls.engine)
        cls.Session = sessionmaker(bind=cls.engine)

    def setUp(self):
        Base.metadata.drop_all(self.engine, tables=TRANSPORT_TABLES)
        Base.metadata.create_all(self.engine, tables=TRANSPORT_TABLES)
        self.session = self.Session()
        seed(self.session)

    def tearDown(self):
        self.session.close()

    def _status(self):
        from services.transport_service import repository as repo

        return repo.list_children_pickdrop(self.session, school_id=1, parent_id=10)

    def test_active_child_on_route_gets_route_and_status(self):
        result = {row["student_id"]: row for row in self._status()}
        aarav = result[101]
        self.assertEqual(aarav["route_id"], 1)
        self.assertEqual(aarav["route_name"], "Route A")
        self.assertEqual(aarav["vehicle"], "KA-01-AB-1234")
        self.assertEqual(aarav["driver_name"], "Ramesh")
        self.assertEqual(aarav["status"], "picked")

    def test_unassigned_child_is_reported_not_assigned(self):
        result = {row["student_id"]: row for row in self._status()}
        anika = result[102]
        self.assertIsNone(anika["route_id"])
        self.assertIsNone(anika["route_name"])
        self.assertEqual(anika["status"], "not_assigned")

    def test_inactive_route_does_not_count_as_assignment(self):
        result = {row["student_id"]: row for row in self._status()}
        rohit = result[103]
        self.assertEqual(rohit["status"], "not_assigned")
        self.assertIsNone(rohit["route_id"])

    def test_inactive_student_is_omitted(self):
        result = {row["student_id"] for row in self._status()}
        self.assertNotIn(104, result, "inactive children must not be listed")

    def test_cross_school_child_is_omitted(self):
        result = {row["student_id"] for row in self._status()}
        self.assertNotIn(105, result, "children of another school must not leak")

    def test_parent_with_no_children_returns_empty(self):
        from services.transport_service import repository as repo

        self.assertEqual(repo.list_children_pickdrop(self.session, 1, 11), [])

    def test_unknown_parent_returns_empty(self):
        from services.transport_service import repository as repo

        self.assertEqual(repo.list_children_pickdrop(self.session, 1, 999), [])

    def test_other_school_has_no_matching_children(self):
        from services.transport_service import repository as repo

        self.assertEqual(repo.list_children_pickdrop(self.session, 2, 20), [])


class ParentPickdropRouterTests(unittest.TestCase):
    """Execute the /routes/mine handler directly (media-test convention)."""

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
        cls.Session = sessionmaker(bind=cls.engine)

    @classmethod
    def tearDownClass(cls):
        for name, mod in cls._saved.items():
            if mod is not None:
                sys.modules[name] = mod
        sys.path.pop(0)

    def setUp(self):
        Base.metadata.drop_all(self.engine, tables=TRANSPORT_TABLES)
        Base.metadata.create_all(self.engine, tables=TRANSPORT_TABLES)
        self.session = self.Session()
        seed(self.session)

    def tearDown(self):
        self.session.close()

    def test_handler_returns_parent_children_status(self):
        user = CurrentUser(user_id=30, role="parent", school_id=1, linked_person_id=10)
        rows = self.router.my_pickdrop_status(db=self.session, school_id=1, current_user=user)
        self.assertEqual(sorted(r["student_id"] for r in rows), [101, 102, 103])
        picked = next(r for r in rows if r["student_id"] == 101)
        self.assertEqual(picked["route_name"], "Route A")
        self.assertEqual(picked["status"], "picked")

    def test_handler_without_linked_parent_returns_empty(self):
        user = CurrentUser(user_id=31, role="parent", school_id=1, linked_person_id=None)
        self.assertEqual(self.router.my_pickdrop_status(db=self.session, school_id=1, current_user=user), [])


class ParentPickdropGuardTests(unittest.TestCase):
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

    def _roles_and_scope(self, func):
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

    def test_mine_endpoint_is_parent_only_with_school_scope(self):
        roles, scope = self._roles_and_scope(self._route_by("get", "/mine"))
        self.assertEqual(roles, {("parent",)})
        self.assertTrue(scope)

    def test_mine_returns_parent_snapshot_schema(self):
        src = ROUTER_SOURCE.read_text(encoding="utf-8")
        self.assertIn('@router.get("/mine", response_model=list[ParentPickDropRead])', src)
        self.assertIn("current_user.linked_person_id", src)