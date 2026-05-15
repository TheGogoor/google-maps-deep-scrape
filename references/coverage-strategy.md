# Coverage strategy — choosing between zip-based and city-based

The single most important decision before starting a scrape: **how do you cover the geographic area?** Get this wrong and you either burn API budget on empty rural areas, or miss the dense urban hotspots where your ICP actually lives.

This doc explains the two patterns, when to use each, the math behind them, and how to handle edge cases.

---

## The two strategies, at a glance

| | **Strategy A: Cities + adaptive grid** | **Strategy B: Zips, one point each** |
|---|---|---|
| **Best for** | ICP clustered in urban centers | ICP spread across suburbs and rural areas |
| **Examples** | music venues, comedy clubs, nightclubs, art galleries, premium fitness | dentists, clinics, gyms, contractors, salons, vets |
| **Location source** | Curated list of cities + population | Bundled US zip DB (Eric's CSV) |
| **Grid handling** | 2×2 to 8×8 based on population tier | Always 1×1 (single point per zip) |
| **Typical scale** | 100-500 cities | 5,000-15,000 zips |
| **API calls (5 queries)** | ~20-100k | ~50-100k |
| **Out-of-area noise** | **~1–3%** (centroids sit inside dense areas) | **~30–40%** — must post-filter by `km_from_centroid` |
| **Cost per real local venue** | lower (~0.5–2 calls/venue) | higher (~0.7–1+ calls/venue, after dropping noise) |
| **Why it works** | Big cities have so many venues that a single point misses them. A grid forces coverage. | Zips are small enough that the API auto-expands radius for sparse zips — but see auto-radius caveat below. |

---

## How to decide

Three diagnostic questions to ask the user:

### 1. Where does this business cluster?

> *"If I told you I want to find every X in the country, where would I look — downtown of big cities, suburbs and strip malls, or rural towns?"*

- **Downtown big cities** → Strategy A. Examples: live music venues, event spaces, comedy clubs, nightclubs, art galleries, boutique hotels, premium fitness, professional services in CBDs.
- **Suburbs/strip malls/rural** → Strategy B. Examples: dentists, urgent-care clinics, gyms, contractors, salons, vets, auto shops.
- **Mixed** → genuinely ambiguous. Lean Strategy A if the densest pockets are urban (the noise penalty for Strategy B in low-density areas is severe — see auto-radius caveat below).

### 2. Would a single map search at zoom 13 return >20 results in a dense area?

The Google Maps API returns max 20 results per call. With `offset=20` we get up to 40 total. After that we're blind unless we search a different (lat/lng) point.

- **Yes, easily** (music venues in Soho, gyms in Manhattan) → Strategy A with a real grid in big cities
- **Probably 5-15** (dentists in suburban Atlanta, pediatricians in a mid-sized city) → Strategy B fine; auto-radius handles density variance

### 3. How important is missing zero locations?

- *"If I miss a major venue in NYC, the client will catch it"* → Strategy A with 8×8 grids for top cities
- *"Close-enough is fine, we're enriching downstream anyway"* → Strategy B

**Don't default blindly — pick based on cluster behavior.** The wrong choice typically costs ~2× more API calls per real venue and pollutes the output with national-scope noise (see auto-radius caveat below). Verticals where the ICP genuinely clusters in city centers (venues, nightclubs, professional services in CBDs) should always use Strategy A even if a zip-based approach looks "safer" on paper. Verticals that spread evenly into suburbs (clinics, dentists, contractors) should always use Strategy B.

---

## Strategy A — the adaptive grid math

For city-based scrapes we tier cities by population and assign each a grid dimension:

| Population | Grid dim | Grid points | Why this dim |
|---|---|---|---|
| ≥ 2,000,000 (megacity) | 8×8 | 64 | London/Tokyo/NYC-scale: hundreds of venues, spread across 20+ km |
| 700k – 2M (major) | 6×6 | 36 | San Francisco/Manchester-scale: substantial but not metropolitan-extreme |
| 300k – 700k (mid) | 4×4 | 16 | Cincinnati/Cardiff-scale: a few dozen venues |
| 100k – 300k (small) | 3×3 | 9 | Reading/Asheville-scale: 10-30 venues, edge cases at city limits |
| < 100k (town) | 2×2 | 4 | Small towns; single centroid usually fine, 2×2 for safety |

**Grid spacing**: 2.5 km between cells at zoom 13. This gives slight overlap (zoom 13 viewport is ~3km wide), which means small overlap in returned results — fine because dedup catches them.

Lat step: `0.0225°` ≈ 2.5 km in latitude (constant everywhere).
Lng step: `0.0362°` ≈ 2.5 km in longitude *at ~50°N*. For tropical or polar regions you may need to recalculate (lng degrees shrink toward the poles).

### Why this specific tier table?

Empirical, from the UK music-venues scrape:
- 8×8 grids hit London/Birmingham — both surface ~3,000+ unique venues after dedup
- 6×6 hit cities like Manchester (~2,000 unique)
- 4×4 hit cities like Reading (~500 unique)
- 2×2 hit small towns like Folkestone, Margate (~100-200 unique)

These are calibrated to *not waste API calls on overlapping cells* while *not missing venues at city edges*. Tweak via `--tiers` if a vertical has different density characteristics.

### Why pop-based instead of area-based for cities?

For cities, **higher population ≈ larger geographic area**. London is both populous (9M) and physically large (~1,500 sq km). A small town like Folkestone is both small in pop (50k) and small in area (~10 sq km). So pop → grid_dim works because both dimensions scale together.

This is the opposite of US zips, where high-pop ↔ small area (urban density). See next section.

---

## Strategy B — why no grid for zips

This is counter-intuitive. You might think a 50,000-population zip needs more search points than a 5,000-population one. **It doesn't**, for two reasons:

### Reason 1: Zip size is inversely correlated with population

In the US, high-pop zips are urban — geographically tiny. NYC zip 10001 covers ~0.3 sq miles with 25,000 people. A rural zip in Nebraska covers 80+ sq miles with 5,000 people. So:
- **High-pop urban zip** (e.g. 25k people in 1 sq mile) → single centroid covers the whole thing at zoom 13.
- **Low-pop rural zip** (e.g. 5k people in 50 sq miles) → centroid covers ~5% of the area — but those 50 sq miles contain ~2 clinics total. The marginal coverage from grid points returns near zero.

### Reason 2: Google Maps API auto-expands radius

The API doesn't strictly bound results to the requested viewport. When density is low locally, it widens the search and returns relevant places from further away. This was validated empirically with the US clinic pilot: a 5,700-pop rural zip and a 60,930-pop urban zip both returned ~100-130 unique clinics. The API found similar numbers because it expanded radius for the rural one.

The pattern: **single search point per zip is enough**. The math works because urban density compensates for small zip area, and rural sparsity gets compensated by the API's radius expansion.

### ⚠️ Auto-radius cuts both ways: it's a noise source, not just coverage

Auto-radius doesn't always expand outward to the *nearest* matches. When local density is low, the API fills its 20-result quota with **nationally-relevant hits to the keyword** — venues that may sit hundreds or thousands of km from the searched centroid.

Empirical data (US live-music-venue pilot, May 2026, NOLA + Reno):
- **30–40% of ZIP-based results were out-of-area noise.** A New Orleans suburb ZIP search for "live music venue" returned Carnegie Hall (NYC), Walt Disney Concert Hall (LA), Denver Coliseum, and other famous national venues to fill the result quota.
- Grid-based scrapes on the same cities had **only 1–3% out-of-area noise**, because grid centroids sit inside dense urban areas and the API rarely needs to expand.

**Mandatory mitigation: always post-filter ZIP-based results by `km_from_centroid`.** Pick a threshold tied to your metro definition (we used 25 km from the city centroid; CBSA radius is another reasonable bound). Skip this step and your raw counts will look great while being 30–40% garbage.

This also means **raw result counts are not a fair way to compare strategies.** A ZIP-based scrape that "found 4× more venues" than a grid-based one likely found 4× more *unfiltered hits*; after geographic post-filtering, the actual yield gap shrinks dramatically and the cost-per-real-venue typically favors the grid for clustered verticals.

### What if I want to grid a zip anyway?

Edit `prepare_locations.py` to set `grid_dim` higher than 1 for specific zips. The scraper supports it (it's the same code path). You just won't get much benefit in practice for clinic-style verticals — you'll burn more API calls for marginal yield.

---

## Edge cases

### "I want to scrape one specific metro"

**Use Strategy B (zip-based)** with `--states "TX"` then post-filter the output JSON to only zips in the Houston metro CBSA, OR use `--exclude-city` to drop everything outside Houston's primary cities.

Or **Strategy A** with a hand-built CSV of `name,population` for the cities you care about. Smaller input → smaller scrape.

### "I want to scrape just 1-3 specific cities for music venues"

**Strategy A**, hand-built CSV:
```csv
name,population,state
London,9648110,England
Manchester,547627,England
Birmingham,1141816,England
```
Run `prepare_locations.py cities --csv-in mycities.csv --country uk`. You'll get 3 cities with adaptive grids (London 8×8, Birmingham 6×6, Manchester 4×4 or 6×6).

### "I want to scrape all 50 US states for clinics"

**Strategy B** with no state filter:
```bash
python3 prepare_locations.py us-zips --min-pop 5000 --output locations.json
```
This gives ~25,000 zips → ~150k–250k API calls → 1-3 days runtime with 5 workers. Budget accordingly.

### "I want to scrape a country I don't have zips for"

**Strategy A** with a hand-built or scraped city list. Sources:
- **Wikipedia** — most countries have a "List of cities in X by population" page
- **GeoNames** — free city DB at https://download.geonames.org/export/dump/ (cities500.zip etc)
- **OpenStreetMap Nominatim** — bulk city lookups

The geocoding pipeline in `prepare_locations.py cities` will use Wikipedia REST API + the Maps API to fill in lat/lng.

### "I want hyper-dense scrape of one neighborhood"

Use Strategy A with `grid_dim` overridden to something high (e.g. 12×12) for that single city. Edit `locations.json` by hand after `prepare_locations.py` generates it.

### "My ICP is mixed urban + rural"

**Use Strategy B (zip-based) with broad min-pop**. The auto-radius behavior catches dense urban areas adequately, and you don't have to maintain a curated city list. This is what the US psychiatric-clinic scrape does.

---

## Common mistakes

1. **Copying a UK-cities tier table to a US-zips scrape.** Don't. Tier table only matters for Strategy A (cities). US zips ignore population for grid_dim entirely.

2. **Using zoom 11 instead of 13 for "more coverage".** Zoom 11 widens viewport ~4× but the API still only returns 20 results, so you get the same 20 venues spread across a much bigger area. Net effect: you miss closer matches. Stick with zoom 13.

3. **Adding queries to make up for not gridding.** More queries help yield orthogonally, not spatially. If you need spatial coverage of a megacity, you need a grid — adding 5 more queries won't help.

4. **Trying to "skip the empty zips" by setting `--min-pop 50000` to avoid rural noise.** You'd miss most of suburbia where your ICP lives. The pilot data showed 5k-pop zips return ~similar clinic counts as 50k-pop zips because of API auto-expansion. Don't over-filter.

5. **Hand-listing 50 cities and calling it "comprehensive" for a country with 500 ICP-relevant cities.** A prior version of the US clinic scrape made this mistake — 146 cities, missed ~80% of suburbs. Use the zip DB for comprehensive coverage; use hand-curated cities only when you genuinely want a tight, urban-only list.

6. **Trusting raw result counts when comparing strategies.** A ZIP-based scrape will *look* like it doubles or quadruples the yield of a grid-based scrape — until you filter by distance from centroid. Much of the "extra" is national auto-radius leakage. Always compare local-only yield (within X km of centroid) and cost-per-real-venue, not raw row counts.

---

## Mixing strategies (advanced)

For some verticals, the right answer is a *blend*: city-based with adaptive grid for the top 20 metros, plus zip-based coverage for everywhere else. The scraper supports this — `locations.json` can contain both grid_dim=1 entries (zip-style) and grid_dim=N entries (city-style) in the same file. The engine doesn't care.

Practically: generate two files via `prepare_locations.py`, then merge their `locations` arrays manually. Set the `metadata.source` to "mixed".

This was overkill for both production scrapes (UK venues + US clinics), but it's an option if you ever need it.
