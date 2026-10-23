"""Sub-Admin role: Add Staff creation, Admin management, lockdown, parity.

Five invariants, one per section below:

1. Add Staff workflow — an Admin creates a Sub-Admin through the ordinary
   POST /staff form (label "Sub-Admin"); the row persists with the right
   role/person_type/school, the provisioned login carries role 'subadmin',
   authenticates with the emailed one-time password, and appears in the staff
   listing. Existing Teacher/Pilot/Other-staff creation is untouched.
2. Admin management — Admin updates, relabels (demote/promote), deactivates
   and removes Sub-Admins through the same staff endpoints; the linked login
   stays in step; Admin's existing ability to edit admin-linked staff rows is
   preserved.
3. Sub-Admin security — direct API calls (no frontend in the loop) are denied:
   cannot mint a Sub-Admin (any label variant), cannot create an Admin account
   at all, cannot update/delete Admin or Sub-Admin staff records including its
   own, cannot assign the Sub-Admin label on edit, cannot escalate through
   crafted person_type/role fields. Cross-school stays denied; other roles
   keep their exact legacy denials; bulk/alternate staff surfaces do not
   exist. Self-service profile reads and password change still work.
4. Operational parity — require_role admission wherever Admin is (every
   distinct admin-guarded combination the routers ship), school scoping,
   cross-school isolation on reports/notifications, _guard_school_access,
   plus the converted call sites proven over HTTP/units (staff attendance,
   class summaries, broadcasts, trips, gallery writes, sender identity,
   admin-login sync, email resolution) and a source scan pinning that no
   inline `role == "admin"` authorization check remains.
5. Schema contracts — migration files, model CHECKs, Role enum, PERMISSIONS
   and broadcast-scope parity; no real email is ever sent (helpers patched).
"""
import re
import sys
import unittest
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from common.authz import (
    ADMIN_ROLE,
    SCHOOL_ADMIN_ROLES,
    SUBADMIN_ROLE,
    assert_can_assign_subadmin,
    assert_can_manage_school_account,
    is_master_like,
    is_school_admin,
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
from common.exceptions import ForbiddenError, NotFoundError
from common.models import (
    Broadcast,
    CheckConstraint,
    Media,
    School,
    SchoolNotification,
    Staff,
    StaffAttendance,
    User,
)
from common.permissions import PERMISSIONS
from common.enums import Role
from common.security import create_access_token, hash_password

# Every distinct require_role(...) combination the routers ship that admits
# Admin (schools/reports/notifications/marks/attendance/transport/timetable/
# leave/communication/media/people/academics/support). If a router grows a new
# admin-guarded combination it belongs here: this list is what proves
# Sub-Admin inherits the whole admin surface.
ADMIN_GUARDS = [
    ("admin",),
    ("master", "admin"),
    ("admin", "master"),
    ("teacher", "admin"),
    ("parent", "teacher", "admin"),
    ("teacher", "admin", "master"),
    ("parent", "teacher", "admin", "master"),
    ("teacher", "admin", "staff"),
    ("admin", "teacher", "staff", "pilot"),   # reports SELF_ROLES
    ("teacher", "admin", "pilot", "staff"),
    ("parent", "teacher", "admin", "pilot", "staff"),
    ("admin", "staff"),
    ("parent", "teacher", "admin", "staff"),
    ("parent", "admin", "pilot"),
    ("admin", "pilot"),
    ("parent", "admin", "pilot", "staff"),
    ("parent", "teacher", "staff", "pilot", "admin"),  # leave request roles
    ("parent", "teacher", "admin", "pilot"),           # transport
]

# Combinations that must keep denying Sub-Admin: Admin is not among them.
NON_ADMIN_GUARDS = [
    ("master",),
    ("parent",),
    ("teacher",),
    ("pilot",),
    ("staff",),
    ("parent", "teacher"),
    ("teacher", "pilot"),
]

GENERIC_DENIED = "Not permitted to manage privileged accounts"
SUBADMIN_PW = "subadmin-pass1"
SUBADMIN_PW_HASH = hash_password(SUBADMIN_PW)


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


def _headers(user_id: int, role: str, school_id: int | None,
             linked_person_id: int | None = None) -> dict:
    token = create_access_token(user_id, role, school_id, linked_person_id)
    return {"Authorization": f"Bearer {token}"}


def _staff_payload(role: str, name: str = "Nia Patel", **overrides) -> dict:
    first = name.split()[0].lower()
    data = {
        "name": name,
        "role": role,
        "phone": "9876543210",
        "email": f"{first}.patel@schoolers.test",
        "date_of_birth": "1994-04-01",
        "marital_status": "Single",
        "gender": "Female",
        "present_address": "12 Lake Road",
        "permanent_address": "12 Lake Road",
        "aadhaar_card": "123456789012",
        "emergency_number": "9876543211",
    }
    data.update(overrides)
    return data


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
            Staff(staff_id=13, school_id=1, name="Nia Patel", role="Sub-Admin"),
            Staff(staff_id=14, school_id=2, name="Omar Siddiq", role="Sub-Admin"),
            Staff(staff_id=15, school_id=1, name="Leena Dsouza", role="Librarian"),
            User(user_id=1, school_id=None, role="master", username="master",
                 password_hash="x"),
            User(user_id=2, school_id=None, role="submaster", username="sub.master",
                 password_hash="x"),
            User(user_id=101, school_id=1, role="admin", username="admin1",
                 password_hash="x", linked_person_id=11, is_active=True),
            User(user_id=102, school_id=2, role="admin", username="admin2",
                 password_hash="x", linked_person_id=12, is_active=True),
            User(user_id=201, school_id=1, role="subadmin", username="sub.admin1",
                 email="nia.patel@schoolers.test", password_hash=SUBADMIN_PW_HASH,
                 linked_person_id=13, is_active=True),
            User(user_id=202, school_id=2, role="subadmin", username="sub.admin2",
                 password_hash=SUBADMIN_PW_HASH, linked_person_id=14, is_active=True),
            User(user_id=301, school_id=1, role="staff", username="leena",
                 password_hash="x", linked_person_id=15, is_active=True),
            User(user_id=401, school_id=1, role="teacher", username="teach1",
                 password_hash="x", is_active=True),
            User(user_id=501, school_id=1, role="parent", username="paren1",
                 password_hash="x", is_active=True),
            SchoolNotification(notification_id=1, school_id=2, type="Holiday",
                               message="Blue Ridge founders day"),
        ]
    )
    db.commit()


class SubAdminAddStaffTests(unittest.TestCase):
    """End-to-end HTTP behaviour over real JWTs: creation, auth, management."""

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        # Broadcast.created_at defaults to Postgres' timezone()/now(); SQLite
        # has neither, so give this engine tiny stand-ins and keep the real
        # INSERT path (the HTTP broadcast tests exercise it) unmodified.
        @event.listens_for(cls.engine, "connect")
        def _sqlite_broadcast_defaults(dbapi_connection, _record):
            dbapi_connection.create_function(
                "timezone", 2, lambda _tz, timestamp: timestamp)
            dbapi_connection.create_function(
                "now", 0, lambda: datetime.utcnow().isoformat(" "))
        # Everything except website_builder_sites: that table's draft column
        # is JSONB (Postgres-only) and no endpoint here touches it.
        Base.metadata.create_all(
            cls.engine,
            tables=[table for name, table in Base.metadata.tables.items()
                    if name != "website_builder_sites"],
        )
        cls.Session = sessionmaker(bind=cls.engine, autoflush=False, future=True)

        cls.auth_router = _load("services/auth_service", "services.auth_service")
        cls.people_router = _load("services/people_service", "services.people_service")
        cls.reports_router = _load("services/reports_service", "services.reports_service")
        cls.notifications_router = _load(
            "services/notifications_service", "services.notifications_service")
        cls.schools_router = _load("services/schools_service", "services.schools_service")
        cls.attendance_router = _load(
            "services/attendance_service", "services.attendance_service")
        cls.communication_router = _load(
            "services/communication_service", "services.communication_service")

        cls.app = FastAPI()
        for mod in (cls.auth_router, cls.people_router, cls.reports_router,
                    cls.notifications_router, cls.attendance_router,
                    cls.communication_router):
            cls.app.include_router(mod.router)
        register_exception_handlers(cls.app)

        def _db():
            session = cls.Session()
            try:
                yield session
            finally:
                session.close()

        cls.app.dependency_overrides[get_db] = _db
        cls.client = TestClient(cls.app)

        # No test ever sends mail: capture the onboarding helper so the
        # provisioned credentials are assertable without a delivery, and
        # swallow every other send (record-update notices, auth emails).
        cls.sent_credentials = []

        def _record_onboarding(school_name, name, recipients, **kw):
            cls.sent_credentials.append(
                (list(recipients), kw.get("username"), kw.get("password")))

        cls._email_patch = mock.patch("common.email.send_email", return_value=False)
        cls._onboard_patch = mock.patch.object(
            cls.people_router.repo, "send_staff_added_email",
            side_effect=_record_onboarding)
        cls._email_patch.start()
        cls._onboard_patch.start()

    @classmethod
    def tearDownClass(cls):
        cls._onboard_patch.stop()
        cls._email_patch.stop()

    def setUp(self):
        db = self.Session()
        self.db = db
        for table in reversed(Base.metadata.sorted_tables):
            if table.name == "website_builder_sites":
                continue  # not created (JSONB), matches create_all above
            db.execute(table.delete())
        db.commit()
        _seed(db)
        type(self).sent_credentials.clear()
        self.admin1_headers = _headers(101, "admin", 1, linked_person_id=11)
        self.admin2_headers = _headers(102, "admin", 2, linked_person_id=12)
        self.sub1_headers = _headers(201, "subadmin", 1, linked_person_id=13)
        self.sub2_headers = _headers(202, "subadmin", 2, linked_person_id=14)
        self.teacher_headers = _headers(401, "teacher", 1)
        self.parent_headers = _headers(501, "parent", 1)
        self.master_headers = _headers(1, "master", None)
        self.submaster_headers = _headers(2, "submaster", None)

    def tearDown(self):
        self.db.close()

    def _login_user(self, staff_id: int) -> User:
        return (
            self.db.query(User).filter(User.linked_person_id == staff_id).one()
        )

    # -- 1. Add Staff workflow -------------------------------------------

    def test_admin_creates_a_sub_admin_through_add_staff(self):
        response = self.client.post(
            "/staff", headers=self.admin1_headers, json=_staff_payload("Sub-Admin"))
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(body["role"], "Sub-Admin")
        self.assertEqual(body["person_type"], "admin")
        self.assertEqual(body["school_id"], 1)
        self.assertTrue(body["is_active"])

        user = (
            self.db.query(User).filter(User.linked_person_id == body["staff_id"]).one()
        )
        self.assertEqual(user.role, "subadmin")
        self.assertEqual(user.school_id, 1)
        self.assertTrue(user.is_active)
        self.assertEqual(user.email, "nia.patel@schoolers.test")

        # The onboarding helper carried working credentials — captured, never
        # delivered: (recipients, username, temp_password).
        self.assertEqual(len(self.sent_credentials), 1)
        recipients, username, password = self.sent_credentials[0]
        self.assertEqual(recipients, ["nia.patel@schoolers.test"])
        self.assertEqual(username, user.username)
        self.assertTrue(password)

    def test_created_sub_admin_authenticates_and_reports_its_identity(self):
        response = self.client.post(
            "/staff", headers=self.admin1_headers, json=_staff_payload("Sub-Admin"))
        self.assertEqual(response.status_code, 201, response.text)
        _, username, password = self.sent_credentials[0]

        login = self.client.post(
            "/auth/login", json={"username": username, "password": password})
        self.assertEqual(login.status_code, 200, login.text)
        self.assertEqual(login.json()["role"], "subadmin")
        self.assertEqual(login.json()["school_id"], 1)

        me = self.client.get(
            "/auth/me", headers={"Authorization": f"Bearer {login.json()['access_token']}"})
        self.assertEqual(me.status_code, 200)
        body = me.json()
        self.assertEqual(body["role"], "subadmin")
        self.assertEqual(body["school_id"], 1)
        self.assertEqual(body["display_name"], "Nia Patel")
        self.assertEqual(body["email"], "nia.patel@schoolers.test")

    def test_staff_listing_shows_the_sub_admin_row(self):
        response = self.client.get("/staff", headers=self.admin1_headers)
        self.assertEqual(response.status_code, 200)
        rows = {row["staff_id"]: row for row in response.json()}
        self.assertEqual(rows[13]["role"], "Sub-Admin")
        # Same visibility for the Sub-Admin actor: the directory is shared.
        as_sub = self.client.get("/staff", headers=self.sub1_headers)
        self.assertEqual(as_sub.status_code, 200)
        self.assertIn(13, {row["staff_id"] for row in as_sub.json()})

    def test_existing_staff_creation_flows_continue_to_work(self):
        for label, person_type, login_role in (
            ("Teacher", "teacher", "teacher"),
            ("Pilot", "pilot", "pilot"),
            ("Lab Assistant", "staff", "staff"),
        ):
            with self.subTest(label=label):
                response = self.client.post(
                    "/staff", headers=self.admin1_headers,
                    json=_staff_payload(label, name=f"Case {label}"))
                self.assertEqual(response.status_code, 201, response.text)
                body = response.json()
                self.assertEqual(body["person_type"], person_type)
                user = self._login_user(body["staff_id"])
                self.assertEqual(user.role, login_role)

    def test_sub_admin_can_create_ordinary_staff_but_the_login_stays_plain(self):
        response = self.client.post(
            "/staff", headers=self.sub1_headers,
            json=_staff_payload("Office Assistant", name="Ravi Kumar"))
        self.assertEqual(response.status_code, 201, response.text)
        user = self._login_user(response.json()["staff_id"])
        self.assertEqual(user.role, "staff")

    # -- 2. Admin management ---------------------------------------------

    def test_admin_updates_a_sub_admin_record(self):
        response = self.client.patch(
            "/staff/13", headers=self.admin1_headers, json={"phone": "9000000042"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["phone"], "9000000042")
        self.assertEqual(self._login_user(13).role, "subadmin")  # role untouched

    def test_admin_demotes_and_promotes_through_the_role_label(self):
        demote = self.client.patch(
            "/staff/13", headers=self.admin1_headers, json={"role": "Warden"})
        self.assertEqual(demote.status_code, 200, demote.text)
        self.assertEqual(demote.json()["role"], "Warden")
        self.assertEqual(self._login_user(13).role, "staff")

        promote = self.client.patch(
            "/staff/15", headers=self.admin1_headers, json={"role": "Sub-Admin"})
        self.assertEqual(promote.status_code, 200, promote.text)
        self.assertEqual(self._login_user(15).role, "subadmin")

    def test_admin_removes_a_sub_admin_and_the_login_goes_with_it(self):
        response = self.client.delete("/staff/13", headers=self.admin1_headers)
        self.assertEqual(response.status_code, 204)
        staff = self.db.query(Staff).filter(Staff.staff_id == 13).one()
        self.assertFalse(staff.is_active)
        user = self._login_user(13)
        self.assertFalse(user.is_active)

        login = self.client.post(
            "/auth/login", json={"username": user.username, "password": SUBADMIN_PW})
        self.assertEqual(login.status_code, 401)

    def test_admin_deactivates_a_sub_admin_via_patch(self):
        # Deactivation through PATCH is the supported status change: the
        # linked Sub-Admin login goes with the staff row, so no privileged
        # login outlives its record.
        off = self.client.patch(
            "/staff/13", headers=self.admin1_headers, json={"is_active": False})
        self.assertEqual(off.status_code, 200, off.text)
        self.assertFalse(off.json()["is_active"])
        self.assertFalse(self._login_user(13).is_active)

        # Subsequent lookups 404 because get_staff resolves active rows only —
        # pre-existing semantics shared by every staff role (there has never
        # been a reactivation endpoint), here just pinned.
        again = self.client.patch(
            "/staff/13", headers=self.admin1_headers, json={"is_active": True})
        self.assertEqual(again.status_code, 404)
        # Direct API with a fresh token still cannot touch the hidden row.
        hidden = self.client.delete("/staff/13", headers=self.sub1_headers)
        self.assertEqual(hidden.status_code, 404)

    def test_admin_account_management_policy_is_unchanged(self):
        # An Admin may still edit the staff record behind an Admin login in
        # its own school — the target guard restricts Sub-Admin actors only.
        response = self.client.patch(
            "/staff/11", headers=self.admin1_headers, json={"phone": "9111111111"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self._login_user(11).role, "admin")

    # -- 3. Sub-Admin security -------------------------------------------

    def test_sub_admin_cannot_create_a_sub_admin_in_any_label_variant(self):
        for variant in ("Sub-Admin", "subadmin", "SUB ADMIN", "Sub–Admin "):
            with self.subTest(variant=variant):
                response = self.client.post(
                    "/staff", headers=self.sub1_headers,
                    json=_staff_payload(variant, name=f"Evil {variant}"))
                self.assertEqual(response.status_code, 403, response.text)
                self.assertEqual(response.json()["detail"], GENERIC_DENIED)
        # Nothing was created, provisioned or labelled along the way.
        self.assertEqual(
            self.db.query(User).filter(User.role == "subadmin").count(), 2)
        self.assertEqual(
            self.db.query(Staff).filter(Staff.role == "Sub-Admin").count(), 2)

    def test_sub_admin_cannot_create_an_admin_account(self):
        # The staff flow has no path to an admin login at all: even the
        # "Admin" label provisions a plain staff login (existing policy).
        response = self.client.post(
            "/staff", headers=self.sub1_headers, json=_staff_payload("Admin"))
        self.assertEqual(response.status_code, 201, response.text)
        user = self._login_user(response.json()["staff_id"])
        self.assertEqual(user.role, "staff")
        self.assertEqual(
            self.db.query(User).filter(User.role == "admin").count(), 2)

    def test_sub_admin_cannot_update_or_delete_another_sub_admin(self):
        patch = self.client.patch(
            "/staff/13", headers=self.sub1_headers, json={"phone": "9000000099"})
        self.assertEqual(patch.status_code, 403, patch.text)
        self.assertEqual(patch.json()["detail"], GENERIC_DENIED)
        delete = self.client.delete("/staff/13", headers=self.sub1_headers)
        self.assertEqual(delete.status_code, 403)
        self.assertEqual(delete.json()["detail"], GENERIC_DENIED)
        self.assertTrue(self.db.query(Staff).filter(Staff.staff_id == 13).one().is_active)

    def test_sub_admin_cannot_update_or_delete_an_admin(self):
        patch = self.client.patch(
            "/staff/11", headers=self.sub1_headers, json={"role": "Clerk"})
        self.assertEqual(patch.status_code, 403, patch.text)
        delete = self.client.delete("/staff/11", headers=self.sub1_headers)
        self.assertEqual(delete.status_code, 403)
        self.assertEqual(self._login_user(11).role, "admin")
        self.assertTrue(self._login_user(11).is_active)

    def test_sub_admin_cannot_modify_its_own_account_through_staff_endpoints(self):
        for method, path, body in (
            ("patch", "/staff/13", {"phone": "1", "is_active": False}),
            ("patch", "/staff/13", {"role": "Teacher"}),
            ("delete", "/staff/13", None),
        ):
            with self.subTest(method=method, body=body):
                response = getattr(self.client, method)(
                    path, headers=self.sub1_headers, **({"json": body} if body else {}))
                self.assertEqual(response.status_code, 403, response.text)
                self.assertEqual(response.json()["detail"], GENERIC_DENIED)
        me_row = self.db.query(Staff).filter(Staff.staff_id == 13).one()
        self.assertTrue(me_row.is_active)
        self.assertIsNone(me_row.phone)  # every rejected patch left it untouched
        self.assertTrue(self._login_user(13).is_active)

    def test_sub_admin_cannot_assign_the_sub_admin_label_on_edit(self):
        response = self.client.patch(
            "/staff/15", headers=self.sub1_headers, json={"role": "Sub-Admin"})
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(response.json()["detail"], GENERIC_DENIED)
        self.assertEqual(self._login_user(15).role, "staff")

    def test_crafted_payloads_cannot_reach_a_privileged_login(self):
        # person_type is a constrained literal; anything outside the four
        # employment values is rejected outright by validation.
        forged = self.client.post(
            "/staff", headers=self.sub1_headers,
            json=_staff_payload("Office Assistant", person_type="subadmin"))
        self.assertEqual(forged.status_code, 422)
        # An Admin actor sending the same field still gets no privileged
        # login unless the label says Sub-Admin, and the label branch pins
        # person_type to "admin" so the two can never disagree.
        created = self.client.post(
            "/staff", headers=self.admin1_headers,
            json=_staff_payload("Admin", person_type="teacher"))
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()["person_type"], "teacher")
        self.assertEqual(self._login_user(created.json()["staff_id"]).role, "teacher")

    def test_staff_surface_has_no_bulk_or_alternate_mutation_path(self):
        paths = {}
        for route in self.people_router.router.routes:
            if "staff" in route.path and route.path != "/staff/{staff_id}/mark":
                paths.setdefault(route.path, set()).update(route.methods)
        self.assertEqual(paths, {
            "/staff": {"GET", "POST"},
            "/staff/{staff_id}": {"PATCH", "DELETE"},
        })

    def test_cross_school_staff_access_stays_denied(self):
        # School 2's staff rows are simply not visible from school 1.
        missing = self.client.patch(
            "/staff/14", headers=self.sub1_headers, json={"phone": "2"})
        self.assertEqual(missing.status_code, 404)
        deleted = self.client.delete("/staff/11", headers=self.admin2_headers)
        self.assertEqual(deleted.status_code, 404)
        # A token without a school cannot reach the school-scoped endpoints.
        schoolless = CurrentUser(user_id=201, role="subadmin", school_id=None)
        with self.assertRaises(ForbiddenError) as ctx:
            require_school_scope(current_user=schoolless)
        self.assertEqual(str(ctx.exception), "User is not associated with a school")

    def test_other_roles_keep_their_existing_denials(self):
        payload = _staff_payload("Office Assistant")
        # require_school_scope resolves before require_role, so master-like
        # tokens (no school claim) see the scope message; school-scoped roles
        # reach the role guard and see the legacy membership message.
        for headers, detail in (
            (self.teacher_headers, "Role 'teacher' cannot access this endpoint"),
            (self.parent_headers, "Role 'parent' cannot access this endpoint"),
            (self.master_headers, "Master Admin must specify a school_id explicitly"),
            (self.submaster_headers, "Master Admin must specify a school_id explicitly"),
        ):
            with self.subTest(detail=detail):
                response = self.client.post("/staff", headers=headers, json=payload)
                self.assertEqual(response.status_code, 403, response.text)
                self.assertEqual(response.json()["detail"], detail)

    def test_unauthenticated_staff_calls_get_401(self):
        response = self.client.post("/staff", json=_staff_payload("Office Assistant"))
        self.assertEqual(response.status_code, 401)

    def test_self_service_profile_and_password_change_still_work(self):
        me = self.client.get("/auth/me", headers=self.sub1_headers)
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["role"], "subadmin")

        wrong = self.client.post(
            "/auth/change-password", headers=self.sub1_headers,
            json={"current_password": "nope", "new_password": "brand-new-pass1"})
        self.assertEqual(wrong.status_code, 401)

        ok = self.client.post(
            "/auth/change-password", headers=self.sub1_headers,
            json={"current_password": SUBADMIN_PW, "new_password": "brand-new-pass1"})
        self.assertEqual(ok.status_code, 200, ok.text)
        login = self.client.post(
            "/auth/login", json={"username": "sub.admin1", "password": "brand-new-pass1"})
        self.assertEqual(login.status_code, 200)
        self.assertEqual(login.json()["role"], "subadmin")

    # -- 4. operational parity -------------------------------------------

    def test_require_role_admits_subadmin_for_every_admin_guard(self):
        sub = CurrentUser(user_id=201, role=SUBADMIN_ROLE, school_id=1)
        for combo in ADMIN_GUARDS:
            with self.subTest(guard=combo):
                self.assertIs(require_role(*combo)(current_user=sub), sub)

    def test_require_role_denies_subadmin_when_admin_is_not_allowed(self):
        sub = CurrentUser(user_id=201, role=SUBADMIN_ROLE, school_id=1)
        for combo in NON_ADMIN_GUARDS:
            with self.subTest(guard=combo):
                with self.assertRaises(ForbiddenError) as ctx:
                    require_role(*combo)(current_user=sub)
                self.assertEqual(
                    str(ctx.exception),
                    "Role 'subadmin' cannot access this endpoint")

    def test_role_allowed_encodes_both_tiers(self):
        self.assertTrue(role_allowed(SUBADMIN_ROLE, ("admin",)))
        self.assertTrue(role_allowed(SUBADMIN_ROLE, ("teacher", "admin")))
        self.assertFalse(role_allowed(SUBADMIN_ROLE, ("master",)))
        self.assertFalse(role_allowed(SUBADMIN_ROLE, ("parent", "teacher")))
        # The tiers never leak into each other (direct membership still
        # behaves as plain set membership when a combo names the role itself).
        self.assertFalse(role_allowed("admin", ("master",)))
        self.assertFalse(role_allowed("submaster", ("admin",)))
        self.assertTrue(role_allowed(SUBADMIN_ROLE, ("subadmin",)))
        # require_master_only keeps Sub-Admin (and everyone but Master) out.
        with self.assertRaises(ForbiddenError) as ctx:
            require_master_only(
                current_user=CurrentUser(user_id=201, role=SUBADMIN_ROLE, school_id=1))
        self.assertEqual(str(ctx.exception), "Role 'subadmin' cannot access this endpoint")

    def test_school_admin_helper_matches_the_tier(self):
        self.assertEqual(SCHOOL_ADMIN_ROLES, frozenset({"admin", "subadmin"}))
        for role in ("admin", "subadmin"):
            with self.subTest(role=role):
                self.assertTrue(is_school_admin(role))
                self.assertFalse(is_master_like(role))
        for role in ("teacher", "staff", "pilot", "parent", "master", "submaster"):
            with self.subTest(role=role):
                self.assertFalse(is_school_admin(role))

    def test_school_scope_and_school_guard_treat_subadmin_as_school_admin(self):
        sub = CurrentUser(user_id=201, role=SUBADMIN_ROLE, school_id=1)
        self.assertEqual(require_school_scope(current_user=sub), 1)

        guard = self.schools_router._guard_school_access
        actor = SimpleNamespace(role="subadmin", school_id=1, user_id=201)
        guard(actor, 1)  # own school: no raise
        with self.assertRaises(ForbiddenError) as ctx:
            guard(actor, 2)
        self.assertEqual(str(ctx.exception), "Not allowed to access this school")

    def test_cross_school_reports_and_notifications_are_isolated(self):
        overview = self.client.get("/reports/school/2/overview",
                                   headers=self.sub1_headers)
        self.assertEqual(overview.status_code, 403, overview.text)
        notes = self.client.get("/notifications/school/2", headers=self.sub1_headers)
        self.assertEqual(notes.status_code, 200)
        self.assertEqual(notes.json(), [])  # school 2's row must not leak
        own = self.client.get("/notifications/school/1", headers=self.sub1_headers)
        self.assertEqual(own.status_code, 200)

    def test_guards_log_denials_without_leaking_details(self):
        with self.assertLogs("schoolers.authz", level="WARNING") as caught:
            with self.assertRaises(ForbiddenError) as ctx:
                assert_can_assign_subadmin(
                    SimpleNamespace(user_id=201, role="subadmin"), "assignment")
        self.assertEqual(str(ctx.exception), GENERIC_DENIED)
        self.assertIn("role='subadmin'", caught.output[0])

        with self.assertLogs("schoolers.authz", level="WARNING"):
            with self.assertRaises(ForbiddenError):
                assert_can_manage_school_account(
                    SimpleNamespace(user_id=201, role="subadmin"),
                    {"staff", "subadmin"}, "update")
        # A plain staff target is fine for a Sub-Admin; an Admin actor is
        # never restricted by this guard.
        assert_can_manage_school_account(
            SimpleNamespace(user_id=201, role="subadmin"), {"staff"}, "update")
        assert_can_manage_school_account(
            SimpleNamespace(user_id=101, role="admin"), {"subadmin"}, "update")

    def test_staff_attendance_reads_and_marks_follow_the_admin_tier(self):
        # Rows for two school-1 staff: one privileged, one ordinary.
        self.db.add_all([
            StaffAttendance(school_id=1, staff_id=11, date=date(2026, 10, 20),
                            status="Present", marked_by=101),
            StaffAttendance(school_id=1, staff_id=15, date=date(2026, 10, 20),
                            status="Absent", marked_by=101),
        ])
        self.db.commit()

        # Sub-Admin reads the whole school register (is_school_admin branch);
        # an ordinary staff session is still forced down to its own row.
        sub_view = self.client.get("/attendance/staff", headers=self.sub1_headers)
        self.assertEqual(sub_view.status_code, 200, sub_view.text)
        self.assertEqual(sorted(r["staff_id"] for r in sub_view.json()), [11, 15])
        staff_view = self.client.get(
            "/attendance/staff",
            headers=_headers(301, "staff", 1, linked_person_id=15))
        self.assertEqual(staff_view.status_code, 200, staff_view.text)
        self.assertEqual(sorted(r["staff_id"] for r in staff_view.json()), [15])

        # Marking is require_role("admin"): Sub-Admin admitted, school-checked.
        marked = self.client.post(
            "/attendance/staff/mark", headers=self.sub1_headers,
            json={"date": "2026-10-21",
                  "entries": [{"staff_id": 11, "status": "Present"}]})
        self.assertEqual(marked.status_code, 200, marked.text)
        self.assertEqual([r["staff_id"] for r in marked.json()], [11])

        cross = self.client.post(
            "/attendance/staff/mark", headers=self.sub1_headers,
            json={"date": "2026-10-21",
                  "entries": [{"staff_id": 12, "status": "Present"}]})
        self.assertEqual(cross.status_code, 404, cross.text)
        self.assertIn("not in your school", cross.json()["detail"])

    def test_class_summary_scopes_to_the_sub_admins_school(self):
        # School-admin branch: an unknown class is "not in your school".
        other = self.client.get(
            "/attendance/class/99/summary", params={"date": "2026-10-20"},
            headers=self.sub1_headers)
        self.assertEqual(other.status_code, 403, other.text)
        self.assertEqual(other.json()["detail"],
                         "This class is not in your school")
        # The same call as a teacher takes the teacher branch, proving which
        # branch a Sub-Admin lands in rather than some generic denial.
        as_teacher = self.client.get(
            "/attendance/class/99/summary", params={"date": "2026-10-20"},
            headers=self.teacher_headers)
        self.assertEqual(as_teacher.status_code, 403, as_teacher.text)
        self.assertEqual(
            as_teacher.json()["detail"],
            "This teacher account isn't linked to a teacher record")

    def test_broadcast_post_derives_the_sub_admin_identity(self):
        # Client-supplied role/sender labels are ignored: the server derives
        # them from the authenticated login and its live staff row.
        response = self.client.post(
            "/broadcasts", headers=self.sub1_headers,
            json={"scope": "school", "message": "Assembly at 10 in the hall",
                  "role_name": "Admin", "sender_name": "Spoofed Name"})
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(body["role_name"], "Sub-Admin")
        self.assertEqual(body["sender_name"], "Nia Patel")
        row = self.db.query(Broadcast).filter(
            Broadcast.message.like("Assembly%")).one()
        self.assertEqual(row.school_id, 1)
        self.assertEqual(row.sender_user_id, 201)

    def test_broadcast_feed_shows_the_whole_school_to_a_sub_admin(self):
        from services.communication_service.repository import list_broadcasts
        now = datetime(2026, 10, 20, 9, 0, 0)
        self.db.add_all([
            Broadcast(school_id=1, scope="school", role_name="Admin",
                      sender_name="Asha Rao", message="Holiday notice",
                      created_at=now),
            Broadcast(school_id=1, scope="pilot", role_name="Pilot",
                      sender_name="P. Ilot", message="Route change",
                      created_at=now),
            Broadcast(school_id=2, scope="school", role_name="Admin",
                      sender_name="Bilal Khan", message="Other school",
                      created_at=now),
        ])
        self.db.commit()

        sub = CurrentUser(user_id=201, role=SUBADMIN_ROLE, school_id=1,
                          linked_person_id=13)
        seen = list_broadcasts(self.db, 1, sub)
        self.assertEqual(sorted(b.message for b in seen),
                         ["Holiday notice", "Route change"])
        # A parent keeps the school-scope-only view, proving the branch
        # actually discriminates rather than returning everything to anyone.
        parent = CurrentUser(user_id=501, role="parent", school_id=1)
        parent_seen = list_broadcasts(self.db, 1, parent)
        self.assertEqual([b.message for b in parent_seen], ["Holiday notice"])

    def test_trip_operations_treat_the_sub_admin_like_a_school_admin(self):
        from services.transport_service.repository import _authorize_trip
        trip = SimpleNamespace(trip_id=1, school_id=1, pilot_id=None,
                               route_id=None)
        sub = CurrentUser(user_id=201, role=SUBADMIN_ROLE, school_id=1,
                          linked_person_id=13)
        _authorize_trip(self.db, trip, sub)  # school-admin tier: allowed
        teacher = CurrentUser(user_id=401, role="teacher", school_id=1)
        with self.assertRaises(ForbiddenError) as ctx:
            _authorize_trip(self.db, trip, teacher)
        self.assertEqual(str(ctx.exception),
                         "Role 'teacher' cannot operate this trip")
        pilot = CurrentUser(user_id=601, role="pilot", school_id=1)
        with self.assertRaises(ForbiddenError) as ctx:
            _authorize_trip(self.db, trip, pilot)
        self.assertEqual(str(ctx.exception),
                         "This trip is not assigned to you")

    def test_gallery_writes_follow_the_admin_tier(self):
        from services.media_service.repository import get_media_for_write
        self.db.add_all([
            Media(school_id=1, title="Science fair", posted_by="Asha Rao",
                  uploader_user_id=101, media_kind="image", file_url="/m/1"),
            Media(school_id=2, title="Other school", posted_by="Bilal Khan",
                  uploader_user_id=102, media_kind="image", file_url="/m/2"),
        ])
        self.db.commit()
        own = self.db.query(Media).filter(Media.school_id == 1).one()
        other = self.db.query(Media).filter(Media.school_id == 2).one()
        sub = CurrentUser(user_id=201, role=SUBADMIN_ROLE, school_id=1,
                          linked_person_id=13)

        # School-admin tier may edit a colleague's upload in-school...
        media = get_media_for_write(self.db, 1, own.media_id, sub)
        self.assertEqual(media.media_id, own.media_id)
        # ...cross-school stays "not found"...
        with self.assertRaises(NotFoundError):
            get_media_for_write(self.db, 1, other.media_id, sub)
        # ...and an ordinary staff session still only touches its own uploads.
        staff_viewer = CurrentUser(user_id=301, role="staff", school_id=1,
                                   linked_person_id=15)
        with self.assertRaises(ForbiddenError) as ctx:
            get_media_for_write(self.db, 1, own.media_id, staff_viewer)
        self.assertEqual(str(ctx.exception),
                         "You can only edit or remove media you uploaded")

    def test_email_resolution_uses_the_sub_admins_own_staff_record(self):
        # users.email missing: a Sub-Admin falls through to its linked staff
        # row (the staff-tuple branch), not to the school's primary address.
        staff14 = self.db.query(Staff).filter(Staff.staff_id == 14).one()
        staff14.email = "omar@schoolers.test"
        self.db.commit()
        sub_me = self.client.get("/auth/me", headers=self.sub2_headers)
        self.assertEqual(sub_me.status_code, 200, sub_me.text)
        self.assertEqual(sub_me.json()["email"], "omar@schoolers.test")
        self.assertEqual(sub_me.json()["display_name"], "Omar Siddiq")
        # An Admin account still resolves to its school's primary email —
        # the two roles deliberately take different branches.
        admin_me = self.client.get("/auth/me", headers=self.admin1_headers)
        self.assertEqual(admin_me.status_code, 200, admin_me.text)
        self.assertEqual(admin_me.json()["email"], "gv@example.com")

    def test_resolve_sender_identity_requires_a_live_staff_record(self):
        from services.communication_service.repository import (
            resolve_sender_identity)
        sub = CurrentUser(user_id=201, role=SUBADMIN_ROLE, school_id=1,
                          linked_person_id=13)
        self.assertEqual(resolve_sender_identity(self.db, sub),
                         ("Sub-Admin", "Nia Patel"))
        # An unlinked login cannot borrow a name at all.
        unlinked = CurrentUser(user_id=202, role=SUBADMIN_ROLE, school_id=2)
        with self.assertRaises(ForbiddenError) as ctx:
            resolve_sender_identity(self.db, unlinked)
        self.assertEqual(str(ctx.exception),
                         "This subadmin account isn't linked to a staff record")
        # The staff row must be live: a deactivated row is "not found".
        self.db.query(Staff).filter(Staff.staff_id == 13).update(
            {"is_active": False})
        self.db.commit()
        with self.assertRaises(ForbiddenError) as ctx:
            resolve_sender_identity(self.db, sub)
        self.assertEqual(str(ctx.exception), "Staff record not found")
        # The Admin identity branch is untouched.
        admin = CurrentUser(user_id=101, role="admin", school_id=1,
                            linked_person_id=11)
        self.assertEqual(resolve_sender_identity(self.db, admin),
                         ("Admin", "Asha Rao"))

    def test_school_admin_login_sync_never_touches_a_sub_admin_login(self):
        from services.schools_service.repository import _sync_admin_user
        school = self.db.query(School).filter(School.school_id == 1).one()
        sub_before = self.db.query(User).filter(User.user_id == 201).one()
        snapshot = (sub_before.username, sub_before.password_hash,
                    sub_before.role)

        # Active admin present: it is the login that gets synced/renamed.
        self.assertIsNone(_sync_admin_user(self.db, school, "Vera", "Cross"))
        admin1 = self.db.query(User).filter(User.user_id == 101).one()
        self.assertEqual(admin1.role, "admin")
        self.assertNotEqual(admin1.username, "admin1")
        sub_mid = self.db.query(User).filter(User.user_id == 201).one()
        self.assertEqual((sub_mid.username, sub_mid.password_hash,
                          sub_mid.role), snapshot)

        # No active admin: a fresh one is provisioned rather than adopting
        # the Sub-Admin login (the query pins role == "admin" for this).
        admin1.is_active = False
        self.db.commit()
        creds = _sync_admin_user(self.db, school, "Vera", "Cross")
        self.assertIsNotNone(creds)
        self.assertIn("admin_username", creds)
        self.assertIn("admin_password", creds)
        fresh = self.db.query(User).filter(
            User.username == creds["admin_username"]).one()
        self.assertEqual(fresh.role, "admin")
        self.assertTrue(fresh.is_active)
        self.assertEqual(fresh.school_id, 1)
        sub_after = self.db.query(User).filter(User.user_id == 201).one()
        self.assertEqual((sub_after.username, sub_after.password_hash,
                          sub_after.role), snapshot)
        other_school_sub = self.db.query(User).filter(User.user_id == 202).one()
        self.assertEqual(other_school_sub.role, "subadmin")



class SubAdminSchemaContractTests(unittest.TestCase):
    """Source-level contracts: migrations, model CHECKs, enum, matrix."""

    ROOT = Path(__file__).resolve().parents[1]

    def test_migration_widens_both_constraints_idempotently_and_safely(self):
        sql = (self.ROOT / "db-migration-subadmin-role.sql").read_text(encoding="utf-8")
        for constraint in ("users_role_check", "support_ticket_messages_role_check"):
            with self.subTest(constraint=constraint):
                self.assertIn(constraint, sql)
                self.assertRegex(
                    sql,
                    rf"{constraint}[\s\S]*?CHECK \([^\)]*subadmin[^\)]*\)")
                self.assertIn("LIKE '%subadmin%'", sql)
                self.assertIn("'schoolers.users'::regclass", sql)
                self.assertIn("'schoolers.support_ticket_messages'::regclass", sql)
        # Rebuilt lists keep every existing role, so running this file before
        # or after the submaster migration can never drop one.
        self.assertIn("'submaster','subadmin'", sql)
        self.assertIn("'parent','teacher','admin','pilot','master','staff'", sql)
        self.assertIn("idempotent", sql.lower())

    def test_db_migrations_and_model_agree_on_author_roles(self):
        migrations = (self.ROOT / "db-migrations.sql").read_text(encoding="utf-8")
        self.assertIn(
            "CHECK (author_role IN ('admin','master','submaster','subadmin'))",
            migrations)

    def test_users_model_carries_no_role_check_that_could_narrow_the_role(self):
        users = Base.metadata.tables["users"]
        role_checks = [
            constraint for constraint in users.constraints
            if isinstance(constraint, CheckConstraint) and "role" in str(constraint)
        ]
        self.assertEqual(role_checks, [])

    def test_role_enum_and_label_literal_fit_the_column(self):
        self.assertEqual(Role.subadmin.value, "subadmin")
        self.assertEqual(len(Role.subadmin.value), 8)  # users.role is String(10)

    def test_permissions_matrix_subadmin_mirrors_admin_exactly(self):
        for module, actions in PERMISSIONS.items():
            for action, roles in actions.items():
                with self.subTest(module=module, action=action):
                    self.assertEqual("subadmin" in roles, "admin" in roles,
                                     "Sub-Admin must mirror Admin in PERMISSIONS")
                    self.assertEqual("submaster" in roles, "master" in roles,
                                     "Sub-Master must mirror Master in PERMISSIONS")

    def test_broadcast_scopes_subadmin_mirrors_admin(self):
        from services.communication_service.repository import ALLOWED_BROADCAST_SCOPES
        self.assertEqual(
            ALLOWED_BROADCAST_SCOPES["subadmin"],
            ALLOWED_BROADCAST_SCOPES["admin"])

    def test_seed_reset_keeps_only_the_master_tier(self):
        # Sub-Admin is school data like Admin: reset wipes it, and only
        # master/submaster rows survive — unchanged policy, new role excluded.
        reset = (self.ROOT / "seed" / "reset.py").read_text(encoding="utf-8")
        self.assertRegex(
            reset,
            r"KEEP_MASTER_USERS = \"role IN \('master', 'submaster'\)\"")

    def test_no_service_decides_authorization_by_inline_admin_string(self):
        # Every remaining `role == "admin"` the API runs is an identity
        # concern (which email to show; which login is THE school admin),
        # never an access decision — authorization goes through authz helpers.
        # This scan fails if anyone reintroduces an inline auth check, which
        # is exactly how the pre-Sub-Admin code denied this tier silently.
        hits: dict[str, list[int]] = {}
        pattern = re.compile(r"""role\s*[!=]=\s*['"]admin['"]""")
        for base in ("services", "common"):
            for path in (self.ROOT / base).rglob("*.py"):
                for lineno, line in enumerate(
                        path.read_text(encoding="utf-8").splitlines(), 1):
                    if pattern.search(line):
                        hits.setdefault(
                            str(path.relative_to(self.ROOT)), []).append(lineno)
        self.assertEqual(
            {name: len(lines) for name, lines in hits.items()},
            {
                # account_email_address / user_email_address: "Admin" resolves
                # to the school's primary email; both then handle the
                # ("teacher","staff","pilot","subadmin") staff tuple instead.
                "services/auth_service/service.py": 2,
                # _sync_admin_user: locates THE school admin login to
                # provision/rename it — deliberately must not match subadmin.
                "services/schools_service/repository.py": 1,
            },
        )
        auth = (self.ROOT / "services/auth_service/service.py").read_text(
            encoding="utf-8")
        self.assertEqual(
            auth.count('user.role in ("teacher", "staff", "pilot", "subadmin")'),
            2)

    def test_docs_and_shipped_files_describe_the_role(self):
        migration = self.ROOT / "db-migration-subadmin-role.sql"
        self.assertTrue(migration.exists())
        docs = (self.ROOT / "FASTAPI_MODULES.md").read_text(encoding="utf-8")
        self.assertIn("Sub-Admin", docs)
        self.assertIn("assert_can_assign_subadmin", docs)


if __name__ == "__main__":
    unittest.main()
