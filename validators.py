"""Input validation.

Dito chine-check ang lahat ng galing sa form BAGO pumasok sa database.
Bawat validate_* function ay nagbabalik ng (cleaned_data, errors):
kapag walang laman ang errors, ligtas nang gamitin ang cleaned_data.
"""
import re
from datetime import datetime, timedelta

import config

NAME_RE = re.compile(r"^[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ.\-' ]*$")
CONTACT_RE = re.compile(r"^\+?\d{7,13}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def clean_text(value):
    """Alisin ang sobrang space sa unahan, dulo, at gitna."""
    return " ".join((value or "").split())


def to_int(value):
    """Gawing int; kapag hindi kaya, None ang ibabalik imbes na mag-crash."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def parse_date(value):
    """'YYYY-MM-DD' -> date object, o None kapag hindi valid."""
    try:
        return datetime.strptime((value or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


# ---------------------------------------------------------------- clients
def validate_client(form):
    errors = []
    name = clean_text(form.get("name"))
    # tanggalin ang space, dash, at parentheses para pare-pareho ang format ng contact
    contact = re.sub(r"[\s\-().]", "", form.get("contact") or "")
    email = (form.get("email") or "").strip().lower()

    if not name:
        errors.append("Client name is required.")
    elif not 2 <= len(name) <= 100:
        errors.append("Client name must be 2 to 100 characters long.")
    elif not NAME_RE.match(name):
        errors.append("Client name may only contain letters, spaces, periods, hyphens and apostrophes.")

    if not contact:
        errors.append("Contact number is required.")
    elif not CONTACT_RE.match(contact):
        errors.append("Contact number must be 7 to 13 digits, for example 09171234567 or +639171234567.")

    if email and (len(email) > 100 or not EMAIL_RE.match(email)):
        errors.append("Email address is not valid.")

    return {"name": name, "contact": contact, "email": email}, errors


# ------------------------------------------------------------- resources
def validate_resource(form):
    errors = []
    name = clean_text(form.get("name"))
    role = (form.get("role") or "").strip()
    is_active = 1 if form.get("is_active") else 0  # checkbox: kapag wala sa form, ibig sabihin unchecked

    if not name:
        errors.append("Name is required.")
    elif not 2 <= len(name) <= 60:
        errors.append("Name must be 2 to 60 characters long.")

    if role not in config.RESOURCE_ROLES:
        errors.append("Please choose a valid role.")

    return {"name": name, "role": role, "is_active": is_active}, errors


# -------------------------------------------------------------- services
def validate_service(form):
    errors = []
    name = clean_text(form.get("name"))
    duration = to_int(form.get("duration_minutes"))

    if not name:
        errors.append("Service name is required.")
    elif not 2 <= len(name) <= 80:
        errors.append("Service name must be 2 to 80 characters long.")

    if duration not in config.SERVICE_DURATIONS:
        allowed = ", ".join(str(d) for d in config.SERVICE_DURATIONS)
        errors.append(f"Duration must be one of these (in minutes): {allowed}.")

    return {"name": name, "duration_minutes": duration}, errors


# ---------------------------------------------------------- appointments
def validate_schedule(form, now=None):
    """Check ng resource, date, at start time (gamit sa booking at reschedule)."""
    now = now or datetime.now()
    errors = []

    resource_id = to_int(form.get("resource_id"))
    if resource_id is None:
        errors.append("Please choose a staff member or resource.")

    appt_date = parse_date(form.get("appt_date"))
    start_time = (form.get("start_time") or "").strip()
    valid_time = start_time in config.time_slots()

    if appt_date is None:
        errors.append("Please enter a valid date.")
    if not valid_time:
        errors.append("Please choose a valid start time.")

    if appt_date is not None:
        today = now.date()
        if appt_date < today:
            errors.append("The date cannot be in the past.")
        elif appt_date > today + timedelta(days=config.MAX_DAYS_AHEAD):
            errors.append(f"Appointments can only be booked up to {config.MAX_DAYS_AHEAD} days ahead.")
        elif appt_date.weekday() in config.CLOSED_WEEKDAYS:
            errors.append("The clinic is closed on Sundays. Please choose another date.")
        elif appt_date == today and valid_time and start_time <= now.strftime("%H:%M"):
            errors.append("That time has already passed today. Please choose a later time.")

    data = {
        "resource_id": resource_id,
        "appt_date": appt_date.isoformat() if appt_date else None,
        "start_time": start_time if valid_time else None,
    }
    return data, errors


def validate_appointment(form, now=None):
    """Buong booking form: client, service, at schedule fields."""
    errors = []
    client_id = to_int(form.get("client_id"))
    service_id = to_int(form.get("service_id"))
    notes = (form.get("notes") or "").strip()

    if client_id is None:
        errors.append("Please choose a client.")
    if service_id is None:
        errors.append("Please choose a service.")
    if len(notes) > 300:
        errors.append("Notes must be 300 characters or fewer.")

    schedule, schedule_errors = validate_schedule(form, now)
    errors.extend(schedule_errors)

    data = {"client_id": client_id, "service_id": service_id, "notes": notes}
    data.update(schedule)
    return data, errors
