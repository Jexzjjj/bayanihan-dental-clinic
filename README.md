# Appointment Scheduling System

Group 1 project. A web app for booking, managing and tracking appointments, built for a dental clinic.

**Stack:** Python, Flask, HTML (Jinja templates), SQL, SQLite. No other framework or database.

## How to run

```bash
pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000. If the configured database does not exist, the app creates it with demo data. Existing database files are preserved and receive only safe, additive schema migrations.

### Environment configuration

| Variable | Purpose |
|---|---|
| `APPOINTMENT_DB` | SQLite database path (defaults to `appointments.db` in the project directory) |
| `SECRET_KEY` | Stable, random secret for signed sessions; set this for deployments and shared workers |
| `FLASK_DEBUG` | Enables Flask debug mode only when explicitly set to `1`, `true`, or `yes` |
| `SESSION_COOKIE_SECURE` | Set to `1` behind HTTPS so browsers send the session cookie only over TLS |

The app generates a random secret for local development if `SECRET_KEY` is unset. That local session key changes on restart; configure a persistent secret before deployment. Session cookies are HTTP-only and use `SameSite=Lax`.

| Command | What it does |
|---|---|
| `python app.py` | Start the app (debug mode is off unless explicitly enabled) |
| `flask --app app init-db` | **Destructively reset** the configured database with sample data |
| `flask --app app init-db --no-seed` | **Destructively reset** the configured database to an empty database |
| `python -m unittest -v` | Run the automated tests (uses a temporary database) |

Back up the configured database before either `init-db` command. Do not point tests at the working clinic database.

## Required features and where they live

| Requirement | Where |
|---|---|
| Register clients | `/clients/new` |
| Create appointments (date, time, service) | `/appointments/new` |
| Display by date and status, search by client name | `/appointments` (filters: client name, date, status, staff) |
| Reschedule | `/appointments/<id>/edit` |
| Cancel | "Cancel" button in any appointment row |
| Update status | Buttons in each row: Confirm, Mark Completed, Cancel, Set to Pending |
| Prevent double booking | `scheduling.find_conflict()` plus SQLite overlap triggers and a unique index |
| Cancelled does not block the slot | The conflict query ignores `status = 'Cancelled'` |

## Pages

1. Dashboard (`/`): today's appointments, counts by status, pending list
2. Day Schedule (`/schedule`): grid with time as rows and staff as columns
3. Appointments (`/appointments`): list with filters and status actions
4. New Booking (`/appointments/new`): with a live "Already booked" panel
5. Reschedule (`/appointments/<id>/edit`)
6. Appointment History (`/appointments/<id>/history`)
7. Clients (`/clients`, new, edit, detail page)
8. Staff and Rooms (`/resources`, new, edit)
9. Services (`/services`, new, edit)

CRUD is complete for clients, staff/resources, services and appointments (appointments can be deleted once cancelled).

## How HTML, Flask, Python and SQL work together

Example: booking an appointment.

```
Browser            templates/appointment_new.html   the form, POST /appointments/new
   |
Flask route        app.py  appointments_new()        receives request.form
   |
Python validation  validators.validate_appointment() checks format, date, time
   |
Python logic       scheduling.book_appointment()     one transaction
   |
SQL / SQLite       find_conflict() SELECT, then INSERT INTO appointments,
   |               then INSERT INTO appointment_history   (all with ? placeholders)
   |
Flask              flash("Appointment #N was booked ...") and redirect
   |
Jinja template     templates/appointments.html       the list, with the message shown
```

## Files

```
app.py            Flask routes (grouped by section)
config.py         Clinic hours, statuses, allowed status changes
db.py             Connection, init, transaction() helper
scheduling.py     Double-booking check, booking, reschedule, status, day grid
validators.py     All input validation
formatting.py     Date and time display helpers
seed.py           Sample data (dates are relative to today)
schema.sql        CREATE TABLE statements
test_app.py       Automated tests using disposable databases
templates/        Jinja HTML pages
static/           style.css, app.js, favicon.svg
```

## Database design

```
clients (client_id PK, name, contact, email, created_at)
staff_or_resources (resource_id PK, name UNIQUE, role, is_active)
services (service_id PK, name UNIQUE, duration_minutes)
appointments (appointment_id PK,
              client_id FK -> clients, resource_id FK -> staff_or_resources,
              service_id FK -> services, duration_minutes snapshot,
              appt_date, start_time, end_time, status CHECK(...), notes, created_at)
appointment_history (history_id PK, appointment_id FK -> appointments ON DELETE CASCADE,
                     action, old_value, new_value, changed_at)
```

- Foreign keys are enforced (`PRAGMA foreign_keys = ON` on every connection) and use `ON DELETE RESTRICT`, so records that are still in use cannot be deleted by accident.
- `CHECK` constraints and triggers protect real calendar dates, 24-hour half-hour times, `end_time > start_time`, service duration snapshots, and valid statuses.
- Dates are stored as `YYYY-MM-DD` and times as 24-hour `HH:MM` text, so `<`, `>` and `ORDER BY` work correctly.
- Listings use `JOIN` across all four tables.

## The double-booking rule

Two active appointments for the same staff or resource cannot overlap.

```sql
SELECT ... FROM appointments
WHERE resource_id = ? AND appt_date = ?
  AND status != 'Cancelled'
  AND ? < end_time      -- new start is before the old end
  AND ? > start_time    -- new end is after the old start
  AND appointment_id != ?   -- only when rescheduling, so it ignores itself
```

- Overlaps are caught, not only identical start times (10:30 conflicts with an existing 10:00 to 11:00).
- Back-to-back bookings are allowed (10:00 to 11:00, then 11:00 to 12:00).
- Cancelled appointments are ignored, so cancelling frees the slot immediately.
- The end time is computed from the service duration, never typed by the user.
- Each appointment stores a duration snapshot. Editing a service affects future bookings; existing appointments retain their original duration and end time, including when rescheduled.
- A partial unique index (`uq_active_slot`) quickly rejects identical start times.
- SQLite triggers reject every overlapping active interval on inserts and schedule updates, including direct SQL writes. Cancelled appointments are excluded.
- Existing databases are checked for overlaps before these guards are installed. Startup stops with a clear error if overlaps already exist; the migration does not alter appointments to resolve them.

## The transaction beyond basic CRUD

`scheduling.reschedule_appointment()` runs inside `db.transaction()`:

1. `BEGIN IMMEDIATE` (takes the write lock, so two requests cannot both pass the check)
2. Load the appointment and make sure it is Pending or Confirmed
3. Check the new staff is active and the booking ends before closing time
4. Check for conflicts (excluding itself)
5. `UPDATE appointments`
6. `INSERT INTO appointment_history` (old slot and new slot)
7. `COMMIT`, or `ROLLBACK` if any step failed

Booking (`book_appointment`) and status changes (`change_status`) use the same pattern. The test `test_failure_in_middle_rolls_everything_back` forces the last step to fail and checks that the appointment is unchanged.

## Validation and error handling

- Required fields, formats and lengths for every form (`validators.py`)
- Date not in the past, not a Sunday, not more than a year ahead; time must be a valid slot and not already passed today
- Booking cannot run past closing time
- Status changes follow `STATUS_FLOW` in `config.py` (Completed and Cancelled are final; an appointment cannot be completed before its scheduled end time)
- Cannot delete a client, staff or service that still has appointment records
- Duplicate client (same name and contact), staff name and service name are rejected
- Every SQL statement uses `?` placeholders, including the search `LIKE` (with `%` and `_` escaped)
- `try/except sqlite3.Error` around writes, plus custom 400, 404, 405 and 500 pages
- CSRF token on every POST form; redirect targets are restricted to local paths
- Random session secret fallback; debug mode is off by default; session cookies are HTTP-only and `SameSite=Lax`
- Runtime database, SQLite journals, environment files, Python caches, and virtual environments are ignored by Git
- Forms use `novalidate` on purpose, so the server's messages are the ones the user sees

HTML templates use Jinja autoescaping. JavaScript renders API values using `textContent`, and write queries use parameterized SQL. The app does not implement user accounts or staff authorization; deploy it only in a trusted environment until authentication and role-based access are added.

Deleting a cancelled appointment is a permanent purge: its `appointment_history` rows are removed by the documented `ON DELETE CASCADE` relationship. Cancellation alone retains the appointment and its history.

## Demo script for the defense

1. **Dashboard:** show today's appointments and the counts by status.
2. **Register a client** with a bad contact number to show the error message, then a valid one.
3. **Book** the client with Dr. Maria Santos. Point at the "Already booked" panel on the right.
4. **Try the same slot again:** rejected with a clear message. Then try an overlapping time such as 30 minutes later: also rejected.
5. **Cancel** the first booking, then book the slot again: it works, because cancelled bookings do not block.
6. **Reschedule** an appointment into a taken slot: rejected, and the original stays. Then reschedule into a free slot and open **History** to show the audit row.
7. **Update status:** Pending to Confirmed to Completed on a past or today booking.
8. **Filters:** search by client name, filter by date and status.
9. **Day Schedule:** show the grid, click an empty slot to start a booking.
10. **Open the code:** follow one request from the form through `app.py`, `validators.py`, `scheduling.py` and the SQL.

## Likely questions from the panel

- **Why check overlap instead of the exact time?** A 60-minute service starting at 10:00 also occupies 10:30.
- **What stops two users booking at the same moment?** `BEGIN IMMEDIATE` plus the unique index.
- **Why store times as text?** `HH:MM` and `YYYY-MM-DD` sort and compare correctly as text in SQLite.
- **How do you prevent SQL injection?** Parameterized queries only; user input is never put into the SQL string.
- **Why can't a client be deleted?** Foreign keys use `RESTRICT` so appointment history is never orphaned.
- **What would you add next?** Login with staff roles, SMS or email reminders, recurring appointments.

## Rubric map

| Rubric item | Evidence |
|---|---|
| System functionality (25) | All required features, plus the day schedule and history |
| Frontend-backend integration (20) | The flow diagram above; Jinja templates, forms, `fetch` to `/api/booked-slots` |
| Database design and SQL (15) | 5 tables, FKs, CHECKs, indexes, JOINs, aggregate counts, transaction |
| Code quality and organization (10) | Separate modules, one job per file, comments |
| Validation and error handling (10) | The section above, plus `test_app.py` |
| User interface and defense (20) | Responsive clinic styling, keyboard navigation, status colors, light/dark theme, demo script above |
