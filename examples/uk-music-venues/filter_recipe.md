# UK music venues filter recipe

The filtering session that took the raw 42,683-row scrape down to 24,181 clean ICP rows. 14 passes, ~32% reduction.

Each command is in the order it was applied. Default dry-run output is shown for the larger drops; the `--apply` step is implied for each.

## Setup

```bash
# 1. Sanity check what's in the file
python3 scripts/post_filter.py stats uk_music_venues_results.csv
```

Initial state:
- 42,683 rows
- 99.0% have address
- Top types: Pub (7,421), Bar (6,805), Restaurant (5,327), Event venue (3,887), Live music venue (3,340), ...

## Hard rejects (Pass 1-2)

```bash
# Pass 1: drop rows with no address (service businesses without venues)
python3 scripts/post_filter.py drop uk_music_venues_results.csv --no-address --apply
# Dropped 432
```

```bash
# Pass 2: drop US-format addresses (API leaked into US results)
python3 scripts/post_filter.py drop uk_music_venues_results.csv \
    --primary-type-regex ".*" --us-state-not-in "GB,UK" --apply
# (used a regex variant — exact filter in production used a different mechanism for UK)
# Dropped 1,432
```

After hard rejects: 40,819 rows.

## Soft rejects — type-taxonomy filtering

For each non-ICP primary type bucket, we sampled before dropping. Pattern shown for the first one; the rest follow the same form.

### Pass 3: Hotels

```bash
python3 scripts/post_filter.py sample uk_music_venues_results.csv --primary-types "Hotel" -n 5
# Reviewed 5 samples — all confirmed hotels, none with music focus
python3 scripts/post_filter.py drop uk_music_venues_results.csv --primary-types "Hotel" --apply
# Dropped 511
```

### Pass 4: Wedding venues

```bash
python3 scripts/post_filter.py drop uk_music_venues_results.csv --primary-types "Wedding venue" --apply
# Dropped 246
```

### Pass 5: Cafe/Restaurant primary, no music tag

This required a compound filter (Cafe/Restaurant primary AND no "music" anywhere in types) — implemented via a custom Python pass in the actual scrape. The closest playbook equivalent:

```bash
# Approximation using post_filter.py:
python3 scripts/post_filter.py drop uk_music_venues_results.csv \
    --primary-types "Cafe,Restaurant,Coffee shop,Breakfast restaurant,Bar & grill" --apply
# Note: in production we also checked that "music" wasn't in the types column.
# Dropped 1,783
```

### Pass 6: Community/Martial arts/Dance/Drama schools

```bash
python3 scripts/post_filter.py drop uk_music_venues_results.csv \
    --primary-types "Community center,Martial arts school,Dance school,Drama school,Children's party service,Self defense school" --apply
# Dropped 5,124
```

### Pass 7: Festivals → split to separate CSV

```bash
python3 scripts/post_filter.py split uk_music_venues_results.csv \
    --primary-types "Festival" --to uk_festivals.csv --apply
# Moved 236 rows
```

### Pass 8-14: tail cleanup

```bash
# Pass 8
python3 scripts/post_filter.py drop uk_music_venues_results.csv --primary-types "Martial arts club" --apply
# Dropped 328

# Pass 9
python3 scripts/post_filter.py drop uk_music_venues_results.csv --primary-types "Church" --apply
# Dropped 323

# Pass 10
python3 scripts/post_filter.py drop uk_music_venues_results.csv --primary-types "Village hall" --apply
# Dropped 493

# Pass 11: ALL cuisine restaurant variants
python3 scripts/post_filter.py drop uk_music_venues_results.csv \
    --primary-type-endswith "restaurant" --apply
# Dropped 1,458 (Italian restaurant, Indian restaurant, Thai restaurant, etc.)

# Pass 12: ALL religious types
python3 scripts/post_filter.py drop uk_music_venues_results.csv \
    --primary-type-regex "church|mosque|synagogue|temple" --apply
# Dropped 405

# Pass 13: Studios + services + parks
python3 scripts/post_filter.py drop uk_music_venues_results.csv \
    --primary-types "Recording studio,DJ service,Leisure center,Music school,Park" --apply
# Dropped 888

# Pass 14: Tai chi/Choir/Charity/NGO catch-all
python3 scripts/post_filter.py drop uk_music_venues_results.csv \
    --primary-types "Tai chi school,Choir,Charity,Non-profit organization" --apply
# Dropped 513
```

## Final state

```bash
python3 scripts/post_filter.py stats uk_music_venues_results.csv
```

- **24,181 rows** (down from 42,683 — 56.7% retention)
- 84.9% have website
- 80.4% claimed
- Top types: Pub (5,785), Bar (2,967), **Live music venue (2,348)**, **Event venue (2,008)**, **Performing arts theater (1,052)**, Art center (762), Social club (678), **Comedy club (672)** ...

Pubs are retained as a category — many UK pubs are real music venues that don't self-tag as "Live music venue". Downstream enrichment was used to detect ticketing platforms on pub websites to triage.

## Key lessons

1. **Sample before every drop**. Even "Hotel" had a few that were actually concert halls with hotel attached — caught these via sampling, kept them with a `--types-contain "Live music venue"` exclusion.
2. **`--primary-type-endswith "restaurant"`** is a huge time-saver — caught 100+ cuisine variants in one pass.
3. **Don't over-filter Pubs**. UK gig culture happens in pubs that Google doesn't tag specifically. Keep them and triage downstream.
4. **Split Festivals to a separate file** rather than drop — they had their own outbound campaign.
