"""Automated tests: tumatakbo gamit ang `python -m unittest -v`.

Sinusubok dito yung business rules (lalo na ang double booking), validation,
at kung bumubukas ba lahat ng page. Temporary database ang gamit, kaya hindi ginagalaw ang totoong data.
"""
import os
import shutil
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta
from unittest import mock

# itakda muna ang temporary DB path BAGO i-import ang app, para hindi gumawa ng appointments.db dito sa folder
_IMPORT_DIR = tempfile.mkdtemp()
os.environ["APPOINTMENT_DB"] = os.path.join(_IMPORT_DIR, "import.db")

import app as app_module  # noqa: E402
import db  # noqa: E402
import scheduling  # noqa: E402
import seed  # noqa: E402
import validators  # noqa: E402


def future_workday(days_ahead=7):
    """Susunod na araw na hindi Sunday, para pasok sa oras ng clinic."""
    day = date.today() + timedelta(days=days_ahead)
    while day.weekday() == 6:
        day += timedelta(days=1)
    return day.isoformat()


class BaseCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "test.db")
        app_module.app.config["DATABASE"] = self.path
        app_module.app.config["TESTING"] = True
        db.init_db(self.path, seed=False)

        # mga pangunahing data na gagamitin sa bawat test
        self.conn = db.connect(self.path)
        self.conn.executemany(
            "INSERT INTO clients (name, contact) VALUES (?, ?)",
            [("Juan Dela Cruz", "09171234567"), ("Maria Santos", "09181234567")],
        )
        self.conn.executemany(
            "INSERT INTO staff_or_resources (name, role, is_active) VALUES (?, ?, ?)",
            [("Dr. Reyes", "Dentist", 1), ("Dr. Garcia", "Dentist", 1), ("Old Chair", "Room / Equipment", 0)],
        )
        self.conn.executemany(
            "INSERT INTO services (name, duration_minutes) VALUES (?, ?)",
            [("Consultation", 30), ("Cleaning", 60), ("Root Canal", 90)],
        )
        self.day = future_workday()
        self.client = app_module.app.test_client()

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # --- helpers
    def post(self, url, data=None, **kwargs):
        """POST na may valid CSRF token, parang totoong form."""
        with self.client.session_transaction() as sess:
            sess["csrf_token"] = "test-token"
        payload = dict(data or {}, csrf_token="test-token")
        return self.client.post(url, data=payload, **kwargs)

    def book(self, client_id=1, resource_id=1, service_id=2, start="10:00", day=None):
        return scheduling.book_appointment(
            self.conn, client_id, resource_id, service_id, day or self.day, start, ""
        )

    def one(self, sql, params=()):
        return self.conn.execute(sql, params).fetchone()


class DoubleBookingTests(BaseCase):
    def test_same_slot_same_resource_is_rejected(self):
        self.book(start="10:00")
        with self.assertRaises(scheduling.ScheduleError) as ctx:
            self.book(client_id=2, start="10:00")
        self.assertIn("already booked", str(ctx.exception))
        self.assertEqual(self.one("SELECT COUNT(*) FROM appointments")[0], 1)

    def test_partial_overlap_is_rejected(self):
        self.book(start="10:00", service_id=2)  # 10:00 - 11:00
        with self.assertRaises(scheduling.ScheduleError):
            self.book(client_id=2, start="10:30", service_id=2)  # 10:30 - 11:30
        with self.assertRaises(scheduling.ScheduleError):
            self.book(client_id=2, start="09:30", service_id=2)  # 09:30 - 10:30

    def test_new_booking_that_wraps_old_one_is_rejected(self):
        self.book(start="10:30", service_id=1)  # 10:30 - 11:00
        with self.assertRaises(scheduling.ScheduleError):
            self.book(client_id=2, start="10:00", service_id=3)  # 10:00 - 11:30

    def test_back_to_back_is_allowed(self):
        self.book(start="10:00", service_id=2)  # hanggang 11:00
        self.book(client_id=2, start="11:00", service_id=2)  # simula 11:00 pwede
        self.assertEqual(self.one("SELECT COUNT(*) FROM appointments")[0], 2)

    def test_different_resource_same_time_is_allowed(self):
        self.book(resource_id=1, start="10:00")
        self.book(client_id=2, resource_id=2, start="10:00")
        self.assertEqual(self.one("SELECT COUNT(*) FROM appointments")[0], 2)

    def test_same_time_different_day_is_allowed(self):
        other_day = future_workday(14)
        self.book(start="10:00")
        self.book(client_id=2, start="10:00", day=other_day)
        self.assertEqual(self.one("SELECT COUNT(*) FROM appointments")[0], 2)

    def test_cancelled_appointment_no_longer_blocks_slot(self):
        appt_id = self.book(start="10:00")
        scheduling.change_status(self.conn, appt_id, "Cancelled")
        # pwede nang i-book ulit yung slot
        self.book(client_id=2, start="10:00")
        self.assertEqual(self.one("SELECT COUNT(*) FROM appointments WHERE status != 'Cancelled'")[0], 1)

    def test_database_unique_index_is_last_line_of_defense(self):
        # kahit lampasan natin ang Python check, ang database mismo ang tatanggi
        self.book(start="10:00")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO appointments (client_id, resource_id, service_id, appt_date, "
                "start_time, end_time, status) VALUES (2, 1, 1, ?, '10:00', '10:30', 'Pending')",
                (self.day,),
            )

    def test_inactive_resource_cannot_be_booked(self):
        with self.assertRaises(scheduling.ScheduleError):
            self.book(resource_id=3)

    def test_booking_cannot_run_past_closing_time(self):
        with self.assertRaises(scheduling.ScheduleError) as ctx:
            self.book(service_id=3, start="16:30")  # 90 mins, matatapos 18:00
        self.assertIn("closing time", str(ctx.exception))


class RescheduleTransactionTests(BaseCase):
    def test_reschedule_updates_slot_and_writes_history(self):
        appt_id = self.book(start="10:00")
        scheduling.reschedule_appointment(self.conn, appt_id, 2, self.day, "13:00")
        row = self.one("SELECT * FROM appointments WHERE appointment_id = ?", (appt_id,))
        self.assertEqual((row["resource_id"], row["start_time"], row["end_time"]), (2, "13:00", "14:00"))
        action = self.one(
            "SELECT action FROM appointment_history WHERE appointment_id = ? ORDER BY history_id DESC",
            (appt_id,),
        )["action"]
        self.assertEqual(action, "Rescheduled")

    def test_reschedule_into_taken_slot_changes_nothing(self):
        first = self.book(start="10:00")
        second = self.book(client_id=2, start="13:00")
        history_before = self.one("SELECT COUNT(*) FROM appointment_history")[0]
        with self.assertRaises(scheduling.ScheduleError):
            scheduling.reschedule_appointment(self.conn, second, 1, self.day, "10:30")
        row = self.one("SELECT start_time FROM appointments WHERE appointment_id = ?", (second,))
        self.assertEqual(row["start_time"], "13:00")
        self.assertEqual(self.one("SELECT COUNT(*) FROM appointment_history")[0], history_before)
        self.assertIsNotNone(first)

    def test_appointment_does_not_conflict_with_its_own_old_slot(self):
        appt_id = self.book(start="10:00", service_id=2)
        # lilipat ng 30 minutes; nag-o-overlap sa sarili niyang lumang slot, dapat pwede
        scheduling.reschedule_appointment(self.conn, appt_id, 1, self.day, "10:30")
        row = self.one("SELECT start_time FROM appointments WHERE appointment_id = ?", (appt_id,))
        self.assertEqual(row["start_time"], "10:30")

    def test_failure_in_middle_rolls_everything_back(self):
        appt_id = self.book(start="10:00")
        # sapilitang pumalya yung huling step (history); dapat bumalik sa dati yung UPDATE
        with mock.patch.object(scheduling, "_log", side_effect=sqlite3.OperationalError("boom")):
            with self.assertRaises(sqlite3.OperationalError):
                scheduling.reschedule_appointment(self.conn, appt_id, 2, self.day, "14:00")
        row = self.one("SELECT resource_id, start_time FROM appointments WHERE appointment_id = ?", (appt_id,))
        self.assertEqual((row["resource_id"], row["start_time"]), (1, "10:00"))
        self.assertFalse(self.conn.in_transaction)

    def test_cancelled_or_completed_cannot_be_rescheduled(self):
        appt_id = self.book(start="10:00")
        scheduling.change_status(self.conn, appt_id, "Cancelled")
        with self.assertRaises(scheduling.ScheduleError):
            scheduling.reschedule_appointment(self.conn, appt_id, 1, self.day, "11:00")


class StatusTests(BaseCase):
    def test_normal_flow(self):
        appt_id = self.book()
        scheduling.change_status(self.conn, appt_id, "Confirmed")
        self.assertEqual(self.one("SELECT status FROM appointments")["status"], "Confirmed")

    def test_cannot_go_backwards_from_completed(self):
        past = (date.today() - timedelta(days=1)).isoformat()
        self.conn.execute(
            "INSERT INTO appointments (client_id, resource_id, service_id, appt_date, start_time, "
            "end_time, status) VALUES (1, 1, 1, ?, '09:00', '09:30', 'Confirmed')", (past,))
        appt_id = self.one("SELECT appointment_id FROM appointments")[0]
        scheduling.change_status(self.conn, appt_id, "Completed")
        with self.assertRaises(scheduling.ScheduleError):
            scheduling.change_status(self.conn, appt_id, "Pending")

    def test_cancelled_is_final(self):
        appt_id = self.book()
        scheduling.change_status(self.conn, appt_id, "Cancelled")
        with self.assertRaises(scheduling.ScheduleError):
            scheduling.change_status(self.conn, appt_id, "Confirmed")

    def test_future_appointment_cannot_be_completed(self):
        appt_id = self.book()
        scheduling.change_status(self.conn, appt_id, "Confirmed")
        with self.assertRaises(scheduling.ScheduleError):
            scheduling.change_status(self.conn, appt_id, "Completed")

    def test_unknown_status_is_rejected(self):
        appt_id = self.book()
        with self.assertRaises(scheduling.ScheduleError):
            scheduling.change_status(self.conn, appt_id, "Done")

    def test_only_cancelled_can_be_deleted(self):
        appt_id = self.book()
        with self.assertRaises(scheduling.ScheduleError):
            scheduling.delete_appointment(self.conn, appt_id)
        scheduling.change_status(self.conn, appt_id, "Cancelled")
        scheduling.delete_appointment(self.conn, appt_id)
        self.assertEqual(self.one("SELECT COUNT(*) FROM appointments")[0], 0)


class ValidatorTests(unittest.TestCase):
    NOW = datetime(2026, 11, 2, 10, 0)  # Monday

    def form(self, **overrides):
        data = {"client_id": "1", "service_id": "1", "resource_id": "1",
                "appt_date": "2026-11-05", "start_time": "10:00", "notes": ""}
        data.update(overrides)
        return data

    def test_valid_appointment_has_no_errors(self):
        _, errors = validators.validate_appointment(self.form(), self.NOW)
        self.assertEqual(errors, [])

    def test_past_date_rejected(self):
        _, errors = validators.validate_appointment(self.form(appt_date="2026-11-01"), self.NOW)
        self.assertTrue(any("past" in e for e in errors))

    def test_sunday_rejected(self):
        _, errors = validators.validate_appointment(self.form(appt_date="2026-11-08"), self.NOW)
        self.assertTrue(any("Sunday" in e for e in errors))

    def test_time_that_already_passed_today_rejected(self):
        _, errors = validators.validate_appointment(
            self.form(appt_date="2026-11-02", start_time="09:00"), self.NOW)
        self.assertTrue(any("passed" in e for e in errors))

    def test_bad_date_and_time_rejected(self):
        _, errors = validators.validate_appointment(
            self.form(appt_date="2026-13-45", start_time="25:99"), self.NOW)
        self.assertEqual(len(errors), 2)

    def test_time_outside_clinic_hours_rejected(self):
        _, errors = validators.validate_appointment(self.form(start_time="20:00"), self.NOW)
        self.assertTrue(errors)

    def test_missing_fields_rejected(self):
        _, errors = validators.validate_appointment({}, self.NOW)
        self.assertGreaterEqual(len(errors), 5)

    def test_client_validation(self):
        _, errors = validators.validate_client({"name": "J", "contact": "abc", "email": "nope"})
        self.assertEqual(len(errors), 3)
        data, errors = validators.validate_client(
            {"name": "  Juan   Dela Cruz ", "contact": "0917 123-4567", "email": "A@B.com"})
        self.assertEqual(errors, [])
        self.assertEqual((data["name"], data["contact"], data["email"]),
                         ("Juan Dela Cruz", "09171234567", "a@b.com"))

    def test_service_duration_must_fit_slots(self):
        _, errors = validators.validate_service({"name": "Test", "duration_minutes": "45"})
        self.assertTrue(errors)


class WebTests(BaseCase):
    def booking_form(self, **overrides):
        data = {"client_id": "1", "service_id": "2", "resource_id": "1",
                "appt_date": self.day, "start_time": "10:00", "notes": ""}
        data.update(overrides)
        return data

    def test_all_pages_open(self):
        self.book()
        urls = ["/", "/schedule", "/schedule?date=" + self.day, "/clients", "/clients/new",
                "/clients/1", "/clients/1/edit", "/resources", "/resources/new", "/resources/1/edit",
                "/services", "/services/new", "/services/1/edit", "/appointments",
                "/appointments?status=Pending&q=juan", "/appointments/new", "/appointments/1/edit",
                "/appointments/1/history", "/api/booked-slots?resource_id=1&date=" + self.day]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_booking_through_form_then_double_booking_blocked(self):
        first = self.post("/appointments/new", self.booking_form(), follow_redirects=True)
        self.assertIn(b"was booked", first.data)

        second = self.post("/appointments/new", self.booking_form(client_id="2"), follow_redirects=True)
        self.assertIn(b"already booked", second.data)
        self.assertEqual(self.one("SELECT COUNT(*) FROM appointments")[0], 1)

    def test_form_without_csrf_token_is_rejected(self):
        response = self.client.post("/appointments/new", data=self.booking_form())
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.one("SELECT COUNT(*) FROM appointments")[0], 0)

    def test_invalid_booking_shows_error_messages(self):
        response = self.post("/appointments/new", {"notes": ""}, follow_redirects=True)
        self.assertIn(b"Please choose a client", response.data)
        self.assertIn(b"Please enter a valid date", response.data)

    def test_cancel_then_rebook_through_web(self):
        self.post("/appointments/new", self.booking_form())
        self.post("/appointments/1/status", {"status": "Cancelled"})
        response = self.post("/appointments/new", self.booking_form(client_id="2"), follow_redirects=True)
        self.assertIn(b"was booked", response.data)

    def test_invalid_status_change_shows_message(self):
        self.post("/appointments/new", self.booking_form())

        # Pending -> Completed direto ay bawal; kailangan muna ng Confirmed
        skipped = self.post("/appointments/1/status", {"status": "Completed"}, follow_redirects=True)
        self.assertIn(b"cannot be changed to Completed", skipped.data)

        # Confirmed na, pero future date pa kaya hindi pa pwedeng i-complete
        self.post("/appointments/1/status", {"status": "Confirmed"})
        early = self.post("/appointments/1/status", {"status": "Completed"}, follow_redirects=True)
        self.assertIn(b"cannot be marked as Completed", early.data)
        self.assertEqual(self.one("SELECT status FROM appointments")["status"], "Confirmed")

    def test_search_by_client_name_and_status_filter(self):
        self.book(client_id=1, start="09:00")
        self.book(client_id=2, start="11:00")
        scheduling.change_status(self.conn, 2, "Confirmed")

        found = self.client.get("/appointments?q=maria").data
        self.assertIn(b"Maria Santos", found)
        self.assertNotIn(b"Juan Dela Cruz", found)

        pending = self.client.get("/appointments?status=Pending").data
        self.assertIn(b"Juan Dela Cruz", pending)
        self.assertNotIn(b"Maria Santos", pending)

        by_date = self.client.get("/appointments?date=" + self.day).data
        self.assertIn(b"2 appointments", by_date)

    def test_search_is_safe_from_sql_injection(self):
        self.book()
        for attack in ["' OR 1=1 --", "'; DROP TABLE clients; --", "%", "_"]:
            with self.subTest(attack=attack):
                self.assertEqual(self.client.get("/clients", query_string={"q": attack}).status_code, 200)
                self.assertEqual(self.client.get("/appointments", query_string={"q": attack}).status_code, 200)
        # buo pa rin ang table, at hindi lahat ng client ang lumabas sa "' OR 1=1 --"
        self.assertEqual(self.one("SELECT COUNT(*) FROM clients")[0], 2)
        self.assertNotIn(b"Juan Dela Cruz", self.client.get("/clients", query_string={"q": "' OR 1=1 --"}).data)

    def test_duplicate_client_rejected(self):
        response = self.post(
            "/clients/new", {"name": "juan dela cruz", "contact": "09171234567"}, follow_redirects=True)
        self.assertIn(b"already registered", response.data)
        self.assertEqual(self.one("SELECT COUNT(*) FROM clients")[0], 2)

    def test_client_crud(self):
        self.post("/clients/new", {"name": "Ana Reyes", "contact": "09191234567"})
        new_id = self.one("SELECT client_id FROM clients WHERE name = 'Ana Reyes'")[0]
        self.post(f"/clients/{new_id}/edit", {"name": "Ana Reyes-Cruz", "contact": "09191234567"})
        self.assertEqual(self.one("SELECT name FROM clients WHERE client_id = ?", (new_id,))[0], "Ana Reyes-Cruz")
        self.post(f"/clients/{new_id}/delete")
        self.assertIsNone(self.one("SELECT 1 FROM clients WHERE client_id = ?", (new_id,)))

    def test_cannot_delete_client_with_appointments(self):
        self.book(client_id=1)
        response = self.post("/clients/1/delete", follow_redirects=True)
        self.assertIn(b"active appointment", response.data)
        self.assertIsNotNone(self.one("SELECT 1 FROM clients WHERE client_id = 1"))

    def test_resource_and_service_crud_and_delete_rules(self):
        self.post("/resources/new", {"name": "Dr. Lim", "role": "Dentist", "is_active": "1"})
        self.assertIsNotNone(self.one("SELECT 1 FROM staff_or_resources WHERE name = 'Dr. Lim'"))
        self.post("/services/new", {"name": "Whitening", "duration_minutes": "90"})
        self.assertIsNotNone(self.one("SELECT 1 FROM services WHERE name = 'Whitening'"))

        self.book(resource_id=1, service_id=2)
        blocked = self.post("/resources/1/delete", follow_redirects=True)
        self.assertIn(b"cannot be deleted", blocked.data)
        blocked = self.post("/services/2/delete", follow_redirects=True)
        self.assertIn(b"cannot be deleted", blocked.data)

    def test_booked_slots_api_hides_cancelled(self):
        appt_id = self.book(start="10:00")
        url = f"/api/booked-slots?resource_id=1&date={self.day}"
        self.assertEqual(len(self.client.get(url).get_json()["slots"]), 1)
        scheduling.change_status(self.conn, appt_id, "Cancelled")
        self.assertEqual(len(self.client.get(url).get_json()["slots"]), 0)

    def test_unknown_pages_show_friendly_404(self):
        for url in ["/nope", "/clients/999", "/appointments/999/edit", "/appointments/999/history"]:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 404)
                self.assertIn(b"Page not found", response.data)

    def test_open_redirect_is_blocked(self):
        self.post("/appointments/new", self.booking_form())
        response = self.post("/appointments/1/status", {"status": "Cancelled", "next": "//evil.example"})
        self.assertEqual(response.headers["Location"], "/appointments")


class DayGridAndSeedTests(BaseCase):
    def test_grid_rowspan_for_long_appointment(self):
        self.book(service_id=3, start="09:00")  # 90 mins = 3 slots
        resources = self.conn.execute("SELECT * FROM staff_or_resources WHERE resource_id = 1").fetchall()
        appts = self.conn.execute(
            app_module.APPT_SELECT + " WHERE a.appt_date = ?", (self.day,)).fetchall()
        grid = scheduling.build_day_grid(resources, appts)
        by_time = {row["time"]: row["cells"][0] for row in grid}
        self.assertEqual((by_time["09:00"]["kind"], by_time["09:00"]["span"]), ("appt", 3))
        self.assertEqual(by_time["09:30"]["kind"], "covered")
        self.assertEqual(by_time["10:00"]["kind"], "covered")
        self.assertEqual(by_time["10:30"]["kind"], "empty")

    def test_sample_data_never_has_overlaps_on_any_weekday(self):
        start = date(2026, 11, 2)
        for offset in range(7):
            with self.subTest(offset=offset):
                path = os.path.join(self.tmp, f"seed{offset}.db")
                db.init_db(path, seed=False)
                conn = db.connect(path)
                seed.load(conn, today=start + timedelta(days=offset))
                overlaps = conn.execute(
                    "SELECT COUNT(*) FROM appointments a JOIN appointments b "
                    "ON a.resource_id = b.resource_id AND a.appt_date = b.appt_date "
                    "AND a.appointment_id < b.appointment_id "
                    "AND a.status != 'Cancelled' AND b.status != 'Cancelled' "
                    "AND a.start_time < b.end_time AND a.end_time > b.start_time"
                ).fetchone()[0]
                conn.close()
                self.assertEqual(overlaps, 0)


if __name__ == "__main__":
    unittest.main()
