"""TinyFish integration for EditMatch.

TinyFish is a web agent. It opens a public web page, follows the instructions
in our goal, and returns structured data. We use it for two things only:
1. Reading a public editor portfolio page.
2. Reading a public reference page a client provides.

It never sees uploaded video files and it never judges video quality.

The API key is read from the TINYFISH_API_KEY environment variable. It is
never sent to the browser and never written into the code.
"""

import ipaddress
import logging
import os
import socket
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from urllib.parse import urlparse

from tinyfish import RunStatus, TinyFish

logger = logging.getLogger(__name__)

# How long we wait for one TinyFish run before we give up and say so.
TIMEOUT_SECONDS = 150

# Host names we never send to TinyFish, even if they resolve to a public address.
BLOCKED_HOST_ENDINGS = (".local", ".localhost", ".internal", ".lan", ".home.arpa")

# Human-readable labels for each status. The templates use these.
STATUS_LABELS = {
    "not_requested": "Waiting to analyse public portfolio.",
    "not_configured": "TinyFish is not configured. Regular matching is still available.",
    "missing_url": "No public portfolio URL was added.",
    "invalid_url": "The portfolio URL could not be used.",
    "completed": "Portfolio information extracted.",
    "incomplete": "Some information was unavailable. The page did not show the details we look for.",
    "failed": "Analysis failed. You can continue without it.",
}

PORTFOLIO_GOAL = (
    "You are reading a public video editor's portfolio page at the given URL. "
    "Also check publicly accessible portfolio pages linked from it, if any. "
    "Return ONLY information that is visibly shown on those pages: "
    "project titles, project descriptions, editing categories or games that the page "
    "explicitly states, editing techniques that the page explicitly mentions, links to other "
    "public portfolio pages, and any price information that is publicly listed. "
    "Do not log in, sign up, contact anyone, send messages or submit forms. "
    "Do not guess the editor's identity, skill level, ratings, pricing or editing quality. "
    "If the page does not give an item, return an empty list or null for it and add the field name "
    "to unavailable_fields. If a video page only shows a title and description, use only that text "
    "and say so in evidence_summary. Keep evidence_summary under 60 words."
)

REFERENCE_GOAL = (
    "You are reading a public web page that a client gave as a reference for a video edit. "
    "Return ONLY information visibly shown on the page: the reference project title, the visible "
    "description, visible text that describes the editing style, and visible metadata about the "
    "video category. Do not log in, sign up, contact anyone or submit forms. "
    "Do not claim to have watched the video or to understand its visual content. "
    "If an item is not shown, return null or an empty list and explain the limitation in "
    "limitations. Keep evidence_summary under 60 words."
)


def _string_array(description):
    return {"type": "array", "items": {"type": "string"}, "description": description}


PORTFOLIO_SCHEMA = {
    "type": "object",
    "properties": {
        "source_url": {"type": "string", "description": "The URL that was read"},
        "project_titles": _string_array("Project titles visibly shown on the page"),
        "project_descriptions": _string_array("Project descriptions visibly shown on the page"),
        "categories_stated": _string_array("Editing categories or games the page explicitly states"),
        "techniques_stated": _string_array("Editing techniques the page explicitly mentions"),
        "portfolio_links": _string_array("Links to other public portfolio pages"),
        "price_information": {"type": ["string", "null"], "description": "Publicly listed pricing, or null"},
        "unavailable_fields": _string_array("Names of items the page did not provide"),
        "evidence_summary": {"type": "string", "description": "Short summary of the visible evidence"},
    },
    "required": [
        "source_url", "project_titles", "project_descriptions", "categories_stated",
        "techniques_stated", "portfolio_links", "price_information", "unavailable_fields",
        "evidence_summary",
    ],
}

REFERENCE_SCHEMA = {
    "type": "object",
    "properties": {
        "source_url": {"type": "string"},
        "reference_title": {"type": ["string", "null"]},
        "visible_description": {"type": ["string", "null"]},
        "style_text": _string_array("Visible text that describes the editing style"),
        "category_metadata": _string_array("Visible metadata about the video category"),
        "limitations": _string_array("Important limits of what could be read"),
        "evidence_summary": {"type": "string"},
    },
    "required": [
        "source_url", "reference_title", "visible_description", "style_text",
        "category_metadata", "limitations", "evidence_summary",
    ],
}


def is_configured():
    """True when an API key is available in the environment."""
    return bool(os.environ.get("TINYFISH_API_KEY", "").strip())


def _is_private_address(address_text):
    """True for loopback, private, link-local and other non-public addresses."""
    ip = ipaddress.ip_address(address_text)
    return (
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_reserved or ip.is_multicast or ip.is_unspecified
    )


def validate_public_url(raw_url):
    """Check that a URL is safe to hand to a web agent.

    Returns (url, None) when it is acceptable, or (None, message) when it is not.
    This blocks local addresses, private network addresses, and login details in the URL.
    """
    url = (raw_url or "").strip()
    if not url:
        return None, "Please enter a URL."
    if len(url) > 500:
        return None, "That URL is too long."

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return None, "The URL must start with http:// or https://."
    if parsed.username or parsed.password:
        return None, "URLs that contain a username or password are not accepted."

    host = (parsed.hostname or "").lower()
    if not host:
        return None, "The URL does not contain a website name."
    if host == "localhost" or host.endswith(BLOCKED_HOST_ENDINGS):
        return None, "Local addresses cannot be analysed."

    try:
        # The host is an IP address written in the URL.
        if _is_private_address(host):
            return None, "Private network addresses cannot be analysed."
        return url, None
    except ValueError:
        pass  # the host is a name, so we look up its addresses below

    try:
        port = parsed.port or 443
        addresses = socket.getaddrinfo(host, port)
    except (socket.gaierror, UnicodeError, ValueError):
        return None, "We could not find that website."

    for entry in addresses:
        if _is_private_address(entry[4][0]):
            return None, "That website points to a private network address and cannot be analysed."
    return url, None


def _clean_list(value, limit=10):
    """Keep only non-empty text items, and cut them to a safe length."""
    if not isinstance(value, list):
        return []
    items = []
    for item in value:
        if isinstance(item, str) and item.strip():
            items.append(item.strip()[:300])
    return items[:limit]


def _clean_text(value, limit=500):
    if isinstance(value, str) and value.strip():
        return value.strip()[:limit]
    return None


def _run_agent(goal, url, schema):
    """Send one goal to TinyFish and wait for the answer, with a time limit.

    Returns a dict with status 'ok' and the result, or status 'failed' or
    'not_configured' with a message. This function never raises.
    """
    api_key = os.environ.get("TINYFISH_API_KEY", "").strip()
    if not api_key:
        return {"status": "not_configured", "message": STATUS_LABELS["not_configured"], "run_id": None}

    client = TinyFish(api_key=api_key)
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(client.agent.run, goal=goal, url=url, output_schema=schema)
    try:
        response = future.result(timeout=TIMEOUT_SECONDS)
    except FutureTimeout:
        return {
            "status": "failed", "run_id": None,
            "message": f"TinyFish did not finish within {TIMEOUT_SECONDS} seconds. You can retry later.",
        }
    except Exception:  # network problems, SDK errors, or an invalid key
        logger.exception("TinyFish request failed for %s", url)
        return {"status": "failed", "run_id": None, "message": STATUS_LABELS["failed"]}
    finally:
        executor.shutdown(wait=False)

    run_id = getattr(response, "run_id", None)
    # A finished API call does not always mean the agent reached its goal.
    if response.status != RunStatus.COMPLETED:
        error = getattr(response, "error", None)
        detail = getattr(error, "message", None) or "the run did not complete"
        return {"status": "failed", "run_id": run_id, "message": f"TinyFish could not finish the run: {detail}."}

    result = response.result if isinstance(response.result, dict) else {}
    return {"status": "ok", "run_id": run_id, "result": result}


def analyse_editor_portfolio(url):
    """Read a public editor portfolio. Returns a dict with status, message, run_id and data.

    'data' is set only when TinyFish returned something useful. Empty results are
    reported as 'incomplete', never as 'completed'.
    """
    outcome = _run_agent(PORTFOLIO_GOAL, url, PORTFOLIO_SCHEMA)
    if outcome["status"] != "ok":
        return {"status": outcome["status"], "message": outcome["message"], "run_id": outcome["run_id"], "data": None}

    raw = outcome["result"]
    data = {
        "source_url": url,
        "project_titles": _clean_list(raw.get("project_titles")),
        "project_descriptions": _clean_list(raw.get("project_descriptions")),
        "categories": _clean_list(raw.get("categories_stated")),
        "techniques": _clean_list(raw.get("techniques_stated")),
        "portfolio_links": [link for link in _clean_list(raw.get("portfolio_links")) if link.startswith("http")],
        "price_information": _clean_text(raw.get("price_information")),
        "unavailable_fields": _clean_list(raw.get("unavailable_fields")),
        "evidence_summary": _clean_text(raw.get("evidence_summary"), 400),
    }
    useful = any([data["project_titles"], data["project_descriptions"], data["categories"],
                  data["techniques"], data["portfolio_links"]])
    status = "completed" if useful else "incomplete"
    return {"status": status, "message": STATUS_LABELS[status], "run_id": outcome["run_id"], "data": data}


def analyse_reference_url(url):
    """Read a public reference page that a client supplied. Same return shape as the portfolio check."""
    outcome = _run_agent(REFERENCE_GOAL, url, REFERENCE_SCHEMA)
    if outcome["status"] != "ok":
        return {"status": outcome["status"], "message": outcome["message"], "run_id": outcome["run_id"], "data": None}

    raw = outcome["result"]
    data = {
        "source_url": url,
        "reference_title": _clean_text(raw.get("reference_title"), 200),
        "visible_description": _clean_text(raw.get("visible_description"), 800),
        "style_text": _clean_list(raw.get("style_text")),
        "category_metadata": _clean_list(raw.get("category_metadata")),
        "limitations": _clean_list(raw.get("limitations")),
        "evidence_summary": _clean_text(raw.get("evidence_summary"), 400),
    }
    useful = bool(data["reference_title"] or data["visible_description"] or data["style_text"])
    status = "completed" if useful else "incomplete"
    return {"status": status, "message": STATUS_LABELS[status], "run_id": outcome["run_id"], "data": data}
