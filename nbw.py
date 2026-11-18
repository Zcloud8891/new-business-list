#!/usr/bin/env python3
"""New Biz Wire: weekly pull of newly registered Oregon businesses.

Source: Oregon Secretary of State "Active Businesses - ALL" (data.oregon.gov, tckn-sxa6),
public domain under ORS 276A.368. The state refreshes it about once a week.

What it does:
  1. Finds the newest registry_date in the dataset and picks the latest complete
     Monday-Sunday week before it (so a partial day is never counted).
  2. Pulls every row registered that week (one row per business x role).
  3. Collapses to one row per business: principal address (mailing as fallback),
     entity type, registry date, registered agent name + address.
  4. Dedupes the same business filed twice (e.g. an LLC and its DBA at one address).
  5. Maps Lane County by ZIP, adds flags and a category guess from the name.
  6. Writes data/lane-county/lane-county_<week-end>.csv and data/oregon/oregon_<week-end>.csv,
     plus a PII-free summaries/weekly/<week-end>.json used by the public site
     (business name + category only, no personal names, no addresses).

Run:  python3 nbw.py                 latest complete week
      python3 nbw.py --end 2026-11-08 a specific week ending on that Sunday
Optional env: SOCRATA_APP_TOKEN (free; avoids throttling). Standard library only.
"""
import collections, csv, datetime, json, os, re, sys, time, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from lane_rules import LANE_ZIPS, NON_LANE_CITIES, category_guess, is_towing

API = 'https://data.oregon.gov/resource/tckn-sxa6.json'
DATA_DIR = os.path.join(HERE, 'data')
WEEKLY = os.path.join(HERE, 'summaries', 'weekly')
PAGE = 50000
SAMPLE_N = 5

# names that usually mean a place customers walk into (a guess, labeled as one)
STOREFRONT_RE = re.compile(r"\b(CAFE|COFFEE|ESPRESSO|BAKERY|BAKESHOP|RESTAURANT|EATERY|DINER|PIZZA|PIZZERIA|TACOS?|TAQUERIA|"
                           r"BBQ|BARBECUE|GRILL|BISTRO|DELI|BREWING|BREWERY|TAPROOM|PUB|TAVERN|WINE BAR|CREAMERY|DONUTS?|"
                           r"SALON|BARBER\w*|NAILS?|LASH\w*|BROWS?|SPA|MED SPA|TATTOO|BOUTIQUE|SHOP|STORE|MARKET|MERCANTILE|"
                           r"THRIFT|VINTAGE|ANTIQUES?|FLORAL|FLORIST|DENTAL|DENTISTRY|DMD|DDS|ORTHODONTICS?|CHIROPRACTIC|CLINIC|"
                           r"OPTOMETRY|EYECARE|PHARMACY|VETERINARY|VET|AUTO REPAIR|AUTOMOTIVE|TIRES?|COLLISION|AUTO BODY|"
                           r"GYM|FITNESS|YOGA|PILATES|DAYCARE|CHILDCARE|PRESCHOOL|MONTESSORI|DISPENSARY|LAUNDROMAT|CLEANERS)\b")
HOLDING_RE = re.compile(r"\b(HOLDINGS?|HOLDCO|HOLDING CO|PROPERTIES|PROPERTY|INVESTMENTS?|INVESTORS?|CAPITAL|"
                        r"ACQUISITIONS?|VENTURES?|ASSETS?|EQUITY|EQUITIES|RENTALS?|REAL ESTATE|"
                        r"ESTATES?|TRUST|PARENT|INTERMEDIATE|TOPCO|MIDCO|BIDCO|SPV|FUND|"
                        r"PORTFOLIO|OWNER|OWNERS|LESSEE|LESSOR|LANDCO|PROPCO|OPCO)\b"
                        r"|^\d{2,}\s+(N|S|E|W|NE|NW|SE|SW)\b|^[\d\s&-]+(,?\s*L\.?L\.?C\.?)?$")
SAMPLE_BLOCK = re.compile(r"\b(TOW|TOWING|ROADSIDE|WRECKER|WRECKERS|FLATBED|RECOVERY|JUMP ?START|LOCKOUT)\b")
# public sample must not show personal names: skip names with initials or personal professional suffixes
PERSONAL_RE = re.compile(r"(\b[A-Z]\.\s|\b[A-Z]\s[A-Z][A-Z]+\s|\b(DMD|DDS|MD|DO|PC|P\.C\.|PLLC|CPA|ESQ|LCSW|LPC|LMFT|RN|NP|PHD|DVM|OD|ND|LAC)\b)")
SUFFIX_RE = re.compile(r"[,.]?\s*\b(L\.?L\.?C\.?|INC\.?|INCORPORATED|CORP\.?|CORPORATION|CO\.?|COMPANY|L\.?L\.?P\.?|"
                       r"LTD\.?|LIMITED|P\.?C\.?|PLLC|LP)\s*$")
# well-known commercial registered-agent offices seen in Oregon filings (street prefix, ZIP)
KNOWN_AGENT_ADDRS = {('7185 SW SANDBURG ST', '97223'), ('1 WORLD TRADE CTR', '97204'), ('780 COMMERCIAL ST SE', '97301'),
                     ('1127 BROADWAY ST NE', '97301'), ('5305 RIVER RD N', '97303'), ('4801 SE VIEW ACRES RD', '97267')}


def get(params, tries=4):
    q = urllib.parse.urlencode(params, safe="$:,'()<>=")
    hdr = {'User-Agent': 'new-biz-wire (github.com/zcloud8891/new-business-list)'}
    if os.environ.get('SOCRATA_APP_TOKEN'):
        hdr['X-App-Token'] = os.environ['SOCRATA_APP_TOKEN']
    for i in range(tries):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(f'{API}?{q}', headers=hdr), timeout=180))
        except Exception as e:  # network blips / throttling
            if i == tries - 1:
                raise
            print(f'retry after {e}', file=sys.stderr)
            time.sleep(10 * (i + 1))


def pick_week(end_arg=None):
    if end_arg:
        end = datetime.date.fromisoformat(end_arg)
    else:
        mx = get({'$select': 'max(registry_date) as m'})[0]['m'][:10]
        mx = datetime.date.fromisoformat(mx)
        end = mx - datetime.timedelta(days=(mx.weekday() + 1) % 7 or 7)  # last Sunday strictly before newest day
    return end - datetime.timedelta(days=6), end


def fetch(start, end):
    where = f"registry_date >= '{start}T00:00:00' AND registry_date < '{end + datetime.timedelta(days=1)}T00:00:00'"
    rows, off = [], 0
    while True:
        page = get({'$where': where, '$limit': PAGE, '$offset': off, '$order': ':id'})
        rows += page
        if len(page) < PAGE:
            return rows
        off += PAGE


def norm_addr(a):
    a = re.sub(r'[^A-Z0-9 ]', ' ', (a or '').upper())
    a = re.sub(r'\b(SUITE|STE|UNIT|APT|#)\b.*$', '', a)
    return re.sub(r'\s+', ' ', a).strip()


def person(r):
    return ' '.join(x for x in [r.get('first_name'), r.get('middle_name'), r.get('last_name'), r.get('suffix')] if x).strip()


def agent_name(r):
    return r.get('entity_of_record_name') or r.get('not_of_record_entity') or person(r)


def base_name(n):
    n = (n or '').upper().strip()
    for _ in range(2):
        n = SUFFIX_RE.sub('', n).strip(' ,.')
    return re.sub(r'[^A-Z0-9]+', ' ', n).strip()


def collapse(rows):
    groups = collections.OrderedDict()
    for r in rows:
        groups.setdefault(r['registry_number'], []).append(r)
    # registered-agent street addresses used by many filings this week = agent offices
    agent_use = collections.Counter()
    for g in groups.values():
        for k in {(norm_addr(r.get('address')), (r.get('zip') or '')[:5]) for r in g if r.get('associated_name_type') == 'REGISTERED AGENT'}:
            agent_use[k] += 1
    busy_agent_addrs = {k for k, n in agent_use.items() if n >= 5 and k[0]} | KNOWN_AGENT_ADDRS
    out = []
    for reg, g in groups.items():
        f = g[0]
        role = lambda t: next((r for r in g if r.get('associated_name_type') == t), None)
        loc, src = role('PRINCIPAL PLACE OF BUSINESS'), 'principal'
        if not loc:
            loc, src = role('MAILING ADDRESS'), 'mailing'
        loc = loc or {}
        if not loc:
            src = 'none'
        ag = role('REGISTERED AGENT') or {}
        name = (f.get('business_name') or '').strip()
        et = f.get('entity_type') or ''
        st, z = (loc.get('state') or '').upper(), (loc.get('zip') or '')[:5]
        city = (loc.get('city') or '').upper()
        key = (norm_addr(loc.get('address')), z)
        ag_key = (norm_addr(ag.get('address')), (ag.get('zip') or '')[:5])
        ag_is_company = bool(ag.get('entity_of_record_name') or ag.get('not_of_record_entity'))
        agent_as_main = bool(key[0]) and (any(key[0].startswith(a) and key[1] == zz for a, zz in busy_agent_addrs)
                                          or (ag_is_company and key == ag_key))
        cat = category_guess(name, et)
        out_of_state = bool(st) and st != 'OR'
        holding = bool(HOLDING_RE.search(name.upper()))
        addr_txt = (loc.get('address') or '').upper()
        storefront = (st == 'OR' and not out_of_state and not agent_as_main and not holding
                      and 'PO BOX' not in addr_txt.replace('.', '') and bool(STOREFRONT_RE.search(name.upper())))
        out.append({
            'registry_number': reg, 'business_name': name, 'entity_type': et,
            'registry_date': (f.get('registry_date') or '')[:10], 'jurisdiction': f.get('jurisdiction') or '',
            'address_source': src, 'principal_address': loc.get('address') or '',
            'principal_address_2': loc.get('address_continued') or '', 'principal_city': loc.get('city') or '',
            'principal_state': st, 'principal_zip': z,
            'county': 'Lane' if (st == 'OR' and z in LANE_ZIPS and city not in NON_LANE_CITIES) else '',
            'registered_agent': agent_name(ag) if ag else '',
            'registered_agent_address': ', '.join(x for x in [ag.get('address'), ag.get('address_continued'), ag.get('city'),
                                                              ' '.join(x for x in [ag.get('state'), ag.get('zip')] if x)] if x),
            'category_guess': cat,
            'flag_out_of_state': out_of_state, 'flag_agent_address_as_main': agent_as_main,
            'flag_holding_like': holding, 'flag_storefront_likely': storefront,
            'also_filed_as': '', 'towing': is_towing(name),
            'sos_registry_link': (f.get('business_details') or {}).get('url', '') if isinstance(f.get('business_details'), dict) else (f.get('business_details') or ''),
        })
    return dedupe(out)


def dedupe(bs):
    """Same base name at the same main address = one business (keep the entity, note the DBA)."""
    pri = lambda b: (1 if 'ASSUMED' in b['entity_type'].upper() else 0, b['registry_date'], b['registry_number'])
    seen, keep = {}, []
    for b in sorted(bs, key=pri):
        k = (base_name(b['business_name']), norm_addr(b['principal_address']), b['principal_zip'])
        if k[0] and k in seen:
            first = seen[k]
            first['also_filed_as'] = '; '.join(x for x in [first['also_filed_as'], f"{b['business_name']} ({b['entity_type'].title()}, #{b['registry_number']})"] if x)
            continue
        seen[k] = b
        keep.append(b)
    keep.sort(key=lambda b: (b['registry_date'], b['business_name']))
    return keep, len(bs) - len(keep)


COLS = ['registry_number', 'business_name', 'entity_type', 'registry_date', 'category_guess', 'county',
        'principal_address', 'principal_address_2', 'principal_city', 'principal_state', 'principal_zip', 'address_source',
        'registered_agent', 'registered_agent_address', 'jurisdiction',
        'flag_out_of_state', 'flag_agent_address_as_main', 'flag_holding_like', 'flag_storefront_likely',
        'also_filed_as', 'sos_registry_link']


def write_csv(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, COLS, extrasaction='ignore')
        w.writeheader()
        for r in rows:
            w.writerow({k: ('yes' if v is True else '' if v is False else v) for k, v in r.items()})


def sample(rows, n=SAMPLE_N):
    """Name + category only. Prefer real, local-looking businesses across different categories."""
    pool = [b for b in rows if not SAMPLE_BLOCK.search(b['business_name'].upper()) and not PERSONAL_RE.search(b['business_name'].upper())
            and 'ASSUMED' not in b['entity_type'].upper() and b['category_guess'] != 'Other / unclear'
            and not b['flag_holding_like'] and not b['flag_out_of_state'] and not b['flag_agent_address_as_main']]
    pool.sort(key=lambda b: (not b['flag_storefront_likely'], b['registry_number']))
    picked, cats = [], set()
    for b in pool:
        if b['category_guess'] not in cats:
            picked.append(b); cats.add(b['category_guess'])
        if len(picked) == n:
            break
    for b in pool:
        if len(picked) >= n:
            break
        if b not in picked:
            picked.append(b)
    return [{'name': b['business_name'], 'cat': b['category_guess']} for b in picked]


def summary(rows, extra):
    c = collections.Counter
    return {**extra, 'total': len(rows),
            'cats': c(b['category_guess'] for b in rows).most_common(),
            'flags': {'out_of_state': sum(b['flag_out_of_state'] for b in rows),
                      'agent_address_as_main': sum(b['flag_agent_address_as_main'] for b in rows),
                      'holding_like': sum(b['flag_holding_like'] for b in rows),
                      'storefront_likely': sum(b['flag_storefront_likely'] for b in rows)},
            'types': c(b['entity_type'].title() for b in rows).most_common(6),
            'sample': sample(rows)}


def main():
    end_arg = sys.argv[sys.argv.index('--end') + 1] if '--end' in sys.argv else None
    start, end = pick_week(end_arg)
    rows = fetch(start, end)
    if not rows:
        raise SystemExit(f'no rows for {start}..{end}; state file may not be refreshed yet')
    bs, dupes = collapse(rows)
    towing = sum(b['towing'] for b in bs)
    bs = [b for b in bs if not b['towing']]  # long-standing rule: no towing/roadside businesses
    lane = [b for b in bs if b['county'] == 'Lane']
    tag = end.isoformat()
    write_csv(os.path.join(DATA_DIR, 'lane-county', f'lane-county_{tag}.csv'), lane)
    write_csv(os.path.join(DATA_DIR, 'oregon', f'oregon_{tag}.csv'), bs)
    lane_cities = collections.Counter(b['principal_city'].title() for b in lane).most_common()
    s = {'week_start': start.isoformat(), 'week_end': tag, 'built': datetime.date.today().isoformat(),
         'source_rows': len(rows), 'duplicates_merged': dupes, 'towing_removed': towing,
         'lane': summary(lane, {'cities': lane_cities}),
         'oregon': summary(bs, {'or_address': sum(1 for b in bs if b['principal_state'] == 'OR')})}
    os.makedirs(WEEKLY, exist_ok=True)
    with open(os.path.join(WEEKLY, f'{tag}.json'), 'w', encoding='utf-8') as fh:
        json.dump(s, fh, indent=1, ensure_ascii=False)
    print(f'week {start}..{end}: rows={len(rows)} businesses={len(bs)} lane={len(lane)} dupes_merged={dupes} towing={towing}')


if __name__ == '__main__':
    main()
