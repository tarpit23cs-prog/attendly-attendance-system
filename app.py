"""
app.py
------
Main Flask application for Attendly - Employee Attendance & Report System.

This is a beginner-friendly project, so ALL the main logic lives in this
one file instead of being split across blueprints/services/classes.
Read it top to bottom - it is written like a story:

1. Setup (Flask app, config, decorators)
2. Small helper functions (time formatting, working hours, etc.)
3. Login / Logout
4. Admin: Dashboard, Employees, Attendance
5. Employee: Dashboard, Clock In/Out, My Attendance
6. Reports (Pandas + NumPy + Chart.js)
7. CSV / PDF export
8. Error handlers
"""

from functools import wraps
from datetime import datetime, date, time, timedelta
import io
import csv

import numpy as np
import pandas as pd
import mysql.connector
from flask import Flask, render_template, request, redirect, url_for, session, flash, Response
from werkzeug.security import generate_password_hash, check_password_hash

from config import SECRET_KEY, OFFICE_START_TIME, GRACE_PERIOD_MINUTES
from db import get_db_connection

app = Flask(__name__)
app.secret_key = SECRET_KEY

# List of departments used in dropdowns across the app
DEPARTMENTS = ["Development", "HR", "Finance", "Sales", "Support", "Marketing"]


# ==========================================================================
# DECORATORS - simple functions that protect routes
# ==========================================================================

def login_required(f):
    """Only allow access if the user is logged in (any role)."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to continue.", "warning")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    """Only allow access if the logged-in user is an admin."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if session.get("role") != "admin":
            flash("You do not have permission to view that page.", "danger")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


def employee_required(f):
    """Only allow access if the logged-in user is a normal employee."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if session.get("role") != "employee":
            flash("You do not have permission to view that page.", "danger")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


# ==========================================================================
# SMALL HELPER FUNCTIONS
# ==========================================================================

def get_office_cutoff_time():
    """
    Reads OFFICE_START_TIME ("09:30:00") and GRACE_PERIOD_MINUTES (15)
    from config.py and returns the latest time an employee can clock in
    and still be marked "Present" (09:45:00 by default).
    """
    h, m, s = [int(x) for x in OFFICE_START_TIME.split(":")]
    start_dt = datetime.combine(date.today(), time(h, m, s))
    cutoff_dt = start_dt + timedelta(minutes=GRACE_PERIOD_MINUTES)
    return cutoff_dt.time()


def timedelta_to_ampm(value):
    """
    MySQL TIME columns come back from mysql-connector-python as
    datetime.timedelta objects (not datetime.time). This converts
    that timedelta into a friendly 12-hour string like "09:12 AM".
    Returns None if the value is None (i.e. not clocked in/out yet).
    """
    if value is None:
        return None
    total_seconds = int(value.total_seconds())
    hours = (total_seconds // 3600) % 24
    minutes = (total_seconds % 3600) // 60
    return time(hours, minutes).strftime("%I:%M %p")


def working_minutes_between(login_value, logout_value):
    """
    Given the raw login_time and logout_time (timedelta objects from MySQL),
    returns the number of minutes worked, or None if either is missing.
    """
    if login_value is None or logout_value is None:
        return None
    diff_seconds = (logout_value - login_value).total_seconds()
    if diff_seconds < 0:
        return None
    return diff_seconds / 60


def minutes_to_label(minutes):
    """Converts a number of minutes into a "8h 51m" style label."""
    if minutes is None or pd.isna(minutes) or minutes <= 0:
        return "--"
    minutes = int(round(minutes))
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h {mins}m"


def parse_date_arg(value, fallback):
    """Safely parses a 'YYYY-MM-DD' string into a date object."""
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return fallback


# ==========================================================================
# LOGIN / LOGOUT
# ==========================================================================

@app.route("/")
def index():
    """Redirect visitors to the right page based on login state/role."""
    if "user_id" not in session:
        return redirect(url_for("login"))
    if session.get("role") == "admin":
        return redirect(url_for("dashboard"))
    return redirect(url_for("employee_dashboard"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html")

    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM employees WHERE email = %s", (email,))
    user = cursor.fetchone()
    cursor.close()
    conn.close()

    # Step 1: does a user with this email exist, and is the password correct?
    if not user or not check_password_hash(user["password"], password):
        flash("Invalid email or password.", "danger")
        return redirect(url_for("login"))

    # Step 2: is the account active?
    if user["status"] != "active":
        flash("Your account has been deactivated. Please contact the admin.", "danger")
        return redirect(url_for("login"))

    # Step 3: log the user in by saving details in the session
    session["user_id"] = user["id"]
    session["role"] = user["role"]
    session["name"] = user["name"]

    flash(f"Welcome back, {user['name']}!", "success")

    if user["role"] == "admin":
        return redirect(url_for("dashboard"))
    return redirect(url_for("employee_dashboard"))


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))


# ==========================================================================
# SHARED HELPER: attendance for a single day (used by dashboard + attendance page)
# ==========================================================================

def get_attendance_for_date(target_date, department=None, status_filter=None):
    """
    Builds the attendance list for one specific day.

    Logic:
      1. Get every ACTIVE employee (role = 'employee').
      2. LEFT JOIN with the attendance table for that date, so employees
         who did NOT clock in still show up (with no login/logout time).
      3. If an employee has no record:
           - Saturday/Sunday -> label as "Weekend" (not counted as absent)
           - Any other day   -> label as "Absent"
      4. Apply department/status filters in Python (keeps the SQL simple).

    Returns: (records, summary)
        records = list of dicts, one per employee
        summary = dict with total/present/absent/late counts
    """
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    query = """
        SELECT e.employee_id, e.name, e.department,
               a.login_time, a.logout_time, a.status
        FROM employees e
        LEFT JOIN attendance a
               ON a.employee_id = e.id AND a.date = %s
        WHERE e.role = 'employee' AND e.status = 'active'
    """
    params = [target_date]

    if department:
        query += " AND e.department = %s"
        params.append(department)

    query += " ORDER BY e.name"

    cursor.execute(query, params)
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    is_weekend = target_date.weekday() >= 5  # 5 = Saturday, 6 = Sunday

    records = []
    for row in rows:
        if row["status"] is None:
            status = "Weekend" if is_weekend else "Absent"
        else:
            status = row["status"]

        minutes = working_minutes_between(row["login_time"], row["logout_time"])

        records.append({
            "employee_id": row["employee_id"],
            "name": row["name"],
            "department": row["department"],
            "status": status,
            "login_time": timedelta_to_ampm(row["login_time"]),
            "logout_time": timedelta_to_ampm(row["logout_time"]),
            "working_hours": minutes_to_label(minutes),
        })

    # Apply the status filter (done in Python since "Absent"/"Weekend"
    # are calculated values, not something we can filter with SQL)
    if status_filter:
        records = [r for r in records if r["status"] == status_filter]

    summary = {
        "total_employees": len(rows),
        "present_today": sum(1 for r in records if r["status"] == "Present"),
        "absent_today": sum(1 for r in records if r["status"] == "Absent"),
        "late_today": sum(1 for r in records if r["status"] == "Late"),
    }

    return records, summary


# ==========================================================================
# ADMIN: DASHBOARD
# ==========================================================================

@app.route("/dashboard")
@login_required
@admin_required
def dashboard():
    today = date.today()
    records, summary = get_attendance_for_date(today)

    return render_template(
        "dashboard.html",
        active_page="dashboard",
        today_display=today.strftime("%A, %d %B %Y"),
        total_employees=summary["total_employees"],
        present_today=summary["present_today"],
        absent_today=summary["absent_today"],
        late_today=summary["late_today"],
        today_attendance=records,
    )


# ==========================================================================
# ADMIN: EMPLOYEE MANAGEMENT
# ==========================================================================

@app.route("/employees")
@login_required
@admin_required
def employees():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM employees ORDER BY id")
    employee_list = cursor.fetchall()
    cursor.close()
    conn.close()

    return render_template("employees.html", active_page="employees", employees=employee_list)


@app.route("/employees/add", methods=["GET", "POST"])
@login_required
@admin_required
def add_employee():
    if request.method == "GET":
        return render_template("add_employee.html", active_page="employees")

    employee_id = request.form.get("employee_id", "").strip()
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    department = request.form.get("department")
    role = request.form.get("role")

    hashed_password = generate_password_hash(password)

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO employees (employee_id, name, email, password, department, role, status)
            VALUES (%s, %s, %s, %s, %s, %s, 'active')
        """, (employee_id, name, email, hashed_password, department, role))
        conn.commit()
        flash(f"Employee {name} added successfully.", "success")
    except mysql.connector.IntegrityError:
        # This happens if employee_id or email already exists (UNIQUE constraint)
        flash("That Employee ID or Email already exists. Please use a different one.", "danger")
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for("employees"))


@app.route("/employees/edit/<int:emp_id>", methods=["GET", "POST"])
@login_required
@admin_required
def edit_employee(emp_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    if request.method == "GET":
        cursor.execute("SELECT * FROM employees WHERE id = %s", (emp_id,))
        emp = cursor.fetchone()
        cursor.close()
        conn.close()

        if not emp:
            flash("Employee not found.", "danger")
            return redirect(url_for("employees"))

        return render_template("edit_employee.html", active_page="employees", emp=emp)

    # POST - update the employee
    employee_id = request.form.get("employee_id", "").strip()
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    department = request.form.get("department")
    role = request.form.get("role")

    try:
        if password:
            # Admin typed a new password -> update it too
            hashed_password = generate_password_hash(password)
            cursor.execute("""
                UPDATE employees
                SET employee_id=%s, name=%s, email=%s, password=%s, department=%s, role=%s
                WHERE id=%s
            """, (employee_id, name, email, hashed_password, department, role, emp_id))
        else:
            # No new password -> keep the existing one
            cursor.execute("""
                UPDATE employees
                SET employee_id=%s, name=%s, email=%s, department=%s, role=%s
                WHERE id=%s
            """, (employee_id, name, email, department, role, emp_id))
        conn.commit()
        flash("Employee updated successfully.", "success")
    except mysql.connector.IntegrityError:
        flash("That Employee ID or Email already exists. Please use a different one.", "danger")
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for("employees"))


@app.route("/employees/toggle/<int:emp_id>", methods=["POST"])
@login_required
@admin_required
def toggle_employee(emp_id):
    """Flips an employee between 'active' and 'inactive'. We never delete employees."""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT status FROM employees WHERE id = %s", (emp_id,))
    emp = cursor.fetchone()

    if emp:
        new_status = "inactive" if emp["status"] == "active" else "active"
        cursor.execute("UPDATE employees SET status = %s WHERE id = %s", (new_status, emp_id))
        conn.commit()
        flash(f"Employee status changed to {new_status}.", "success")

    cursor.close()
    conn.close()
    return redirect(url_for("employees"))


# ==========================================================================
# ADMIN: ATTENDANCE PAGE (with simple GET filters)
# ==========================================================================

@app.route("/attendance")
@login_required
@admin_required
def attendance():
    selected_date = parse_date_arg(request.args.get("date"), date.today())
    selected_department = request.args.get("department", "")
    selected_status = request.args.get("status", "")

    records, _ = get_attendance_for_date(
        selected_date,
        department=selected_department or None,
        status_filter=selected_status or None,
    )

    return render_template(
        "attendance.html",
        active_page="attendance",
        departments=DEPARTMENTS,
        selected_date=selected_date.isoformat(),
        selected_department=selected_department,
        selected_status=selected_status,
        records=records,
    )


# ==========================================================================
# EMPLOYEE: CLOCK IN / CLOCK OUT
# ==========================================================================

@app.route("/clock-in", methods=["POST"])
@login_required
@employee_required
def clock_in():
    employee_db_id = session["user_id"]
    today = date.today()
    now = datetime.now()

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # Rule: only one clock-in per employee per day
    cursor.execute(
        "SELECT id FROM attendance WHERE employee_id = %s AND date = %s",
        (employee_db_id, today)
    )
    existing = cursor.fetchone()

    if existing:
        flash("You have already clocked in today.", "warning")
    else:
        # Decide Present vs Late by comparing current time to the office cutoff
        cutoff_time = get_office_cutoff_time()
        status = "Present" if now.time() <= cutoff_time else "Late"

        cursor.execute("""
            INSERT INTO attendance (employee_id, date, login_time, status)
            VALUES (%s, %s, %s, %s)
        """, (employee_db_id, today, now.strftime("%H:%M:%S"), status))
        conn.commit()
        flash(f"Clocked in at {now.strftime('%I:%M %p')} - marked as {status}.", "success")

    cursor.close()
    conn.close()
    return redirect(url_for("employee_dashboard"))


@app.route("/clock-out", methods=["POST"])
@login_required
@employee_required
def clock_out():
    employee_db_id = session["user_id"]
    today = date.today()
    now = datetime.now()

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute(
        "SELECT id, logout_time FROM attendance WHERE employee_id = %s AND date = %s",
        (employee_db_id, today)
    )
    record = cursor.fetchone()

    if not record:
        flash("Please clock in first before clocking out.", "warning")
    elif record["logout_time"] is not None:
        flash("You have already clocked out today.", "warning")
    else:
        cursor.execute(
            "UPDATE attendance SET logout_time = %s WHERE id = %s",
            (now.strftime("%H:%M:%S"), record["id"])
        )
        conn.commit()
        flash(f"Clocked out at {now.strftime('%I:%M %p')}. Have a great day!", "success")

    cursor.close()
    conn.close()
    return redirect(url_for("employee_dashboard"))


# ==========================================================================
# EMPLOYEE: DASHBOARD
# ==========================================================================

@app.route("/employee-dashboard")
@login_required
@employee_required
def employee_dashboard():
    employee_db_id = session["user_id"]
    today = date.today()

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("SELECT * FROM employees WHERE id = %s", (employee_db_id,))
    employee = cursor.fetchone()

    cursor.execute(
        "SELECT * FROM attendance WHERE employee_id = %s AND date = %s",
        (employee_db_id, today)
    )
    today_row = cursor.fetchone()

    today_record = None
    if today_row:
        minutes = working_minutes_between(today_row["login_time"], today_row["logout_time"])
        today_record = {
            "status": today_row["status"],
            "login_time": timedelta_to_ampm(today_row["login_time"]),
            "logout_time": timedelta_to_ampm(today_row["logout_time"]),
            "working_hours": minutes_to_label(minutes),
        }

    # Simple 30-day summary: how many working days, how many were present/late
    window_start = today - timedelta(days=29)
    working_days = int(np.busday_count(window_start, today + timedelta(days=1)))

    cursor.execute("""
        SELECT status FROM attendance
        WHERE employee_id = %s AND date BETWEEN %s AND %s
    """, (employee_db_id, window_start, today))
    recent_rows = cursor.fetchall()

    cursor.close()
    conn.close()

    present_days = sum(1 for r in recent_rows if r["status"] == "Present")
    late_days = sum(1 for r in recent_rows if r["status"] == "Late")
    attendance_pct = round((present_days + late_days) / working_days * 100, 1) if working_days > 0 else 0

    stats = {
        "present_days": present_days,
        "late_days": late_days,
        "attendance_pct": attendance_pct,
    }

    return render_template(
        "dashboard.html",
        active_page="dashboard",
        employee=employee,
        today_record=today_record,
        stats=stats,
    )


# ==========================================================================
# EMPLOYEE: MY ATTENDANCE HISTORY
# ==========================================================================

@app.route("/my-attendance")
@login_required
@employee_required
def my_attendance():
    employee_db_id = session["user_id"]

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT * FROM attendance
        WHERE employee_id = %s
        ORDER BY date DESC
    """, (employee_db_id,))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    records = []
    for row in rows:
        minutes = working_minutes_between(row["login_time"], row["logout_time"])
        records.append({
            "date": row["date"],
            "status": row["status"],
            "login_time": timedelta_to_ampm(row["login_time"]),
            "logout_time": timedelta_to_ampm(row["logout_time"]),
            "working_hours": minutes_to_label(minutes),
        })

    return render_template("my_attendance.html", active_page="my_attendance", records=records)


# ==========================================================================
# REPORTS - this is where Pandas and NumPy are used
# ==========================================================================

def get_month_range(month_str):
    """Turns 'YYYY-MM' into (first_day, last_day) of that month."""
    year, month = [int(x) for x in month_str.split("-")]
    start = date(year, month, 1)
    if month == 12:
        end = date(year, 12, 31)
    else:
        end = date(year, month + 1, 1) - timedelta(days=1)
    return start, end


def get_report_date_range():
    """
    Reads period/date/month/year from the query string (?period=...)
    and returns (start_date, end_date, period, label) for that range.
    Used by both the /report page and the CSV/PDF export routes so
    they always agree on what date range is being reported on.
    """
    today = date.today()
    period = request.args.get("period", "monthly")

    if period == "daily":
        selected = parse_date_arg(request.args.get("date"), today)
        return selected, selected, period, selected.strftime("%d %B %Y")

    if period == "yearly":
        year = int(request.args.get("year", today.year))
        start = date(year, 1, 1)
        end = date(year, 12, 31)
        end = min(end, today)  # never report on future days
        return start, end, period, str(year)

    # default: monthly
    month_str = request.args.get("month", today.strftime("%Y-%m"))
    start, end = get_month_range(month_str)
    end = min(end, today)
    return start, end, period, start.strftime("%B %Y")


def build_report(start_date, end_date):
    """
    The core Pandas + NumPy report logic.

    Steps:
      1. Load every active employee into a DataFrame.
      2. Load every attendance row in the date range into a DataFrame.
      3. Use NumPy to count how many working days (Mon-Fri) are in the range.
      4. Group the attendance DataFrame by employee to get Present/Late counts
         and average working hours.
      5. Merge that back onto the employee list (so employees with ZERO
         attendance records still show up with 0 present / all absent).
      6. Build department-wise totals for the bar chart.
    """
    working_days = int(np.busday_count(start_date, end_date + timedelta(days=1)))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # 1. All active employees
    cursor.execute("""
        SELECT employee_id, name, department
        FROM employees
        WHERE role = 'employee' AND status = 'active'
        ORDER BY name
    """)
    employees_df = pd.DataFrame(
        cursor.fetchall(), columns=["employee_id", "name", "department"]
    )

    # 2. Raw attendance rows for the date range, joined with employee info
    cursor.execute("""
        SELECT e.employee_id, e.name, e.department,
               a.date, a.login_time, a.logout_time, a.status
        FROM attendance a
        JOIN employees e ON e.id = a.employee_id
        WHERE a.date BETWEEN %s AND %s AND e.role = 'employee'
    """, (start_date, end_date))
    attendance_rows = cursor.fetchall()
    cursor.close()
    conn.close()

    attendance_df = pd.DataFrame(
        attendance_rows,
        columns=["employee_id", "name", "department", "date", "login_time", "logout_time", "status"]
    )

    # Working minutes for each row (NaN if login/logout missing)
    if not attendance_df.empty:
        attendance_df["working_minutes"] = attendance_df.apply(
            lambda r: working_minutes_between(r["login_time"], r["logout_time"]), axis=1
        )
    else:
        attendance_df["working_minutes"] = pd.Series(dtype=float)

    # 3. Group attendance by employee -> present/late counts + average minutes
    if not attendance_df.empty:
        grouped = attendance_df.groupby(["employee_id", "name", "department"]).agg(
            present=("status", lambda s: (s == "Present").sum()),
            late=("status", lambda s: (s == "Late").sum()),
            avg_minutes=("working_minutes", "mean"),
        ).reset_index()
    else:
        grouped = pd.DataFrame(columns=["employee_id", "name", "department", "present", "late", "avg_minutes"])

    # 4. Merge onto the full employee list so everyone appears, even with 0 records
    merged = employees_df.merge(grouped, on=["employee_id", "name", "department"], how="left")
    merged["present"] = pd.to_numeric(merged["present"], errors="coerce").fillna(0).astype(int)
    merged["late"] = pd.to_numeric(merged["late"], errors="coerce").fillna(0).astype(int)

    if working_days > 0:
        merged["absent"] = (working_days - merged["present"] - merged["late"]).clip(lower=0)
        merged["attendance_pct"] = ((merged["present"] + merged["late"]) / working_days * 100).round(1)
    else:
        merged["absent"] = 0
        merged["attendance_pct"] = 0.0

    merged["avg_working_hours"] = merged["avg_minutes"].apply(minutes_to_label)

    # 5. Overall summary numbers (shown at the top of the report)
    total_employees = len(employees_df)
    total_present = int(merged["present"].sum())
    total_late = int(merged["late"].sum())
    total_absent = int(merged["absent"].sum())
    denominator = total_employees * working_days
    attendance_rate = round((total_present + total_late) / denominator * 100, 1) if denominator > 0 else 0

    summary = {
        "total_employees": total_employees,
        "present": total_present,
        "late": total_late,
        "absent": total_absent,
        "working_days": working_days,
        "attendance_rate": attendance_rate,
    }

    # 6. Department-wise stats, for the bar chart
    dept_employee_counts = employees_df.groupby("department").size() if not employees_df.empty else pd.Series(dtype=int)
    dept_group = merged.groupby("department").agg(present=("present", "sum"), late=("late", "sum")).reset_index()

    department_labels, department_rates = [], []
    for dept in DEPARTMENTS:
        emp_count = int(dept_employee_counts.get(dept, 0))
        row = dept_group[dept_group["department"] == dept]
        attended = int(row["present"].iloc[0] + row["late"].iloc[0]) if not row.empty else 0
        dept_denominator = emp_count * working_days
        rate = round(attended / dept_denominator * 100, 1) if dept_denominator > 0 else 0
        department_labels.append(dept)
        department_rates.append(rate)

    employee_rows = merged.sort_values("name").to_dict("records")

    return {
        "summary": summary,
        "employees": employee_rows,
        "department_labels": department_labels,
        "department_rates": department_rates,
    }


def get_monthly_trend_last_6_months():
    """
    Builds the "Monthly Attendance Trend" line chart data:
    for each of the last 6 months, what % of possible attendance
    was actually recorded? Uses NumPy's busday_count for working days.
    """
    today = date.today()
    labels, rates = [], []

    # Walk backwards from the current month, 6 months total
    for i in range(5, -1, -1):
        month_index = today.month - i
        year = today.year
        while month_index <= 0:
            month_index += 12
            year -= 1

        month_start, month_end = get_month_range(f"{year}-{month_index:02d}")
        month_end = min(month_end, today)
        if month_end < month_start:
            labels.append(month_start.strftime("%b"))
            rates.append(0)
            continue

        report = build_report(month_start, month_end)
        labels.append(month_start.strftime("%b"))
        rates.append(report["summary"]["attendance_rate"])

    return labels, rates


@app.route("/report")
@login_required
@admin_required
def report():
    start_date, end_date, period, label = get_report_date_range()
    report_data = build_report(start_date, end_date)
    trend_labels, trend_rates = get_monthly_trend_last_6_months()

    # Optional: admin clicked a specific employee to see their individual report
    selected_employee = None
    employee_id_param = request.args.get("employee_id")
    if employee_id_param:
        for row in report_data["employees"]:
            if row["employee_id"] == employee_id_param:
                selected_employee = row
                break

    today = date.today()
    return render_template(
        "report.html",
        active_page="report",
        period=period,
        label=label,
        selected_date=request.args.get("date", today.isoformat()),
        selected_month=request.args.get("month", today.strftime("%Y-%m")),
        selected_year=request.args.get("year", str(today.year)),
        summary=report_data["summary"],
        employees=report_data["employees"],
        department_labels=report_data["department_labels"],
        department_rates=report_data["department_rates"],
        trend_labels=trend_labels,
        trend_rates=trend_rates,
        selected_employee=selected_employee,
        query_string=request.query_string.decode(),
    )


# ==========================================================================
# CSV EXPORT (uses the same date range currently shown on the Report page)
# ==========================================================================

@app.route("/export-csv")
@login_required
@admin_required
def export_csv():
    start_date, end_date, period, label = get_report_date_range()

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT e.employee_id, e.name, a.date, a.status, a.login_time, a.logout_time
        FROM attendance a
        JOIN employees e ON e.id = a.employee_id
        WHERE a.date BETWEEN %s AND %s AND e.role = 'employee'
        ORDER BY a.date, e.name
    """, (start_date, end_date))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    # Build the CSV in memory using Python's built-in csv module
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Employee ID", "Employee Name", "Date", "Status", "Login Time", "Logout Time", "Working Hours"])

    for row in rows:
        minutes = working_minutes_between(row["login_time"], row["logout_time"])
        writer.writerow([
            row["employee_id"],
            row["name"],
            row["date"],
            row["status"],
            timedelta_to_ampm(row["login_time"]) or "--",
            timedelta_to_ampm(row["logout_time"]) or "--",
            minutes_to_label(minutes),
        ])

    filename = f"attendance_report_{start_date}_{end_date}.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )



# ==========================================================================
# ERROR HANDLERS
# ==========================================================================

@app.errorhandler(404)
def page_not_found(e):
    return render_template("404.html"), 404


@app.errorhandler(500)
def server_error(e):
    return render_template("500.html"), 500


@app.errorhandler(mysql.connector.Error)
def handle_db_error(e):
    # Never show raw SQL errors to the user - just log them and show a friendly page
    print("Database error:", e)
    return render_template("500.html"), 500


if __name__ == "__main__":
    app.run(debug=True)
