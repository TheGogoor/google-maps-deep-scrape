# UK music venues — actual CONFIG block used in the production scrape.
# Copy/paste into scripts/scraper.py to reproduce.

PROJECT_NAME    = "uk_music_venues"
LOCATIONS_FILE  = "locations.json"            # built from a hand-curated CSV of 300 UK cities

# UK biases the API differently than US — use the `region` parameter, not `country`.
# In practice some APIs accept either; this scrape used region=uk and it worked well.
COUNTRY = None
REGION  = "uk"

SEARCH_QUERIES = [
    "live music venue",         # the broadest ICP-targeted query
    "music venue",              # catches venues that don't self-tag as "live"
    "concert hall",             # formal venues (theatres, town halls with shows)
    "comedy club",              # comedy-ticketed venues — fits the music-venue ICP
    "arts centre",              # multi-arts venues (theatre + music)
]

# Pilot keys — small towns + one mid-sized city. Empty grid_dim=2 for safety.
PILOT_KEYS = ["folkestone", "scarborough", "taunton"]

# Grid step at ~50°N (most of the UK)
GRID_STEP_LAT = 0.0225
GRID_STEP_LNG = 0.0362

# Tuning — this scrape predated parallel workers; with 5 workers it's much faster
MAX_WORKERS      = 5
ZOOM             = 13
LIMIT_PER_QUERY  = 20
USE_OFFSET_20    = True
REQ_DELAY        = 0.10
CHECKPOINT_EVERY = 50

OUTPUT_DIR = "."
