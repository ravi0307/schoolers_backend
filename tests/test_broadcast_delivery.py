"""
Functional tests for route-scoped broadcast delivery.

Runs against an in-memory SQLite database (subset of the shared schema), so it
works anywhere without a live Postgres. Verifies who may *create* a route
broadcast and which roles can *see* which broadcasts after one is sent.
"""
import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from common.dependencies import CurrentUser
from common.exceptions import NotFoundError
from common.models import (
    Base,
    Broadcast,
    Parent,
    ParentStudent,
    Route,
    RouteStudent,
    SchoolClass,
    Staff,
    Student,
    Subject,
    TeacherClassSubject,
)
import services.communication_service.repository as repo
from services.communication_service.schemas import BroadcastCreate, BroadcastRead

TABLES = [
    "staff",
    "subjects",
    "classes",
    "teacher_class_subjects",
    "students",
    "parents",
    "parent_student",
    "routes",
    "route_students",
    "broadcasts",
]


class BroadcastDeliveryTests(unittest.TestCase):
    SCHEDULED = datetime(2026, 9, 1, 9, 0, 0)

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
        now = self.SCHEDULED

        school_1 = 1

        staff = Staff(staff_id=1, school_id=school_1, name="Staff", role="Admin")
        teacher = Staff(staff_id=2, school_id=school_1, name="T. Eacher", role="Teacher",
                        person_type="teacher", role_title="Teacher", phone="000")
        self.db.add_all([staff, teacher])

        self.subject_1 = Subject(subject_id=1, school_id=school_1, name="Maths")
        self.db.add(self.subject_1)

        self.cls_1 = SchoolClass(class_id=1, school_id=school_1, name="Class 1")
        self.cls_75 = SchoolClass(class_id=75, school_id=school_1, name="Class 75")
        self.db.add_all([self.cls_1, self.cls_75])

        # Teacher 1 teaches a subject in Class 1 only.
        self.db.add(TeacherClassSubject(staff_id=1, class_id=1, subject_id=1, is_class_teacher=True))

        student_7 = Student(student_id=7, school_id=school_1, class_id=1, admission_no="A7", name="Tara Dhaliwal")
        student_446 = Student(student_id=446, school_id=school_1, class_id=75, admission_no="A446", name="Other Kid")
        self.db.add_all([student_7, student_446])

        parent_225 = Parent(parent_id=225, school_id=school_1, name="Ansh Purohit", phone="000")
        parent_168 = Parent(parent_id=168, school_id=school_1, name="Other Parent", phone="000")
        self.db.add_all([parent_225, parent_168])

        self.db.add_all([
            ParentStudent(parent_id=225, student_id=7),
            ParentStudent(parent_id=168, student_id=446),
        ])

        self.route_1 = Route(route_id=1, school_id=school_1, name="Route 1", vehicle="Bus A")
        self.route_51 = Route(route_id=51, school_id=school_1, name="Route 51", vehicle="Bus B")
        self.db.add_all([self.route_1, self.route_51])

        self.db.add_all([
            RouteStudent(route_id=1, student_id=7, status="pending"),
            RouteStudent(route_id=51, student_id=446, status="picked"),
        ])

        self.school_b = Broadcast(school_id=1, scope="school", role_name="Admin", sender_name="V. Joshi", message="School-wide", created_at=now)
        self.class_1_b = Broadcast(school_id=1, class_id=1, scope="class", role_name="Teacher", sender_name="T. Eacher", message="Class 1 note", created_at=now)
        self.class_75_b = Broadcast(school_id=1, class_id=75, scope="class", role_name="Teacher", sender_name="T. Eacher", message="Class 75 note", created_at=now)
        self.route_1_b = Broadcast(school_id=1, route_id=1, scope="route", role_name="Pilot", sender_name="P. One", message="Route 1 delay", created_at=now)
        self.route_51_b = Broadcast(school_id=1, route_id=51, scope="route", role_name="Pilot", sender_name="P. Two", message="Route 51 delay", created_at=now)
        self.pilot_b = Broadcast(school_id=1, scope="pilot", role_name="Admin", sender_name="V. Joshi", message="Pilot notice", created_at=now)
        self.db.add_all([
            self.school_b,
            self.class_1_b,
            self.class_75_b,
            self.route_1_b,
            self.route_51_b,
            self.pilot_b,
        ])
        self.db.flush()

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def _list(self, role, user_id=1, linked_person_id=None):
        current = CurrentUser(user_id=user_id, role=role, school_id=1, linked_person_id=linked_person_id)
        fetched = repo.list_broadcasts(self.db, school_id=1, current_user=current)
        return {b.message for b in fetched}

    def test_admin_sees_every_broadcast_in_school(self):
        self.assertEqual(
            self._list("admin"),
            {"School-wide", "Class 1 note", "Class 75 note", "Route 1 delay", "Route 51 delay", "Pilot notice"},
        )

    def test_teacher_sees_school_class_and_route_broadcasts(self):
        seen = self._list("teacher", linked_person_id=1)
        self.assertIn("School-wide", seen)
        self.assertIn("Class 1 note", seen)
        self.assertIn("Route 1 delay", seen)
        self.assertNotIn("Class 75 note", seen)  # teacher 1 does not teach class 75
        self.assertNotIn("Route 51 delay", seen)  # no taught class rides route 51
        self.assertNotIn("Pilot notice", seen)

    def test_unlinked_teacher_sees_only_school_wide(self):
        seen = self._list("teacher")
        self.assertEqual(seen, {"School-wide"})

    def test_pilot_sees_school_route_and_pilot_broadcasts(self):
        seen = self._list("pilot")
        self.assertIn("School-wide", seen)
        self.assertIn("Route 1 delay", seen)
        self.assertIn("Pilot notice", seen)
        self.assertNotIn("Class 1 note", seen)

    def test_route_parent_sees_only_their_own_route_broadcast(self):
        seen = self._list("parent", user_id=3, linked_person_id=225)
        self.assertIn("School-wide", seen)
        self.assertIn("Class 1 note", seen)   # child 7 is in class 1
        self.assertIn("Route 1 delay", seen)  # child 7 rides route 1
        self.assertNotIn("Class 75 note", seen)
        self.assertNotIn("Route 51 delay", seen)
        self.assertNotIn("Pilot notice", seen)

    def test_other_parent_does_not_see_unrelated_route_broadcast(self):
        seen = self._list("parent", user_id=4, linked_person_id=168)
        self.assertIn("School-wide", seen)
        self.assertIn("Class 75 note", seen)
        self.assertIn("Route 51 delay", seen)
        self.assertNotIn("Route 1 delay", seen)
        self.assertNotIn("Class 1 note", seen)
        self.assertNotIn("Pilot notice", seen)

    def test_parent_with_no_children_sees_only_school_wide(self):
        seen = self._list("parent", user_id=5, linked_person_id=999)
        self.assertEqual(seen, {"School-wide"})

    def test_create_route_broadcast_validates_route_belongs_to_school(self):
        created = repo.create_broadcast(
            self.db,
            school_id=1,
            data={
                "scope": "route",
                "route_id": 1,
                "role_name": "Pilot",
                "sender_name": "P. One",
                "message": "New delay",
                "created_at": datetime(2026, 9, 2, 9, 0, 0),
            },
        )
        self.assertEqual(created.scope, "route")
        self.assertEqual(created.route_id, 1)

        with self.assertRaises(NotFoundError):
            repo.create_broadcast(
                self.db,
                school_id=1,
                data={
                    "scope": "route",
                    "route_id": 999,
                    "role_name": "Pilot",
                    "sender_name": "P. One",
                    "message": "Phantom route",
                },
            )

    def test_broadcast_create_schema_requires_route_id_for_route_scope(self):
        with self.assertRaises(Exception):
            BroadcastCreate(scope="route", role_name="Pilot", sender_name="P. One", message="No target")
        with self.assertRaises(Exception):
            BroadcastCreate(scope="school", route_id=1, role_name="Pilot", sender_name="P. One", message="Mixed")
        valid = BroadcastCreate(scope="route", route_id=1, role_name="Pilot", sender_name="P. One", message="Ok")
        self.assertEqual(valid.route_id, 1)

    def test_broadcast_create_schema_requires_class_id_for_class_scope(self):
        with self.assertRaises(Exception):
            BroadcastCreate(scope="class", role_name="Teacher", sender_name="T. Eacher", message="No target")
        with self.assertRaises(Exception):
            BroadcastCreate(scope="school", class_id=1, role_name="Teacher", sender_name="T. Eacher", message="Mixed")
        valid = BroadcastCreate(scope="class", class_id=1, role_name="Teacher", sender_name="T. Eacher", message="Ok")
        self.assertEqual(valid.class_id, 1)

    def test_teacher_creates_school_and_class_broadcasts(self):
        school_one = repo.create_broadcast(
            self.db,
            school_id=1,
            data={
                "scope": "school",
                "role_name": "Teacher",
                "sender_name": "T. Eacher",
                "message": "Campus notice",
                "created_at": datetime(2026, 9, 2, 9, 0, 0),
            },
        )
        self.assertEqual(school_one.scope, "school")
        self.assertIsNone(school_one.class_id)
        self.assertIsNone(school_one.route_id)
        self.assertEqual(school_one.role_name, "Teacher")
        self.assertEqual(school_one.sender_name, "T. Eacher")
        self.assertTrue(school_one.is_active)

        class_one = repo.create_broadcast(
            self.db,
            school_id=1,
            data={
                "scope": "class",
                "class_id": 1,
                "role_name": "Teacher",
                "sender_name": "T. Eacher",
                "message": "Homework due Friday",
                "created_at": datetime(2026, 9, 2, 10, 0, 0),
            },
        )
        self.assertEqual(class_one.scope, "class")
        self.assertEqual(class_one.class_id, 1)

    def test_teacher_class_broadcast_reaches_class_parents_and_teacher_only(self):
        repo.create_broadcast(
            self.db,
            school_id=1,
            data={
                "scope": "class",
                "class_id": 1,
                "role_name": "Teacher",
                "sender_name": "T. Eacher",
                "message": "Class 1 field trip reminder",
                "created_at": datetime(2026, 9, 2, 9, 0, 0),
            },
        )

        teacher_seen = self._list("teacher", linked_person_id=1)
        self.assertIn("Class 1 field trip reminder", teacher_seen)

        route_parent_seen = self._list("parent", user_id=3, linked_person_id=225)
        self.assertIn("Class 1 field trip reminder", route_parent_seen)

        other_parent_seen = self._list("parent", user_id=4, linked_person_id=168)
        self.assertNotIn("Class 1 field trip reminder", other_parent_seen)

        pilot_seen = self._list("pilot")
        self.assertNotIn("Class 1 field trip reminder", pilot_seen)

    def test_teacher_school_broadcast_reaches_every_role(self):
        repo.create_broadcast(
            self.db,
            school_id=1,
            data={
                "scope": "school",
                "role_name": "Teacher",
                "sender_name": "T. Eacher",
                "message": "Sports day assembly",
                "created_at": datetime(2026, 9, 2, 9, 0, 0),
            },
        )

        for role, user_id, linked_person_id in [
            ("admin", 1, None),
            ("teacher", 1, 1),
            ("pilot", 1, None),
            ("parent", 3, 225),
            ("parent", 4, 168),
            ("parent", 5, 999),
        ]:
            seen = self._list(role, user_id=user_id, linked_person_id=linked_person_id)
            self.assertIn("Sports day assembly", seen)


class BroadcastAuthorshipTests(unittest.TestCase):
    """Who wrote a broadcast, as a user id rather than a name.

    The admin and teacher broadcast pages split their history into Posted and
    Received. They used to do that by comparing the sender's display name against
    the signed-in user's, which filed *every* message under Received whenever the
    two strings differed -- and the server derives sender_name from the Staff
    record (falling back to the literal "Admin"), while the client compared its
    own account name. Two admins both post as "Admin", so the label cannot
    separate them at all.
    """

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
        self.db.add(
            Staff(staff_id=2, school_id=1, name="T. Eacher", role="Teacher",
                  person_type="teacher", role_title="Teacher", phone="000")
        )
        self.db.add(
            Staff(staff_id=3, school_id=1, name="T. Eacher", role="Teacher",
                  person_type="teacher", role_title="Teacher", phone="000")
        )
        self.db.commit()

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    # created_at is passed explicitly throughout: its server default calls
    # timezone(), which SQLite does not have, and these tests run on SQLite.
    CREATED_AT = datetime(2026, 9, 5, 9, 0, 0)

    def _create(self, sender_user_id, message="Notice"):
        return repo.create_broadcast(
            self.db,
            school_id=1,
            data={
                "scope": "school",
                # Both admins post under the same label. Only the id tells them
                # apart, which is the whole point of storing it.
                "role_name": "Admin",
                "sender_name": "Admin",
                "message": message,
                "created_at": self.CREATED_AT,
            },
            sender_user_id=sender_user_id,
        )

    def test_a_broadcast_records_the_author_it_was_given(self):
        row = self._create(322, "Mine")
        self.assertEqual(row.sender_user_id, 322)
        self.db.expire_all()
        self.assertEqual(self.db.get(Broadcast, row.broadcast_id).sender_user_id, 322)

    def test_two_authors_are_distinguishable_though_their_names_match(self):
        mine = self._create(322, "Mine")
        theirs = self._create(323, "Theirs")
        self.assertNotEqual(mine.sender_user_id, theirs.sender_user_id)
        # The label is identical, so a client reading only sender_name cannot
        # tell these two apart -- which is what broke the split.
        self.assertEqual(mine.sender_name, theirs.sender_name)

    def test_authorship_is_optional_so_older_and_seeded_rows_still_write(self):
        row = repo.create_broadcast(
            self.db,
            school_id=1,
            data={"scope": "school", "role_name": "Admin", "sender_name": "Admin",
                  "message": "Seed row", "created_at": self.CREATED_AT},
        )
        self.assertIsNone(row.sender_user_id)

    def test_the_column_does_not_leak_into_the_client_payload(self):
        # The client must not be able to claim authorship of someone else's
        # message by sending the field itself.
        payload = BroadcastCreate(scope="school", message="Forged")
        self.assertNotIn("sender_user_id", payload.model_dump())
        row = repo.create_broadcast(
            self.db,
            school_id=1,
            data={**payload.model_dump(), "role_name": "Admin", "sender_name": "Admin",
                  "created_at": self.CREATED_AT},
            sender_user_id=322,
        )
        self.assertEqual(row.sender_user_id, 322, "the id comes from the session, not the payload")

    def test_the_read_schema_carries_the_author(self):
        self.assertIn("sender_user_id", BroadcastRead.model_fields)
        # Nullable, so a row without an author still serialises rather than 500ing
        # a client that is asking for its whole history.
        row = self._create(322, "Mine")
        self.assertEqual(BroadcastRead.model_validate(row).sender_user_id, 322)
        row.sender_user_id = None
        self.assertIsNone(BroadcastRead.model_validate(row).sender_user_id)


if __name__ == "__main__":
    unittest.main()