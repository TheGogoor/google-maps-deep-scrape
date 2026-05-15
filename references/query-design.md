# Query design — picking the right SEARCH_QUERIES

Query selection is the single biggest lever for **what** you find. The coverage strategy (cities-vs-zips, grid sizing) controls *where* you look; queries control *what you ask for* in each search.

This doc covers: how to pick the right queries, how to validate them with a pilot, and the common mistakes.

---

## The goal: 4–6 orthogonal queries

**Orthogonal** = each query surfaces a different segment of the same ICP. Synonyms are not orthogonal.

| Synonymous (bad) | Orthogonal (good) |
|---|---|
| "music venue" + "live music venue" + "concert venue" | "music venue" + "concert hall" + "comedy club" |
| "psychiatrist" + "psych doctor" + "psychiatry practice" | "psychiatrist" + "behavioral health" + "psychiatric nurse practitioner" |
| "dentist" + "dental clinic" + "dentistry" | "dentist" + "orthodontist" + "pediatric dentist" + "oral surgeon" |

**Why orthogonal matters:** the Maps API has a 20-result-per-call cap (40 with offset). If queries are synonyms, the same top-20 venues appear in each query — your dedup rate hits 80–95% and queries 2-5 add almost nothing. Orthogonal queries each get their own top-20.

**Why 4–6:** below 4, you'll miss inventory the API doesn't surface for any single query. Above 6, diminishing returns kick in hard — empirically, the 7th and 8th queries usually add <5% unique each, not worth the API budget.

---

## How to find orthogonal candidates

### Step 1: Free-associate

Ask: *"What's the absolute most generic word for this business, and then what are the 5 most adjacent business types that the same ICP might tag themselves as on Google?"*

For music venues → music venue (generic), comedy club (related but different inventory), concert hall (formal vs casual), nightclub (party-oriented), arts centre (mixed-arts).

For psychiatric clinics → psychiatrist (the doctor noun), psychiatric clinic (the practice), behavioral health (a broader category), mental health clinic (a vertical synonym), psychiatric nurse practitioner (a separate provider type).

### Step 2: Think about the long tail

Each query is like a fishing net cast in a different direction. A venue tagged as "comedy club" on Google probably won't show up for "music venue" searches even if it hosts music shows. Each angle catches what the others miss.

### Step 3: Sanity-check with Maps directly

Before committing to a query list, open Google Maps in a browser and type each query against a known city. Look at what types of businesses appear. If two queries return >80% the same businesses, they're synonyms — pick one.

### Step 4: Check it survives the pilot

After running a 10-location pilot, `scripts/pilot_analyze.py` will flag queries contributing <10% unique. Drop those.

---

## The "prospect or already-converted" guard

Before finalizing queries, pause and ask: *"For each query, does matching it mean the business **is a prospect** or **already has what we're selling**?"*

If a query matches "already has the thing we're selling", **drop it** — you'll surface non-ICP.

Examples where this matters:
- Selling "TMS-as-a-service" to psychiatric practices that *don't yet* offer TMS → don't query `TMS therapy` (matches practices that already do it).
- Selling "build us a website" to businesses without one → don't query "{vertical} website" (matches the ones who already have decent web presence).
- Selling "AI compliance audits" to firms that haven't done one → don't query "AI compliance auditor" (matches competitors).

When in doubt, **ask the user**: *"For [query X], if a business matches this, are they a target customer for what you're selling, or do they already have the thing you're selling?"*

For most consumer-facing verticals (dentists, gyms, restaurants), this isn't an issue — querying "dentist" finds dentists, who are the ICP. The trap only really applies to B2B services where you're selling **to** the businesses, not **for** them.

---

## Local language for non-English countries

If scraping in a non-English country, **use the local-language queries**, not English translations.

- Germany: `Zahnarzt für Kinder`, not `pediatric dentist`
- France: `vétérinaire`, not `veterinarian`
- Spain: `clínica psiquiátrica`, not `psychiatric clinic`
- Japan: `精神科クリニック`, not `psychiatric clinic`

Google indexes local-language business names. An English query in a German city will surface English-speaking expat businesses and tourist-facing places, not the actual local market.

**If unsure of the local term**: ask the user. They probably know the vertical's local-language vocabulary. Failing that, search "X in Germany" on Google in incognito and see what the dominant business names use.

---

## Worked examples

### Music venues (UK example)

Started exploration with 7 queries:
- `live music venue` ✓
- `music venue` ✓
- `concert hall` ✓
- `comedy club` ✓
- `arts centre` ✓
- `gig venue` ✗ — pilot showed 5% unique, dropped
- `nightclub` ✗ — user dropped pre-pilot (sub-vertical with limited ICP fit — most nightclubs don't run ticketed live shows with lineups)

Final 5. The kept ones each surface ~10-30% unique contributions in pilot.

### Psychiatric clinics (US example)

5 final queries:
- `psychiatrist` (33% of pilot first-seen)
- `psychiatric clinic` (20%)
- `behavioral health` (18%)
- `mental health clinic` (10%) — marginal but kept (the campaign's stated ICP explicitly included this segment)
- `psychiatric nurse practitioner` (18%) — captures PMHNP-led practices specifically

The pilot analyzer flagged "mental health clinic" as weak (just above the 10% threshold). User-judgment call: kept because the query specifically captures a sub-segment (community mental health centers) the other queries miss.

### What this might look like for other verticals

| Vertical | Likely query set | Why |
|---|---|---|
| Veterinarians (US) | `veterinarian`, `animal hospital`, `veterinary clinic`, `exotic pet veterinarian`, `pet emergency clinic` | Each catches different sub-segments — emergency-only, exotic-only, general |
| Dental practices (US) | `dentist`, `orthodontist`, `pediatric dentist`, `oral surgeon`, `cosmetic dentistry` | The specialties are clearly orthogonal |
| Coffee shops (urban) | `coffee shop`, `specialty coffee`, `coffee roaster`, `espresso bar`, `cafe` | Tighter overlap — would need to validate via pilot |
| Auto repair (suburbs) | `auto repair shop`, `mechanic`, `transmission repair`, `auto body shop`, `tire shop` | Mostly orthogonal, except "auto repair shop" and "mechanic" may overlap |
| Law firms | `lawyer`, `law firm`, `personal injury lawyer`, `family lawyer`, `business attorney` | Specialties are orthogonal, "lawyer" + "law firm" overlap heavily — pick one |

---

## How to interpret the pilot's per-query output

After running a pilot, `scripts/pilot_analyze.py` reports each query's unique-first-seen contribution as a percentage.

| % unique | Verdict |
|---|---|
| **≥ 20%** | Strong contribution. Keep. |
| **10–20%** | Pulling weight. Keep. |
| **5–10%** | Marginal. Keep only if you specifically need that angle; drop if you have a clean 4-query set without it. |
| **< 5%** | Dead weight. Drop — it's burning 20% of your API calls for almost nothing. |

The pilot analyzer automatically flags <5% as dead and 5-10% as weak.

### Why the % adds up across queries

Each row in the CSV has a `search_query` field — the *first* query that surfaced that unique business. Subsequent queries that returned the same business don't get credit. So per-query shares are zero-sum across the ~100% total unique.

**Important nuance:** the order matters. If queries are processed in `[A, B, C, D, E]` order, query A gets first-seen credit for any venue it can find. B then gets credit for venues it finds that A didn't. Etc. This means the *last* query in your list has to find genuinely unique inventory to score well — which is exactly the test of orthogonality.

---

## Common mistakes

1. **Synonym queries**: 3 versions of "music venue" + "live music venue" + "gig venue". Dedup kills you, queries 2-3 contribute <5%.

2. **Too narrow a query that only matches the "already converted"** (see prospect-vs-converted guard above).

3. **Too broad a query that pulls in everything**: querying just "event venue" for music targets fills the CSV with wedding halls and conference centers. Use a more ICP-specific term.

4. **Querying in English in a non-English country**: surfaces tourist-facing/expat businesses, not the actual market.

5. **Skipping the pilot for query validation**: you don't know what's orthogonal until you see the pilot's per-query stats. Run the pilot, then commit.

6. **Adding queries without removing**: every new query is +20% API calls. Each addition needs to justify itself. The pilot analyzer's "drop" verdicts are your friend.

7. **Mixing prescriber + non-prescriber queries when only prescribers are ICP**: e.g. for an MSO targeting prescriber-led psych practices, querying "psychologist" would surface non-prescriber practices. The final 5 carefully avoid that. If your ICP is narrower than "all healthcare", make sure your queries don't accidentally pull in the wrong sub-vertical.

---

## Iteration pattern

Don't try to nail the perfect query set on the first try. Pattern:

1. **Brainstorm 5-7 candidates** from free association + Maps sanity check.
2. **Pilot run** with all of them (10 locations, ~5-10 minutes).
3. **Analyze**: `python3 pilot_analyze.py` prints per-query contribution.
4. **Drop the dead** (<5% unique).
5. **If needed, re-pilot** with the trimmed set — usually not needed; just launch the full run.
6. **After full run**, the post-scrape filter is where you actually shape the ICP. Queries are about coverage; filters are about precision.
