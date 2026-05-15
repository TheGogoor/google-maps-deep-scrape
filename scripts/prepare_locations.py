#!/usr/bin/env python3
"""
Generate `locations.json` for scraper.py from one of two sources:

  us-zips   — filter the bundled US zip-code DB by states + min population.
              Each output location is a single point (grid_dim=1).
              Best for verticals with suburban/rural ICP (clinics, gyms, dentists).

  cities    — geocode a CSV of city names + populations into adaptive-grid locations.
              Each city gets a grid_dim based on its population (2×2 to 8×8).
              Best for verticals clustered in urban centers (music venues, comedy clubs).

USAGE:
    python3 prepare_locations.py us-zips \\
        --states "PA,NJ,DE,MD,VA,WV,NC,SC,GA,TN,KY,FL,AL,MS,AR,LA,OH,IN,MI,MO,MN,WI,IA,IL,KS,NE,ND,SD,CO,OK,TX" \\
        --min-pop 5000 \\
        --exclude-city "Chicago" \\
        --output locations.json

    python3 prepare_locations.py cities \\
        --csv-in my-cities.csv \\
        --country uk \\
        --output locations.json

    python3 prepare_locations.py cities --help    # see all options for cities mode

OUTPUT FORMAT (same for both modes):
    {
      "metadata": { "source": "...", "country": "...", "count": 123, ... },
      "locations": [
        { "key": "...", "name": "...", "state": "...",
          "lat": 0.0, "lng": 0.0, "population": 0, "grid_dim": 1 },
        ...
      ]
    }
"""

import argparse
import csv
import json
import os
import sys
import time
import urllib.parse
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: `requests` not installed. Run: pip install requests")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

# Default tier table — feel free to override via --tiers.
# Format: (min_population, grid_dim) — sorted descending by population.
DEFAULT_TIERS = [
    (2_000_000, 8),
    (700_000,   6),
    (300_000,   4),
    (100_000,   3),
    (0,         2),
]

# Path to bundled US zip database (relative to this script's location).
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_US_ZIPS_CSV = SCRIPT_DIR.parent / "data" / "us-zip-codes.csv"

# Country bounding boxes for sanity-checking geocoded coordinates.
# (lat_min, lat_max, lng_min, lng_max)
COUNTRY_BOUNDS = {
    "us": (24.0, 49.5,  -125.0,  -67.0),
    "uk": (49.5, 61.0,    -8.8,    2.0),
    "ca": (41.0, 84.0,  -141.0,  -52.0),
    "au": (-44.0, -10.0,  112.0,  154.0),
    "de": (47.0, 55.5,     5.5,   15.5),
    "fr": (41.0, 51.5,    -5.5,   10.0),
    "es": (35.0, 44.0,   -10.0,    4.5),
    "it": (35.0, 47.5,     6.5,   19.0),
    "nl": (50.5, 53.7,     3.0,    7.5),
    "be": (49.5, 51.7,     2.5,    6.5),
    "ie": (51.0, 55.5,   -10.5,   -6.0),
    "nz": (-47.5, -34.0, 166.0,  179.0),
}

API_KEY = os.environ.get("RAPIDAPI_KEY")
API_HOST = "maps-data.p.rapidapi.com"

WIKI_UA = "GoogleMapsDeepScrape/1.0"

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def grid_dim_for_pop(pop: int, tiers=DEFAULT_TIERS) -> int:
    for min_pop, dim in tiers:
        if pop >= min_pop:
            return dim
    return 1

def parse_tiers(spec: str):
    """Parse a tier spec like '2000000:8,700000:6,300000:4,100000:3,0:2'."""
    out = []
    for chunk in spec.split(","):
        try:
            p, d = chunk.strip().split(":")
            out.append((int(p), int(d)))
        except ValueError:
            print(f"ERROR: bad --tiers chunk '{chunk}'. Expected min_pop:grid_dim.")
            sys.exit(1)
    return sorted(out, key=lambda x: -x[0])

def in_bounds(lat: float, lng: float, country: str) -> bool:
    box = COUNTRY_BOUNDS.get(country.lower())
    if not box:
        return True  # no bounds known → don't reject
    lo_lat, hi_lat, lo_lng, hi_lng = box
    return lo_lat <= lat <= hi_lat and lo_lng <= lng <= hi_lng

def write_output(path: Path, metadata: dict, locations: list):
    out = {"metadata": metadata, "locations": locations}
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\n✓ Wrote {len(locations):,} locations to {path}")

def estimate_api_calls(locations: list, n_queries: int = 5, avg_offset_calls: float = 1.5):
    """Rough estimate: locations × grid_points × queries × avg_offset_calls."""
    points = sum(loc.get("grid_dim", 1) ** 2 for loc in locations)
    return int(points * n_queries * avg_offset_calls)

# ---------------------------------------------------------------------------
# Geocoding (for `cities` mode)
# ---------------------------------------------------------------------------

def geocode_via_api(query: str, country: str = "us") -> tuple | None:
    """Geocode via RapidAPI Maps Data. Returns (lat, lng) or None."""
    if not API_KEY:
        return None
    url = "https://maps-data.p.rapidapi.com/geocoding.php"
    headers = {"x-rapidapi-host": API_HOST, "x-rapidapi-key": API_KEY}
    params = {"query": query, "lang": "en", "country": country}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=30)
        if r.status_code != 200:
            return None
        data = r.json().get("data")
        if not data:
            return None
        first = data[0] if isinstance(data, list) else data
        lat = first.get("latitude") or first.get("lat")
        lng = first.get("longitude") or first.get("lng") or first.get("lon")
        if lat is None or lng is None:
            return None
        return (float(lat), float(lng))
    except (requests.exceptions.RequestException, ValueError, TypeError):
        return None

def geocode_via_wikipedia(title: str) -> tuple | None:
    """Geocode via Wikipedia page summary. Free, no key, very reliable for known cities."""
    url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(title)}"
    try:
        r = requests.get(url, headers={"User-Agent": WIKI_UA}, timeout=15)
        if r.status_code != 200:
            return None
        coords = r.json().get("coordinates")
        if not coords:
            return None
        lat, lng = coords.get("lat"), coords.get("lon")
        if lat is None or lng is None:
            return None
        return (float(lat), float(lng))
    except (requests.exceptions.RequestException, ValueError, TypeError):
        return None

def geocode_with_fallback(name: str, country: str, state: str = "") -> tuple | None:
    """
    Try multiple strategies until one returns a valid (in-bounds) result:
      1. Wikipedia page summary by name (very high quality for cities)
      2. RapidAPI Maps geocoding with country hint
      3. RapidAPI Maps geocoding with name + state/country suffix variations
    Returns (lat, lng, method) or (None, None, 'failed').
    """
    # 1. Wikipedia first — usually fastest, no API quota burned
    for title in ([name] if not state else [f"{name}, {state}", name]):
        result = geocode_via_wikipedia(title)
        if result and in_bounds(*result, country):
            return (*result, "wikipedia")

    # 2. Maps API direct
    result = geocode_via_api(name, country)
    if result and in_bounds(*result, country):
        return (*result, "api")

    # 3. Maps API with query variations
    variants = []
    if state:
        variants.append(f"{name}, {state}")
    variants.extend([
        f"{name}, {country.upper()}",
        f"{name} city",
    ])
    for v in variants:
        result = geocode_via_api(v, country)
        if result and in_bounds(*result, country):
            return (*result, f"api:{v!r}")
        time.sleep(0.4)  # be polite

    return (None, None, "failed")

# ---------------------------------------------------------------------------
# Mode: us-zips
# ---------------------------------------------------------------------------

def cmd_us_zips(args):
    src = Path(args.zip_csv_path)
    if not src.exists():
        print(f"ERROR: US zip CSV not found at {src}")
        print(f"       Download us-zip-codes.csv and place it at:")
        print(f"       {DEFAULT_US_ZIPS_CSV}")
        sys.exit(1)

    target_states = set(s.strip().upper() for s in args.states.split(",")) if args.states else None
    excluded_cities = set(c.strip() for c in args.exclude_city.split(",")) if args.exclude_city else set()

    kept = []
    counts = {"state": 0, "pop": 0, "city": 0}

    print(f"Reading: {src}")
    if target_states:
        print(f"Filter states ({len(target_states)}): {', '.join(sorted(target_states))}")
    print(f"Min population: {args.min_pop:,}")
    if excluded_cities:
        print(f"Excluded cities: {sorted(excluded_cities)}")
    print()

    with open(src) as f:
        for row in csv.DictReader(f):
            state = (row.get("state") or "").upper()
            if target_states and state not in target_states:
                counts["state"] += 1
                continue
            try:
                pop = int(row.get("irs_estimated_population") or 0)
            except ValueError:
                pop = 0
            if pop < args.min_pop:
                counts["pop"] += 1
                continue
            city = row.get("primary_city", "")
            if city in excluded_cities:
                counts["city"] += 1
                continue
            try:
                lat = float(row["latitude"])
                lng = float(row["longitude"])
            except (ValueError, KeyError):
                continue

            kept.append({
                "key":        row["zip"].zfill(5),
                "name":       city,
                "state":      state,
                "lat":        lat,
                "lng":        lng,
                "population": pop,
                "grid_dim":   1,    # zips are single-point by default
            })

    print(f"Kept:    {len(kept):,} zips")
    print(f"Skipped: state={counts['state']:,}  pop={counts['pop']:,}  city={counts['city']:,}")

    # Quick distribution
    if kept:
        from collections import Counter
        by_state = Counter(z["state"] for z in kept)
        print(f"\nTop 10 states by zip count:")
        for s, n in by_state.most_common(10):
            print(f"  {s}: {n:,}")

    # Cost estimate
    est_calls = estimate_api_calls(kept, n_queries=args.n_queries)
    est_hours = est_calls * 0.55 / 3600 / max(args.workers, 1)
    print(f"\nEstimate ({args.n_queries} queries × ~1.5 avg offset calls, {args.workers}-worker parallelism):")
    print(f"  ~{est_calls:,} API calls")
    print(f"  ~{est_hours:.1f} hours runtime")
    print(f"  ~{est_calls/300_000*100:.1f}% of a 300k/month API budget")

    write_output(Path(args.output), {
        "source":    "us-zips",
        "states":    sorted(target_states) if target_states else "all",
        "min_pop":   args.min_pop,
        "excluded_cities": sorted(excluded_cities),
        "count":     len(kept),
    }, kept)

# ---------------------------------------------------------------------------
# Mode: cities (with geocoding)
# ---------------------------------------------------------------------------

def cmd_cities(args):
    src = Path(args.csv_in)
    if not src.exists():
        print(f"ERROR: cities CSV not found at {src}")
        print(f"       Expected columns: name, population (and optionally state).")
        sys.exit(1)

    tiers = parse_tiers(args.tiers) if args.tiers else DEFAULT_TIERS
    print(f"Reading: {src}")
    print(f"Country: {args.country}")
    print(f"Tiers:   {tiers}")
    print(f"Skip-geocode (input already has lat/lng): {args.skip_geocode}")
    print()

    rows = list(csv.DictReader(open(src)))
    if not rows:
        print("ERROR: input CSV has no rows")
        sys.exit(1)
    headers = list(rows[0].keys())
    if "name" not in headers:
        print(f"ERROR: input CSV must have a 'name' column. Found: {headers}")
        sys.exit(1)

    if args.skip_geocode and not ("lat" in headers and ("lng" in headers or "lon" in headers)):
        print("ERROR: --skip-geocode requires lat and lng (or lon) columns in the input.")
        sys.exit(1)

    locations = []
    failed = []
    methods = {"wikipedia": 0, "api": 0, "input": 0, "failed": 0}

    for i, row in enumerate(rows, 1):
        name = row["name"].strip()
        if not name:
            continue
        state = (row.get("state") or "").strip()
        try:
            pop = int(row.get("population") or 0)
        except ValueError:
            pop = 0

        if args.skip_geocode:
            try:
                lat = float(row["lat"])
                lng = float(row.get("lng") or row.get("lon"))
            except (ValueError, KeyError, TypeError):
                failed.append({"name": name, "state": state, "reason": "bad input lat/lng"})
                methods["failed"] += 1
                continue
            method = "input"
        else:
            lat, lng, method = geocode_with_fallback(name, args.country, state)
            if lat is None:
                failed.append({"name": name, "state": state, "reason": "no geocode result"})
                methods["failed"] += 1
                print(f"[{i:>3}] {name:<25} ✗ failed")
                time.sleep(0.3)
                continue
            time.sleep(0.3)

        dim = grid_dim_for_pop(pop, tiers)
        key = name.lower().replace(" ", "-").replace("'", "").replace(",", "")

        locations.append({
            "key":        key,
            "name":       name,
            "state":      state,
            "lat":        round(lat, 4),
            "lng":        round(lng, 4),
            "population": pop,
            "grid_dim":   dim,
        })

        bucket = "wikipedia" if method == "wikipedia" else "api" if method.startswith("api") else "input"
        methods[bucket] += 1

        if i % 25 == 0 or i == len(rows):
            print(f"[{i:>4}/{len(rows)}] {name:<25} {method:<14} {lat:>8.4f} {lng:>9.4f}  dim={dim}")

    # Summary
    print(f"\n✓ Geocoded {len(locations):,} cities")
    print(f"  Methods used: {dict(methods)}")
    from collections import Counter
    dim_dist = Counter(l["grid_dim"] for l in locations)
    print(f"  Grid distribution: {dict(sorted(dim_dist.items(), reverse=True))}")
    total_points = sum(d**2 * n for d, n in dim_dist.items())
    print(f"  Total grid points: {total_points:,}")

    # Cost estimate
    est_calls = estimate_api_calls(locations, n_queries=args.n_queries)
    est_hours = est_calls * 0.55 / 3600 / max(args.workers, 1)
    print(f"\nEstimate ({args.n_queries} queries × ~1.5 avg offset calls, {args.workers}-worker parallelism):")
    print(f"  ~{est_calls:,} API calls")
    print(f"  ~{est_hours:.1f} hours runtime")
    print(f"  ~{est_calls/300_000*100:.1f}% of a 300k/month API budget")

    if failed:
        fail_path = Path(args.output).with_name(Path(args.output).stem + "_failed.csv")
        with open(fail_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["name", "state", "reason"])
            w.writeheader()
            for row in failed:
                w.writerow(row)
        print(f"\n⚠️  {len(failed)} cities failed to geocode → {fail_path}")
        print(f"   Edit that file with manual lat/lng and re-run with --skip-geocode on a fixed CSV.")

    write_output(Path(args.output), {
        "source":    "cities",
        "country":   args.country,
        "tiers":     tiers,
        "count":     len(locations),
        "failed":    len(failed),
    }, locations)

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(
        description="Generate locations.json for scraper.py.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = p.add_subparsers(dest="mode", required=True)

    # us-zips
    z = sub.add_parser("us-zips", help="Filter the bundled US zip database.")
    z.add_argument("--states", default="",
                   help="Comma-separated 2-letter US state codes. Empty = all 50.")
    z.add_argument("--min-pop", type=int, default=5000,
                   help="Minimum population per zip (default 5000).")
    z.add_argument("--exclude-city", default="",
                   help="Comma-separated primary_city names to exclude (e.g. 'Chicago').")
    z.add_argument("--zip-csv-path", default=str(DEFAULT_US_ZIPS_CSV),
                   help=f"Path to us-zip-codes.csv (default: bundled at {DEFAULT_US_ZIPS_CSV})")
    z.add_argument("--output", default="locations.json")
    z.add_argument("--n-queries", type=int, default=5, help="For cost estimate")
    z.add_argument("--workers",   type=int, default=5, help="For cost estimate")
    z.set_defaults(func=cmd_us_zips)

    # cities
    c = sub.add_parser("cities", help="Geocode a CSV of cities + populations into adaptive-grid locations.")
    c.add_argument("--csv-in", required=True,
                   help="Input CSV. Required columns: name, population. Optional: state, lat, lng.")
    c.add_argument("--country", default="us",
                   help="2-letter ISO code (for geocoding bias + bounds check). Default us.")
    c.add_argument("--tiers", default="",
                   help=f"Population tier spec, e.g. '2000000:8,700000:6,300000:4,100000:3,0:2'. Default: {DEFAULT_TIERS}")
    c.add_argument("--skip-geocode", action="store_true",
                   help="If input CSV already has lat/lng, skip geocoding.")
    c.add_argument("--output", default="locations.json")
    c.add_argument("--n-queries", type=int, default=5, help="For cost estimate")
    c.add_argument("--workers",   type=int, default=5, help="For cost estimate")
    c.set_defaults(func=cmd_cities)

    args = p.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
