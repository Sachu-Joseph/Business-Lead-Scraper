
"""
High-accuracy business detail extractor
for TN-LEAD-SCRAPER.

Compatible with the current main.py.

IMPORTANT:
The current main.py passes a WEBSITE URL to extract_contacts().
This extractor therefore supports BOTH:

    extract_contacts(html, url="...")
    extract_contacts(url, candidate_url="...", timeout=7, user_agent="...")

It will automatically download a URL when a URL is supplied.

Main goals:
- Business name
- Phone
- Email
- WhatsApp
- Address
- Category
- Website text
- Page title / description
- JSON-LD / Schema.org
- Ecommerce detection
- WhatsApp detection
- Internal contact/shop pages
- Indian phone normalization
- Good extraction without overly aggressive rejection
"""

import json
import re
import threading
from urllib.parse import (
    urljoin,
    urlparse,
    unquote,
    parse_qs,
)

import requests
from bs4 import BeautifulSoup


# ============================================================
# REGEX
# ============================================================

EMAIL_REGEX = re.compile(
    r"(?<![A-Z0-9._%+\-])"
    r"[A-Z0-9._%+\-]+"
    r"@"
    r"[A-Z0-9.\-]+\.[A-Z]{2,}"
    r"(?![A-Z0-9._%+\-])",
    re.IGNORECASE,
)

INDIAN_MOBILE_REGEX = re.compile(
    r"(?<!\d)"
    r"(?:\+?91[\s.\-]?)?"
    r"(?:0[\s.\-]?)?"
    r"[6-9]\d{4}[\s.\-]?\d{5}"
    r"(?!\d)"
)

INDIAN_LANDLINE_REGEX = re.compile(
    r"(?<!\d)"
    r"(?:\+?91[\s.\-]?)?"
    r"(?:0[\s.\-]?)?"
    r"(?:\(?\d{2,5}\)?[\s.\-]?)"
    r"\d{5,8}"
    r"(?!\d)"
)

POSTCODE_REGEX = re.compile(
    r"(?<!\d)[1-9][0-9]{5}(?!\d)"
)


# ============================================================
# CONSTANTS
# ============================================================

CONTACT_PAGE_WORDS = (
    "contact",
    "contact-us",
    "contactus",
    "get-in-touch",
    "reach-us",
    "reachus",
)

ABOUT_PAGE_WORDS = (
    "about",
    "about-us",
    "aboutus",
    "who-we-are",
)

COMMERCE_PAGE_WORDS = (
    "shop",
    "store",
    "products",
    "product",
    "catalog",
    "catalogue",
    "collection",
    "collections",
)

ORDER_PAGE_WORDS = (
    "order",
    "order-now",
    "buy",
    "buy-now",
    "checkout",
)

BOOKING_PAGE_WORDS = (
    "booking",
    "book",
    "appointment",
    "schedule",
    "reserve",
)

LOCATION_PAGE_WORDS = (
    "location",
    "locations",
    "find-us",
    "findus",
    "directions",
    "visit",
    "where-we-are",
)

WHATSAPP_DOMAINS = (
    "wa.me",
    "api.whatsapp.com",
    "web.whatsapp.com",
    "whatsapp.com",
)

SOCIAL_DOMAINS = (
    "facebook.com",
    "instagram.com",
    "linkedin.com",
    "youtube.com",
    "twitter.com",
    "x.com",
    "pinterest.com",
    "threads.net",
    "t.me",
)

BAD_EMAIL_PARTS = (
    "noreply",
    "no-reply",
    "donotreply",
    "do-not-reply",
    "example.com",
    "sentry",
    "wixpress",
    "wordpress",
    "wordpress.org",
    "squarespace",
    "shopify",
    "godaddy",
    "cloudflare",
)

GENERIC_EMAIL_PREFIXES = (
    "admin",
    "administrator",
    "webmaster",
    "hostmaster",
    "postmaster",
    "mailer-daemon",
    "technical",
    "tech",
    "developer",
    "dev",
)

BUSINESS_SCHEMA_TYPES = {
    "organization",
    "localbusiness",
    "store",
    "shop",
    "restaurant",
    "cafeorcoffeeshop",
    "bakery",
    "beautysalon",
    "hairdresser",
    "healthandbeautybusiness",
    "medicalbusiness",
    "dentist",
    "physician",
    "pharmacy",
    "automotivebusiness",
    "autorepair",
    "autobodyshop",
    "realestateagent",
    "professionalservice",
    "financialservice",
    "educationalorganization",
    "school",
    "collegeoruniversity",
    "sportsactivitylocation",
    "gym",
    "sportsclub",
    "clothingstore",
    "electronicsstore",
    "furniturestore",
    "jewelry",
    "jewelrystore",
    "petstore",
    "supermarket",
    "groceryStore",
    "homeandconstructionbusiness",
    "generalcontractor",
}


# ============================================================
# BASIC HELPERS
# ============================================================

def clean_text(value):
    if value is None:
        return ""

    value = str(value)
    value = value.replace("\xa0", " ")
    value = value.replace("\u200b", "")
    value = value.replace("\ufeff", "")
    value = re.sub(r"\s+", " ", value)

    return value.strip()


def clean_address(value):
    """Keep human-readable address text and discard structured-data debris."""
    value = clean_text(value)
    if not value:
        return ""

    value = re.sub(
        r"\{[^{}]*(?:@type|streetAddress|addressLocality|postalCode)"
        r"[^{}]*\}",
        " ",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"['\"]?@type['\"]?\s*:\s*['\"][^'\"]+['\"]", " ", value)
    value = re.sub(r"\s*,\s*,+", ", ", value)
    value = re.sub(r"\s+", " ", value).strip(" ,")

    if not value or value.lower() in {"in", "india", "country: in"}:
        return ""

    return value


def normalize_digits(value):
    return re.sub(r"\D", "", str(value or ""))


def normalize_name(value):
    value = clean_text(value).lower()

    value = re.sub(
        r"[^a-z0-9\u0B80-\u0BFF& ]+",
        " ",
        value,
    )

    return clean_text(value)


def get_domain(url):
    try:
        parsed = urlparse(str(url))
        domain = parsed.netloc.lower().strip()

        if "@" in domain:
            domain = domain.split("@", 1)[-1]

        if domain.startswith("www."):
            domain = domain[4:]

        return domain

    except Exception:
        return ""


def same_domain(url1, url2):
    d1 = get_domain(url1)
    d2 = get_domain(url2)

    return bool(d1 and d2 and d1 == d2)


def ensure_html(value):
    """
    Only allow actual HTML into BeautifulSoup.

    URLs are deliberately rejected here.
    """

    if value is None:
        return ""

    if not isinstance(value, str):
        return ""

    value = value.strip()

    if not value:
        return ""

    lowered = value.lower()

    if lowered.startswith(
        (
            "http://",
            "https://",
            "www.",
        )
    ):
        return ""

    return value


def make_soup(html):
    html = ensure_html(html)

    if not html:
        return None

    try:
        return BeautifulSoup(
            html,
            "html.parser",
        )

    except Exception:
        return None


# ============================================================
# URL FETCHING
# ============================================================

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0 Safari/537.36"
)
_session_local = threading.local()


def get_http_session():
    session = getattr(_session_local, "session", None)
    if session is None:
        session = requests.Session()
        _session_local.session = session
    return session


def is_url(value):
    value = str(value or "").strip().lower()

    return value.startswith(
        (
            "http://",
            "https://",
        )
    )


def normalize_url(url):
    url = clean_text(url)

    if not url:
        return ""

    if not url.startswith(
        (
            "http://",
            "https://",
        )
    ):
        url = "https://" + url

    return url


def fetch_html(
    url,
    timeout=8,
    user_agent=None,
):
    """
    Download website HTML.

    Returns:
        html, final_url, status_code
    """

    url = normalize_url(url)

    if not url:
        return "", "", 0

    headers = {
        "User-Agent": (
            user_agent
            or DEFAULT_USER_AGENT
        ),
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,*/*;q=0.8"
        ),
        "Accept-Language": (
            "en-US,en;q=0.8"
        ),
        "Connection": "keep-alive",
    }

    try:
        response = get_http_session().get(
            url,
            headers=headers,
            timeout=timeout,
            allow_redirects=True,
        )

        status = response.status_code
        final_url = response.url or url

        content_type = (
            response.headers.get(
                "Content-Type",
                "",
            )
            .lower()
        )

        if status >= 400:
            return "", final_url, status

        # Avoid trying to parse PDFs/images/etc.
        if content_type and not any(
            item in content_type
            for item in (
                "text/html",
                "application/xhtml+xml",
            )
        ):
            return "", final_url, status

        html = response.text

        if not html or len(html) < 50:
            return "", final_url, status

        return html, final_url, status

    except requests.RequestException:
        return "", url, 0

    except Exception:
        return "", url, 0


# ============================================================
# PHONE
# ============================================================

def normalize_indian_phone(value):
    """
    Normalize an Indian mobile number.

    Examples:
        +91 98765 43210 -> 9876543210
        09876543210     -> 9876543210
        98765-43210     -> 9876543210
    """

    digits = normalize_digits(value)

    if not digits:
        return ""

    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]

    if digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]

    if len(digits) != 10:
        return ""

    if digits[0] not in "6789":
        return ""

    return digits


def extract_phone_candidates(text):
    if not text:
        return []

    found = []

    for match in INDIAN_MOBILE_REGEX.finditer(
        str(text)
    ):
        phone = normalize_indian_phone(
            match.group(0)
        )

        if phone and phone not in found:
            found.append(phone)

    return found


def extract_landline_candidates(text):
    if not text:
        return []

    found = []

    for match in INDIAN_LANDLINE_REGEX.finditer(
        str(text)
    ):
        raw = match.group(0)
        digits = normalize_digits(raw)

        if normalize_indian_phone(raw):
            continue

        if len(digits) < 8 or len(digits) > 13:
            continue

        if digits not in found:
            found.append(digits)

    return found


def phone_quality(phone):
    phone = normalize_indian_phone(phone)

    if not phone:
        return -100

    score = 50

    if phone[0] in "6789":
        score += 30

    return score


# ============================================================
# EMAIL
# ============================================================

def is_bad_email(email):
    email = clean_text(email).lower()

    if not email or "@" not in email:
        return True

    for part in BAD_EMAIL_PARTS:
        if part in email:
            return True

    local = email.split("@", 1)[0]

    if local in {
        "example",
        "test",
        "testing",
        "sample",
    }:
        return True

    return False


def email_quality(email):
    email = clean_text(email).lower()

    if not email or is_bad_email(email):
        return -100

    score = 50

    local, _, domain = email.partition("@")

    if local in {
        "info",
        "hello",
        "contact",
        "sales",
        "enquiry",
        "inquiry",
        "office",
        "booking",
        "orders",
        "order",
        "support",
    }:
        score += 20

    if local.startswith(
        GENERIC_EMAIL_PREFIXES
    ):
        score -= 15

    if any(
        word in local
        for word in (
            "owner",
            "manager",
            "founder",
            "business",
        )
    ):
        score += 10

    if domain.endswith(
        (
            ".in",
            ".co.in",
            ".com",
            ".net",
            ".org",
        )
    ):
        score += 5

    return score


def extract_email_candidates(text):
    if not text:
        return []

    emails = set()

    for match in EMAIL_REGEX.findall(
        str(text)
    ):
        email = clean_text(match).lower()

        if not is_bad_email(email):
            emails.add(email)

    return sorted(
        emails,
        key=lambda x: (
            -email_quality(x),
            x,
        ),
    )


# ============================================================
# WHATSAPP
# ============================================================

def extract_phone_from_whatsapp_url(href):
    href = unquote(
        str(href or "")
    )

    if not href:
        return ""

    match = re.search(
        r"wa\.me/(\d{10,15})",
        href,
        re.IGNORECASE,
    )

    if match:
        digits = match.group(1)

        if digits.startswith("91"):
            return normalize_indian_phone(
                digits[-10:]
            )

        return normalize_indian_phone(
            digits
        )

    try:
        parsed = urlparse(href)
        query = parse_qs(parsed.query)

        for key in (
            "phone",
            "number",
            "mobile",
            "tel",
        ):
            for value in query.get(
                key,
                [],
            ):
                phone = normalize_indian_phone(
                    value
                )

                if phone:
                    return phone

    except Exception:
        pass

    phones = extract_phone_candidates(
        href
    )

    return phones[0] if phones else ""


def extract_whatsapp_numbers(
    soup,
    text="",
):
    numbers = []

    if soup:
        for anchor in soup.find_all(
            "a",
            href=True,
        ):
            href = str(
                anchor.get(
                    "href",
                    "",
                )
            )

            lowered = href.lower()

            if not any(
                domain in lowered
                for domain in WHATSAPP_DOMAINS
            ):
                continue

            phone = extract_phone_from_whatsapp_url(
                href
            )

            if phone and phone not in numbers:
                numbers.append(phone)

    if text:
        for match in re.finditer(
            r"whatsapp",
            text,
            re.IGNORECASE,
        ):
            start = max(
                0,
                match.start() - 150,
            )

            end = min(
                len(text),
                match.end() + 250,
            )

            nearby = text[
                start:end
            ]

            for phone in extract_phone_candidates(
                nearby
            ):
                if phone not in numbers:
                    numbers.append(phone)

    return numbers


def extract_whatsapp(
    soup,
    text="",
):
    numbers = extract_whatsapp_numbers(
        soup,
        text,
    )

    return numbers[0] if numbers else ""


# ============================================================
# JSON-LD
# ============================================================

def _flatten_jsonld(value):
    results = []

    if isinstance(value, list):
        for item in value:
            results.extend(
                _flatten_jsonld(item)
            )

    elif isinstance(value, dict):
        results.append(value)

        for key in (
            "@graph",
            "mainEntity",
            "mainEntityOfPage",
            "itemListElement",
            "about",
            "subjectOf",
            "publisher",
            "author",
            "provider",
            "brand",
            "location",
        ):
            nested = value.get(key)

            if nested:
                results.extend(
                    _flatten_jsonld(nested)
                )

    return results


def extract_jsonld_data(soup):
    if not soup:
        return []

    records = []

    scripts = soup.find_all(
        "script",
        attrs={
            "type": re.compile(
                r"application/ld\+json",
                re.IGNORECASE,
            )
        },
    )

    for script in scripts:
        raw = (
            script.string
            or script.get_text()
        )

        if not raw:
            continue

        raw = raw.strip()

        raw = re.sub(
            r"^\s*<!--",
            "",
            raw,
        )

        raw = re.sub(
            r"-->\s*$",
            "",
            raw,
        )

        try:
            parsed = json.loads(raw)

        except Exception:
            continue

        for item in _flatten_jsonld(
            parsed
        ):
            if isinstance(item, dict):
                records.append(item)

    unique = []
    seen = set()

    for item in records:
        try:
            key = json.dumps(
                item,
                sort_keys=True,
                ensure_ascii=False,
            )

        except Exception:
            key = repr(item)

        if key not in seen:
            seen.add(key)
            unique.append(item)

    return unique


def get_schema_types(item):
    raw = item.get(
        "@type",
        "",
    )

    values = (
        raw
        if isinstance(raw, list)
        else [raw]
    )

    types = []

    for value in values:
        value = clean_text(
            value
        ).lower()

        if "/" in value:
            value = value.rsplit(
                "/",
                1,
            )[-1]

        if value:
            types.append(value)

    return types


def is_business_schema(item):
    if not isinstance(item, dict):
        return False

    types = get_schema_types(item)

    for schema_type in types:
        compact = schema_type.replace(
            " ",
            "",
        )

        if (
            compact in BUSINESS_SCHEMA_TYPES
            or "localbusiness" in compact
            or "organization" in compact
            or "store" in compact
            or "restaurant" in compact
            or "salon" in compact
            or "clinic" in compact
            or "medical" in compact
            or "gym" in compact
        ):
            return True

    return any(
        item.get(field)
        for field in (
            "telephone",
            "email",
            "address",
            "openingHours",
            "openingHoursSpecification",
        )
    )


def business_schema_records(
    jsonld
):
    return [
        item
        for item in jsonld or []
        if is_business_schema(item)
    ]


# ============================================================
# JSON-LD FIELD EXTRACTION
# ============================================================

def jsonld_business_name(jsonld):
    candidates = []

    for item in business_schema_records(
        jsonld
    ):
        name = item.get("name")

        if isinstance(name, dict):
            name = name.get("name")

        name = clean_text(name)

        if len(name) >= 2:
            candidates.append(name)

    return choose_best_business_name(
        candidates
    )


def jsonld_phones(jsonld):
    phones = []

    for item in business_schema_records(
        jsonld
    ):
        telephone = item.get(
            "telephone"
        )

        if not telephone:
            continue

        for phone in extract_phone_candidates(
            str(telephone)
        ):
            if phone not in phones:
                phones.append(phone)

    return phones


def jsonld_emails(jsonld):
    emails = []

    for item in business_schema_records(
        jsonld
    ):
        email = item.get("email")

        values = (
            email
            if isinstance(email, list)
            else [email]
        )

        for value in values:
            for found in extract_email_candidates(
                str(value or "")
            ):
                if found not in emails:
                    emails.append(found)

    return emails


def jsonld_address(item):
    if not isinstance(item, dict):
        return ""

    address = item.get(
        "address"
    )

    if isinstance(address, str):
        value = clean_address(address)

        if len(value) >= 8:
            return value

    if not isinstance(address, dict):
        return ""

    parts = []

    for key in (
        "streetAddress",
        "addressLocality",
        "addressRegion",
        "postalCode",
        "addressCountry",
    ):
        value = clean_text(
            address.get(key)
        )

        if value and value not in parts:
            parts.append(value)

    return clean_address(", ".join(parts))


def jsonld_address_candidates(
    jsonld
):
    addresses = []

    for item in business_schema_records(
        jsonld
    ):
        address = jsonld_address(item)

        if (
            address
            and address not in addresses
        ):
            addresses.append(address)

    return addresses


def jsonld_category(jsonld):
    categories = []

    for item in business_schema_records(
        jsonld
    ):
        for key in (
            "category",
            "additionalType",
            "description",
        ):
            value = item.get(key)

            if isinstance(value, str):
                value = clean_text(value)

                if (
                    value
                    and value not in categories
                ):
                    categories.append(value)

    return categories


# ============================================================
# BUSINESS NAME
# ============================================================

def clean_business_name(value):
    value = clean_text(value)

    if not value:
        return ""

    value = re.sub(
        r"\s*[|\-–—:]\s*"
        r"(home|official website|official site|"
        r"contact us|contact|about us)\s*$",
        "",
        value,
        flags=re.IGNORECASE,
    )

    value = re.sub(
        r"\s*[|•]+\s*",
        " ",
        value,
    )

    value = clean_text(value)

    if len(value) > 150:
        value = value[:150].strip()

    return value


def looks_like_bad_business_name(
    value
):
    value = clean_text(value)

    if not value:
        return True

    lowered = value.lower()

    bad = (
        "homepage",
        "welcome",
        "contact us",
        "about us",
        "privacy policy",
        "terms and conditions",
        "terms of service",
        "cookie policy",
        "page not found",
        "404",
        "login",
        "sign in",
        "register",
        "search results",
    )

    if lowered in bad:
        return True

    if len(value) < 2:
        return True

    if len(value) > 150:
        return True

    return False


def choose_best_business_name(
    candidates
):
    cleaned = []

    for candidate in candidates:
        candidate = clean_business_name(
            candidate
        )

        if not candidate:
            continue

        if looks_like_bad_business_name(
            candidate
        ):
            continue

        if candidate not in cleaned:
            cleaned.append(candidate)

    if not cleaned:
        return ""

    def score(name):
        value = name.lower()
        result = 0

        words = name.split()

        if 1 <= len(words) <= 8:
            result += 10

        if any(
            word in value
            for word in (
                "studio",
                "store",
                "shop",
                "salon",
                "fitness",
                "gym",
                "restaurant",
                "cafe",
                "bakery",
                "clinic",
                "dental",
                "boutique",
                "fashion",
                "furniture",
                "jewellery",
                "jewelry",
                "academy",
                "school",
                "services",
                "traders",
            )
        ):
            result += 10

        if any(
            phrase in value
            for phrase in (
                "official website",
                "privacy",
                "contact us",
                "about us",
                "home page",
            )
        ):
            result -= 30

        if len(name) > 100:
            result -= 20

        return result

    return max(
        cleaned,
        key=score,
    )


def extract_business_name(
    soup,
    jsonld=None,
):
    if not soup:
        return ""

    jsonld = (
        jsonld
        if jsonld is not None
        else extract_jsonld_data(soup)
    )

    candidates = []

    # 1. Schema.org
    schema_name = jsonld_business_name(
        jsonld
    )

    if schema_name:
        candidates.append(schema_name)

    # 2. OpenGraph site name
    meta = soup.find(
        "meta",
        attrs={
            "property": "og:site_name"
        },
    )

    if meta:
        value = clean_text(
            meta.get("content")
        )

        if value:
            candidates.append(value)

    # 3. Application name
    meta = soup.find(
        "meta",
        attrs={
            "name": "application-name"
        },
    )

    if meta:
        value = clean_text(
            meta.get("content")
        )

        if value:
            candidates.append(value)

    # 4. H1
    h1 = soup.find("h1")

    if h1:
        candidates.append(
            h1.get_text(
                " ",
                strip=True,
            )
        )

    # 5. Logo alt
    for image in soup.find_all(
        "img",
        alt=True,
    ):
        alt = clean_text(
            image.get("alt")
        )

        if not alt:
            continue

        if any(
            word in alt.lower()
            for word in (
                "logo",
                "brand",
            )
        ):
            alt = re.sub(
                r"\s*(logo|brand)\s*",
                " ",
                alt,
                flags=re.IGNORECASE,
            )

            candidates.append(
                clean_text(alt)
            )

    # 6. Title
    if soup.title:
        candidates.append(
            soup.title.get_text(
                " ",
                strip=True,
            )
        )

    return choose_best_business_name(
        candidates
    )


# ============================================================
# CATEGORY
# ============================================================

def extract_category(
    soup,
    jsonld=None,
):
    if not soup:
        return ""

    jsonld = (
        jsonld
        if jsonld is not None
        else extract_jsonld_data(soup)
    )

    categories = jsonld_category(
        jsonld
    )

    if categories:
        return categories[0]

    for selector in (
        "meta[name='category']",
        "meta[property='business:category']",
        "meta[name='keywords']",
    ):
        element = soup.select_one(
            selector
        )

        if element:
            value = clean_text(
                element.get("content")
            )

            if value:
                return value

    breadcrumb = soup.select_one(
        "[itemtype*='BreadcrumbList']"
    )

    if breadcrumb:
        value = clean_text(
            breadcrumb.get_text(
                " ",
                strip=True,
            )
        )

        if 3 <= len(value) <= 200:
            return value

    return ""


# ============================================================
# ADDRESS
# ============================================================

def extract_address_from_html(
    soup
):
    if not soup:
        return ""

    selectors = (
        "address",
        "[itemprop='address']",
        "[itemtype*='PostalAddress']",
        ".address",
        ".contact-address",
        ".contact_address",
        ".contactAddress",
        ".store-address",
        ".shop-address",
        ".business-address",
        ".location-address",
        "#address",
        "#contact-address",
    )

    candidates = []

    for selector in selectors:
        try:
            elements = soup.select(
                selector
            )

        except Exception:
            continue

        for element in elements:
            value = clean_text(
                element.get_text(
                    " ",
                    strip=True,
                )
            )

            if len(value) >= 10:
                candidates.append(value)

    parts = []

    for prop in (
        "streetAddress",
        "addressLocality",
        "addressRegion",
        "postalCode",
        "addressCountry",
    ):
        element = soup.find(
            attrs={
                "itemprop": prop
            }
        )

        if element:
            value = clean_text(
                element.get("content")
                or element.get_text(
                    " ",
                    strip=True,
                )
            )

            if value:
                parts.append(value)

    if parts:
        candidates.append(
            ", ".join(
                dict.fromkeys(parts)
            )
        )

    if not candidates:
        return ""

    with_postcode = [
        value
        for value in candidates
        if POSTCODE_REGEX.search(value)
    ]

    if with_postcode:
        return max(
            with_postcode,
            key=len,
        )

    return max(
        candidates,
        key=len,
    )


def extract_business_address(
    soup,
    jsonld=None,
):
    if not soup:
        return ""

    jsonld = (
        jsonld
        if jsonld is not None
        else extract_jsonld_data(soup)
    )

    candidates = jsonld_address_candidates(
        jsonld
    )

    html_address = extract_address_from_html(
        soup
    )

    if html_address:
        candidates.append(
            html_address
        )

    if not candidates:
        return ""

    for candidate in candidates:
        if POSTCODE_REGEX.search(candidate):
            return clean_address(candidate)

    return clean_address(
        max(
            candidates,
            key=len,
        )
    )


# ============================================================
# SOCIAL LINKS
# ============================================================

def extract_social_links(soup):
    result = {}

    if not soup:
        return result

    for anchor in soup.find_all(
        "a",
        href=True,
    ):
        href = str(
            anchor.get(
                "href",
                "",
            )
        ).strip()

        lowered = href.lower()

        for domain in SOCIAL_DOMAINS:
            if domain in lowered:
                key = domain.split(".")[0]

                if key == "x":
                    key = "twitter"

                if key not in result:
                    result[key] = href

                break

    return result


# ============================================================
# INTERNAL LINKS
# ============================================================

def find_internal_links(
    soup,
    base_url,
    max_links=10,
):
    if not soup or not base_url:
        return []

    candidates = []

    for anchor in soup.find_all(
        "a",
        href=True,
    ):
        href = str(
            anchor.get(
                "href",
                "",
            )
        ).strip()

        if not href:
            continue

        if href.startswith(
            (
                "#",
                "mailto:",
                "tel:",
                "javascript:",
            )
        ):
            continue

        absolute = urljoin(
            base_url,
            href,
        )

        if not absolute.startswith(
            (
                "http://",
                "https://",
            )
        ):
            continue

        if not same_domain(
            absolute,
            base_url,
        ):
            continue

        text = clean_text(
            anchor.get_text(
                " ",
                strip=True,
            )
        ).lower()

        path = urlparse(
            absolute
        ).path.lower()

        combined = (
            f"{text} {path}"
        )

        score = 0

        if any(
            word in combined
            for word in CONTACT_PAGE_WORDS
        ):
            score += 100

        if any(
            word in combined
            for word in ABOUT_PAGE_WORDS
        ):
            score += 80

        if any(
            word in combined
            for word in BOOKING_PAGE_WORDS
        ):
            score += 75

        if any(
            word in combined
            for word in ORDER_PAGE_WORDS
        ):
            score += 70

        if any(
            word in combined
            for word in COMMERCE_PAGE_WORDS
        ):
            score += 60

        if "whatsapp" in combined:
            score += 110

        if score > 0:
            candidates.append(
                (
                    score,
                    absolute,
                )
            )

    unique = {}

    for score, link in candidates:
        normalized = link.rstrip("/")

        if (
            normalized not in unique
            or score > unique[normalized][0]
        ):
            unique[normalized] = (
                score,
                link,
            )

    ordered = sorted(
        unique.values(),
        key=lambda x: (
            -x[0],
            x[1],
        ),
    )

    return [
        url
        for _, url in ordered[:max_links]
    ]


def find_useful_pages(
    base_url,
    soup,
):
    return find_internal_links(
        soup,
        base_url,
        max_links=10,
    )


# ============================================================
# SIGNAL DETECTION
# ============================================================

def detect_signals(
    soup,
    text="",
):
    text = clean_text(
        text
    ).lower()

    signals = {
        "whatsapp": False,
        "whatsapp_order": False,
        "whatsapp_catalog": False,
        "online_order": False,
        "booking": False,
        "appointment": False,
        "enquiry": False,
        "catalog": False,
        "products": False,
        "delivery": False,
        "offers": False,
        "payment": False,
        "contact_form": False,
        "lead_form": False,
    }

    if soup:
        whatsapp_numbers = extract_whatsapp_numbers(
            soup,
            text,
        )

        signals["whatsapp"] = bool(
            whatsapp_numbers
        )

        # Forms
        for form in soup.find_all("form"):
            form_text = clean_text(
                form.get_text(
                    " ",
                    strip=True,
                )
            ).lower()

            if any(
                word in form_text
                for word in (
                    "contact",
                    "enquiry",
                    "inquiry",
                    "get in touch",
                )
            ):
                signals["contact_form"] = True

            if any(
                word in form_text
                for word in (
                    "lead",
                    "demo",
                    "callback",
                    "request",
                )
            ):
                signals["lead_form"] = True

    signals["whatsapp_order"] = (
        "whatsapp" in text
        and any(
            term in text
            for term in (
                "order on whatsapp",
                "order via whatsapp",
                "whatsapp order",
                "whatsapp us to order",
                "order through whatsapp",
                "order using whatsapp",
            )
        )
    )

    signals["whatsapp_catalog"] = (
        "whatsapp" in text
        and any(
            term in text
            for term in (
                "whatsapp catalog",
                "whatsapp catalogue",
                "catalog on whatsapp",
                "catalogue on whatsapp",
            )
        )
    )

    signals["online_order"] = any(
        term in text
        for term in (
            "order online",
            "buy now",
            "add to cart",
            "shopping cart",
            "checkout",
            "place order",
            "shop now",
            "buy online",
        )
    )

    signals["booking"] = any(
        term in text
        for term in (
            "book now",
            "booking",
            "reserve",
            "reservation",
        )
    )

    signals["appointment"] = any(
        term in text
        for term in (
            "appointment",
            "book appointment",
            "schedule appointment",
        )
    )

    signals["enquiry"] = any(
        term in text
        for term in (
            "enquiry",
            "inquiry",
            "enquire now",
            "send enquiry",
            "get in touch",
            "request a quote",
            "contact us",
        )
    )

    signals["catalog"] = any(
        term in text
        for term in (
            "catalog",
            "catalogue",
        )
    )

    signals["products"] = any(
        term in text
        for term in (
            "products",
            "our products",
            "shop products",
            "product details",
        )
    )

    signals["delivery"] = any(
        term in text
        for term in (
            "delivery",
            "home delivery",
            "door delivery",
            "order tracking",
            "track order",
            "shipping",
        )
    )

    signals["offers"] = any(
        term in text
        for term in (
            "offer",
            "offers",
            "discount",
            "promotion",
            "promotions",
            "sale",
        )
    )

    signals["payment"] = any(
        term in text
        for term in (
            "payment",
            "upi",
            "razorpay",
            "pay now",
            "online payment",
            "cash on delivery",
            "cod",
        )
    )

    return signals


# ============================================================
# CONTACT EXTRACTION
# ============================================================

def extract_phones(
    soup,
    text="",
):
    phones = []

    if soup:
        # tel: links are strongest
        for anchor in soup.find_all(
            "a",
            href=True,
        ):
            href = str(
                anchor.get(
                    "href",
                    "",
                )
            )

            if href.lower().startswith(
                "tel:"
            ):
                raw = unquote(
                    href[4:]
                )

                phone = normalize_indian_phone(
                    raw
                )

                if (
                    phone
                    and phone not in phones
                ):
                    phones.append(phone)

    for phone in extract_phone_candidates(
        text
    ):
        if phone not in phones:
            phones.append(phone)

    return phones


def extract_emails(
    soup,
    text="",
):
    emails = []

    if soup:
        for anchor in soup.find_all(
            "a",
            href=True,
        ):
            href = str(
                anchor.get(
                    "href",
                    "",
                )
            )

            if href.lower().startswith(
                "mailto:"
            ):
                email = unquote(
                    href[7:]
                ).split(
                    "?",
                    1,
                )[0]

                for found in extract_email_candidates(
                    email
                ):
                    if found not in emails:
                        emails.append(found)

    for email in extract_email_candidates(
        text
    ):
        if email not in emails:
            emails.append(email)

    return emails


def choose_best_phone(
    phones,
    whatsapp_numbers=None,
):
    whatsapp_numbers = (
        whatsapp_numbers
        or []
    )

    normalized = []

    for phone in phones or []:
        value = normalize_indian_phone(
            phone
        )

        if (
            value
            and value not in normalized
        ):
            normalized.append(value)

    if not normalized:
        return ""

    # WhatsApp number gets highest priority.
    for phone in whatsapp_numbers:
        if phone in normalized:
            return phone

    return max(
        normalized,
        key=phone_quality,
    )


def choose_best_email(
    emails,
    business_name="",
    website="",
):
    if not emails:
        return ""

    business_tokens = set(
        re.findall(
            r"[a-z0-9]+",
            normalize_name(
                business_name
            ),
        )
    )

    domain = get_domain(
        website
    )

    def score(email):
        value = email.lower()

        result = email_quality(
            value
        )

        local, _, email_domain = (
            value.partition("@")
        )

        if (
            domain
            and email_domain == domain
        ):
            result += 35

        for token in business_tokens:
            if (
                len(token) >= 4
                and token in local
            ):
                result += 8

        return result

    return max(
        emails,
        key=score,
    )


# ============================================================
# PAGE PARSER
# ============================================================

def _parse_page(
    html,
    url="",
):
    """
    Parse one HTML page.

    This function never downloads anything.
    """

    soup = make_soup(html)

    if not soup:
        return {
            "business_name": "",
            "category": "",
            "phone": "",
            "phones": [],
            "email": "",
            "emails": [],
            "whatsapp": "",
            "whatsapp_numbers": [],
            "address": "",
            "signals": {},
            "social_links": {},
            "internal_links": [],
            "confidence": {},
            "_title": "",
            "_description": "",
            "_website_text": "",
        }

    # Parse JSON-LD BEFORE removing scripts.
    jsonld = extract_jsonld_data(
        soup
    )

    title = ""

    if soup.title:
        title = clean_text(
            soup.title.get_text(
                " ",
                strip=True,
            )
        )

    description = ""

    meta_description = soup.find(
        "meta",
        attrs={
            "name": re.compile(
                r"^description$",
                re.IGNORECASE,
            )
        },
    )

    if meta_description:
        description = clean_text(
            meta_description.get(
                "content"
            )
        )

    # Extract text before removing scripts.
    text_soup = soup

    for element in text_soup.find_all(
        (
            "script",
            "style",
            "noscript",
            "svg",
            "template",
        )
    ):
        element.decompose()

    text = clean_text(
        text_soup.get_text(
            " ",
            strip=True,
        )
    )

    # Core fields
    business_name = extract_business_name(
        text_soup,
        jsonld,
    )

    category = extract_category(
        text_soup,
        jsonld,
    )

    address = extract_business_address(
        text_soup,
        jsonld,
    )

    jsonld_phones_found = jsonld_phones(
        jsonld
    )

    jsonld_emails_found = jsonld_emails(
        jsonld
    )

    phones = extract_phones(
        text_soup,
        text,
    )

    emails = extract_emails(
        text_soup,
        text,
    )

    for phone in jsonld_phones_found:
        if phone not in phones:
            phones.append(phone)

    for email in jsonld_emails_found:
        if email not in emails:
            emails.append(email)

    whatsapp_numbers = extract_whatsapp_numbers(
        text_soup,
        text,
    )

    phone = choose_best_phone(
        phones,
        whatsapp_numbers,
    )

    email = choose_best_email(
        emails,
        business_name,
        url,
    )

    whatsapp = (
        whatsapp_numbers[0]
        if whatsapp_numbers
        else ""
    )

    signals = detect_signals(
        text_soup,
        text,
    )

    if whatsapp:
        signals["whatsapp"] = True

    internal_links = find_internal_links(
        text_soup,
        url,
        max_links=10,
    )

    social_links = extract_social_links(
        text_soup
    )

    # Ecommerce fallback category detection.
    lower_text = (
        f"{title} "
        f"{description} "
        f"{text}"
    ).lower()

    ecommerce_terms = (
        "online store",
        "online shop",
        "shop online",
        "ecommerce",
        "e-commerce",
        "buy now",
        "add to cart",
        "checkout",
        "order online",
        "shopping cart",
        "shop now",
        "product catalog",
    )

    ecommerce_hits = sum(
        1
        for term in ecommerce_terms
        if term in lower_text
    )

    if (
        not category
        and ecommerce_hits >= 1
    ):
        category = "Online Store"

    # Confidence
    confidence = {
        "business_name": 0,
        "category": 0,
        "phone": 0,
        "email": 0,
        "whatsapp": 0,
        "address": 0,
    }

    if business_name:
        confidence["business_name"] = 85

        if jsonld_business_name(
            jsonld
        ):
            confidence["business_name"] = 98

    if category:
        confidence["category"] = 70

        if jsonld_category(
            jsonld
        ):
            confidence["category"] = 92

    if phone:
        confidence["phone"] = 75

        if phone in jsonld_phones_found:
            confidence["phone"] = 92

        if text_soup.find(
            "a",
            href=re.compile(
                r"^tel:",
                re.IGNORECASE,
            ),
        ):
            confidence["phone"] = max(
                confidence["phone"],
                97,
            )

    if email:
        confidence["email"] = 70

        if email in jsonld_emails_found:
            confidence["email"] = 90

        if text_soup.find(
            "a",
            href=re.compile(
                r"^mailto:",
                re.IGNORECASE,
            ),
        ):
            confidence["email"] = max(
                confidence["email"],
                97,
            )

    if whatsapp:
        confidence["whatsapp"] = 98

    if address:
        confidence["address"] = 75

        if jsonld_address_candidates(
            jsonld
        ):
            confidence["address"] = 95

        if POSTCODE_REGEX.search(address):
            confidence["address"] = max(
                confidence["address"],
                90,
            )

    return {
        "business_name": business_name,
        "category": category,
        "website": url or "",
        "phone": phone,
        "phones": phones,
        "email": email,
        "emails": emails,
        "whatsapp": whatsapp,
        "whatsapp_numbers": whatsapp_numbers,
        "address": address,
        "social_links": social_links,
        "signals": signals,
        "internal_links": internal_links,
        "confidence": confidence,

        # IMPORTANT:
        # main.py uses these fields for category/location.
        "_title": title,
        "_description": description,
        "_website_text": text,
        "description": description,
    }


# ============================================================
# MERGING
# ============================================================

def merge_contacts(
    primary,
    secondary,
):
    primary = dict(
        primary or {}
    )

    secondary = dict(
        secondary or {}
    )

    merged = dict(primary)

    # Lists
    for key in (
        "phones",
        "emails",
        "whatsapp_numbers",
        "internal_links",
    ):
        values = []

        for source in (
            primary,
            secondary,
        ):
            for value in (
                source.get(key) or []
            ):
                if value not in values:
                    values.append(value)

        merged[key] = values

    # Social
    social = {}

    social.update(
        primary.get(
            "social_links",
            {},
        )
        or {}
    )

    social.update(
        secondary.get(
            "social_links",
            {},
        )
        or {}
    )

    merged["social_links"] = social

    # Signals
    signals = {}

    for source in (
        primary,
        secondary,
    ):
        for key, value in (
            source.get(
                "signals",
                {},
            )
            or {}
        ).items():

            signals[key] = bool(
                signals.get(
                    key,
                    False,
                )
                or value
            )

    merged["signals"] = signals

    # Confidence
    confidence = {}

    for source in (
        primary,
        secondary,
    ):
        for key, value in (
            source.get(
                "confidence",
                {},
            )
            or {}
        ).items():

            confidence[key] = max(
                confidence.get(
                    key,
                    0,
                ),
                value,
            )

    merged["confidence"] = confidence

    # Scalar fields
    for key in (
        "business_name",
        "category",
        "website",
        "phone",
        "email",
        "whatsapp",
        "address",
        "_title",
        "_description",
        "_website_text",
        "description",
    ):
        if not merged.get(key):
            merged[key] = secondary.get(
                key,
                "",
            )

    merged["phone"] = choose_best_phone(
        merged.get(
            "phones",
            [],
        ),
        merged.get(
            "whatsapp_numbers",
            [],
        ),
    )

    merged["email"] = choose_best_email(
        merged.get(
            "emails",
            [],
        ),
        merged.get(
            "business_name",
            "",
        ),
        merged.get(
            "website",
            "",
        ),
    )

    return merged


# ============================================================
# MAIN EXTRACTION
# ============================================================

def extract_contacts(
    html_or_url,
    url="",
    candidate_url="",
    timeout=8,
    user_agent=None,
):
    """
    Main extraction function.

    Supports BOTH:

        extract_contacts(html, url="https://site.com")

    and:

        extract_contacts(
            "https://site.com",
            candidate_url="https://site.com",
            timeout=7,
            user_agent="..."
        )

    This compatibility is required by the current main.py.
    """

    # --------------------------------------------------------
    # Determine whether input is HTML or URL
    # --------------------------------------------------------

    supplied = str(
        html_or_url or ""
    ).strip()

    target_url = (
        candidate_url
        or url
        or ""
    )

    html = ""

    status_code = 0

    final_url = target_url

    if is_url(supplied):

        target_url = normalize_url(
            supplied
        )

        html, final_url, status_code = fetch_html(
            target_url,
            timeout=timeout,
            user_agent=user_agent,
        )

    else:

        html = supplied

        if target_url:
            target_url = normalize_url(
                target_url
            )

    # --------------------------------------------------------
    # Empty result
    # --------------------------------------------------------

    empty = {
        "business_name": "",
        "category": "",
        "website": (
            final_url
            or target_url
        ),
        "phone": "",
        "phones": [],
        "email": "",
        "emails": [],
        "whatsapp": "",
        "whatsapp_numbers": [],
        "address": "",
        "social_links": {},
        "signals": {},
        "internal_links": [],
        "confidence": {},

        "_title": "",
        "_description": "",
        "_website_text": "",
        "description": "",

        "status_code": status_code,
        "final_url": final_url or target_url,
    }

    if not html:
        return empty

    # --------------------------------------------------------
    # Parse homepage
    # --------------------------------------------------------

    result = _parse_page(
        html,
        final_url or target_url,
    )

    result["status_code"] = status_code
    result["final_url"] = (
        final_url
        or target_url
    )

    # --------------------------------------------------------
    # IMPORTANT:
    # If homepage doesn't contain contact details,
    # inspect one useful internal page.
    #
    # This fixes many websites where:
    # homepage = products
    # contact page = phone/address/email
    # --------------------------------------------------------

    # A homepage frequently shows only a phone or location. Continue to the
    # contact page until we have a usable outreach channel.
    missing_contact = not result.get("phone") or not (
        result.get("email") or result.get("whatsapp")
    )

    missing_identity = not result.get(
        "business_name"
    )

    if missing_contact or missing_identity:

        internal_pages = result.get(
            "internal_links",
            [],
        )

        # Contact pages first.
        priority_pages = []

        for page in internal_pages:

            page_lower = page.lower()

            if any(
                word in page_lower
                for word in CONTACT_PAGE_WORDS
            ):
                priority_pages.append(page)

        # Then pages that commonly contain a second address or phone.
        for page in internal_pages:

            if page in priority_pages:
                continue

            page_lower = page.lower()

            if any(
                word in page_lower
                for word in (
                    *ABOUT_PAGE_WORDS,
                    *LOCATION_PAGE_WORDS,
                    *BOOKING_PAGE_WORDS,
                    *COMMERCE_PAGE_WORDS,
                    *ORDER_PAGE_WORDS,
                )
            ):
                priority_pages.append(page)

        # A small bounded crawl improves coverage without turning a lead
        # verification into an unbounded site scrape.
        priority_pages = list(dict.fromkeys(priority_pages))[:4]

        for page_url in priority_pages:

            page_html, page_final_url, _ = fetch_html(
                page_url,
                timeout=timeout,
                user_agent=user_agent,
            )

            if not page_html:
                continue

            page_result = _parse_page(
                page_html,
                page_final_url or page_url,
            )

            result = merge_contacts(
                result,
                page_result,
            )

            # Stop once all useful public contact fields are available.
            if (
                result.get("phone")
                and (result.get("email") or result.get("whatsapp"))
                and result.get("address")
            ):
                break

    # --------------------------------------------------------
    # Ensure website remains homepage/domain
    # --------------------------------------------------------

    result["website"] = (
        target_url
        or final_url
        or result.get(
            "website",
            "",
        )
    )

    result["final_url"] = (
        final_url
        or target_url
    )

    # --------------------------------------------------------
    # Final WhatsApp signal
    # --------------------------------------------------------

    if result.get("whatsapp"):
        result.setdefault(
            "signals",
            {},
        )["whatsapp"] = True

    # --------------------------------------------------------
    # Ensure useful combined website text
    # --------------------------------------------------------

    combined_text = " ".join(
        [
            result.get(
                "_title",
                "",
            ),
            result.get(
                "_description",
                "",
            ),
            result.get(
                "_website_text",
                "",
            ),
            result.get(
                "category",
                "",
            ),
            result.get(
                "address",
                "",
            ),
        ]
    )

    result["_website_text"] = clean_text(
        combined_text
    )

    # --------------------------------------------------------
    # Ecommerce fallback
    # --------------------------------------------------------

    ecommerce_words = (
        "online store",
        "online shop",
        "shop online",
        "ecommerce",
        "e-commerce",
        "buy now",
        "add to cart",
        "checkout",
        "order online",
        "shopping cart",
        "shop now",
        "product catalog",
    )

    ecommerce_hits = sum(
        1
        for word in ecommerce_words
        if word in result[
            "_website_text"
        ].lower()
    )

    if (
        not result.get("category")
        and ecommerce_hits >= 1
    ):
        result["category"] = "Online Store"

    # Strong ecommerce signals
    if ecommerce_hits >= 1:
        result.setdefault(
            "signals",
            {},
        )["products"] = True

    if ecommerce_hits >= 2:
        result.setdefault(
            "signals",
            {},
        )["online_order"] = True

    return result


# ============================================================
# COMPATIBILITY HELPERS
# ============================================================

def get_phone(
    html,
    url="",
):
    return extract_contacts(
        html,
        url=url,
    ).get(
        "phone",
        "",
    )


def get_email(
    html,
    url="",
):
    return extract_contacts(
        html,
        url=url,
    ).get(
        "email",
        "",
    )


def get_whatsapp(
    html,
    url="",
):
    return extract_contacts(
        html,
        url=url,
    ).get(
        "whatsapp",
        "",
    )


def get_address(
    html,
    url="",
):
    return extract_contacts(
        html,
        url=url,
    ).get(
        "address",
        "",
    )


def get_business_name(
    html,
    url="",
):
    return extract_contacts(
        html,
        url=url,
    ).get(
        "business_name",
        "",
    )


def extract_business_details(
    html,
    url="",
):
    return extract_contacts(
        html,
        url=url,
    )


# ============================================================
# EXTRACTION QUALITY
# ============================================================

def extraction_quality(result):
    if not result:
        return 0

    score = 0

    if result.get("business_name"):
        score += 25

    if result.get("phone"):
        score += 20

    if result.get("email"):
        score += 15

    if result.get("whatsapp"):
        score += 20

    if result.get("address"):
        score += 15

    if result.get("category"):
        score += 5

    return min(
        score,
        100,
    )


# ============================================================
# EXPORTS
# ============================================================

__all__ = [
    "clean_text",
    "normalize_digits",
    "normalize_indian_phone",
    "extract_phone_candidates",
    "extract_landline_candidates",
    "extract_email_candidates",
    "extract_jsonld_data",
    "extract_business_name",
    "extract_category",
    "extract_business_address",
    "extract_phones",
    "extract_emails",
    "extract_whatsapp",
    "extract_whatsapp_numbers",
    "find_useful_pages",
    "find_internal_links",
    "extract_social_links",
    "detect_signals",
    "extract_contacts",
    "extract_business_details",
    "merge_contacts",
    "get_phone",
    "get_email",
    "get_whatsapp",
    "get_address",
    "get_business_name",
    "extraction_quality",
]
