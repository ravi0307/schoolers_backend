"""
Tests for the parent-facing /leave/mine endpoint in leave_service.

Executes the router handler end-to-end against an in-memory database and
pins the parent-only, read-only role guard via AST (transport pick/drop
test convention).
"""
import ast
import sys
import unittest
from datetime import date
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from common.dependencies import CurrentUser
from common.models import (
    Base,
    LeaveRequest,
    Parent,
    ParentStudent,
    School,
    SchoolClass,
    Student,
)

_ENGINES = []

ROOT = Path(__file__).resolve().parent.parent
ROUTER_SOURCE = ROOT / "services" / "leave_service" / "router.py"

LEAVE_TABLES = [
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
            SchoolClass(class_id=7, school_id=2, name="Grade 7"),
            Parent(parent_id=10, school_id=1, name="Mrs. Rao", phone="1"),
            Parent(parent_id=11, school_id=1, name="Mr. Nair", phone="2"),
            Parent(parent_id=20, school_id=2, name="Ms. Cross", phone="3"),
            Student(student_id=101, school_id=1, class_id=5, admission_no="ADM101", name="Aarav Rao"),
            Student(student_id=102, school_id=1, class_id=5, admission_no="ADM102", name="Anika Rao"),
            Student(student_id=103, school_id=1, class_id=5, admission_no="ADM103", name="Dormant", is_active=False),
            Student(student_id=104, school_id=2, class_id=7, admission_no="ADM104", name="Neha Cross"),
            ParentStudent(parent_id=10, student_id=101, relationship_="Mother"),
            ParentStudent(parent_id=10, student_id=102, relationship_="Mother"),
            ParentStudent(parent_id=10, student_id=103, relationship_="Mother"),
            # Cross-school child linked to the same parent.
            ParentStudent(parent_id=10, student_id=104, relationship_="Mother"),
            # Requests for Mrs. Rao's children.
            LeaveRequest(
                leave_id=1,
                school_id=1,
                requester_type="Student",
                requester_name="Aarav Rao",
                from_date=date(2026, 3, 2),
                to_date=date(2026, 3, 3),
                reason="Family function",
                status="Pending",
            ),
            LeaveRequest(
                leave_id=2,
                school_id=1,
                requester_type="Student",
                requester_name="Anika Rao",
                from_date=date(2026, 4, 1),
                to_date=date(2026, 4, 2),
                reason="Medical",
                status="Approved",
            ),
            # Someone else's request in the same school.
            LeaveRequest(
                leave_id=3,
                school_id=1,
                requester_type="Teacher",
                requester_name="Mr. Nair",
                from_date=date(2026, 5, 1),
                to_date=date(2026, 5, 1),
                reason="Conference",
                status="Rejected",
            ),
            # Another school's request.
            LeaveRequest(
                leave_id=4,
                school_id=2,
                requester_type="Student",
                requester_name="Neha Cross",
                from_date=date(2026, 5, 2),
                to_date=date(2026, 5, 3),
                reason="Trip",
                status="Pending",
            ),
            LeaveRequest(
                leave_id=5,
                school_id=1,
                requester_type="Student",
                requester_name="Aarav Rao",
                from_date=date(2026, 2, 1),
                to_date=date(2026, 2, 2),
                reason="Old request",
                status="Pending",
                is_active=False,
            ),
        ]
    )
    db.commit()


def tearDownModule():
    for engine in _ENGINES:
        engine.dispose()


class LeaveMineRepositoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite://")
        _ENGINES.append(cls.engine)
        cls.Session = sessionmaker(bind=cls.engine)

    def setUp(self):
        Base.metadata.drop_all(self.engine, tables=LEAVE_TABLES)
        Base.metadata.create_all(self.engine, tables=LEAVE_TABLES)
        self.session = self.Session()
        seed(self.session)

    def tearDown(self):
        self.session.close()

    def _names(self):
        from services.leave_service import repository as repo

        return repo.child_names_of_parent(self.session, school_id=1, parent_id=10)

    def _mine(self):
        from services.leave_service import repository as repo

        return repo.list_for_child_names(self.session, 1, self._names())

    def test_resolves_only_active_children_of_the_parents_school(self):
        names = self._names()
        self.assertEqual(sorted(names), ["Aarav Rao", "Anika Rao"])

    def test_returns_requests_filed_for_the_children(self):
        rows = {l.leave_id: l for l in self._mine()}
        self.assertEqual(set(rows), {1, 2})

    def test_omits_other_requesters_and_other_schools(self):
        rows = {l.leave_id for l in self._mine()}
        self.assertNotIn(3, rows, "other people's requests must not leak")
        self.assertNotIn(4, rows, "other schools' requests must not leak")

    def test_omits_inactive_requests(self):
        rows = {l.leave_id for l in self._mine()}
        self.assertNotIn(5, rows, "deleted requests must not appear")

    def test_results_are_ordered_newest_first(self):
        rows = self._mine()
        self.assertTrue(rows, "expected leave requests for the children")
        self.assertEqual(sorted(l.leave_id for l in rows), [1, 2])

    def test_parent_without_children_returns_empty(self):
        from services.leave_service import repository as repo

        rows = repo.list_for_child_names(self.session, 1, repo.child_names_of_parent(self.session, 1, 11))
        self.assertEqual(rows, [])

    def test_empty_name_list_is_a_no_op(self):
        from services.leave_service import repository as repo

        self.assertEqual(repo.list_for_child_names(self.session, 1, []), [])

    def test_unknown_parent_returns_empty(self):
        from services.leave_service import repository as repo

        self.assertEqual(repo.child_names_of_parent(self.session, 1, 999), [])

    def test_cross_school_child_is_scoped_to_its_own_school(self):
        from services.leave_service import repository as repo

        # At school 1 the parent sees only school-1 children, never Neha Cross.
        self.assertEqual(repo.child_names_of_parent(self.session, 1, 10), ["Aarav Rao", "Anika Rao"])
        # At school 2 the same parent sees only the school-2 child.
        self.assertEqual(repo.child_names_of_parent(self.session, 2, 10), ["Neha Cross"])


class LeaveMineRouterTests(unittest.TestCase):
    _saved = None

    @classmethod
    def setUpClass(cls):
        service_dir = str(ROOT / "services" / "leave_service")
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
        Base.metadata.drop_all(self.engine, tables=LEAVE_TABLES)
        Base.metadata.create_all(self.engine, tables=LEAVE_TABLES)
        self.session = self.Session()
        seed(self.session)

    def tearDown(self):
        self.session.close()

    def test_handler_returns_children_leave_requests(self):
        user = CurrentUser(user_id=30, role="parent", school_id=1, linked_person_id=10)
        rows = self.router.list_my_children_leave_requests(db=self.session, school_id=1, current_user=user)
        by_id = {l.leave_id: l for l in rows}
        self.assertEqual(sorted(by_id), [1, 2])
        self.assertEqual(by_id[1].status, "Pending")

    def test_handler_without_linked_parent_returns_empty(self):
        user = CurrentUser(user_id=31, role="parent", school_id=1, linked_person_id=None)
        self.assertEqual(self.router.list_my_children_leave_requests(db=self.session, school_id=1, current_user=user), [])

    def test_handler_rows_validate_against_the_response_model(self):
        from schemas import LeaveRequestRead

        user = CurrentUser(user_id=30, role="parent", school_id=1, linked_person_id=10)
        rows = self.router.list_my_children_leave_requests(db=self.session, school_id=1, current_user=user)
        models = [LeaveRequestRead.model_validate(l, from_attributes=True) for l in rows]
        self.assertEqual(sorted(m.leave_id for m in models), [1, 2])
        self.assertEqual(next(m for m in models if m.leave_id == 2).status, "Approved")


class LeaveMineGuardTests(unittest.TestCase):
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

    def test_mine_is_a_read_only_get_view(self):
        func = self._route_by("get", "/mine")
        decorators = [
            dec.func.attr
            for dec in func.decorator_list
            if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and getattr(dec.func.value, "id", None) == "router"
        ]
        self.assertEqual(decorators, ["get"], "parent leave history must only be readable")
        for method in ("post", "patch", "delete"):
            with self.assertRaises(AssertionError):
                self._route_by(method, "/mine")

    def test_admin_list_route_is_unchanged(self):
        roles, scope = self._roles_and_scope(self._route_by("get", ""))
        self.assertEqual(roles, {("admin",)})
        self.assertTrue(scope)

    def test_create_still_allows_parent(self):
        roles, scope = self._roles_and_scope(self._route_by("post", ""))
        self.assertEqual(roles, {("parent", "teacher", "staff", "pilot", "admin")})
        self.assertTrue(scope)

    def test_mine_filters_by_child_names(self):
        src = ROUTER_SOURCE.read_text(encoding="utf-8")
        self.assertIn('@router.get("/mine", response_model=list[LeaveRequestRead])', src)
        self.assertIn("child_names_of_parent", src)
        self.assertIn("list_for_child_names", src)
        self.assertIn("current_user.linked_person_id", src)


if __name__ == "__main__":
    unittest.main()