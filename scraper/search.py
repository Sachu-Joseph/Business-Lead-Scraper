import re
import time
import os
from urllib.parse import urlparse, urlunparse
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

try:
    from ddgs import DDGS
except ImportError:
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        DDGS = None

from utils.helpers import (
    is_directory,
    is_bad_url_path,
)


# ============================================================
# SETTINGS
# ============================================================

WEB_WORKERS = 2
RESULTS_PER_QUERY = 12
MAX_RETRIES = 2
REQUEST_TIMEOUT = 8
# Search snippets often omit the city even when the query contains it.  An
# exact category match is enough to reach website verification, where the
# stricter category, location, and contact checks are applied.
MIN_SEARCH_SCORE = 8
OSM_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OSM_OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OSM_TIMEOUT = 20
GOOGLE_PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
ALLOW_SEARCH_FALLBACK = os.getenv(
    "TN_DISABLE_SEARCH_FALLBACK",
    "",
).strip().lower() not in {"1", "true", "yes", "on"}

OSM_CATEGORY_TAGS = {
    "cafe": [("amenity", "cafe")],
    "restaurant": [("amenity", "restaurant")],
    "bakery": [("shop", "bakery")],
    "toy store": [("shop", "toys")],
    "clothing store": [("shop", "clothes")],
    "furniture store": [("shop", "furniture")],
    "jewellery store": [("shop", "jewelry")],
    "mobile shop": [("shop", "mobile_phone")],
    "electronics store": [("shop", "electronics")],
    "grocery store": [
        ("shop", "supermarket"),
        ("shop", "convenience"),
        ("shop", "general"),
        ("shop", "greengrocer"),
        ("shop", "deli"),
        ("shop", "farm"),
        ("shop", "food"),
        ("shop", "beverages"),
        ("shop", "butcher"),
        ("shop", "variety_store"),
        ("shop", "wholesale"),
    ],
    "gym": [("leisure", "fitness_centre")],
    "salon": [("shop", "hairdresser"), ("shop", "beauty")],
    "clinic": [("amenity", "clinic")],
    "dental clinic": [("amenity", "dentist")],
    "watch shop": [("shop", "watches")],
}


# ============================================================
# CATEGORY SYSTEM
# ============================================================

CATEGORY_ALIASES = {
    "online store": [
        "online store",
        "online shop",
        "ecommerce",
        "e-commerce",
        "d2c",
        "shop online",
        "order online",
        "online shopping",
    ],

    "clothing store": [
        "clothing",
        "clothing store",
        "fashion store",
        "apparel",
        "garments",
        "dress shop",
        "fashion boutique",
    ],

    "boutique": [
        "boutique",
        "fashion boutique",
        "women boutique",
        "ladies boutique",
    ],

    "gym": [
        "gym",
        "fitness center",
        "fitness centre",
        "fitness studio",
        "workout studio",
    ],

    "salon": [
        "salon",
        "beauty salon",
        "hair salon",
        "beauty parlour",
        "beauty parlor",
    ],

    "restaurant": [
        "restaurant",
        "food restaurant",
        "dining",
        "family restaurant",
    ],

    "cafe": [
        "cafe",
        "coffee shop",
        "coffee cafe",
        "restaurant cafe",
    ],

    "bakery": [
        "bakery",
        "cake shop",
        "cakes",
        "bakes",
    ],

    "clinic": [
        "clinic",
        "medical clinic",
        "health clinic",
        "speciality clinic",
        "specialty clinic",
    ],

    "dental clinic": [
        "dental clinic",
        "dentist",
        "dental hospital",
        "dental care",
    ],

    "pet clinic": [
        "pet clinic",
        "veterinary clinic",
        "vet clinic",
        "animal clinic",
    ],

    "furniture store": [
        "furniture",
        "furniture store",
        "furniture showroom",
        "home furniture",
    ],

    "jewellery store": [
        "jewellery",
        "jewelry",
        "jewellery store",
        "jewelry store",
        "gold jewellery",
    ],

    "mobile shop": [
        "mobile shop",
        "mobile store",
        "smartphone shop",
        "phone shop",
        "mobile showroom",
    ],

    "electronics store": [
        "electronics",
        "electronics store",
        "electronic shop",
        "home appliances",
    ],

    "textile store": [
        "textile",
        "textile store",
        "textiles",
        "fabric store",
        "cloth store",
    ],

    "spice store": [
        "spices",
        "spice store",
        "masala",
        "spice shop",
    ],

    "grocery store": [
        "grocery",
        "grocery store",
        "supermarket",
        "provisions",
    ],

    "real estate": [
        "real estate",
        "property dealer",
        "real estate agency",
        "property consultant",
    ],

    "photographer": [
        "photographer",
        "photography",
        "photo studio",
        "wedding photographer",
    ],

    "coaching/training": [
        "coaching",
        "training institute",
        "coaching centre",
        "coaching center",
        "academy",
        "training center",
    ],

    "interior designer": [
        "interior designer",
        "interior design",
        "interiors",
    ],

    "car service": [
        "car service",
        "car repair",
        "automobile service",
        "auto service",
        "car workshop",
    ],

    "driving school": [
        "driving school",
        "driving institute",
        "motor driving school",
    ],

    "toy store": [
        "toy store",
        "toy shop",
        "toys",
        "kids toy store",
        "kids toy shop",
        "children's toy store",
        "educational toys",
        "wooden toys",
    ],

    "watch shop": [
        "watch shop",
        "watch store",
        "watch showroom",
        "watch dealer",
        "wrist watch store",
        "timepiece store",
        "luxury watches",
    ],
}


def normalize_category(category):
    """Convert user category into a canonical category."""

    value = re.sub(r"\s+", " ", str(category).strip().lower())

    for canonical, aliases in CATEGORY_ALIASES.items():
        if value == canonical:
            return canonical

        for alias in aliases:
            if value == alias:
                return canonical

    # Fuzzy-ish fallback
    if "ecommerce" in value or "e-commerce" in value or "online shop" in value:
        return "online store"

    if "gym" in value or "fitness" in value:
        return "gym"

    if "salon" in value or "beauty" in value:
        return "salon"

    if "restaurant" in value:
        return "restaurant"

    if "cafe" in value or "coffee" in value:
        return "cafe"

    if "clothing" in value or "fashion" in value or "garment" in value:
        return "clothing store"

    if "toy" in value:
        return "toy store"

    if "furniture" in value:
        return "furniture store"

    if "clinic" in value:
        return "clinic"

    return value


def get_category_keywords(category):
    canonical = normalize_category(category)

    return CATEGORY_ALIASES.get(
        canonical,
        [canonical]
    )


# ============================================================
# DOMAIN FILTERS
# ============================================================

BLOCKED_DOMAINS = {
    "facebook.com",
    "instagram.com",
    "linktr.ee",
    "beacons.ai",
    "taplink.cc",
    "bio.link",
    "youtube.com",
    "linkedin.com",
    "twitter.com",
    "x.com",
    "pinterest.com",
    "reddit.com",
    "quora.com",

    "justdial.com",
    "sulekha.com",
    "indiamart.com",
    "tradeindia.com",
    "yellowpages.com",
    "yelp.com",
    "magicpin.in",
    "nearbuy.com",
    "asklaila.com",
    "joonsquare.com",
    "gymlocator.in",
    "dialmia.com",
    "funtalia.com",
    "threebestrated.in",
    "3bestincity.com",
    "yappe.in",

    "amazon.in",
    "amazon.com",
    "flipkart.com",
    "myntra.com",
    "meesho.com",

    "naukri.com",
    "indeed.com",
    "foundit.in",
    "shine.com",

    "wikipedia.org",

    "gov.in",
    "nic.in",
}

SOCIAL_PROFILE_DOMAINS = {
    "instagram.com",
    "facebook.com",
}

SOCIAL_NON_PROFILE_PATHS = {
    "p",
    "reel",
    "reels",
    "video",
    "videos",
    "watch",
    "groups",
    "events",
    "marketplace",
    "explore",
    "stories",
    "photos",
    "pages",
    "share",
    "sharer",
}

CORPORATE_DOMAINS = {
    "reliancedigital.in",
    "relianceindustries.com",
    "tatacliq.com",
    "croma.com",
    "poorvika.com",
    "samsung.com",
    "apple.com",
    "mi.com",
    "oneplus.in",
    "vijaysales.com",
    "lenskart.com",
    "nykaa.com",
    "decathlon.in",
    "adidas.co.in",
    "nike.com",
}


BAD_NAME_TERMS = [
    "jobs",
    "job opening",
    "career",
    "careers",
    "vacancy",
    "directory",
    "listing",
    "list of",
    "top 10",
    "best 10",
    "near me",
    "review",
    "reviews",
    "blog",
    "article",
    "news",
    "wiki",
    "government",
    "ministry",
    "department",
    "court",
    "university",
    "college",
]


BAD_BUSINESS_TERMS = [
    "digital marketing agency",
    "web development",
    "website development",
    "software company",
    "software solutions",
    "seo agency",
    "marketing agency",
    "app development",
    "ecommerce development",
    "e-commerce development",
    "whatsapp api provider",
    "whatsapp solution provider",
    "crm software",
    "saas company",
    "technology company",
]

# Exclude high-volume listing websites before results reach our own filters.
# This leaves more search-result slots for actual business websites.
SEARCH_EXCLUSIONS = " ".join([
    "-site:justdial.com",
    "-site:sulekha.com",
    "-site:indiamart.com",
    "-site:facebook.com",
    "-site:instagram.com",
    "-site:youtube.com",
    "-site:tripadvisor.in",
    "-site:holidify.com",
])


def get_domain(url):
    try:
        host = urlparse(url).netloc.lower()
        host = host.split("@")[-1].split(":")[0]

        if host.startswith("www."):
            host = host[4:]

        return host
    except Exception:
        return ""


def extract_social_profile(url):
    """Return a public Instagram/Facebook profile URL, or an empty string."""
    try:
        parsed = urlparse(str(url or "").strip())
        domain = parsed.netloc.lower().split(":")[0]
        if domain.startswith("www."):
            domain = domain[4:]
        if domain not in SOCIAL_PROFILE_DOMAINS:
            return ""

        parts = [
            part.strip()
            for part in parsed.path.split("/")
            if part.strip()
        ]
        if len(parts) != 1 or parts[0].lower() in SOCIAL_NON_PROFILE_PATHS:
            return ""

        username = parts[0]
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", username):
            return ""

        return f"https://www.{domain}/{username}"
    except Exception:
        return ""


def clean_social_business_name(title, profile_url):
    """Extract one business name from a search engine social-result title."""
    value = re.sub(r"\s+", " ", str(title or "")).strip()
    value = re.split(
        r"\s+(?:[-|•]\s*)?(?:facebook|instagram)"
        r"(?:\s*(?:photos?|videos?|reels?))?",
        value,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip(" -|•")
    value = re.sub(
        r"\s+(?:photos?|videos?|reels?)\s+and\s+"
        r"(?:photos?|videos?|reels?).*$",
        "",
        value,
        flags=re.IGNORECASE,
    ).strip(" -|•")
    if "|" in value:
        value = value.split("|", 1)[0].strip(" -|•")

    lowered = value.lower()
    generic = (
        "best gym",
        "best gyms",
        "gyms in ",
        "gym in ",
        "fitness center ",
        "fitness centre ",
        "chennai best gyms",
    )
    if (
        not value
        or any(term in lowered for term in generic)
        or "facebook" in lowered
        or "instagram" in lowered
        or "photos and" in lowered
        or "videos" in lowered
        or "|" in value
    ):
        value = ""

    if value:
        return value

    username = str(profile_url or "").rstrip("/").rsplit("/", 1)[-1]
    return username.replace("_", " ").replace("-", " ").strip()


def root_domain(url):
    domain = get_domain(url)

    parts = domain.split(".")

    if len(parts) >= 3 and ".".join(parts[-2:]) in {
        "co.in",
        "com.au",
        "co.uk",
        "co.nz",
        "org.in",
        "net.in",
        "gen.in",
        "firm.in",
        "ind.in",
        "co.za",
        "com.br",
        "com.cn",
        "com.eg",
        "com.hk",
        "com.il",
        "com.my",
        "com.mx",
        "com.ng",
        "com.pk",
        "com.sa",
        "com.sg",
        "com.tr",
        "com.tw",
        "co.id",
        "co.jp",
        "co.kr",
        "co.th",
        "co.ke",
    }:
        return ".".join(parts[-3:])

    if len(parts) >= 2:
        return ".".join(parts[-2:])

    return domain


def normalize_url(url):
    if not url:
        return ""

    url = str(url).strip()

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        parsed = urlparse(url)

        return urlunparse((
            parsed.scheme,
            parsed.netloc.lower(),
            parsed.path.rstrip("/"),
            "",
            "",
            "",
        ))
    except Exception:
        return url


def is_blocked_domain(url):
    domain = get_domain(url)
    root = root_domain(url)

    for blocked in BLOCKED_DOMAINS:
        if domain == blocked or domain.endswith("." + blocked):
            return True

    for corporate in CORPORATE_DOMAINS:
        if domain == corporate or domain.endswith("." + corporate):
            return True

    if root in {
        "google.com",
        "google.co.in",
        "bing.com",
        "duckduckgo.com",
    }:
        return True

    return False


# ============================================================
# CANDIDATE FILTERING
# ============================================================

def is_bad_candidate(name, url, snippet=""):
    text = f"{name} {url} {snippet}".lower()

    if not url:
        return True

    if is_blocked_domain(url):
        return True

    if is_directory(url):
        return True

    if is_bad_url_path(url):
        return True

    for term in BAD_NAME_TERMS:
        if term in text:
            return True

    # Don't accidentally collect service providers instead
    for term in BAD_BUSINESS_TERMS:
        if term in text:
            return True

    return False


def category_match_score(name, snippet, category):
    text = f"{name} {snippet}".lower()

    keywords = get_category_keywords(category)

    score = 0

    for keyword in keywords:
        if keyword.lower() in text:
            score += 8

    return min(score, 35)


def location_match_score(name, snippet, location):
    text = f"{name} {snippet}".lower()

    location_words = [
        x.strip().lower()
        for x in re.split(r"[,\s]+", location)
        if x.strip()
    ]

    score = 0

    for word in location_words:
        if len(word) >= 4 and word in text:
            score += 5

    return min(score, 20)


# ============================================================
# SEARCH QUERY BUILDER
# ============================================================

def build_queries(category, location):
    canonical = normalize_category(category)

    quoted_location = f'"{location}"'

    queries = []

    if canonical == "online store":
        queries = [
            f'"online store" {quoted_location} contact',
            f'"online shop" {quoted_location} contact',
            f'ecommerce {quoted_location} "contact us"',
            f'"shop online" {quoted_location}',
            f'"order online" {quoted_location}',
            f'"WhatsApp" "order" {quoted_location}',
            f'"WhatsApp catalog" {quoted_location}',
            f'"D2C" {quoted_location} brand',
            f'"online shopping" {quoted_location} brand',
            f'"buy online" {quoted_location} business',
            f'"contact us" "WhatsApp" {quoted_location}',
            f'"about us" "online store" {quoted_location}',
        ]

    else:
        keywords = get_category_keywords(canonical)

        primary = keywords[:4]

        for keyword in primary:
            queries.append(
                f'"{keyword}" {quoted_location} contact'
            )

        for keyword in primary[:2]:
            queries.append(
                f'"{keyword}" {quoted_location} WhatsApp'
            )

        queries.extend([
            f'"{canonical}" {quoted_location} "about us"',
            f'"{canonical}" {quoted_location} "contact us"',
            f'"{canonical}" {quoted_location} official website',
        ])

    # Remove duplicates
    final = []

    for query in queries:
        if query not in final:
            final.append(query)

    return [f"{query} {SEARCH_EXCLUSIONS}" for query in final]


def build_social_queries(category, location):
    canonical = normalize_category(category)
    keywords = get_category_keywords(canonical) or [canonical]
    quoted_location = f'"{location}"'
    queries = []

    for keyword in keywords:
        queries.extend([
            f'site:instagram.com "{keyword}" {quoted_location}',
            f'site:facebook.com "{keyword}" {quoted_location}',
        ])

    return list(dict.fromkeys(queries))


# ============================================================
# DDGS SEARCH
# ============================================================

def search_one_query(query):
    if DDGS is None:
        print(
            "\nERROR: DDGS package is missing.\n"
            "Install it with:\n"
            "pip install ddgs\n"
        )
        return []

    results = []

    for attempt in range(MAX_RETRIES + 1):
        try:
            # Fresh client reduces connection-reset problems
            with DDGS(timeout=REQUEST_TIMEOUT) as ddgs:
                items = ddgs.text(
                    query,
                    max_results=RESULTS_PER_QUERY,
                )

                for item in items:
                    if not isinstance(item, dict):
                        continue

                    results.append(item)

            return results

        except Exception as exc:
            error_text = str(exc).lower()

            retryable = any([
                "reset" in error_text,
                "timeout" in error_text,
                "timed out" in error_text,
                "http2" in error_text,
                "429" in error_text,
                "503" in error_text,
                "connection" in error_text,
            ])

            if not retryable:
                return []

            if attempt < MAX_RETRIES:
                time.sleep(1.0 + attempt)

    return []


# ============================================================
# OPENSTREETMAP DISCOVERY
# ============================================================

def _osm_address(tags, location):
    parts = [
        tags.get("addr:housenumber", ""),
        tags.get("addr:street", ""),
        tags.get("addr:suburb", ""),
        tags.get("addr:city", ""),
        tags.get("addr:postcode", ""),
    ]
    address = ", ".join(part.strip() for part in parts if part.strip())
    return address


def discover_osm_businesses(category, location, wanted):
    """Return named OSM businesses in the requested category and location.

    Missing contact fields are intentionally allowed. OSM identity, element
    type, coordinates, tags, and address are retained as verification
    evidence instead of being flattened into a search-result title.
    """
    tags_to_search = OSM_CATEGORY_TAGS.get(category, [])
    if not tags_to_search:
        return []

    headers = {"User-Agent": "TN-Lead-Scraper/1.0 (local-business-research)"}
    try:
        geocode = requests.get(
            OSM_NOMINATIM_URL,
            params={
                "q": location,
                "format": "jsonv2",
                "limit": 10,
                "addressdetails": 1,
            },
            headers=headers,
            timeout=OSM_TIMEOUT,
        )
        geocode.raise_for_status()
        places = geocode.json()
        if not places:
            print(f"  ! OSM geocoding returned no place for [{location}]")
            return []

        requested = re.sub(r"[^a-z0-9]+", " ", location.lower()).strip()
        place_types = {"city", "town", "village", "municipality", "suburb"}
        ranked_places = sorted(
            places,
            key=lambda place: (
                place.get("type") in place_types,
                requested in str(place.get("display_name", "")).lower(),
                -abs(
                    float(place["boundingbox"][1])
                    - float(place["boundingbox"][0])
                ) * abs(
                    float(place["boundingbox"][3])
                    - float(place["boundingbox"][2])
                ),
            ),
            reverse=True,
        )
        place = ranked_places[0]
        south, north, west, east = place["boundingbox"]
        clauses = "".join(
            f'nwr["{key}"="{value}"]({south},{west},{north},{east});'
            for key, value in tags_to_search
        )
        query = f"[out:json][timeout:25];({clauses});out center tags;"
        response = requests.post(
            OSM_OVERPASS_URL,
            data={"data": query},
            headers=headers,
            timeout=OSM_TIMEOUT + 10,
        )
        response.raise_for_status()
        elements = response.json().get("elements", [])
    except (KeyError, ValueError, requests.RequestException):
        print(f"  ! OSM discovery failed for [{location}]")
        return []

    if not elements:
        print(
            f"  ! OSM found no mapped [{category}] businesses in [{location}] "
            f"for tags: {', '.join(f'{key}={value}' for key, value in tags_to_search)}"
        )

    candidates = []
    seen = set()
    for element in elements:
        tags = element.get("tags", {}) or {}
        name = str(tags.get("name", "")).strip()
        phone = str(tags.get("contact:phone") or tags.get("phone") or "").strip()
        whatsapp = str(
            tags.get("contact:whatsapp") or tags.get("whatsapp") or ""
        ).strip()
        email = str(tags.get("contact:email") or tags.get("email") or "").strip()

        if not name:
            continue

        key = re.sub(r"[^a-z0-9]", "", name.lower())
        if not key or key in seen:
            continue
        seen.add(key)

        website = str(
            tags.get("contact:website") or tags.get("website") or ""
        ).strip()
        if website and is_bad_candidate(name, website):
            continue

        candidates.append({
            "business_name": name,
            "name": name,
            "category": category,
            "website": normalize_url(website) if website else "",
            "url": normalize_url(website) if website else "",
            "address": _osm_address(tags, location),
            "phone": phone,
            "email": email,
            "whatsapp": whatsapp,
            "source": "openstreetmap",
            "search_location": location,
            "search_score": 30,
            "_osm_id": str(element.get("id", "")).strip(),
            "_osm_type": str(element.get("type", "")).strip(),
            "_osm_lat": element.get("lat", (element.get("center") or {}).get("lat")),
            "_osm_lon": element.get("lon", (element.get("center") or {}).get("lon")),
            "_osm_tags": dict(tags),
            "_category_evidence": " ".join(filter(
                None,
                [tags.get("amenity", ""), tags.get("shop", ""), category],
            )),
            "_location_evidence": _osm_address(tags, location) or location,
            "_geocode_display_name": str(
                place.get("display_name", "")
            ).strip(),
            "_geocode_bbox": [south, north, west, east],
            "_geocode_type": str(place.get("type", "")).strip(),
            "_category_verified": True,
            "_location_verified": True,
            "_osm_verified": True,
        })

    candidates.sort(
        key=lambda item: (
            bool(item.get("website")),
            bool(item.get("phone")),
            bool(item.get("email")),
            bool(item.get("address")),
        ),
        reverse=True,
    )
    return candidates


def discover_google_places(category, location, wanted):
    """Discover official business websites through Google Places.

    Google Places is used only as a discovery source. Each candidate still has
    to pass the website/contact verification pipeline before it reaches CSV.
    """
    api_key = os.getenv("GOOGLE_MAPS_API_KEY", "").strip()
    if not api_key:
        return []

    queries = get_category_keywords(category)[:3]
    if not queries:
        queries = [category]

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": (
            "places.id,places.displayName,places.formattedAddress,"
            "places.websiteUri,places.businessStatus"
        ),
    }
    candidates = []
    seen_place_ids = set()

    for keyword in queries:
        try:
            response = requests.post(
                GOOGLE_PLACES_URL,
                headers=headers,
                json={
                    "textQuery": f"{keyword} in {location}, India",
                    "pageSize": 20,
                    "languageCode": "en",
                },
                timeout=OSM_TIMEOUT,
            )
            response.raise_for_status()
            places = response.json().get("places", [])
        except (ValueError, requests.RequestException):
            continue

        for place in places:
            place_id = str(place.get("id", "")).strip()
            name = str((place.get("displayName") or {}).get("text", "")).strip()
            website = normalize_url(place.get("websiteUri", ""))

            if not place_id or place_id in seen_place_ids:
                continue
            seen_place_ids.add(place_id)

            if not name or not website or is_bad_candidate(name, website):
                continue

            candidates.append({
                "business_name": name,
                "name": name,
                "category": category,
                "website": website,
                "url": website,
                "address": str(place.get("formattedAddress", "")).strip(),
                "phone": "",
                "email": "",
                "whatsapp": "",
                "source": "google_places",
                "search_location": location,
                "search_score": 45,
            })

    return candidates


# ============================================================
# CANDIDATE DISCOVERY
# ============================================================

def discover_businesses(category, location, wanted=50):
    canonical = normalize_category(category)

    osm_candidates = discover_osm_businesses(canonical, location, wanted)
    if not ALLOW_SEARCH_FALLBACK:
        print(
            f"\nOSM-first discovery for [{canonical}] in [{location}]..."
        )
        print(
            f"  + {len(osm_candidates)} OpenStreetMap candidates"
        )
        print(
            f"  → {len(osm_candidates)} candidates after source filtering"
        )
        osm_candidates = deduplicate_businesses(osm_candidates)
        osm_candidates.sort(
            key=lambda item: (
                bool(item.get("website")),
                bool(item.get("phone")),
                bool(item.get("email")),
                bool(item.get("address")),
            ),
            reverse=True,
        )
        return osm_candidates

    queries = build_queries(canonical, location)

    print(
        f"\nSearching {len(queries)} targeted queries "
        f"for [{canonical}] in [{location}]..."
    )

    raw_results = []

    # Two workers only.
    # More workers caused HTTP2/connection reset issues.
    with ThreadPoolExecutor(max_workers=WEB_WORKERS) as executor:

        futures = {
            executor.submit(search_one_query, query): query
            for query in queries
        }

        for future in as_completed(futures):
            query = futures[future]

            try:
                results = future.result()

                raw_results.extend(results)

                print(
                    f"  ✓ {len(results):2d} results | "
                    f"{query[:75]}"
                )

            except Exception as exc:
                print(
                    f"  ! Search failed: {query[:60]} "
                    f"({exc})"
                )

    social_results = []
    social_queries = build_social_queries(canonical, location)
    with ThreadPoolExecutor(max_workers=WEB_WORKERS) as executor:
        futures = {
            executor.submit(search_one_query, query): query
            for query in social_queries
        }

        for future in as_completed(futures):
            try:
                social_results.extend(future.result())
            except Exception as exc:
                print(
                    f"  ! Social search failed: {futures[future][:60]} "
                    f"({exc})"
                )

    candidates = []

    seen_domains = set()

    for item in raw_results:

        title = str(
            item.get("title")
            or item.get("name")
            or ""
        ).strip()

        url = normalize_url(
            item.get("href")
            or item.get("url")
            or ""
        )

        snippet = str(
            item.get("body")
            or item.get("snippet")
            or ""
        ).strip()

        if not title or not url:
            continue

        if is_bad_candidate(title, url, snippet):
            continue

        domain = root_domain(url)

        if not domain:
            continue

        # Deduplicate domains
        if domain in seen_domains:
            continue

        seen_domains.add(domain)

        score = 0

        score += category_match_score(
            title,
            snippet,
            canonical,
        )

        score += location_match_score(
            title,
            snippet,
            location,
        )

        text = f"{title} {snippet}".lower()

        if "contact" in text:
            score += 5

        if "whatsapp" in text:
            score += 10

        if "order online" in text or "shop online" in text:
            score += 8

        if "official website" in text:
            score += 3

        candidates.append({
            "business_name": title,
            "name": title,
            "category": canonical,
            "website": url,
            "url": url,
            "address": "",
            "phone": "",
            "email": "",
            "whatsapp": "",
            "source": "web_search",
            "search_location": location,
            "search_score": score,
            "snippet": snippet,
            "_location_evidence": f"{title} {snippet}",
        })

    candidates.sort(
        key=lambda x: x.get("search_score", 0),
        reverse=True,
    )

    # Don't return an excessive number of junk candidates
    # Low-scoring results are usually search articles that survived a generic
    # domain check. Keep enough candidates for verification without spending
    # requests on those weak matches.
    candidates = [
        candidate for candidate in candidates
        if candidate.get("search_score", 0) >= MIN_SEARCH_SCORE
    ]

    social_candidates = []
    social_seen = set()
    for item in social_results:
        profile_url = extract_social_profile(
            item.get("href") or item.get("url") or ""
        )
        if not profile_url or profile_url in social_seen:
            continue

        title = str(item.get("title") or item.get("name") or "").strip()
        snippet = str(
            item.get("body") or item.get("snippet") or ""
        ).strip()
        if not title and not snippet:
            continue

        business_name = clean_social_business_name(
            title,
            profile_url,
        )
        if not business_name:
            continue

        social_seen.add(profile_url)
        social_candidates.append({
            "business_name": business_name,
            "name": business_name,
            "category": canonical,
            "website": "",
            "url": "",
            "social_profile_url": profile_url,
            "address": "",
            "phone": "",
            "email": "",
            "whatsapp": "",
            "source": "social_search",
            "search_location": location,
            "search_score": (
                category_match_score(title, snippet, canonical)
                + location_match_score(title, snippet, location)
            ),
            "snippet": snippet,
            "_category_evidence": f"{title} {snippet} {canonical}",
            "_location_evidence": f"{title} {snippet}",
            "_social_verified": True,
        })

    candidates.extend(
        social_candidates
    )

    # Map records complement search results for local businesses that have no
    # well-indexed website. They are deliberately restricted to public
    # outreach details before entering the verification pipeline.
    for candidate in osm_candidates:
        domain = root_domain(candidate.get("website", ""))
        if domain and domain in seen_domains:
            continue
        if domain:
            seen_domains.add(domain)
        candidates.append(candidate)

    google_candidates = discover_google_places(
        canonical,
        location,
        wanted,
    )
    for candidate in google_candidates:
        domain = root_domain(candidate.get("website", ""))
        if domain and domain in seen_domains:
            continue
        if domain:
            seen_domains.add(domain)
        candidates.append(candidate)

    candidates = deduplicate_businesses(candidates)
    candidates.sort(
        key=lambda x: x.get("search_score", 0),
        reverse=True,
    )
    if osm_candidates:
        print(
            f"  + {len(osm_candidates)} map candidates with public contact details"
        )

    if google_candidates:
        print(
            f"  + {len(google_candidates)} Google Places website candidates"
        )

    print(
        f"  → {len(candidates)} usable candidates after filtering"
    )

    return candidates


# ============================================================
# DEDUPLICATION
# ============================================================

def deduplicate_businesses(businesses):
    """
    Remove duplicate businesses by country-aware website root, stable
    source identity, branch-safe phone/name identity, and normalized name.
    """

    unique = []

    seen_domains = set()
    seen_phones = set()
    seen_names = set()

    for business in businesses:

        website = business.get("website") or business.get("url") or ""

        domain = root_domain(website)

        phone = re.sub(
            r"\D",
            "",
            str(business.get("phone", "")),
        )

        name = re.sub(
            r"[^a-z0-9]+",
            " ",
            str(
                business.get("business_name")
                or business.get("name")
                or ""
            ).lower(),
        ).strip()

        address = re.sub(
            r"[^a-z0-9]+",
            " ",
            str(business.get("address", "")).lower(),
        ).strip()
        osm_key = (
            f"{business.get('_osm_type')}:{business.get('_osm_id')}"
            if business.get("_osm_id")
            else ""
        )
        # Scope fallback identity to the website when present so the same
        # brand name on country domains (or separate branch sites) is kept.
        name_identity = f"{name}:{address}" if name and address else name
        name_key = f"{domain}:{name_identity}" if domain and name_identity else name_identity

        if domain and domain in seen_domains:
            continue
        if osm_key and osm_key in seen_names:
            continue

        # A shared corporate phone must not merge separate branches or brands.
        # Phone-only deduplication is safe only when the identity is otherwise
        # the same; website, OSM, and name/address keys remain authoritative.
        phone_key = f"{phone}:{name_key}" if phone and name_key else ""

        if phone_key and phone_key in seen_phones:
            continue

        if name_key and name_key in seen_names:
            continue

        if domain:
            seen_domains.add(domain)

        if phone_key:
            seen_phones.add(phone_key)

        if name_key:
            seen_names.add(name_key)
        if osm_key:
            seen_names.add(osm_key)

        unique.append(business)

    return unique


# Backward-compatible alias
deduplicate_leads = deduplicate_businesses


# ============================================================
# OPTIONAL CATEGORY INFORMATION
# ============================================================

def get_category_info(category):
    canonical = normalize_category(category)

    return {
        "category": canonical,
        "keywords": get_category_keywords(canonical),
    }
