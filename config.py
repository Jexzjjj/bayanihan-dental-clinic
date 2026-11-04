"""Mga setting at constants ng buong app.

Dito lahat ng pwedeng baguhin (oras ng clinic, status, atbp.) para iisang
lugar lang ang kailangan i-edit kapag may nagbago.
"""
import os
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CLINIC_NAME = "Bayanihan Dental Clinic"

# pang-sign ng session at flash messages; sa totoong deployment, palitan ito
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-secret-palitan-mo-ito")

# dito naka-save yung SQLite file; pwedeng palitan gamit environment variable
DB_PATH = os.environ.get("APPOINTMENT_DB", os.path.join(BASE_DIR, "appointments.db"))

# oras ng clinic, naka 24-hour "HH:MM" para madaling i-compare as text
OPEN_TIME = "08:00"
CLOSE_TIME = "17:00"
SLOT_MINUTES = 30
CLOSED_WEEKDAYS = (6,)  # 6 = Sunday sa Python
MAX_DAYS_AHEAD = 365

STATUSES = ("Pending", "Confirmed", "Completed", "Cancelled")

# ito yung mga status na "active" pa, ibig sabihin pwede pang i-reschedule
ACTIVE_STATUSES = ("Pending", "Confirmed")

# kung anong status lang ang pwedeng puntahan mula sa current status
# yung Completed at Cancelled, final na, hindi na mababago
STATUS_FLOW = {
    "Pending": ("Confirmed", "Cancelled"),
    "Confirmed": ("Pending", "Completed", "Cancelled"),
    "Completed": (),
    "Cancelled": (),
}

# label ng button sa bawat status change
ACTION_LABELS = {
    "Pending": "Set to Pending",
    "Confirmed": "Confirm",
    "Completed": "Mark Completed",
    "Cancelled": "Cancel",
}

# yung "default" susunod na hakbang mula sa current status; ito lang ang ipapakitang
# button agad sa row, yung iba (undo, cancel, reschedule, history...) nasa "More" menu na lang
PRIMARY_NEXT_STATUS = {
    "Pending": "Confirmed",
    "Confirmed": "Completed",
}

RESOURCE_ROLES = ("Dentist", "Dental Hygienist", "Room / Equipment")

# duration ng service, laging multiple ng slot (30 mins) para sakto sa grid
SERVICE_DURATIONS = tuple(range(30, 241, 30))


def time_slots():
    """Lahat ng pwedeng start time: 08:00, 08:30, ... hanggang bago mag-close."""
    slots = []
    current = datetime.strptime(OPEN_TIME, "%H:%M")
    closing = datetime.strptime(CLOSE_TIME, "%H:%M")
    while current < closing:
        slots.append(current.strftime("%H:%M"))
        current += timedelta(minutes=SLOT_MINUTES)
    return slots
