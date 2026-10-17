"""Database setup for EditMatch.

EditMatch stores its data in one SQLite file inside the instance/ folder.
Python's built-in sqlite3 module reads and writes that file.

Every query in this project uses ? placeholders, so user input is never
pasted straight into the SQL text. That protects us from SQL injection.
"""

import sqlite3

from flask import current_app, g

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('editor', 'client')),
    is_demo INTEGER NOT NULL DEFAULT 0,        -- 1 = fictional seed data
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- One editor profile per editor account.
-- List-type values (categories, games, skill_tags) are stored as comma-separated text.
CREATE TABLE IF NOT EXISTS editor_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    profile_photo TEXT NOT NULL DEFAULT '',
    bio TEXT NOT NULL DEFAULT '',
    city TEXT NOT NULL DEFAULT '',
    experience_level TEXT NOT NULL DEFAULT '',
    languages TEXT NOT NULL DEFAULT '',
    categories TEXT NOT NULL DEFAULT '',
    games TEXT NOT NULL DEFAULT '',
    skill_tags TEXT NOT NULL DEFAULT '',
    pricing_model TEXT NOT NULL DEFAULT '',
    minimum_price INTEGER,                      -- rupees; NULL means "not stated"
    maximum_price INTEGER,
    price_negotiable INTEGER NOT NULL DEFAULT 0,
    delivery_hours INTEGER,                     -- typical delivery time; NULL means "not stated"
    availability TEXT NOT NULL DEFAULT '',
    preferred_contact TEXT NOT NULL DEFAULT '',
    contact_email TEXT NOT NULL DEFAULT '',     -- private: only shown after an accepted invitation
    social_link TEXT NOT NULL DEFAULT '',
    youtube_link TEXT NOT NULL DEFAULT '',
    portfolio_website TEXT NOT NULL DEFAULT '',
    portfolio_url TEXT NOT NULL DEFAULT '',
    tinyfish_analysis_json TEXT,                -- facts extracted from the public portfolio
    tinyfish_analysis_status TEXT NOT NULL DEFAULT 'not_requested',
    tinyfish_last_checked TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS portfolio_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    editor_id INTEGER NOT NULL REFERENCES editor_profiles(id) ON DELETE CASCADE,
    position INTEGER NOT NULL DEFAULT 1,
    title TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '',
    game TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    uploaded_video_path TEXT NOT NULL DEFAULT '',   -- file saved in static/uploads/videos
    external_video_url TEXT NOT NULL DEFAULT '',    -- a link the editor typed in
    skill_tags TEXT NOT NULL DEFAULT '',
    software_used TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_samples_editor ON portfolio_samples(editor_id);

CREATE TABLE IF NOT EXISTS job_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '',
    game TEXT NOT NULL DEFAULT '',
    platform TEXT NOT NULL DEFAULT '',
    video_type TEXT NOT NULL DEFAULT '',
    audience TEXT NOT NULL DEFAULT '',
    raw_description TEXT NOT NULL DEFAULT '',
    parsed_requirements_json TEXT NOT NULL DEFAULT '{}',
    minimum_budget INTEGER,
    maximum_budget INTEGER,
    deadline TEXT NOT NULL DEFAULT '',          -- ISO date, e.g. 2026-12-31
    delivery_hours INTEGER,
    finished_duration_seconds INTEGER,
    aspect_ratio TEXT NOT NULL DEFAULT '',
    required_effects TEXT NOT NULL DEFAULT '',
    voiceover_preference TEXT NOT NULL DEFAULT '',
    subtitles_required INTEGER NOT NULL DEFAULT 0,
    music_sync INTEGER NOT NULL DEFAULT 0,
    revisions INTEGER,
    additional_instructions TEXT NOT NULL DEFAULT '',
    reference_url TEXT NOT NULL DEFAULT '',
    reference_analysis_json TEXT,
    reference_analysis_status TEXT NOT NULL DEFAULT 'not_requested',
    reference_file_path TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'Submitted',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_jobs_client ON job_requests(client_id);

-- Saved results of each matching run. Old rows for a job are replaced on every run.
CREATE TABLE IF NOT EXISTS match_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES job_requests(id) ON DELETE CASCADE,
    editor_id INTEGER NOT NULL REFERENCES editor_profiles(id) ON DELETE CASCADE,
    compatibility_score INTEGER NOT NULL,
    reason_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (job_id, editor_id)
);

CREATE TABLE IF NOT EXISTS invitations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES job_requests(id) ON DELETE CASCADE,
    client_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    editor_id INTEGER NOT NULL REFERENCES editor_profiles(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'Pending' CHECK (status IN ('Pending', 'Accepted', 'Declined')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
-- Only one pending invitation for the same project and editor.
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_pending_invite
    ON invitations(job_id, editor_id) WHERE status = 'Pending';

-- A record of every TinyFish request, so the history is visible.
CREATE TABLE IF NOT EXISTS tinyfish_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    purpose TEXT NOT NULL,                      -- 'portfolio' or 'reference'
    editor_id INTEGER,
    job_id INTEGER,
    source_url TEXT NOT NULL,
    run_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def get_db():
    """Return the database connection for the current web request.

    Flask keeps one connection per request in the special object g.
    """
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row  # lets us read columns by name, e.g. row["email"]
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(error=None):
    """Close the connection at the end of each request."""
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    """Create every table if it does not exist yet. Safe to run many times."""
    get_db().executescript(SCHEMA_SQL)


def init_app(app):
    """Register the close function so each request cleans up its connection."""
    app.teardown_appcontext(close_db)
