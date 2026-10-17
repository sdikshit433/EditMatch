"""Matching rules for EditMatch.

This file has no Flask and no database code, so every rule can be tested with
fixed inputs. The score is a transparent points system. It shows how well the
saved information fits a client's request. It does NOT measure artistic
quality, creativity or professionalism.

The code is split into three steps:
1. Read the client's description and form fields (parse_project_description, build_requirements).
2. Score one editor against one request (calculate_editor_match_score).
3. Rank editors and write plain-English explanations (get_top_matching_editors, explain_match).
"""

import re

# ---------------------------------------------------------------------------
# SETTINGS YOU CAN CHANGE
# ---------------------------------------------------------------------------

# Points for each part of the score. The six values add up to 100.
SCORE_WEIGHTS = {
    "category_and_game": 30,
    "effects_and_techniques": 25,
    "budget": 20,
    "deadline": 10,
    "portfolio_evidence": 10,
    "availability": 5,
}

# Each matching sample (or matching fact from a public page) adds this many points.
POINTS_PER_EVIDENCE_ITEM = 5

# When information is missing we award this share of the points (0.5 = half).
# Missing information is never treated as perfect, and a missing price is never treated as free.
UNKNOWN_SHARE = 0.5

# Points taken away when the editor's starting price is above the client's maximum budget.
BUDGET_MISMATCH_PENALTY = 10

# Share of the availability points for each status.
AVAILABILITY_SHARE = {"Available": 1.0, "Busy": 0.4, "Unavailable": 0.0}

# Score thresholds used to label each match.
STRONG_MATCH_SCORE = 60
PARTIAL_MATCH_SCORE = 30

# Dropdown options. The forms, the filters and the parser all read these lists.
CATEGORY_OPTIONS = [
    "Gaming", "YouTube videos", "Instagram Reels", "YouTube Shorts", "Cinematic videos",
    "Anime/AMV", "Podcast editing", "Wedding editing", "Advertisements", "Educational videos",
    "Documentary editing", "Motion graphics", "Other",
]
GAME_OPTIONS = ["Fortnite", "Valorant", "BGMI", "PUBG", "Minecraft", "GTA V", "Call of Duty"]
TECHNIQUE_OPTIONS = [
    "Velocity editing", "Beat synchronisation", "Smooth transitions", "Camera shake", "Masking",
    "Motion graphics", "Visual effects", "Colour grading", "Sound design", "Subtitles and captions",
    "Zoom effects", "Cinematic effects", "3D effects", "Slow motion", "Meme editing",
    "Green screen", "Other",
]
EXPERIENCE_OPTIONS = ["Beginner", "Intermediate", "Experienced", "Professional"]
PRICING_MODELS = ["Per project", "Per finished minute", "Hourly"]
AVAILABILITY_OPTIONS = ["Available", "Busy", "Unavailable"]
CONTACT_OPTIONS = ["Email", "Instagram DM", "YouTube comment"]
PLATFORM_OPTIONS = ["YouTube", "Instagram", "TikTok", "Twitch", "Other"]
VIDEO_TYPE_OPTIONS = ["Short-form", "Long-form", "Montage", "Cinematic", "Advertisement", "Podcast", "Other"]
ASPECT_RATIO_OPTIONS = ["16:9", "9:16", "1:1", "Other"]
VOICEOVER_OPTIONS = ["Yes", "No", "Optional"]
# Label shown to the user -> number of hours stored in the database.
DELIVERY_OPTIONS = {"24 hours": 24, "48 hours": 48, "3 days": 72, "1 week": 168, "2 weeks": 336}

# Words that are written differently but mean the same thing.
# Keys and values are lowercase. Values are the canonical (standard) forms.
TAG_SYNONYMS = {
    "beat sync": "beat synchronisation",
    "beat synchronization": "beat synchronisation",
    "beat synced": "beat synchronisation",
    "color grading": "colour grading",
    "velocity edit": "velocity editing",
    "velocity edits": "velocity editing",
    "velocity": "velocity editing",
    "vfx": "visual effects",
    "transition": "smooth transitions",
    "transitions": "smooth transitions",
    "smooth transition": "smooth transitions",
    "caption": "subtitles and captions",
    "captions": "subtitles and captions",
    "subtitle": "subtitles and captions",
    "subtitles": "subtitles and captions",
    "reel": "instagram reels",
    "reels": "instagram reels",
    "instagram reel": "instagram reels",
    "shorts": "youtube shorts",
    "youtube short": "youtube shorts",
    "short form": "short form video",
    "short form videos": "short form video",
    "3d": "3d effects",
    "3d effect": "3d effects",
    "slowmo": "slow motion",
    "slow mo": "slow motion",
    "shake": "camera shake",
    "mask": "masking",
    "sound effects": "sound design",
    "sound effect": "sound design",
    "greenscreen": "green screen",
    "meme": "meme editing",
    "memes": "meme editing",
    "motion graphic": "motion graphics",
    "anime": "anime/amv",
    "amv": "anime/amv",
}

# Phrases to look for in a client's description.
# Each key is a phrase; the value is the canonical label we store.
TEXT_GAME_KEYWORDS = {
    "fortnite": "Fortnite", "valorant": "Valorant", "bgmi": "BGMI", "pubg": "PUBG",
    "minecraft": "Minecraft", "gta v": "GTA V", "gta 5": "GTA V", "gta": "GTA V",
    "call of duty": "Call of Duty",
}
TEXT_CATEGORY_KEYWORDS = {
    "reel": "Instagram Reels", "instagram": "Instagram Reels", "shorts": "YouTube Shorts",
    "youtube short": "YouTube Shorts", "gaming": "Gaming", "anime": "Anime/AMV", "amv": "Anime/AMV",
    "podcast": "Podcast editing", "wedding": "Wedding editing", "advert": "Advertisements",
    "educational": "Educational videos", "tutorial": "Educational videos",
    "documentary": "Documentary editing", "motion graphics": "Motion graphics",
    "youtube": "YouTube videos",
}
TEXT_PLATFORM_KEYWORDS = {
    "youtube": "YouTube", "instagram": "Instagram", "reel": "Instagram", "tiktok": "TikTok",
    "twitch": "Twitch",
}
TEXT_EFFECT_KEYWORDS = {
    "velocity": "velocity editing", "beat sync": "beat synchronisation",
    "beat synchronisation": "beat synchronisation", "beat synchronization": "beat synchronisation",
    "transition": "smooth transitions", "camera shake": "camera shake", "shake": "camera shake",
    "masking": "masking", "mask": "masking", "motion graphic": "motion graphics",
    "vfx": "visual effects", "visual effect": "visual effects",
    "colour grading": "colour grading", "color grading": "colour grading",
    "sound design": "sound design", "sound effect": "sound design", "zoom": "zoom effects",
    "cinematic": "cinematic effects", "3d": "3d effects", "slow motion": "slow motion",
    "slowmo": "slow motion", "slow mo": "slow motion", "meme": "meme editing",
    "green screen": "green screen", "greenscreen": "green screen",
}
TEXT_EXTRA_KEYWORDS = [
    "montage", "highlights", "trailer", "vlog", "tutorial", "meme", "viral", "music",
]

# Amounts such as "₹2,000", "Rs 2000" or "INR 5000", with words before them that
# tell us whether the amount is a minimum or a maximum.
BUDGET_PATTERN = re.compile(
    r"(under|below|up to|maximum|max|budget of|budget|at least|minimum|min|from)?\s*"
    r"(?:₹|\brs\.?|\binr)\s*(\d[\d,]*)"  # the amount must start with a digit, so "rs," in "hours," is ignored
)
MIN_BUDGET_WORDS = ("at least", "minimum", "min", "from")
DURATION_PATTERN = re.compile(r"(\d+)\s*(seconds?|secs?|minutes?|mins?)\b")
DELIVERY_PATTERN = re.compile(r"(\d+)\s*(hours?|hrs?|days?)\b")
ASPECT_PATTERN = re.compile(r"\b(9:16|16:9|1:1)\b")
NO_VOICEOVER_PATTERN = re.compile(r"\bno (voice ?over|narration)\b")
VOICEOVER_PATTERN = re.compile(r"\b(voice ?over|narration)\b")
SUBTITLE_PATTERN = re.compile(r"\b(subtitles?|captions?)\b")

# The fields that a client's requirements can contain.
REQUIREMENT_FIELDS = [
    "game", "category", "platform", "effects", "voiceover", "subtitles", "duration_seconds",
    "budget_min", "budget_max", "aspect_ratio", "delivery_hours",
]
EMPTY_VALUES = (None, "", [])


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def split_csv(text):
    """Turn 'Gaming, Instagram Reels' into ['Gaming', 'Instagram Reels']."""
    items = text if isinstance(text, (list, tuple)) else str(text or "").split(",")
    return [str(item).strip() for item in items if str(item).strip()]


def normalise_tag(value):
    """Return the standard lowercase form of a tag, so that equivalent words match.

    Examples: 'Beat Sync' -> 'beat synchronisation', 'VFX' -> 'visual effects',
    'Short-form video' -> 'short form video'.
    """
    text = str(value or "").lower().replace("-", " ").replace("_", " ")
    text = " ".join(text.split())
    return TAG_SYNONYMS.get(text, text)


def normalise_skill_tags(values):
    """Normalise a list (or comma-separated text) of tags and remove duplicates.

    The order of the first appearance is kept.
    """
    seen = []
    for item in split_csv(values):
        tag = normalise_tag(item)
        if tag and tag not in seen:
            seen.append(tag)
    return seen


def format_hours(hours):
    """Show a number of hours in words, e.g. 48 -> '2 days', 36 -> '36 hours'."""
    if hours % 24 == 0:
        days = hours // 24
        return f"{days} day" if days == 1 else f"{days} days"
    return f"{hours} hours"


def _clean_text(text):
    """Lowercase the text and turn hyphens into spaces, so '60-second' reads as '60 second'."""
    return " ".join(str(text or "").lower().replace("-", " ").split())


def _first_match(text, keyword_map):
    """Return the value for the first keyword found in the text, or None."""
    for phrase, value in keyword_map.items():
        if re.search(r"\b" + re.escape(phrase) + r"s?\b", text):
            return value
    return None


def _all_matches(text, keyword_map):
    """Return every value whose keyword appears in the text, without duplicates."""
    found = []
    for phrase, value in keyword_map.items():
        if re.search(r"\b" + re.escape(phrase) + r"s?\b", text) and value not in found:
            found.append(value)
    return found


def _find_budget(text):
    """Return (minimum, maximum) budget in rupees found in the text. Missing values are None."""
    minimum, maximum = None, None
    for match in BUDGET_PATTERN.finditer(text):
        amount = int(match.group(2).replace(",", ""))
        if match.group(1) in MIN_BUDGET_WORDS:
            minimum = amount
        else:
            maximum = amount
    if minimum is not None and maximum is not None and minimum > maximum:
        minimum = None  # contradictory wording; keep only the maximum
    return minimum, maximum


def _find_duration_seconds(text):
    match = DURATION_PATTERN.search(text)
    if not match:
        return None
    amount = int(match.group(1))
    return amount if match.group(2).startswith("sec") else amount * 60


def _find_delivery_hours(text):
    match = DELIVERY_PATTERN.search(text)
    if not match:
        return None
    amount = int(match.group(1))
    return amount * 24 if match.group(2).startswith("day") else amount


# ---------------------------------------------------------------------------
# Step 1: reading the client's description
# ---------------------------------------------------------------------------

def parse_project_description(description):
    """Find requirements written in plain English.

    This uses keyword lists and regular expressions only. It does not use an AI
    model. Each field is None when the description does not mention it.
    """
    text = _clean_text(description)
    game = _first_match(text, TEXT_GAME_KEYWORDS)
    category = _first_match(text, TEXT_CATEGORY_KEYWORDS)
    if category is None and game is not None:
        category = "Gaming"  # a named game is almost always gaming content
    budget_min, budget_max = _find_budget(text)

    aspect_match = ASPECT_PATTERN.search(text)
    voiceover = None
    if NO_VOICEOVER_PATTERN.search(text):
        voiceover = "No"
    elif VOICEOVER_PATTERN.search(text):
        voiceover = "Yes"

    return {
        "game": game,
        "category": category,
        "platform": _first_match(text, TEXT_PLATFORM_KEYWORDS),
        "effects": _all_matches(text, TEXT_EFFECT_KEYWORDS),
        "voiceover": voiceover,
        "subtitles": "Yes" if SUBTITLE_PATTERN.search(text) else None,
        "duration_seconds": _find_duration_seconds(text),
        "budget_min": budget_min,
        "budget_max": budget_max,
        "aspect_ratio": aspect_match.group(1) if aspect_match else None,
        "delivery_hours": _find_delivery_hours(text),
        "keywords": _all_matches(text, {word: word for word in TEXT_EXTRA_KEYWORDS}),
    }


def build_requirements(form, pointers, description):
    """Combine three sources into one set of requirements.

    Priority order (highest first):
    1. A value in a structured form field chosen by the client.
    2. A value the client corrected in the requirement pointers.
    3. A value found in the free-text description.

    Returns the final values, where each value came from, and any conflicts.
    """
    from_text = parse_project_description(description)
    values, sources, conflicts = {}, {}, []

    for field in REQUIREMENT_FIELDS:
        values[field] = None
        sources[field] = "Not specified"
        candidates = [
            ("Form field", form.get(field)),
            ("Your correction", pointers.get(field)),
            ("Parsed from description", from_text.get(field)),
        ]
        for label, candidate in candidates:
            if candidate not in EMPTY_VALUES:
                values[field] = candidate
                sources[field] = label
                break

        # Tell the client when the description disagrees with a value they chose.
        # Lists are not compared here, to keep the message useful and not noisy.
        text_value = from_text.get(field)
        chosen = values[field]
        if (
            sources[field] in ("Form field", "Your correction")
            and text_value not in EMPTY_VALUES
            and not isinstance(chosen, list)
            and str(text_value).lower() != str(chosen).lower()
        ):
            conflicts.append(
                f"Your description suggests {field.replace('_', ' ')} = {text_value}, "
                f"but your chosen {field.replace('_', ' ')} is {chosen}. Your choice was used."
            )

    return {"values": values, "sources": sources, "conflicts": conflicts}


# ---------------------------------------------------------------------------
# Step 2: scoring one editor
# ---------------------------------------------------------------------------

def _new_result():
    return {"score": 0, "points": {}, "penalties": {}, "strengths": [], "mismatches": [], "unknowns": []}


def _unknown(result, key, note):
    """Missing information gets partial points and is listed as unknown."""
    result["points"][key] = round(SCORE_WEIGHTS[key] * UNKNOWN_SHARE, 1)
    result["unknowns"].append(note)


def _score_category_and_game(job, editor, result):
    key = "category_and_game"
    job_game = normalise_tag(job.get("game"))
    job_category = normalise_tag(job.get("category"))
    if not job_game and not job_category:
        _unknown(result, key, "No game or category was requested")
        return

    public = editor.get("public") or {}
    declared_games = {normalise_tag(g) for g in editor.get("games", [])}
    declared_categories = {normalise_tag(c) for c in editor.get("categories", [])}
    for sample in editor.get("samples", []):
        if sample.get("game"):
            declared_games.add(normalise_tag(sample["game"]))
        if sample.get("category"):
            declared_categories.add(normalise_tag(sample["category"]))
    public_games = {normalise_tag(g) for g in public.get("games", [])}
    public_categories = {normalise_tag(c) for c in public.get("categories", [])}

    checks = []
    if job_game:
        checks.append(("game", job["game"], job_game, declared_games, public_games))
    if job_category:
        checks.append(("category", job["category"], job_category, declared_categories, public_categories))

    matched_count = 0
    for kind, label, term, declared, public_set in checks:
        if term in declared:
            matched_count += 1
            result["strengths"].append(f"Lists {label} as a {kind} speciality")
        elif term in public_set:
            matched_count += 1
            result["strengths"].append(f"Public portfolio page lists {label} as a {kind} (extracted by TinyFish)")
        else:
            result["mismatches"].append(f"No {kind} match for {label} on the profile")

    result["points"][key] = round(SCORE_WEIGHTS[key] * matched_count / len(checks), 1)


def _score_effects(job, editor, result):
    key = "effects_and_techniques"
    requested = normalise_skill_tags(job.get("effects") or [])
    if not requested:
        _unknown(result, key, "No specific editing effects were requested")
        return

    labels = {normalise_tag(effect): effect for effect in job.get("effects") or []}
    declared = set(normalise_skill_tags(editor.get("skill_tags", [])))
    for sample in editor.get("samples", []):
        declared.update(normalise_skill_tags(sample.get("skill_tags", [])))
    public = set(normalise_skill_tags((editor.get("public") or {}).get("techniques", [])))

    matched_declared = [t for t in requested if t in declared]
    matched_public = [t for t in requested if t in public and t not in declared]
    missing = [t for t in requested if t not in declared and t not in public]

    result["points"][key] = round(
        SCORE_WEIGHTS[key] * (len(matched_declared) + len(matched_public)) / len(requested), 1
    )
    if matched_declared:
        names = ", ".join(labels[t] for t in matched_declared)
        result["strengths"].append(f"Profile or sample tags include {names}")
    if matched_public:
        names = ", ".join(labels[t] for t in matched_public)
        result["strengths"].append(f"Public portfolio page mentions {names} (extracted by TinyFish)")
    if missing:
        names = ", ".join(labels[t] for t in missing)
        result["mismatches"].append(f"Not listed on the profile or samples: {names}")


def _score_budget(job, editor, result):
    key = "budget"
    budget_max = job.get("budget_max")
    price = editor.get("minimum_price")
    if budget_max in EMPTY_VALUES or price in EMPTY_VALUES:
        _unknown(result, key, "The starting price or the client's budget is not stated")
        return

    if price <= budget_max:
        result["points"][key] = SCORE_WEIGHTS[key]
        result["strengths"].append(f"Starting price ₹{price:,} is within your ₹{budget_max:,} maximum")
    else:
        result["points"][key] = 0
        result["penalties"]["budget_mismatch"] = BUDGET_MISMATCH_PENALTY
        result["mismatches"].append(f"Starting price ₹{price:,} is above your ₹{budget_max:,} maximum budget")


def _score_deadline(job, editor, result):
    key = "deadline"
    requested_hours = job.get("delivery_hours")
    typical_hours = editor.get("delivery_hours")
    if requested_hours in EMPTY_VALUES:
        _unknown(result, key, "The client's deadline is not stated")
        return
    if typical_hours in EMPTY_VALUES:
        _unknown(result, key, "The editor's typical delivery time is not stated")
        return

    if typical_hours <= requested_hours:
        result["points"][key] = SCORE_WEIGHTS[key]
        result["strengths"].append(
            f"Typical delivery ({format_hours(typical_hours)}) fits the requested {format_hours(requested_hours)}"
        )
    else:
        result["points"][key] = 0
        result["mismatches"].append(
            f"Typical delivery ({format_hours(typical_hours)}) is longer than the requested {format_hours(requested_hours)}"
        )


def _score_portfolio_evidence(job, editor, result):
    key = "portfolio_evidence"
    requested_terms = {normalise_tag(job.get("game")), normalise_tag(job.get("category"))}
    requested_terms |= set(normalise_skill_tags(job.get("effects") or []))
    requested_terms.discard("")
    if not requested_terms:
        _unknown(result, key, "Nothing specific was requested, so portfolio relevance is unknown")
        return

    samples = editor.get("samples", [])
    evidence_count = 0
    for sample in samples:
        sample_terms = {normalise_tag(sample.get("category")), normalise_tag(sample.get("game"))}
        sample_terms |= set(normalise_skill_tags(sample.get("skill_tags", [])))
        if requested_terms & sample_terms:
            evidence_count += 1
            result["strengths"].append(f"Sample '{sample.get('title')}' is relevant to this request")

    public = editor.get("public") or {}
    public_terms = set(normalise_skill_tags(public.get("categories", []) + public.get("techniques", [])))
    title_text = " ".join(public.get("project_titles", [])).lower()
    if any(term in public_terms or term in title_text for term in requested_terms):
        evidence_count += 1
        result["strengths"].append("Public portfolio page lists a relevant project or technique (extracted by TinyFish)")

    result["points"][key] = min(SCORE_WEIGHTS[key], POINTS_PER_EVIDENCE_ITEM * evidence_count)
    if evidence_count == 0:
        if samples:
            result["mismatches"].append("None of the portfolio samples match this request")
        else:
            result["mismatches"].append("No portfolio samples have been uploaded")


def _score_availability(editor, result):
    key = "availability"
    status = editor.get("availability") or ""
    if status not in AVAILABILITY_SHARE:
        # Missing availability is not treated as ideal, so it gets no points.
        result["points"][key] = 0
        result["unknowns"].append("Availability is not stated")
        return

    result["points"][key] = round(SCORE_WEIGHTS[key] * AVAILABILITY_SHARE[status], 1)
    if status == "Available":
        result["strengths"].append("Marked as Available")
    else:
        result["mismatches"].append(f"Marked as {status}")


def calculate_editor_match_score(job, editor):
    """Score one editor against one request. The same inputs always give the same score.

    job keys: game, category, effects (list), budget_max, delivery_hours
    editor keys: name, games, categories, skill_tags, samples, minimum_price,
                 availability, delivery_hours, public (verified TinyFish facts or None)
    """
    result = _new_result()
    _score_category_and_game(job, editor, result)
    _score_effects(job, editor, result)
    _score_budget(job, editor, result)
    _score_deadline(job, editor, result)
    _score_portfolio_evidence(job, editor, result)
    _score_availability(editor, result)

    total = sum(result["points"].values()) - sum(result["penalties"].values())
    result["score"] = int(round(max(0, min(100, total))))
    return result


# ---------------------------------------------------------------------------
# Step 3: ranking and explaining
# ---------------------------------------------------------------------------

def explain_match(result):
    """Write a short explanation that uses only the facts that were actually found."""
    score = result["score"]
    if score >= STRONG_MATCH_SCORE:
        label = "Strong match"
    elif score >= PARTIAL_MATCH_SCORE:
        label = "Partial match"
    else:
        label = "Weak match"

    if result["strengths"]:
        summary = f"{label}: " + ". ".join(result["strengths"]) + "."
    else:
        summary = f"{label}: no clear overlap with this request was found."

    return {
        "label": label,
        "summary": summary,
        "strengths": result["strengths"],
        "mismatches": result["mismatches"],
        "unknowns": result["unknowns"],
    }


def get_top_matching_editors(job, editors, limit=3):
    """Score every editor and return the best ones, highest score first."""
    ranked = []
    for editor in editors:
        result = calculate_editor_match_score(job, editor)
        ranked.append({"editor": editor, "result": result, "explanation": explain_match(result)})
    ranked.sort(key=lambda item: (-item["result"]["score"], item["editor"]["name"]))
    return ranked[:limit]
