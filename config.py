# ============================================================
# BUSINESS LEAD SCRAPER CONFIG
# ============================================================

OUTPUT_FILE = "data/business_leads.csv"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36"
)

# Network
REQUEST_TIMEOUT = 10
SEARCH_QUERY_TIMEOUT = 15

# Search
SEARCH_RESULTS_PER_QUERY = 10
MAX_SEARCH_WORKERS = 5

# Website enrichment
MAX_WORKERS = 8
MAX_PAGES_PER_SITE = 4
MAX_LINKS_TO_SCAN = 30

# Lead selection
CANDIDATE_MULTIPLIER = 8
MIN_LEAD_SCORE = 45
MAX_LEAD_SCORE = 100

# OpenStreetMap
OSM_TIMEOUT = 15

# How many OSM objects to request per query
OSM_LIMIT = 200