# Example: UK music venues

A real production scrape — building an exhaustive UK music-venue list for cold outbound to live-events SaaS prospects.

| | |
|---|---|
| **Strategy** | A (cities + adaptive grid) |
| **Geography** | 300 UK cities, all regions |
| **Country code** | `uk` (region param, not country) |
| **Queries** | 5 orthogonal (see `config_block.py`) |
| **Grid spacing** | 2.5 km at zoom 13 |
| **Tier table** | 8/6/4/3/2 grid_dim by population (default) |
| **API calls** | ~22,000 |
| **Runtime** | ~3 hours (single-threaded — this scrape predated the parallel-workers refactor; with 5 workers it would be ~30 minutes) |
| **Raw output** | 42,683 unique venues |
| **After filtering** | 24,181 venues |

## Why Strategy A (cities + adaptive grid)?

Music venues cluster heavily in city centers. London alone has thousands of music venues spread across 20+ km — a single search point at the city centroid would miss most of them. The 8×8 grid for London ensures we cover Soho, Camden, Shoreditch, Brixton, etc. all individually.

By contrast, a single zip in suburban Birmingham would only catch maybe 5-15 venues — not enough density to justify gridding.

## The 5 queries (final, after pilot)

```python
SEARCH_QUERIES = [
    "live music venue",
    "music venue",
    "concert hall",
    "comedy club",
    "arts centre",
]
```

The pilot started with 7. Dropped `gig venue` (~5% unique — heavy overlap with "live music venue") and `nightclub` (sub-vertical mismatch — nightclubs typically don't run ticketed live shows with lineups, wrong ICP for this campaign).

See `config_block.py` for the full CONFIG block.

## The filter recipe

The raw 42,683 → 24,181 filtered passed through 14 type-taxonomy drops. See `filter_recipe.md` for the full sequence with row counts.

## Files in this example

- `config_block.py` — the CONFIG block from scraper.py with real values + commentary
- `locations.sample.json` — first 25 entries of the locations.json that was used (with adaptive grid_dim)
- `filter_recipe.md` — the full filtering session as a series of `post_filter.py` commands
