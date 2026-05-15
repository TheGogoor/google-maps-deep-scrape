#!/usr/bin/env python3
"""
Google Maps Deep Scrape — template scraper.

Single-file, config-driven, parallel-workers scraper for building large
ICP-targeted business lists from Google Maps via RapidAPI Maps Data.

USAGE:
    export RAPIDAPI_KEY=your_key_here
    python3 -u scraper.py                  # full run with defaults
    python3 -u scraper.py --resume         # resume from last checkpoint
    python3 -u scraper.py --pilot          # small pilot run on PILOT_KEYS (or first 10 locs)
    python3 -u scraper.py --workers 8      # override worker count
    python3 -u scraper.py --dry-run        # print plan, don't call API
    python3 -u scraper.py --reset          # WIPE state files (asks for confirmation)

For long-running scrapes on macOS, wrap in caffeinate to prevent sleep:
    nohup caffeinate -dis python3 -u scraper.py > run.log 2>&1 &
    disown

See SKILL.md and references/ in the parent skill repo for the methodology.
"""

# ╔═══════════════════════════════════════════════════════════════════════════╗
# ║                     PROJECT CONFIG — EDIT THIS BLOCK                      ║
# ║                                                                           ║
# ║   Claude: only edit values in this block. The engine below is the         ║
# ║   bulletproof safety stack — do not modify unless you know exactly        ║
# ║   what you are doing.                                                     ║
# ╚═══════════════════════════════════════════════════════════════════════════╝

PROJECT_NAME    = "my_scrape"            # file prefix: my_scrape_results.csv, my_scrape_progress.json, ...
LOCATIONS_FILE  = "locations.json"       # built by prepare_locations.py

# API region/country bias params. Most verticals only need one.
# Use COUNTRY (ISO 2-letter, e.g. "us", "uk", "ca", "de") — Google Maps respects this.
# REGION is an alternative bias param that some queries respond better to. Leave None unless you find COUNTRY isn't biasing strongly enough.
COUNTRY = "us"
REGION  = None

# 3-6 orthogonal queries. See references/query-design.md for selection guidance.
SEARCH_QUERIES = [
    "your first query",
    "your second query",
]

# Pilot mode: --pilot runs only these location.key values.
# If empty, --pilot uses the first 10 entries in LOCATIONS_FILE.
PILOT_KEYS = []

# Grid spacing — only relevant if any location has grid_dim > 1.
# Defaults give ~2.5 km cells at zoom 13. Adjust LNG_STEP for non-mid-latitudes.
GRID_STEP_LAT = 0.0225                   # ~2.5 km in latitude
GRID_STEP_LNG = 0.0362                   # ~2.5 km in longitude at ~50°N

# Tuning knobs
MAX_WORKERS      = 5                     # parallel HTTP workers (5 is safe, 8-10 push the rate limit)
ZOOM             = 13                    # 11 = metro, 13 = neighbourhood, 15 = street
LIMIT_PER_QUERY  = 20                    # API max per call
USE_OFFSET_20    = True                  # paginate to get 21–40 (breaks the 20-result ceiling)
REQ_DELAY        = 0.10                  # sleep between calls per worker
CHECKPOINT_EVERY = 50                    # save progress every N API calls

OUTPUT_DIR = "."                         # where to write results (defaults to cwd)

# ╔═══════════════════════════════════════════════════════════════════════════╗
# ║                  ENGINE BELOW — DO NOT EDIT                               ║
# ║                                                                           ║
# ║   This is the bulletproof safety stack:                                   ║
# ║     - PID-based single-instance lock                                      ║
# ║     - atomic JSON checkpoint every CHECKPOINT_EVERY calls                 ║
# ║     - append-only seen_ids.txt for dedup                                  ║
# ║     - per-row CSV write + flush() — every result on disk within ~1s       ║
# ║     - SIGINT/SIGTERM handlers — graceful shutdown via Ctrl+C or `kill`    ║
# ║     - 429 retry with exponential backoff [5, 15, 30, 60] seconds          ║
# ║     - ThreadPoolExecutor parallelism with one threading.Lock around state ║
# ║     - human-readable run_status.txt rewritten every checkpoint            ║
# ╚═══════════════════════════════════════════════════════════════════════════╝

import argparse
import csv
import datetime as dt
import json
import math
import os
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: `requests` not installed. Run: pip install requests")
    sys.exit(1)

# ---------------------------------------------------------------------------
# API setup — key from environment variable (never hardcoded)
# ---------------------------------------------------------------------------

API_HOST = "maps-data.p.rapidapi.com"
API_URL  = f"https://{API_HOST}/searchmaps.php"

API_KEY = os.environ.get("RAPIDAPI_KEY")
if not API_KEY:
    print("ERROR: RAPIDAPI_KEY environment variable not set.")
    print("       Get a key at https://rapidapi.com/alexanderxbx/api/maps-data")
    print("       Then: export RAPIDAPI_KEY=your_key_here")
    print("       Or add the line above to your ~/.zshrc / ~/.bash_profile")
    sys.exit(1)

# ---------------------------------------------------------------------------
# File paths — derived from PROJECT_NAME + OUTPUT_DIR
# ---------------------------------------------------------------------------

def get_paths(pilot_mode: bool):
    out = Path(OUTPUT_DIR).resolve()
    out.mkdir(parents=True, exist_ok=True)
    prefix = f"pilot_{PROJECT_NAME}" if pilot_mode else PROJECT_NAME
    return {
        "csv":       out / f"{prefix}_results.csv",
        "progress":  out / f"{prefix}_progress.json",
        "seen_ids":  out / f"{prefix}_seen_ids.txt",
        "lock":      out / f"{prefix}.lock",
        "status":    out / f"{prefix}_run_status.txt",
    }

FIELDNAMES = [
    "name", "website", "full_address", "city",
    "search_key", "search_name", "search_state", "search_query", "grid_point",
    "types", "description",
    "rating", "review_count",
    "is_claimed", "verified",
    "latitude", "longitude",
    "place_link", "business_id",
    "km_from_centroid",
]

# ---------------------------------------------------------------------------
# Lock file (single-instance guard with PID liveness check)
# ---------------------------------------------------------------------------

def is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False

def acquire_lock(lock_path: Path):
    if lock_path.exists():
        try:
            old_pid = int(lock_path.read_text().strip())
        except Exception:
            old_pid = None
        if old_pid and is_pid_alive(old_pid):
            print(f"ERROR: another scraper is already running as PID {old_pid}")
            print(f"       (lock file: {lock_path})")
            print(f"       If you're sure it's dead, delete the lock file and retry.")
            sys.exit(1)
        else:
            print(f"[lock] stale lock (PID {old_pid} dead), overwriting")
    lock_path.write_text(str(os.getpid()))

def release_lock(lock_path: Path):
    try:
        if lock_path.exists():
            lock_path.unlink()
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Thread-safe State (single lock protects all shared-state mutations)
# ---------------------------------------------------------------------------

class State:
    def __init__(self, paths, resume: bool):
        self.paths = paths
        self.lock = threading.Lock()      # the single state lock

        self.completed_tasks = set()      # task_key strings already done
        self.seen_ids = set()             # business_ids already in the CSV
        self.started_at = dt.datetime.now().isoformat(timespec="seconds")
        self.api_calls_made = 0
        self.rows_written = 0
        self.current_position = "initializing"

        self.csv_file = None
        self.csv_writer = None
        self.seen_ids_file = None

        if resume:
            self._load_progress()
            self._load_seen_ids()

    def _load_progress(self):
        p = self.paths["progress"]
        if not p.exists():
            print("[resume] no progress.json found — starting fresh")
            return
        data = json.loads(p.read_text())
        self.completed_tasks = set(data.get("completed_tasks", []))
        self.api_calls_made  = data.get("api_calls_made", 0)
        self.rows_written    = data.get("rows_written", 0)
        self.started_at      = data.get("started_at", self.started_at)
        print(f"[resume] loaded {len(self.completed_tasks):,} completed tasks")
        print(f"[resume] {self.api_calls_made:,} API calls already made, "
              f"{self.rows_written:,} rows already written")

    def _load_seen_ids(self):
        p = self.paths["seen_ids"]
        if not p.exists():
            print("[resume] no seen_ids.txt found")
            return
        with open(p) as f:
            for line in f:
                bid = line.strip()
                if bid:
                    self.seen_ids.add(bid)
        print(f"[resume] loaded {len(self.seen_ids):,} known business_ids")

    def open_writers(self):
        csv_exists = self.paths["csv"].exists() and self.paths["csv"].stat().st_size > 0
        self.csv_file = open(self.paths["csv"], "a", newline="", encoding="utf-8")
        self.csv_writer = csv.DictWriter(self.csv_file, fieldnames=FIELDNAMES)
        if not csv_exists:
            self.csv_writer.writeheader()
            self.csv_file.flush()
        self.seen_ids_file = open(self.paths["seen_ids"], "a")

    def close_writers(self):
        try:
            if self.csv_file:      self.csv_file.close()
            if self.seen_ids_file: self.seen_ids_file.close()
        except Exception:
            pass

    def save_progress(self):
        """MUST be called under self.lock."""
        data = {
            "started_at":       self.started_at,
            "last_update":      dt.datetime.now().isoformat(timespec="seconds"),
            "api_calls_made":   self.api_calls_made,
            "rows_written":     self.rows_written,
            "completed_tasks":  sorted(self.completed_tasks),
            "seen_ids_count":   len(self.seen_ids),
            "current_position": self.current_position,
        }
        p = self.paths["progress"]
        tmp = p.with_suffix(p.suffix + ".tmp")
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.rename(tmp, p)

    def write_status(self, total_calls_est: int, workers: int):
        """MUST be called under self.lock."""
        elapsed = dt.datetime.now() - dt.datetime.fromisoformat(self.started_at)
        elapsed_s = elapsed.total_seconds()
        elapsed_str = str(elapsed).split(".")[0]
        pct = self.api_calls_made / total_calls_est * 100 if total_calls_est else 0
        rate_per_min = self.rows_written / max(elapsed_s / 60, 1)
        call_rate_per_min = self.api_calls_made / max(elapsed_s / 60, 1)
        remaining = max(total_calls_est - self.api_calls_made, 0)
        eta_min = remaining / max(call_rate_per_min, 1)
        eta_str = str(dt.timedelta(minutes=int(eta_min)))

        lines = [
            f"{PROJECT_NAME} scraper — RUNNING (PID {os.getpid()}, {workers} workers)",
            f"Started:    {self.started_at}",
            f"Elapsed:    {elapsed_str}",
            f"Progress:   {self.api_calls_made:,} / ~{total_calls_est:,} API calls ({pct:.1f}%)",
            f"Results:    {self.rows_written:,} unique rows in CSV",
            f"Rate:       {call_rate_per_min:.1f} calls/min  ({rate_per_min:.1f} rows/min)",
            f"Current:    {self.current_position}",
            f"ETA:        {eta_str} remaining (cumulative-rate estimate; recent rate may differ)",
            f"Updated:    {dt.datetime.now().isoformat(timespec='seconds')}",
        ]
        self.paths["status"].write_text("\n".join(lines) + "\n")

# ---------------------------------------------------------------------------
# API call (single function — owns the retry/backoff ladder)
# ---------------------------------------------------------------------------

def search_maps(query: str, lat: float, lng: float, offset: int = 0) -> list:
    """Call the searchmaps endpoint. Returns list of place dicts (possibly empty)."""
    headers = {"x-rapidapi-host": API_HOST, "x-rapidapi-key": API_KEY}
    params = {
        "query":  query,
        "lat":    lat,
        "lng":    lng,
        "zoom":   ZOOM,
        "limit":  LIMIT_PER_QUERY,
        "lang":   "en",
        "offset": offset,
    }
    if COUNTRY: params["country"] = COUNTRY
    if REGION:  params["region"]  = REGION

    backoffs = [5, 15, 30, 60]
    for delay in [0] + backoffs:
        if delay:
            time.sleep(delay)
        try:
            r = requests.get(API_URL, headers=headers, params=params, timeout=30)
            if r.status_code == 429:
                continue   # rate-limited, will retry after next backoff
            r.raise_for_status()
            return r.json().get("data", []) or []
        except requests.exceptions.RequestException:
            continue
    return []   # give up after the backoff ladder

# ---------------------------------------------------------------------------
# Geo helpers
# ---------------------------------------------------------------------------

def haversine_km(lat1, lng1, lat2, lng2):
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl   = math.radians(lng2 - lng1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dl/2)**2
    return 2 * R * math.asin(math.sqrt(a))

def build_grid(center_lat, center_lng, dim):
    """N×N grid centered on (lat, lng). dim=1 returns a single centroid point."""
    if dim <= 1:
        return [(center_lat, center_lng)]
    points = []
    for i in range(dim):
        for j in range(dim):
            lat_off = (i - (dim - 1) / 2) * GRID_STEP_LAT
            lng_off = (j - (dim - 1) / 2) * GRID_STEP_LNG
            points.append((center_lat + lat_off, center_lng + lng_off))
    return points

# ---------------------------------------------------------------------------
# Row builder + task key
# ---------------------------------------------------------------------------

def to_row(p, loc, grid_point, query, point_lat, point_lng):
    desc = p.get("description")
    desc_first = desc[0] if isinstance(desc, list) and desc else (desc if isinstance(desc, str) else "")
    plat, plng = p.get("latitude"), p.get("longitude")
    km = ""
    if plat is not None and plng is not None:
        try:
            km = round(haversine_km(point_lat, point_lng, float(plat), float(plng)), 2)
        except (TypeError, ValueError):
            km = ""
    return {
        "name":             p.get("name") or "",
        "website":          p.get("website") or "",
        "full_address":     p.get("full_address") or "",
        "city":             p.get("city") or "",
        "search_key":       loc["key"],
        "search_name":      loc.get("name", ""),
        "search_state":     loc.get("state", ""),
        "search_query":     query,
        "grid_point":       grid_point,
        "types":            " | ".join(p.get("types") or []),
        "description":      desc_first or "",
        "rating":           p.get("rating") if p.get("rating") is not None else "",
        "review_count":     p.get("review_count") if p.get("review_count") is not None else "",
        "is_claimed":       p.get("is_claimed") if p.get("is_claimed") is not None else "",
        "verified":         p.get("verified") if p.get("verified") is not None else "",
        "latitude":         plat if plat is not None else "",
        "longitude":        plng if plng is not None else "",
        "place_link":       p.get("place_link") or "",
        "business_id":      p.get("business_id") or "",
        "km_from_centroid": km,
    }

def task_key(loc_key, grid_idx, total_grid, query, offset):
    """Unique key for the task: location + grid point + query + offset."""
    return f"{loc_key}|{grid_idx}/{total_grid}|{query}|{offset}"

# ---------------------------------------------------------------------------
# Worker — processes ONE (location, grid_point, query) task pair (offset=0 then maybe offset=20)
# ---------------------------------------------------------------------------

def process_one(loc, grid_idx, grid_total, grid_lat, grid_lng, query, state, total_calls_est):
    grid_point = f"{grid_idx}/{grid_total}"
    key0  = task_key(loc["key"], grid_idx, grid_total, query, 0)
    key20 = task_key(loc["key"], grid_idx, grid_total, query, 20)

    with state.lock:
        done_0  = key0  in state.completed_tasks
        done_20 = key20 in state.completed_tasks

    if done_0 and done_20:
        return

    # offset=0
    if not done_0:
        places = search_maps(query, grid_lat, grid_lng, offset=0)
        time.sleep(REQ_DELAY)
        with state.lock:
            state.api_calls_made += 1
            n_new = _ingest(places, loc, grid_point, query, grid_lat, grid_lng, state)
            state.completed_tasks.add(key0)
            state.current_position = f"{loc['key']} ({loc.get('name','?')},{loc.get('state','')}) g={grid_point} '{query}' off=0"
            print(f"[{state.api_calls_made:>5}] {state.current_position:<70} → raw:{len(places):2d} new:{n_new:2d} (uniq:{state.rows_written})", flush=True)
            # Early-exit: if offset=0 returned <20, no offset=20 to fetch
            if not USE_OFFSET_20 or len(places) < LIMIT_PER_QUERY:
                state.completed_tasks.add(key20)
                _maybe_checkpoint(state, total_calls_est)
                return
            _maybe_checkpoint(state, total_calls_est)

    # offset=20
    if not done_20 and USE_OFFSET_20:
        places = search_maps(query, grid_lat, grid_lng, offset=20)
        time.sleep(REQ_DELAY)
        with state.lock:
            state.api_calls_made += 1
            n_new = _ingest(places, loc, grid_point, query, grid_lat, grid_lng, state)
            state.completed_tasks.add(key20)
            state.current_position = f"{loc['key']} ({loc.get('name','?')},{loc.get('state','')}) g={grid_point} '{query}' off=20"
            print(f"[{state.api_calls_made:>5}] {state.current_position:<70} → raw:{len(places):2d} new:{n_new:2d} (uniq:{state.rows_written})", flush=True)
            _maybe_checkpoint(state, total_calls_est)

def _ingest(places, loc, grid_point, query, plat, plng, state):
    """MUST be called under state.lock. Returns count of new rows written."""
    new = 0
    for p in places:
        bid = p.get("business_id")
        if not bid: continue
        if p.get("is_permanently_closed") or p.get("is_temporarily_closed"): continue
        if bid in state.seen_ids: continue
        state.csv_writer.writerow(to_row(p, loc, grid_point, query, plat, plng))
        state.csv_file.flush()                       # per-row CSV flush
        state.seen_ids.add(bid)
        state.seen_ids_file.write(bid + "\n")
        state.seen_ids_file.flush()
        state.rows_written += 1
        new += 1
    return new

def _maybe_checkpoint(state, total_calls_est):
    """MUST be called under state.lock."""
    if state.api_calls_made % CHECKPOINT_EVERY == 0:
        state.save_progress()
        state.write_status(total_calls_est, getattr(state, "_workers", MAX_WORKERS))

# ---------------------------------------------------------------------------
# Plan + run
# ---------------------------------------------------------------------------

def load_locations(pilot_mode: bool):
    """Read LOCATIONS_FILE, return list of dicts with at least {key, name, lat, lng}."""
    path = Path(LOCATIONS_FILE)
    if not path.is_absolute():
        path = Path(OUTPUT_DIR).resolve() / path
    if not path.exists():
        print(f"ERROR: locations file not found at {path}")
        print(f"       Build one with scripts/prepare_locations.py first.")
        sys.exit(1)

    data = json.loads(path.read_text())
    locs = data.get("locations", data) if isinstance(data, dict) else data
    # Validate required fields
    for loc in locs:
        for field in ("key", "lat", "lng"):
            if field not in loc:
                print(f"ERROR: location missing required field '{field}': {loc}")
                sys.exit(1)
        loc.setdefault("name", loc["key"])
        loc.setdefault("grid_dim", 1)

    if pilot_mode:
        if PILOT_KEYS:
            keys = set(PILOT_KEYS)
            locs = [l for l in locs if l["key"] in keys]
            print(f"[pilot] using {len(locs)} pilot locations from PILOT_KEYS")
        else:
            locs = locs[:10]
            print(f"[pilot] PILOT_KEYS empty — using first {len(locs)} locations")

    return locs

def compute_tasks(locations):
    """Flatten locations × grid_points × queries into a task list."""
    tasks = []
    for loc in locations:
        grid = build_grid(loc["lat"], loc["lng"], loc.get("grid_dim", 1))
        for idx, (glat, glng) in enumerate(grid, 1):
            for query in SEARCH_QUERIES:
                tasks.append((loc, idx, len(grid), glat, glng, query))
    return tasks

def run(args):
    paths = get_paths(args.pilot)

    if args.reset:
        print("⚠️  --reset will DELETE the following files:")
        for k in ("csv", "progress", "seen_ids", "status", "lock"):
            if paths[k].exists():
                print(f"    {paths[k]}")
        if input("Type 'RESET' to confirm: ").strip() != "RESET":
            print("Aborted.")
            return
        for k in ("csv", "progress", "seen_ids", "status", "lock"):
            try: paths[k].unlink()
            except FileNotFoundError: pass
        print("[reset] state wiped.")
        return

    locs = load_locations(args.pilot)
    tasks = compute_tasks(locs)
    total_pairs = len(tasks)
    total_calls_max = total_pairs * (2 if USE_OFFSET_20 else 1)
    grid_total = sum(l.get("grid_dim", 1)**2 for l in locs)

    print(f"Plan: {len(locs):,} locations × {grid_total:,} grid points × {len(SEARCH_QUERIES)} queries")
    print(f"      = {total_pairs:,} task pairs → up to {total_calls_max:,} API calls")
    print(f"      Workers: {args.workers}")

    if args.dry_run:
        print("[dry-run] exiting without making any API calls.")
        return

    acquire_lock(paths["lock"])
    state = State(paths, resume=args.resume)
    state._workers = args.workers
    state.open_writers()

    def on_signal(sig, _frame):
        print(f"\n[signal {sig}] flushing state and exiting cleanly...", flush=True)
        with state.lock:
            state.save_progress()
            state.write_status(total_calls_max, args.workers)
        state.close_writers()
        release_lock(paths["lock"])
        os._exit(0)
    signal.signal(signal.SIGINT,  on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    try:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futures = [
                ex.submit(process_one, loc, idx, grid_n, glat, glng, q, state, total_calls_max)
                for (loc, idx, grid_n, glat, glng, q) in tasks
            ]
            for fut in as_completed(futures):
                exc = fut.exception()
                if exc is not None:
                    print(f"[worker error] {exc}", flush=True)

        # Final flush
        with state.lock:
            state.save_progress()
            state.write_status(total_calls_max, args.workers)

        print()
        print("=" * 80)
        print(f"{PROJECT_NAME} SCRAPE COMPLETE")
        print(f"  API calls made:  {state.api_calls_made:,}")
        print(f"  Unique results:  {state.rows_written:,}")
        print(f"  Output CSV:      {paths['csv']}")
        print(f"  Progress file:   {paths['progress']}")
        print(f"  Seen IDs file:   {paths['seen_ids']}")
        print("=" * 80)

    finally:
        state.close_writers()
        release_lock(paths["lock"])

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(
        description=f"Google Maps deep scrape — project: {PROJECT_NAME}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--resume",  action="store_true", help="resume from last checkpoint")
    p.add_argument("--pilot",   action="store_true", help="run a small pilot (PILOT_KEYS or first 10 locs)")
    p.add_argument("--dry-run", action="store_true", help="print the plan, don't call the API")
    p.add_argument("--reset",   action="store_true", help="WIPE state files (asks for confirmation)")
    p.add_argument("--workers", type=int, default=MAX_WORKERS,
                   help=f"parallel HTTP workers (default {MAX_WORKERS})")
    args = p.parse_args()
    run(args)

if __name__ == "__main__":
    main()
