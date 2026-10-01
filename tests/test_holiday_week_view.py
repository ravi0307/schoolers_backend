"""
Named-holiday CRUD and weekly timetable view tests.

The model is a named holiday on a specific calendar date (``occasion`` +
``holiday_date``), resolved against a recurring weekday timetable.

Two defects are pinned here:

1. ``GET /timetable/class/{id}`` was not scoped to the caller's school, so any
   authenticated parent/teacher could read another school's class timetable.
2. A holiday must be matched by *date*, not by weekday. Matching by weekday
   would light up every Saturday forever, which is the recurring-template bug
   this feature was rewritten to avoid.

3. A holiday may be entered as a single day or as an inclusive span of days.
   The span is stored one row per day, so a multi-day break reaches the timetable
   without the timetable knowing anything about ranges.

Also covers the week arithmetic both services share, since a mismatch between
the API's Monday and the browser's Monday would highlight the wrong column.
"""
import unittest
from datetime import date, datetime, time, timedelta

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from common.models import Base, Holiday, Period, SchoolClass, Subject, TimetableEntry
from common.holidays import holidays_in_range, list_holidays
from common.week import DAY_ABBREVIATIONS, monday_of, week_dates
from common.exceptions import AppError, ConflictError
from services.timetable_service.schemas import TimetableEntryUpdate
import services.timetable_service.repository as timetable_repo
import services.academics_service.repository as academics_repo

TABLES = [
    "subjects",
    "timetable_entries",
    "periods",
    "classes",
    "holidays",
]

# A Monday and a Sunday inside the week the tests anchor on.
WEEK_START = date(2026, 9, 21)
WEEK_END = date(2026, 9, 27)


def _sqlite_helpers(dbapi_connection, connection_record):
    # timetable_entries.created_on uses text("timezone('Asia/Kolkata', now())"),
    # a Postgres expression; mimic it so inserts work on the in-memory DB.
    dbapi_connection.create_function("now", 0, lambda: datetime.now().isoformat())
    dbapi_connection.create_function("timezone", 2, lambda zone, ts: ts)


class WeekArithmeticTests(unittest.TestCase):
    def test_monday_of_snaps_backwards_to_monday(self):
        self.assertEqual(monday_of(date(2026, 9, 21)), date(2026, 9, 21))  # Mon
        self.assertEqual(monday_of(date(2026, 9, 23)), date(2026, 9, 21))  # Wed
        self.assertEqual(monday_of(date(2026, 9, 27)), date(2026, 9, 21))  # Sun

    def test_week_dates_always_returns_monday_first_seven_days(self):
        days = week_dates(date(2026, 9, 23))
        self.assertEqual([d for d, _ in days], list(DAY_ABBREVIATIONS))
        self.assertEqual(days[0][1], WEEK_START)
        self.assertEqual(days[-1][1], WEEK_END)
        # Contiguous, no gaps or duplicates.
        for i in range(1, 7):
            self.assertEqual(days[i][1] - days[i - 1][1], timedelta(days=1))

    def test_week_dates_accepts_any_day_inside_the_target_week(self):
        from_wednesday = week_dates(date(2026, 9, 23))
        from_sunday = week_dates(date(2026, 9, 27))
        self.assertEqual(from_wednesday, from_sunday)


class _InMemoryDb:
    """Shared in-memory fixtures.

    Deliberately not a ``TestCase``: the two suites below need the same tables
    and seed rows, and subclassing a real TestCase would re-run its tests in
    every child.
    """

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite://")
        event.listen(cls.engine, "connect", _sqlite_helpers)
        Base.metadata.create_all(
            cls.engine,
            tables=[Base.metadata.tables[name] for name in TABLES],
        )
        cls.Session = sessionmaker(bind=cls.engine, autoflush=False, future=True)

    def setUp(self):
        self.db: Session = self.Session()
        for name in TABLES:
            self.db.execute(Base.metadata.tables[name].delete())
        self.db.add_all(
            [
                SchoolClass(class_id=1, school_id=1, name="Class 1"),
                SchoolClass(class_id=2, school_id=2, name="Class 2"),
            ]
        )
        self.db.commit()

    def tearDown(self):
        self.db.rollback()
        self.db.close()


class HolidayCrudTests(_InMemoryDb, unittest.TestCase):
    def test_new_school_starts_with_no_holidays(self):
        # Holidays are dated rows an admin adds, so an empty list is correct
        # and GET must not invent placeholder rows.
        self.assertEqual(academics_repo.list_holidays(self.db, 1), [])

    def test_create_holiday_stores_occasion_and_date(self):
        holiday = academics_repo.create_holiday(
            self.db, 1, "Diwali", date(2026, 11, 8)
        )
        self.assertIsNotNone(holiday.holiday_id)
        self.assertEqual(holiday.school_id, 1)
        self.assertEqual(holiday.occasion, "Diwali")
        self.assertEqual(holiday.holiday_date, date(2026, 11, 8))

    def test_list_holidays_is_ordered_by_date(self):
        academics_repo.create_holiday(self.db, 1, "Holi", date(2026, 3, 4))
        academics_repo.create_holiday(self.db, 1, "Diwali", date(2026, 11, 8))
        academics_repo.create_holiday(self.db, 1, "New Year", date(2026, 1, 1))
        rows = academics_repo.list_holidays(self.db, 1)
        self.assertEqual(
            [r.occasion for r in rows], ["New Year", "Holi", "Diwali"]
        )

    def test_list_holidays_is_scoped_to_one_school(self):
        academics_repo.create_holiday(self.db, 1, "Diwali", date(2026, 11, 8))
        academics_repo.create_holiday(self.db, 2, "Thanksgiving", date(2026, 11, 26))
        self.assertEqual(
            [r.occasion for r in academics_repo.list_holidays(self.db, 1)], ["Diwali"]
        )

    def test_duplicate_date_in_the_same_school_conflicts(self):
        academics_repo.create_holiday(self.db, 1, "Diwali", date(2026, 11, 8))
        with self.assertRaises(ConflictError):
            academics_repo.create_holiday(self.db, 1, "Diwali Holiday", date(2026, 11, 8))

    def test_the_same_date_in_two_schools_is_allowed(self):
        academics_repo.create_holiday(self.db, 1, "Diwali", date(2026, 11, 8))
        academics_repo.create_holiday(self.db, 2, "Festival", date(2026, 11, 8))
        self.assertEqual(academics_repo.list_holidays(self.db, 1)[0].occasion, "Diwali")
        self.assertEqual(academics_repo.list_holidays(self.db, 2)[0].occasion, "Festival")

    def test_get_holiday_is_scoped_to_the_school(self):
        holiday = academics_repo.create_holiday(
            self.db, 1, "Diwali", date(2026, 11, 8)
        )
        self.assertIsNotNone(academics_repo.get_holiday(self.db, 1, holiday.holiday_id))
        # Another school must not be able to address it.
        self.assertIsNone(academics_repo.get_holiday(self.db, 2, holiday.holiday_id))

    def test_update_holiday_changes_occasion_and_date(self):
        holiday = academics_repo.create_holiday(
            self.db, 1, "Diwali", date(2026, 11, 8)
        )
        updated = academics_repo.update_holiday(
            self.db, holiday, "Diwali Holiday", date(2026, 11, 9)
        )
        self.assertEqual(updated.holiday_id, holiday.holiday_id)
        self.assertEqual(updated.occasion, "Diwali Holiday")
        self.assertEqual(updated.holiday_date, date(2026, 11, 9))
        self.assertEqual(len(academics_repo.list_holidays(self.db, 1)), 1)

    def test_update_holiday_leaves_omitted_fields_untouched(self):
        # Rescheduling a holiday must not blank its name, and renaming must not
        # move its date.
        holiday = academics_repo.create_holiday(
            self.db, 1, "Diwali", date(2026, 11, 8)
        )
        renamed = academics_repo.update_holiday(self.db, holiday, "Deepawali", None)
        self.assertEqual(renamed.occasion, "Deepawali")
        self.assertEqual(renamed.holiday_date, date(2026, 11, 8))

        moved = academics_repo.update_holiday(self.db, holiday, None, date(2026, 11, 9))
        self.assertEqual(moved.occasion, "Deepawali")
        self.assertEqual(moved.holiday_date, date(2026, 11, 9))

    def test_update_holiday_onto_a_taken_date_conflicts(self):
        academics_repo.create_holiday(self.db, 1, "Holi", date(2026, 3, 4))
        diwali = academics_repo.create_holiday(self.db, 1, "Diwali", date(2026, 11, 8))
        with self.assertRaises(ConflictError):
            academics_repo.update_holiday(self.db, diwali, None, date(2026, 3, 4))

    def test_update_holiday_keeping_its_own_date_does_not_conflict_with_itself(self):
        holiday = academics_repo.create_holiday(
            self.db, 1, "Diwali", date(2026, 11, 8)
        )
        renamed = academics_repo.update_holiday(
            self.db, holiday, "Diwali Holiday", None
        )
        self.assertEqual(renamed.occasion, "Diwali Holiday")

    def test_delete_holiday_removes_the_row(self):
        holiday = academics_repo.create_holiday(
            self.db, 1, "Diwali", date(2026, 11, 8)
        )
        academics_repo.delete_holiday(self.db, holiday)
        self.assertEqual(academics_repo.list_holidays(self.db, 1), [])
        self.assertIsNone(
            self.db.query(Holiday).filter(
                Holiday.holiday_id == holiday.holiday_id
            ).first()
        )

    def test_delete_one_holiday_leaves_the_others(self):
        academics_repo.create_holiday(self.db, 1, "Holi", date(2026, 3, 4))
        diwali = academics_repo.create_holiday(self.db, 1, "Diwali", date(2026, 11, 8))
        academics_repo.delete_holiday(self.db, diwali)
        self.assertEqual(
            [r.occasion for r in academics_repo.list_holidays(self.db, 1)], ["Holi"]
        )

    def test_deleting_another_schools_holiday_is_not_possible(self):
        # The router resolves through get_holiday first, which is school-scoped;
        # this pins that the scoping helper is what guards it.
        diwali = academics_repo.create_holiday(
            self.db, 1, "Diwali", date(2026, 11, 8)
        )
        self.assertIsNone(academics_repo.get_holiday(self.db, 2, diwali.holiday_id))
        self.assertEqual(len(academics_repo.list_holidays(self.db, 1)), 1)


class HolidayRangeLookupTests(_InMemoryDb, unittest.TestCase):
    def test_holidays_in_range_maps_date_to_occasion(self):
        academics_repo.create_holiday(self.db, 1, "Gandhi Jayanti", date(2026, 9, 23))
        mapping = holidays_in_range(self.db, 1, WEEK_START, WEEK_END)
        self.assertEqual(mapping, {date(2026, 9, 23): "Gandhi Jayanti"})

    def test_holidays_in_range_excludes_dates_on_either_side(self):
        academics_repo.create_holiday(self.db, 1, "Before", date(2026, 9, 20))
        academics_repo.create_holiday(self.db, 1, "After", date(2026, 9, 28))
        self.assertEqual(holidays_in_range(self.db, 1, WEEK_START, WEEK_END), {})

    def test_holidays_in_range_includes_both_endpoints(self):
        academics_repo.create_holiday(self.db, 1, "First", date(2026, 9, 21))
        academics_repo.create_holiday(self.db, 1, "Last", date(2026, 9, 27))
        mapping = holidays_in_range(self.db, 1, WEEK_START, WEEK_END)
        self.assertEqual(
            mapping,
            {date(2026, 9, 21): "First", date(2026, 9, 27): "Last"},
        )

    def test_holidays_in_range_is_scoped_to_the_school(self):
        academics_repo.create_holiday(self.db, 2, "Festival", date(2026, 9, 23))
        self.assertEqual(holidays_in_range(self.db, 1, WEEK_START, WEEK_END), {})

    def test_shared_list_helper_returns_date_order(self):
        academics_repo.create_holiday(self.db, 1, "B", date(2026, 5, 5))
        academics_repo.create_holiday(self.db, 1, "A", date(2026, 1, 1))
        self.assertEqual([h.occasion for h in list_holidays(self.db, 1)], ["A", "B"])


class HolidayRangeCreationTests(_InMemoryDb, unittest.TestCase):
    def test_one_day_without_an_end_date_creates_a_single_row(self):
        # The common case must stay a single row, not a one-element range, so
        # nothing downstream learns to expect a list where there was one value.
        created = academics_repo.create_holiday_range(
            self.db, 1, "Diwali", date(2026, 11, 8)
        )
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].holiday_date, date(2026, 11, 8))

    def test_an_end_date_equal_to_the_start_creates_a_single_row(self):
        # Picking the same calendar day twice is a one-day holiday, not a
        # request for a two-day span that happens to repeat a date.
        created = academics_repo.create_holiday_range(
            self.db, 1, "Diwali", date(2026, 11, 8), date(2026, 11, 8)
        )
        self.assertEqual(len(created), 1)

    def test_a_range_creates_one_row_per_day_including_both_ends(self):
        created = academics_repo.create_holiday_range(
            self.db, 1, "Diwali Break", date(2026, 11, 8), date(2026, 11, 11)
        )
        self.assertEqual(
            [h.holiday_date for h in created],
            [
                date(2026, 11, 8),
                date(2026, 11, 9),
                date(2026, 11, 10),
                date(2026, 11, 11),
            ],
        )
        self.assertTrue(all(h.occasion == "Diwali Break" for h in created))

    def test_a_range_spanning_a_weekend_includes_the_weekend_days(self):
        # Sat/Sun are closed days like any other here, and the school week runs
        # seven days, so dropping them would silently shorten the break.
        created = academics_repo.create_holiday_range(
            self.db, 1, "Long Weekend", date(2026, 11, 7), date(2026, 11, 9)
        )
        self.assertEqual(
            [h.holiday_date for h in created],
            [date(2026, 11, 7), date(2026, 11, 8), date(2026, 11, 9)],
        )

    def test_a_range_crossing_a_month_boundary_covers_both_months(self):
        created = academics_repo.create_holiday_range(
            self.db, 1, "Year End", date(2026, 12, 30), date(2027, 1, 2)
        )
        self.assertEqual(
            [h.holiday_date for h in created],
            [
                date(2026, 12, 30),
                date(2026, 12, 31),
                date(2027, 1, 1),
                date(2027, 1, 2),
            ],
        )

    def test_a_range_crossing_a_leap_day_includes_it(self):
        created = academics_repo.create_holiday_range(
            self.db, 1, "Spring Break", date(2028, 2, 28), date(2028, 3, 1)
        )
        self.assertIn(date(2028, 2, 29), [h.holiday_date for h in created])

    def test_a_range_in_another_school_does_not_touch_this_one(self):
        academics_repo.create_holiday_range(
            self.db, 1, "Diwali", date(2026, 11, 8), date(2026, 11, 10)
        )
        academics_repo.create_holiday_range(
            self.db, 2, "Festival", date(2026, 11, 8), date(2026, 11, 10)
        )
        self.assertEqual(
            len(academics_repo.list_holidays(self.db, 1)), 3
        )
        self.assertEqual(
            len(academics_repo.list_holidays(self.db, 2)), 3
        )


class HolidayRangeConflictTests(_InMemoryDb, unittest.TestCase):
    def test_a_range_overlapping_an_existing_holiday_is_refused_whole(self):
        academics_repo.create_holiday(self.db, 1, "Holi", date(2026, 3, 5))
        with self.assertRaises(ConflictError):
            academics_repo.create_holiday_range(
                self.db, 1, "Break", date(2026, 3, 4), date(2026, 3, 7)
            )
        # Nothing from the refused range may be written: a half-applied break
        # would leave the school open on a day the admin believed was shut.
        self.assertEqual(
            [h.occasion for h in academics_repo.list_holidays(self.db, 1)], ["Holi"]
        )

    def test_a_range_whose_only_conflict_is_its_first_day_is_refused(self):
        academics_repo.create_holiday(self.db, 1, "Holi", date(2026, 3, 4))
        with self.assertRaises(ConflictError):
            academics_repo.create_holiday_range(
                self.db, 1, "Break", date(2026, 3, 4), date(2026, 3, 6)
            )
        self.assertEqual(len(academics_repo.list_holidays(self.db, 1)), 1)

    def test_a_range_whose_only_conflict_is_its_last_day_is_refused(self):
        academics_repo.create_holiday(self.db, 1, "Holi", date(2026, 3, 6))
        with self.assertRaises(ConflictError):
            academics_repo.create_holiday_range(
                self.db, 1, "Break", date(2026, 3, 4), date(2026, 3, 6)
            )
        self.assertEqual(len(academics_repo.list_holidays(self.db, 1)), 1)

    def test_a_range_touching_the_day_before_an_existing_holiday_is_allowed(self):
        academics_repo.create_holiday(self.db, 1, "Holi", date(2026, 3, 5))
        academics_repo.create_holiday_range(
            self.db, 1, "Break", date(2026, 3, 3), date(2026, 3, 4)
        )
        self.assertEqual(
            [h.holiday_date for h in academics_repo.list_holidays(self.db, 1)],
            [date(2026, 3, 3), date(2026, 3, 4), date(2026, 3, 5)],
        )

    def test_the_refusal_message_names_the_offending_dates(self):
        # A term break can be dozens of days wide; "that date" would leave the
        # admin hunting for which one is wrong.
        academics_repo.create_holiday(self.db, 1, "Holi", date(2026, 3, 5))
        with self.assertRaises(ConflictError) as caught:
            academics_repo.create_holiday_range(
                self.db, 1, "Break", date(2026, 3, 4), date(2026, 3, 6)
            )
        self.assertIn("2026-03-05", str(caught.exception))

    def test_a_reversed_range_is_rejected_rather_than_silently_reordered(self):
        # Reordering for the user would hide which of their two picks was wrong.
        with self.assertRaises(AppError):
            academics_repo.create_holiday_range(
                self.db, 1, "Break", date(2026, 3, 7), date(2026, 3, 4)
            )
        self.assertEqual(academics_repo.list_holidays(self.db, 1), [])


class HolidayGroupTests(_InMemoryDb, unittest.TestCase):
    def _break(self):
        return academics_repo.create_holiday_range(
            self.db, 1, "Diwali Break", date(2026, 11, 8), date(2026, 11, 10)
        )

    def test_consecutive_days_of_one_occasion_are_one_group(self):
        rows = self._break()
        group = academics_repo.holiday_group(self.db, rows[1])
        self.assertEqual(
            [h.holiday_date for h in group],
            [date(2026, 11, 8), date(2026, 11, 9), date(2026, 11, 10)],
        )

    def test_any_day_of_a_break_reaches_the_whole_break(self):
        rows = self._break()
        self.assertEqual(len(academics_repo.holiday_group(self.db, rows[2])), 3)

    def test_a_gap_starts_a_separate_group(self):
        # Grouping across a gap would invent a closure on the missing day.
        academics_repo.create_holiday_range(
            self.db, 1, "Diwali Break", date(2026, 11, 8), date(2026, 11, 9)
        )
        later = academics_repo.create_holiday(self.db, 1, "Diwali Break", date(2026, 11, 12))
        self.assertEqual(len(academics_repo.holiday_group(self.db, later)), 1)

    def test_a_different_occasion_next_door_is_not_joined(self):
        # Two festivals side by side are two entries, and merging them would
        # hide one of the names behind the other.
        academics_repo.create_holiday(self.db, 1, "Diwali", date(2026, 11, 8))
        academics_repo.create_holiday(self.db, 1, "Ganesh Chaturthi", date(2026, 11, 9))
        first = academics_repo.list_holidays(self.db, 1)[0]
        self.assertEqual(len(academics_repo.holiday_group(self.db, first)), 1)

    def test_a_group_never_reaches_into_another_school(self):
        other = academics_repo.create_holiday_range(
            self.db, 2, "Diwali Break", date(2026, 11, 8), date(2026, 11, 10)
        )
        self.assertEqual(len(academics_repo.holiday_group(self.db, other[0])), 3)


class HolidayRangeUpdateTests(_InMemoryDb, unittest.TestCase):
    def _break(self):
        return academics_repo.create_holiday_range(
            self.db, 1, "Diwali Break", date(2026, 11, 8), date(2026, 11, 10)
        )

    def test_renaming_a_break_renames_every_day_of_it(self):
        rows = self._break()
        academics_repo.update_holiday_range(self.db, rows[0], "Deepavali", None, None)
        self.assertEqual(
            [h.occasion for h in academics_repo.list_holidays(self.db, 1)],
            ["Deepavali"] * 3,
        )

    def test_extending_a_break_adds_the_new_days(self):
        rows = self._break()
        updated = academics_repo.update_holiday_range(
            self.db, rows[0], None, None, date(2026, 11, 12)
        )
        self.assertEqual(
            [h.holiday_date for h in updated],
            [
                date(2026, 11, 8),
                date(2026, 11, 9),
                date(2026, 11, 10),
                date(2026, 11, 11),
                date(2026, 11, 12),
            ],
        )

    def test_shortening_a_break_removes_the_dropped_days(self):
        rows = self._break()
        academics_repo.update_holiday_range(
            self.db, rows[0], None, None, date(2026, 11, 8)
        )
        self.assertEqual(
            [h.holiday_date for h in academics_repo.list_holidays(self.db, 1)],
            [date(2026, 11, 8)],
        )

    def test_moving_a_break_without_restating_the_end_keeps_its_length(self):
        # Shifting a break by one day must not collapse it to a single day.
        rows = self._break()
        updated = academics_repo.update_holiday_range(
            self.db, rows[0], None, date(2026, 11, 15), None
        )
        self.assertEqual(
            [h.holiday_date for h in updated],
            [
                date(2026, 11, 15),
                date(2026, 11, 16),
                date(2026, 11, 17),
            ],
        )

    def test_moving_and_resizing_a_break_together_uses_both(self):
        rows = self._break()
        updated = academics_repo.update_holiday_range(
            self.db, rows[0], None, date(2026, 11, 15), date(2026, 11, 16)
        )
        self.assertEqual(
            [h.holiday_date for h in updated],
            [date(2026, 11, 15), date(2026, 11, 16)],
        )

    def test_a_move_onto_another_holiday_is_refused_and_leaves_the_break_intact(self):
        rows = self._break()
        academics_repo.create_holiday(self.db, 1, "Ganesh Chaturthi", date(2026, 11, 16))
        with self.assertRaises(ConflictError):
            academics_repo.update_holiday_range(
                self.db, rows[0], None, date(2026, 11, 15), None
            )
        # The refusal must not leave the break half-shifted.
        self.assertEqual(
            [h.holiday_date for h in academics_repo.list_holidays(self.db, 1)],
            [
                date(2026, 11, 8),
                date(2026, 11, 9),
                date(2026, 11, 10),
                date(2026, 11, 16),
            ],
        )

    def test_updating_a_break_does_not_conflict_with_its_own_days(self):
        # A resize that overlaps the break's existing span is normal editing,
        # not a collision with another holiday.
        rows = self._break()
        academics_repo.update_holiday_range(
            self.db, rows[0], None, None, date(2026, 11, 14)
        )
        self.assertEqual(len(academics_repo.list_holidays(self.db, 1)), 7)

    def test_an_edit_touching_any_day_of_a_break_rewrites_the_whole_break(self):
        rows = self._break()
        academics_repo.update_holiday_range(self.db, rows[2], "Deepavali", None, None)
        self.assertEqual(
            [h.occasion for h in academics_repo.list_holidays(self.db, 1)],
            ["Deepavali"] * 3,
        )


class HolidayGroupDeleteTests(_InMemoryDb, unittest.TestCase):
    def test_removing_a_break_removes_all_of_its_days(self):
        rows = academics_repo.create_holiday_range(
            self.db, 1, "Diwali Break", date(2026, 11, 8), date(2026, 11, 10)
        )
        removed = academics_repo.delete_holiday_group(self.db, rows[1])
        self.assertEqual(removed, 3)
        self.assertEqual(academics_repo.list_holidays(self.db, 1), [])

    def test_removing_a_break_leaves_other_holidays_alone(self):
        academics_repo.create_holiday(self.db, 1, "Holi", date(2026, 3, 4))
        rows = academics_repo.create_holiday_range(
            self.db, 1, "Diwali Break", date(2026, 11, 8), date(2026, 11, 10)
        )
        academics_repo.delete_holiday_group(self.db, rows[0])
        self.assertEqual(
            [h.occasion for h in academics_repo.list_holidays(self.db, 1)], ["Holi"]
        )

    def test_removing_a_break_leaves_an_adjacent_different_holiday_alone(self):
        academics_repo.create_holiday(self.db, 1, "Holi", date(2026, 11, 7))
        rows = academics_repo.create_holiday_range(
            self.db, 1, "Diwali Break", date(2026, 11, 8), date(2026, 11, 10)
        )
        academics_repo.delete_holiday_group(self.db, rows[0])
        self.assertEqual(
            [h.occasion for h in academics_repo.list_holidays(self.db, 1)], ["Holi"]
        )

    def test_removing_a_single_day_holiday_still_removes_exactly_one_row(self):
        rows = academics_repo.create_holiday_range(
            self.db, 1, "Diwali", date(2026, 11, 8), date(2026, 11, 8)
        )
        self.assertEqual(academics_repo.delete_holiday_group(self.db, rows[0]), 1)


class TimetableWeekViewTests(_InMemoryDb, unittest.TestCase):
    def _add_timetable(self):
        self.db.add_all(
            [
                Subject(subject_id=1, school_id=1, name="Maths"),
                Subject(subject_id=2, school_id=2, name="Robotics"),
                Period(period_id=1, period_no=1, period_time="9:00 AM - 9:45 AM"),
                TimetableEntry(
                    entry_id=1, school_id=1, class_id=1, day_of_week="Mon",
                    period_id=1, period_start_time=time(9, 0), period_end_time=time(9, 45),
                    subject_id=1, staff_id=1,
                ),
                TimetableEntry(
                    entry_id=2, school_id=1, class_id=1, day_of_week="Wed",
                    period_id=1, period_start_time=time(9, 0), period_end_time=time(9, 45),
                    subject_id=1, staff_id=1,
                ),
            ]
        )
        self.db.commit()

    def test_class_timetable_is_scoped_to_the_callers_school(self):
        # Regression: a teacher in school 1 used to read school 2's timetable.
        self._add_timetable()
        self.db.add(
            TimetableEntry(
                entry_id=3, school_id=2, class_id=2, day_of_week="Mon",
                period_id=1, period_start_time=time(9, 0), period_end_time=time(9, 45),
                subject_id=2, staff_id=2,
            )
        )
        self.db.commit()
        self.assertEqual(len(timetable_repo.get_class_timetable(self.db, 2, 1)), 0)
        self.assertEqual(len(timetable_repo.get_class_timetable(self.db, 1, 1)), 2)
        self.assertEqual(len(timetable_repo.get_class_timetable(self.db, 2, 2)), 1)

    def test_get_class_in_school_rejects_foreign_class(self):
        self.assertIsNotNone(timetable_repo.get_class_in_school(self.db, 1, 1))
        self.assertIsNone(timetable_repo.get_class_in_school(self.db, 2, 1))
        self.assertIsNone(timetable_repo.get_class_in_school(self.db, 1, 2))

    def test_get_class_week_returns_seven_monday_first_days(self):
        self._add_timetable()
        week = timetable_repo.get_class_week(self.db, 1, 1, WEEK_START)
        self.assertEqual(week["week_start"], WEEK_START)
        self.assertEqual(week["week_end"], WEEK_END)
        self.assertEqual(week["class_id"], 1)
        self.assertEqual(week["school_id"], 1)
        self.assertEqual([d["day_of_week"] for d in week["days"]], list(DAY_ABBREVIATIONS))
        self.assertEqual(week["days"][0]["date"], WEEK_START)
        self.assertEqual(week["days"][-1]["date"], WEEK_END)

    def test_get_class_week_groups_entries_under_their_day(self):
        self._add_timetable()
        week = timetable_repo.get_class_week(self.db, 1, 1, WEEK_START)
        by_day = {d["day_of_week"]: d for d in week["days"]}
        self.assertEqual(len(by_day["Mon"]["entries"]), 1)
        self.assertEqual(by_day["Mon"]["entries"][0].entry_id, 1)
        self.assertEqual(len(by_day["Wed"]["entries"]), 1)
        self.assertEqual(by_day["Tue"]["entries"], [])
        self.assertEqual(by_day["Sun"]["entries"], [])

    def test_get_class_week_flags_only_the_matching_date(self):
        self._add_timetable()
        academics_repo.create_holiday(self.db, 1, "Gandhi Jayanti", date(2026, 9, 23))
        week = timetable_repo.get_class_week(self.db, 1, 1, WEEK_START)
        by_day = {d["day_of_week"]: d for d in week["days"]}
        self.assertTrue(by_day["Wed"]["is_holiday"])
        self.assertEqual(by_day["Wed"]["holiday_name"], "Gandhi Jayanti")
        self.assertFalse(by_day["Mon"]["is_holiday"])
        self.assertIsNone(by_day["Mon"]["holiday_name"])

    def test_a_holiday_outside_the_week_does_not_highlight_any_column(self):
        # The recurring-template bug in one assertion: a dated holiday must not
        # paint every occurrence of its weekday.
        self._add_timetable()
        academics_repo.create_holiday(self.db, 1, "Diwali", date(2026, 11, 8))
        week = timetable_repo.get_class_week(self.db, 1, 1, WEEK_START)
        self.assertTrue(all(d["is_holiday"] is False for d in week["days"]))
        # ...but the same school is highlighted in the week that contains it.
        diwali_week = timetable_repo.get_class_week(self.db, 1, 1, date(2026, 11, 8))
        flagged = [d for d in diwali_week["days"] if d["is_holiday"]]
        self.assertEqual([d["day_of_week"] for d in flagged], ["Sun"])
        self.assertEqual(flagged[0]["holiday_name"], "Diwali")

    def test_holidays_in_the_displayed_week_do_not_affect_other_weeks(self):
        self._add_timetable()
        academics_repo.create_holiday(self.db, 1, "Gandhi Jayanti", date(2026, 9, 23))
        next_week = timetable_repo.get_class_week(self.db, 1, 1, date(2026, 9, 28))
        self.assertTrue(all(d["is_holiday"] is False for d in next_week["days"]))

    def test_get_class_week_ignores_another_schools_holidays(self):
        self._add_timetable()
        academics_repo.create_holiday(self.db, 2, "Festival", date(2026, 9, 23))
        week = timetable_repo.get_class_week(self.db, 1, 1, WEEK_START)
        self.assertTrue(all(d["is_holiday"] is False for d in week["days"]))

    def test_a_holiday_range_reddens_every_day_of_it_in_one_week(self):
        # The point of storing a range one row per day: the timetable resolves
        # holidays by date, so a three-day break lights three columns with no
        # range awareness in this service at all.
        self._add_timetable()
        academics_repo.create_holiday_range(
            self.db, 1, "Gandhi Jayanti Break", date(2026, 9, 21), date(2026, 9, 23)
        )
        week = timetable_repo.get_class_week(self.db, 1, 1, WEEK_START)
        flagged = {d["day_of_week"]: d["holiday_name"] for d in week["days"] if d["is_holiday"]}
        self.assertEqual(
            flagged,
            {
                "Mon": "Gandhi Jayanti Break",
                "Tue": "Gandhi Jayanti Break",
                "Wed": "Gandhi Jayanti Break",
            },
        )

    def test_a_range_only_reddens_the_days_it_actually_covers(self):
        self._add_timetable()
        academics_repo.create_holiday_range(
            self.db, 1, "Short Break", date(2026, 9, 22), date(2026, 9, 23)
        )
        week = timetable_repo.get_class_week(self.db, 1, 1, WEEK_START)
        self.assertEqual(
            [d["day_of_week"] for d in week["days"] if d["is_holiday"]],
            ["Tue", "Wed"],
        )

    def test_a_range_spanning_two_weeks_shows_up_in_each_of_them(self):
        # A break longer than a week must be visible from either week, since
        # the timetable only ever renders seven days at a time.
        self._add_timetable()
        academics_repo.create_holiday_range(
            self.db, 1, "Long Break", date(2026, 9, 24), date(2026, 10, 2)
        )
        this_week = timetable_repo.get_class_week(self.db, 1, 1, WEEK_START)
        next_week = timetable_repo.get_class_week(self.db, 1, 1, date(2026, 9, 28))
        self.assertEqual(
            [d["day_of_week"] for d in this_week["days"] if d["is_holiday"]],
            ["Thu", "Fri", "Sat", "Sun"],
        )
        # The second week runs 28 Sep - 4 Oct, so it carries the rest of the
        # break through to its final day.
        self.assertEqual(
            [d["date"] for d in next_week["days"] if d["is_holiday"]],
            [
                date(2026, 9, 28),
                date(2026, 9, 29),
                date(2026, 9, 30),
                date(2026, 10, 1),
                date(2026, 10, 2),
            ],
        )

    def test_removing_a_range_clears_the_timetable_in_every_affected_week(self):
        self._add_timetable()
        rows = academics_repo.create_holiday_range(
            self.db, 1, "Gandhi Jayanti Break", date(2026, 9, 21), date(2026, 9, 23)
        )
        academics_repo.delete_holiday_group(self.db, rows[1])
        week = timetable_repo.get_class_week(self.db, 1, 1, WEEK_START)
        self.assertTrue(all(d["is_holiday"] is False for d in week["days"]))

    def test_two_holidays_in_one_week_are_both_flagged(self):
        self._add_timetable()
        academics_repo.create_holiday(self.db, 1, "A", date(2026, 9, 22))
        academics_repo.create_holiday(self.db, 1, "B", date(2026, 9, 26))
        week = timetable_repo.get_class_week(self.db, 1, 1, WEEK_START)
        flagged = {d["day_of_week"]: d["holiday_name"] for d in week["days"] if d["is_holiday"]}
        self.assertEqual(flagged, {"Tue": "A", "Sat": "B"})

    def test_get_class_week_advances_dates_and_keeps_the_recurring_template(self):
        self._add_timetable()
        first = timetable_repo.get_class_week(self.db, 1, 1, WEEK_START)
        next_week = timetable_repo.get_class_week(self.db, 1, 1, date(2026, 9, 28))
        self.assertEqual(next_week["week_start"], date(2026, 9, 28))
        self.assertEqual(next_week["days"][0]["date"], date(2026, 9, 28))
        # Entries are a recurring weekday template, so they repeat each week.
        first_ids = [e.entry_id for d in first["days"] for e in d["entries"]]
        next_ids = [e.entry_id for d in next_week["days"] for e in d["entries"]]
        self.assertEqual(first_ids, next_ids)

    def test_get_class_week_snaps_a_mid_week_date_to_monday(self):
        self._add_timetable()
        from_wed = timetable_repo.get_class_week(self.db, 1, 1, date(2026, 9, 23))
        from_mon = timetable_repo.get_class_week(self.db, 1, 1, WEEK_START)
        self.assertEqual(from_wed["week_start"], WEEK_START)
        self.assertEqual(
            [d["date"] for d in from_wed["days"]],
            [d["date"] for d in from_mon["days"]],
        )

    def test_get_class_week_excludes_another_schools_entries(self):
        self._add_timetable()
        self.db.add(
            TimetableEntry(
                entry_id=3, school_id=2, class_id=2, day_of_week="Mon",
                period_id=1, period_start_time=time(9, 0), period_end_time=time(9, 45),
                subject_id=2, staff_id=2,
            )
        )
        self.db.commit()
        week = timetable_repo.get_class_week(self.db, 1, 1, WEEK_START)
        monday = next(d for d in week["days"] if d["day_of_week"] == "Mon")
        self.assertEqual([e.entry_id for e in monday["entries"]], [1])

    def test_get_class_week_defaults_to_the_current_week(self):
        self._add_timetable()
        week = timetable_repo.get_class_week(self.db, 1, 1, None)
        today = date.today()
        self.assertEqual(week["week_start"], monday_of(today))
        self.assertEqual(week["week_end"], monday_of(today) + timedelta(days=6))

    def test_get_class_week_handles_a_school_with_no_holidays(self):
        self._add_timetable()
        self.assertEqual(self.db.query(Holiday).filter(Holiday.school_id == 1).count(), 0)
        week = timetable_repo.get_class_week(self.db, 1, 1, WEEK_START)
        # No rows must mean "no holidays", not a crash or a missing day.
        self.assertEqual(len(week["days"]), 7)
        self.assertTrue(all(d["is_holiday"] is False for d in week["days"]))
        self.assertTrue(all(d["holiday_name"] is None for d in week["days"]))


class HolidayOverrideFlagTests(_InMemoryDb, unittest.TestCase):
    """`is_holiday_override` must be explicit, never inferred from a weekday.

    This is a behaviour change from the recurring-weekday model. An entry is a
    repeating Mon-Sun template with no date of its own, so there is no holiday
    date to compare it against: inferring the flag would mark a period on every
    Fri forever. The flag now only moves when a caller asks for it.
    """

    def _entry(self):
        self.db.add_all(
            [
                Subject(subject_id=1, school_id=1, name="Maths"),
                Period(period_id=1, period_no=1, period_time="9:00 AM - 9:45 AM"),
                TimetableEntry(
                    entry_id=1, school_id=1, class_id=1, day_of_week="Fri",
                    period_id=1, period_start_time=time(9, 0), period_end_time=time(9, 45),
                    subject_id=1, staff_id=1,
                ),
            ]
        )
        self.db.commit()
        return self.db.get(TimetableEntry, 1)

    def test_updating_other_fields_leaves_the_flag_alone(self):
        entry = self._entry()
        self.assertFalse(entry.is_holiday_override)
        updated = timetable_repo.update_entry(
            self.db, entry, subject_id=None, teacher_id=None,
            period_start_time=None, period_end_time=None, school_id=1,
        )
        self.assertFalse(updated.is_holiday_override)

    def test_a_holiday_on_the_entries_weekday_does_not_raise_the_flag(self):
        # Gandhi Jayanti falls on Friday 2026-10-02, the same weekday as the
        # entry. Under the old weekday-based model this would have set the flag.
        entry = self._entry()
        self.db.add(
            Holiday(
                holiday_id=1, school_id=1, occasion="Gandhi Jayanti",
                holiday_date=date(2026, 10, 2),
            )
        )
        self.db.commit()
        week = timetable_repo.get_class_week(self.db, 1, 1, date(2026, 9, 28))
        friday = next(d for d in week["days"] if d["day_of_week"] == "Fri")
        self.assertTrue(friday["is_holiday"])
        self.assertEqual(friday["holiday_name"], "Gandhi Jayanti")
        entry = self.db.get(TimetableEntry, 1)
        self.assertFalse(
            entry.is_holiday_override,
            "a dated holiday must not mark every Friday as an override",
        )

    def test_the_flag_moves_only_when_explicitly_set(self):
        entry = self._entry()
        updated = timetable_repo.update_entry(
            self.db, entry, subject_id=None, teacher_id=None,
            period_start_time=None, period_end_time=None, school_id=1,
            is_holiday_override=True,
        )
        self.assertTrue(updated.is_holiday_override)
        cleared = timetable_repo.clear_override(self.db, updated)
        self.assertFalse(cleared.is_holiday_override)

    def test_update_schema_defaults_the_flag_to_none_not_false(self):
        # False would silently clear an existing override on any PATCH.
        schema = TimetableEntryUpdate(subject_id=1)
        self.assertIsNone(schema.is_holiday_override)
        self.assertTrue(TimetableEntryUpdate(is_holiday_override=True).is_holiday_override)

    def test_update_schema_rejects_an_empty_patch(self):
        with self.assertRaises(ValueError):
            TimetableEntryUpdate()
