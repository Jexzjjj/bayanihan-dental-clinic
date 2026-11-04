"""Sample data para sa demo.

Relative sa araw ngayon ang mga date, kaya laging may laman ang dashboard
kahit anong araw mo patakbuhin. Parameterized din ang lahat ng insert.
"""
from datetime import date, timedelta

import config
from scheduling import add_minutes

CLIENTS = [
    ("Juan Dela Cruz", "09175550101", "juan.delacruz@example.com"),
    ("Maria Clara Ramos", "09175550102", "mclara.ramos@example.com"),
    ("Jose Miguel Mercado", "09175550103", None),
    ("Ana Liza Villanueva", "09175550104", "analiza.v@example.com"),
    ("Carlo Bautista", "09175550105", None),
    ("Angelica Dizon", "09175550106", "angelica.dizon@example.com"),
    ("Ramon Aquino", "09175550107", None),
    ("Grace Pascual", "09175550108", "grace.pascual@example.com"),
    ("Mark Anthony Salazar", "09175550109", None),
    ("Rowena Castillo", "09175550110", "rowena.castillo@example.com"),
]

RESOURCES = [
    ("Dr. Maria Santos", "Dentist", 1),
    ("Dr. Paolo Reyes", "Dentist", 1),
    ("Ms. Liza Garcia", "Dental Hygienist", 1),
    ("X-Ray Room", "Room / Equipment", 1),
]

SERVICES = [
    ("Consultation", 30),
    ("Oral Prophylaxis (Cleaning)", 60),
    ("Tooth Extraction", 60),
    ("Dental Filling (Pasta)", 60),
    ("Braces Adjustment", 30),
    ("Root Canal Treatment", 90),
    ("Teeth Whitening", 90),
    ("Panoramic X-Ray", 30),
]

# (day_offset, start, client#, resource#, service#, status, notes)
# numbers ay 1-based, ayon sa pagkakasunod sa taas
APPOINTMENTS = [
    (-3, "09:00", 1, 1, 1, "Completed", None),
    (-3, "10:00", 2, 3, 2, "Completed", None),
    (-3, "13:00", 3, 2, 3, "Completed", "Lower left molar"),
    (-2, "09:30", 4, 1, 4, "Completed", None),
    (-2, "14:00", 5, 2, 5, "Completed", None),
    (-1, "10:00", 6, 2, 6, "Completed", "Second session next month"),
    (-1, "15:00", 7, 1, 1, "Cancelled", "Client called, not feeling well"),
    (0, "08:30", 8, 1, 1, "Completed", None),
    (0, "09:00", 1, 3, 2, "Confirmed", None),
    (0, "10:00", 9, 2, 4, "Confirmed", None),
    (0, "11:00", 2, 1, 5, "Confirmed", None),
    (0, "13:30", 10, 1, 2, "Pending", "Asked for afternoon slot"),
    (0, "14:00", 3, 2, 6, "Pending", None),
    # cancelled ito, kaya naka-book ulit yung parehong slot sa susunod na row
    (0, "15:00", 4, 3, 2, "Cancelled", "Rescheduled by client"),
    (0, "15:00", 5, 3, 2, "Confirmed", None),
    (1, "09:00", 6, 1, 3, "Confirmed", None),
    (1, "09:00", 7, 2, 1, "Pending", None),
    (1, "10:30", 8, 3, 7, "Pending", None),
    (1, "13:00", 9, 4, 8, "Confirmed", "Needed for braces evaluation"),
    (1, "14:00", 10, 1, 4, "Pending", None),
    (2, "09:30", 1, 2, 2, "Pending", None),
    (2, "11:00", 2, 1, 6, "Confirmed", None),
    (2, "13:00", 3, 3, 2, "Pending", None),
    (3, "10:00", 4, 1, 1, "Pending", None),
    (3, "14:30", 5, 2, 5, "Pending", None),
    (5, "09:00", 6, 2, 7, "Pending", None),
]


def workday(today, offset):
    """today + offset, pero iwas Sunday (sarado ang clinic)."""
    day = today + timedelta(days=offset)
    if offset == 0:
        return day  # yung "today" hindi ginagalaw para laging may laman ang dashboard
    step = 1 if offset > 0 else -1
    while day.weekday() in config.CLOSED_WEEKDAYS:
        day += timedelta(days=step)
    return day


def load(conn, today=None):
    today = today or date.today()

    conn.executemany("INSERT INTO clients (name, contact, email) VALUES (?, ?, ?)", CLIENTS)
    conn.executemany(
        "INSERT INTO staff_or_resources (name, role, is_active) VALUES (?, ?, ?)", RESOURCES
    )
    conn.executemany("INSERT INTO services (name, duration_minutes) VALUES (?, ?)", SERVICES)

    for offset, start, client_no, resource_no, service_no, status, notes in APPOINTMENTS:
        duration = SERVICES[service_no - 1][1]
        cursor = conn.execute(
            "INSERT INTO appointments "
            "(client_id, resource_id, service_id, appt_date, start_time, end_time, status, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (client_no, resource_no, service_no, workday(today, offset).isoformat(),
             start, add_minutes(start, duration), status, notes),
        )
        conn.execute(
            "INSERT INTO appointment_history (appointment_id, action, old_value, new_value) "
            "VALUES (?, 'Created', NULL, 'Sample data')",
            (cursor.lastrowid,),
        )
