import re
from urllib.parse import urlparse


# ============================================================
# QUALITY FILTERS
# ============================================================

FAKE_PHONES = {
    "0000000000",
    "1111111111",
    "1234567890",
    "9876543210",
    "9999999999",
    "8888888888",
    "7777777777",
    "6666666666",
    "9000000000",
    "9123456789",
}

FREE_EMAIL_DOMAINS = {
    "gmail.com",
    "googlemail.com",
    "yahoo.com",
    "yahoo.co.in",
    "yahoo.in",
    "hotmail.com",
    "outlook.com",
    "live.com",
    "rediffmail.com",
    "protonmail.com",
    "icloud.com",
}

BAD_URL_PATH_SEGMENTS = (
    "/blog/",
    "/news/",
    "/article/",
    "/articles/",
    "/pages/",
    "/wiki/",
    "/directory/",
    "/listing/",
    "/listings/",
    "/top-10",
    "/top-20",
    "/best-",
    "/near-me",
    "/review",
    "/reviews/",
    "/compare/",
    "/ranking/",
    "/guide/",
    "/how-to",
    "/category/",
    "/tag/",
    "/author/",
    "/jobs/",
    "/career/",
    "/vacancy/",
)


def normalize_phone_digits(phone):
    return re.sub(r"\D", "", str(phone or ""))


def is_fake_phone(phone):
    digits = normalize_phone_digits(phone)

    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]

    if len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]

    if len(digits) != 10:
        return True

    if digits in FAKE_PHONES:
        return True

    # Repeated digits (e.g. 9888888888)
    if len(set(digits)) <= 2:
        return True

    return False


def email_domain(email):
    email = str(email or "").strip().lower()

    if "@" not in email:
        return ""

    return email.rsplit("@", 1)[-1]


def is_free_email(email):
    domain = email_domain(email)

    return domain in FREE_EMAIL_DOMAINS


def email_domain_matches_website(email, website):
    email_dom = email_domain(email)
    site_dom = get_domain(website)

    if not email_dom or not site_dom:
        return False

    return (
        email_dom == site_dom
        or email_dom.endswith("." + site_dom)
        or site_dom.endswith("." + email_dom)
    )


def is_bad_url_path(url):
    try:
        path = urlparse(str(url or "")).path.lower()
    except Exception:
        return False

    if not path or path == "/":
        return False

    return any(segment in path for segment in BAD_URL_PATH_SEGMENTS)


def contact_quality_score(data, website=""):
    """
    Score how trustworthy extracted contact details are (0-100).
    Used during verification to reject weak leads.
    """

    score = 0

    phone = data.get("phone", "")
    email = data.get("email", "")
    whatsapp = data.get("whatsapp", "")
    address = data.get("address", "")

    confidence = data.get("confidence", {}) or {}

    if phone and not is_fake_phone(phone):
        score += 25

        if confidence.get("phone", 0) >= 90:
            score += 10

    if whatsapp and not is_fake_phone(whatsapp):
        score += 20

    if email:
        score += 10

        if email_domain_matches_website(email, website):
            score += 15
        elif not is_free_email(email):
            score += 8

        if confidence.get("email", 0) >= 90:
            score += 5

    if address and len(str(address)) >= 15:
        score += 10

        if confidence.get("address", 0) >= 85:
            score += 5

    if confidence.get("business_name", 0) >= 85:
        score += 5

    return min(score, 100)


# ============================================================
# NORMALIZE URL
# ============================================================

def normalize_url(url):

    if not url:
        return ""

    url = str(url).strip()

    if not url:
        return ""

    if not url.startswith(
        ("http://", "https://")
    ):
        url = "https://" + url

    try:

        parsed = urlparse(url)

        if not parsed.netloc:
            return ""

        return (
            f"{parsed.scheme}://"
            f"{parsed.netloc}"
        ).rstrip("/")

    except Exception:

        return ""


# ============================================================
# GET DOMAIN
# ============================================================

def get_domain(url):

    if not url:
        return ""

    try:

        parsed = urlparse(url)

        domain = parsed.netloc.lower()

        if domain.startswith("www."):
            domain = domain[4:]

        return domain

    except Exception:

        return ""


# ============================================================
# SOCIAL MEDIA
# ============================================================

def is_social_media(url):

    domain = get_domain(url)

    social_domains = {

        "facebook.com",
        "instagram.com",
        "youtube.com",
        "linkedin.com",
        "twitter.com",
        "x.com",
        "tiktok.com",

    }

    return any(
        domain == item
        or domain.endswith("." + item)
        for item in social_domains
    )


# ============================================================
# DIRECTORY
# ============================================================

def is_directory(url):

    domain = get_domain(url)

    directory_domains = {

        "justdial.com",
        "sulekha.com",
        "yelp.com",
        "tripadvisor.com",
        "tripadvisor.in",
        "indiamart.com",
        "nearbuy.com",
        "magicpin.in",
        "urbanpro.com",
        "practo.com",
        "fitpass.co.in",
        "cybo.com",
        "yappe.in",
        "bdir.in",
        "findby.in",
        "infoisinfo.co.in",
        "starofservice.in",
        "idbf.in",
        "3bestincity.com",
        "threebestrated.in",
        "5bestincity.com",
        "restaurant-guru.in",
        "infobel.com",
        "houzz.in",
        "houzz.com",
        "zomato.com",
        "swiggy.com",
        "wikipedia.org",
        "booking.com",
        "makemytrip.com",
        "goibibo.com",
        "agoda.com",
        "trivago.com",
        "naukri.com",
        "indeed.com",
        "olx.in",
        "quikr.com",
        "indiaonline.in",
        "indiaonline.co.in",
        "foursquare.com",
        "showmelocal.com",
        "hotfrog.in",
        "cylex.in",
        "locanto.in",
        "click.in",
        "asklaila.com",
        "tradeindia.com",
        "yellowpages.co.in",
        "holidify.com",
        "tripoto.com",
        "lbb.in",
        "eazydiner.com",
        "dineout.co.in",
        "zomato.in",
        "restaurantguru.com",
        "cntraveller.in",
        "treebo.com",
        "goibibo.com",
        "makemytrip.com",

    }

    return any(
        domain == item
        or domain.endswith("." + item)
        for item in directory_domains
    )


# ============================================================
# CHECK WEBSITE
# ============================================================

def is_valid_business_website(url):

    if not url:
        return False

    if is_social_media(url):
        return False

    if is_directory(url):
        return False

    return True
