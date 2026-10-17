"""EditMatch: a website that connects video editors with clients.

Start the website with:  python app.py
Then open http://127.0.0.1:5000 in your browser.

Reading order for beginners:
1. This file: routes (web addresses), login checks, forms and uploads.
2. database.py: the tables we store data in.
3. matching_service.py: how editors are scored.
4. tinyfish_service.py: how TinyFish reads public web pages.
"""

import json
import logging
import os
import re
import secrets
import sqlite3
import uuid
from datetime import date
from functools import wraps
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from flask import (
    Flask, abort, flash, g, redirect, render_template, request, session, url_for,
)
from flask_wtf.csrf import CSRFProtect
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.security import check_password_hash, generate_password_hash

import database
import matching_service as matching
import tinyfish_service as tinyfish

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")  # reads TINYFISH_API_KEY and friends from the .env file

logger = logging.getLogger(__name__)

MB = 1024 * 1024
MAX_SAMPLES = 5
PHOTO_MAX_BYTES = 5 * MB
VIDEO_MAX_BYTES = int(os.environ.get("MAX_VIDEO_MB", "50")) * MB
PHOTO_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
VIDEO_EXTENSIONS = {"mp4", "webm", "mov"}
REFERENCE_EXTENSIONS = PHOTO_EXTENSIONS | VIDEO_EXTENSIONS
CLOSED_JOB_STATUSES = ("Completed", "Cancelled")
JOB_STATUSES = [
    "Draft", "Submitted", "Matching", "Matches Found", "Invitation Sent",
    "In Discussion", "Completed", "Cancelled",
]
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

app = Flask(__name__)
app.config.update(
    # The secret key signs the login cookie. Set FLASK_SECRET_KEY in .env for real use.
    SECRET_KEY=os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32),
    DATABASE=str(BASE_DIR / "instance" / "editmatch.db"),
    UPLOAD_FOLDER=str(BASE_DIR / "static" / "uploads"),
    # A request can hold up to five videos, a photo and a reference file.
    MAX_CONTENT_LENGTH=7 * VIDEO_MAX_BYTES,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE") == "1",
)
if not os.environ.get("FLASK_SECRET_KEY"):
    logger.warning("FLASK_SECRET_KEY is not set. Logins will reset when the server restarts.")

CSRFProtect(app)  # every POST form must include a hidden csrf_token field
database.init_app(app)


# ---------------------------------------------------------------------------
# Login and access control
# ---------------------------------------------------------------------------

def current_user():
    """Return the logged-in user's row from the database, or None."""
    if "user_id" not in session:
        return None
    if "current_user" not in g:
        g.current_user = database.get_db().execute(
            "SELECT * FROM users WHERE id = ?", (session["user_id"],)
        ).fetchone()
    return g.current_user


def dashboard_url_for(user):
    return url_for("editor_dashboard") if user["role"] == "editor" else url_for("client_dashboard")


def login_required(role=None):
    """Decorator: the page needs a logged-in user, and optionally a specific role."""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = current_user()
            if user is None:
                flash("Please log in to continue.", "error")
                return redirect(url_for("login"))
            if role and user["role"] != role:
                flash(f"That page is only for {role} accounts.", "error")
                return redirect(dashboard_url_for(user))
            return view(*args, **kwargs)
        return wrapped
    return decorator


@app.context_processor
def inject_template_values():
    """Values that every template can use without being passed in each route."""
    user = current_user()
    return {
        "current_user": user,
        "dashboard_url": dashboard_url_for(user) if user else None,
        "category_options": matching.CATEGORY_OPTIONS,
        "game_options": matching.GAME_OPTIONS,
        "technique_options": matching.TECHNIQUE_OPTIONS,
        "experience_options": matching.EXPERIENCE_OPTIONS,
        "pricing_options": matching.PRICING_MODELS,
        "availability_options": matching.AVAILABILITY_OPTIONS,
        "contact_options": matching.CONTACT_OPTIONS,
        "platform_options": matching.PLATFORM_OPTIONS,
        "video_type_options": matching.VIDEO_TYPE_OPTIONS,
        "aspect_options": matching.ASPECT_RATIO_OPTIONS,
        "voiceover_options": matching.VOICEOVER_OPTIONS,
        "delivery_options": matching.DELIVERY_OPTIONS,
        "job_statuses": JOB_STATUSES,
        "tinyfish_labels": tinyfish.STATUS_LABELS,
        "tinyfish_configured": tinyfish.is_configured(),
        "max_samples": MAX_SAMPLES,
        "video_limit_mb": VIDEO_MAX_BYTES // MB,
        "photo_limit_mb": PHOTO_MAX_BYTES // MB,
    }


# ---------------------------------------------------------------------------
# Reading and checking form input
# ---------------------------------------------------------------------------

def is_web_link(value):
    """True for a normal http or https link. This only checks the format."""
    parsed = urlparse(value or "")
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def read_text(source, name, max_length, errors, label, required=False):
    value = source.get(name, "").strip()
    if required and not value:
        errors.append(f"{label} is required.")
    if len(value) > max_length:
        errors.append(f"{label} must be {max_length} characters or fewer.")
    return value[:max_length]


def read_int(source, name, errors, label, minimum=0, maximum=None, required=False):
    raw = source.get(name, "").strip().replace(",", "")
    if not raw:
        if required:
            errors.append(f"{label} is required.")
        return None
    if not raw.isdigit():
        errors.append(f"{label} must be a whole number.")
        return None
    value = int(raw)
    if value < minimum or (maximum is not None and value > maximum):
        errors.append(f"{label} must be between {minimum} and {maximum if maximum is not None else 'a large number'}.")
        return None
    return value


def read_choice(source, name, options, errors, label, required=False):
    value = source.get(name, "").strip()
    if not value:
        if required:
            errors.append(f"Please choose a {label}.")
        return ""
    if value not in options:
        errors.append(f"Please choose a valid {label}.")
        return ""
    return value


def read_choices(source, name, options):
    """Return the checkbox values that are in the allowed list. Anything else is ignored."""
    return [value for value in source.getlist(name) if value in options]


def read_custom_list(source, name, errors, label):
    """Read a comma-separated 'Other' field, such as 'Rocket League, Dota 2'."""
    items = matching.split_csv(source.get(name, ""))
    if len(items) > 5:
        errors.append(f"{label}: please list no more than five items.")
    for item in items:
        if len(item) > 40:
            errors.append(f"{label}: each item must be 40 characters or fewer.")
    return items[:5]


def read_delivery(source, name, errors, label="Typical delivery time"):
    raw = source.get(name, "").strip()
    if not raw:
        return None
    if not raw.isdigit() or int(raw) not in matching.DELIVERY_OPTIONS.values():
        errors.append(f"Please choose a valid {label.lower()}.")
        return None
    return int(raw)


def read_date(source, name, errors, label):
    raw = source.get(name, "").strip()
    if not raw:
        return ""
    try:
        chosen = date.fromisoformat(raw)
    except ValueError:
        errors.append(f"{label} is not a valid date.")
        return ""
    if chosen < date.today():
        errors.append(f"{label} cannot be in the past.")
        return ""
    return chosen.isoformat()


def safe_return_path(target, default):
    """Only redirect to a page on this website, never to another site."""
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return default


def posted_values(form):
    """Copy submitted form data so the page can show it again after an error."""
    values = {}
    for key in form.keys():
        if key in ("categories", "games", "skill_tags", "effects") or key.endswith("_techniques"):
            values[key] = form.getlist(key)
        else:
            values[key] = form.get(key, "")
    return values


# ---------------------------------------------------------------------------
# Uploaded files
# ---------------------------------------------------------------------------

def file_signature_ok(data, extension):
    """Check the first bytes of a file, so a renamed text file cannot pass as a video."""
    if extension in ("jpg", "jpeg"):
        return data.startswith(b"\xff\xd8\xff")
    if extension == "png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if extension == "webp":
        return data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    if extension in ("mp4", "mov"):
        return data[4:8] in (b"ftyp", b"moov", b"mdat", b"wide")
    if extension == "webm":
        return data.startswith(b"\x1a\x45\xdf\xa3")
    return False


def read_upload(file_storage, allowed_extensions, max_bytes, label, errors):
    """Check an uploaded file without saving it yet.

    Returns (extension, data) when the file is valid. Returns None when no file
    was chosen or when the file is invalid. Problems are added to errors.
    """
    if file_storage is None or not file_storage.filename:
        return None
    extension = file_storage.filename.rsplit(".", 1)[-1].lower() if "." in file_storage.filename else ""
    if extension not in allowed_extensions:
        errors.append(f"{label} must be one of: {', '.join(sorted(allowed_extensions))}.")
        return None
    data = file_storage.read(max_bytes + 1)  # read one byte past the limit to detect big files
    if not data:
        errors.append(f"{label} is empty.")
        return None
    if len(data) > max_bytes:
        errors.append(f"{label} is larger than {max_bytes // MB} MB.")
        return None
    if not file_signature_ok(data, extension):
        errors.append(f"{label} does not look like a valid .{extension} file.")
        return None
    return extension, data


def store_upload(folder, extension, data):
    """Save file bytes with a random name. The original file name is never used."""
    name = f"{uuid.uuid4().hex}.{extension}"
    target_folder = Path(app.config["UPLOAD_FOLDER"]) / folder
    target_folder.mkdir(parents=True, exist_ok=True)
    (target_folder / name).write_bytes(data)
    return f"uploads/{folder}/{name}"


def delete_upload(relative_path):
    """Delete an uploaded file. Only paths inside static/uploads are allowed."""
    if not relative_path or not relative_path.startswith("uploads/"):
        return
    target = Path(app.root_path) / "static" / relative_path
    target.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Loading editor profiles from the database
# ---------------------------------------------------------------------------

def get_profile_row(user_id):
    return database.get_db().execute(
        "SELECT * FROM editor_profiles WHERE user_id = ?", (user_id,)
    ).fetchone()


def load_profile_for_user(user_id):
    """The logged-in editor's full profile (with name and samples), or None."""
    rows = fetch_editor_profiles("WHERE ep.user_id = ?", (user_id,))
    return rows[0] if rows else None


def fetch_samples(editor_id):
    rows = database.get_db().execute(
        "SELECT * FROM portfolio_samples WHERE editor_id = ? ORDER BY position, id", (editor_id,)
    ).fetchall()
    samples = []
    for row in rows:
        sample = dict(row)
        sample["tag_list"] = matching.split_csv(sample["skill_tags"])
        samples.append(sample)
    return samples


def hydrate_profile(row):
    """Turn a database row into a dictionary with ready-to-use lists."""
    profile = dict(row)
    profile["category_list"] = matching.split_csv(profile["categories"])
    profile["game_list"] = matching.split_csv(profile["games"])
    profile["skill_list"] = matching.split_csv(profile["skill_tags"])
    profile["samples"] = fetch_samples(profile["id"])
    profile["analysis"] = json.loads(profile["tinyfish_analysis_json"]) if profile["tinyfish_analysis_json"] else None
    return profile


def fetch_editor_profiles(where="", params=(), limit=None):
    """Load editor profiles. The where text is written in this file, never typed by users."""
    sql = (
        "SELECT ep.*, u.full_name, u.is_demo FROM editor_profiles ep "
        "JOIN users u ON u.id = ep.user_id " + where + " ORDER BY u.full_name"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = database.get_db().execute(sql, params).fetchall()
    return [hydrate_profile(row) for row in rows]


def profile_completion(profile):
    """Percentage of the profile that is filled in. Profile fields are 60%, samples are 40%."""
    checks = [
        bool(profile["full_name"]), bool(profile["profile_photo"]), bool(profile["bio"]),
        bool(profile["category_list"]), bool(profile["skill_list"]),
        profile["minimum_price"] is not None, profile["maximum_price"] is not None,
        profile["delivery_hours"] is not None, bool(profile["contact_email"]),
        bool(profile["availability"]),
    ]
    field_share = sum(checks) / len(checks)
    sample_share = min(len(profile["samples"]), MAX_SAMPLES) / MAX_SAMPLES
    return round(100 * (0.6 * field_share + 0.4 * sample_share))


def profile_values(profile):
    """Form values for the edit page, taken from the saved profile."""
    values = dict(profile)
    values["full_name"] = profile["full_name"]
    values["categories"] = profile["category_list"]
    values["games"] = profile["game_list"]
    values["skill_tags"] = profile["skill_list"]
    values["other_category"] = ", ".join(c for c in profile["category_list"] if c not in matching.CATEGORY_OPTIONS)
    values["other_games"] = ", ".join(g for g in profile["game_list"] if g not in matching.GAME_OPTIONS)
    values["other_technique"] = ", ".join(t for t in profile["skill_list"] if t not in matching.TECHNIQUE_OPTIONS)
    for index, sample in enumerate(profile["samples"], start=1):
        values[f"sample_{index}_id"] = sample["id"]
        values[f"sample_{index}_title"] = sample["title"]
        values[f"sample_{index}_category"] = sample["category"]
        values[f"sample_{index}_game"] = sample["game"]
        values[f"sample_{index}_description"] = sample["description"]
        values[f"sample_{index}_software"] = sample["software_used"]
        values[f"sample_{index}_video_url"] = sample["external_video_url"]
        values[f"sample_{index}_uploaded"] = sample["uploaded_video_path"]
        values[f"sample_{index}_techniques"] = sample["tag_list"]
    return values


def default_values(user):
    """Form values for a brand-new profile."""
    return {
        "full_name": user["full_name"], "contact_email": user["email"], "availability": "Available",
        "categories": [], "games": [], "skill_tags": [], "price_negotiable": 0,
        "other_category": "", "other_games": "", "other_technique": "",
    }


def contact_visible_to(profile, user):
    """Private contact details are shown only to the owner, or to a client whose invitation was accepted."""
    if user is None:
        return False
    if user["id"] == profile["user_id"]:
        return True
    if user["role"] != "client":
        return False
    accepted = database.get_db().execute(
        "SELECT 1 FROM invitations i JOIN job_requests j ON j.id = i.job_id "
        "WHERE i.editor_id = ? AND j.client_id = ? AND i.status = 'Accepted' LIMIT 1",
        (profile["id"], user["id"]),
    ).fetchone()
    return accepted is not None


# ---------------------------------------------------------------------------
# TinyFish: saving what the agent found
# ---------------------------------------------------------------------------

def record_tinyfish_run(purpose, source_url, outcome, editor_id=None, job_id=None):
    """Keep a history row for every TinyFish attempt."""
    db = database.get_db()
    db.execute(
        "INSERT INTO tinyfish_runs (purpose, editor_id, job_id, source_url, run_id, status, message) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (purpose, editor_id, job_id, source_url, outcome.get("run_id") or "", outcome["status"], outcome["message"]),
    )


def run_portfolio_analysis(profile_id, force=False):
    """Read the editor's public portfolio with TinyFish and save the result.

    Results are saved, so a page is not read again unless force is True.
    Returns a message to show the user.
    """
    db = database.get_db()
    row = db.execute("SELECT * FROM editor_profiles WHERE id = ?", (profile_id,)).fetchone()
    url = row["portfolio_url"]
    if not url:
        save_analysis("editor_profiles", profile_id, "missing_url", None)
        return tinyfish.STATUS_LABELS["missing_url"]

    cleaned_url, error = tinyfish.validate_public_url(url)
    if error:
        save_analysis("editor_profiles", profile_id, "invalid_url", None)
        return error

    if not force and row["tinyfish_analysis_status"] == "completed" and row["tinyfish_analysis_json"]:
        saved = json.loads(row["tinyfish_analysis_json"])
        if saved.get("source_url") == cleaned_url:
            return "Showing the saved TinyFish results. Use Retry to read the page again."

    outcome = tinyfish.analyse_editor_portfolio(cleaned_url)
    save_analysis("editor_profiles", profile_id, outcome["status"], outcome.get("data"))
    record_tinyfish_run("portfolio", cleaned_url, outcome, editor_id=profile_id)
    db.commit()
    return outcome["message"]


def run_reference_analysis(job_id, url):
    """Read the client's reference page with TinyFish and save the result."""
    cleaned_url, error = tinyfish.validate_public_url(url)
    db = database.get_db()
    if error:
        save_analysis("job_requests", job_id, "invalid_url", None)
        return error
    outcome = tinyfish.analyse_reference_url(cleaned_url)
    save_analysis("job_requests", job_id, outcome["status"], outcome.get("data"))
    record_tinyfish_run("reference", cleaned_url, outcome, job_id=job_id)
    db.commit()
    return outcome["message"]


def save_analysis(table, row_id, status, data):
    """Save an analysis status and its data, then commit. The table is always one of the two names below."""
    db = database.get_db()
    if table == "editor_profiles":
        db.execute(
            "UPDATE editor_profiles SET tinyfish_analysis_status = ?, tinyfish_analysis_json = ?, "
            "tinyfish_last_checked = datetime('now') WHERE id = ?",
            (status, json.dumps(data) if data else None, row_id),
        )
    else:
        db.execute(
            "UPDATE job_requests SET reference_analysis_status = ?, reference_analysis_json = ?, "
            "updated_at = datetime('now') WHERE id = ?",
            (status, json.dumps(data) if data else None, row_id),
        )
    db.commit()


# ---------------------------------------------------------------------------
# Matching: connecting saved data to the scoring rules
# ---------------------------------------------------------------------------

def job_delivery_hours(job_row):
    """The client's requested delivery time. Uses the deadline date when no delivery time was chosen."""
    if job_row["delivery_hours"] is not None:
        return job_row["delivery_hours"]
    if job_row["deadline"]:
        days_left = (date.fromisoformat(job_row["deadline"]) - date.today()).days
        return max(24, days_left * 24)
    return None


def job_for_matching(job_row):
    return {
        "game": job_row["game"] or None,
        "category": job_row["category"] or None,
        "effects": matching.split_csv(job_row["required_effects"]),
        "budget_max": job_row["maximum_budget"],
        "delivery_hours": job_delivery_hours(job_row),
    }


def editor_for_matching(profile):
    """Only facts from a SUCCESSFUL TinyFish run are passed to the matcher."""
    public = profile["analysis"] if profile["tinyfish_analysis_status"] == "completed" else None
    return {
        "id": profile["id"],
        "name": profile["full_name"],
        "games": profile["game_list"],
        "categories": profile["category_list"],
        "skill_tags": profile["skill_list"],
        "samples": [
            {"title": s["title"], "category": s["category"], "game": s["game"],
             "skill_tags": s["tag_list"], "description": s["description"]}
            for s in profile["samples"]
        ],
        "minimum_price": profile["minimum_price"],
        "availability": profile["availability"],
        "delivery_hours": profile["delivery_hours"],
        "public": public,
        "profile": profile,  # used by the templates only; the matcher ignores it
    }


def run_matching(job_row):
    """Score every editor for this job, save the results, and return them best first."""
    db = database.get_db()
    editors = [editor_for_matching(p) for p in fetch_editor_profiles()]
    ranked = matching.get_top_matching_editors(job_for_matching(job_row), editors, limit=10)

    db.execute("DELETE FROM match_results WHERE job_id = ?", (job_row["id"],))
    for item in ranked:
        db.execute(
            "INSERT INTO match_results (job_id, editor_id, compatibility_score, reason_json) VALUES (?, ?, ?, ?)",
            (job_row["id"], item["editor"]["id"], item["result"]["score"], json.dumps(item["explanation"])),
        )
    has_useful_match = any(item["result"]["score"] >= matching.PARTIAL_MATCH_SCORE for item in ranked)
    if has_useful_match and job_row["status"] in ("Submitted", "Matching"):
        db.execute("UPDATE job_requests SET status = 'Matches Found' WHERE id = ?", (job_row["id"],))
    db.commit()
    return ranked


def get_job_for_client(job_id, client_id):
    return database.get_db().execute(
        "SELECT * FROM job_requests WHERE id = ? AND client_id = ?", (job_id, client_id)
    ).fetchone()


def open_jobs_for_client(client_id):
    return database.get_db().execute(
        "SELECT id, title FROM job_requests WHERE client_id = ? AND status NOT IN (?, ?) ORDER BY created_at DESC",
        (client_id, *CLOSED_JOB_STATUSES),
    ).fetchall()


# ---------------------------------------------------------------------------
# Public pages
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    demo_editors = fetch_editor_profiles("WHERE u.is_demo = 1", limit=3)
    return render_template("index.html", demo_editors=demo_editors)


@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user():
        return redirect(dashboard_url_for(current_user()))
    selected_role = request.args.get("type", "editor")
    if request.method == "GET":
        return render_template("register.html", values={"role": selected_role}, errors=[])

    errors = []
    values = posted_values(request.form)
    full_name = read_text(request.form, "full_name", 100, errors, "Full name", required=True)
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    confirm = request.form.get("confirm_password", "")
    role = request.form.get("role", "")

    if not EMAIL_PATTERN.match(email) or len(email) > 254:
        errors.append("Please enter a valid email address.")
    if len(password) < 8:
        errors.append("Password must be at least 8 characters long.")
    elif not (re.search(r"[A-Za-z]", password) and re.search(r"\d", password)):
        errors.append("Password must contain at least one letter and one number.")
    if password != confirm:
        errors.append("The two passwords do not match.")
    if role not in ("editor", "client"):
        errors.append("Please choose whether you are an editor or a client.")

    if not errors:
        db = database.get_db()
        if db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone():
            errors.append("An account with this email already exists. Please log in instead.")
        else:
            try:
                cursor = db.execute(
                    "INSERT INTO users (full_name, email, password_hash, role) VALUES (?, ?, ?, ?)",
                    (full_name, email, generate_password_hash(password), role),
                )
                db.commit()
            except sqlite3.IntegrityError:  # a second request registered the same email first
                db.rollback()
                errors.append("An account with this email already exists. Please log in instead.")
            else:
                session.clear()
                session["user_id"] = cursor.lastrowid
                flash("Your account has been created.", "success")
                return redirect(url_for("editor_apply") if role == "editor" else url_for("client_dashboard"))

    return render_template("register.html", values=values, errors=errors)


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(dashboard_url_for(current_user()))
    if request.method == "GET":
        return render_template("login.html", values={}, errors=[])

    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    user = database.get_db().execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    # The same message is shown for a wrong email and a wrong password, so attackers cannot tell which one.
    if user is None or not check_password_hash(user["password_hash"], password):
        return render_template("login.html", values={"email": email}, errors=["Email or password is incorrect."])

    session.clear()
    session["user_id"] = user["id"]
    flash(f"Welcome back, {user['full_name']}.", "success")
    return redirect(dashboard_url_for(user))


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("index"))


@app.route("/editors")
def browse_editors():
    raw_max_price = request.args.get("max_price", "").strip()
    filters = {
        "q": request.args.get("q", "").strip()[:100].lower(),
        "category": request.args.get("category", "") if request.args.get("category", "") in matching.CATEGORY_OPTIONS else "",
        "game": request.args.get("game", "") if request.args.get("game", "") in matching.GAME_OPTIONS else "",
        "technique": request.args.get("technique", "") if request.args.get("technique", "") in matching.TECHNIQUE_OPTIONS else "",
        "availability": request.args.get("availability", "") if request.args.get("availability", "") in matching.AVAILABILITY_OPTIONS else "",
        "max_price": int(raw_max_price) if raw_max_price.isdigit() else None,
    }
    profiles = [p for p in fetch_editor_profiles() if editor_passes_filters(p, filters)]
    user = current_user()
    client_jobs = open_jobs_for_client(user["id"]) if user and user["role"] == "client" else []
    return render_template(
        "browse_editors.html", profiles=profiles, filters=filters, client_jobs=client_jobs,
        any_filter=any(v not in ("", None) for v in filters.values()),
    )


def editor_passes_filters(profile, filters):
    """True when the editor matches every filter the visitor chose."""
    def terms(*lists):
        return {matching.normalise_tag(item) for group in lists for item in group if item}

    sample_categories = [s["category"] for s in profile["samples"]]
    sample_games = [s["game"] for s in profile["samples"]]
    sample_tags = [t for s in profile["samples"] for t in s["tag_list"]]

    if filters["category"] and matching.normalise_tag(filters["category"]) not in terms(profile["category_list"], sample_categories):
        return False
    if filters["game"] and matching.normalise_tag(filters["game"]) not in terms(profile["game_list"], sample_games):
        return False
    if filters["technique"] and matching.normalise_tag(filters["technique"]) not in terms(profile["skill_list"], sample_tags):
        return False
    if filters["availability"] and profile["availability"] != filters["availability"]:
        return False
    if filters["max_price"] is not None and (profile["minimum_price"] is None or profile["minimum_price"] > filters["max_price"]):
        return False
    if filters["q"]:
        haystack = " ".join(
            [profile["full_name"], profile["bio"], profile["categories"], profile["games"], profile["skill_tags"]]
            + [s["title"] + " " + s["category"] + " " + s["game"] + " " + s["description"] + " " + s["skill_tags"]
               for s in profile["samples"]]
        ).lower()
        if filters["q"] not in haystack:
            return False
    return True


@app.route("/editor/<int:profile_id>")
def editor_profile(profile_id):
    rows = fetch_editor_profiles("WHERE ep.id = ?", (profile_id,))
    if not rows:
        abort(404)
    profile = rows[0]
    user = current_user()
    client_jobs = open_jobs_for_client(user["id"]) if user and user["role"] == "client" else []
    return render_template(
        "editor_profile.html", profile=profile, is_owner=bool(user and user["id"] == profile["user_id"]),
        show_contact=contact_visible_to(profile, user), client_jobs=client_jobs,
        completion=profile_completion(profile),
    )


# ---------------------------------------------------------------------------
# Editor pages
# ---------------------------------------------------------------------------

@app.route("/editor/apply", methods=["GET", "POST"])
@login_required("editor")
def editor_apply():
    user = current_user()
    if get_profile_row(user["id"]) is not None:
        flash("You already have an editor profile, so you can edit it here.", "info")
        return redirect(url_for("editor_edit"))
    return save_editor_profile(user, None)


@app.route("/editor/edit", methods=["GET", "POST"])
@login_required("editor")
def editor_edit():
    user = current_user()
    profile = load_profile_for_user(user["id"])
    if profile is None:
        return redirect(url_for("editor_apply"))
    return save_editor_profile(user, profile)


def read_editor_fields(errors):
    """Read the profile section of the editor form. Lists are saved as comma-separated text."""
    form = request.form
    fields = {
        "bio": read_text(form, "bio", 1000, errors, "Short bio", required=True),
        "city": read_text(form, "city", 100, errors, "City"),
        "experience_level": read_choice(form, "experience_level", matching.EXPERIENCE_OPTIONS, errors, "experience level", required=True),
        "languages": read_text(form, "languages", 100, errors, "Preferred language"),
        "pricing_model": read_choice(form, "pricing_model", matching.PRICING_MODELS, errors, "pricing model", required=True),
        "availability": read_choice(form, "availability", matching.AVAILABILITY_OPTIONS, errors, "availability", required=True),
        "preferred_contact": read_choice(form, "preferred_contact", matching.CONTACT_OPTIONS, errors, "contact method"),
        "contact_email": read_text(form, "contact_email", 254, errors, "Contact email", required=True),
        "social_link": read_text(form, "social_link", 300, errors, "Instagram or social link"),
        "youtube_link": read_text(form, "youtube_link", 300, errors, "YouTube channel link"),
        "portfolio_website": read_text(form, "portfolio_website", 300, errors, "Portfolio website"),
        "portfolio_url": read_text(form, "portfolio_url", 300, errors, "Public portfolio URL"),
        "minimum_price": read_int(form, "minimum_price", errors, "Minimum price (₹)", 0, 10_000_000),
        "maximum_price": read_int(form, "maximum_price", errors, "Maximum price (₹)", 0, 10_000_000),
        "delivery_hours": read_delivery(form, "delivery_hours", errors),
        "price_negotiable": 1 if form.get("price_negotiable") else 0,
    }
    fields["full_name"] = read_text(form, "full_name", 100, errors, "Full name", required=True)

    if fields["contact_email"] and not EMAIL_PATTERN.match(fields["contact_email"]):
        errors.append("Please enter a valid contact email address.")
    for label, key in (("Social link", "social_link"), ("YouTube link", "youtube_link"),
                       ("Portfolio website", "portfolio_website"), ("Portfolio URL", "portfolio_url")):
        if fields[key] and not is_web_link(fields[key]):
            errors.append(f"{label} must start with http:// or https://.")
    if fields["minimum_price"] is not None and fields["maximum_price"] is not None \
            and fields["maximum_price"] < fields["minimum_price"]:
        errors.append("The maximum price cannot be lower than the minimum price.")

    categories = read_choices(form, "categories", matching.CATEGORY_OPTIONS)
    categories += read_custom_list(form, "other_category", errors, "Other category")
    games = read_choices(form, "games", matching.GAME_OPTIONS)
    games += read_custom_list(form, "other_games", errors, "Other games")
    skills = read_choices(form, "skill_tags", matching.TECHNIQUE_OPTIONS)
    skills += read_custom_list(form, "other_technique", errors, "Other technique")
    if not categories:
        errors.append("Choose at least one editing speciality.")
    fields["categories"] = ", ".join(dict.fromkeys(categories))
    fields["games"] = ", ".join(dict.fromkeys(games))
    fields["skill_tags"] = ", ".join(dict.fromkeys(skills))
    return fields


def read_sample_slots(existing_by_id, errors):
    """Read the five sample slots. Returns (samples_to_save, new_video_files).

    Empty slots are skipped. A slot that is partly filled must be completed.
    """
    samples, new_files = [], {}
    for index in range(1, MAX_SAMPLES + 1):
        prefix = f"sample_{index}_"
        sample_id = request.form.get(prefix + "id", "").strip()
        existing = existing_by_id.get(int(sample_id)) if sample_id.isdigit() else None

        if existing and request.form.get(prefix + "remove"):
            samples.append({"id": existing["id"], "remove": True})
            continue

        title = read_text(request.form, prefix + "title", 120, errors, f"Sample {index} title")
        category = request.form.get(prefix + "category", "").strip()
        game = request.form.get(prefix + "game", "").strip()[:60]
        description = request.form.get(prefix + "description", "").strip()[:600]
        software = request.form.get(prefix + "software", "").strip()[:100]
        video_url = request.form.get(prefix + "video_url", "").strip()
        techniques = read_choices(request.form, prefix + "techniques", matching.TECHNIQUE_OPTIONS)
        video_file = request.files.get(prefix + "video")
        has_file = bool(video_file and video_file.filename)

        started = any([title, category, game, description, video_url, has_file, techniques])
        if not started:
            continue

        label = f"Sample {index}"
        if not title:
            errors.append(f"{label}: please add a title.")
        if category not in matching.CATEGORY_OPTIONS:
            errors.append(f"{label}: please choose a category.")
        if video_url and not is_web_link(video_url):
            errors.append(f"{label}: the video link must start with http:// or https://.")
        has_saved_video = bool(existing and (existing["uploaded_video_path"] or existing["external_video_url"]))
        if not (has_file or video_url or has_saved_video):
            errors.append(f"{label}: upload a video file or enter a public video link.")

        upload_key = None
        if has_file:
            upload = read_upload(video_file, VIDEO_EXTENSIONS, VIDEO_MAX_BYTES, f"{label} video", errors)
            if upload:
                upload_key = prefix
                new_files[upload_key] = upload

        samples.append({
            "id": existing["id"] if existing else None,
            "remove": False,
            "position": index,
            "title": title,
            "category": category,
            "game": game,
            "description": description,
            "software_used": software,
            "skill_tags": ", ".join(techniques),
            "external_video_url": video_url,
            "new_video_key": upload_key,
        })
    return samples, new_files


def save_editor_profile(user, profile):
    """Shared handler for the create and edit pages."""
    is_edit = profile is not None
    if request.method == "GET":
        values = profile_values(profile) if is_edit else default_values(user)
        return render_editor_form(values, profile, [])

    errors = []
    values = posted_values(request.form)
    fields = read_editor_fields(errors)
    existing_by_id = {s["id"]: s for s in profile["samples"]} if is_edit else {}
    samples, new_files = read_sample_slots(existing_by_id, errors)
    photo = read_upload(request.files.get("profile_photo"), PHOTO_EXTENSIONS, PHOTO_MAX_BYTES, "Profile photo", errors)

    if errors:
        return render_editor_form(values, profile, errors)

    db = database.get_db()
    written_files = []  # new files saved in this request, deleted again if saving fails
    files_to_delete = []  # old files that are replaced or removed
    try:
        stored_photo = None
        if photo:
            stored_photo = store_upload("photos", *photo)
            written_files.append(stored_photo)
        stored_videos = {}
        for key, (extension, data) in new_files.items():
            stored_videos[key] = store_upload("videos", extension, data)
            written_files.append(stored_videos[key])

        # The owner's user row is the only account this profile belongs to.
        db.execute("UPDATE users SET full_name = ? WHERE id = ?", (fields.pop("full_name"), user["id"]))

        profile_data = dict(fields)
        if stored_photo:
            profile_data["profile_photo"] = stored_photo
        if is_edit and profile["portfolio_url"] != fields["portfolio_url"]:
            # A new portfolio link must be analysed again, so the old results are cleared.
            profile_data["tinyfish_analysis_status"] = "not_requested"
            profile_data["tinyfish_analysis_json"] = None

        # Column names come from this file only, never from user input.
        if is_edit:
            profile_id = profile["id"]
            assignments = ", ".join(f"{column} = ?" for column in profile_data)
            db.execute(
                f"UPDATE editor_profiles SET {assignments}, updated_at = datetime('now') WHERE id = ?",
                [*profile_data.values(), profile_id],
            )
        else:
            columns = ["user_id", *profile_data.keys()]
            placeholders = ", ".join("?" for _ in columns)
            cursor = db.execute(
                f"INSERT INTO editor_profiles ({', '.join(columns)}) VALUES ({placeholders})",
                [user["id"], *profile_data.values()],
            )
            profile_id = cursor.lastrowid

        for sample in samples:
            save_sample(db, profile_id, sample, stored_videos, existing_by_id, files_to_delete)
        db.commit()
    except (sqlite3.Error, OSError):
        db.rollback()
        for relative_path in written_files:
            delete_upload(relative_path)
        logger.exception("Could not save editor profile")
        errors.append("Your profile could not be saved. Please try again.")
        return render_editor_form(values, profile, errors)

    for relative_path in files_to_delete:
        delete_upload(relative_path)

    if request.form.get("action") == "analyse":
        message = run_portfolio_analysis(profile_id)
        flash(f"Profile saved. {message}", "info")
    else:
        flash("Your profile has been saved.", "success")
    return redirect(url_for("editor_dashboard"))


def save_sample(db, profile_id, sample, stored_videos, existing_by_id, files_to_delete):
    """Insert, update or delete one portfolio sample. Old video files go into files_to_delete."""
    if sample["remove"]:
        old = existing_by_id.get(sample["id"])
        db.execute("DELETE FROM portfolio_samples WHERE id = ? AND editor_id = ?", (sample["id"], profile_id))
        if old:
            files_to_delete.extend([old["uploaded_video_path"]])
        return

    video_path, video_url = "", ""
    old = existing_by_id.get(sample["id"]) if sample["id"] else None
    if sample["new_video_key"]:
        video_path = stored_videos[sample["new_video_key"]]
        if old:
            files_to_delete.append(old["uploaded_video_path"])
    elif sample["external_video_url"]:
        video_url = sample["external_video_url"]
        if old and old["uploaded_video_path"]:
            files_to_delete.append(old["uploaded_video_path"])
    elif old:
        video_path, video_url = old["uploaded_video_path"], old["external_video_url"]

    values = (sample["position"], sample["title"], sample["category"], sample["game"], sample["description"],
              video_path, video_url, sample["skill_tags"], sample["software_used"])
    if old:
        db.execute(
            "UPDATE portfolio_samples SET position = ?, title = ?, category = ?, game = ?, description = ?, "
            "uploaded_video_path = ?, external_video_url = ?, skill_tags = ?, software_used = ? "
            "WHERE id = ? AND editor_id = ?",
            (*values, old["id"], profile_id),
        )
    else:
        db.execute(
            "INSERT INTO portfolio_samples (editor_id, position, title, category, game, description, "
            "uploaded_video_path, external_video_url, skill_tags, software_used) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (profile_id, *values),
        )


def render_editor_form(values, profile, errors):
    is_edit = profile is not None
    return render_template(
        "editor_apply.html", values=values, errors=errors, profile=profile,
        is_edit=is_edit, form_action=url_for("editor_edit" if is_edit else "editor_apply"),
    )


@app.route("/editor/portfolio/analyse", methods=["POST"])
@login_required("editor")
def editor_retry_analysis():
    row = get_profile_row(current_user()["id"])
    if row is None:
        return redirect(url_for("editor_apply"))
    message = run_portfolio_analysis(row["id"], force=True)
    flash(message, "info")
    database.get_db().commit()
    return redirect(url_for("editor_dashboard"))


@app.route("/editor/dashboard")
@login_required("editor")
def editor_dashboard():
    user = current_user()
    profile = load_profile_for_user(user["id"])
    if profile is None:
        flash("Create your editor profile to get started.", "info")
        return redirect(url_for("editor_apply"))
    db = database.get_db()
    invitations = [dict(r) for r in db.execute(
        "SELECT i.id, i.status, i.created_at, j.title, j.category, j.game, j.raw_description, "
        "j.minimum_budget, j.maximum_budget, j.deadline, j.required_effects, j.delivery_hours, "
        "u.full_name AS client_name, u.email AS client_email "
        "FROM invitations i JOIN job_requests j ON j.id = i.job_id JOIN users u ON u.id = j.client_id "
        "WHERE i.editor_id = ? ORDER BY i.created_at DESC",
        (profile["id"],),
    ).fetchall()]
    for invitation in invitations:
        invitation["effects_list"] = matching.split_csv(invitation["required_effects"])
        if invitation["status"] != "Accepted":
            invitation["client_email"] = None  # contact details are private until accepted
    runs = db.execute(
        "SELECT * FROM tinyfish_runs WHERE editor_id = ? ORDER BY id DESC LIMIT 10", (profile["id"],)
    ).fetchall()
    return render_template(
        "editor_dashboard.html", profile=profile, completion=profile_completion(profile),
        invitations=invitations, runs=runs,
        analysis=profile["analysis"] if profile["tinyfish_analysis_status"] == "completed" else None,
    )


@app.route("/invitations/<int:invitation_id>/respond", methods=["POST"])
@login_required("editor")
def respond_to_invitation(invitation_id):
    decision = request.form.get("decision", "")
    if decision not in ("Accepted", "Declined"):
        abort(400)
    row = get_profile_row(current_user()["id"])
    db = database.get_db()
    invitation = db.execute(
        "SELECT * FROM invitations WHERE id = ? AND editor_id = ?",
        (invitation_id, row["id"] if row else -1),
    ).fetchone()
    if invitation is None:
        abort(404)  # also covers invitations that belong to other editors
    if invitation["status"] != "Pending":
        flash("You have already responded to this invitation.", "info")
        return redirect(url_for("editor_dashboard"))

    db.execute(
        "UPDATE invitations SET status = ?, updated_at = datetime('now') WHERE id = ? AND status = 'Pending'",
        (decision, invitation_id),
    )
    if decision == "Accepted":
        db.execute(
            "UPDATE job_requests SET status = 'In Discussion', updated_at = datetime('now') WHERE id = ?",
            (invitation["job_id"],),
        )
    db.commit()
    flash(
        "Invitation accepted. The client can now see your contact details." if decision == "Accepted"
        else "Invitation declined.",
        "success",
    )
    return redirect(url_for("editor_dashboard"))


# ---------------------------------------------------------------------------
# Client pages
# ---------------------------------------------------------------------------

@app.route("/client/dashboard")
@login_required("client")
def client_dashboard():
    user = current_user()
    db = database.get_db()
    jobs = [dict(r) for r in db.execute(
        "SELECT j.*, "
        "(SELECT COUNT(*) FROM match_results m WHERE m.job_id = j.id) AS match_count, "
        "(SELECT COUNT(*) FROM invitations i WHERE i.job_id = j.id) AS invite_count, "
        "(SELECT COUNT(*) FROM invitations i WHERE i.job_id = j.id AND i.status = 'Accepted') AS accepted_count "
        "FROM job_requests j WHERE j.client_id = ? ORDER BY j.created_at DESC",
        (user["id"],),
    ).fetchall()]
    activity = db.execute(
        "SELECT i.status, i.updated_at, j.title, j.id AS job_id, p.id AS profile_id, u.full_name AS editor_name, "
        "CASE WHEN i.status = 'Accepted' THEN p.contact_email ELSE '' END AS contact_email "
        "FROM invitations i JOIN job_requests j ON j.id = i.job_id "
        "JOIN editor_profiles p ON p.id = i.editor_id JOIN users u ON u.id = p.user_id "
        "WHERE i.client_id = ? ORDER BY i.updated_at DESC LIMIT 8",
        (user["id"],),
    ).fetchall()
    return render_template("client_dashboard.html", jobs=jobs, activity=activity)


@app.route("/hire", methods=["GET", "POST"])
@login_required("client")
def hire_editor():
    if request.method == "GET":
        return render_hire_form({}, {}, None, [])

    values = posted_values(request.form)
    if request.form.get("action") == "understand":
        # Show the parsed requirements as editable pointers. Nothing is saved yet.
        description = request.form.get("description", "")
        parsed = matching.parse_project_description(description)
        pointers = {field: parsed.get(field) for field in matching.REQUIREMENT_FIELDS}
        message = "Check the requirements below. Correct anything that is wrong, then click Find My Editors."
        flash(message, "info")
        return render_hire_form(values, pointers_to_text(pointers), parsed, [])

    job_id, errors = create_job()
    if errors:
        return render_hire_form(values, read_pointer_text(), None, errors)
    return redirect(url_for("job_matches", job_id=job_id))


def pointers_to_text(pointers):
    """Convert parsed values into text for the form, e.g. effects become 'Velocity editing, Smooth transitions'."""
    text = {}
    for field, value in pointers.items():
        if isinstance(value, list):
            text[field] = ", ".join(value)
        elif value is None:
            text[field] = ""
        else:
            text[field] = str(value)
    return text


def read_pointer_text():
    return {field: request.form.get("pointer_" + field, "").strip() for field in matching.REQUIREMENT_FIELDS}


def read_pointers_typed():
    """Turn the pointer text back into numbers and lists for the requirement rules."""
    typed = {}
    for field, text in read_pointer_text().items():
        if not text:
            typed[field] = None
        elif field == "effects":
            typed[field] = matching.split_csv(text)
        elif field in ("duration_seconds", "budget_min", "budget_max", "delivery_hours"):
            typed[field] = int(text) if text.isdigit() else None
        else:
            typed[field] = text
    return typed


def render_hire_form(values, pointers, parsed, errors):
    return render_template(
        "hire_editor.html", values=values, pointers=pointers, parsed=parsed, errors=errors,
        example_prompts=EXAMPLE_PROMPTS,
    )


EXAMPLE_PROMPTS = [
    "Fortnite cinematic montage with velocity edits.",
    "Instagram Reel with subtitles, transitions, and colour grading.",
    "YouTube gaming video with memes, sound effects, and captions.",
]


def create_job():
    """Validate the project form and save it. Returns (job_id, errors)."""
    errors = []
    form = request.form
    title = read_text(form, "title", 120, errors, "Project title", required=True)
    description = read_text(form, "description", 3000, errors, "Project description", required=True)
    if description and len(description) < 20:
        errors.append("Please describe the project in at least 20 characters.")

    structured = {
        "game": read_text(form, "game", 60, errors, "Game") or None,
        "category": read_choice(form, "category", matching.CATEGORY_OPTIONS, errors, "category") or None,
        "platform": read_choice(form, "platform", matching.PLATFORM_OPTIONS, errors, "platform") or None,
        "effects": read_choices(form, "effects", matching.TECHNIQUE_OPTIONS),
        "voiceover": read_choice(form, "voiceover", matching.VOICEOVER_OPTIONS, errors, "voiceover option") or None,
        "subtitles": read_choice(form, "subtitles", ["Yes", "No"], errors, "subtitle option") or None,
        "duration_seconds": read_int(form, "duration_seconds", errors, "Finished video length (seconds)", 1, 3600),
        "budget_min": read_int(form, "budget_min", errors, "Minimum budget (₹)", 0, 10_000_000),
        "budget_max": read_int(form, "budget_max", errors, "Maximum budget (₹)", 0, 10_000_000),
        "aspect_ratio": read_choice(form, "aspect_ratio", matching.ASPECT_RATIO_OPTIONS, errors, "aspect ratio") or None,
        "delivery_hours": read_delivery(form, "delivery_hours", errors, "Preferred delivery time"),
    }
    requirements = matching.build_requirements(structured, read_pointers_typed(), description)
    final = requirements["values"]

    if not final["category"]:
        errors.append("Please choose a category, or describe the type of video in your description.")
    if final["budget_max"] is None:
        errors.append("Please enter a maximum budget in ₹, or write it in your description (for example: under ₹2,000).")
    if final["budget_min"] is not None and final["budget_max"] is not None and final["budget_min"] > final["budget_max"]:
        errors.append("The minimum budget cannot be higher than the maximum budget.")

    deadline = read_date(form, "deadline", errors, "Deadline")
    revisions = read_int(form, "revisions", errors, "Number of revisions", 0, 20)
    reference_url = read_text(form, "reference_url", 500, errors, "Reference link")
    if reference_url and not is_web_link(reference_url):
        errors.append("The reference link must start with http:// or https://.")
    reference_upload = read_upload(request.files.get("reference_file"), REFERENCE_EXTENSIONS,
                                   VIDEO_MAX_BYTES, "Reference file", errors)
    video_type = read_choice(form, "video_type", matching.VIDEO_TYPE_OPTIONS, errors, "video type")
    audience = read_text(form, "audience", 120, errors, "Intended audience")
    instructions = read_text(form, "additional_instructions", 1000, errors, "Additional instructions")

    if errors:
        return None, errors

    db = database.get_db()
    stored_reference = store_upload("references", *reference_upload) if reference_upload else ""
    try:
        cursor = db.execute(
            "INSERT INTO job_requests (client_id, title, category, game, platform, video_type, audience, "
            "raw_description, parsed_requirements_json, minimum_budget, maximum_budget, deadline, delivery_hours, "
            "finished_duration_seconds, aspect_ratio, required_effects, voiceover_preference, subtitles_required, "
            "music_sync, revisions, additional_instructions, reference_url, reference_file_path, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Submitted')",
            (
                current_user()["id"], title, final["category"] or "", final["game"] or "",
                final["platform"] or "", video_type, audience, description,
                json.dumps(requirements), final["budget_min"], final["budget_max"], deadline,
                final["delivery_hours"], final["duration_seconds"], final["aspect_ratio"] or "",
                ", ".join(final["effects"] or []), final["voiceover"] or "Optional",
                1 if final["subtitles"] == "Yes" else 0, 1 if form.get("music_sync") else 0,
                revisions, instructions, reference_url, stored_reference,
            ),
        )
        job_id = cursor.lastrowid
        db.commit()
    except sqlite3.Error:
        db.rollback()
        delete_upload(stored_reference)
        logger.exception("Could not save job request")
        return None, ["Your project could not be saved. Please try again."]

    for conflict in requirements["conflicts"]:
        flash(conflict, "info")
    if reference_url:
        flash(run_reference_analysis(job_id, reference_url), "info")
    db.commit()
    run_matching(get_job_row(job_id))
    return job_id, []


def get_job_row(job_id):
    return database.get_db().execute("SELECT * FROM job_requests WHERE id = ?", (job_id,)).fetchone()


@app.route("/matches/<int:job_id>")
@login_required("client")
def job_matches(job_id):
    user = current_user()
    job = get_job_for_client(job_id, user["id"])
    if job is None:
        abort(404)
    ranked = run_matching(job)
    strong = [item for item in ranked if item["result"]["score"] >= matching.PARTIAL_MATCH_SCORE]
    shown = strong[:3] if strong else ranked[:3]
    invited_ids = {r["editor_id"] for r in database.get_db().execute(
        "SELECT editor_id FROM invitations WHERE job_id = ? AND status = 'Pending'", (job_id,)
    ).fetchall()}
    return render_template(
        "match_results.html", job=job, shown=shown, total_editors=len(ranked),
        show_fallback=not strong, invited_ids=invited_ids,
        requirements=json.loads(job["parsed_requirements_json"]),
        reference=json.loads(job["reference_analysis_json"]) if job["reference_analysis_json"] else None,
    )


@app.route("/invite", methods=["POST"])
@login_required("client")
def send_invitation():
    user = current_user()
    job_id = request.form.get("job_id", "")
    editor_id = request.form.get("editor_id", "")
    if not (job_id.isdigit() and editor_id.isdigit()):
        abort(400)
    return_to = safe_return_path(request.form.get("return_to", ""), url_for("job_matches", job_id=job_id))

    db = database.get_db()
    job = get_job_for_client(int(job_id), user["id"])
    if job is None:
        abort(404)
    if db.execute("SELECT id FROM editor_profiles WHERE id = ?", (int(editor_id),)).fetchone() is None:
        abort(404)
    if job["status"] in CLOSED_JOB_STATUSES:
        flash("This project is closed, so no new invitations can be sent.", "error")
        return redirect(return_to)

    latest = db.execute(
        "SELECT status FROM invitations WHERE job_id = ? AND editor_id = ? ORDER BY id DESC LIMIT 1",
        (job["id"], int(editor_id)),
    ).fetchone()
    if latest and latest["status"] == "Pending":
        flash("You have already invited this editor to this project. They have not responded yet.", "info")
    elif latest and latest["status"] == "Accepted":
        flash("This editor has already accepted this project.", "info")
    else:
        try:
            db.execute(
                "INSERT INTO invitations (job_id, client_id, editor_id, status) VALUES (?, ?, ?, 'Pending')",
                (job["id"], user["id"], int(editor_id)),
            )
            if job["status"] in ("Submitted", "Matching", "Matches Found"):
                db.execute("UPDATE job_requests SET status = 'Invitation Sent', updated_at = datetime('now') WHERE id = ?", (job["id"],))
            db.commit()
            flash("Invitation sent. The editor will see it on their dashboard.", "success")
        except sqlite3.IntegrityError:  # a pending invitation was created by a second click
            db.rollback()
            flash("You have already invited this editor to this project.", "info")
    return redirect(return_to)


@app.route("/jobs/<int:job_id>/status", methods=["POST"])
@login_required("client")
def update_job_status(job_id):
    user = current_user()
    new_status = request.form.get("status", "")
    if new_status not in ("Completed", "Cancelled"):
        abort(400)
    if get_job_for_client(job_id, user["id"]) is None:
        abort(404)
    db = database.get_db()
    db.execute("UPDATE job_requests SET status = ?, updated_at = datetime('now') WHERE id = ? AND client_id = ?",
               (new_status, job_id, user["id"]))
    db.commit()
    flash(f"Project marked as {new_status.lower()}.", "success")
    return redirect(url_for("client_dashboard"))


# ---------------------------------------------------------------------------
# Error pages
# ---------------------------------------------------------------------------

@app.errorhandler(404)
def not_found(error):
    return render_template("404.html"), 404


@app.errorhandler(500)
def server_error(error):
    return render_template("500.html"), 500


@app.errorhandler(RequestEntityTooLarge)
def upload_too_large(error):
    flash(f"The upload is too large. Videos must be {VIDEO_MAX_BYTES // MB} MB or smaller.", "error")
    return redirect(request.referrer or url_for("index"))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    with app.app_context():
        database.init_db()
    for folder in ("photos", "videos", "references"):
        (Path(app.config["UPLOAD_FOLDER"]) / folder).mkdir(parents=True, exist_ok=True)
    app.run(host="127.0.0.1", port=8000, debug=os.environ.get("FLASK_DEBUG") == "1")
