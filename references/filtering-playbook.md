# Filtering playbook — turning a raw scrape into a usable list

**This is an optional post-scrape phase. Only run filtering when the user explicitly asks.** The raw scrape CSV is already a complete deliverable — filtering improves precision but the user may want to do it themselves in Clay, downstream tooling, or by hand. Don't presume.

When the user does ask ("filter this", "clean the list", "drop the non-X", "make it Clay-ready"), follow the loop in this doc.

---

## The filtering loop

Every filtering session is a series of small, reversible decisions. The loop:

```
1. Stats — what's actually in the file?
2. Propose — based on stats + the user's ICP, suggest a drop
3. Sample — show 5-10 example rows that would be dropped
4. Confirm — user says drop / keep / partial
5. Apply — drop with --apply (which makes a backup)
6. Repeat — move to the next category
```

**Never skip step 3 (sample).** Even for the most obvious "hard rejects" — show what would be dropped before doing it. The user needs to see the actual rows to trust the filter. This is the single most important rule of this playbook.

**Always use the helper script.** Don't manually edit the CSV in Python during a conversation — use `scripts/post_filter.py` so the user sees the commands and the dry-run output. Every drop creates an automatic timestamped backup; `undo` restores the latest one if needed.

**Filter in-place on a single canonical CSV.** Don't proliferate `_filtered_v1.csv`, `_filtered_v2.csv`, `_filtered_v3.csv` files — they get unwieldy fast and make it hard to track what was actually applied. Keep your raw scrape output (`{project}_results.csv`) immutable as the source-of-truth, copy it once to a working file, and apply each filter in-place by rewriting that file. The helper script's automatic backups give you the rollback path without cluttering the directory.

---

## Step 1 — Start with stats

Always start with:

```bash
python3 scripts/post_filter.py stats results.csv
```

This shows:
- Total row count
- Data quality (% with address, website, claimed, rating)
- Top 20 primary types (Google's classification) with row counts
- Top 15 states parsed from `full_address`
- Top 15 `search_state` values (which location surfaced the row)

This gives both you and the user a shared picture of what's in the file. Use it to anchor the conversation.

---

## Step 2 — The three categories of filters

After stats, propose drops in this order. Within each category, propose one filter at a time, sample, confirm, apply, repeat.

### Category A — Hard rejects (high-confidence, but still ask)

These are filters where the user almost always says yes — but you still surface counts and show samples first. Never apply silently.

| Filter | When to propose | Command |
|---|---|---|
| **Empty `full_address`** | If stats show "Has full address" <100%. These are usually service businesses with no physical location (touring acts, online services, mobile DJs) — not real local businesses. | `post_filter.py drop CSV --no-address` |
| **Cross-region noise** (US) | If `km_from_centroid` in pilot was wide (p90 > 50km) OR stats show many non-target-state results. The API auto-expanded radius and surfaced businesses from outside the scope. | `post_filter.py drop CSV --us-state-not-in "PA,NJ,DE,..."` |
| **Non-target country** (e.g. US results in a UK scrape) | If you see results with US state codes in the address from a UK scrape. | Use `--primary-type-regex` or sample to confirm; usually combined with the cross-region filter |

**Example — from the UK music-venues scrape we dropped:**
- 432 rows with empty `full_address` (all classified as `Service establishment` — entertainers, DJs, wedding services without a real venue)
- 1,432 rows with US-format addresses (Brooklyn comedy clubs, Hartford venues — API leaked into US results)

**Example — for the US psychiatric-clinics scrape, expect:**
- A small empty-address bucket (a few hundred at most)
- ~14% of results >50km from search zip — these are NOT all out-of-region noise (some are legitimate suburbs of the search city), so you need to parse the actual state from `full_address` and drop only those outside the 31 target states

### Category B — Soft rejects (type-taxonomy filtering)

This is where most of the filtering happens. Google's `types` field is your primary signal. For each large primary-type bucket that doesn't match the ICP, propose a drop — but **always sample first**.

The pattern:

```bash
# 1. See the bucket
python3 scripts/post_filter.py stats CSV

# 2. Sample a non-ICP bucket
python3 scripts/post_filter.py sample CSV --primary-types "Hotel" -n 10

# 3. Show samples to user, ask "drop all 511 Hotel rows?"
# 4. If yes:
python3 scripts/post_filter.py drop CSV --primary-types "Hotel" --apply
```

**Worked example — UK music-venues filtering passes (in the order we did them):**

| Pass | Filter | Rows dropped | Why |
|---|---|---:|---|
| 1 | `--no-address` | 432 | Service establishments |
| 2 | `--us-state-not-in "..."` | 1,432 | US contamination |
| 3 | `--primary-types "Hotel"` | 511 | client's stated exclusion (hotel event spaces) |
| 4 | `--primary-types "Wedding venue"` | 246 | client's stated exclusion |
| 5 | Cafe/Restaurant primary, no "music" in types | 1,783 | Just food, not venues |
| 6 | Community center/Martial arts/Dance school | 5,124 | Not music venues |
| 7 | Split `--primary-types "Festival"` to `festivals.csv` | 236 | Different campaign target |
| 8 | `--primary-types "Martial arts club"` | 328 | Same as #6 |
| 9 | `--primary-types "Church"` | 323 | (most don't host commercial events) |
| 10 | `--primary-types "Village hall"` | 493 | Rural community halls, not commercial venues |
| 11 | `--primary-type-endswith "restaurant"` | 1,458 | All cuisine restaurants |
| 12 | Religious types | 405 | (Anglican church, Mosque, etc.) |
| 13 | Recording studio/DJ service/Leisure center/Music school/Park | 888 | Studios + services + parks |
| 14 | Tai chi/Choir/Charity/NGO | 513 | Catch-all small buckets |

**Total**: 42,683 raw → 29,024 filtered → **32% reduction** by precision filtering.

**US psychiatric clinics filtering (expected, not yet done):**
- Drop `Psychologist`, `Counselor`, `Psychotherapist`, `Social worker`, `Family counselor`, `Applied behavior analysis therapist` — non-prescribers, not ICP for an MSO partnership
- Drop `Psychiatric hospital`, `Hospital`, `Medical Center` — strategy excludes hospital-affiliated clinics
- Drop `Addiction treatment center` — different sub-vertical
- Drop `Non-profit organization` if not clinical
- Keep `Psychiatrist`, `Mental health clinic`, `Mental health service`, `Nurse practitioner`, `Doctor`, `Child psychiatrist`

### Category C — Quality floor (optional)

After type-taxonomy filtering, optionally drop low-signal rows:

```bash
# Drop rows with no website AND no claim AND zero reviews
python3 scripts/post_filter.py drop CSV --unclaimed-zero-reviews
```

These are typically:
- Listings nobody set up a profile for (likely defunct)
- Auto-generated business entries from old data
- Tiny sole-prop operations not actively running

**Be careful here**: small rural businesses often legitimately have 0 reviews and aren't claimed but are still real ICP. Always sample first; if the samples look like real businesses, skip this filter.

---

## Useful regex / substring patterns for common batches

For cleaning up large type-taxonomy tails:

| Pattern | What it catches | Example use |
|---|---|---|
| `--primary-type-endswith "restaurant"` | All cuisine variants (Italian restaurant, Indian restaurant, ...) | Music venue scrape — restaurants aren't ICP |
| `--primary-type-endswith " church"` | Religious denominations | Music venue scrape — most churches don't host commercial music |
| `--primary-type-endswith " school"` | Schools of every type | Music venue scrape — schools aren't venues |
| `--primary-type-regex "martial\|karate\|judo\|taekwondo\|kickbox"` | Martial arts variants | Music venue scrape — martial arts schools |
| `--primary-type-regex "yoga\|pilates\|fitness\|gym\|wellness"` | Fitness/wellness | Music venue scrape — fitness studios |
| `--types-contain "wedding"` | Anything that does weddings (even as a secondary type) | Music venue scrape — but be careful, some real venues also do weddings |

---

## Step 3 — Splitting out a sub-vertical

Sometimes a primary type isn't junk but belongs in a separate file (different downstream campaign). In the UK music-venues scrape we split `Festival` to `festivals.csv` because festivals had their own outbound strategy.

```bash
python3 scripts/post_filter.py split CSV --primary-types "Festival" --to festivals.csv --apply
```

This drops the matching rows from the main CSV AND writes them to the destination. Same dry-run-by-default semantics.

---

## Step 4 — Final stats + summary

After all filter passes, run stats again. The top primary types should now be ~100% ICP-shaped. If you still see large non-ICP buckets, propose another pass.

Tell the user the filtering tally:

```
Filtering passes complete. Tally:
- Raw scrape:        42,683 rows
- After hard rejects: 40,819 rows  (-1,864)
- After type filter:  29,024 rows (-11,795)
- Net retention:      68% of raw
```

---

## Common mistakes

1. **Auto-applying drops without sample review.** Always sample, even for the most obvious-looking filter. There's always an edge case.

2. **Dropping a primary type entirely when 30% of the bucket is legitimate.** When sample shows mixed results (some ICP, some not), use a more specific filter — combine `--primary-types "Pub"` with `--types-contain "live music"` to keep music pubs, drop non-music pubs.

3. **Treating Google's primary type as authoritative.** Google's classification is good but not perfect — a venue tagged as "Pub" might actually be a major music venue, and "Comedy club" might be a comedy night at a restaurant. Sampling catches this.

4. **Filtering too aggressively in the first pass.** Better to over-drop than to leave noise — but each drop is permanent (well, undoable via backup). Slow down on contested categories.

5. **Forgetting to filter cross-state contamination on US scrapes.** The pilot's wide spatial spread is the warning sign. The full run will have *more* of this (more zips × more queries → more chances for the API to expand outward). Always do the `--us-state-not-in` pass.

6. **Skipping the stats pass at the start.** Don't propose drops based on memory of what was probably in the file. Start with stats every time.

7. **Not using `--dry-run` (default) first.** The script defaults to dry-run for a reason. Look at the count and sample before adding `--apply`.

---

## Iteration pattern (full filtering session)

A typical filtering conversation looks like:

```
USER:   "Filter this CSV down to actual music venues"

CLAUDE: [runs post_filter.py stats]
        "Here's what's in the file:
         - 42,683 total rows
         - 99% have addresses
         - Top 5 types: Pub (7,421), Bar (6,805), Restaurant (5,327),
           Event venue (3,887), Live music venue (3,340)

         I'd start with hard rejects (no-address + US contamination),
         then work through the type taxonomy. Want to start there?"

USER:   "Yes"

CLAUDE: [runs sample --no-address, shows 5 rows]
        "All 432 no-address rows are service businesses (DJs, entertainers).
         Drop all 432?"

USER:   "Yes"

CLAUDE: [runs drop --no-address --apply]
        "Done. 42,251 rows remaining. Next: US contamination —
         I see 1,432 rows with US-format addresses..."

[continues through ~10-15 filter passes]

USER:   "How's the file looking?"

CLAUDE: [runs stats]
        "Down to 24,181 rows. Top types are now Pub, Bar,
         Live music venue, Event venue, Performing arts theater,
         Comedy club. Looks ICP-shaped. Want to stop here, or
         continue with the quality floor?"

USER:   "Stop here."
```

The pattern: **stats → propose → sample → confirm → apply → next**. Always interactive, always reversible, always backed up.

---

## Helper script reference

All commands use `scripts/post_filter.py`. Quick reference:

```bash
# Read-only inspection
python3 post_filter.py stats CSV
python3 post_filter.py sample CSV [filters] -n N [--random] [--all-columns]

# Destructive (default dry-run; --apply commits + makes backup)
python3 post_filter.py drop CSV [filters] [--apply]
python3 post_filter.py split CSV [filters] --to OTHER.csv [--apply]

# Recovery
python3 post_filter.py undo CSV [--apply]    # restores latest backup
```

Filters (combinable; AND semantics):
- `--primary-types "A,B,C"` — exact primary-type match
- `--primary-type-endswith "X"` — suffix match
- `--primary-type-regex "REGEX"` — regex match (case-insensitive)
- `--types-contain "X"` — substring in any type
- `--no-address` — empty `full_address`
- `--no-website` — empty `website`
- `--us-state-not-in "PA,NJ,..."` — parsed US state NOT in target list
- `--unclaimed-zero-reviews` — quality floor

Each `drop` and `split` with `--apply` creates a backup like `CSV.bak.20260511_142337.csv`. `undo` restores the most recent one.
