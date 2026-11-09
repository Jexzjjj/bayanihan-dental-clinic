-- Schema ng Appointment Scheduling System (SQLite)
-- Tatakbo ito kapag nag-init ng database; buburahin muna yung lumang tables.

PRAGMA foreign_keys = ON;

-- child tables muna ang dina-drop para hindi mag-error sa foreign key
DROP TABLE IF EXISTS appointment_history;
DROP TABLE IF EXISTS appointments;
DROP TABLE IF EXISTS services;
DROP TABLE IF EXISTS staff_or_resources;
DROP TABLE IF EXISTS clients;

-- mga pasyente / customers
CREATE TABLE clients (
    client_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL CHECK (length(trim(name)) >= 2),
    contact    TEXT NOT NULL,
    email      TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX idx_clients_name ON clients (name);

-- staff o resource na binu-book (dentist, hygienist, room)
CREATE TABLE staff_or_resources (
    resource_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    role        TEXT NOT NULL,
    is_active   INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
);

-- listahan ng services; dito nanggagaling yung duration ng appointment
CREATE TABLE services (
    service_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT NOT NULL UNIQUE,
    duration_minutes INTEGER NOT NULL
        CHECK (duration_minutes > 0 AND duration_minutes % 30 = 0)
);

-- main table: date at time ay naka-text ("YYYY-MM-DD" at "HH:MM")
-- para gumana nang tama ang pag-compare at pag-sort gamit ang < at >
CREATE TABLE appointments (
    appointment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id      INTEGER NOT NULL REFERENCES clients (client_id) ON DELETE RESTRICT,
    resource_id    INTEGER NOT NULL REFERENCES staff_or_resources (resource_id) ON DELETE RESTRICT,
    service_id     INTEGER NOT NULL REFERENCES services (service_id) ON DELETE RESTRICT,
    appt_date      TEXT NOT NULL CHECK (
        appt_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
        AND date(appt_date, '+0 days') = appt_date
    ),
    start_time     TEXT NOT NULL CHECK (
        start_time GLOB '[0-2][0-9]:[0-5][0-9]'
        AND substr(start_time, 1, 2) BETWEEN '00' AND '23'
        AND substr(start_time, 4, 2) IN ('00', '30')
    ),
    end_time       TEXT NOT NULL CHECK (
        end_time GLOB '[0-2][0-9]:[0-5][0-9]'
        AND substr(end_time, 1, 2) BETWEEN '00' AND '23'
        AND substr(end_time, 4, 2) IN ('00', '30')
    ),
    duration_minutes INTEGER NOT NULL DEFAULT 30
        CHECK (duration_minutes BETWEEN 30 AND 240 AND duration_minutes % 30 = 0),
    status         TEXT NOT NULL DEFAULT 'Pending'
        CHECK (status IN ('Pending', 'Confirmed', 'Completed', 'Cancelled')),
    notes          TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    CHECK (end_time > start_time)
);

-- pampabilis ng conflict check at ng listing
CREATE INDEX idx_appt_resource_slot ON appointments (resource_id, appt_date, start_time);
CREATE INDEX idx_appt_date_status   ON appointments (appt_date, status);
CREATE INDEX idx_appt_client_date   ON appointments (client_id, appt_date);
CREATE INDEX idx_appt_service       ON appointments (service_id);

-- Fast exact-start guard plus triggers below for all overlapping active slots.
CREATE UNIQUE INDEX uq_active_slot
    ON appointments (resource_id, appt_date, start_time)
    WHERE status != 'Cancelled';

-- SQLite has no exclusion constraint, so enforce interval overlap at the
-- database layer too. These guards also cover direct SQL writes and reschedules.
CREATE TRIGGER appointments_no_overlap_insert
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
END;

CREATE TRIGGER appointments_no_overlap_update
BEFORE UPDATE OF resource_id, appt_date, start_time, end_time, status ON appointments
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
END;

-- Verify true calendar/time values and the appointment's stored duration.
CREATE TRIGGER appointments_validate_insert
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
END;

CREATE TRIGGER appointments_validate_update
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
END;

-- audit trail: dito nilalagay kung ano ang nagbago at kailan
CREATE TABLE appointment_history (
    history_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    appointment_id INTEGER NOT NULL REFERENCES appointments (appointment_id) ON DELETE CASCADE,
    action         TEXT NOT NULL,
    old_value      TEXT,
    new_value      TEXT,
    changed_at     TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX idx_history_appt ON appointment_history (appointment_id);
