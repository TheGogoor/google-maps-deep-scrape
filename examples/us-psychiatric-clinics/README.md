# Example: US psychiatric clinics

A real production scrape — building the full TAM list of psychiatric practices across 31 US states for a behavioral-health MSO outbound campaign.

| | |
|---|---|
| **Strategy** | B (zips + single point per zip) |
| **Geography** | 8,704 zips across 31 US states (Mid-Atlantic + Southeast + Midwest) |
| **Country code** | `us` |
| **Queries** | 5 orthogonal (see `config_block.py`) |
| **Grid** | All locations grid_dim=1 (no grid — API auto-radius covers density variance) |
| **API calls** | ~86,000 actual (out of ~87,040 max possible) |
| **Runtime** | ~5 hours (with 5 parallel workers + intermittent sleep on user's laptop) |
| **Raw output** | 178,855 unique clinics |
| **Filtering** | Not yet executed at time of writing; see filter_recipe.md for the planned passes |

## Why Strategy B (zips + single point)?

The campaign's ICP was **"suburban and rural areas of small-to-medium cities, NOT NYC/LA/Chicago."** Textbook Strategy B case:

- Clinics are spread across suburbs and small cities, not clustered in megacities
- Density varies hugely (rural Nebraska vs suburban Atlanta) — handled by API auto-radius rather than grid
- 8,704 zips is fine-grained coverage; 300 cities would miss the suburbs between them

The pilot validated this: a 5,700-pop rural zip returned 118 clinics, a 60,930-pop urban zip returned 131. API auto-expands radius when local density is low.

## The 5 queries

```python
SEARCH_QUERIES = [
    "psychiatrist",
    "psychiatric clinic",
    "behavioral health",
    "mental health clinic",
    "psychiatric nurse practitioner",
]
```

Notably excluded:
- `TMS therapy` and `ketamine clinic` — these would surface practices that *already* offer interventional psych, which is exactly what the campaign was *selling them*. Wrong-fit by the "prospect-vs-already-converted" guard.
- `psychologist` — non-prescriber, doesn't fit the MSO target (which needs prescribers to do TMS/Spravato)

## Location filter

```bash
python3 scripts/prepare_locations.py us-zips \
    --states "PA,NJ,DE,MD,VA,WV,NC,SC,GA,TN,KY,FL,AL,MS,AR,LA,OH,IN,MI,MO,MN,WI,IA,IL,KS,NE,ND,SD,CO,OK,TX" \
    --min-pop 5000 \
    --exclude-city "Chicago" \
    --output locations.json
# Output: 8,704 zips (filtered down from 42,735 in the bundled US zip DB)
```

The Chicago exclusion was an explicit client requirement (the only big-metro exclusion in the 31 target states).

## Pilot results

10 zips spanning 5 states (NC, SC, FL, IN, MO, IL, TN, MO) and pop tiers 5.7k → 60.9k.

- 99 API calls in 4.3 minutes (pre-parallel) / 45 seconds (with 5 workers)
- 1,069 unique clinics
- Per-query yield: all 5 queries pulled weight (35%, 20%, 18%, 18%, 10%) — none flagged as dead
- Spatial spread: p50=7km, p75=25km, **p90=1,177km** ← significant! Means 14% of results are far from search zip (API auto-radius going wide for niche queries in sparse zips)
- 86% have website, 78% claimed

The pilot's wide spatial spread led directly to the planned post-filter: drop rows where the parsed US state in `full_address` is outside the 31 target states.

## Files in this example

- `config_block.py` — the CONFIG block from scraper.py with real values + commentary
- `locations.sample.json` — first 25 of the 8,704 zips (with notes on regenerating the full file)
- `filter_recipe.md` — the planned filtering passes (not yet executed at time of writing)
