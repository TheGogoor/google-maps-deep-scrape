# Google Maps Deep Scrape

A reusable workflow for building large, ICP-targeted business lists from Google Maps. Battle-tested on two real production scrapes totaling **~200,000 unique businesses**.

Runs as a [Claude Code](https://www.anthropic.com/claude-code) skill (auto-loaded into Claude's context when you ask for Google Maps scraping) and as a normal Python project (run the scripts directly).

---

## What's in the box

```
google-maps-deep-scrape/
├── SKILL.md                          ← Entry point Claude reads
├── README.md                         ← (you are here)
├── references/
│   ├── architecture.md               ← Safety stack, parallel workers, sleep gotchas
│   ├── coverage-strategy.md          ← City+grid vs zip+single decision tree
│   ├── query-design.md               ← Orthogonal queries, pilot interpretation
│   └── filtering-playbook.md         ← Post-scrape filtering knowledge
├── scripts/
│   ├── scraper.py                    ← Bulletproof config-driven scraper (parallel workers)
│   ├── prepare_locations.py          ← Generate locations.json from zips OR cities
│   ├── pilot_analyze.py              ← Yield + spatial-spread + query-contribution metrics
│   └── post_filter.py                ← Interactive filtering helper (stats, sample, drop, split, undo)
├── data/
│   └── us-zip-codes.csv              ← 42,735 US zips w/ population & lat/lng (public dataset)
└── examples/
    ├── uk-music-venues/              ← Real working config — music venues in 300 UK cities
    └── us-psychiatric-clinics/       ← Real working config — psychiatric clinics in 31 US states
```

---

## What it does

Scrapes Google Maps via the RapidAPI Maps Data API, with the goal of building **exhaustive, ICP-targeted lists** that are clean enough to feed straight into outbound tooling (Clay, Smartlead, etc.).

The methodology:

- **Multi-query orthogonal sweep** — 4-6 queries that each capture a different segment of the same ICP
- **Adaptive grid for urban verticals** — 2×2 to 8×8 grid by city population
- **Zip-based scan for suburban/rural** — single point per zip, the API auto-expands when local density is low
- **Offset pagination** — break Google's 20-result-per-call ceiling
- **Parallel workers** — 5× speedup over single-threaded with no 429 issues in practice
- **Bulletproof safety stack** — survive system sleep, network drops, manual stops, with full `--resume`
- **Pilot-first** — validate yield + queries on 10 locations before committing to a multi-hour run
- **Iterative post-scrape filtering** — type-taxonomy review with sample-confirm-drop loops

### Production track record

| Project | Strategy | Scale | API calls | Output |
|---|---|---|---:|---|
| Music venues across UK | Cities + adaptive grid | 300 cities | ~22k | 42,683 raw → 24,181 filtered |
| Psychiatric clinics across 31 US states | Zips + single point | 8,704 zips | ~86k | 178,855 raw |

---

## Quick start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Get a RapidAPI key

Subscribe to the Maps Data API at https://rapidapi.com/alexanderxbx/api/maps-data and add the key to your shell:

```bash
export RAPIDAPI_KEY=your_key_here
# (or add to ~/.zshrc / ~/.bash_profile to persist)
```

### 3. Build a location list

For US suburban/rural verticals — filter the bundled zip database:

```bash
python3 scripts/prepare_locations.py us-zips \
    --states "PA,NJ,DE,MD,VA" \
    --min-pop 5000 \
    --output locations.json
```

For city-based verticals — supply a CSV of `name,population[,state]` and geocode:

```bash
python3 scripts/prepare_locations.py cities \
    --csv-in my-cities.csv \
    --country uk \
    --output locations.json
```

### 4. Copy + configure the scraper

```bash
cp scripts/scraper.py my-project.py
# Edit the CONFIG block at the top (PROJECT_NAME, COUNTRY, SEARCH_QUERIES, etc.)
```

### 5. Pilot first

```bash
python3 -u my-project.py --pilot               # 10-location pilot, ~5 min
python3 scripts/pilot_analyze.py               # reads pilot_*_results.csv, shows recommendations
```

### 6. Full run

```bash
# macOS — wrap in caffeinate to survive sleep
nohup caffeinate -dis python3 -u my-project.py > run.log 2>&1 &
disown

# Linux / cloud VM — just nohup
nohup python3 -u my-project.py > run.log 2>&1 &
disown

# Monitor anytime
cat my-project_run_status.txt
tail -f run.log
```

### 7. (Optional) Filter the output

```bash
python3 scripts/post_filter.py stats my-project_results.csv
python3 scripts/post_filter.py sample my-project_results.csv --primary-types "Hotel" -n 5
python3 scripts/post_filter.py drop my-project_results.csv --primary-types "Hotel,Wedding venue" --apply
```

See [`references/filtering-playbook.md`](references/filtering-playbook.md) for the full filter pattern.

---

## Install as a Claude Code skill

If you use Claude Code, install this as a skill so it auto-loads whenever you ask for Google Maps scraping:

```bash
# Clone wherever you keep your skills
git clone https://github.com/<your-username>/google-maps-deep-scrape ~/projects/google-maps-deep-scrape

# Symlink into ~/.claude/skills so Claude Code picks it up
mkdir -p ~/.claude/skills
ln -s ~/projects/google-maps-deep-scrape ~/.claude/skills/google-maps-deep-scrape
```

After that, restart Claude Code. The next time you say something like *"Help me scrape Google Maps for veterinarians in Texas"*, Claude will auto-load `SKILL.md` and walk you through the full workflow.

---

## How a session typically goes

When you invoke this as a skill, Claude runs a guided interview (not a charge-ahead build):

1. **Scope** — Claude asks what vertical, geography, ICP density pattern, business model, scale, exclusions
2. **Propose** — Claude suggests Strategy A or B, a draft 4-6 query list, and an estimated scale; you sign off
3. **Pilot** — Claude builds `locations.json`, copies + configures the scraper, runs a 10-location pilot, shows you the analysis
4. **Full run** — with your approval, launches in the background; you check status whenever you want
5. **(Optional) Filtering** — only on request, Claude walks through the type-taxonomy filtering loop with sample-confirm-drop at each step

The detailed conversation flow is in [`SKILL.md`](SKILL.md).

---

## Examples

Two real production configs are bundled in `examples/`:

- [`examples/uk-music-venues/`](examples/uk-music-venues/) — Strategy A (cities + adaptive grid). 300 cities, 5 queries, ~22k API calls, ~24k filtered music venues.
- [`examples/us-psychiatric-clinics/`](examples/us-psychiatric-clinics/) — Strategy B (zips + single point). 8,704 zips across 31 states, 5 queries, ~86k API calls, ~179k clinics.

Each example folder includes the full CONFIG block, a sample of the locations file, and the actual filter recipe that was applied.

---

## Architecture highlights

The scraper is designed to survive *anything*:

| | Why it matters |
|---|---|
| **PID-locked single-instance** | Two scrapers on the same CSV would corrupt it |
| **Atomic JSON checkpoints** every 50 API calls | `--resume` picks up exactly where you stopped |
| **Append-only seen_ids.txt** | Dedup state is on disk within ~1 second per result |
| **Per-row CSV flush** | Crash/reboot doesn't lose written rows |
| **SIGINT/SIGTERM handlers** | `Ctrl+C` or `kill <pid>` stops cleanly |
| **429 retry ladder** `[5, 15, 30, 60]s` | Rate-limit transients are absorbed automatically |
| **`ThreadPoolExecutor` parallelism** | 5× speedup over single-threaded; configurable |
| **Human-readable status file** | `cat run_status.txt` anytime to see live state |

Full details in [`references/architecture.md`](references/architecture.md).

---

## When to use this

✅ **Yes** — when you need:
- Exhaustive coverage of a country or set of states for a specific vertical
- A pilot-validated, ICP-shaped list that's clean enough to feed Clay/Smartlead/etc.
- A scrape that may take 30 minutes or 30 hours, that needs to survive the laptop sleeping
- A reusable methodology you can apply to the next vertical without rebuilding from scratch

⚠️ **Probably overkill** — for:
- A one-time grab of 50-100 leads in one neighborhood
- Just exploring what's on the map for a brand-new vertical (use the API directly with a few manual searches first)

---

## Cost

- **Maps Data API** on RapidAPI: free tier covers a few thousand calls/month; paid tiers start ~$15/mo for 100k calls/month. Each "deep scrape" costs ~50-100k calls.
- **Cloud VM** (optional, see `references/architecture.md`): $5/month for hands-off multi-day runs. **You don't need this — running on a personal Mac plugged into power works fine.**

---

## License

MIT. See [LICENSE](LICENSE).

---

## Acknowledgements

The bundled `data/us-zip-codes.csv` is a public US zip code dataset. The methodology was developed through two production scrapes (music venues + psychiatric clinics) and includes lessons that cost real hours to learn.
