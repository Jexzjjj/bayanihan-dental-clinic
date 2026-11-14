"""Appointment Scheduling System (Flask).

Daloy ng bawat request:
    HTML form (browser) -> Flask route (dito) -> Python validation/logic
    -> parameterized SQL -> SQLite -> Jinja template -> HTML pabalik sa browser

Hatiin ng file:
    1. Setup at helpers
    2. Dashboard at day schedule
    3. Clients (CRUD)
    4. Staff / Resources (CRUD)
    5. Services (CRUD)
    6. Appointments (book, list, reschedule, status, delete, history)
    7. Error pages
"""
import os
import secrets
import sqlite3
from datetime import date, datetime, timedelta

import click
from flask import Flask, abort, flash, jsonify, redirect, render_template, request, session, url_for

import config
import db
import scheduling
import validators
from formatting import fmt_date, fmt_time

# =====================================================================
# 1. SETUP AT HELPERS
# =====================================================================
app = Flask(__name__)
app.config.update(
    SECRET_KEY=config.SECRET_KEY,
    DATABASE=config.DB_PATH,
    DEBUG=config.DEBUG,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=config.SESSION_COOKIE_SECURE,
)
db.init_app(app)

DB_ERROR = "A database error occurred and nothing was saved. Please try again."

# lahat ng appointment listing ay gumagamit ng join na ito para iisa lang ang query na babantayan
APPT_SELECT = """
    SELECT a.appointment_id, a.appt_date, a.start_time, a.end_time, a.status, a.notes,
           a.service_id,
           c.client_id, c.name AS client_name, c.contact,
           r.resource_id, r.name AS resource_name,
           s.name AS service_name, a.duration_minutes
    FROM appointments a
    JOIN clients c ON c.client_id = a.client_id
    JOIN staff_or_resources r ON r.resource_id = a.resource_id
    JOIN services s ON s.service_id = a.service_id
"""

# mga value na magagamit sa lahat ng template
app.jinja_env.globals.update(
    CLINIC_NAME=config.CLINIC_NAME,
    STATUSES=config.STATUSES,
    ACTIVE_STATUSES=config.ACTIVE_STATUSES,
    STATUS_FLOW=config.STATUS_FLOW,
    ACTION_LABELS=config.ACTION_LABELS,
    PRIMARY_NEXT_STATUS=config.PRIMARY_NEXT_STATUS,
    today=lambda: date.today().isoformat(),
    current_time=lambda: datetime.now().strftime("%H:%M"),
)
app.add_template_filter(fmt_date, "fmt_date")
app.add_template_filter(fmt_time, "fmt_time")



def ensure_database():
    """Initialize an empty database; never overwrite an existing installation."""
    path = app.config["DATABASE"]

    conn = sqlite3.connect(path)
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    finally:
        conn.close()

    required_tables = {
        "clients",
        "staff_or_resources",
        "services",
        "appointments",
    }

    if not tables:
        # Genuinely empty database: safe to initialize.
        db.init_db(path, seed=True)
    elif required_tables.issubset(tables):
        # Existing installation: apply non-destructive migrations.
        db.migrate_db(path)
    else:
        missing = required_tables - tables
        raise RuntimeError(
            f"Database schema is incomplete. Missing tables: {', '.join(sorted(missing))}. "
            "Database was not modified."
        )



ensure_database()


@app.cli.command("init-db")
@click.option("--seed/--no-seed", default=True, help="Maglagay ng sample data.")
def init_db_command(seed):
    """Burahin at gawing bago ang database."""
    db.init_db(app.config["DATABASE"], seed=seed)
    click.echo("Database reset done.")


# ---- CSRF protection: may hidden token ang bawat POST form para hindi mapeke ng ibang site
@app.template_global()
def csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(16)
    return session["csrf_token"]


@app.before_request
def check_csrf():
    if request.method == "POST":
        sent = request.form.get("csrf_token", "")
        expected = session.get("csrf_token", "")
        if not expected or not secrets.compare_digest(sent, expected):
            abort(400)


def flash_errors(errors):
    for message in errors:
        flash(message, "error")


def safe_next(default):
    """Balik sa dating page pagkatapos ng action, pero relative path lang ang pinapayagan."""
    target = request.form.get("next", "")
    if target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return default


def like_pattern(text):
    """I-escape ang % at _ para literal silang hahanapin sa LIKE search."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def fetch_or_404(conn, sql, params):
    row = conn.execute(sql, params).fetchone()
    if row is None:
        abort(404)
    return row


def slots_context(conn, resource_id_text, date_text, exclude_id=None):
    """Datos para sa 'Already booked' panel: (heading, rows, note)."""
    resource_id = validators.to_int(resource_id_text)
    appt_date = validators.parse_date(date_text)
    resource = None
    if resource_id is not None:
        resource = conn.execute(
            "SELECT name FROM staff_or_resources WHERE resource_id = ?", (resource_id,)
        ).fetchone()
    if resource is None or appt_date is None:
        return "Pick a staff member and a date to see what is already booked.", [], ""

    rows = scheduling.booked_slots(conn, resource_id, appt_date.isoformat(), exclude_id)
    heading = f"{resource['name']}, {fmt_date(appt_date.isoformat())}"
    note = "" if rows else "Nothing booked yet. All time slots are open."
    return heading, rows, note


# =====================================================================
# 2. DASHBOARD AT DAY SCHEDULE
# =====================================================================
@app.route("/")
def dashboard():
    conn = db.get_db()
    today = date.today()

    todays = conn.execute(
        APPT_SELECT + " WHERE a.appt_date = ? ORDER BY a.start_time, r.name",
        (today.isoformat(),),
    ).fetchall()

    # bilang ng appointments ngayon, hinati sa status
    counts = {status: 0 for status in config.STATUSES}
    for row in conn.execute(
        "SELECT status, COUNT(*) AS total FROM appointments WHERE appt_date = ? GROUP BY status",
        (today.isoformat(),),
    ):
        counts[row["status"]] = row["total"]

    next_week = conn.execute(
        "SELECT COUNT(*) FROM appointments "
        "WHERE appt_date > ? AND appt_date <= ? AND status IN ('Pending', 'Confirmed')",
        (today.isoformat(), (today + timedelta(days=7)).isoformat()),
    ).fetchone()[0]

    # mga naghihintay pa ng confirmation, pinakamalapit na date ang una
    waiting = conn.execute(
        APPT_SELECT + " WHERE a.status = 'Pending' AND a.appt_date >= ? "
        "ORDER BY a.appt_date, a.start_time LIMIT 8",
        (today.isoformat(),),
    ).fetchall()

    return render_template(
        "dashboard.html", todays=todays, counts=counts, next_week=next_week,
        waiting=waiting, next_url=request.full_path.rstrip("?"),
    )


@app.route("/schedule")
def schedule_day():
    """Day view: oras sa rows, staff sa columns."""
    conn = db.get_db()
    day = validators.parse_date(request.args.get("date")) or date.today()
    day_text = day.isoformat()

    # active na resources, pati yung inactive na may booking pa sa araw na ito
    resources = conn.execute(
        "SELECT resource_id, name, role FROM staff_or_resources "
        "WHERE is_active = 1 OR resource_id IN "
        "(SELECT resource_id FROM appointments WHERE appt_date = ? AND status != 'Cancelled') "
        "ORDER BY name",
        (day_text,),
    ).fetchall()
    appointments = conn.execute(
        APPT_SELECT + " WHERE a.appt_date = ? AND a.status != 'Cancelled'", (day_text,)
    ).fetchall()

    grid = scheduling.build_day_grid(resources, appointments)
    return render_template(
        "schedule.html", day=day_text, resources=resources, grid=grid,
        prev_day=(day - timedelta(days=1)).isoformat(),
        next_day=(day + timedelta(days=1)).isoformat(),
        closed=day.weekday() in config.CLOSED_WEEKDAYS,
        can_book=day >= date.today() and day.weekday() not in config.CLOSED_WEEKDAYS,
    )


# =====================================================================
# 3. CLIENTS
# =====================================================================
@app.route("/clients")
def clients_list():
    conn = db.get_db()
    q = request.args.get("q", "").strip()

    sql = (
        "SELECT c.client_id, c.name, c.contact, c.email, COUNT(a.appointment_id) AS appt_count "
        "FROM clients c LEFT JOIN appointments a ON a.client_id = c.client_id"
    )
    params = []
    if q:
        sql += " WHERE c.name LIKE ? ESCAPE '\\' OR c.contact LIKE ? ESCAPE '\\'"
        params += [like_pattern(q), like_pattern(q)]
    sql += " GROUP BY c.client_id ORDER BY c.name COLLATE NOCASE"

    return render_template("clients.html", clients=conn.execute(sql, params).fetchall(), q=q)


@app.route("/clients/new", methods=["GET", "POST"])
def clients_new():
    conn = db.get_db()
    then = request.values.get("then", "")  # kapag "book", didiretso sa booking page pagkatapos mag-register

    if request.method == "POST":
        data, errors = validators.validate_client(request.form)
        if not errors and conn.execute(
            "SELECT 1 FROM clients WHERE lower(name) = lower(?) AND contact = ?",
            (data["name"], data["contact"]),
        ).fetchone():
            errors.append("This client is already registered with the same name and contact number.")

        if not errors:
            try:
                cursor = conn.execute(
                    "INSERT INTO clients (name, contact, email) VALUES (?, ?, ?)",
                    (data["name"], data["contact"], data["email"] or None),
                )
            except sqlite3.Error:
                app.logger.exception("Failed to insert client")
                errors.append(DB_ERROR)
            else:
                flash(f"Client {data['name']} was registered.", "success")
                if then == "book":
                    return redirect(url_for("appointments_new", client_id=cursor.lastrowid))
                return redirect(url_for("clients_list"))

        flash_errors(errors)
        form = request.form
    else:
        form = {}
    return render_template("client_form.html", mode="new", form=form, then=then)


@app.route("/clients/<int:client_id>")
def clients_detail(client_id):
    conn = db.get_db()
    client = fetch_or_404(conn, "SELECT * FROM clients WHERE client_id = ?", (client_id,))
    appointments = conn.execute(
        APPT_SELECT + " WHERE a.client_id = ? ORDER BY a.appt_date DESC, a.start_time DESC",
        (client_id,),
    ).fetchall()
    return render_template(
        "client_detail.html", client=client, appointments=appointments,
        next_url=request.full_path.rstrip("?"),
    )


@app.route("/clients/<int:client_id>/edit", methods=["GET", "POST"])
def clients_edit(client_id):
    conn = db.get_db()
    client = fetch_or_404(conn, "SELECT * FROM clients WHERE client_id = ?", (client_id,))

    if request.method == "POST":
        data, errors = validators.validate_client(request.form)
        if not errors and conn.execute(
            "SELECT 1 FROM clients WHERE lower(name) = lower(?) AND contact = ? AND client_id != ?",
            (data["name"], data["contact"], client_id),
        ).fetchone():
            errors.append("Another client already has the same name and contact number.")

        if not errors:
            try:
                conn.execute(
                    "UPDATE clients SET name = ?, contact = ?, email = ? WHERE client_id = ?",
                    (data["name"], data["contact"], data["email"] or None, client_id),
                )
            except sqlite3.Error:
                app.logger.exception("Failed to update client")
                errors.append(DB_ERROR)
            else:
                flash(f"Client {data['name']} was updated.", "success")
                return redirect(url_for("clients_detail", client_id=client_id))

        flash_errors(errors)
        form = request.form
    else:
        form = dict(client)
    return render_template("client_form.html", mode="edit", form=form, client=client, then="")


@app.route("/clients/<int:client_id>/delete", methods=["POST"])
def clients_delete(client_id):
    conn = db.get_db()
    client = fetch_or_404(conn, "SELECT * FROM clients WHERE client_id = ?", (client_id,))

    total, active = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(status IN ('Pending', 'Confirmed')), 0) "
        "FROM appointments WHERE client_id = ?",
        (client_id,),
    ).fetchone()

    # bawal i-delete kung may appointment records pa, para hindi mawala ang history
    if active:
        flash(f"{client['name']} still has {active} active appointment(s). "
              "Cancel or complete them first.", "error")
    elif total:
        flash(f"{client['name']} has {total} past appointment record(s) that are kept for history, "
              "so this client cannot be deleted.", "error")
    else:
        try:
            conn.execute("DELETE FROM clients WHERE client_id = ?", (client_id,))
        except sqlite3.Error:
            app.logger.exception("Failed to delete client")
            flash(DB_ERROR, "error")
        else:
            flash(f"Client {client['name']} was deleted.", "success")
    return redirect(url_for("clients_list"))


# =====================================================================
# 4. STAFF / RESOURCES
# =====================================================================
@app.route("/resources")
def resources_list():
    conn = db.get_db()
    rows = conn.execute(
        "SELECT r.resource_id, r.name, r.role, r.is_active, COUNT(a.appointment_id) AS appt_count "
        "FROM staff_or_resources r LEFT JOIN appointments a ON a.resource_id = r.resource_id "
        "GROUP BY r.resource_id ORDER BY r.is_active DESC, r.name COLLATE NOCASE"
    ).fetchall()
    return render_template("resources.html", resources=rows)


@app.route("/resources/new", methods=["GET", "POST"])
def resources_new():
    conn = db.get_db()
    if request.method == "POST":
        data, errors = validators.validate_resource(request.form)
        if not errors and conn.execute(
            "SELECT 1 FROM staff_or_resources WHERE lower(name) = lower(?)", (data["name"],)
        ).fetchone():
            errors.append("A staff member or resource with that name already exists.")

        if not errors:
            try:
                conn.execute(
                    "INSERT INTO staff_or_resources (name, role, is_active) VALUES (?, ?, ?)",
                    (data["name"], data["role"], data["is_active"]),
                )
            except sqlite3.Error:
                app.logger.exception("Failed to insert resource")
                errors.append(DB_ERROR)
            else:
                flash(f"{data['name']} was added.", "success")
                return redirect(url_for("resources_list"))

        flash_errors(errors)
        form = request.form
    else:
        form = {"is_active": "1"}  # naka-check by default
    return render_template("resource_form.html", mode="new", form=form)


@app.route("/resources/<int:resource_id>/edit", methods=["GET", "POST"])
def resources_edit(resource_id):
    conn = db.get_db()
    resource = fetch_or_404(
        conn, "SELECT * FROM staff_or_resources WHERE resource_id = ?", (resource_id,)
    )

    if request.method == "POST":
        data, errors = validators.validate_resource(request.form)
        if not errors and conn.execute(
            "SELECT 1 FROM staff_or_resources WHERE lower(name) = lower(?) AND resource_id != ?",
            (data["name"], resource_id),
        ).fetchone():
            errors.append("A staff member or resource with that name already exists.")

        if not errors:
            try:
                conn.execute(
                    "UPDATE staff_or_resources SET name = ?, role = ?, is_active = ? "
                    "WHERE resource_id = ?",
                    (data["name"], data["role"], data["is_active"], resource_id),
                )
            except sqlite3.Error:
                app.logger.exception("Failed to update resource")
                errors.append(DB_ERROR)
            else:
                flash(f"{data['name']} was updated.", "success")
                return redirect(url_for("resources_list"))

        flash_errors(errors)
        form = request.form
    else:
        form = dict(resource)
        form["is_active"] = "1" if resource["is_active"] else ""
    return render_template("resource_form.html", mode="edit", form=form, resource=resource)


@app.route("/resources/<int:resource_id>/delete", methods=["POST"])
def resources_delete(resource_id):
    conn = db.get_db()
    resource = fetch_or_404(
        conn, "SELECT * FROM staff_or_resources WHERE resource_id = ?", (resource_id,)
    )
    total = conn.execute(
        "SELECT COUNT(*) FROM appointments WHERE resource_id = ?", (resource_id,)
    ).fetchone()[0]

    if total:
        flash(f"{resource['name']} has {total} appointment record(s) and cannot be deleted. "
              "Edit it and uncheck Active instead, so it no longer appears in new bookings.", "error")
    else:
        try:
            conn.execute("DELETE FROM staff_or_resources WHERE resource_id = ?", (resource_id,))
        except sqlite3.Error:
            app.logger.exception("Failed to delete resource")
            flash(DB_ERROR, "error")
        else:
            flash(f"{resource['name']} was deleted.", "success")
    return redirect(url_for("resources_list"))


# =====================================================================
# 5. SERVICES
# =====================================================================
@app.route("/services")
def services_list():
    conn = db.get_db()
    rows = conn.execute(
        "SELECT s.service_id, s.name, s.duration_minutes, COUNT(a.appointment_id) AS appt_count "
        "FROM services s LEFT JOIN appointments a ON a.service_id = s.service_id "
        "GROUP BY s.service_id ORDER BY s.name COLLATE NOCASE"
    ).fetchall()
    return render_template("services.html", services=rows)


@app.route("/services/new", methods=["GET", "POST"])
def services_new():
    conn = db.get_db()
    if request.method == "POST":
        data, errors = validators.validate_service(request.form)
        if not errors and conn.execute(
            "SELECT 1 FROM services WHERE lower(name) = lower(?)", (data["name"],)
        ).fetchone():
            errors.append("A service with that name already exists.")

        if not errors:
            try:
                conn.execute(
                    "INSERT INTO services (name, duration_minutes) VALUES (?, ?)",
                    (data["name"], data["duration_minutes"]),
                )
            except sqlite3.Error:
                app.logger.exception("Failed to insert service")
                errors.append(DB_ERROR)
            else:
                flash(f"Service {data['name']} was added.", "success")
                return redirect(url_for("services_list"))

        flash_errors(errors)
        form = request.form
    else:
        form = {"duration_minutes": "30"}
    return render_template("service_form.html", mode="new", form=form)


@app.route("/services/<int:service_id>/edit", methods=["GET", "POST"])
def services_edit(service_id):
    conn = db.get_db()
    service = fetch_or_404(conn, "SELECT * FROM services WHERE service_id = ?", (service_id,))

    if request.method == "POST":
        data, errors = validators.validate_service(request.form)
        if not errors and conn.execute(
            "SELECT 1 FROM services WHERE lower(name) = lower(?) AND service_id != ?",
            (data["name"], service_id),
        ).fetchone():
            errors.append("A service with that name already exists.")

        if not errors:
            try:
                conn.execute(
                    "UPDATE services SET name = ?, duration_minutes = ? WHERE service_id = ?",
                    (data["name"], data["duration_minutes"], service_id),
                )
            except sqlite3.Error:
                app.logger.exception("Failed to update service")
                errors.append(DB_ERROR)
            else:
                flash(f"Service {data['name']} was updated. "
                      "Existing appointments keep their original end time.", "success")
                return redirect(url_for("services_list"))

        flash_errors(errors)
        form = request.form
    else:
        form = dict(service)
    return render_template("service_form.html", mode="edit", form=form, service=service)


@app.route("/services/<int:service_id>/delete", methods=["POST"])
def services_delete(service_id):
    conn = db.get_db()
    service = fetch_or_404(conn, "SELECT * FROM services WHERE service_id = ?", (service_id,))
    total = conn.execute(
        "SELECT COUNT(*) FROM appointments WHERE service_id = ?", (service_id,)
    ).fetchone()[0]

    if total:
        flash(f"{service['name']} is used by {total} appointment record(s) and cannot be deleted.", "error")
    else:
        try:
            conn.execute("DELETE FROM services WHERE service_id = ?", (service_id,))
        except sqlite3.Error:
            app.logger.exception("Failed to delete service")
            flash(DB_ERROR, "error")
        else:
            flash(f"Service {service['name']} was deleted.", "success")
    return redirect(url_for("services_list"))


# =====================================================================
# 6. APPOINTMENTS
# =====================================================================
@app.route("/appointments")
def appointments_list():
    """Listahan ng appointments; may filter sa date, status, staff, at search sa client name."""
    conn = db.get_db()
    q = request.args.get("q", "").strip()
    status = request.args.get("status", "").strip()
    date_text = request.args.get("date", "").strip()
    resource_id = validators.to_int(request.args.get("resource_id"))

    # i-build ang WHERE; fixed na text lang ang idinadagdag sa SQL, ang values nasa params list
    conditions, params = [], []
    if q:
        conditions.append("c.name LIKE ? ESCAPE '\\'")
        params.append(like_pattern(q))
    if status:
        if status in config.STATUSES:
            conditions.append("a.status = ?")
            params.append(status)
        else:
            flash("Unknown status filter was ignored.", "error")
            status = ""
    if date_text:
        parsed = validators.parse_date(date_text)
        if parsed:
            conditions.append("a.appt_date = ?")
            params.append(parsed.isoformat())
            date_text = parsed.isoformat()
        else:
            flash("The date filter was not a valid date, so it was ignored.", "error")
            date_text = ""
    if resource_id is not None:
        conditions.append("a.resource_id = ?")
        params.append(resource_id)

    PAGE_SIZE = 20

    where_sql = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    total_count = conn.execute(
        "SELECT COUNT(*) FROM appointments a JOIN clients c ON c.client_id = a.client_id "
        "JOIN staff_or_resources r ON r.resource_id = a.resource_id "
        "JOIN services s ON s.service_id = a.service_id" + where_sql,
        params,
    ).fetchone()[0]
    total_pages = max(1, -(-total_count // PAGE_SIZE))  # ceiling division

    page = validators.to_int(request.args.get("page")) or 1
    page = max(1, min(page, total_pages))
    offset = (page - 1) * PAGE_SIZE

    sql = APPT_SELECT + where_sql + " ORDER BY a.appt_date, a.start_time, r.name LIMIT ? OFFSET ?"
    rows = conn.execute(sql, params + [PAGE_SIZE, offset]).fetchall()

    def page_url(p):
        args = request.args.to_dict()
        if p <= 1:
            args.pop("page", None)
        else:
            args["page"] = p
        return url_for("appointments_list", **args)

    resources = conn.execute(
        "SELECT resource_id, name FROM staff_or_resources ORDER BY name COLLATE NOCASE"
    ).fetchall()
    filters = {"q": q, "status": status, "date": date_text,
               "resource_id": resource_id if resource_id is not None else ""}
    return render_template(
        "appointments.html", rows=rows, filters=filters, resources=resources,
        has_filters=bool(conditions), next_url=request.full_path.rstrip("?"),
        page=page, total_pages=total_pages, total_count=total_count,
        pg_prev_url=page_url(page - 1) if page > 1 else None,
        pg_next_url=page_url(page + 1) if page < total_pages else None,
    )


@app.route("/appointments/new", methods=["GET", "POST"])
def appointments_new():
    conn = db.get_db()

    if request.method == "POST":
        data, errors = validators.validate_appointment(request.form)
        if not errors:
            try:
                # dito dumadaan ang conflict check + insert + history (isang transaction)
                appointment_id = scheduling.book_appointment(conn, **data)
            except scheduling.ScheduleError as exc:
                errors.append(str(exc))
            except sqlite3.Error:
                app.logger.exception("Failed to book appointment")
                errors.append(DB_ERROR)
            else:
                flash(f"Appointment #{appointment_id} was booked for {fmt_date(data['appt_date'])} "
                      f"at {fmt_time(data['start_time'])}. Status: Pending.", "success")
                return redirect(url_for("appointments_list", date=data["appt_date"]))

        flash_errors(errors)
        form = request.form
    else:
        form = request.args  # galing sa link, halimbawa sa day schedule o sa client page

    heading, slots, note = slots_context(conn, form.get("resource_id"), form.get("appt_date"))
    return render_template(
        "appointment_new.html", form=form, slots=slots, slots_heading=heading, slots_note=note,
        clients=conn.execute("SELECT client_id, name FROM clients ORDER BY name COLLATE NOCASE").fetchall(),
        services=conn.execute(
            "SELECT service_id, name, duration_minutes FROM services ORDER BY name COLLATE NOCASE"
        ).fetchall(),
        resources=conn.execute(
            "SELECT resource_id, name, role FROM staff_or_resources WHERE is_active = 1 "
            "ORDER BY name COLLATE NOCASE"
        ).fetchall(),
        time_slots=config.time_slots(),
    )


@app.route("/appointments/<int:appointment_id>/edit", methods=["GET", "POST"])
def appointments_edit(appointment_id):
    """Reschedule: palitan ang staff, date, o oras."""
    conn = db.get_db()
    appt = fetch_or_404(conn, APPT_SELECT + " WHERE a.appointment_id = ?", (appointment_id,))

    if appt["status"] not in config.ACTIVE_STATUSES:
        flash(f"A {appt['status']} appointment cannot be rescheduled.", "error")
        return redirect(url_for("appointments_list"))

    if request.method == "POST":
        data, errors = validators.validate_schedule(request.form)
        if not errors:
            try:
                scheduling.reschedule_appointment(conn, appointment_id, **data)
            except scheduling.ScheduleError as exc:
                errors.append(str(exc))
            except sqlite3.Error:
                app.logger.exception("Failed to reschedule appointment")
                errors.append(DB_ERROR)
            else:
                flash(f"Appointment #{appointment_id} was moved to {fmt_date(data['appt_date'])} "
                      f"at {fmt_time(data['start_time'])}.", "success")
                return redirect(url_for("appointments_list", date=data["appt_date"]))

        flash_errors(errors)
        form = request.form
    else:
        form = {"resource_id": appt["resource_id"], "appt_date": appt["appt_date"],
                "start_time": appt["start_time"]}

    heading, slots, note = slots_context(
        conn, form.get("resource_id"), form.get("appt_date"), exclude_id=appointment_id
    )
    return render_template(
        "appointment_edit.html", appt=appt, form=form, slots=slots, slots_heading=heading,
        slots_note=note,
        resources=conn.execute(
            "SELECT resource_id, name, role FROM staff_or_resources WHERE is_active = 1 "
            "ORDER BY name COLLATE NOCASE"
        ).fetchall(),
        time_slots=config.time_slots(),
    )


@app.route("/appointments/<int:appointment_id>/status", methods=["POST"])
def appointments_status(appointment_id):
    """Update ng status (Confirm, Complete, Cancel, atbp.)."""
    conn = db.get_db()
    new_status = request.form.get("status", "")
    try:
        old_status, new_status = scheduling.change_status(conn, appointment_id, new_status)
    except scheduling.ScheduleError as exc:
        flash(str(exc), "error")
    except sqlite3.Error:
        app.logger.exception("Failed to change status")
        flash(DB_ERROR, "error")
    else:
        flash(f"Appointment #{appointment_id} changed from {old_status} to {new_status}.", "success")
    return redirect(safe_next(url_for("appointments_list")))


@app.route("/appointments/<int:appointment_id>/delete", methods=["POST"])
def appointments_delete(appointment_id):
    conn = db.get_db()
    try:
        scheduling.delete_appointment(conn, appointment_id)
    except scheduling.ScheduleError as exc:
        flash(str(exc), "error")
    except sqlite3.Error:
        app.logger.exception("Failed to delete appointment")
        flash(DB_ERROR, "error")
    else:
        flash(f"Appointment #{appointment_id} was deleted.", "success")
    return redirect(safe_next(url_for("appointments_list")))


@app.route("/appointments/<int:appointment_id>/history")
def appointments_history(appointment_id):
    conn = db.get_db()
    appt = fetch_or_404(conn, APPT_SELECT + " WHERE a.appointment_id = ?", (appointment_id,))
    history = conn.execute(
        "SELECT action, old_value, new_value, changed_at FROM appointment_history "
        "WHERE appointment_id = ? ORDER BY history_id DESC",
        (appointment_id,),
    ).fetchall()
    return render_template("history.html", appt=appt, history=history)


@app.route("/api/booked-slots")
def api_booked_slots():
    """JSON na kinukuha ng JavaScript para ma-update ang 'Already booked' panel nang walang reload."""
    conn = db.get_db()
    heading, rows, note = slots_context(
        conn, request.args.get("resource_id"), request.args.get("date"),
        exclude_id=validators.to_int(request.args.get("exclude")),
    )
    return jsonify(
        heading=heading,
        note=note,
        slots=[
            {"start": fmt_time(r["start_time"]), "end": fmt_time(r["end_time"]),
             "service": r["service_name"], "status": r["status"]}
            for r in rows
        ],
    )


# =====================================================================
# 7. ERROR PAGES
# =====================================================================
def render_error(code, title, message):
    return render_template("error.html", code=code, title=title, message=message), code


@app.errorhandler(400)
def bad_request(_error):
    return render_error(400, "Request could not be processed",
                        "Your form session expired or the request was invalid. "
                        "Go back, refresh the page, and try again.")


@app.errorhandler(404)
def not_found(_error):
    return render_error(404, "Page not found", "The page or record you are looking for does not exist.")


@app.errorhandler(405)
def method_not_allowed(_error):
    return render_error(405, "Action not allowed", "That action is not allowed on this page.")


@app.errorhandler(sqlite3.Error)
@app.errorhandler(500)
def server_error(error):
    app.logger.exception("Unhandled error: %s", error)
    return render_error(500, "Something went wrong",
                        "The system had a problem processing your request. Nothing was changed. "
                        "Please try again, or tell the administrator if it keeps happening.")


if __name__ == "__main__":
    app.run(debug=app.config["DEBUG"])
