"""Sub-Master role: hierarchy admission, privileged-account lockdown, parity.

Five invariants, one per section below:

1. Admission — Sub-Master is admitted everywhere require_role allows Master
   (every distinct master-guarded role combination the routers ship), and
   denied everywhere Master is not allowed, with the exact legacy 403 body.
2. Lockdown — every /auth/privileged-accounts operation is Master-only;
   Master rows have no mutation path at all; payloads cannot forge a role;
   denials are one generic detail string.
3. Lifecycle — create (password shown once), login, deactivate blocks login,
   reactivate, rotate credential; unknown ids 404; duplicates 409.
4. Operational parity — support inbox scoping and replies (author_role
   'submaster' passes the CHECK), school feature guard, marks explicit-school
   scope, notifications admission.
5. Unchanged for everyone else — admin/teacher/parent/pilot denials keep the
   exact codes and detail bodies they had before the role existed.
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from common.authz import (
    MASTER_ROLE,
    PRIVILEGED_ROLES,
    SUBMASTER_ROLE,
    assert_can_manage_privileged,
    assert_master_only,
    is_master_like,
    role_allowed,
)
from common.database import Base, get_db
from common.dependencies import (
    CurrentUser,
    require_master_only,
    require_role,
    require_school_scope,
)
from common.exception_handlers import register_exception_handlers
from common.exceptions import ForbiddenError
from common.models import CheckConstraint, School, Staff, User
from common.permissions import PERMISSIONS, check_permission
from common.enums import Role
from common.security import create_access_token, hash_password

# Every distinct require_role(...) combination the routers ship that admits
# Master. If a router grows a new master-guarded combination it must be added
# here: this list is what proves Sub-Master inherits the whole master surface.
MASTER_GUARDS = [
    ("master",),
    ("master", "admin"),
    ("admin", "master"),
    ("teacher", "admin", "master"),
    ("parent", "teacher", "admin", "master"),
]

# Combinations that must keep denying Sub-Master: Master is not among them.
NON_MASTER_GUARDS = [
    ("admin",),
    ("parent", "teacher", "admin"),
    ("teacher", "admin"),
    ("parent",),
    ("admin", "pilot"),
    ("pilot",),
    ("teacher", "admin", "staff"),
    ("admin", "teacher", "staff", "pilot"),  # reports self-summary (SELF_ROLES)
]

SUBMASTER_PW = "submaster-pass1"
# One bcrypt cost for the whole module; every setUp re-seeds from this hash.
SUBMASTER_PW_HASH = hash_password(SUBMASTER_PW)
LEGACY_DENIAL = "Role 'submaster' cannot access this endpoint"
GENERIC_PRIVILEGED_DENIAL = "Not permitted to manage privileged accounts"


def _load(service_dir: str, package: str, bare: tuple[str, ...] = ()):
    """Import a service router whose module does bare imports
    (`import repository`, `from schemas import ...`) by putting its directory
    on sys.path, then remove the shared bare names so the next service does
    not inherit this one's modules (the router keeps its own references)."""
    sys.path.insert(0, service_dir)
    try:
        for mod in ("repository", "schemas", "bulk", "service"):
            sys.modules.pop(mod, None)
        return __import__(f"{package}.router", fromlist=["x"])
    finally:
        sys.path.pop(0)
        for mod in ("repository", "schemas", "bulk", "service", *bare):
            sys.modules.pop(mod, None)


def _seed(db):
    db.add_all(
        [
            School(school_id=1, name="Green Valley", address="1 Road", pincode="560001",
                   city="Bengaluru", state="KA", primary_contact="9000000001",
                   primary_email="gv@example.com"),
            School(school_id=2, name="Blue Ridge", address="2 Road", pincode="560002",
                   city="Bengaluru", state="KA", primary_contact="9000000002",
                   primary_email="br@example.com"),
            Staff(staff_id=11, school_id=1, name="Asha Rao", role="Admin"),
            Staff(staff_id=12, school_id=2, name="Bilal Khan", role="Admin"),
            User(user_id=1, school_id=None, role="master", username="master",
                 password_hash="x"),
            User(user_id=2, school_id=None, role="submaster", username="sub.admin",
                 email="sub.admin@example.com", password_hash=SUBMASTER_PW_HASH,
                 is_active=True),
            User(user_id=101, school_id=1, role="admin", username="admin1",
                 password_hash="x", linked_person_id=11),
            User(user_id=102, school_id=2, role="admin", username="admin2",
                 password_hash="x", linked_person_id=12),
            User(user_id=201, school_id=1, role="teacher", username="teach1",
                 password_hash="x"),
            User(user_id=301, school_id=1, role="parent", username="paren1",
                 password_hash="x"),
        ]
    )
    db.commit()


class SubMasterAuthorizationTests(unittest.TestCase):
    """End-to-end HTTP behaviour over real JWTs, plus the admission matrix."""

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        # Everything except website_builder_sites: that table's draft column
        # is JSONB (Postgres-only) and no endpoint here touches it.
        Base.metadata.create_all(
            cls.engine,
            tables=[table for name, table in Base.metadata.tables.items()
                    if name != "website_builder_sites"],
        )
        cls.Session = sessionmaker(bind=cls.engine, autoflush=False, future=True)

        cls.auth_router = _load("services/auth_service", "services.auth_service")
        cls.support_router = _load("services/support_service", "services.support_service")
        cls.schools_router = _load("services/schools_service", "services.schools_service")
        cls.notifications_router = _load(
            "services/notifications_service", "services.notifications_service")
        cls.marks_router = _load("services/marks_service", "services.marks_service")

        cls.app = FastAPI()
        for mod in (cls.auth_router, cls.support_router, cls.schools_router,
                    cls.notifications_router):
            cls.app.include_router(mod.router)
        register_exception_handlers(cls.app)

        session_holder = {}

        def _db():
            session = cls.Session()
            session_holder["s"] = session
            try:
                yield session
            finally:
                session.close()

        cls.app.dependency_overrides[get_db] = _db
        cls.client = TestClient(cls.app)

    def setUp(self):
        db = self.Session()
        self.db = db
        for table in reversed(Base.metadata.sorted_tables):
            if table.name == "website_builder_sites":
                continue  # not created (JSONB), matches create_all above
            db.execute(table.delete())
        db.commit()
        _seed(db)
        self.master_headers = _headers(1, MASTER_ROLE, None)
        self.sub_headers = _headers(2, SUBMASTER_ROLE, None)
        self.admin1_headers = _headers(101, "admin", 1)
        self.teacher_headers = _headers(201, "teacher", 1)
        self.parent_headers = _headers(301, "parent", 1)

    def tearDown(self):
        self.db.close()

    # -- 1. admission matrix ---------------------------------------------

    def test_require_role_admits_submaster_for_every_master_guard(self):
        sub = CurrentUser(user_id=2, role=SUBMASTER_ROLE, school_id=None)
        for combo in MASTER_GUARDS:
            with self.subTest(guard=combo):
                self.assertIs(require_role(*combo)(current_user=sub), sub)

    def test_require_role_denies_submaster_when_master_is_not_allowed(self):
        sub = CurrentUser(user_id=2, role=SUBMASTER_ROLE, school_id=None)
        for combo in NON_MASTER_GUARDS:
            with self.subTest(guard=combo):
                with self.assertRaises(ForbiddenError) as ctx:
                    require_role(*combo)(current_user=sub)
                self.assertEqual(str(ctx.exception), LEGACY_DENIAL)

    def test_require_master_only_rejects_every_role_but_master(self):
        allowed = CurrentUser(user_id=1, role=MASTER_ROLE, school_id=None)
        self.assertIs(require_master_only(current_user=allowed), allowed)
        for role in ("submaster", "admin", "teacher", "parent", "pilot", "staff"):
            with self.subTest(role=role):
                with self.assertRaises(ForbiddenError) as ctx:
                    require_master_only(
                        current_user=CurrentUser(user_id=9, role=role, school_id=1))
                self.assertEqual(
                    str(ctx.exception), f"Role '{role}' cannot access this endpoint")

    def test_role_allowed_encodes_the_hierarchy(self):
        self.assertTrue(role_allowed(SUBMASTER_ROLE, ("master",)))
        self.assertTrue(role_allowed(SUBMASTER_ROLE, ("admin", "master")))
        self.assertTrue(role_allowed(MASTER_ROLE, ("master",)))
        self.assertFalse(role_allowed("admin", ("master",)))
        self.assertFalse(role_allowed(SUBMASTER_ROLE, ("admin",)))
        self.assertTrue(is_master_like(SUBMASTER_ROLE) and is_master_like(MASTER_ROLE))
        self.assertFalse(is_master_like("admin"))

    def test_require_school_scope_treats_submaster_like_master(self):
        sub = CurrentUser(user_id=2, role=SUBMASTER_ROLE, school_id=None)
        with self.assertRaises(ForbiddenError) as ctx:
            require_school_scope(current_user=sub)
        self.assertEqual(
            str(ctx.exception), "Master Admin must specify a school_id explicitly")
        admin = CurrentUser(user_id=101, role="admin", school_id=1)
        self.assertEqual(require_school_scope(current_user=admin), 1)
        admin_no_school = CurrentUser(user_id=105, role="admin", school_id=None)
        with self.assertRaises(ForbiddenError):
            require_school_scope(current_user=admin_no_school)

    # -- 2/3. privileged-account API: lockdown + lifecycle ----------------

    def test_privileged_endpoints_require_authentication(self):
        for method, path in _PRIVILEGED_CALLS():
            with self.subTest(path=f"{method} {path}"):
                response = getattr(self.client, method)(path)
                self.assertEqual(response.status_code, 401)

    def test_submaster_cannot_reach_any_privileged_endpoint(self):
        for method, path in _PRIVILEGED_CALLS(user_id=2):
            with self.subTest(path=f"{method} {path}"):
                response = _call(self.client, method, path,
                                 headers=self.sub_headers,
                                 body=_mutation_json(method))
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()["detail"], LEGACY_DENIAL)

    def test_admin_cannot_reach_privileged_endpoints(self):
        for method, path in _PRIVILEGED_CALLS(user_id=2):
            with self.subTest(path=f"{method} {path}"):
                response = _call(self.client, method, path,
                                 headers=self.admin1_headers,
                                 body=_mutation_json(method))
                self.assertEqual(response.status_code, 403)
                self.assertEqual(
                    response.json()["detail"],
                    "Role 'admin' cannot access this endpoint")

    def test_master_lists_only_privileged_accounts(self):
        response = self.client.get("/auth/privileged-accounts", headers=self.master_headers)
        self.assertEqual(response.status_code, 200)
        roles = {row["role"] for row in response.json()}
        self.assertLessEqual(roles, {"master", "submaster"})
        self.assertIn("submaster", roles)
        usernames = {row["username"] for row in response.json()}
        self.assertIn("master", usernames)
        self.assertIn("sub.admin", usernames)

    def test_create_forces_submaster_role_and_returns_password_once(self):
        payload = {
            "username": "ops.lead",
            "email": "ops@example.com",
            # Forged field: extra keys are ignored and the role is assigned
            # server-side, so this can never mint a Master.
            "role": MASTER_ROLE,
            "password": "lead-secret-99",
        }
        response = self.client.post(
            "/auth/privileged-accounts", headers=self.master_headers, json=payload)
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["role"], SUBMASTER_ROLE)
        self.assertEqual(body["username"], "ops.lead")
        self.assertTrue(body["is_active"])
        self.assertEqual(body["password"], "lead-secret-99")

        # The returned credential actually signs in as a Sub-Master.
        login = self.client.post(
            "/auth/login", json={"username": "ops.lead", "password": "lead-secret-99"})
        self.assertEqual(login.status_code, 200)
        self.assertEqual(login.json()["role"], SUBMASTER_ROLE)

    def test_create_generates_a_password_when_omitted(self):
        response = self.client.post(
            "/auth/privileged-accounts", headers=self.master_headers,
            json={"username": "auto.pass"})
        self.assertEqual(response.status_code, 201)
        generated = response.json()["password"]
        self.assertGreaterEqual(len(generated), 8)
        login = self.client.post(
            "/auth/login", json={"username": "auto.pass", "password": generated})
        self.assertEqual(login.status_code, 200)

    def test_duplicate_username_is_409(self):
        response = self.client.post(
            "/auth/privileged-accounts", headers=self.master_headers,
            json={"username": "SUB.ADMIN"})  # case-insensitive collision
        self.assertEqual(response.status_code, 409)

    def test_update_rotates_password_and_activation(self):
        # Rotate the seeded submaster's password.
        response = self.client.patch(
            "/auth/privileged-accounts/2", headers=self.master_headers,
            json={"password": "rotated-pass-42", "email": "new@example.com"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["email"], "new@example.com")

        old = self.client.post(
            "/auth/login", json={"username": "sub.admin", "password": SUBMASTER_PW})
        self.assertEqual(old.status_code, 401)
        fresh = self.client.post(
            "/auth/login", json={"username": "sub.admin", "password": "rotated-pass-42"})
        self.assertEqual(fresh.status_code, 200)

        # Deactivate: new logins are blocked (identifier lookup matches only
        # active rows). Reactivation restores access with the same password.
        off = self.client.patch(
            "/auth/privileged-accounts/2", headers=self.master_headers,
            json={"is_active": False})
        self.assertEqual(off.status_code, 200)
        blocked = self.client.post(
            "/auth/login", json={"username": "sub.admin", "password": "rotated-pass-42"})
        self.assertEqual(blocked.status_code, 401)
        on = self.client.patch(
            "/auth/privileged-accounts/2", headers=self.master_headers,
            json={"is_active": True})
        self.assertEqual(on.status_code, 200)
        back = self.client.post(
            "/auth/login", json={"username": "sub.admin", "password": "rotated-pass-42"})
        self.assertEqual(back.status_code, 200)

    def test_master_and_other_roles_have_no_mutation_path(self):
        # A Master row: refused for everyone, including a Master actor, with
        # the same generic detail any other non-Sub-Master target gets.
        for method, path in (("patch", "/auth/privileged-accounts/1"),
                             ("delete", "/auth/privileged-accounts/1")):
            with self.subTest(path=f"{method} {path}"):
                response = _call(self.client, method, path,
                                 headers=self.master_headers,
                                 body={"is_active": False} if method == "patch" else None)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()["detail"], GENERIC_PRIVILEGED_DENIAL)
        # A non-privileged row (admin) is equally out of reach.
        response = self.client.patch(
            "/auth/privileged-accounts/101", headers=self.master_headers,
            json={"is_active": False})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], GENERIC_PRIVILEGED_DENIAL)

    def test_unknown_account_is_404(self):
        response = self.client.patch(
            "/auth/privileged-accounts/999999", headers=self.master_headers,
            json={"email": "x@example.com"})
        self.assertEqual(response.status_code, 404)
        response = self.client.delete(
            "/auth/privileged-accounts/999999", headers=self.master_headers)
        self.assertEqual(response.status_code, 404)

    def test_delete_soft_deactivates_and_keeps_the_row(self):
        response = self.client.delete(
            "/auth/privileged-accounts/2", headers=self.master_headers)
        self.assertEqual(response.status_code, 204)
        row = self.db.query(User).filter(User.user_id == 2).one()
        self.assertFalse(row.is_active)
        self.assertEqual(row.role, SUBMASTER_ROLE)
        # Reactivation via PATCH still works after the soft delete.
        back = self.client.patch(
            "/auth/privileged-accounts/2", headers=self.master_headers,
            json={"is_active": True})
        self.assertEqual(back.status_code, 200)

    def test_authz_denials_are_generic_and_logged(self):
        sub_actor = CurrentUser(user_id=2, role=SUBMASTER_ROLE, school_id=None)
        master_actor = CurrentUser(user_id=1, role=MASTER_ROLE, school_id=None)
        # Master actor may manage a Sub-Master target: no raise.
        assert_can_manage_privileged(master_actor, SUBMASTER_ROLE, "update")
        cases = [
            (master_actor, MASTER_ROLE, "update"),
            (master_actor, "admin", "update"),
            (sub_actor, SUBMASTER_ROLE, "update"),
            (sub_actor, MASTER_ROLE, "delete"),
        ]
        with mock.patch("common.authz.logger") as fake_logger:
            for actor, target, action in cases:
                with self.subTest(actor=actor.role, target=target, action=action):
                    with self.assertRaises(ForbiddenError) as ctx:
                        assert_can_manage_privileged(actor, target, action)
                    self.assertEqual(str(ctx.exception), GENERIC_PRIVILEGED_DENIAL)
            self.assertTrue(fake_logger.warning.called)
        with mock.patch("common.authz.logger") as fake_logger:
            with self.assertRaises(ForbiddenError):
                assert_master_only(sub_actor, "create")
            self.assertTrue(fake_logger.warning.called)
            # The log line carries actor and role, never a secret.
            logged = fake_logger.warning.call_args[0][0]
            self.assertIn("submaster", logged)
            self.assertNotIn(SUBMASTER_PW, logged)

    # -- 4. operational parity --------------------------------------------

    def test_support_inbox_is_scoped_like_master(self):
        import services.support_service.repository as support_repo
        support_repo.create_ticket(
            self.db, CurrentUser(user_id=101, role="admin", school_id=1,
                                 linked_person_id=11),
            {"subject": "Slow portal", "body": "It hangs.", "attachments": []})
        support_repo.create_ticket(
            self.db, CurrentUser(user_id=102, role="admin", school_id=2,
                                 linked_person_id=12),
            {"subject": "Fee error", "body": "Wrong total.", "attachments": []})

        sub = self.client.get("/support/tickets", headers=self.sub_headers)
        self.assertEqual(sub.status_code, 200)
        self.assertEqual(len(sub.json()), 2)  # every school, like master

        admin = self.client.get("/support/tickets", headers=self.admin1_headers)
        self.assertEqual(admin.status_code, 200)
        self.assertEqual(len(admin.json()), 1)  # own school only, unchanged

        master = self.client.get("/support/tickets", headers=self.master_headers)
        self.assertEqual(len(master.json()), 2)

    def test_support_reply_records_submaster_author_and_passes_the_check(self):
        import services.support_service.repository as support_repo
        ticket = support_repo.create_ticket(
            self.db, CurrentUser(user_id=101, role="admin", school_id=1,
                                 linked_person_id=11),
            {"subject": "Printer offline", "body": "Queue stuck.", "attachments": []})

        response = self.client.post(
            f"/support/tickets/{ticket.ticket_id}/messages",
            headers=self.sub_headers,
            json={"body": "Resetting the queue now.", "attachments": []})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["author_role"], SUBMASTER_ROLE)
        self.assertEqual(response.json()["author_name"], "sub.admin")

        # Sub-Master may also move the status — that guard is
        # require_role("master"), so parity applies.
        status = self.client.patch(
            f"/support/tickets/{ticket.ticket_id}/status",
            headers=self.sub_headers, json={"status": "In progress"})
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["status"], "In progress")

        # An admin still cannot set status (unchanged).
        admin_status = self.client.patch(
            f"/support/tickets/{ticket.ticket_id}/status",
            headers=self.admin1_headers, json={"status": "Completed"})
        self.assertEqual(admin_status.status_code, 403)

    def test_school_feature_guard_admits_submaster_across_schools(self):
        for school_id in (1, 2):
            with self.subTest(school_id=school_id):
                response = self.client.get(
                    f"/schools/{school_id}/features", headers=self.sub_headers)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["school_id"], school_id)

        # Admin of school 1 still refused on school 2 — unchanged.
        cross = self.client.get("/schools/2/features", headers=self.admin1_headers)
        self.assertEqual(cross.status_code, 403)

        # Admin cannot read features of their own school? (they can) — sanity.
        own = self.client.get("/schools/1/features", headers=self.admin1_headers)
        self.assertEqual(own.status_code, 200)

    def test_marks_master_scope_requires_explicit_school_for_submaster(self):
        scope = self.marks_router._master_scope
        sub = CurrentUser(user_id=2, role=SUBMASTER_ROLE, school_id=None)
        with self.assertRaises(ForbiddenError) as ctx:
            scope(sub, None)
        self.assertEqual(str(ctx.exception), "Master access requires an explicit school_id")
        self.assertEqual(scope(sub, 1), 1)
        master = CurrentUser(user_id=1, role=MASTER_ROLE, school_id=None)
        self.assertEqual(scope(master, 2), 2)

    def test_notifications_send_is_admitted_for_submaster_not_admin(self):
        # is_email_configured is forced off on the repo module the router
        # actually holds (the bare `repository` in router.repo — NOT the
        # full-path module), so the call is deterministic in every
        # environment and can never attempt a real SMTP send. The point under
        # test is only which role passes the require_role("master") gate.
        with mock.patch.object(
            self.notifications_router.repo, "is_email_configured",
            return_value=False,
        ):
            sub = self.client.post(
                "/notifications/school/1", headers=self.sub_headers,
                json={"type": "General", "message": "Exam timetable updated."})
            self.assertNotEqual(sub.status_code, 403)
            self.assertEqual(sub.status_code, 400)  # email gate, not authz

            admin = self.client.post(
                "/notifications/school/1", headers=self.admin1_headers,
                json={"type": "General", "message": "Exam timetable updated."})
            self.assertEqual(admin.status_code, 403)

    def test_non_admin_roles_cannot_reach_admin_only_endpoints(self):
        # PATCH /notifications/{id}/read is require_role("admin") with no
        # master in the set: Sub-Master gains nothing where Master himself
        # has nothing, and teacher/parent denials keep their legacy bodies.
        expected = [
            (self.sub_headers, LEGACY_DENIAL),
            (self.teacher_headers, "Role 'teacher' cannot access this endpoint"),
            (self.parent_headers, "Role 'parent' cannot access this endpoint"),
        ]
        for headers, detail in expected:
            with self.subTest(detail=detail):
                response = self.client.patch(
                    "/notifications/999999/read", headers=headers)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()["detail"], detail)

        # The School Admin passes the gate (reaches the handler: unknown id
        # is 404, not 403) — unchanged.
        admin = self.client.patch(
            "/notifications/999999/read", headers=self.admin1_headers)
        self.assertEqual(admin.status_code, 404)

    # -- 5. model-level parity --------------------------------------------

    def test_role_enum_and_permission_matrix_include_submaster(self):
        self.assertEqual(Role.submaster.value, SUBMASTER_ROLE)
        self.assertEqual(PRIVILEGED_ROLES, frozenset({MASTER_ROLE, SUBMASTER_ROLE}))
        self.assertIn(SUBMASTER_ROLE, PERMISSIONS["schools"]["write"])
        self.assertIn(SUBMASTER_ROLE, PERMISSIONS["notifications"]["write"])
        self.assertIn(SUBMASTER_ROLE, PERMISSIONS["reports"]["read"])
        # Unchanged: a School Admin still cannot write schools or notifications.
        with self.assertRaises(ForbiddenError):
            check_permission("schools", "write", "admin")
        with self.assertRaises(ForbiddenError):
            check_permission("notifications", "write", "admin")
        check_permission("schools", "write", SUBMASTER_ROLE)  # no raise

    def test_submaster_email_reaches_the_recovery_lookup(self):
        from services.auth_service.service import user_email_address
        sub = self.db.query(User).filter(User.user_id == 2).one()
        self.assertEqual(user_email_address(self.db, sub), "sub.admin@example.com")
        master = self.db.query(User).filter(User.user_id == 1).one()
        self.assertIsNone(user_email_address(self.db, master))

    # -- lifecycle extras: sessions, recovery, validation ------------------

    def test_deactivation_blocks_refresh_tokens_not_only_logins(self):
        # Refresh re-reads the users row (unlike the access-token claim), so
        # deactivation must cut active sessions off at refresh as well.
        login = self.client.post(
            "/auth/login", json={"username": "sub.admin", "password": SUBMASTER_PW})
        self.assertEqual(login.status_code, 200)
        refresh_token = login.json()["refresh_token"]

        alive = self.client.post("/auth/refresh", json={"refresh_token": refresh_token})
        self.assertEqual(alive.status_code, 200)

        off = self.client.patch(
            "/auth/privileged-accounts/2", headers=self.master_headers,
            json={"is_active": False})
        self.assertEqual(off.status_code, 200)
        dead = self.client.post("/auth/refresh", json={"refresh_token": refresh_token})
        self.assertEqual(dead.status_code, 401)

        on = self.client.patch(
            "/auth/privileged-accounts/2", headers=self.master_headers,
            json={"is_active": True})
        self.assertEqual(on.status_code, 200)
        back = self.client.post("/auth/refresh", json={"refresh_token": refresh_token})
        self.assertEqual(back.status_code, 200)

    def test_forgot_password_otp_flow_reaches_a_submaster(self):
        # The anonymous flow is unchanged; the only new wiring is that a
        # Sub-Master's own row supplies the delivery address. Delivery is
        # mocked so the test can never touch real SMTP.
        from services.auth_service import service as auth_service

        with mock.patch.object(auth_service, "_deliver_reset_token") as deliver:
            raw_token, expires_at = auth_service.forgot_password_request(
                self.db, "sub.admin")
            self.assertIsNotNone(raw_token)
            self.assertIsNotNone(expires_at)
            deliver.assert_called_once_with("sub.admin@example.com", raw_token)

            # Reset with the OTP, then sign in with the new password.
            auth_service.forgot_password_reset(
                self.db, "sub.admin", raw_token, "recovered-pass-7")
            login = self.client.post(
                "/auth/login",
                json={"username": "sub.admin", "password": "recovered-pass-7"})
            self.assertEqual(login.status_code, 200)
            self.assertEqual(login.json()["role"], SUBMASTER_ROLE)

        # A Master has no address on file: the flow still issues a token
        # internally (pre-existing behaviour, discarded by the router's
        # uniform response) but nothing is ever delivered.
        with mock.patch.object(auth_service, "_deliver_reset_token") as deliver:
            token, expires = auth_service.forgot_password_request(self.db, "master")
            self.assertIsNotNone(token)
            self.assertIsNotNone(expires)
            deliver.assert_not_called()

    def test_me_reports_submaster_identity_without_a_school(self):
        response = self.client.get("/auth/me", headers=self.sub_headers)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["role"], SUBMASTER_ROLE)
        self.assertIsNone(body["school_id"])
        self.assertIsNone(body["school_name"])
        self.assertEqual(body["username"], "sub.admin")
        self.assertEqual(body["email"], "sub.admin@example.com")
        # No linked person row, so the display name falls back to the username.
        self.assertEqual(body["display_name"], "sub.admin")

    def test_patch_email_empty_string_clears_the_address(self):
        response = self.client.patch(
            "/auth/privileged-accounts/2", headers=self.master_headers,
            json={"email": ""})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["email"])
        from services.auth_service.service import user_email_address
        sub = self.db.query(User).filter(User.user_id == 2).one()
        self.assertIsNone(user_email_address(self.db, sub))

    def test_password_rotation_clears_an_outstanding_reset_otp(self):
        import hashlib
        sub = self.db.query(User).filter(User.user_id == 2).one()
        sub.password_reset_token = hashlib.sha256(b"stale-otp").hexdigest()
        self.db.commit()

        response = self.client.patch(
            "/auth/privileged-accounts/2", headers=self.master_headers,
            json={"password": "rotated-pass-42"})
        self.assertEqual(response.status_code, 200)
        self.db.refresh(sub)
        self.assertIsNone(sub.password_reset_token)
        self.assertIsNone(sub.password_reset_token_expires_at)

    def test_create_and_update_validate_like_the_rest_of_the_api(self):
        for payload, label in (
            ({"username": "ab"}, "username too short"),
            ({"username": "  ", "password": "long-enough-1"}, "blank username"),
            ({"username": "valid.name", "password": "short"}, "short password"),
        ):
            with self.subTest(label=label):
                response = self.client.post(
                    "/auth/privileged-accounts", headers=self.master_headers,
                    json=payload)
                self.assertEqual(response.status_code, 422)
        response = self.client.patch(
            "/auth/privileged-accounts/2", headers=self.master_headers,
            json={"password": "short"})
        self.assertEqual(response.status_code, 422)

    def test_a_forged_school_claim_grants_no_implicit_scope(self):
        # Even a Sub-Master token that carries a school_id claim is refused
        # the implicit school scope — the role check comes first, exactly as
        # for Master.
        forged = CurrentUser(user_id=2, role=SUBMASTER_ROLE, school_id=1)
        with self.assertRaises(ForbiddenError) as ctx:
            require_school_scope(current_user=forged)
        self.assertEqual(
            str(ctx.exception), "Master Admin must specify a school_id explicitly")


class SubMasterSchemaContractTests(unittest.TestCase):
    """Source-level contracts: the migration pair, the model, the gateway
    path, and the seed-reset predicate must all agree with the role."""

    ROOT = Path(__file__).resolve().parents[1]

    def test_migration_widens_both_constraints_idempotently(self):
        sql = (self.ROOT / "db-migration-submaster-role.sql").read_text(encoding="utf-8")
        for constraint in ("users_role_check", "support_ticket_messages_role_check"):
            with self.subTest(constraint=constraint):
                self.assertIn(constraint, sql)
                # Rebuild lists submaster...
                self.assertRegex(
                    sql,
                    rf"{constraint}[\s\S]*?CHECK \([^\)]*submaster[^\)]*\)")
                # ...and only when the old definition is present and stale.
                self.assertIn("LIKE '%submaster%'", sql)
                self.assertIn("'schoolers.users'::regclass", sql)
                self.assertIn("'schoolers.support_ticket_messages'::regclass", sql)
        self.assertIn("idempotent", sql.lower())

    def test_db_migrations_support_check_lists_submaster(self):
        migrations = (self.ROOT / "db-migrations.sql").read_text(encoding="utf-8")
        self.assertIn(
            "CHECK (author_role IN ('admin','master','submaster'))", migrations)

    def test_users_model_carries_no_role_check_that_could_narrow_the_role(self):
        # The live CHECK lives only in the database (widened by the
        # migration); the model must not re-introduce a narrower list.
        users = Base.metadata.tables["users"]
        role_checks = [
            constraint for constraint in users.constraints
            if isinstance(constraint, CheckConstraint) and "role" in str(constraint)
        ]
        self.assertEqual(role_checks, [])

    def test_gateway_forwards_the_auth_segment_where_the_endpoints_live(self):
        gateway = (self.ROOT / "gateway" / "main.py").read_text(encoding="utf-8")
        # The privileged-account endpoints hang off /auth, so the gateway's
        # path-segment table must route that segment to the auth service.
        self.assertIn('"auth"', gateway)

    def test_seed_reset_keeps_both_privileged_roles(self):
        reset = (self.ROOT / "seed" / "reset.py").read_text(encoding="utf-8")
        self.assertIn("KEEP_MASTER_USERS", reset)
        self.assertRegex(
            reset,
            r"KEEP_MASTER_USERS = \"role IN \('master', 'submaster'\)\"")


def _headers(user_id: int, role: str, school_id: int | None) -> dict:
    token = create_access_token(user_id, role, school_id)
    return {"Authorization": f"Bearer {token}"}


def _call(client, method: str, path: str, headers: dict | None = None,
          body=None):
    """TestClient.get/delete accept no `json` kwarg — send a body only on
    the methods that can carry one."""
    kwargs = {"headers": headers} if headers is not None else {}
    if body is not None and method in ("post", "put", "patch"):
        kwargs["json"] = body
    return getattr(client, method)(path, **kwargs)


def _PRIVILEGED_CALLS(user_id: int = 2):
    """(http method, path) for each privileged endpoint, aimed at `user_id`."""
    return (
        ("get", "/auth/privileged-accounts"),
        ("post", "/auth/privileged-accounts"),
        ("patch", f"/auth/privileged-accounts/{user_id}"),
        ("delete", f"/auth/privileged-accounts/{user_id}"),
    )


def _mutation_json(method: str) -> dict | None:
    if method == "post":
        return {"username": "forged.ops"}
    if method == "patch":
        return {"is_active": False}
    return None


if __name__ == "__main__":
    unittest.main()
