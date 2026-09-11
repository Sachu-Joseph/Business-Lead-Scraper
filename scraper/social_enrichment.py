"""
scraper/social_enrichment.py

Optional Instagram enrichment for the flexible business lead scraper.

This module can enrich an existing lead with:

    IG Followers
    IG Media Count
    IG Bio
    IG Name
    IG Profile Picture

IMPORTANT:
Instagram enrichment is OPTIONAL.

If Meta/Instagram API credentials are not configured, this module simply
skips enrichment and returns the lead unchanged. The main scraper will
continue working normally.

Environment variables:

    IG_USER_ID=<your Instagram Professional account ID>
    IG_ACCESS_TOKEN=<your Meta access token>

Example Windows PowerShell:

    $env:IG_USER_ID="123456789"
    $env:IG_ACCESS_TOKEN="YOUR_TOKEN"

Then run:

    python main.py

You can permanently configure the variables through Windows Environment
Variables if required.

NOTE:
The exact Meta permissions/API product requirements can change over time.
Use Meta's current Instagram Graph API documentation when creating the
application and access token.
"""

import os
import re
import time
from urllib.parse import urlparse

import requests


# ============================================================
# CONFIGURATION
# ============================================================

# Keep this configurable instead of hard-coding the version everywhere.
GRAPH_API_VERSION = os.getenv("IG_GRAPH_API_VERSION", "v21.0")

GRAPH_API_BASE = (
    f"https://graph.facebook.com/{GRAPH_API_VERSION}"
)

REQUEST_TIMEOUT = 15

# Number of additional attempts after the first request.
MAX_RETRIES = 2

RETRY_DELAY_SECONDS = 3


# ============================================================
# ENVIRONMENT VARIABLES
# ============================================================

IG_USER_ID = os.getenv("IG_USER_ID", "").strip()

IG_ACCESS_TOKEN = os.getenv("IG_ACCESS_TOKEN", "").strip()


# ============================================================
# INSTAGRAM PATHS THAT ARE NOT USER PROFILES
# ============================================================

NON_PROFILE_PATHS = {
    "p",
    "reel",
    "reels",
    "explore",
    "stories",
    "accounts",
    "tv",
    "direct",
    "about",
    "developer",
    "privacy",
    "legal",
    "directory",
}


# ============================================================
# USERNAME VALIDATION
# ============================================================

USERNAME_PATTERN = re.compile(
    r"^[A-Za-z0-9._]{1,30}$"
)


# ============================================================
# HELPERS
# ============================================================

def instagram_api_configured():
    """
    Returns True when both required environment variables exist.
    """

    return bool(IG_USER_ID and IG_ACCESS_TOKEN)


def extract_instagram_username(url_or_handle):
    """
    Extract an Instagram username from:

        https://www.instagram.com/example/
        https://instagram.com/example
        http://instagram.com/example/
        @example
        example

    Returns:
        username string
        or "" when the value does not look like a profile.
    """

    if not url_or_handle:
        return ""

    value = str(url_or_handle).strip()

    if not value:
        return ""

    # --------------------------------------------------------
    # Handle direct @username / username input
    # --------------------------------------------------------

    if not value.startswith(("http://", "https://")):

        value = value.strip().lstrip("@")

        # Remove query / path fragments if somebody supplied them.
        value = value.split("?")[0]
        value = value.split("#")[0]
        value = value.split("/")[0]

        if value.lower() in NON_PROFILE_PATHS:
            return ""

        if USERNAME_PATTERN.fullmatch(value):
            return value

        return ""

    # --------------------------------------------------------
    # Parse Instagram URL
    # --------------------------------------------------------

    try:
        parsed = urlparse(value)

        hostname = (parsed.netloc or "").lower()

        # Remove www.
        hostname = hostname.removeprefix("www.")

        if hostname not in {
            "instagram.com",
            "instagram.co",
        }:
            return ""

        path_parts = [
            part.strip()
            for part in parsed.path.split("/")
            if part.strip()
        ]

        if not path_parts:
            return ""

        username = path_parts[0]

        # URLs such as:
        #
        # /p/ABC123/
        # /reel/ABC123/
        # /stories/username/123/
        #
        # are NOT profile URLs.

        if username.lower() in NON_PROFILE_PATHS:
            return ""

        username = username.lstrip("@")

        if not USERNAME_PATTERN.fullmatch(username):
            return ""

        return username

    except Exception:
        return ""


def _safe_int(value):
    """
    Convert a value to int when possible.

    Returns "" when conversion fails.
    """

    if value in (None, ""):
        return ""

    try:
        return int(value)
    except (TypeError, ValueError):
        return ""


def _safe_string(value):
    """
    Safely convert API values to strings.
    """

    if value is None:
        return ""

    return str(value).strip()


# ============================================================
# INSTAGRAM BUSINESS DISCOVERY
# ============================================================

def fetch_instagram_business_discovery(username):
    """
    Query Instagram Business Discovery for another Professional account.

    Returns a dictionary containing available Instagram information.

    Returns:
        dict
        or None when enrichment cannot be performed.

    IMPORTANT:
    This function NEVER raises an exception for normal API failures.
    """

    if not username:
        return None

    # --------------------------------------------------------
    # API not configured
    # --------------------------------------------------------

    if not instagram_api_configured():

        print(
            "   ℹ Instagram API not configured - "
            "skipping Instagram enrichment"
        )

        return None

    # --------------------------------------------------------
    # API fields
    # --------------------------------------------------------

    fields = (
        f"business_discovery.username({username})"
        "{followers_count,"
        "media_count,"
        "biography,"
        "name,"
        "profile_picture_url}"
    )

    endpoint = f"{GRAPH_API_BASE}/{IG_USER_ID}"

    params = {
        "fields": fields,
        "access_token": IG_ACCESS_TOKEN,
    }

    # --------------------------------------------------------
    # Retry loop
    # --------------------------------------------------------

    for attempt in range(MAX_RETRIES + 1):

        try:

            response = requests.get(
                endpoint,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )

        except requests.RequestException as error:

            print(
                f"   ⚠ Instagram API connection failed: {error}"
            )

            return None

        # ----------------------------------------------------
        # Success
        # ----------------------------------------------------

        if response.status_code == 200:

            try:

                payload = response.json()

            except ValueError:

                print(
                    "   ⚠ Instagram API returned invalid JSON"
                )

                return None

            data = payload.get("business_discovery")

            if isinstance(data, dict):
                return data

            return None

        # ----------------------------------------------------
        # Rate limiting
        # ----------------------------------------------------

        response_text = response.text.lower()

        is_rate_limited = (
            response.status_code == 429
            or "rate limit" in response_text
            or "too many requests" in response_text
        )

        if is_rate_limited:

            if attempt >= MAX_RETRIES:

                print(
                    "   ⚠ Instagram API rate limit reached - "
                    "skipping enrichment"
                )

                return None

            wait_seconds = RETRY_DELAY_SECONDS * (attempt + 1)

            print(
                "   ⚠ Instagram API rate limited - "
                f"retrying in {wait_seconds}s..."
            )

            time.sleep(wait_seconds)

            continue

        # ----------------------------------------------------
        # Authentication errors
        # ----------------------------------------------------

        if response.status_code in {401, 403}:

            print(
                "   ⚠ Instagram API authentication/permission "
                f"error ({response.status_code}) - "
                "skipping enrichment"
            )

            return None

        # ----------------------------------------------------
        # Account not found / unsupported account
        # ----------------------------------------------------

        if response.status_code == 404:

            print(
                f"   ℹ Instagram account @{username} "
                "was not found"
            )

            return None

        # ----------------------------------------------------
        # Other API errors
        # ----------------------------------------------------

        try:

            error_data = response.json()

            api_error = error_data.get("error", {})

            error_message = api_error.get(
                "message",
                response.text[:200],
            )

        except ValueError:

            error_message = response.text[:200]

        print(
            "   ⚠ Instagram API error "
            f"({response.status_code}): "
            f"{error_message}"
        )

        return None

    return None


# ============================================================
# LEAD ENRICHMENT
# ============================================================

def enrich_with_instagram(lead, instagram_url):
    """
    Enrich an existing lead with Instagram information.

    Existing lead dictionary is modified and returned.

    Added fields:

        IG Followers
        IG Media Count
        IG Bio
        IG Name
        IG Profile Picture

    This function is deliberately failure-safe.
    """

    # --------------------------------------------------------
    # Make sure lead is a dictionary
    # --------------------------------------------------------

    if not isinstance(lead, dict):
        return lead

    # --------------------------------------------------------
    # Always create these fields.
    #
    # This keeps the CSV structure consistent even when the
    # Instagram API isn't configured.
    # --------------------------------------------------------

    lead.setdefault("IG Followers", "")
    lead.setdefault("IG Media Count", "")
    lead.setdefault("IG Bio", "")
    lead.setdefault("IG Name", "")
    lead.setdefault("IG Profile Picture", "")

    # --------------------------------------------------------
    # Extract username
    # --------------------------------------------------------

    username = extract_instagram_username(instagram_url)

    if not username:
        return lead

    # --------------------------------------------------------
    # Fetch data
    # --------------------------------------------------------

    try:

        data = fetch_instagram_business_discovery(username)

    except Exception as error:

        # Absolute last line of defence:
        # Instagram enrichment must NEVER crash the scraper.

        print(
            f"   ⚠ Instagram enrichment failed: {error}"
        )

        return lead

    if not data:
        return lead

    # --------------------------------------------------------
    # Save available fields
    # --------------------------------------------------------

    lead["IG Followers"] = _safe_int(
        data.get("followers_count")
    )

    lead["IG Media Count"] = _safe_int(
        data.get("media_count")
    )

    lead["IG Bio"] = _safe_string(
        data.get("biography")
    )

    lead["IG Name"] = _safe_string(
        data.get("name")
    )

    lead["IG Profile Picture"] = _safe_string(
        data.get("profile_picture_url")
    )

    return lead


# ============================================================
# OPTIONAL HELPER
# ============================================================

def get_instagram_enrichment_status():
    """
    Returns a simple status dictionary.

    Useful for main.py if you want to display whether Instagram
    enrichment is enabled.
    """

    return {
        "configured": instagram_api_configured(),
        "user_id_configured": bool(IG_USER_ID),
        "access_token_configured": bool(IG_ACCESS_TOKEN),
        "graph_api_version": GRAPH_API_VERSION,
    }


# ============================================================
# TEST
# ============================================================

if __name__ == "__main__":

    print("=" * 60)
    print("INSTAGRAM ENRICHMENT TEST")
    print("=" * 60)

    status = get_instagram_enrichment_status()

    print(
        "API configured:",
        status["configured"]
    )

    print(
        "User ID configured:",
        status["user_id_configured"]
    )

    print(
        "Access token configured:",
        status["access_token_configured"]
    )

    print(
        "Graph API version:",
        status["graph_api_version"]
    )

    print()

    test_urls = [
        "https://www.instagram.com/example/",
        "https://www.instagram.com/p/ABC123/",
        "@example",
        "example",
    ]

    print("Username extraction tests:")
    print("-" * 60)

    for test_url in test_urls:

        username = extract_instagram_username(test_url)

        print(
            f"{test_url:<50} -> {username}"
        )

    print()

    print(
        "Instagram enrichment is optional. "
        "Your main scraper will continue working "
        "without API credentials."
    )