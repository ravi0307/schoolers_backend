import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import services.auth_service.service as auth_service
from services.auth_service.service import (
    OTP_EXPIRE_SECONDS, _consume_otp, _store_otp, user_email_address,
)


class FakeQuery:
    def __init__(self, result):
        self._result = result

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self._result


class FakeDB:
    def __init__(self, results):
        self._results = results

    def query(self, model):
        return FakeQuery(self._results.get(model))


class OTPStoreTests(unittest.TestCase):
    def setUp(self):
        auth_service._otp_store.clear()

    def test_store_otp_returns_a_six_digit_numeric_code(self):
        self.assertRegex(_store_otp(123), r"^[0-9]{6}$")

    def test_consume_otp_accepts_the_stored_code_exactly_once(self):
        otp = _store_otp(123)
        self.assertTrue(_consume_otp(123, otp))
        self.assertFalse(_consume_otp(123, otp))

    def test_consume_otp_rejects_a_wrong_code_and_clears_the_entry(self):
        _store_otp(1)
        self.assertFalse(_consume_otp(1, "000000"))
        self.assertFalse(_consume_otp(1, "000000"))

    def test_a_fresh_code_overwrites_any_previous_code(self):
        _store_otp(1)
        second = _store_otp(1)
        self.assertTrue(_consume_otp(1, second))

    def test_consume_otp_with_no_entry_returns_false(self):
        self.assertFalse(_consume_otp(999_999, "123456"))

    def test_consume_otp_rejects_an_expired_code(self):
        with mock.patch(
            "services.auth_service.service.time.monotonic",
            side_effect=[100.0, 100.0 + OTP_EXPIRE_SECONDS + 1],
        ):
            otp = _store_otp(1)
            self.assertFalse(_consume_otp(1, otp))

    def test_otps_are_kept_separate_per_user(self):
        otp_a = _store_otp(1)
        otp_b = _store_otp(2)
        self.assertTrue(_consume_otp(1, otp_a))
        self.assertTrue(_consume_otp(2, otp_b))


class UserEmailAddressTests(unittest.TestCase):
    def test_admin_uses_the_school_primary_email(self):
        db = FakeDB({auth_service.School: SimpleNamespace(primary_email="school@example.com")})
        user = SimpleNamespace(role="admin", school_id=1)
        self.assertEqual(user_email_address(db, user), "school@example.com")

    def test_admin_without_a_school_record_has_no_email(self):
        db = FakeDB({auth_service.School: None})
        user = SimpleNamespace(role="admin", school_id=1)
        self.assertIsNone(user_email_address(db, user))

    def test_teacher_staff_and_parent_use_their_person_email(self):
        for role, model in (
            ("teacher", auth_service.Teacher),
            ("staff", auth_service.Staff),
            ("parent", auth_service.Parent),
        ):
            with self.subTest(role=role):
                db = FakeDB({model: SimpleNamespace(email=f"{role}@example.com")})
                user = SimpleNamespace(role=role, linked_person_id=7)
                self.assertEqual(user_email_address(db, user), f"{role}@example.com")

    def test_pilot_uses_the_pilot_email(self):
        db = FakeDB({auth_service.Pilot: SimpleNamespace(email="pilot@example.com")})
        user = SimpleNamespace(role="pilot", user_id=5)
        self.assertEqual(user_email_address(db, user), "pilot@example.com")

    def test_master_and_unknown_roles_have_no_email(self):
        db = FakeDB({})
        self.assertIsNone(user_email_address(db, SimpleNamespace(role="master", user_id=1)))
        self.assertIsNone(user_email_address(db, SimpleNamespace(role="other", user_id=1)))

    def test_missing_person_record_has_no_email(self):
        user = SimpleNamespace(role="teacher", linked_person_id=9)
        self.assertIsNone(user_email_address(FakeDB({auth_service.Teacher: None}), user))


if __name__ == "__main__":
    unittest.main()