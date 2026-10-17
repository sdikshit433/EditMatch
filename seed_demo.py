"""Add fictional demo accounts so the website has something to show.

Run once with:  python seed_demo.py

Every demo account uses an @editmatch.local email address and is marked as demo data.
Existing accounts are never changed. If a demo email already exists, that account is skipped.
Change or delete these accounts before any public deployment.
"""

from werkzeug.security import generate_password_hash

import database
from app import app

DEMO_PASSWORD = "DemoPass123"

DEMO_EDITORS = [
    {
        "name": "Aarav Demo", "email": "demo.aarav@editmatch.local",
        "bio": "Demo profile. Fast-paced gaming montages with beat-synced cuts.",
        "experience_level": "Experienced", "availability": "Available",
        "categories": "Gaming", "games": "Fortnite, Valorant",
        "skill_tags": "Velocity editing, Beat synchronisation, Camera shake, Colour grading",
        "pricing_model": "Per project", "minimum_price": 1500, "maximum_price": 4000,
        "delivery_hours": 48, "contact_email": "demo.aarav@editmatch.local",
        "samples": [
            {"title": "Fortnite Velocity Edit", "category": "Gaming", "game": "Fortnite",
             "description": "Fast-paced montage with beat-synchronised transitions.",
             "skill_tags": "Velocity editing, Beat synchronisation, Camera shake, Colour grading"},
        ],
    },
    {
        "name": "Meera Demo", "email": "demo.meera@editmatch.local",
        "bio": "Demo profile. Short vertical videos with clean captions.",
        "experience_level": "Intermediate", "availability": "Available",
        "categories": "Instagram Reels, YouTube Shorts", "games": "",
        "skill_tags": "Subtitles and captions, Smooth transitions, Zoom effects",
        "pricing_model": "Per project", "minimum_price": 800, "maximum_price": 1500,
        "delivery_hours": 24, "contact_email": "demo.meera@editmatch.local",
        "samples": [
            {"title": "Reel With Captions", "category": "Instagram Reels", "game": "",
             "description": "Vertical reel with subtitles and smooth transitions.",
             "skill_tags": "Subtitles and captions, Smooth transitions"},
        ],
    },
    {
        "name": "Rohan Demo", "email": "demo.rohan@editmatch.local",
        "bio": "Demo profile. Cinematic and motion graphics edits for brands.",
        "experience_level": "Professional", "availability": "Busy",
        "categories": "Cinematic videos, Motion graphics, Advertisements", "games": "",
        "skill_tags": "Cinematic effects, Colour grading, Visual effects, 3D effects",
        "pricing_model": "Per finished minute", "minimum_price": 5000, "maximum_price": 12000,
        "delivery_hours": 168, "contact_email": "demo.rohan@editmatch.local",
        "samples": [
            {"title": "Product Ad Concept", "category": "Advertisements", "game": "",
             "description": "Short product advert with 3D titles and grading.",
             "skill_tags": "3D effects, Colour grading"},
        ],
    },
    {
        "name": "Ishita Demo", "email": "demo.ishita@editmatch.local",
        "bio": "Demo profile. Clear podcast and educational edits with sound design.",
        "experience_level": "Experienced", "availability": "Available",
        "categories": "Podcast editing, Educational videos", "games": "",
        "skill_tags": "Sound design, Subtitles and captions",
        "pricing_model": "Hourly", "minimum_price": 600, "maximum_price": 1200,
        "delivery_hours": 72, "contact_email": "demo.ishita@editmatch.local",
        "samples": [
            {"title": "Podcast Episode Cut", "category": "Podcast editing", "game": "",
             "description": "Clean audio edit with captions for a one-hour episode.",
             "skill_tags": "Sound design, Subtitles and captions"},
        ],
    },
]

DEMO_CLIENT = {"name": "Demo Client", "email": "demo.client@editmatch.local"}


def seed():
    db = database.get_db()
    password_hash = generate_password_hash(DEMO_PASSWORD)
    created = []

    for editor in DEMO_EDITORS:
        if db.execute("SELECT id FROM users WHERE email = ?", (editor["email"],)).fetchone():
            continue
        cursor = db.execute(
            "INSERT INTO users (full_name, email, password_hash, role, is_demo) VALUES (?, ?, ?, 'editor', 1)",
            (editor["name"], editor["email"], password_hash),
        )
        profile_cursor = db.execute(
            "INSERT INTO editor_profiles (user_id, bio, experience_level, categories, games, skill_tags, "
            "pricing_model, minimum_price, maximum_price, delivery_hours, availability, contact_email) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (cursor.lastrowid, editor["bio"], editor["experience_level"], editor["categories"], editor["games"],
             editor["skill_tags"], editor["pricing_model"], editor["minimum_price"], editor["maximum_price"],
             editor["delivery_hours"], editor["availability"], editor["contact_email"]),
        )
        for position, sample in enumerate(editor["samples"], start=1):
            db.execute(
                "INSERT INTO portfolio_samples (editor_id, position, title, category, game, description, skill_tags) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (profile_cursor.lastrowid, position, sample["title"], sample["category"], sample["game"],
                 sample["description"], sample["skill_tags"]),
            )
        created.append(editor["name"])

    if not db.execute("SELECT id FROM users WHERE email = ?", (DEMO_CLIENT["email"],)).fetchone():
        db.execute(
            "INSERT INTO users (full_name, email, password_hash, role, is_demo) VALUES (?, ?, ?, 'client', 1)",
            (DEMO_CLIENT["name"], DEMO_CLIENT["email"], password_hash),
        )
        created.append(DEMO_CLIENT["name"])

    db.commit()
    return created


if __name__ == "__main__":
    with app.app_context():
        database.init_db()
        names = seed()
    if names:
        print("Created demo accounts:", ", ".join(names))
    else:
        print("Demo accounts already exist. Nothing changed.")
    print(f"Demo password for every demo account: {DEMO_PASSWORD}")
