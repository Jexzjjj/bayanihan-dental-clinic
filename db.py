"""Database connection at mga helper.

Lahat ng koneksyon sa SQLite dumadaan dito para iisa lang ang setup.
"""
import os
import sqlite3
from contextlib import contextmanager

from flask import current_app, g

SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")


def connect(path):
    """Gumawa ng bagong connection.

    isolation_level=None ibig sabihin autocommit tayo, tapos tayo mismo
    ang magsasabi ng BEGIN at COMMIT kapag transaction (tingnan ang transaction()).
    """
    conn = sqlite3.connect(path, isolation_level=None, timeout=10)
    conn.row_factory = sqlite3.Row  # para pwedeng row["name"] imbes na row[1]
    # off by default ang foreign keys sa SQLite, kaya kailangan i-on sa bawat connection
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_db():
    """Isang connection lang bawat request; ginagamit ulit kung nabuksan na."""
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_error=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_app(app):
    # isasara yung connection pagkatapos ng bawat request
    app.teardown_appcontext(close_db)


def init_db(path, seed=False):
    """Gumawa ng bagong database. Babala: buburahin nito yung lumang data."""
    conn = connect(path)
    try:
        with open(SCHEMA_PATH, encoding="utf-8") as schema_file:
            conn.executescript(schema_file.read())
        if seed:
            import seed as seed_module  # dito na i-import para walang circular import

            seed_module.load(conn)
    finally:
        conn.close()


def migrate_db(path):
    """Install overlap guards without rebuilding or rewriting existing records.

    Refuse to install the guards over conflicting legacy data so an operator can
    review and resolve it explicitly instead of silently changing appointments.
    """
    conn = connect(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        overlap = conn.execute(
            "SELECT 1 FROM appointments a JOIN appointments b "
            "ON a.resource_id = b.resource_id AND a.appt_date = b.appt_date "
            "AND a.appointment_id < b.appointment_id "
            "AND a.status != 'Cancelled' AND b.status != 'Cancelled' "
            "AND a.start_time < b.end_time AND a.end_time > b.start_time LIMIT 1"
        ).fetchone()
        if overlap:
            raise RuntimeError(
                "Database migration stopped: overlapping active appointments already exist. "
                "Resolve them before starting the application."
            )
        appointment_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(appointments)")
        }
        invalid_schedule = conn.execute(
            """SELECT 1 FROM appointments
            WHERE date(appt_date, '+0 days') IS NOT appt_date
               OR strftime('%H:%M', '2000-01-01 ' || start_time) IS NOT start_time
               OR strftime('%H:%M', '2000-01-01 ' || end_time) IS NOT end_time
               OR substr(start_time, 4, 2) NOT IN ('00', '30')
               OR substr(end_time, 4, 2) NOT IN ('00', '30')
               OR end_time <= start_time
               OR ((CAST(substr(end_time, 1, 2) AS INTEGER) * 60 + CAST(substr(end_time, 4, 2) AS INTEGER))
                   - (CAST(substr(start_time, 1, 2) AS INTEGER) * 60 + CAST(substr(start_time, 4, 2) AS INTEGER))) <= 0
               OR ((CAST(substr(end_time, 1, 2) AS INTEGER) * 60 + CAST(substr(end_time, 4, 2) AS INTEGER))
                   - (CAST(substr(start_time, 1, 2) AS INTEGER) * 60 + CAST(substr(start_time, 4, 2) AS INTEGER))) % 30 != 0
               OR ((CAST(substr(end_time, 1, 2) AS INTEGER) * 60 + CAST(substr(end_time, 4, 2) AS INTEGER))
                   - (CAST(substr(start_time, 1, 2) AS INTEGER) * 60 + CAST(substr(start_time, 4, 2) AS INTEGER))) > 240
            LIMIT 1"""
        ).fetchone()
        if invalid_schedule:
            raise RuntimeError(
                "Database migration stopped: an appointment has an invalid calendar date, "
                "time, or duration. Review the existing records before starting the application."
            )
        if "duration_minutes" not in appointment_columns:
            conn.execute(
                "ALTER TABLE appointments ADD COLUMN duration_minutes INTEGER NOT NULL "
                "DEFAULT 30 CHECK (duration_minutes BETWEEN 30 AND 240 AND duration_minutes % 30 = 0)"
            )
            conn.execute(
                """UPDATE appointments SET duration_minutes =
                   (CAST(substr(end_time, 1, 2) AS INTEGER) * 60 + CAST(substr(end_time, 4, 2) AS INTEGER))
                   - (CAST(substr(start_time, 1, 2) AS INTEGER) * 60 + CAST(substr(start_time, 4, 2) AS INTEGER))"""
            )
        else:
            invalid_duration = conn.execute(
                """SELECT 1 FROM appointments
                WHERE duration_minutes < 30 OR duration_minutes > 240 OR duration_minutes % 30 != 0
                   OR duration_minutes !=
                     (CAST(substr(end_time, 1, 2) AS INTEGER) * 60 + CAST(substr(end_time, 4, 2) AS INTEGER))
                     - (CAST(substr(start_time, 1, 2) AS INTEGER) * 60 + CAST(substr(start_time, 4, 2) AS INTEGER))
                LIMIT 1"""
            ).fetchone()
            if invalid_duration:
                raise RuntimeError(
                    "Database migration stopped: an appointment's stored duration does not match its time range."
                )
        conn.execute(
            """CREATE TRIGGER IF NOT EXISTS appointments_no_overlap_insert
            BEFORE INSERT ON appointments
            WHEN NEW.status != 'Cancelled' AND EXISTS (
                SELECT 1 FROM appointments a
                WHERE a.resource_id = NEW.resource_id
                  AND a.appt_date = NEW.appt_date
                  AND a.status != 'Cancelled'
                  AND NEW.start_time < a.end_time
                  AND NEW.end_time > a.start_time
            )
            BEGIN
                SELECT RAISE(ABORT, 'appointment overlaps an existing booking');
            END"""
        )
        conn.execute(
            """CREATE TRIGGER IF NOT EXISTS appointments_no_overlap_update
            BEFORE UPDATE OF resource_id, appt_date, start_time, end_time, status
            ON appointments
            WHEN NEW.status != 'Cancelled' AND EXISTS (
                SELECT 1 FROM appointments a
                WHERE a.resource_id = NEW.resource_id
                  AND a.appt_date = NEW.appt_date
                  AND a.appointment_id != NEW.appointment_id
                  AND a.status != 'Cancelled'
                  AND NEW.start_time < a.end_time
                  AND NEW.end_time > a.start_time
            )
            BEGIN
                SELECT RAISE(ABORT, 'appointment overlaps an existing booking');
            END"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_appt_client_date "
            "ON appointments (client_id, appt_date)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_appt_service ON appointments (service_id)"
        )
        conn.execute(
            """CREATE TRIGGER IF NOT EXISTS appointments_validate_insert
            BEFORE INSERT ON appointments
            WHEN date(NEW.appt_date, '+0 days') IS NOT NEW.appt_date
              OR strftime('%H:%M', '2000-01-01 ' || NEW.start_time) IS NOT NEW.start_time
              OR strftime('%H:%M', '2000-01-01 ' || NEW.end_time) IS NOT NEW.end_time
              OR substr(NEW.start_time, 4, 2) NOT IN ('00', '30')
              OR substr(NEW.end_time, 4, 2) NOT IN ('00', '30')
              OR (SELECT is_active FROM staff_or_resources WHERE resource_id = NEW.resource_id) != 1
              OR NEW.end_time <= NEW.start_time
              OR (CAST(substr(NEW.end_time, 1, 2) AS INTEGER) * 60 + CAST(substr(NEW.end_time, 4, 2) AS INTEGER))
                 - (CAST(substr(NEW.start_time, 1, 2) AS INTEGER) * 60 + CAST(substr(NEW.start_time, 4, 2) AS INTEGER))
                 != NEW.duration_minutes
              OR NEW.duration_minutes != (SELECT duration_minutes FROM services WHERE service_id = NEW.service_id)
            BEGIN
                SELECT RAISE(ABORT, 'invalid appointment date, time, or duration');
            END"""
        )
        conn.execute(
            """CREATE TRIGGER IF NOT EXISTS appointments_validate_update
            BEFORE UPDATE OF resource_id, service_id, appt_date, start_time, end_time, duration_minutes ON appointments
            WHEN date(NEW.appt_date, '+0 days') IS NOT NEW.appt_date
              OR strftime('%H:%M', '2000-01-01 ' || NEW.start_time) IS NOT NEW.start_time
              OR strftime('%H:%M', '2000-01-01 ' || NEW.end_time) IS NOT NEW.end_time
              OR substr(NEW.start_time, 4, 2) NOT IN ('00', '30')
              OR substr(NEW.end_time, 4, 2) NOT IN ('00', '30')
              OR (SELECT is_active FROM staff_or_resources WHERE resource_id = NEW.resource_id) != 1
              OR NEW.end_time <= NEW.start_time
              OR (CAST(substr(NEW.end_time, 1, 2) AS INTEGER) * 60 + CAST(substr(NEW.end_time, 4, 2) AS INTEGER))
                 - (CAST(substr(NEW.start_time, 1, 2) AS INTEGER) * 60 + CAST(substr(NEW.start_time, 4, 2) AS INTEGER))
                 != NEW.duration_minutes
              OR (NEW.service_id != OLD.service_id AND
                  NEW.duration_minutes != (SELECT duration_minutes FROM services WHERE service_id = NEW.service_id))
            BEGIN
                SELECT RAISE(ABORT, 'invalid appointment date, time, or duration');
            END"""
        )
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


@contextmanager
def transaction(conn):
    """All-or-nothing na transaction.

    BEGIN IMMEDIATE = kinukuha agad ang write lock, kaya yung check at yung
    insert/update ay hindi masisingitan ng ibang request sa gitna.
    Pag may nag-error kahit isang step, ROLLBACK lahat para walang kalahating save.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
