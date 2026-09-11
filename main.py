
import csv
from difflib import SequenceMatcher
import re
import sys
import time
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from scraper.search import (
    discover_businesses,
    deduplicate_businesses,
    normalize_category,
    get_category_keywords,
)

from scraper.extractor import extract_contacts
from scraper.social_enrichment import (
    enrich_with_instagram,
    instagram_api_configured,
)

from utils.helpers import (
    is_directory,
    is_bad_url_path,
    is_fake_phone,
    is_free_email,
    email_domain_matches_website,
    contact_quality_score,
)


# ============================================================
# SETTINGS
# ============================================================

OUTPUT_FILE = Path("data") / "business_leads.csv"

REQUEST_TIMEOUT = 8
VERIFY_WORKERS = 6
_session_local = threading.local()

MIN_AUTOMATION_SCORE = 60

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/140.0 Safari/537.36"
)

HEADERS = {
    "User-Agent": USER_AGENT
}


def get_http_session():
    session = getattr(_session_local, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update(HEADERS)
        _session_local.session = session
    return session


def configure_console():
    """Keep status output usable in Windows terminals with legacy encodings."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(
                encoding="utf-8",
                errors="replace",
            )


# ============================================================
# FIXED CATEGORY SYSTEM
# ============================================================

FIXED_CATEGORIES = {

    "clothing": "Clothing",
    "boutique": "Clothing",
    "apparel": "Clothing",
    "garments": "Clothing",
    "garment": "Clothing",
    "fashion": "Clothing",
    "fashion store": "Clothing",
    "fashion boutique": "Clothing",
    "streetwear": "Clothing",
    "mens clothing": "Clothing",
    "womens clothing": "Clothing",
    "kids clothing": "Clothing",
    "textile": "Clothing",
    "textile store": "Clothing",

    "gym": "Gym",
    "fitness": "Gym",
    "fitness centre": "Gym",
    "fitness center": "Gym",
    "fitness studio": "Gym",
    "gymnasium": "Gym",
    "crossfit": "Gym",
    "workout studio": "Gym",

    "salon": "Salon",
    "beauty salon": "Salon",
    "hair salon": "Salon",
    "ladies salon": "Salon",
    "unisex salon": "Salon",
    "beauty parlour": "Salon",
    "beauty parlor": "Salon",
    "spa": "Salon",

    "restaurant": "Restaurant",
    "restaurants": "Restaurant",
    "family restaurant": "Restaurant",
    "veg restaurant": "Restaurant",
    "non veg restaurant": "Restaurant",

    "cafe": "Cafe",
    "café": "Cafe",
    "coffee shop": "Cafe",
    "coffee cafe": "Cafe",

    "bakery": "Bakery",
    "cake shop": "Bakery",
    "cakes": "Bakery",

    "clinic": "Clinic",
    "medical clinic": "Clinic",
    "health clinic": "Clinic",

    "dental clinic": "Dental Clinic",
    "dentist": "Dental Clinic",
    "dental": "Dental Clinic",

    "pet clinic": "Pet Clinic",
    "veterinary clinic": "Pet Clinic",
    "veterinary": "Pet Clinic",
    "vet": "Pet Clinic",

    "furniture": "Furniture",
    "furniture store": "Furniture",
    "furniture shop": "Furniture",

    "jewellery": "Jewellery",
    "jewelry": "Jewellery",
    "jewellery store": "Jewellery",
    "jewelry store": "Jewellery",

    "mobile": "Mobile Shop",
    "mobile shop": "Mobile Shop",
    "mobile store": "Mobile Shop",
    "mobile phone shop": "Mobile Shop",

    "electronics": "Electronics",
    "electronics store": "Electronics",
    "electronic shop": "Electronics",

    "grocery": "Grocery",
    "grocery store": "Grocery",
    "supermarket": "Grocery",
    "provision store": "Grocery",

    "spices": "Spices",
    "spice": "Spices",
    "spice store": "Spices",
    "masala": "Spices",
    "masala store": "Spices",

    "real estate": "Real Estate",
    "real estate agency": "Real Estate",
    "property": "Real Estate",
    "property dealer": "Real Estate",

    "photographer": "Photography",
    "photography": "Photography",
    "photo studio": "Photography",

    "coaching": "Coaching",
    "coaching centre": "Coaching",
    "coaching center": "Coaching",
    "training institute": "Coaching",
    "tuition": "Coaching",

    "interior designer": "Interior Design",
    "interior design": "Interior Design",
    "interiors": "Interior Design",

    "car service": "Car Service",
    "car repair": "Car Service",
    "auto service": "Car Service",
    "automobile service": "Car Service",

    "driving school": "Driving School",

    "toy store": "Toys",
    "toy shop": "Toys",
    "toys": "Toys",

    "watch shop": "Watches",
    "watch store": "Watches",
    "watches": "Watches",

    "online store": "Online Store",
    "online shop": "Online Store",
    "ecommerce": "Online Store",
    "e-commerce": "Online Store",
}


def fixed_category(value):
    """
    Convert every user/search category into one stable CSV category.
    """
    value = str(value or "").strip().lower()
    value = re.sub(r"\s+", " ", value)

    if value in FIXED_CATEGORIES:
        return FIXED_CATEGORIES[value]

    # Use the search module's normalization as a secondary lookup.
    try:
        normalized = normalize_category(value)
        normalized = str(normalized or "").strip().lower()

        if normalized in FIXED_CATEGORIES:
            return FIXED_CATEGORIES[normalized]

        # Old search.py names
        old_map = {
            "clothing store": "Clothing",
            "boutique": "Clothing",
            "gym": "Gym",
            "salon": "Salon",
            "restaurant": "Restaurant",
            "cafe": "Cafe",
            "bakery": "Bakery",
            "clinic": "Clinic",
            "dental clinic": "Dental Clinic",
            "pet clinic": "Pet Clinic",
            "furniture store": "Furniture",
            "jewellery store": "Jewellery",
            "mobile shop": "Mobile Shop",
            "electronics store": "Electronics",
            "textile store": "Clothing",
            "spice store": "Spices",
            "grocery store": "Grocery",
            "real estate": "Real Estate",
            "photographer": "Photography",
            "coaching/training": "Coaching",
            "interior designer": "Interior Design",
            "car service": "Car Service",
            "driving school": "Driving School",
            "toy store": "Toys",
            "watch shop": "Watches",
            "online store": "Online Store",
        }

        if normalized in old_map:
            return old_map[normalized]

    except Exception:
        pass

    # Preserve unknown categories in a clean format.
    return value.title()


# ============================================================
# FILTERS
# ============================================================

BAD_DOMAINS = {
    "justdial.com",
    "sulekha.com",
    "indiamart.com",
    "tradeindia.com",
    "yellowpages.co.in",
    "facebook.com",
    "instagram.com",
    "youtube.com",
    "linkedin.com",
    "twitter.com",
    "x.com",
    "linktr.ee",
    "beacons.ai",
    "taplink.cc",
    "bio.link",
    "amazon.in",
    "amazon.com",
    "flipkart.com",
    "myntra.com",
    "meesho.com",
    "naukri.com",
    "indeed.com",
    "gov.in",
    "nic.in",
    "joonsquare.com",
    "gymlocator.in",
    "dialmia.com",
    "funtalia.com",
    "threebestrated.in",
    "3bestincity.com",
    "yappe.in",
}

BAD_TERMS = (
    "directory",
    "listing",
    "listings",
    "jobs",
    "career",
    "vacancy",
    "government",
    "ministry",
    "department",
    "university",
    "college",
    "court",
    "news",
    "blog",
    "review",
    "top 10",
    "top 20",
    "best restaurants",
    "best stores",
    "digital marketing agency",
    "web development agency",
    "software company",
    "seo agency",
    "ecommerce development",
    "whatsapp api provider",
    "crm software",
    "saas company",
)


def clean_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_digits(value):
    return re.sub(r"\D", "", str(value or ""))


def get_domain(url):
    try:
        from urllib.parse import urlparse

        domain = urlparse(str(url or "")).netloc.lower()

        if domain.startswith("www."):
            domain = domain[4:]

        return domain
    except Exception:
        return ""


def website_origin(url):
    """Return the business website origin instead of an internal page URL."""
    try:
        parsed = urlparse(str(url or ""))
        if not parsed.netloc:
            return clean_text(url)
        return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    except Exception:
        return clean_text(url)


def is_bad_domain(url):
    domain = get_domain(url)

    return any(
        domain == item or domain.endswith("." + item)
        for item in BAD_DOMAINS
    )


def contains_bad_terms(value):
    text = clean_text(value).lower()

    return any(
        term in text
        for term in BAD_TERMS
    )


def usable_business_name(value):
    """Reject URLs, aggregate titles, and category-only names."""
    name = clean_text(value)
    lowered = name.lower()
    if not name or len(name) > 140:
        return False
    if re.match(r"^(https?://|www\.)", lowered):
        return False
    if any(marker in lowered for marker in (
        "online shopping for",
        "official website",
        "contact us",
        "near me",
        "list of ",
        "best ",
        "home page",
        "welcome to ",
        "step into ",
        "specialty coffee shop in ",
        "order cakes online",
    )):
        return False
    if contains_bad_terms(name):
        return False
    if re.fullmatch(
        r"(clothing|clothes|fashion|textiles?|toys?|cafe|cafes|"
        r"restaurant|restaurants|store|shop)",
        lowered,
    ):
        return False
    return bool(re.search(r"[a-zA-Z\u0B80-\u0BFF]", name))


# ============================================================
# CATEGORY MATCHING
# ============================================================

def category_terms(category):

    canonical = fixed_category(category)

    try:
        raw = get_category_keywords(category)
    except Exception:
        raw = []

    terms = [canonical.lower()]

    for item in raw:
        item = clean_text(item).lower()

        if len(item) >= 3:
            terms.append(item)

    # Extra strong aliases
    extras = {
        "Clothing": [
            "clothing",
            "boutique",
            "apparel",
            "garment",
            "fashion",
            "streetwear",
            "textile",
        ],
        "Gym": [
            "gym",
            "fitness",
            "crossfit",
            "workout",
            "fitness centre",
            "fitness center",
        ],
        "Salon": [
            "salon",
            "beauty",
            "hair",
            "parlour",
            "parlor",
            "spa",
        ],
        "Restaurant": [
            "restaurant",
            "dining",
            "eatery",
        ],
        "Cafe": [
            "cafe",
            "café",
            "coffee",
        ],
        "Bakery": [
            "bakery",
            "cakes",
        ],
        "Clinic": [
            "clinic",
            "medical",
        ],
        "Furniture": [
            "furniture",
        ],
        "Jewellery": [
            "jewellery",
            "jewelry",
        ],
        "Mobile Shop": [
            "mobile",
            "smartphone",
            "phone shop",
        ],
        "Electronics": [
            "electronics",
            "electronic",
        ],
        "Grocery": [
            "grocery",
            "supermarket",
            "provision",
        ],
        "Spices": [
            "spice",
            "spices",
            "masala",
        ],
    }

    terms.extend(
        extras.get(canonical, [])
    )

    return list(dict.fromkeys(terms))


def category_matches(category, data):

    if data.get("_category_verified"):
        return True

    terms = category_terms(category)

    searchable = " ".join(
        clean_text(data.get(key, ""))
        for key in (
            "business_name",
            "_category_evidence",
            "_title",
            "_description",
            "_website_text",
            "description",
            "services",
        )
    ).lower()

    if not searchable:
        return False

    # Strong exact phrase match
    for term in terms:
        if term in searchable:
            return True

    # Allow product/service evidence.
    canonical = fixed_category(category)

    if canonical == "Clothing":
        return any(
            word in searchable
            for word in (
                "dress",
                "saree",
                "shirt",
                "jeans",
                "kurti",
                "ethnic wear",
                "western wear",
                "menswear",
                "womenswear",
            )
        )

    if canonical == "Gym":
        return any(
            word in searchable
            for word in (
                "strength training",
                "personal trainer",
                "weight training",
                "workout",
                "crossfit",
                "fitness training",
            )
        )

    return False


# ============================================================
# LOCATION MATCHING
# ============================================================

def location_matches(location, data):

    wanted = clean_text(location).lower()

    if not wanted:
        return True

    if data.get("_location_verified") and not clean_text(
        data.get("address", "")
    ):
        # OSM coordinates/bounds are independent location evidence when the
        # mapper did not provide a textual address.
        return True

    explicit_address = clean_text(data.get("address", "")).lower()
    if explicit_address:
        # An extracted address is authoritative. Do not let a query phrase in
        # a title or page body override an address from another city.
        address_parts = [
            clean_text(part).lower()
            for part in re.split(r"[,/-]", wanted)
            if clean_text(part)
        ]
        if wanted in explicit_address:
            return True
        if len(address_parts) >= 2:
            return sum(part in explicit_address for part in address_parts) >= 2
        if len(address_parts) == 1 and len(address_parts[0]) >= 4:
            return address_parts[0] in explicit_address
        return False

    searchable_fields = [
        "_title",
        "_description",
        "_website_text",
        "business_name",
    ]
    if not explicit_address:
        searchable_fields.append("_location_evidence")

    searchable = " ".join(
        clean_text(data.get(key, ""))
        for key in searchable_fields
    ).lower()

    if not searchable:
        return False

    if wanted in searchable:
        return True

    parts = [
        clean_text(part).lower()
        for part in re.split(r"[,/-]", wanted)
        if clean_text(part)
    ]

    if len(parts) >= 2:
        hits = sum(
            1 for part in parts
            if part in searchable
        )

        return hits >= 2

    part = parts[0] if parts else wanted

    if len(part) >= 6:
        return part in searchable

    return False


# ============================================================
# AUTOMATION SCORE
# ============================================================

def calculate_automation_score(data):

    score = 0

    if data.get("phone"):
        score += 15

    if data.get("email"):
        score += 8

    if data.get("whatsapp"):
        score += 25

    signals = " ".join(
        clean_text(data.get(key, ""))
        for key in (
            "_website_text",
            "_description",
            "description",
            "services",
        )
    ).lower()

    if "whatsapp" in signals:
        score += 10

    if "catalog" in signals:
        score += 6

    if "order" in signals:
        score += 8

    if "appointment" in signals:
        score += 10

    if "booking" in signals:
        score += 10

    if "online order" in signals:
        score += 10

    if "enquiry" in signals or "enquire" in signals:
        score += 7

    if "product" in signals:
        score += 6

    if "delivery" in signals:
        score += 5

    if "offer" in signals:
        score += 5

    if any(
        term in signals
        for term in (
            "demo",
            "free trial",
            "admission",
            "enroll",
            "registration",
        )
    ):
        score += 7

    if data.get("address"):
        score += 5

    if data.get("_osm_verified"):
        score += 15

    return min(score, 100)


def opportunity(score):

    if score >= 85:
        return "VERY HIGH"

    if score >= 75:
        return "HIGH"

    if score >= 60:
        return "GOOD"

    return "POTENTIAL"


def lead_contact_priority(lead):
    """Rank leads by the contact details most useful for outreach."""
    has_phone = bool(clean_text(lead.get("phone", "")))
    has_email = bool(clean_text(lead.get("email", "")))
    has_website = bool(clean_text(lead.get("website", "")))

    return (
        int(has_phone and has_email and has_website),
        int(has_phone) + int(has_email) + int(has_website),
        int(has_phone),
        int(has_email),
        int(has_website),
    )


def has_outreach_contact(lead):
    """Return whether a lead has at least one usable outreach channel."""
    return any(
        clean_text(lead.get(field, ""))
        for field in ("website", "phone", "email")
    )


def evidence_confidence(data):
    """Expose source confidence without changing the six-column CSV."""
    identity = 0
    category = 0
    location = 0
    contact = 0

    if data.get("_osm_verified"):
        identity += 45
        category += 35
        location += 35
    if data.get("business_name"):
        identity += 25
    if data.get("_website_text"):
        identity += 15
    if data.get("_category_verified"):
        category += 45
    if data.get("_category_evidence") or data.get("_website_text"):
        category += 15
    if data.get("_location_verified"):
        location += 45
    if data.get("address"):
        location += 35
    if data.get("phone"):
        contact += 35
    if data.get("email"):
        contact += 30
    if data.get("website"):
        contact += 20

    return {
        "identity": min(identity, 100),
        "category": min(category, 100),
        "location": min(location, 100),
        "contact": min(contact, 100),
    }


def identity_similarity(left, right):
    """Compare two business names after removing generic business words."""
    def normalized(value):
        value = re.sub(r"[^a-z0-9]+", " ", clean_text(value).lower())
        words = [
            word for word in value.split()
            if word not in {
                "shop", "store", "cafe", "coffee", "restaurant",
                "official", "website", "india",
            }
        ]
        return " ".join(words)

    first = normalized(left)
    second = normalized(right)
    if not first or not second:
        return 0.0
    if first == second or first in second or second in first:
        return 1.0
    return SequenceMatcher(None, first, second).ratio()


# ============================================================
# VERIFY BUSINESS
# ============================================================

def verify_candidate(
    candidate,
    requested_category,
    search_location,
    rejection_reasons=None,
):

    candidate_label = clean_text(
        candidate.get("business_name")
        or candidate.get("name")
        or candidate.get("title")
        or candidate.get("website")
        or "Unnamed candidate"
    )

    def reject(reason):
        if rejection_reasons is not None:
            rejection_reasons.append(
                f"{candidate_label}: {reason}"
            )
        return None

    # IMPORTANT:
    # This is the ONLY category used in the final CSV.
    category = fixed_category(requested_category)

    website = clean_text(
        candidate.get("website")
        or candidate.get("url")
        or ""
    )

    if website:

        if is_bad_domain(website):
            return reject("blocked directory/social domain")

        if is_directory(website):
            return reject("directory website")

        if is_bad_url_path(website):
            return reject("blocked directory/article URL path")

    candidate_name = clean_text(
        candidate.get("business_name")
        or candidate.get("name")
        or candidate.get("title")
        or ""
    )

    if not candidate_name:
        return reject("missing business name")

    if not usable_business_name(candidate_name):
        return reject("business name is not a clean business identity")

    result = {}

    # --------------------------------------------------------
    # OSM / candidate-only record
    # --------------------------------------------------------

    if (
        candidate.get("_osm_verified")
        or candidate.get("_social_verified")
    ) and not website:

        result = dict(candidate)

    # --------------------------------------------------------
    # Website extraction
    # --------------------------------------------------------

    elif website:

        try:
            response = get_http_session().get(
                website,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
            )

            if response.status_code >= 400:
                return reject(
                    f"website returned HTTP {response.status_code}"
                )

            html = response.text

            try:
                result = extract_contacts(
                    html,
                    url=response.url,
                ) or {}
            except Exception:
                result = {}

            result["_title"] = clean_text(
                candidate.get("title")
                or candidate_name
            )

            result["_description"] = clean_text(
                candidate.get("description")
            )

            result["_website_text"] = clean_text(
                BeautifulSoup(html, "html.parser").get_text(" ")
                if html
                else ""
            )

        except Exception as error:
            if candidate.get("_osm_verified"):
                result = dict(candidate)
                result["_website_failure"] = (
                    f"{type(error).__name__}: {error}"
                )
            else:
                return reject(
                    f"website request failed: {type(error).__name__}"
                )

    else:
        return reject("missing website and no verified map record")

    # --------------------------------------------------------
    # Merge candidate data
    # --------------------------------------------------------

    business_name = clean_text(
        result.get("business_name")
        or candidate_name
    )

    website_name = clean_text(result.get("business_name"))
    name_similarity = identity_similarity(candidate_name, website_name)
    if website_name and name_similarity < 0.35:
        if candidate.get("_osm_verified"):
            result["_identity_conflict"] = (
                f"OSM name {candidate_name!r} vs website name {website_name!r}"
            )
        else:
            return reject(
                f"website identity conflicts with candidate ({website_name})"
            )

    phone = clean_text(
        result.get("phone")
        or candidate.get("phone")
        or ""
    )

    email = clean_text(
        result.get("email")
        or candidate.get("email")
        or ""
    )

    whatsapp = clean_text(
        result.get("whatsapp")
        or candidate.get("whatsapp")
        or ""
    )

    address = clean_text(
        result.get("address")
        or candidate.get("address")
        or ""
    )

    # --------------------------------------------------------
    # Validation
    # --------------------------------------------------------

    if not business_name:
        return reject("missing business name after extraction")

    if not usable_business_name(business_name):
        return reject("extracted business name is not a clean business identity")

    if phone and is_fake_phone(phone):
        phone = ""

    if whatsapp and is_fake_phone(whatsapp):
        whatsapp = ""

    if email:

        if (
            is_free_email(email)
            and website
            and not email_domain_matches_website(
                email,
                website
            )
        ):
            # Free email is still allowed.
            # Do NOT reject a potentially good small business.
            pass

    data = dict(result)

    data.update({
        "business_name": business_name,
        "phone": phone,
        "email": email,
        "whatsapp": whatsapp,
        "address": address,
        "_category_evidence": clean_text(
            candidate.get("_category_evidence")
            or candidate.get("snippet")
            or ""
        ),
        "_location_evidence": clean_text(
            candidate.get("_location_evidence")
            or candidate.get("snippet")
            or candidate.get("title")
            or ""
        ),
        "_category_verified": bool(
            candidate.get("_category_verified")
        ),
        "_location_verified": bool(
            candidate.get("_location_verified")
        ),
        "_identity_confidence": (
            result.get("confidence", {}).get("business_name", 0)
            if isinstance(result.get("confidence"), dict)
            else 0
        ),
        "_website_name": website_name,
        "_identity_similarity": name_similarity,
        "_website_failure": clean_text(
            result.get("_website_failure")
        ),
        "_identity_conflict": clean_text(
            result.get("_identity_conflict")
        ),
    })

    # --------------------------------------------------------
    # Category
    # --------------------------------------------------------
    #
    # IMPORTANT:
    # Extractor category is deliberately ignored.
    # It can only provide evidence.
    #

    if not category_matches(category, data):
        return reject(
            f"category evidence does not match requested category {category}"
        )

    # --------------------------------------------------------
    # Location
    # --------------------------------------------------------

    if not location_matches(
        search_location,
        data
    ):

        return reject(
            f"location does not match requested location {search_location}"
        )

    # --------------------------------------------------------
    # Contact quality
    # --------------------------------------------------------

    quality = contact_quality_score(
        data,
        website
    )

    # --------------------------------------------------------
    # Automation score
    # --------------------------------------------------------

    automation_score = calculate_automation_score(
        data
    )

    if automation_score < MIN_AUTOMATION_SCORE:
        automation_score = max(
            automation_score,
            0
        )

    social_links = result.get("social_links", {}) or {}
    instagram_url = clean_text(
        social_links.get("instagram")
        or candidate.get("instagram_url")
        or ""
    )
    facebook_url = clean_text(
        social_links.get("facebook")
        or candidate.get("facebook_url")
        or ""
    )
    social_profile_url = clean_text(
        candidate.get("social_profile_url")
        or ""
    )
    if social_profile_url:
        if "instagram.com/" in social_profile_url:
            instagram_url = instagram_url or social_profile_url
        elif "facebook.com/" in social_profile_url:
            facebook_url = facebook_url or social_profile_url

    lead = {
        "business_name": business_name,
        "category": category,          # FIXED
        "website": website_origin(website),
        "phone": phone,
        "address": address,
        "email": email,
        "whatsapp": whatsapp,
        "automation_score": automation_score,
        "automation_opportunity": opportunity(
            automation_score
        ),
        "contact_quality": quality,
        "source": candidate.get(
            "source",
            "Web Search"
        ),
        "search_location": search_location,
        "instagram_url": instagram_url,
        "facebook_url": facebook_url,
        "evidence_confidence": evidence_confidence(data),
        "osm_id": clean_text(candidate.get("_osm_id")),
        "source_url": clean_text(
            candidate.get("website")
            or candidate.get("url")
            or ""
        ),
        "verification_status": (
            "uncertain"
            if data.get("_identity_conflict") or data.get("_website_failure")
            else "verified"
        ),
        "verification_reasons": [
            reason for reason in (
                data.get("_identity_conflict"),
                data.get("_website_failure"),
            ) if reason
        ],
    }
    missing_fields = [
        field for field in ("website", "phone", "email")
        if not clean_text(lead.get(field))
    ]
    if missing_fields:
        lead["verification_reasons"].append(
            "missing outreach fields: " + ", ".join(missing_fields)
        )

    if instagram_url and instagram_api_configured():
        lead = enrich_with_instagram(
            lead,
            instagram_url,
        )

    return lead


# ============================================================
# FINAL DEDUPLICATION
# ============================================================

def deduplicate_verified(leads):

    unique = {}

    for lead in leads:

        domain = get_domain(
            lead.get("website", "")
        )

        phone = normalize_digits(
            lead.get("phone", "")
        )

        name = re.sub(
            r"[^a-z0-9]+",
            "",
            clean_text(
                lead.get("business_name", "")
            ).lower()
        )

        address = re.sub(
            r"[^a-z0-9]+",
            "",
            clean_text(lead.get("address", "")).lower(),
        )

        # A shared corporate phone is not enough to merge different brands.
        # A domain, or the same name at the same address, is stronger.
        if domain:
            key = f"domain:{domain}"
        elif name and address:
            key = f"name-address:{name}:{address}"
        elif name:
            key = f"name:{name}"
        else:
            key = f"phone-name:{phone}:{name}" if phone else None

        if not key:
            continue

        old = unique.get(key)

        if (
            old is None
            or lead.get(
                "automation_score",
                0
            ) > old.get(
                "automation_score",
                0
            )
        ):
            unique[key] = lead

    return list(unique.values())


# ============================================================
# SAVE CSV
# ============================================================

def save_csv(leads):

    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    columns = [
        "business_name",
        "category",
        "website",
        "phone",
        "address",
        "email",
    ]

    rows = []

    for lead in leads:

        rows.append({
            "business_name": lead.get(
                "business_name",
                ""
            ),
            "category": fixed_category(
                lead.get(
                    "category",
                    ""
                )
            ),
            "website": clean_text(
                lead.get(
                    "website",
                    ""
                )
            ).rstrip("/"),
            "phone": clean_text(
                lead.get(
                    "phone",
                    ""
                )
            ),
            "address": clean_text(
                lead.get(
                    "address",
                    ""
                )
            ),
            "email": clean_text(
                lead.get(
                    "email",
                    ""
                )
            ),
        })

    try:

        with open(
            OUTPUT_FILE,
            "w",
            newline="",
            encoding="utf-8-sig",
        ) as file:

            writer = csv.DictWriter(
                file,
                fieldnames=columns
            )

            writer.writeheader()
            writer.writerows(rows)

        return True

    except PermissionError:

        print()
        print(
            "❌ CSV is open in Excel/LibreOffice."
        )

        print(
            f"Close {OUTPUT_FILE} and run again."
        )

        return False


# ============================================================
# MAIN
# ============================================================

def main():

    print()
    print("=" * 70)
    print("       TAMIL NADU LOCAL BUSINESS LEAD SCRAPER")
    print("=" * 70)

    category_input = input(
        "\nBusiness categories (comma separated): "
    ).strip()

    location_input = input(
        "Cities/locations (comma separated): "
    ).strip()

    categories = [
        fixed_category(item)
        for item in category_input.split(",")
        if clean_text(item)
    ]

    locations = [
        clean_text(item)
        for item in location_input.split(",")
        if clean_text(item)
    ]

    categories = list(
        dict.fromkeys(categories)
    )

    if not categories:
        print("❌ No category entered.")
        return

    if not locations:
        print("❌ No location entered.")
        return

    print()
    print("Fixed categories:")
    print(
        ", ".join(categories)
    )

    candidates = []

    # --------------------------------------------------------
    # DISCOVERY
    # --------------------------------------------------------

    for category in categories:

        for location in locations:

            print()
            print(
                f"SEARCHING: {category} - {location}"
            )

            try:

                found = discover_businesses(
                    category=category,
                    location=location,
                    wanted=0
                )

            except Exception as error:

                print(
                    f"⚠ Search failed: {error}"
                )

                continue

            for item in found:

                item["category"] = category
                item["requested_category"] = category
                item["requested_location"] = location

                candidates.append(item)

            print(
                f"  Candidates: {len(found)}"
            )

    candidates = deduplicate_businesses(
        candidates
    )

    print()
    print(
        f"Unique candidates: {len(candidates)}"
    )

    # --------------------------------------------------------
    # VERIFICATION
    # --------------------------------------------------------

    verified = []
    rejection_reasons = []

    print()
    print(
        f"Verifying {len(candidates)} candidates..."
    )

    with ThreadPoolExecutor(
        max_workers=VERIFY_WORKERS
    ) as executor:

        futures = {}

        for candidate in candidates:

            category = fixed_category(
                candidate.get(
                    "category",
                    ""
                )
            )

            location = candidate.get(
                "requested_location",
                candidate.get(
                    "search_location",
                    ""
                )
            )

            future = executor.submit(
                verify_candidate,
                candidate,
                category,
                location,
                rejection_reasons,
            )

            futures[future] = candidate

        for future in as_completed(
            futures
        ):

            try:

                lead = future.result()

                if lead:
                    verified.append(lead)

            except Exception as error:
                rejection_reasons.append(
                    f"{futures[future].get('business_name', 'Unnamed candidate')}: "
                    f"verification crashed ({type(error).__name__})"
                )
                continue

    if rejection_reasons:
        print()
        print("REJECTED CANDIDATES (verification reasons)")
        for reason in rejection_reasons:
            print(f"  - {reason}")

    # --------------------------------------------------------
    # FINAL
    # --------------------------------------------------------

    verified = deduplicate_verified(
        verified
    )

    verified.sort(
        key=lambda item: (
            lead_contact_priority(item),
            item.get(
                "automation_score",
                0
            ),
            bool(
                item.get(
                    "whatsapp"
                )
            ),
            bool(
                item.get(
                    "email"
                )
            ),
            bool(
                item.get(
                    "phone"
                )
            ),
        ),
        reverse=True
    )

    complete_count = sum(
        bool(clean_text(item.get("website")))
        and bool(clean_text(item.get("phone")))
        and bool(clean_text(item.get("email")))
        for item in verified
    )
    partial_count = len(verified) - complete_count
    print(
        f"\nComplete contacts: {complete_count} | "
        f"Partial contacts retained: {partial_count}"
    )

    print()
    print("=" * 70)
    print("FINAL RESULTS")
    print("=" * 70)

    for index, lead in enumerate(
        verified,
        1
    ):

        print(
            f"{index}. "
            f"{lead['business_name']} | "
            f"{lead['category']} | "
            f"{lead['automation_score']}/100"
        )

    if save_csv(verified):

        print()
        print(
            f"✓ CSV saved: {OUTPUT_FILE}"
        )

        print(
            f"✓ Final leads: {len(verified)}"
        )

        print(
            "\nColumns:"
        )

        print(
            "business_name, category, website, "
            "phone, address, email"
        )


if __name__ == "__main__":
    configure_console()
    main()
