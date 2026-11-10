#!/usr/bin/env python3
"""Lane County / Oregon New Business List: free public teaser site generator.

Run:  python3 build.py            (fetch the latest month from data.oregon.gov, rebuild docs/)
      python3 build.py --rebuild  (re-render docs/ from saved summaries only, no fetch)

Unattended monthly flow: fetch the state file -> if its month is new, save a PII-free
summary to summaries/YYYY-MM.json -> re-render every page (latest + all archive months)
into docs/ -> the caller commits and pushes. Safe to run daily: if nothing changed,
the output is byte-identical and there is nothing to commit.

Privacy: only counts and a masked 10-row sample per page are saved or published.
Owner names and street addresses never leave this script unmasked.
Rule: towing / roadside businesses are dropped from every count, sample and page.
Standard library only.
"""
import csv, datetime, html, json, os, re, sys, urllib.request, collections, io

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lane_rules import LANE_ZIPS, NON_LANE_CITIES, category_guess, is_towing

HERE = os.path.dirname(os.path.abspath(__file__))
SUMMARIES = os.path.join(HERE, 'summaries')
OUT = os.path.join(HERE, 'docs')

# ---------- config ----------
BASE_URL = os.environ.get('SITE_BASE_URL', 'https://zcloud8891.github.io/new-business-list').rstrip('/')
BASE_PATH = re.sub(r'^https?://[^/]+', '', BASE_URL)  # '' when served at a domain root
LANE_URL = 'https://buy.stripe.com/cNi3cw2gs1aydlv3iQfIs01'
OREGON_URL = 'https://buy.stripe.com/5kQ14odZa1ay81b5qYfIs02'
CANCEL_URL = os.environ.get('CANCEL_URL', '')  # Stripe customer-portal link, shown in the FAQ when set
DATA_URL = 'https://data.oregon.gov/resource/esjy-u4fc.json?$limit=50000&$offset={}&$order=:id'
DATASET_PAGE = 'https://data.oregon.gov/business/New-Businesses-Registered-Last-Month/esjy-u4fc'
MIN_CITY = 3
SAMPLE_N = 10
# extra caution for the public sample only (counts use the shared TOWING rule so they match the Sheets)
SAMPLE_BLOCK = re.compile(r"\b(TOW|TOWING|ROADSIDE|WRECKER|WRECKERS|FLATBED|RECOVERY|JUMP ?START|LOCKOUT)\b")
ROLES = ['MEMBER', 'MANAGER', 'PRESIDENT', 'SECRETARY', 'REGISTRANT', 'AUTHORIZED REPRESENTATIVE', 'PARTNER',
         'GENERAL PARTNER', 'TRUSTEE', 'INDIVIDUAL WITH DIRECT KNOWLEDGE']

# ---------- data ----------
def fetch_rows():
    rows, off = [], 0
    while True:
        req = urllib.request.Request(DATA_URL.format(off), headers={'User-Agent': 'new-business-list-builder'})
        page = json.load(urllib.request.urlopen(req, timeout=180))
        rows += page
        if len(page) < 50000:
            break
        off += 50000
    if not rows:
        raise SystemExit('state file came back empty')
    return rows

def pname(r):
    return ' '.join(x for x in [r.get('first_name'), r.get('middle_name'), r.get('last_name'), r.get('suffix')] if x).strip() \
        or r.get('not_of_record_entity') or r.get('entity_of_record_name') or ''

def tcase(s):
    return re.sub(r"(^|[^a-z'])([a-z])", lambda m: m.group(1) + m.group(2).upper(), (s or '').lower())

def businesses(rows):
    groups = collections.OrderedDict()
    for r in rows:
        groups.setdefault(r['registry_number'], []).append(r)
    out = []
    for reg, g in groups.items():
        f = g[0]
        loc = next((r for r in g if r.get('associated_name_type') == 'PRINCIPAL PLACE OF BUSINESS'), None)
        src = 'principal'
        if not loc:
            loc = next((r for r in g if r.get('associated_name_type') == 'MAILING ADDRESS'), None) or {}
            src = 'mailing' if loc else 'none'
        people = []
        for role in ROLES:
            for r in g:
                if r.get('associated_name_type') == role and pname(r) and pname(r) not in people:
                    people.append(pname(r))
        name = f.get('business_name', '')
        out.append({
            'name': name, 'type': tcase(f.get('entity_type', '')), 'date': (f.get('registry_date') or '')[:10],
            'city': tcase(loc.get('city', '')), 'state': loc.get('state', ''), 'zip': (loc.get('zip_code') or '')[:5],
            'street': ' '.join(x for x in [loc.get('address_'), loc.get('address_continued')] if x), 'src': src,
            'people': people, 'cat': category_guess(name, f.get('entity_type', '')), 'towing': is_towing(name),
        })
    return out

def is_lane(b):
    return b['state'] == 'OR' and b['zip'] in LANE_ZIPS and b['city'].upper() not in NON_LANE_CITIES

def slug(s):
    return re.sub(r'[^a-z0-9]+', '-', s.lower()).strip('-')

def mask_person(n):
    parts = [p for p in re.split(r'\s+', n) if p]
    return ' '.join(p[0] + '.' for p in parts[:3]) if parts else ''

def sample(rows, n=SAMPLE_N):
    pool = [b for b in rows if not SAMPLE_BLOCK.search(b['name'].upper())]
    pool.sort(key=lambda b: (b['date'], b['name']), reverse=True)
    by_cat = collections.OrderedDict()
    for b in pool:
        by_cat.setdefault(b['cat'], []).append(b)
    named = [c for c in by_cat if c != 'Other / unclear'] + (['Other / unclear'] if 'Other / unclear' in by_cat else [])
    picked = []
    while len(picked) < min(n, len(pool)):
        for c in named:
            if by_cat[c] and len(picked) < n:
                picked.append(by_cat[c].pop(0))
    picked.sort(key=lambda b: (b['date'], b['name']))
    return [{'name': b['name'], 'type': b['type'], 'date': b['date'], 'city': b['city'], 'state': b['state'],
             'cat': b['cat'], 'owner': mask_person(b['people'][0]) if b['people'] else '',
             'has_street': bool(b['street'])} for b in picked]

def counts(rows, key):
    return collections.Counter(b[key] for b in rows).most_common()

def summarize(rows):
    allb = [b for b in businesses(rows)]
    month = max(b['date'][:7] for b in allb)
    removed_all = sum(b['towing'] for b in allb)
    allb = [b for b in allb if not b['towing']]
    lane = [b for b in allb if is_lane(b)]
    orb = [b for b in allb if b['state'] == 'OR']
    s = {
        'month': month, 'built': datetime.date.today().isoformat(),
        'first_date': min(b['date'] for b in allb), 'last_date': max(b['date'] for b in allb),
        'towing_removed_statewide': removed_all,
        'oregon': {'total': len(allb), 'or_address': len(orb), 'out_of_state': len(allb) - len(orb),
                   'cities': counts(orb, 'city')[:25], 'cats': counts(allb, 'cat'), 'types': counts(allb, 'type')[:8],
                   'sample': sample(orb)},
        'lane': {'total': len(lane), 'cities': counts(lane, 'city'), 'cats': counts(lane, 'cat'),
                 'types': counts(lane, 'type')[:8], 'with_people': sum(1 for b in lane if b['people']),
                 'principal': sum(1 for b in lane if b['src'] == 'principal'), 'sample': sample(lane)},
        'city': {},
    }
    for c, n in s['lane']['cities']:
        if n >= MIN_CITY:
            cr = [b for b in lane if b['city'] == c]
            s['city'][c] = {'total': n, 'cats': counts(cr, 'cat'), 'types': counts(cr, 'type')[:6], 'sample': sample(cr)}
    return s

# ---------- rendering ----------
E = lambda x: html.escape(str(x), quote=True)
def fmt(n): return f'{n:,}'
def month_name(m): return datetime.date(int(m[:4]), int(m[5:7]), 1).strftime('%B %Y')
def url(path): return BASE_URL + path
def href(path): return BASE_PATH + path

CSS = """
:root{--navy:#1F2A44;--navy2:#2c3b5e;--gold:#F2C14E;--ink:#1b1f29;--mute:#5a6275;--bg:#fbfaf6;--line:#e4e1d8}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;font:16px/1.55 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:var(--ink);background:var(--bg)}
a{color:var(--navy2)}header.top{background:var(--navy);color:#fff}header.top .w{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:12px 16px}
header.top a{color:#fff;text-decoration:none;font-weight:700}header.top nav a{font-weight:500;margin-left:14px;font-size:.95rem;opacity:.9}
.w{max-width:900px;margin:0 auto;padding:0 16px}
.hero{background:var(--navy);color:#fff;padding:28px 0 32px;border-bottom:4px solid var(--gold)}
.hero h1{font-size:1.65rem;line-height:1.2;margin:0 0 10px}.hero p{margin:0 0 16px;color:#dfe4ef}
.btn{display:inline-block;background:var(--gold);color:var(--navy);font-weight:800;padding:14px 20px;border-radius:10px;text-decoration:none;font-size:1.05rem;margin:4px 8px 4px 0}
.btn.alt{background:transparent;color:#fff;border:2px solid var(--gold)}
.btn.dark{background:var(--navy);color:#fff}
section{padding:22px 0;border-bottom:1px solid var(--line)}h2{font-size:1.25rem;margin:0 0 12px;color:var(--navy)}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px}
.stat{background:#fff;border:1px solid var(--line);border-radius:10px;padding:12px}.stat b{display:block;font-size:1.5rem;color:var(--navy)}
.stat span{color:var(--mute);font-size:.9rem}
.tbl{width:100%;border-collapse:collapse;background:#fff;font-size:.92rem}.tbl th,.tbl td{text-align:left;padding:8px;border-bottom:1px solid var(--line);vertical-align:top}
.tbl th{background:#f1efe7;color:var(--navy)}.tbl td.n{text-align:right;font-variant-numeric:tabular-nums}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch;border:1px solid var(--line);border-radius:10px}
.mask{color:var(--mute);letter-spacing:.5px}.lock{background:#fff8e3;border:1px dashed var(--gold);border-radius:10px;padding:14px;margin-top:12px}
.bars div{display:flex;align-items:center;gap:8px;margin:4px 0;font-size:.92rem}.bars i{display:block;height:10px;background:var(--gold);border-radius:5px;min-width:2px}
.bars span.l{flex:0 0 46%}.bars span.c{flex:0 0 3.5em;text-align:right;font-variant-numeric:tabular-nums}
ul.who{padding-left:20px;margin:0}ul.who li{margin:4px 0}details{background:#fff;border:1px solid var(--line);border-radius:10px;padding:10px 14px;margin:8px 0}
summary{font-weight:700;cursor:pointer;color:var(--navy)}.small{font-size:.85rem;color:var(--mute)}
.cities{display:flex;flex-wrap:wrap;gap:8px;padding:0;list-style:none}.cities a{display:block;background:#fff;border:1px solid var(--line);border-radius:8px;padding:6px 10px;text-decoration:none}
footer{padding:24px 0 40px;color:var(--mute);font-size:.88rem}
header.top nav{white-space:nowrap}header.top nav a{margin-left:10px;font-size:.88rem}
@media(max-width:640px){.smp thead{display:none}.smp tr{display:block;border-bottom:2px solid var(--line);padding:6px 0}.smp td{display:flex;gap:10px;border:0;padding:3px 10px}
.smp td:before{content:attr(data-l);flex:0 0 32%;color:var(--mute);font-size:.85rem}header.top .brand{font-size:.95rem}}
@media(min-width:700px){.hero h1{font-size:2.1rem}}
"""

def bars(pairs, total):
    mx = max([n for _, n in pairs] or [1])
    return '<div class="bars">' + ''.join(
        f'<div><span class="l">{E(k)}</span><i style="width:{max(2, round(40 * n / mx))}%"></i><span class="c">{fmt(n)}</span></div>'
        for k, n in pairs) + '</div>'

def sample_table(rows, area_word):
    if not rows:
        return ''
    trs = ''.join(
        f"<tr><td data-l='Business'><b>{E(r['name'])}</b></td><td data-l='Type'>{E(r['type'])}</td><td data-l='Filed'>{E(r['date'])}</td><td data-l='City'>{E(r['city'])}</td><td data-l='Category*'>{E(r['cat'])}</td>"
        f"<td class=mask data-l='Owner'>{E(r['owner']) + ' •••••' if r['owner'] else '—'}</td><td class=mask data-l='Street'>{'•••• ••••• (in full list)' if r['has_street'] else '—'}</td></tr>"
        for r in rows)
    return (f'<div class="scroll"><table class="tbl smp"><thead><tr><th>Business</th><th>Type</th><th>Filed</th><th>City</th>'
            f'<th>Category*</th><th>Owner / officer</th><th>Street address</th></tr></thead><tbody>{trs}</tbody></table></div>'
            f'<p class="small">A sample of {len(rows)} {area_word} filings. Owner names and street addresses are masked here; '
            f'the full list shows them as filed with the state. *Category is our best guess from the business name.</p>')

WHO = """<section><div class="w"><h2>Who this is for</h2><p>Brand-new owners need nearly everything in their first month. If you sell any of this, these are your warmest leads:</p>
<ul class="who"><li><b>Insurance agents</b>: liability, workers' comp, commercial auto</li><li><b>Bookkeepers and tax preparers</b>: books, payroll, first-year filings</li>
<li><b>Web designers</b>: a site, a Google Business Profile, a logo</li><li><b>Sign shops and printers</b>: storefront signs, vehicle wraps, cards</li>
<li><b>Banks and credit unions</b>: business checking, merchant services, loans</li><li><b>Commercial cleaners</b>: offices, shops and restaurants that just opened</li></ul></div></section>"""

def compliance():
    return """<section><div class="w"><h2>A reminder before you reach out</h2>
<p>Many new owners are sole proprietors using a home address and a personal cell phone. If you email them, follow <a href="https://www.ftc.gov/business-guidance/resources/can-spam-act-compliance-guide-business" rel="nofollow">CAN-SPAM</a>: honest subject lines, your postal address, and an opt-out you honor within 10 business days. If you call or text, the <a href="https://www.fcc.gov/general/telemarketing-and-robocalls" rel="nofollow">TCPA</a> bans autodialed, prerecorded or AI-voice marketing to cell phones without prior written consent, and the <a href="https://www.donotcall.gov/" rel="nofollow">Do Not Call Registry</a> can apply. Some owners have asked the state not to disclose a home address under ORS 192.445. Please respect that. Mail and an honest, useful offer work best anyway.</p></div></section>"""

def faq_items(tier, price, month):
    items = [
        ('What exactly do I get?', f'A Google Sheet with every {tier} business that registered with the Oregon Secretary of State in {month_name(month)}: business name, entity type, filing date, street address, city and ZIP, registered agent, and the owners or officers named in the filing, plus a category guess. Right after checkout you\'re sent straight to the Sheet.'),
        ('How often does it update?', 'Monthly. The state posts last month\'s filings somewhere between the 5th and the 17th of each month. A script checks every day, and a new tab for the new month shows up on its own, usually the same day the state posts it.'),
        ('How much is it?', f'${price} a month. Cancel anytime.' + (f' Manage or cancel your plan <a href="{E(CANCEL_URL)}">here</a>.' if CANCEL_URL else '')),
        ('Are phone numbers and emails included?', 'No. The state filing doesn\'t include them, and we don\'t make them up. You get what\'s on the public record: names, addresses and filing details.'),
        ('Where does the data come from?', f'The Oregon Secretary of State\'s public business registry, published on <a href="{DATASET_PAGE}" rel="nofollow">data.oregon.gov</a> as "New Businesses Registered Last Month." Oregon law puts this data in the public domain (ORS 276A.368). We clean it, sort it by Lane County ZIP code, and add categories.'),
        ('Is it complete and accurate?', 'It\'s as good as the state\'s file, and the state doesn\'t guarantee it, so neither can we. The list is provided as-is. Categories are a guess from the business name. A few businesses only list a mailing address.'),
        ('Why no towing companies?', 'We leave towing and roadside-assistance businesses out of every list on purpose.'),
    ]
    return items

def faq_html(items):
    return '<section><div class="w"><h2>Questions</h2>' + ''.join(f'<details><summary>{E(q)}</summary><p>{a}</p></details>' for q, a in items) + '</div></section>'

def faq_ld(items):
    return {'@type': 'FAQPage', 'mainEntity': [{'@type': 'Question', 'name': q, 'acceptedAnswer': {'@type': 'Answer', 'text': re.sub('<[^>]+>', '', a)}} for q, a in items]}

def product_ld(name, desc, price, buy, page_url):
    return {'@type': 'Product', 'name': name, 'description': desc, 'brand': {'@type': 'Brand', 'name': name}, 'url': page_url,
            'category': 'Business lead lists',
            'offers': {'@type': 'Offer', 'price': f'{price}.00', 'priceCurrency': 'USD', 'availability': 'https://schema.org/InStock',
                       'url': buy, 'priceSpecification': {'@type': 'UnitPriceSpecification', 'price': f'{price}.00', 'priceCurrency': 'USD',
                                                          'billingDuration': 1, 'billingIncrement': 1, 'unitCode': 'MON'}}}

def page(path, title, desc, canonical, body, ld, noindex=False):
    ldj = json.dumps({'@context': 'https://schema.org', '@graph': ld}, ensure_ascii=False).replace('</', '<\\/')
    robots = '<meta name="robots" content="noindex,follow">' if noindex else ''
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{E(title)}</title><meta name="description" content="{E(desc)}"><link rel="canonical" href="{E(canonical)}">{robots}
<meta property="og:title" content="{E(title)}"><meta property="og:description" content="{E(desc)}"><meta property="og:type" content="website"><meta property="og:url" content="{E(canonical)}">
<meta name="theme-color" content="#1F2A44"><style>{CSS}</style><script type="application/ld+json">{ldj}</script></head><body>
<header class="top"><div class="w"><a class="brand" href="{href('/')}">New Business List</a><nav><a href="{href('/lane-county/')}">Lane County</a><a href="{href('/oregon/')}">Oregon</a><a href="{href('/archive/')}">Archive</a></nav></div></header>
{body}
<footer><div class="w"><p>Source: Oregon Secretary of State public business registry records, via <a href="{DATASET_PAGE}" rel="nofollow">data.oregon.gov</a> ("New Businesses Registered Last Month"), public domain under ORS 276A.368. Provided as-is; the state does not warrant completeness or accuracy, and neither do we. Towing and roadside businesses are excluded.</p>
<p>Lane County New Business List and Oregon New Business List are run by a dad in Veneta, Oregon. Payments are handled securely by Stripe.</p></div></footer></body></html>
"""
    dest = os.path.join(OUT, path.strip('/'), 'index.html') if path.strip('/') else os.path.join(OUT, 'index.html')
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, 'w', encoding='utf-8') as fh:
        fh.write(doc)

def hero(h1, lead, price, buy, alt=None):
    a = f'<a class="btn alt" href="{E(alt[1])}">{E(alt[0])}</a>' if alt else ''
    return f'<div class="hero"><div class="w"><h1>{E(h1)}</h1><p>{lead}</p><a class="btn" href="{E(buy)}">Get the full list: ${price}/mo</a>{a}</div></div>'

def buy_box(price, buy, n, area):
    return f'<div class="lock"><b>{fmt(n)} {E(area)} businesses in the full list.</b> See every one, with owners/officers and street addresses as filed. <br><a class="btn dark" href="{E(buy)}">Unlock all {fmt(n)} for ${price}/mo</a></div>'

def city_links(s, m, current=None):
    lis = ''.join(f'<li><a href="{href("/" + slug(c) + "/" + ("" if current else m + "/"))}">{E(c)} ({fmt(v["total"])})</a></li>' for c, v in s['city'].items())
    return f'<ul class="cities">{lis}</ul>'

def archive_note(m, latest):
    if m == latest:
        return ''
    return f'<div class="w"><p class="lock">This is the archived {month_name(m)} page. <a href="{{LATEST}}">See the latest month</a>.</p></div>'

def render_month(s, latest, months):
    m = s['month']; mn = month_name(m); is_latest = (m == latest)
    targets = [('', True)] if False else []
    # (path, canonical) pairs: latest pages at /x/, month pages at /x/YYYY-MM/
    def both(sub):
        res = [(f'/{sub}{m}/', url(f'/{sub}{m}/') if not is_latest else url(f'/{sub}'))]
        if is_latest:
            res.append((f'/{sub}', url(f'/{sub}')))
        return res
    L, O = s['lane'], s['oregon']
    lane_faq, or_faq = faq_items('Lane County', 9, m), faq_items('Oregon', 19, m)
    # Lane County page
    for path, canon in both('lane-county/'):
        title = f'New businesses in Lane County, Oregon — {mn} ({fmt(L["total"])})'
        desc = f'{fmt(L["total"])} new businesses registered in Lane County, Oregon in {mn}. See counts by city and category and a free sample. Full list with owners and addresses: $9/mo.'
        body = hero(title, f'Every new business that registered with the Oregon Secretary of State in {mn} with a Lane County address: {fmt(L["total"])} in all, from Eugene and Springfield to Florence and Oakridge.', 9, LANE_URL, ('All of Oregon: $19/mo', OREGON_URL))
        body += archive_note(m, latest).replace('{LATEST}', href('/lane-county/'))
        body += f'''<section><div class="w"><h2>{mn} at a glance</h2><div class="stats"><div class="stat"><b>{fmt(L["total"])}</b><span>new Lane County businesses</span></div>
<div class="stat"><b>{fmt(len(L["cities"]))}</b><span>towns and cities</span></div><div class="stat"><b>{fmt(L["with_people"])}</b><span>with an owner or officer named</span></div>
<div class="stat"><b>{fmt(L["principal"])}</b><span>with a principal business address</span></div></div></div></section>
<section><div class="w"><h2>New businesses by city</h2>{bars(L["cities"], L["total"])}<p class="small">City pages (3 or more filings):</p>{city_links(s, m, is_latest and path == "/lane-county/")}</div></section>
<section><div class="w"><h2>By category</h2>{bars(L["cats"], L["total"])}<p class="small">Category is a guess from the business name, so about half land in "Other / unclear."</p></div></section>
<section><div class="w"><h2>Free sample</h2>{sample_table(L["sample"], "Lane County")}{buy_box(9, LANE_URL, L["total"], "Lane County")}</div></section>
{WHO}{faq_html(lane_faq)}{compliance()}'''
        ld = [product_ld('Lane County New Business List', 'Monthly Google Sheet of every new business registered in Lane County, Oregon, from Oregon Secretary of State public records.', 9, LANE_URL, canon), faq_ld(lane_faq)]
        page(path, title, desc, canon, body, ld)
    # Oregon page
    for path, canon in both('oregon/'):
        title = f'New businesses in Oregon — {mn} ({fmt(O["total"])})'
        desc = f'{fmt(O["total"])} new business registrations with the Oregon Secretary of State in {mn}. Counts by city and category, a free sample, and the full statewide list for $19/mo.'
        body = hero(title, f'Every business that registered with the Oregon Secretary of State in {mn}: {fmt(O["total"])} filings. {fmt(O["or_address"])} list an Oregon address; {fmt(O["out_of_state"])} are out-of-state companies registering to do business here.', 19, OREGON_URL, ('Just Lane County: $9/mo', LANE_URL))
        body += archive_note(m, latest).replace('{LATEST}', href('/oregon/'))
        body += f'''<section><div class="w"><h2>{mn} at a glance</h2><div class="stats"><div class="stat"><b>{fmt(O["total"])}</b><span>new registrations statewide</span></div>
<div class="stat"><b>{fmt(O["or_address"])}</b><span>with an Oregon address</span></div><div class="stat"><b>{fmt(L["total"])}</b><span>in Lane County</span></div></div></div></section>
<section><div class="w"><h2>Top 25 Oregon cities</h2>{bars(O["cities"], O["or_address"])}</div></section>
<section><div class="w"><h2>By category</h2>{bars(O["cats"], O["total"])}<p class="small">Category is a guess from the business name.</p></div></section>
<section><div class="w"><h2>Free sample</h2>{sample_table(O["sample"], "Oregon")}{buy_box(19, OREGON_URL, O["total"], "Oregon")}</div></section>
{WHO}{faq_html(or_faq)}{compliance()}'''
        ld = [product_ld('Oregon New Business List', 'Monthly Google Sheet of every new business registered with the Oregon Secretary of State, statewide.', 19, OREGON_URL, canon), faq_ld(or_faq)]
        page(path, title, desc, canon, body, ld)
    # City pages
    for c, v in s['city'].items():
        for path, canon in both(slug(c) + '/'):
            title = f'New businesses in {c}, Oregon — {mn} ({fmt(v["total"])})'
            desc = f'{fmt(v["total"])} new businesses registered in {c}, Oregon in {mn}. Free sample and category counts. Every new Lane County business, {c} included, for $9/mo.'
            body = hero(title, f'{fmt(v["total"])} new businesses with a {E(c)} address registered with the Oregon Secretary of State in {mn}. They\'re part of {fmt(L["total"])} new businesses across Lane County, and the $9 list includes all of them.', 9, LANE_URL)
            body += archive_note(m, latest).replace('{LATEST}', href('/' + slug(c) + '/'))
            body += f'''<section><div class="w"><h2>{E(c)} by category</h2>{bars(v["cats"], v["total"])}<p class="small">Category is a guess from the business name.</p></div></section>
<section><div class="w"><h2>By entity type</h2>{bars(v["types"], v["total"])}</div></section>
<section><div class="w"><h2>Free sample</h2>{sample_table(v["sample"], c)}{buy_box(9, LANE_URL, L["total"], "Lane County")}</div></section>
<section><div class="w"><h2>Other Lane County cities in {mn}</h2>{city_links(s, m, is_latest and path == "/" + slug(c) + "/")}</div></section>
{WHO}{faq_html(lane_faq)}{compliance()}'''
            ld = [product_ld('Lane County New Business List', f'Monthly Google Sheet of every new business registered in Lane County, Oregon, including {c}.', 9, LANE_URL, canon), faq_ld(lane_faq)]
            page(path, title, desc, canon, body, ld)

def render_home(s, months):
    m = s['month']; mn = month_name(m); L, O = s['lane'], s['oregon']
    h1 = f'{fmt(L["total"])} new Lane County businesses registered in {mn}. Get every one for $9 a month.'
    title = f'Lane County & Oregon New Business Lists — {mn}'
    desc = f'{fmt(L["total"])} new Lane County businesses and {fmt(O["total"])} statewide registered in {mn}. Monthly lists from Oregon Secretary of State records, $9/mo Lane, $19/mo all Oregon.'
    body = hero(h1, f'All of Oregon is $19 a month ({fmt(O["total"])} filings in {mn}). It\'s built from the state\'s own records by a dad in Veneta, and a fresh Google Sheet shows up on its own every month.', 9, LANE_URL, ('All of Oregon: $19/mo', OREGON_URL))
    body += f'''<section><div class="w"><h2>{mn} at a glance</h2><div class="stats"><div class="stat"><b>{fmt(L["total"])}</b><span>Lane County</span></div>
<div class="stat"><b>{fmt(O["total"])}</b><span>all Oregon</span></div>
{''.join(f'<div class="stat"><b>{fmt(n)}</b><span>{E(c)}</span></div>' for c, n in L["cities"][:4])}</div></div></section>
<section><div class="w"><h2>Pick your list</h2><p><b>Lane County New Business List: $9/mo.</b> Eugene, Springfield, Florence, Cottage Grove, Veneta and every other Lane County town. <a href="{href('/lane-county/')}">See {mn}</a></p>
<p><b>Oregon New Business List: $19/mo.</b> Every new registration in the state. <a href="{href('/oregon/')}">See {mn}</a></p>
<p class="small">Lane County city pages:</p>{city_links(s, m, True)}</div></section>
<section><div class="w"><h2>Free sample from Lane County</h2>{sample_table(L["sample"][:5], "Lane County")}{buy_box(9, LANE_URL, L["total"], "Lane County")}</div></section>
{WHO}{faq_html(faq_items('Lane County', 9, m))}{compliance()}'''
    ld = [{'@type': 'WebSite', 'name': 'New Business List', 'url': url('/')},
          product_ld('Lane County New Business List', 'Monthly Google Sheet of every new business registered in Lane County, Oregon.', 9, LANE_URL, url('/lane-county/')),
          product_ld('Oregon New Business List', 'Monthly Google Sheet of every new business registered in Oregon.', 19, OREGON_URL, url('/oregon/')),
          faq_ld(faq_items('Lane County', 9, m))]
    page('/', title, desc, url('/'), body, ld)

def render_archive(sums):
    rows = ''.join(f'<tr><td>{month_name(m)}</td><td class=n><a href="{href("/lane-county/" + m + "/")}">{fmt(s["lane"]["total"])}</a></td><td class=n><a href="{href("/oregon/" + m + "/")}">{fmt(s["oregon"]["total"])}</a></td></tr>'
                   for m, s in sorted(sums.items(), reverse=True))
    title = 'Oregon new business counts by month: archive'
    body = f'<div class="hero"><div class="w"><h1>{E(title)}</h1><p>Every month we\'ve published, with Lane County and statewide totals.</p><a class="btn" href="{LANE_URL}">Lane County: $9/mo</a><a class="btn alt" href="{OREGON_URL}">All Oregon: $19/mo</a></div></div>'
    body += f'<section><div class="w"><div class="scroll"><table class="tbl"><thead><tr><th>Month</th><th>Lane County</th><th>All Oregon</th></tr></thead><tbody>{rows}</tbody></table></div></div></section>'
    page('/archive/', title, 'Monthly counts of new business registrations in Lane County and Oregon, from Oregon Secretary of State records.', url('/archive/'), body, [{'@type': 'CollectionPage', 'name': title}])

def render_all(sums):
    import shutil
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT)
    # keep Google Search Console verification + Jekyll opt-out across rebuilds
    open(os.path.join(OUT, 'google4693936bfde5db7c.html'), 'w').write('google-site-verification: google4693936bfde5db7c.html\n')
    open(os.path.join(OUT, '.nojekyll'), 'w').write('')
    latest = max(sums)
    for m, s in sorted(sums.items()):
        render_month(s, latest, sorted(sums))
    render_home(sums[latest], sorted(sums))
    render_archive(sums)
    # sitemap: latest pages + past-month archives (the current-month archive copy canonicals to latest)
    s = sums[latest]
    locs = ['/', '/lane-county/', '/oregon/', '/archive/'] + [f'/{slug(c)}/' for c in s['city']]
    for m, sm in sorted(sums.items()):
        if m != latest:
            locs += [f'/lane-county/{m}/', f'/oregon/{m}/'] + [f'/{slug(c)}/{m}/' for c in sm['city']]
    lastmod = s['built']
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + \
        ''.join(f'<url><loc>{E(url(p))}</loc><lastmod>{lastmod}</lastmod></url>\n' for p in locs) + '</urlset>\n'
    open(os.path.join(OUT, 'sitemap.xml'), 'w').write(xml)
    open(os.path.join(OUT, 'robots.txt'), 'w').write(f'User-agent: *\nAllow: /\nSitemap: {url("/sitemap.xml")}\n')
    open(os.path.join(OUT, '.nojekyll'), 'w').write('')
    open(os.path.join(OUT, '404.html'), 'w').write(f'<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>Not found</title><p style="font-family:system-ui;padding:24px">Page not found. <a href="{href("/")}">Go to the latest new business list</a>.</p>')
    n = sum(1 for _, _, f in os.walk(OUT) for x in f if x == 'index.html')
    print(f'rendered {n} pages for months {sorted(sums)} (latest {latest}) into {OUT}')

def load_sums():
    sums = {}
    if os.path.isdir(SUMMARIES):
        for f in sorted(os.listdir(SUMMARIES)):
            if re.fullmatch(r'\d{4}-\d{2}\.json', f):
                sums[f[:7]] = json.load(open(os.path.join(SUMMARIES, f)))
    return sums

def main():
    os.makedirs(SUMMARIES, exist_ok=True)
    sums = load_sums()
    if '--rebuild' not in sys.argv:
        rows = fetch_rows()
        month = max((r.get('registry_date') or '')[:7] for r in rows)
        if month in sums and '--force' not in sys.argv:
            print(f'{month} already summarized; re-rendering only')
        else:
            s = summarize(rows)
            with open(os.path.join(SUMMARIES, f'{month}.json'), 'w') as fh:
                json.dump(s, fh, indent=1, ensure_ascii=False)
            sums[month] = s
            print(f'{month}: lane={s["lane"]["total"]} oregon={s["oregon"]["total"]} cities={len(s["city"])}')
    if not sums:
        raise SystemExit('no summaries yet')
    render_all(sums)

if __name__ == '__main__':
    main()
