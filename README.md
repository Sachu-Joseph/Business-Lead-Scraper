## Lead sources

The scraper discovers businesses from OpenStreetMap first:

- OpenStreetMap records (default and free)
- An official website linked from the OSM record, when available

When an OSM record includes an official website, the website is verified first.
The scraper reads the homepage and bounded contact/about/shop pages for
structured business identity, phone, email, address, category, and public
social/contact links. Website identity conflicts and fetch failures are kept as
internal uncertainty reasons instead of silently being treated as clean data.
Businesses without websites remain eligible when their map identity,
category, and location evidence are valid.

For website-backed businesses, verification checks up to four targeted public
pages, prioritizing contact, location, directions, booking, about, shop, and
order pages. Missing phone, email, or address values are filled only when the
official site publishes them; existing trusted values are never overwritten.

Ordinary web search and public Instagram/Facebook discovery are enabled by
default for broader coverage. Search-result titles are never trusted as final
business names; candidates still require strict identity, category, location,
and website verification. To disable this fallback coverage, set:

```powershell
$env:TN_DISABLE_SEARCH_FALLBACK="1"
python main.py
```

Fallback candidates still pass the same category/location checks and website
verification. The scraper does not automate logged-in platform pages, scrape
private content, or bypass platform controls.

## Optional Google Places coverage

Google Maps cannot be scraped reliably through its browser interface. For
additional map coverage, configure the official Google Places API with a key
that has Places API (New) enabled:

```powershell
$env:GOOGLE_MAPS_API_KEY="your-api-key"
uvicorn webapp:app --reload
```

Google Places results are used only for discovery. Businesses with a public
Google phone/address but no website are retained as legitimate partial leads;
businesses with a website still go through official-site verification. Google
Cloud billing and API quotas are controlled by the account that owns the key.

## Optional Instagram enrichment

Existing leads can be enriched with public Business Discovery fields when the
official Meta/Instagram API is configured:

```powershell
$env:IG_USER_ID="your-professional-account-id"
$env:IG_ACCESS_TOKEN="your-meta-access-token"
python main.py
```

Without these variables, social discovery still works and the scraper
continues without API enrichment.

## Result coverage and ordering

The scraper searches all generated queries and enabled sources for the
requested category and locations. It exports every verified business, placing
records with website + phone + email first, followed by legitimate partial
records with missing details. Missing values are never fabricated.

Generic page titles such as `Home Page`, `Welcome to ...`, SEO phrases, and
link-hub domains such as `linktr.ee` are rejected. When a website provides an
explicit address, that address is authoritative for location validation; a
query word in the page title cannot override an address from another city.

## CSV output

The CSV schema remains:

```text
business_name,category,website,phone,address,email
```

Social profile URLs are retained as internal lead metadata and do not change
the six-column CSV contract.

## Local web app

Install the added free local dependencies and start the browser app:

```powershell
pip install -r requirements.txt
uvicorn webapp:app --reload
```

Open `http://127.0.0.1:8000`. The app stores job state in
`data/scraper_jobs.sqlite3`, runs the existing scraper locally, shows progress,
and provides CSV downloads with the same six-column schema.