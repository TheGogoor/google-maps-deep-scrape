---
name: google-maps-deep-scrape
description: Build Google Maps business lists for cold outbound — scales from small targeted batches (one metro, a hundred leads) to exhaustive country-wide TAM scrapes (tens to hundreds of thousands of leads). Uses RapidAPI Maps Data API with multi-query orthogonal sweeps, adaptive grids for dense urban verticals, zip-based scans for suburban/rural verticals, optional parallel workers, and a bulletproof safety stack (lock file, atomic checkpoints, per-row CSV flush, signal handlers, retry-with-backoff, resumable). The same architecture handles a 10-zip pilot in 1 minute or a 31-state run over 12 hours — scale is controlled by the location list, not by which scraper you run. Trigger phrases include "scrape google maps for X", "find [businesses] in [region/state/country]", "build a list of [businesses]", "deep scrape google maps", "scrape google maps for [vertical] across [geography]", "TAM list of [businesses]".
---

# Google Maps Deep Scrape

A reusable workflow for building ICP-targeted business lists from Google Maps. Battle-tested on two real production scrapes:
- **UK music venues**: 300 cities, adaptive grid (2×2 → 8×8 by population), 22k API calls → 42k raw → 24k filtered clean leads
- **US psychiatric clinics**: 8,704 zips across 31 states, no grid, 86k API calls → 179k unique clinics → ~100k filtered ICP

This skill walks you through (a) deciding the right coverage strategy for the vertical, (b) building the location list at the right scale, (c) running a pilot to validate assumptions, (d) launching the bulletproof run, and (e) post-scrape filtering.

### Scale is independent of scraper

The same scraper handles **any scope**. You change what's in `locations.json`, not which tool you run:

- **Small batch** — one metro, 50 zips, a single city's grid → minutes
- **State-level** — every zip in TX or every city in California → an hour or two
- **Country-wide** — all 31 US states or the whole UK → 8–20 hours with parallel workers

The bulletproof safety stack (resume, per-row flush, lock file, signal handlers) is on regardless of scope — there's no downside to having it for a 10-minute run, and it's a lifesaver for a 12-hour one.

---

## 🗣️ How a session typically goes (the conversation flow)

**This is the most important section. Read it before doing anything else.**

When the user invokes this skill, treat it as a **guided interview**, not a "build everything based on inferred context" task. The user usually starts with one sentence ("scrape Google Maps for X"). Your job is to walk them from vague intent through a sign-off-able plan, then a pilot, then a full run. **At every transition, the user explicitly approves.**

Five phases. Move forward only after the user agrees with what you're proposing.

### Phase 1 — Scope (interview the user, do not propose yet)

Ask the user, in roughly this order. Don't dump all 6 at once — start with 3, then drill in as the picture clarifies.

1. **The vertical** — What kind of business are we scraping? Sometimes obvious from the initial message; sometimes you need to ask.
2. **The geography** — Country? Specific states or regions? A single metro? Multiple countries?
3. **The ICP density pattern** — Where does this business cluster — downtown big cities, suburbs, rural towns, or mixed? This is what decides Strategy A vs B (see `references/coverage-strategy.md`).
4. **The business model** — Are you *selling to* these businesses, or building something *for* them? This unlocks the "prospect vs already-converted" guard for query design (see `references/query-design.md`).
5. **Expected scale** — Are we building a hands-on list of a few hundred leads for one metro, or a full-country TAM list with tens of thousands?
6. **Exclusions** — Any specific business types, cities, chains, or characteristics to exclude up-front?

**Don't**: propose strategy, queries, or scale before you've heard answers to at least 1-3.
**Don't**: ask all 6 in a single wall of text — ask in clusters of 2-3, react to the answers, then drill deeper.

### Phase 2 — Propose (and get sign-off before building)

Once you have enough to propose, present **all of this together**:

- **Strategy**: A (cities + adaptive grid) or B (zips, single point) — with **one-line reasoning** referencing the user's answers
- **Draft query list**: 4-6 orthogonal queries, with a one-line note on what each captures
- **Estimated scale**: rough API call count + runtime + % of monthly budget
- **Confirmation question**: "Want to refine any of this before we build the locations file?"

If the user pushes back on queries or wants to add/drop, adjust and re-present. **Do not build `locations.json` or copy `scripts/scraper.py` until the user explicitly approves the plan.**

### Phase 3 — Build + Pilot (always)

After approval:

1. Generate `locations.json` via `scripts/prepare_locations.py` (see `references/coverage-strategy.md` for which sub-command).
2. Set up the user's project folder, copy `scripts/scraper.py` into it, edit the CONFIG block (only the config block — see scraper.py for what to change).
3. Run a **10-location pilot** with `--pilot` (~3-10 minutes).
4. Run `scripts/pilot_analyze.py` and show the user:
   - Headline yield
   - Per-query contribution (with any flagged dead/weak)
   - Spatial spread verdict
   - Data quality summary
   - The recommendations section verbatim
5. **Ask the user**: "Based on the pilot, want to adjust queries / strategy / scope before the full run, or launch as-is?"

**Never** skip the pilot. Even for a "small" scrape — the pilot is often the whole job for a small batch anyway.

### Phase 4 — Full run

With explicit user confirmation, launch the full run (backgrounded with `caffeinate -dis nohup ... &`). When you launch, tell the user:

- **Expected runtime** (e.g. "~3-5 hours, finishes around 8pm")
- **Status check commands** (cat the status file, tail the log, wc -l the CSV)
- **How to interrupt gracefully** if needed (`kill <pid>` for SIGTERM — see `references/architecture.md`)
- **Caffeinate warning** if running on macOS without AC power

Then **step back**. Don't poll the run unprompted. The user will ask "how is it going" when curious — at that point, do a status check and report.

### Phase 5 — Filtering (optional, on user request only)

The raw CSV is the deliverable. Filtering is a **separate, optional follow-up** — only do it if the user asks ("clean this list", "filter to ICP", "drop the non-X", "make it Clay-ready").

When the user does ask, **read `references/filtering-playbook.md`** and follow its sample-review-first flow. Never apply filters silently, even for the "hard rejects" — always surface the count and category first.

---

## ⚡ Before you do anything — confirm setup

### One-time setup

1. **RapidAPI key**: scraping uses `maps-data.p.rapidapi.com`. The user needs a subscription. If `RAPIDAPI_KEY` is not in the environment, ask the user for it and write it to `~/.zshrc` (or `~/.bash_profile`) as `export RAPIDAPI_KEY=...`. **Never** commit the key to a repo.

2. **Python 3.8+** with `requests` installed (`pip install requests`)

3. **macOS only — for runs longer than ~30 minutes**: the scraper script must be wrapped in `caffeinate -dis` to survive system sleep. The `-s` flag only works on AC power; warn the user to keep the laptop plugged in OR run on a cloud VM.

### Confirm before running

- Has the user set `RAPIDAPI_KEY`?
- For multi-hour runs: is the laptop on AC and plugged in?
- For cross-day runs: is the user OK with running on their machine or do they want a cloud VM?

---

## 🧭 Step 1 — Diagnose the vertical and pick coverage strategy

Two coverage strategies. Pick by asking the user about the ICP's density pattern:

### Strategy A: City-based with adaptive grid

**Use when:** Business is clustered in urban centers, missing one venue costs you a lead, and density varies hugely across the country.

Examples: music venues, comedy clubs, art galleries, nightclubs, premium restaurants, premium fitness studios.

How it works:
- Hardcoded list of top N cities by population (300 for UK, would be 500 for US, etc.)
- Each city gets a **grid** of search points based on its population:
  - 8×8 grid for megacities (≥2M)
  - 6×6 for major (700k–2M)
  - 4×4 for mid (300k–700k)
  - 3×3 for small (100k–300k)
  - 2×2 for towns (<100k)
- Grid spacing: ~2.5 km at zoom 13 (`LAT_STEP = 0.0225`, `LNG_STEP = 0.0362` at ~50°N — adjust for other latitudes)
- 5 orthogonal queries × 2 offsets each
- See `examples/uk-music-venues/` for a full working config

### Strategy B: Zip-based with single point per zip

**Use when:** Business is spread across suburbs and rural areas, and the API can be relied on to auto-expand its radius when local density is low.

Examples: dentists, clinics (any specialty), gyms, contractors, salons, real estate agents, veterinarians.

How it works:
- US zip database with population (bundled in `data/us-zip-codes.csv`, 42,735 entries)
- Filter zips: target states, minimum population, exclude any major metros the ICP excludes
- **One search point per zip** (no grid — the API auto-expands radius for sparse zips, validated empirically)
- 5 orthogonal queries × 2 offsets each
- See `examples/us-psychiatric-clinics/` for a full working config

### How to decide — questions to ask the user

1. **"Where does this business cluster — big cities (Manhattan, downtown Atlanta), suburbs (subdivisions, strip-mall corridors), or rural areas?"**
   - "Big cities" → Strategy A
   - "Suburbs/rural" → Strategy B
   - "Mixed" → Strategy B with broad population threshold

2. **"Is it the kind of business where a single map search at zoom 13 would return more than 20 results in a dense area?"**
   - Music venues in Soho London → yes, definitely. Strategy A with adaptive grid.
   - Dental clinics in suburban Atlanta → maybe 5–15. Strategy B fine.

3. **"How important is missing zero venues?"**
   - "If I miss any major venue in NYC, the client will catch it" → A with 8×8 grid for big cities
   - "Close-enough is fine" → B

If unsure, default to **Strategy B** — simpler, fewer API calls, and the API's auto-radius is surprisingly good for most verticals.

For deeper rationale, read **`references/coverage-strategy.md`**.

---

## 🔍 Step 2 — Design search queries

The single most important lever for yield. Aim for **4–6 orthogonal queries** that each surface a different segment of the same ICP.

### Principles

1. **Each query should add ≥10% unique** when pilot-tested. Anything below → drop it.
2. **Orthogonal not synonymous**: "music venue" + "live music venue" overlap heavily. "music venue" + "concert hall" + "comedy club" are orthogonal — each finds different inventory.
3. **Think about which side of the query is the prospect.** Sometimes a query that *seems* like an ICP match will surface businesses that are already-converted (i.e. *not* a prospect). Worth pausing to ask: does matching this query mean the business *is* a prospect, or that they *already have* the thing we're selling? When in doubt, ask the user.
4. **Local-language queries** for non-English countries (e.g. `Zahnarzt für Kinder` not `pediatric dentist` for Germany).

### Worked example — music venues (UK)

Started with 7, dropped 2 after pilot showed <6% unique each. Final 5:
- `live music venue`
- `music venue`
- `concert hall`
- `comedy club`
- `arts centre`

### Worked example — psychiatric clinics (US)

5 queries, chosen to surface practices broadly. Final set:
- `psychiatrist`
- `psychiatric clinic`
- `behavioral health`
- `mental health clinic`
- `psychiatric nurse practitioner`

For more depth on query selection, including a checklist for finding orthogonal candidates, read **`references/query-design.md`**.

---

## 🧪 Step 3 — Run a small pilot FIRST

**Never skip this.** A 10-location pilot takes 1–5 minutes and saves you from running a 10-hour scrape with wrong assumptions.

### What the pilot validates

1. **Yield per location** — how many unique results per location? Tells you what the full run will produce.
2. **Per-query contribution** — is each query adding ≥10% unique? If not, drop it before full run.
3. **Spatial spread** — track `km_from_centroid` per result. If most results are within 1–5 km of search point, single point per zip is fine. If you see large gaps and dense areas at the edges, you may need a grid.
4. **Dedup rate** — what fraction of returned places are already-seen? High dedup rate = you have query overlap = you can drop a query.
5. **Data quality** — % with website, % claimed, % with full address.

### Pilot setup

Pick 8–15 locations spanning the population/density distribution you'll see in the full run:
- 2–3 high-pop (urban dense)
- 3–4 mid-pop (suburban)
- 2–3 low-pop (rural)
- Spread across regions you'll be scraping

Run with the `--pilot` flag of `scripts/scraper.py`. Output files use `pilot_*` prefix so they don't pollute the real-run state files.

Then run `scripts/pilot_analyze.py` to get the metrics above.

For detailed pilot interpretation guidance, read **`references/architecture.md` → "Pilot analysis"**.

---

## 🚀 Step 4 — Launch the full run

The scraper is built with a bulletproof safety stack. Once you launch, you don't need to babysit it.

### Safety features (all active by default)

1. **Lock file** — refuses to start if another instance is alive (prevents double-runs)
2. **Atomic JSON checkpoint** every 25 API calls — `--resume` picks up exactly where you left off
3. **Append-only `seen_ids.txt`** for dedup — every row's `business_id` is on disk within 1 second
4. **Per-row CSV write + flush** — if the process dies, every row already on screen is on disk
5. **SIGINT/SIGTERM handler** — `Ctrl+C` or `kill <pid>` flushes state and exits gracefully
6. **429 retry with exponential backoff** — `[5, 15, 30, 60]` second ladder, then gives up that specific call
7. **`run_status.txt`** — human-readable snapshot, rewritten every 25 calls, `cat` it anytime
8. **`run.log`** — full stdout log via `tee`

### Concurrency

The default is **5 parallel workers** via `ThreadPoolExecutor`. Pilot showed 5.7× speedup over single-threaded with zero 429s on sustained load. To override: `--workers N`.

- 3–5 workers: safe default
- 8–10 workers: faster, possibly triggers more 429s (retries handle it but throughput suffers)
- 1 worker: only useful for debugging

### Launch command (macOS, long-running)

```bash
cd "your-project-folder"
nohup caffeinate -dis python3 -u scripts/scraper.py --config config.py > run.log 2>&1 &
disown
```

- `nohup` + `disown` → survives terminal close
- `caffeinate -dis` → prevents sleep (`-d` display, `-i` idle, `-s` system — `-s` is AC-only)
- `python3 -u` → unbuffered stdout so `tail -f run.log` shows live progress

### Resume after a stop

```bash
nohup caffeinate -dis python3 -u scripts/scraper.py --config config.py --resume > run.log 2>&1 &
disown
```

### Status check

```bash
cat run_status.txt              # snapshot
tail -f run.log                  # live tail (Ctrl+C to exit, doesn't stop scraper)
wc -l output.csv                 # row count
```

For detailed architecture explanation, read **`references/architecture.md`**.

---

## 🔬 Step 5 — Post-scrape filtering (optional)

When the scrape finishes, the raw CSV is the deliverable. **Filtering is a separate, optional follow-up step** — only run it if the user asks (e.g. "clean this list", "filter to ICP", "drop the non-music venues", "make it Clay-ready").

### When the user wants to filter

**Read `references/filtering-playbook.md`** — that file has the full decision tree, sample-review-first methodology, and category-by-category filtering patterns for several verticals.

The general shape:
1. **Quick stats pass** — distribution by `types` primary category, by state, by website-present, etc. Tells you what's actually in the file.
2. **Hard rejects** (high confidence — but still confirm with the user before applying): no address, out-of-region, etc.
3. **Soft rejects** (require sample review): use the Google `types` field as the signal. For each large non-ICP bucket, pull 5–10 sample rows, show the user, get an explicit "drop" before removing.
4. **Quality floor** (optional): drop unclaimed + 0 reviews, drop missing website, etc.

### Do not auto-filter

Even for the "hard rejects" — out-of-region or no-address rows — surface the count to the user and ask before removing. The filtering playbook is opinionated about why and when each filter applies; defer to it rather than running filters silently.

For the full filtering decision tree + interactive helper script, read **`references/filtering-playbook.md`** and use **`scripts/post_filter.py`**.

---

## 📋 Workflow summary

When a user asks for a Google Maps deep scrape:

1. **Confirm setup**: `RAPIDAPI_KEY` present, Python installed, AC power for long runs
2. **Diagnose vertical** → pick Strategy A (city + adaptive grid) or Strategy B (zip + single point)
3. **Design queries** → 4–6 orthogonal, exclude already-converted queries, local language for non-English
4. **Build location list** → use `scripts/prepare_locations.py` (city-based or zip-based mode)
5. **Pilot 8–15 locations** → 100 API calls, ~1 min, validates yield + query orthogonality + spatial spread
6. **Launch full run** with `caffeinate -dis nohup ... &` for macOS, or screen/tmux on a cloud VM
7. **Monitor casually** — `cat run_status.txt` whenever curious
8. **Post-scrape filtering** *(optional — only when the user asks)* — read `references/filtering-playbook.md` and confirm each step with the user. The raw CSV is a complete deliverable on its own.

---

## 📦 What's in this skill

```
google-maps-deep-scrape/
├── SKILL.md                          ← (you are here)
├── README.md                         ← for humans browsing the repo on GitHub
├── references/
│   ├── architecture.md               ← safety stack details, parallel workers, caffeinate gotchas
│   ├── coverage-strategy.md          ← city-based vs zip-based decision tree
│   ├── query-design.md               ← orthogonal queries, pilot interpretation
│   └── filtering-playbook.md         ← post-scrape type-taxonomy filtering decision tree
├── scripts/
│   ├── scraper.py                    ← bulletproof config-driven scraper (parallel workers)
│   ├── prepare_locations.py          ← generate locations.json from zips or cities
│   ├── pilot_analyze.py              ← yield + spatial-spread + query-contribution metrics
│   └── post_filter.py                ← interactive filtering helper
├── data/
│   └── us-zip-codes.csv              ← 42,735 US zips w/ pop, lat/lng (public dataset)
└── examples/
    ├── uk-music-venues/              ← real working config, UK music venues
    └── us-psychiatric-clinics/       ← real working config, US psychiatric clinics
```

When this skill is loaded, read the reference files **only when needed**. Each one is targeted:
- Architecture decisions or debugging the scraper? → `architecture.md`
- Picking cities vs zips? → `coverage-strategy.md`
- User unsure about queries? → `query-design.md`
- Filtering the output? → `filtering-playbook.md`

---

## ⚠️ Common gotchas

1. **API key in repo** — the most expensive mistake. Always env var.
2. **macOS sleep on battery** — `caffeinate -s` only works on AC. For overnight runs, plug in OR use a cloud VM. The scraper will still resume gracefully if interrupted, but you'll lose hours of progress.
3. **Querying the already-converted ICP** — if you're selling X to businesses that *don't* have X, never include X in your queries. You'll surface only non-ICP.
4. **Skipping the pilot** — a 1-minute pilot has saved every multi-hour scrape we've run. Never skip it.
5. **Adaptive grid in the wrong direction** — US zips are inversely correlated with size (high-pop urban zips are geographically tiny; low-pop rural zips are huge). Don't blindly copy UK city pop tiers to US zips. See `coverage-strategy.md`.
6. **5-worker safety on sustained load** — pilot validates a small burst doesn't trigger 429s, but sustained 5-worker load over hours may. Existing retry/backoff handles it transparently; you just lose 5–15% throughput.

---

## 🎯 When this skill is the right tool

Use this for any Google Maps list-building task that benefits from:
- **Orthogonal multi-query coverage** — surfaces inventory a single broad query would miss
- **Breaking the 20-result API ceiling** via offset pagination + grid/zip sweeping
- **Resumability** — if anything goes wrong (network, sleep, manual stop), you continue from the last checkpoint
- **Honest, ICP-targeted output** — the architecture rewards specific queries, so the raw output is already much cleaner than a single-query broad scrape

It works at any scale, from a 10-zip pilot to a 31-state country sweep. The safety stack is on regardless. The only real cost of using this for small scrapes is the 1-minute mental overhead of running the pilot first — and that pilot itself is often the whole job for a small batch.
