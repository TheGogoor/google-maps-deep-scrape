#!/usr/bin/env python3
"""
Analyze a pilot run's output CSV.

Computes the metrics you need to decide whether to launch the full run:
  1. Headline yield (total rows, unique fraction)
  2. Per-query yield (which queries are pulling weight, which are dead)
  3. Per-location yield (does yield scale with location size as expected?)
  4. Spatial spread (km_from_centroid distribution — tells you if single-point
     coverage is enough or if you need a grid)
  5. Data quality (% with website, claimed, addressed)
  6. Top types (sanity check — are the results ICP-shaped?)

Then prints automated recommendations like:
  - "Drop query X — only 4% unique contribution"
  - "Single point per location is sufficient (p90 km_from_centroid = 8.2)"
  - "Spatial spread is wide — consider state-level post-filter on full address"

USAGE:
    python3 pilot_analyze.py                                # auto-find latest pilot CSV in cwd
    python3 pilot_analyze.py --csv pilot_foo_results.csv    # explicit path
    python3 pilot_analyze.py --csv path/to/pilot.csv --queries 5

The --queries flag tells the analyzer how many queries you expected (used in
the per-query contribution thresholds). Defaults to whatever's in the CSV.
"""

import argparse
import csv
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

# ---------------------------------------------------------------------------
# Recommendation thresholds — tweak if you want stricter/looser advice
# ---------------------------------------------------------------------------

DEAD_QUERY_THRESHOLD     = 0.05   # below 5% unique contribution → dead weight
WEAK_QUERY_THRESHOLD     = 0.10   # below 10% → marginal, flag for review
WIDE_SPREAD_KM           = 50     # p90 above this → spatial spread is concerning
EXTREME_SPREAD_KM        = 500    # p90 above this → API is returning cross-country noise
LOW_WEBSITE_PCT          = 0.50   # below 50% with website → quality concern
LOW_CLAIMED_PCT          = 0.40   # below 40% claimed → many defunct/abandoned

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def autodiscover_pilot_csv():
    """Look for pilot_*_results.csv in cwd. Return the most recently modified one."""
    candidates = sorted(Path(".").glob("pilot_*_results.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None

def percentile(sorted_values, p):
    if not sorted_values:
        return None
    idx = int(len(sorted_values) * p / 100)
    return sorted_values[min(idx, len(sorted_values) - 1)]

def normalize_columns(rows):
    """Back-compat: earlier ad-hoc scrapers used different column names
    (`search_zip` / `search_city` instead of the unified `search_key` / `search_name`).
    Fill `search_key` from `search_zip` and `search_name` from `search_city` if missing."""
    for r in rows:
        if not r.get("search_key"):
            r["search_key"] = r.get("search_zip") or r.get("search_city") or ""
        if not r.get("search_name"):
            r["search_name"] = r.get("search_city") or r.get("search_name") or ""

def bar(n, max_n, width=30):
    if max_n == 0:
        return ""
    return "█" * int(n / max_n * width)

# ---------------------------------------------------------------------------
# Analysis sections
# ---------------------------------------------------------------------------

def headline(rows):
    print("=" * 80)
    print(f"PILOT ANALYSIS  —  {len(rows):,} unique rows")
    print("=" * 80)

def per_query(rows):
    print()
    print("=" * 80)
    print("PER-QUERY YIELD  (first-seen contribution)")
    print("=" * 80)
    counts = Counter(r.get("search_query", "") for r in rows)
    total = sum(counts.values())
    if not total:
        print("(no search_query data)")
        return None

    queries_in_data = len(counts)
    print(f"  {'Query':<40} {'Unique':>8} {'Share':>8}")
    print("  " + "-" * 60)
    flagged_dead = []
    flagged_weak = []
    for q, n in counts.most_common():
        share = n / total
        marker = ""
        if share < DEAD_QUERY_THRESHOLD:
            marker = "✗ dead"
            flagged_dead.append(q)
        elif share < WEAK_QUERY_THRESHOLD:
            marker = "⚠ weak"
            flagged_weak.append(q)
        print(f"  {q:<40} {n:>8,} {share*100:>7.1f}%  {marker}")

    return {
        "queries_in_data": queries_in_data,
        "flagged_dead":    flagged_dead,
        "flagged_weak":    flagged_weak,
    }

def per_location(rows):
    print()
    print("=" * 80)
    print("PER-LOCATION YIELD")
    print("=" * 80)
    by_loc = defaultdict(int)
    pops = {}
    for r in rows:
        key = r.get("search_key", "")
        by_loc[key] += 1
        # Population isn't in the CSV directly — we don't have it.
        # Just sort by yield.

    if not by_loc:
        print("(no search_key data)")
        return None

    sorted_locs = sorted(by_loc.items(), key=lambda x: -x[1])
    max_n = sorted_locs[0][1]
    print(f"  {'Key':<15} {'Name + State':<30} {'Yield':>6}  Distribution")
    name_by_key = {}
    state_by_key = {}
    for r in rows:
        k = r.get("search_key", "")
        if k not in name_by_key:
            name_by_key[k]  = r.get("search_name", "")
            state_by_key[k] = r.get("search_state", "")

    for key, n in sorted_locs[:20]:
        label = f"{name_by_key.get(key, ''):<24} {state_by_key.get(key, '')}"
        print(f"  {key:<15} {label:<30} {n:>6}  {bar(n, max_n)}")

    if len(sorted_locs) > 20:
        print(f"  ... ({len(sorted_locs) - 20} more locations)")

    yields = [n for _, n in sorted_locs]
    print()
    print(f"  Mean yield: {statistics.mean(yields):.1f}")
    print(f"  Median yield: {statistics.median(yields):.1f}")
    print(f"  Stddev: {statistics.stdev(yields) if len(yields) > 1 else 0:.1f}")

    return {"n_locations": len(by_loc)}

def spatial_spread(rows):
    print()
    print("=" * 80)
    print("SPATIAL SPREAD  (distance from each result to its search-point centroid)")
    print("=" * 80)
    dists = []
    for r in rows:
        try:
            d = float(r.get("km_from_centroid", ""))
            dists.append(d)
        except (ValueError, TypeError):
            pass

    if not dists:
        print("(no km_from_centroid data — was this CSV from this scraper?)")
        return None

    dists.sort()
    p50 = percentile(dists, 50)
    p75 = percentile(dists, 75)
    p90 = percentile(dists, 90)
    p95 = percentile(dists, 95)
    p99 = percentile(dists, 99)
    maxd = dists[-1]

    print(f"  Rows with distance: {len(dists):,}")
    print(f"  p50:  {p50:>9.2f} km")
    print(f"  p75:  {p75:>9.2f} km")
    print(f"  p90:  {p90:>9.2f} km")
    print(f"  p95:  {p95:>9.2f} km")
    print(f"  p99:  {p99:>9.2f} km")
    print(f"  Max:  {maxd:>9.2f} km")
    print()
    print("  Distribution:")
    buckets = [(0, 1), (1, 2), (2, 5), (5, 10), (10, 25), (25, 50), (50, 100), (100, 1000), (1000, 100000)]
    n_total = len(dists)
    for lo, hi in buckets:
        n = sum(1 for d in dists if lo <= d < hi)
        print(f"    {lo:>4}-{hi:<5} km:  {n:>5}  ({n/n_total*100:>5.1f}%)  {bar(n, n_total, 30)}")

    over_50 = sum(1 for d in dists if d > 50)
    over_500 = sum(1 for d in dists if d > 500)

    return {
        "p50": p50, "p75": p75, "p90": p90, "p95": p95, "p99": p99,
        "max": maxd, "over_50": over_50, "over_500": over_500,
        "n": n_total,
    }

def quality(rows):
    print()
    print("=" * 80)
    print("DATA QUALITY")
    print("=" * 80)
    n = len(rows)
    with_site = sum(1 for r in rows if r.get("website"))
    claimed   = sum(1 for r in rows if r.get("is_claimed") in ("True", "true", True, "1"))
    with_addr = sum(1 for r in rows if r.get("full_address"))
    with_rate = sum(1 for r in rows if r.get("rating"))
    print(f"  Has website:    {with_site:>6,}  ({with_site/n*100:>5.1f}%)")
    print(f"  Is claimed:     {claimed:>6,}  ({claimed/n*100:>5.1f}%)")
    print(f"  Has full addr:  {with_addr:>6,}  ({with_addr/n*100:>5.1f}%)")
    print(f"  Has rating:     {with_rate:>6,}  ({with_rate/n*100:>5.1f}%)")
    return {
        "with_site_pct": with_site / n,
        "claimed_pct":   claimed / n,
        "with_addr_pct": with_addr / n,
        "n":             n,
    }

def top_types(rows, limit=15):
    print()
    print("=" * 80)
    print(f"TOP {limit} PRIMARY TYPES  (Google's classification)")
    print("=" * 80)
    types = Counter()
    for r in rows:
        first_type = (r.get("types") or "").split(" | ")[0]
        if first_type:
            types[first_type] += 1
    for t, n in types.most_common(limit):
        print(f"  {n:>5,}  {t}")
    return None

# ---------------------------------------------------------------------------
# Recommendations engine
# ---------------------------------------------------------------------------

def recommendations(per_query_data, spread_data, quality_data, n_rows):
    print()
    print("=" * 80)
    print("RECOMMENDATIONS")
    print("=" * 80)
    advice = []

    # --- Queries ---
    if per_query_data:
        dead = per_query_data["flagged_dead"]
        weak = per_query_data["flagged_weak"]
        if dead:
            for q in dead:
                advice.append(f"✗  DROP query '{q}' — contributes <5% unique. Removing saves ~20% of API calls.")
        if weak:
            for q in weak:
                advice.append(f"⚠️  REVIEW query '{q}' — only marginal contribution (5-10%). Worth keeping only if you specifically need its angle.")
        if not dead and not weak:
            advice.append("✓  All queries pulling weight (≥10% unique each).")

    # --- Spatial spread ---
    if spread_data:
        p90 = spread_data["p90"]
        if p90 > EXTREME_SPREAD_KM:
            advice.append(
                f"⚠️  Spatial spread is very wide (p90 = {p90:.1f} km). The API is auto-expanding "
                f"radius significantly. Plan to post-filter the full run by parsing the actual "
                f"address state — about {spread_data['over_50']/spread_data['n']*100:.1f}% of "
                f"results are >50km from their search point."
            )
        elif p90 > WIDE_SPREAD_KM:
            advice.append(
                f"⚠️  Spatial spread is wider than ideal (p90 = {p90:.1f} km). Single-point coverage "
                f"is probably still fine, but consider a post-scrape address-state filter."
            )
        else:
            advice.append(
                f"✓  Spatial spread is healthy (p90 = {p90:.1f} km). Single point per location is "
                f"sufficient — no need to add a grid."
            )

    # --- Quality ---
    if quality_data:
        if quality_data["with_site_pct"] < LOW_WEBSITE_PCT:
            advice.append(
                f"⚠️  Only {quality_data['with_site_pct']*100:.0f}% of results have a website. "
                f"Downstream Clay enrichment may underperform — many businesses won't have a domain "
                f"to scrape."
            )
        if quality_data["claimed_pct"] < LOW_CLAIMED_PCT:
            advice.append(
                f"⚠️  Only {quality_data['claimed_pct']*100:.0f}% of results are claimed. "
                f"Higher chance of stale/defunct entries — consider a post-filter on claimed status."
            )

    # --- Pilot scale check ---
    if n_rows < 50:
        advice.append(
            f"⚠️  Only {n_rows} unique rows in the pilot — that's thin. The yield projections will be noisy. "
            f"Consider expanding the pilot (more locations or more queries) before extrapolating."
        )
    elif n_rows > 5000:
        advice.append(
            f"✓  Pilot has {n_rows:,} rows — plenty to extrapolate the full run yield from."
        )

    for line in advice:
        print(f"  {line}")
    print()

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(
        description="Analyze a pilot CSV and print actionable recommendations.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--csv", help="path to pilot CSV (default: auto-find pilot_*_results.csv in cwd)")
    args = p.parse_args()

    csv_path = Path(args.csv) if args.csv else autodiscover_pilot_csv()
    if not csv_path or not csv_path.exists():
        print("ERROR: no pilot CSV found.")
        print("       Provide --csv path/to/pilot.csv, or cd to a folder with pilot_*_results.csv")
        sys.exit(1)

    print(f"Loading: {csv_path}")
    rows = list(csv.DictReader(open(csv_path)))
    if not rows:
        print("ERROR: CSV is empty.")
        sys.exit(1)

    normalize_columns(rows)
    headline(rows)
    pq    = per_query(rows)
    pl    = per_location(rows)
    sp    = spatial_spread(rows)
    q     = quality(rows)
    top_types(rows)
    recommendations(pq, sp, q, len(rows))

if __name__ == "__main__":
    main()
