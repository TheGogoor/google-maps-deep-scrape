#!/usr/bin/env python3
"""
Post-scrape filter helper for Google Maps scrape outputs.

This is a Swiss-army-knife of CSV filter primitives meant to be driven by
Claude during the optional Phase 5 of the skill (see SKILL.md and
references/filtering-playbook.md). It is NOT meant to be auto-run after a
scrape — only invoke it when the user explicitly asks to filter.

Sub-commands:

    stats CSV
        Show distribution of primary types, by state, data quality, etc.
        Read-only — never modifies anything.

    sample CSV [filters] [-n N]
        Print full sample rows matching a filter (default 5).
        Use this BEFORE proposing a drop, so the user sees what would go.
        Read-only — never modifies anything.

    drop CSV [filters] [--apply]
        Drop rows matching the filter(s).
        Default is dry-run (prints count + sample rows, nothing written).
        Use --apply to commit the drop. Always makes a timestamped backup first.

    split CSV [filters] --to OTHER.csv [--apply]
        Move matching rows from CSV → OTHER.csv (so e.g. festivals can be
        extracted from a venues file).
        Same dry-run-by-default semantics.

    undo CSV
        Restore the most recent backup.

FILTERS (combinable; AND semantics):
    --primary-types "A,B,C"       row's primary type in this list
    --primary-type-endswith "X"   row's primary type ends with "X" (e.g. "restaurant")
    --primary-type-regex "REGEX"  row's primary type matches REGEX (case-insensitive)
    --types-contain "X"           any type in the row contains X
    --no-address                  full_address is empty
    --no-website                  website is empty
    --us-state-not-in "PA,NJ,..." parses US state from full_address; rows whose
                                  state is NOT in the list are matched (i.e.
                                  cross-state contamination)
    --unclaimed-zero-reviews      is_claimed false AND review_count == 0

USAGE:
    python3 post_filter.py stats results.csv
    python3 post_filter.py sample results.csv --primary-types "Hotel" -n 5
    python3 post_filter.py drop results.csv --no-address
    python3 post_filter.py drop results.csv --primary-types "Hotel,Wedding venue" --apply
    python3 post_filter.py split results.csv --primary-types "Festival" --to festivals.csv --apply
    python3 post_filter.py undo results.csv
"""

import argparse
import csv
import datetime as dt
import re
import shutil
import sys
from collections import Counter
from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_csv(path: Path) -> tuple:
    """Return (rows, fieldnames)."""
    with open(path) as f:
        rdr = csv.DictReader(f)
        rows = list(rdr)
        fieldnames = rdr.fieldnames or []
    return rows, fieldnames

def write_csv(path: Path, rows: list, fieldnames: list):
    """Atomic write via tmp + rename."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    tmp.replace(path)

def backup_csv(path: Path) -> Path:
    """Snapshot the file with a timestamped suffix; return backup path."""
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = path.with_suffix(f".bak.{ts}.csv")
    shutil.copy(path, bak)
    return bak

def latest_backup(path: Path) -> Path | None:
    """Find the most recent *.bak.*.csv for this file."""
    bak_pattern = f"{path.stem}.bak.*.csv"
    cands = sorted(path.parent.glob(bak_pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return cands[0] if cands else None

def primary_type(row) -> str:
    return (row.get("types") or "").split(" | ")[0].strip()

US_STATE_RE = re.compile(r",\s*([A-Z]{2})\s+\d{5}")
US_ZIP_RE   = re.compile(r"\b\d{5}(-\d{4})?\b")

def parse_us_state(address: str) -> str:
    """Return the 2-letter state code from a US-formatted address, or ''."""
    m = US_STATE_RE.search(address or "")
    return m.group(1) if m else ""

# ---------------------------------------------------------------------------
# Predicate builder — combines all CLI filter flags into one match() function
# ---------------------------------------------------------------------------

def build_matcher(args):
    """Return (matcher_fn, description_str). matcher_fn(row) → True if row matches."""
    preds = []
    desc_parts = []

    if args.primary_types:
        wanted = set(s.strip() for s in args.primary_types.split(",") if s.strip())
        preds.append(lambda r: primary_type(r) in wanted)
        desc_parts.append(f"primary_type in {sorted(wanted)}")

    if args.primary_type_endswith:
        suffix = args.primary_type_endswith.lower()
        preds.append(lambda r: primary_type(r).lower().endswith(suffix))
        desc_parts.append(f"primary_type ends with '{suffix}'")

    if args.primary_type_regex:
        pat = re.compile(args.primary_type_regex, re.IGNORECASE)
        preds.append(lambda r: bool(pat.search(primary_type(r))))
        desc_parts.append(f"primary_type matches /{args.primary_type_regex}/i")

    if args.types_contain:
        needle = args.types_contain.lower()
        preds.append(lambda r: needle in (r.get("types") or "").lower())
        desc_parts.append(f"any type contains '{needle}'")

    if args.no_address:
        preds.append(lambda r: not (r.get("full_address") or "").strip())
        desc_parts.append("full_address is empty")

    if args.no_website:
        preds.append(lambda r: not (r.get("website") or "").strip())
        desc_parts.append("website is empty")

    if args.us_state_not_in:
        wanted = set(s.strip().upper() for s in args.us_state_not_in.split(","))
        def has_us_state_outside(r):
            st = parse_us_state(r.get("full_address") or "")
            return bool(st) and st not in wanted
        preds.append(has_us_state_outside)
        desc_parts.append(f"US-format address with state NOT in {sorted(wanted)}")

    if args.unclaimed_zero_reviews:
        def p(r):
            claimed = r.get("is_claimed") in ("True", "true", True, "1")
            try:
                reviews = int(r.get("review_count") or 0)
            except ValueError:
                reviews = 0
            return (not claimed) and reviews == 0
        preds.append(p)
        desc_parts.append("unclaimed AND 0 reviews")

    if not preds:
        return None, "(no filter specified)"

    def matcher(row):
        return all(p(row) for p in preds)

    return matcher, " AND ".join(desc_parts)

# ---------------------------------------------------------------------------
# Sub-command: stats
# ---------------------------------------------------------------------------

def cmd_stats(args):
    rows, _ = load_csv(Path(args.csv))
    n = len(rows)
    print(f"Total rows: {n:,}\n")

    # Data quality
    print("=" * 80)
    print("DATA QUALITY")
    print("=" * 80)
    with_addr = sum(1 for r in rows if (r.get("full_address") or "").strip())
    with_site = sum(1 for r in rows if (r.get("website") or "").strip())
    claimed   = sum(1 for r in rows if r.get("is_claimed") in ("True", "true", True, "1"))
    with_rate = sum(1 for r in rows if r.get("rating"))
    print(f"  Has full address: {with_addr:>7,}  ({with_addr/n*100:>5.1f}%)")
    print(f"  Has website:      {with_site:>7,}  ({with_site/n*100:>5.1f}%)")
    print(f"  Is claimed:       {claimed:>7,}  ({claimed/n*100:>5.1f}%)")
    print(f"  Has rating:       {with_rate:>7,}  ({with_rate/n*100:>5.1f}%)")

    # Top primary types
    print()
    print("=" * 80)
    print(f"TOP {args.top_types} PRIMARY TYPES")
    print("=" * 80)
    types = Counter(primary_type(r) for r in rows)
    distinct = len(types)
    print(f"  ({distinct:,} distinct primary types total)\n")
    for t, n_ in types.most_common(args.top_types):
        pct = n_ / n * 100
        print(f"  {n_:>6,} ({pct:>5.1f}%)  {t}")

    # States in address
    print()
    print("=" * 80)
    print("TOP STATES (parsed from full_address)")
    print("=" * 80)
    states = Counter(parse_us_state(r.get("full_address") or "") for r in rows)
    states.pop("", None)
    for s, n_ in states.most_common(15):
        print(f"  {n_:>6,}  {s}")

    # search_state distribution (the location-file state, not parsed)
    search_states = Counter((r.get("search_state") or "").strip() for r in rows)
    search_states.pop("", None)
    if search_states:
        print()
        print("=" * 80)
        print("TOP search_state VALUES (which location surfaced the row)")
        print("=" * 80)
        for s, n_ in search_states.most_common(15):
            print(f"  {n_:>6,}  {s}")

# ---------------------------------------------------------------------------
# Sub-command: sample
# ---------------------------------------------------------------------------

def cmd_sample(args):
    rows, _ = load_csv(Path(args.csv))
    matcher, desc = build_matcher(args)
    if matcher is None:
        print(f"ERROR: must provide at least one filter flag for sample. See --help.")
        sys.exit(1)

    matched = [r for r in rows if matcher(r)]
    print(f"Filter: {desc}")
    print(f"Matched: {len(matched):,} / {len(rows):,} rows ({len(matched)/len(rows)*100:.1f}%)")
    print()

    if not matched:
        return

    import random
    if args.random:
        random.seed(args.seed)
        random.shuffle(matched)

    show_cols = (
        ["name", "website", "full_address", "city", "search_state",
         "types", "rating", "review_count", "is_claimed"]
        if not args.all_columns
        else None  # None = show every column
    )

    for i, r in enumerate(matched[:args.limit], 1):
        print(f"--- Sample {i} ---")
        for k, v in r.items():
            if show_cols and k not in show_cols:
                continue
            v_str = (v or "").strip()
            if len(v_str) > 100:
                v_str = v_str[:97] + "..."
            print(f"  {k:<18} {v_str or '(empty)'}")
        print()

# ---------------------------------------------------------------------------
# Sub-command: drop
# ---------------------------------------------------------------------------

def cmd_drop(args):
    path = Path(args.csv)
    rows, fields = load_csv(path)
    matcher, desc = build_matcher(args)
    if matcher is None:
        print(f"ERROR: must provide at least one filter flag for drop. See --help.")
        sys.exit(1)

    matched = [r for r in rows if matcher(r)]
    kept    = [r for r in rows if not matcher(r)]

    print(f"Filter: {desc}")
    print(f"Would drop: {len(matched):,} rows  ({len(matched)/len(rows)*100:.1f}%)")
    print(f"Would keep: {len(kept):,} rows")
    print()

    if not matched:
        print("Nothing matched — no changes.")
        return

    # Show a few sample drops for confirmation
    print("Sample of rows that WOULD be dropped:")
    for i, r in enumerate(matched[: min(3, len(matched))], 1):
        name = (r.get("name") or "").strip()[:50]
        typ  = primary_type(r)
        addr = (r.get("full_address") or "").strip()[:60]
        print(f"  {i}. {name} | {typ} | {addr}")
    print()

    if not args.apply:
        print(f"[dry-run] No changes written. Re-run with --apply to commit.")
        return

    bak = backup_csv(path)
    write_csv(path, kept, fields)
    print(f"✓ Backup: {bak}")
    print(f"✓ Dropped {len(matched):,} rows from {path}")
    print(f"✓ {path} now has {len(kept):,} rows")

# ---------------------------------------------------------------------------
# Sub-command: split
# ---------------------------------------------------------------------------

def cmd_split(args):
    src = Path(args.csv)
    dst = Path(args.to)
    rows, fields = load_csv(src)
    matcher, desc = build_matcher(args)
    if matcher is None:
        print(f"ERROR: must provide at least one filter flag for split.")
        sys.exit(1)

    matched = [r for r in rows if matcher(r)]
    kept    = [r for r in rows if not matcher(r)]

    print(f"Filter: {desc}")
    print(f"Would split: {len(matched):,} rows from {src.name} → {dst.name}")
    print(f"Would leave: {len(kept):,} rows in {src.name}")

    if not matched:
        print("Nothing matched — no changes.")
        return

    if not args.apply:
        print(f"[dry-run] No changes written. Re-run with --apply to commit.")
        return

    bak = backup_csv(src)
    write_csv(dst, matched, fields)
    write_csv(src, kept, fields)
    print(f"✓ Backup of {src.name}: {bak}")
    print(f"✓ {dst} now has {len(matched):,} rows")
    print(f"✓ {src} now has {len(kept):,} rows")

# ---------------------------------------------------------------------------
# Sub-command: undo (restore latest backup)
# ---------------------------------------------------------------------------

def cmd_undo(args):
    path = Path(args.csv)
    bak = latest_backup(path)
    if not bak:
        print(f"ERROR: no backup found for {path}")
        sys.exit(1)
    print(f"Most recent backup: {bak}")
    if not args.apply:
        print(f"[dry-run] would restore {bak} → {path}")
        print(f"          Use --apply to commit.")
        return
    shutil.copy(bak, path)
    print(f"✓ Restored {path} from {bak}")

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def add_filter_flags(p):
    p.add_argument("--primary-types", help="comma-separated list, exact match on primary type")
    p.add_argument("--primary-type-endswith", help="match primary type ending in this string")
    p.add_argument("--primary-type-regex",    help="regex (case-insensitive) for primary type")
    p.add_argument("--types-contain",          help="match if any type in the row contains this substring")
    p.add_argument("--no-address", action="store_true", help="match rows with empty full_address")
    p.add_argument("--no-website", action="store_true", help="match rows with empty website")
    p.add_argument("--us-state-not-in",        help="comma-separated state codes; match rows whose parsed US state is NOT in the list (cross-state contamination)")
    p.add_argument("--unclaimed-zero-reviews", action="store_true",
                   help="match rows that are unclaimed AND have 0 reviews")

def main():
    p = argparse.ArgumentParser(
        description="Post-scrape filter helper.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("stats", help="distribution + quality stats (read-only)")
    s.add_argument("csv")
    s.add_argument("--top-types", type=int, default=20)
    s.set_defaults(func=cmd_stats)

    sa = sub.add_parser("sample", help="print sample rows matching a filter (read-only)")
    sa.add_argument("csv")
    add_filter_flags(sa)
    sa.add_argument("-n", "--limit", type=int, default=5)
    sa.add_argument("--random", action="store_true", help="random sample instead of first N")
    sa.add_argument("--seed", type=int, default=42)
    sa.add_argument("--all-columns", action="store_true", help="show every CSV column, not just headline")
    sa.set_defaults(func=cmd_sample)

    d = sub.add_parser("drop", help="drop rows matching a filter (dry-run by default)")
    d.add_argument("csv")
    add_filter_flags(d)
    d.add_argument("--apply", action="store_true", help="actually do it (default is dry-run)")
    d.set_defaults(func=cmd_drop)

    sp = sub.add_parser("split", help="move matching rows to a separate CSV (dry-run by default)")
    sp.add_argument("csv")
    add_filter_flags(sp)
    sp.add_argument("--to", required=True, help="destination CSV path")
    sp.add_argument("--apply", action="store_true", help="actually do it (default is dry-run)")
    sp.set_defaults(func=cmd_split)

    u = sub.add_parser("undo", help="restore the latest backup")
    u.add_argument("csv")
    u.add_argument("--apply", action="store_true", help="actually restore (default is dry-run)")
    u.set_defaults(func=cmd_undo)

    args = p.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
