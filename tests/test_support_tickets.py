"""
Support ticket behaviour: opening, scoping, the message trail, and status.

These exercise the repository directly (the service has no HTTP harness in the
suite), which is where the rules actually live: a ticket belongs to one school,
an admin only ever reads their own school's, the master reads every school, and
the conversation and status are two separate concerns.
"""
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from common.dependencies import CurrentUser
from common.models import Base, School, Staff, SupportTicket, SupportTicketMessage, User
import services.support_service.repository as repo

TABLES = [
    "schools", "users", "staff", "support_tickets", "support_ticket_messages",
]


def seed(db: Session):
    db.add_all(
        [
            School(
                school_id=1, name="Green Valley", address="1 Road", pincode="560001",
                city="Bengaluru", state="KA", primary_contact="9000000001",
                primary_email="gv@example.com",
            ),
            School(
                school_id=2, name="Blue Ridge", address="2 Road", pincode="560002",
                city="Bengaluru", state="KA", primary_contact="9000000002",
                primary_email="br@example.com",
            ),
            Staff(staff_id=11, school_id=1, name="Asha Rao", role="Admin"),
            Staff(staff_id=12, school_id=2, name="Bilal Khan", role="Admin"),
            User(user_id=101, school_id=1, role="admin", username="admin1",
                 password_hash="x", linked_person_id=11),
            User(user_id=102, school_id=2, role="admin", username="admin2",
                 password_hash="x", linked_person_id=12),
            User(user_id=1, school_id=None, role="master", username="master",
                 password_hash="x"),
        ]
    )
    db.commit()


class SupportTicketTests(unittest.TestCase):
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
        seed(self.db)
        self.admin1 = CurrentUser(user_id=101, role="admin", school_id=1, linked_person_id=11)
        self.admin2 = CurrentUser(user_id=102, role="admin", school_id=2, linked_person_id=12)
        self.master = CurrentUser(user_id=1, role="master", school_id=None)

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def open_ticket(self, user=None, subject="Portal is slow", body="It hangs on login."):
        return repo.create_ticket(
            self.db,
            user or self.admin1,
            {"subject": subject, "body": body, "attachments": ["/api/v1/schools/uploads/a.pdf"]},
        )

    def test_opening_a_ticket_records_the_first_message(self):
        ticket = self.open_ticket()
        self.assertEqual(ticket.status, "Open")
        self.assertEqual(ticket.school_id, 1)
        # The display name is the linked staff name, not the username.
        self.assertEqual(ticket.created_by_name, "Asha Rao")

        messages = (
            self.db.query(SupportTicketMessage)
            .filter(SupportTicketMessage.ticket_id == ticket.ticket_id)
            .all()
        )
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].author_role, "admin")
        self.assertEqual(messages[0].author_name, "Asha Rao")
        self.assertEqual(messages[0].attachments, ["/api/v1/schools/uploads/a.pdf"])

    def test_an_admin_only_lists_their_own_school(self):
        self.open_ticket(self.admin1, subject="Mine")
        self.open_ticket(self.admin2, subject="Theirs")

        mine = repo.list_tickets(self.db, school_id=self.admin1.school_id)
        self.assertEqual([t["subject"] for t in mine], ["Mine"])
        self.assertEqual(mine[0]["school_name"], "Green Valley")
        self.assertEqual(mine[0]["message_count"], 1)

    def test_the_master_lists_every_school(self):
        self.open_ticket(self.admin1, subject="Mine")
        self.open_ticket(self.admin2, subject="Theirs")

        all_tickets = repo.list_tickets(self.db)
        self.assertEqual({t["subject"] for t in all_tickets}, {"Mine", "Theirs"})
        # The school name travels with the row so the master inbox stays readable.
        self.assertEqual({t["school_name"] for t in all_tickets}, {"Green Valley", "Blue Ridge"})

    def test_status_filters_the_list(self):
        ticket = self.open_ticket()
        repo.set_status(self.db, ticket, "In progress")

        self.assertEqual(len(repo.list_tickets(self.db, status="In progress")), 1)
        self.assertEqual(repo.list_tickets(self.db, status="Completed"), [])

    def test_can_view_is_school_scoped_for_admins_and_open_for_master(self):
        ticket = self.open_ticket(self.admin1)
        self.assertTrue(repo.can_view(self.admin1, ticket))
        self.assertFalse(repo.can_view(self.admin2, ticket))
        self.assertTrue(repo.can_view(self.master, ticket))

    def test_replies_keep_both_sides_in_order(self):
        ticket = self.open_ticket(self.admin1, body="First")
        repo.add_message(self.db, ticket, self.master, {"body": "Looking into it", "attachments": []})
        repo.add_message(self.db, ticket, self.admin1, {"body": "Any update?", "attachments": []})

        detail = repo.ticket_detail(self.db, ticket)
        self.assertEqual([m["body"] for m in detail["messages"]], ["First", "Looking into it", "Any update?"])
        self.assertEqual([m["author_role"] for m in detail["messages"]], ["admin", "master", "admin"])
        self.assertEqual(detail["message_count"], 3)
        # The master has no linked staff row, so the username is the fallback.
        self.assertEqual(detail["messages"][1]["author_name"], "master")

    def test_set_status_moves_the_ticket(self):
        ticket = self.open_ticket()
        repo.set_status(self.db, ticket, "Completed")
        self.assertEqual(repo.get_ticket(self.db, ticket.ticket_id).status, "Completed")

    def test_a_missing_ticket_is_none(self):
        self.assertIsNone(repo.get_ticket(self.db, 4242))


if __name__ == "__main__":
    unittest.main()
