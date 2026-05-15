# US psychiatric clinics filter recipe (planned)

This scrape was complete at time of writing but the filtering phase hadn't yet been executed. This file documents the **planned** filter passes based on the pilot analysis and the client's stated ICP rules.

## Initial state

```bash
python3 scripts/post_filter.py stats us_psychiatric_clinics_results.csv
```

- 178,855 rows
- 73.8% have website
- 63.3% claimed
- 98.5% have full address
- Top types: Mental health service (38,431), Psychiatrist (31,917), Mental health clinic (19,406), Psychologist (17,255), Counselor (11,279), Medical clinic (8,989), Psychotherapist (8,710), Nurse practitioner (5,010), Doctor (3,398), ...

## Pass 1: Cross-state contamination

Pilot showed p90 of `km_from_centroid` = 1,177 km — meaning ~14% of results were attributed to a zip 100+ km from where the business actually is. These are real businesses, but their actual addresses may be outside the 31 target states. Filter those.

```bash
# Sample what's about to be dropped
python3 scripts/post_filter.py sample us_psychiatric_clinics_results.csv \
    --us-state-not-in "PA,NJ,DE,MD,VA,WV,NC,SC,GA,TN,KY,FL,AL,MS,AR,LA,OH,IN,MI,MO,MN,WI,IA,IL,KS,NE,ND,SD,CO,OK,TX" \
    -n 10

# Expect ~25,000 rows that have parseable US state outside the 31 target. Confirm with user.
python3 scripts/post_filter.py drop us_psychiatric_clinics_results.csv \
    --us-state-not-in "PA,NJ,DE,MD,VA,WV,NC,SC,GA,TN,KY,FL,AL,MS,AR,LA,OH,IN,MI,MO,MN,WI,IA,IL,KS,NE,ND,SD,CO,OK,TX" \
    --apply
# Expected: ~25,000 rows dropped → ~154,000 remaining
```

## Pass 2: Non-prescriber types (the MSO needs prescribers)

The MSO partnership requires practices that can *prescribe* (psychiatrists, PMHNPs). Drop non-prescriber types — they're valid mental-health providers but not ICP for this product.

```bash
python3 scripts/post_filter.py sample us_psychiatric_clinics_results.csv --primary-types "Psychologist" -n 5
# Confirm — psychologists do therapy not medication, not ICP for MSO

python3 scripts/post_filter.py drop us_psychiatric_clinics_results.csv \
    --primary-types "Psychologist,Counselor,Psychotherapist,Social worker,Family counselor,Applied behavior analysis therapist" \
    --apply
# Expected: ~37,000 rows dropped (Psychologist 17k + Counselor 11k + Psychotherapist 8.7k + smaller buckets)
```

## Pass 3: Hospital-affiliated

The strategy doc explicitly excludes practices partnered with large hospital systems. Drop the hospital-tagged primaries.

```bash
python3 scripts/post_filter.py sample us_psychiatric_clinics_results.csv --primary-types "Psychiatric hospital" -n 5
# Confirm these are hospitals (not small clinics misattributed)

python3 scripts/post_filter.py drop us_psychiatric_clinics_results.csv \
    --primary-types "Psychiatric hospital,Hospital,Medical Center" \
    --apply
# Expected: ~2,500 rows dropped
```

## Pass 4: Wrong-vertical addiction-focused

Addiction treatment is a different sub-vertical (MAT is one of the products offered but the targeting is different). Drop addiction-focused primaries.

```bash
python3 scripts/post_filter.py sample us_psychiatric_clinics_results.csv --primary-types "Addiction treatment center" -n 5
# Confirm

python3 scripts/post_filter.py drop us_psychiatric_clinics_results.csv \
    --primary-types "Addiction treatment center" --apply
# Expected: ~2,000 rows dropped
```

## Pass 5: Non-clinical / non-profit

```bash
python3 scripts/post_filter.py sample us_psychiatric_clinics_results.csv --primary-types "Non-profit organization" -n 5
# Some are mental-health non-profits (drop); some are unrelated (also drop — not target either way)

python3 scripts/post_filter.py drop us_psychiatric_clinics_results.csv \
    --primary-types "Non-profit organization" --apply
# Expected: ~1,000 rows dropped
```

## Pass 6 (optional): Quality floor

```bash
python3 scripts/post_filter.py sample us_psychiatric_clinics_results.csv --unclaimed-zero-reviews -n 10
# Mixed: some defunct, some legitimate sole-prop. Probably skip this filter unless quality is critical.
```

## Expected final state

After passes 1-5:
- Started: 178,855
- After cross-state: ~154,000
- After non-prescribers: ~117,000
- After hospitals: ~114,500
- After addiction: ~112,500
- After non-profits: ~111,500

So we'd land at roughly **~100,000-110,000 ICP-qualified clinics** — vs a previous scraper's 14,000. **8-10× more coverage** of the actual TAM.

## Notes

- **Pass 1 (cross-state)** is the most important and most defensible — it removes API noise without losing any in-region clinics.
- **Pass 2 (non-prescribers)** is the client-specific drop. Different MSO products would keep these.
- **Don't drop "Mental health service"** — this is the largest bucket (38k) and a mix of psychiatrists, group practices, and clinics. Keep and let downstream enrichment triage by checking for prescriber-related signals on the website.

## Cross-dedupe with existing lists (next step after filtering)

This client also had an NPPES-derived list (85k orgs + 71k individuals) from prior work. After Google Maps filtering, dedupe against NPPES using:
- Phone number match
- Address fuzzy-match (street + zip)
- Name fuzzy-match (Levenshtein ≤ 3)

Rows present in both lists are higher-confidence ICP (validated from two sources). Rows only in Google Maps are speculative — useful for top-of-funnel but lower priority than NPPES-validated.
