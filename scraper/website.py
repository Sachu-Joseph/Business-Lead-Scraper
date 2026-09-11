import requests

from bs4 import BeautifulSoup

from urllib.parse import (
    urljoin,
    urlparse,
)

from config import (
    USER_AGENT,
    REQUEST_TIMEOUT,
)


HEADERS = {
    "User-Agent": USER_AGENT,
}


# ============================================================
# FETCH PAGE
# ============================================================

def fetch_page(url):

    try:

        response = requests.get(

            url,

            headers=HEADERS,

            timeout=REQUEST_TIMEOUT,

            allow_redirects=True,

        )

        if response.status_code >= 400:

            return None

        content_type = (
            response
            .headers
            .get(
                "Content-Type",
                ""
            )
            .lower()
        )

        if (
            "text/html"
            not in content_type
        ):

            return None

        return {

            "url":
                response.url,

            "html":
                response.text,

            "status":
                response.status_code,

        }

    except requests.RequestException:

        return None


# ============================================================
# DOMAIN
# ============================================================

def get_domain(url):

    try:

        domain = (
            urlparse(url)
            .netloc
            .lower()
        )

        if domain.startswith("www."):
            domain = domain[4:]

        return domain

    except Exception:

        return ""


# ============================================================
# FIND CONTACT PAGE
# ============================================================

def find_contact_page(
    base_url,
    html
):

    soup = BeautifulSoup(
        html,
        "lxml"
    )

    base_domain = get_domain(
        base_url
    )

    keywords = [

        "contact",
        "contact us",

        "get in touch",

        "reach us",

        "enquiry",
        "enquire",

        "location",

        "contact-us",

    ]

    for link in soup.find_all(
        "a",
        href=True
    ):

        text = link.get_text(
            " ",
            strip=True
        ).lower()

        href = link.get(
            "href",
            ""
        ).strip()

        if not href:
            continue

        if href.startswith(
            "#"
        ):
            continue

        full_url = urljoin(
            base_url,
            href
        )

        link_domain = get_domain(
            full_url
        )

        if link_domain != base_domain:
            continue

        combined = (
            f"{text} {href.lower()}"
        )

        if any(
            keyword in combined
            for keyword in keywords
        ):

            return full_url

    return None