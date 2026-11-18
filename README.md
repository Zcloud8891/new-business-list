# New Biz Wire: Lane County & Oregon new business lists

Weekly digest of newly registered Oregon businesses, built from Oregon Secretary of State public records
on data.oregon.gov (public domain, ORS 276A.368). Served by GitHub Pages from `docs/`.

## Weekly pipeline (`.github/workflows/weekly.yml`)
Runs every Tuesday 15:00 UTC, and on demand (Actions tab -> "New Biz Wire weekly" -> Run workflow).

1. `python3 nbw.py` pulls "Active Businesses - ALL" (`tckn-sxa6`, updated weekly by the state) for the latest
   complete Monday-Sunday week, collapses role rows to one row per business, merges duplicate filings
   (same name at the same address, e.g. an LLC and its DBA), maps Lane County by ZIP (`lane_rules.py`),
   adds flags and a category guess, and writes:
   - `data/lane-county/lane-county_<week-end>.csv`
   - `data/oregon/oregon_<week-end>.csv`
   - `summaries/weekly/<week-end>.json` (PII-free: counts + 5 business names with a category)
2. `python3 build.py` re-renders `docs/` (and still adds a monthly summary when the state posts its
   monthly "New Businesses Registered Last Month" file, `esjy-u4fc`).
3. Commits `data/`, `summaries/`, `docs/` if anything changed.

Optional repo secret `SOCRATA_APP_TOKEN` (free from data.oregon.gov) avoids API throttling.

### CSV columns
registry_number, business_name, entity_type, registry_date, category_guess, county, principal_address(_2),
principal_city/state/zip, address_source (principal or mailing fallback), registered_agent,
registered_agent_address, jurisdiction, flag_out_of_state, flag_agent_address_as_main, flag_holding_like,
flag_storefront_likely, also_filed_as, sos_registry_link. Flags and categories are guesses from the filing.

## Rules
- The public site shows counts and business names with a category only: no personal names, no addresses.
- Towing and roadside businesses are excluded everywhere.
- Paid delivery is unchanged: Stripe Payment Links send buyers to the subscriber Google Sheet, which is
  updated by its own Apps Script (outside this repo).
