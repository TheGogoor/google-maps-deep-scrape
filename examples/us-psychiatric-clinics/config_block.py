# US psychiatric clinics — actual CONFIG block used in the production scrape.
# Copy/paste into scripts/scraper.py to reproduce.

PROJECT_NAME    = "us_psychiatric_clinics"
LOCATIONS_FILE  = "locations.json"            # built via: prepare_locations.py us-zips ...

COUNTRY = "us"
REGION  = None

SEARCH_QUERIES = [
    "psychiatrist",                       # broad — captures solo + group practices (~33% of yield)
    "psychiatric clinic",                 # group practices specifically (~20%)
    "behavioral health",                  # broader category, multi-disciplinary (~18%)
    "mental health clinic",               # community + vertical synonym (~10%)
    "psychiatric nurse practitioner",     # PMHNP-led practices — autonomous-state ICP (~18%)
]

# 10 pilot zips spanning 5 states + pop tiers from 5.7k → 60.9k
PILOT_KEYS = [
    "27610",   # Raleigh, NC — 60,930
    "29732",   # Rock Hill, SC — 53,190
    "33904",   # Cape Coral, FL — 25,760
    "46544",   # Mishawaka, IN — 28,070
    "33901",   # Fort Myers, FL — 18,080
    "64116",   # Kansas City, MO — 14,930
    "61603",   # Peoria, IL — 11,870
    "29033",   # Cayce, SC — 9,610
    "63780",   # Scott City, MO — 5,700
    "37407",   # Chattanooga, TN — 7,120
]

# Grid step at ~35°N (US south-central baseline)
GRID_STEP_LAT = 0.0225
GRID_STEP_LNG = 0.0362

# Tuning — production used 5 workers
MAX_WORKERS      = 5
ZOOM             = 13
LIMIT_PER_QUERY  = 20
USE_OFFSET_20    = True
REQ_DELAY        = 0.10
CHECKPOINT_EVERY = 50

OUTPUT_DIR = "."
