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
    appt_date      TEXT NOT NULL
        CHECK (appt_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    start_time     TEXT NOT NULL CHECK (start_time GLOB '[0-2][0-9]:[0-5][0-9]'),
    end_time       TEXT NOT NULL CHECK (end_time GLOB '[0-2][0-9]:[0-5][0-9]'),
    status         TEXT NOT NULL DEFAULT 'Pending'
        CHECK (status IN ('Pending', 'Confirmed', 'Completed', 'Cancelled')),
    notes          TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    CHECK (end_time > start_time)
);

-- pampabilis ng conflict check at ng listing
CREATE INDEX idx_appt_resource_slot ON appointments (resource_id, appt_date, start_time);
CREATE INDEX idx_appt_date_status   ON appointments (appt_date, status);

-- huling depensa laban sa double booking: kahit makalusot sa Python check,
-- hindi papayagan ng database yung dalawang active na may parehong start time
-- yung Cancelled hindi kasama, kaya nababakante agad ang slot
CREATE UNIQUE INDEX uq_active_slot
    ON appointments (resource_id, appt_date, start_time)
    WHERE status != 'Cancelled';

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
