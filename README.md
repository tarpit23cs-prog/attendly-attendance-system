# Attendly

Attendly is a simple Employee Attendance & Report System built with **Python Flask**
and **MySQL**. Employees can clock in/out and view their attendance history, while
an admin can manage employees, view daily attendance, and generate attendance
reports powered by **Pandas** and **NumPy**.

This project was built to be **beginner-friendly and easy to explain in an
interview** — there is no advanced architecture, no REST API layer, no ORM, and
no authentication frameworks. Just plain Flask routes, plain SQL, and Jinja
templates.

---

## Features

**Employee**
- Login / Logout
- Clock In / Clock Out (login & logout time are recorded automatically)
- View today's attendance status and working hours
- View full attendance history
- See attendance percentage over the last 30 days

**Admin**
- Login / Logout
- Dashboard with today's Present / Absent / Late counts
- Add, edit, and deactivate employees (no hard deletes)
- View attendance for any date, filterable by department and status
- Generate Daily / Monthly / Yearly attendance reports (built with Pandas)
- View charts: Present vs Absent vs Late, Monthly Trend, Department Attendance
- Export attendance data as CSV
- Export attendance summary as PDF

---

## Technologies

| Layer      | Tech |
|------------|------|
| Backend    | Python 3.11+, Flask |
| Database   | MySQL, mysql-connector-python |
| Data/Reports | Pandas, NumPy |
| Frontend   | HTML, CSS, Bootstrap 5, Bootstrap Icons |
| Charts     | Chart.js (via CDN) |
| Templates  | Jinja2 |
| PDF Export | ReportLab |
| Security   | Werkzeug password hashing, Flask sessions |

---

## Project Structure

```
attendly/
│
├── app.py                 # All Flask routes and logic
├── db.py                  # MySQL connection helper
├── config.py               # Loads settings from .env
├── requirements.txt
├── .env.example
├── .gitignore
│
├── database/
│   ├── schema.sql          # Table definitions
│   └── seed.sql             # Sample data (1 admin + 14 employees, 1 month of attendance)
│
├── templates/               # Jinja2 HTML templates
└── static/
    ├── css/style.css
    └── js/script.js
```

---

## Database Setup

1. Log in to MySQL and create the database:

```sql
CREATE DATABASE attendly;
```

2. From the project folder, load the schema and sample data:

```bash
mysql -u root -p attendly < database/schema.sql
mysql -u root -p attendly < database/seed.sql
```

(Or from inside the MySQL shell: `USE attendly;` then `SOURCE database/schema.sql;`
and `SOURCE database/seed.sql;`)

---

## Python Setup

**Windows**
```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

**macOS / Linux**
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

---

## Environment Setup

Copy `.env.example` to `.env` and fill in your own MySQL credentials:

```
DB_HOST=localhost
DB_USER=root
DB_PASSWORD=your_mysql_password
DB_NAME=attendly
SECRET_KEY=change-this-to-a-random-secret-key
```

Never commit your real `.env` file — it's already in `.gitignore`.

---

## Run the App

```bash
python app.py
```

Then open **http://127.0.0.1:5000** in your browser.

---

## Default Credentials

**Admin**
- Email: `admin@attendly.com`
- Password: `Admin@123`

**Employee**
- Email: `rahul@attendly.com`
- Password: `Employee@123`

(All 14 seeded employees use the password `Employee@123`. See `database/seed.sql`
for the full list of emails.)

---

## How Attendance Works

1. **Clock In** — When an employee clicks "Clock In", Flask records the current
   date and time and inserts a new row into the `attendance` table. The status
   (`Present` or `Late`) is decided by comparing the clock-in time against the
   office start time + a 15-minute grace period (both configurable in
   `config.py`).

2. **Clock Out** — When the employee clicks "Clock Out", Flask finds today's
   attendance row and updates its `logout_time`.

3. **Working Hours** — Calculated in Python using simple `datetime` subtraction
   between login time and logout time (e.g. 09:12 to 18:03 = "8h 51m").

4. **Absent Employees** — Attendly does **not** insert an "Absent" row for every
   employee every day. Instead, on the Attendance and Dashboard pages, any
   active employee with no attendance record for a working day is calculated
   as Absent in Python. Weekends are excluded from this calculation.

5. **Reports** — The `/report` page fetches raw attendance rows from MySQL,
   loads them into a **Pandas DataFrame**, and uses `groupby()` to calculate
   Present/Late counts and average working hours per employee. **NumPy's
   `busday_count()`** is used to calculate how many working days (Monday-Friday)
   fall inside the selected date range, which is needed to calculate Absent
   days and the attendance percentage.

---

## Where Pandas and NumPy Are Used

- **Pandas** (`build_report()` in `app.py`): loading attendance rows into a
  DataFrame, grouping by employee to count Present/Late, calculating average
  working hours per employee, and grouping by department for the department
  attendance chart.
- **NumPy** (`np.busday_count()`): calculating the number of working days
  (Mon-Fri) in a given date range — used for both the Absent-day calculation
  and the attendance percentage on the Report page and the Employee Dashboard.

---

## Notes

- Employees are **never deleted** — an admin can only mark them
  `active`/`inactive`.
- Passwords are hashed with Werkzeug (`generate_password_hash` /
  `check_password_hash`) — nothing is stored in plain text.
- This project intentionally avoids Flask Blueprints, JWT, and REST API design
  to keep the codebase simple and easy to explain.
