import os
import sqlite3
from datetime import datetime
from functools import wraps

from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from matcher import analyze_match
from resume_parser import clean_text, read_docx, read_pdf


BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATABASE = os.path.join(BASE_DIR, "resume_screening.db")

app = Flask(__name__)
app.config["SECRET_KEY"] = "change-this-secret-key"


def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                full_name TEXT DEFAULT '',
                email TEXT DEFAULT '',
                phone TEXT DEFAULT '',
                skills TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                resume_name TEXT NOT NULL,
                job_title TEXT NOT NULL,
                score REAL NOT NULL,
                category TEXT NOT NULL,
                matched_skills TEXT DEFAULT '',
                missing_skills TEXT DEFAULT '',
                recommendation TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
            """
        )
        admin = conn.execute(
            "SELECT id FROM users WHERE username = ?", ("admin",)
        ).fetchone()
        if admin is None:
            conn.execute(
                """
                INSERT INTO users (
                    username, password_hash, role, full_name, email, phone,
                    skills, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "admin",
                    generate_password_hash("admin"),
                    "admin",
                    "System Administrator",
                    "admin@example.com",
                    "",
                    "python, machine learning, data science",
                    datetime.now().strftime("%Y-%m-%d %H:%M"),
                ),
            )


@app.context_processor
def inject_user():
    return {"current_user": get_current_user()}


def get_current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    with get_db() as conn:
        return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            flash("Please log in first.", "warning")
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = get_current_user()
        if not user or user["role"] != "admin":
            flash("Admin access is required.", "danger")
            return redirect(url_for("dashboard"))
        return view(*args, **kwargs)

    return wrapped


def match_category(score):
    if score >= 85:
        return "Great Match"
    if score >= 70:
        return "Good Match"
    if score >= 40:
        return "Average Match"
    return "Poor Match"


def recommendation_for(score, missing_skills):
    if score >= 85:
        return "Strong candidate match. Move forward with interview screening."
    if score >= 70:
        return "Good match. Review experience details and validate the missing skills."
    if score >= 40:
        return "Partial match. Training or a junior role may be more suitable."
    if missing_skills:
        return "Low match. Candidate should improve the missing skills before applying."
    return "Low match. Add clearer job skills to improve the analysis."


def read_resume_file(resume):
    filename = resume.filename.lower()
    if filename.endswith(".pdf"):
        return read_pdf(resume)
    if filename.endswith(".docx"):
        return read_docx(resume)
    raise ValueError("Only PDF and DOCX resumes are supported.")


@app.route("/")
def index():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip()

        if len(username) < 3 or len(password) < 4:
            flash("Username must be 3+ characters and password 4+ characters.", "danger")
            return render_template("register.html")

        try:
            with get_db() as conn:
                conn.execute(
                    """
                    INSERT INTO users (
                        username, password_hash, role, full_name, email, created_at
                    )
                    VALUES (?, ?, 'user', ?, ?, ?)
                    """,
                    (
                        username,
                        generate_password_hash(password),
                        full_name,
                        email,
                        datetime.now().strftime("%Y-%m-%d %H:%M"),
                    ),
                )
            flash("Registration successful. Please log in.", "success")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("That username is already registered.", "danger")

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]

        with get_db() as conn:
            user = conn.execute(
                "SELECT * FROM users WHERE username = ?", (username,)
            ).fetchone()

        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["role"] = user["role"]
            flash("Login successful.", "success")
            return redirect(url_for("dashboard"))

        flash("Invalid username or password.", "danger")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    user = get_current_user()
    with get_db() as conn:
        if user["role"] == "admin":
            total_users = conn.execute(
                "SELECT COUNT(*) AS count FROM users WHERE role = 'user'"
            ).fetchone()["count"]
            total_reports = conn.execute(
                "SELECT COUNT(*) AS count FROM reports"
            ).fetchone()["count"]
            recent_reports = conn.execute(
                """
                SELECT reports.*, users.username
                FROM reports
                JOIN users ON users.id = reports.user_id
                ORDER BY reports.id DESC
                LIMIT 5
                """
            ).fetchall()
        else:
            total_users = None
            total_reports = conn.execute(
                "SELECT COUNT(*) AS count FROM reports WHERE user_id = ?",
                (user["id"],),
            ).fetchone()["count"]
            recent_reports = conn.execute(
                """
                SELECT reports.*, users.username
                FROM reports
                JOIN users ON users.id = reports.user_id
                WHERE reports.user_id = ?
                ORDER BY reports.id DESC
                LIMIT 5
                """,
                (user["id"],),
            ).fetchall()

    return render_template(
        "dashboard.html",
        total_users=total_users,
        total_reports=total_reports,
        recent_reports=recent_reports,
    )


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    user = get_current_user()
    if request.method == "POST":
        with get_db() as conn:
            conn.execute(
                """
                UPDATE users
                SET full_name = ?, email = ?, phone = ?, skills = ?
                WHERE id = ?
                """,
                (
                    request.form.get("full_name", "").strip(),
                    request.form.get("email", "").strip(),
                    request.form.get("phone", "").strip(),
                    request.form.get("skills", "").strip(),
                    user["id"],
                ),
            )
        flash("Profile updated.", "success")
        return redirect(url_for("profile"))
    return render_template("profile.html", user=user)


@app.route("/match", methods=["GET", "POST"])
@login_required
def match():
    if request.method == "POST":
        resume = request.files.get("resume")
        job_description = request.form.get("job_description", "")
        job_title = request.form.get("job_title", "Untitled Job").strip() or "Untitled Job"

        if not resume or not resume.filename:
            flash("Please upload a resume.", "danger")
            return redirect(url_for("match"))

        try:
            resume_text = clean_text(read_resume_file(resume))
            job_text = clean_text(job_description)
            analysis = analyze_match(resume_text, job_text)
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("match"))

        score = analysis["score"]
        category = match_category(score)
        missing_skills = sorted(analysis["missing_skills"])
        matched_skills = sorted(analysis["matched_skills"])
        recommendation = recommendation_for(score, missing_skills)

        with get_db() as conn:
            cursor = conn.execute(
                """
                INSERT INTO reports (
                    user_id, resume_name, job_title, score, category,
                    matched_skills, missing_skills, recommendation, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session["user_id"],
                    resume.filename,
                    job_title,
                    score,
                    category,
                    ", ".join(matched_skills),
                    ", ".join(missing_skills),
                    recommendation,
                    datetime.now().strftime("%Y-%m-%d %H:%M"),
                ),
            )
            report_id = cursor.lastrowid

        return redirect(url_for("report_detail", report_id=report_id))

    return render_template("match.html")


@app.route("/reports")
@login_required
def reports():
    user = get_current_user()
    with get_db() as conn:
        if user["role"] == "admin":
            rows = conn.execute(
                """
                SELECT reports.*, users.username
                FROM reports
                JOIN users ON users.id = reports.user_id
                ORDER BY reports.id DESC
                """
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT reports.*, users.username
                FROM reports
                JOIN users ON users.id = reports.user_id
                WHERE reports.user_id = ?
                ORDER BY reports.id DESC
                """,
                (user["id"],),
            ).fetchall()
    return render_template("reports.html", reports=rows)


@app.route("/reports/<int:report_id>")
@login_required
def report_detail(report_id):
    user = get_current_user()
    with get_db() as conn:
        report = conn.execute(
            """
            SELECT reports.*, users.username, users.full_name, users.email
            FROM reports
            JOIN users ON users.id = reports.user_id
            WHERE reports.id = ?
            """,
            (report_id,),
        ).fetchone()

    if not report:
        flash("Report not found.", "danger")
        return redirect(url_for("reports"))
    if user["role"] != "admin" and report["user_id"] != user["id"]:
        flash("You cannot view that report.", "danger")
        return redirect(url_for("reports"))

    return render_template("report_detail.html", report=report)


@app.route("/performance")
@login_required
def performance():
    user = get_current_user()
    with get_db() as conn:
        if user["role"] == "admin":
            rows = conn.execute("SELECT score, category FROM reports").fetchall()
        else:
            rows = conn.execute(
                "SELECT score, category FROM reports WHERE user_id = ?", (user["id"],)
            ).fetchall()

    total = len(rows)
    average_score = round(sum(row["score"] for row in rows) / total, 2) if total else 0
    buckets = {
        "Great Match": 0,
        "Good Match": 0,
        "Average Match": 0,
        "Poor Match": 0,
    }
    for row in rows:
        buckets[row["category"]] += 1

    return render_template(
        "performance.html",
        total=total,
        average_score=average_score,
        buckets=buckets,
    )


@app.route("/admin/users")
@admin_required
def admin_users():
    with get_db() as conn:
        users = conn.execute("SELECT * FROM users ORDER BY id DESC").fetchall()
    return render_template("admin_users.html", users=users)


@app.route("/admin/users/add", methods=["GET", "POST"])
@admin_required
def admin_add_user():
    if request.method == "POST":
        try:
            with get_db() as conn:
                conn.execute(
                    """
                    INSERT INTO users (
                        username, password_hash, role, full_name, email, phone,
                        skills, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        request.form["username"].strip(),
                        generate_password_hash(request.form["password"]),
                        request.form.get("role", "user"),
                        request.form.get("full_name", "").strip(),
                        request.form.get("email", "").strip(),
                        request.form.get("phone", "").strip(),
                        request.form.get("skills", "").strip(),
                        datetime.now().strftime("%Y-%m-%d %H:%M"),
                    ),
                )
            flash("User added.", "success")
            return redirect(url_for("admin_users"))
        except sqlite3.IntegrityError:
            flash("That username already exists.", "danger")
    return render_template("admin_user_form.html", edited_user=None)


@app.route("/admin/users/<int:user_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_edit_user(user_id):
    with get_db() as conn:
        edited_user = conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    if not edited_user:
        flash("User not found.", "danger")
        return redirect(url_for("admin_users"))

    if request.method == "POST":
        password = request.form.get("password", "")
        with get_db() as conn:
            if password:
                conn.execute(
                    """
                    UPDATE users
                    SET username = ?, password_hash = ?, role = ?, full_name = ?,
                        email = ?, phone = ?, skills = ?
                    WHERE id = ?
                    """,
                    (
                        request.form["username"].strip(),
                        generate_password_hash(password),
                        request.form.get("role", "user"),
                        request.form.get("full_name", "").strip(),
                        request.form.get("email", "").strip(),
                        request.form.get("phone", "").strip(),
                        request.form.get("skills", "").strip(),
                        user_id,
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE users
                    SET username = ?, role = ?, full_name = ?, email = ?,
                        phone = ?, skills = ?
                    WHERE id = ?
                    """,
                    (
                        request.form["username"].strip(),
                        request.form.get("role", "user"),
                        request.form.get("full_name", "").strip(),
                        request.form.get("email", "").strip(),
                        request.form.get("phone", "").strip(),
                        request.form.get("skills", "").strip(),
                        user_id,
                    ),
                )
        flash("User updated.", "success")
        return redirect(url_for("admin_users"))

    return render_template("admin_user_form.html", edited_user=edited_user)


@app.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@admin_required
def admin_delete_user(user_id):
    current_user = get_current_user()
    if user_id == current_user["id"]:
        flash("You cannot delete your own admin account.", "danger")
        return redirect(url_for("admin_users"))

    with get_db() as conn:
        conn.execute("DELETE FROM reports WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    flash("User deleted.", "success")
    return redirect(url_for("admin_users"))


init_db()


if __name__ == "__main__":
    app.run(debug=True)
