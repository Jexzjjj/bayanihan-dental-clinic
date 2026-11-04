"""Business logic ng scheduling: double-booking check, booking, reschedule, status.

Walang Flask dito, puro Python at SQL lang, para madaling i-test.
Lahat ng SQL gumagamit ng ? placeholders (parameterized), hindi kailanman string formatting.
"""
from datetime import datetime, timedelta

import config
from db import transaction
from formatting import fmt_date, fmt_time


class ScheduleError(Exception):
    """Business rule error. Yung message mismo ang ipapakita sa user."""


def add_minutes(hhmm, minutes):
    """'10:00' + 90 -> '11:30'."""
    result = datetime.strptime(hhmm, "%H:%M") + timedelta(minutes=minutes)
    return result.strftime("%H:%M")


def minutes_between(start, end):
    delta = datetime.strptime(end, "%H:%M") - datetime.strptime(start, "%H:%M")
    return int(delta.total_seconds() // 60)


# --------------------------------------------------------- conflict check
def find_conflict(conn, resource_id, appt_date, start_time, end_time, exclude_id=None):
    """Hanapin kung may active appointment na nag-o-overlap sa hinihinging slot.

    Overlap rule: bago_start < luma_end AND bago_end > luma_start.
    Kaya pwede ang back-to-back (10:00-11:00 tapos 11:00-12:00).
    Yung Cancelled hindi kasama, kaya nababakante agad ang slot niya.
    exclude_id = yung appointment na ina-reschedule, para hindi mag-conflict sa sarili.
    """
    sql = """
        SELECT a.appointment_id, a.start_time, a.end_time, a.status,
               r.name AS resource_name
        FROM appointments a
        JOIN staff_or_resources r ON r.resource_id = a.resource_id
        WHERE a.resource_id = ?
          AND a.appt_date = ?
          AND a.status != 'Cancelled'
          AND ? < a.end_time
          AND ? > a.start_time
    """
    params = [resource_id, appt_date, start_time, end_time]
    if exclude_id is not None:
        sql += " AND a.appointment_id != ?"
        params.append(exclude_id)
    sql += " ORDER BY a.start_time LIMIT 1"
    return conn.execute(sql, params).fetchone()


def booked_slots(conn, resource_id, appt_date, exclude_id=None):
    """Lahat ng active na booking ng isang resource sa isang araw (pang-display sa booking page)."""
    sql = """
        SELECT a.appointment_id, a.start_time, a.end_time, a.status,
               c.name AS client_name, s.name AS service_name
        FROM appointments a
        JOIN clients c ON c.client_id = a.client_id
        JOIN services s ON s.service_id = a.service_id
        WHERE a.resource_id = ?
          AND a.appt_date = ?
          AND a.status != 'Cancelled'
    """
    params = [resource_id, appt_date]
    if exclude_id is not None:
        sql += " AND a.appointment_id != ?"
        params.append(exclude_id)
    sql += " ORDER BY a.start_time"
    return conn.execute(sql, params).fetchall()


def _conflict_message(conflict, appt_date):
    return (
        f"{conflict['resource_name']} is already booked from {fmt_time(conflict['start_time'])} "
        f"to {fmt_time(conflict['end_time'])} on {fmt_date(appt_date)}. "
        "Please choose another time or another staff member."
    )


def _slot_label(appt_date, start_time, end_time, resource_name):
    return f"{fmt_date(appt_date)}, {fmt_time(start_time)} - {fmt_time(end_time)} with {resource_name}"


def _log(conn, appointment_id, action, old_value, new_value):
    """Magsulat sa appointment_history; kasama ito sa parehong transaction."""
    conn.execute(
        "INSERT INTO appointment_history (appointment_id, action, old_value, new_value) "
        "VALUES (?, ?, ?, ?)",
        (appointment_id, action, old_value, new_value),
    )


def _check_closing_time(service, end_time):
    if end_time > config.CLOSE_TIME:
        raise ScheduleError(
            f"{service['name']} takes {service['duration_minutes']} minutes and would end after "
            f"closing time ({fmt_time(config.CLOSE_TIME)}). Please choose an earlier start time."
        )


# ---------------------------------------------------------------- booking
def book_appointment(conn, client_id, resource_id, service_id, appt_date, start_time, notes=""):
    """Mag-book ng bagong appointment. Ibinabalik ang appointment_id."""
    # buong flow nasa isang transaction: check -> insert -> history
    with transaction(conn):
        client = conn.execute("SELECT name FROM clients WHERE client_id = ?", (client_id,)).fetchone()
        if client is None:
            raise ScheduleError("The selected client does not exist.")

        resource = conn.execute(
            "SELECT name, is_active FROM staff_or_resources WHERE resource_id = ?", (resource_id,)
        ).fetchone()
        if resource is None or not resource["is_active"]:
            raise ScheduleError("The selected staff or resource is not available.")

        service = conn.execute(
            "SELECT name, duration_minutes FROM services WHERE service_id = ?", (service_id,)
        ).fetchone()
        if service is None:
            raise ScheduleError("The selected service does not exist.")

        # end time kinukuha sa duration ng service, hindi galing sa user
        end_time = add_minutes(start_time, service["duration_minutes"])
        _check_closing_time(service, end_time)

        # i-check muna kung may conflict bago mag-insert
        conflict = find_conflict(conn, resource_id, appt_date, start_time, end_time)
        if conflict is not None:
            raise ScheduleError(_conflict_message(conflict, appt_date))

        cursor = conn.execute(
            "INSERT INTO appointments "
            "(client_id, resource_id, service_id, appt_date, start_time, end_time, status, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, 'Pending', ?)",
            (client_id, resource_id, service_id, appt_date, start_time, end_time, notes or None),
        )
        appointment_id = cursor.lastrowid
        _log(conn, appointment_id, "Created", None,
             _slot_label(appt_date, start_time, end_time, resource["name"]))
    return appointment_id


# ------------------------------------------------------------- reschedule
def reschedule_appointment(conn, appointment_id, resource_id, appt_date, start_time):
    """Ilipat ang date/time/staff ng appointment.

    Isang transaction: check status -> check conflict -> update -> history.
    Pag pumalya kahit isang step, rollback lahat at walang magbabago sa appointment.
    """
    with transaction(conn):
        current = conn.execute(
            """
            SELECT a.*, s.name AS service_name, s.duration_minutes, r.name AS resource_name
            FROM appointments a
            JOIN services s ON s.service_id = a.service_id
            JOIN staff_or_resources r ON r.resource_id = a.resource_id
            WHERE a.appointment_id = ?
            """,
            (appointment_id,),
        ).fetchone()
        if current is None:
            raise ScheduleError("Appointment not found.")
        if current["status"] not in config.ACTIVE_STATUSES:
            raise ScheduleError(f"A {current['status']} appointment cannot be rescheduled.")

        target = conn.execute(
            "SELECT name, is_active FROM staff_or_resources WHERE resource_id = ?", (resource_id,)
        ).fetchone()
        if target is None or not target["is_active"]:
            raise ScheduleError("The selected staff or resource is not available.")

        end_time = add_minutes(start_time, current["duration_minutes"])
        _check_closing_time({"name": current["service_name"],
                             "duration_minutes": current["duration_minutes"]}, end_time)

        if (current["resource_id"], current["appt_date"], current["start_time"]) == \
                (resource_id, appt_date, start_time):
            raise ScheduleError("The appointment is already scheduled at that date, time and staff.")

        # exclude_id para hindi mag-conflict yung appointment sa sarili niyang lumang slot
        conflict = find_conflict(conn, resource_id, appt_date, start_time, end_time,
                                 exclude_id=appointment_id)
        if conflict is not None:
            raise ScheduleError(_conflict_message(conflict, appt_date))

        conn.execute(
            "UPDATE appointments SET resource_id = ?, appt_date = ?, start_time = ?, end_time = ? "
            "WHERE appointment_id = ?",
            (resource_id, appt_date, start_time, end_time, appointment_id),
        )
        _log(
            conn, appointment_id, "Rescheduled",
            _slot_label(current["appt_date"], current["start_time"], current["end_time"],
                        current["resource_name"]),
            _slot_label(appt_date, start_time, end_time, target["name"]),
        )


# ----------------------------------------------------------------- status
def change_status(conn, appointment_id, new_status):
    """Palitan ang status ayon sa config.STATUS_FLOW. Ibinabalik ang (luma, bago)."""
    if new_status not in config.STATUSES:
        raise ScheduleError("Unknown status.")

    with transaction(conn):
        current = conn.execute(
            "SELECT status, appt_date FROM appointments WHERE appointment_id = ?", (appointment_id,)
        ).fetchone()
        if current is None:
            raise ScheduleError("Appointment not found.")

        old_status = current["status"]
        if new_status not in config.STATUS_FLOW[old_status]:
            raise ScheduleError(f"An appointment that is {old_status} cannot be changed to {new_status}.")

        # hindi pwedeng i-complete ang appointment na hindi pa nangyayari
        if new_status == "Completed" and current["appt_date"] > datetime.now().date().isoformat():
            raise ScheduleError("A future appointment cannot be marked as Completed.")

        conn.execute("UPDATE appointments SET status = ? WHERE appointment_id = ?",
                     (new_status, appointment_id))
        _log(conn, appointment_id, "Status changed", old_status, new_status)
    return old_status, new_status


def delete_appointment(conn, appointment_id):
    """Cancelled lang ang pwedeng i-delete; yung history niya kasamang mabubura (ON DELETE CASCADE)."""
    cursor = conn.execute(
        "DELETE FROM appointments WHERE appointment_id = ? AND status = 'Cancelled'",
        (appointment_id,),
    )
    if cursor.rowcount == 0:
        raise ScheduleError("Only Cancelled appointments can be deleted. Cancel it first.")


# --------------------------------------------------------------- day grid
def build_day_grid(resources, appointments):
    """Gawing rows ng table (oras x staff) para sa day-view schedule.

    Bawat cell ay isa sa tatlo: "empty" (libre), "appt" (may rowspan),
    o "covered" (kasama sa rowspan ng appointment sa taas, kaya walang i-render).
    """
    starts = {}
    covered = set()
    for appt in appointments:
        span = minutes_between(appt["start_time"], appt["end_time"]) // config.SLOT_MINUTES
        starts[(appt["resource_id"], appt["start_time"])] = (appt, span)
        for step in range(1, span):
            covered.add((appt["resource_id"], add_minutes(appt["start_time"], step * config.SLOT_MINUTES)))

    rows = []
    for slot in config.time_slots():
        cells = []
        for resource in resources:
            key = (resource["resource_id"], slot)
            if key in covered:
                cells.append({"kind": "covered"})
            elif key in starts:
                appt, span = starts[key]
                cells.append({"kind": "appt", "appt": appt, "span": span})
            else:
                cells.append({"kind": "empty", "resource_id": resource["resource_id"]})
        rows.append({"time": slot, "cells": cells})
    return rows
