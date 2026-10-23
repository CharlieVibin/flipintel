# FlipIntel

**Automatic UK second-hand hardware deal discovery.** FlipIntel scans broad
hardware categories, identifies the exact model, values it against live UK
comparables, subtracts realistic selling costs and ranks only deals that still
clear a profit, ROI and confidence floor.

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688)
![React](https://img.shields.io/badge/React-18-61DAFB)
![SQLite](https://img.shields.io/badge/SQLite-local-003B57)

## What works

- **Category-first discovery.** The scanner browses 34 electronics category
  targets directly — 10 Vinted branches, 6 Gumtree branches and 18 eBay
  branches — with no search phrase required. Categories are scanned newest-first
  and tracked independently, with resumable backfills, adaptive 3–20 minute
  intervals, overlap stopping and a safety page. The 89-keyword phrase list is
  still used for two secondary searches per cycle to catch miscategorised items
  and valuation comparables, but it no longer limits what gets discovered.
- **Live collection** from Vinted UK and Gumtree UK with per-host rate limits,
  robots.txt checks, retries, circuit breakers and honest failure states.
- **Optional eBay UK** collection through the free official Browse API. Add
  developer keys to `.env`; no HTML scraping is used because eBay's robots
  policy prohibits it.
- **Automatic model identification** for GPUs, CPUs, memory, NVMe/SATA/2.5-inch/
  external SSDs, HDDs, motherboards, complete PCs, laptops, consoles, handhelds,
  monitors, phones and tablets. Generation, capacity, VRAM, interface and kit
  details are part of the product key where they are stated. Accessories, empty
  boxes, games, Kinect-style add-ons, faceplates, parts-only devices and
  wanted ads are excluded from deal recommendations; genuine console bundles
  remain identified as consoles.
- **Comparable-market valuation** that prefers recently completed sales,
  keeps sold and asking evidence separate, uses weighted medians/quartiles,
  trims outliers, caps confidence honestly and supports complete-PC component
  fallback. Asking-only confidence is capped at 52% (56% with a CeX anchor), and
  a single user-entered sale never becomes independent confirmation.
- **Incremental revaluation.** Each listing has a fingerprint of the fields that
  affect its valuation, and each product key has a signature of its comparable
  pool and sold evidence. A scan re-evaluates only new or changed listings and
  the keys whose market actually moved, which takes seconds instead of
  recomputing ~21k listings. A full reconciliation still runs every six hours
  and after any valuation-logic change, so aged evidence and recency decay are
  not ignored.
- **Evidence lab with bulk sold-price import**. Download a CSV template, upload
  completed sales with validation and a preview, and review imported, updated,
  skipped and rejected rows with reasons. Re-importing the same sale updates it
  instead of duplicating it. CSV export covers all user evidence or one product.
  Records keep explicit provenance: user-entered, user-imported, retail anchor or
  independently observed marketplace sale. Limits: 2 MB and 2,000 rows.
- **Verified sold evidence entry** remains available in the deal drawer for quick
  one-off additions; both paths write to the same evidence ledger.
- **Evidence-gated calibration**. Category asking-to-sold adjustments are fitted
  only from correctly matched products with enough recent asks and completed
  sales, then measured on held-out products. With insufficient or user-only
  evidence the app reports that calibration is unsupported and keeps the
  conservative 85% fallback instead of inventing a factor.
- **Resale ranges and conservative profit**. When evidence dispersion supports a
  range, the dashboard shows conservative, central and optimistic resale values
  plus profit at the conservative and central estimates; otherwise it says the
  range is unsupported.
- **Cost accounting**: purchase price, delivery/buyer fee, configurable resale
  channel, selling fees, outbound delivery, packaging, other costs and ROI.
- **Manual-review queue** for implausibly cheap items, including unusually
  cheap memory/storage. These are excluded from automatic recommendations and
  can be inspected through the dashboard's Review filter.
- **Cross-marketplace duplicate flags** for likely cross-posts. Original rows
  and URLs remain intact.
- **Dashboard** with ranked feed, filters (condition, confidence, marketplace,
  age), sorting, deal drawer, evidence, verified-sold import, images, direct
  listing links, saved/dismissed deals, review queue, scanner health, scan
  history and settings.
- **Notifications**: optional Discord webhook and browser notifications.
- **Single-user local app**: SQLite, no paid APIs, no cloud dependency.

## Quick start (Windows)

Requirements: Python 3.10+ and Node.js 20+ (Node is only needed to build the
dashboard).

1. Double-click `start.bat`.
2. Wait for dependencies to install and the dashboard to build.
3. FlipIntel opens at **http://127.0.0.1:8765**.
4. Press `Ctrl+C` in the terminal to stop it.

Manual start:

```bat
py -3 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
cd frontend
npm install
npm run build
cd ..
python run.py
```

Copy `.env.example` to `.env` to add optional credentials. Never commit `.env`.

## eBay UK

eBay does not permit HTML scraping and directs automated access to its API.
Create a free application at <https://developer.ebay.com/my/keys>, set:

```env
EBAY_CLIENT_ID=...
EBAY_CLIENT_SECRET=...
```

then restart FlipIntel. Until keys are present, the dashboard shows
**Needs API key** and never reports a fake successful scan.

## Marketplace status

| Source | Status | Notes |
| --- | --- | --- |
| Vinted UK | Working | Category browse verified on all 10 branches; 96 cards per page, newest first, marketplace caps pagination at page 10. |
| Gumtree UK | Working | Category browse verified on all 6 branches; 30 cards per page, newest first. Authoritative JSON-LD on search pages and per-card anchors on category pages. |
| eBay UK | Requires free API keys | Official Browse API only. Category search sends `category_ids` without a keyword, 200 items/page, 4 requests/cycle; not exercised live without keys. |
| Facebook Marketplace | Not available | Requires a signed-in session and prohibits automated collection; no listings are fabricated. |
| CeX UK | Often blocked | Used only as an optional price anchor. Cloudflare returned HTTP 403 in testing; valuations continue from live comparables. |

All collectors can be repaired independently under `app/collectors/`.

## How a deal is calculated

1. **Identify** the listing against hardware model patterns. Ambiguous items
   are kept for market tracking but not valued.
2. **Collect comparables** with the same canonical product key and kind.
   Completed sales are kept separate from asking prices. With two or more sold
   rows, the sold-only weighted median is used; with one, it is blended with
   asks and confidence is capped. Asking-only evidence uses a lower weighted
   quantile and a configurable `asking_to_sold_factor` (default 85%), never a
   plain average of wishful asks. If calibration has enough matched evidence,
   the validated category factor replaces the fallback for that category.
3. **Normalise** for condition, deduplicate evidence, trim outliers with
   weighted quartiles and a Tukey lower fence for asking pools, then estimate
   the realistic good-condition resale value. A single desperate or scam ask
   cannot drag the comparable market down.
4. **Subtract costs**: purchase price, delivery/buyer fee, configured resale
   channel fees, outbound shipping, packaging and other configured costs.
5. **Score confidence** from identification quality, sample size, price
   dispersion, evidence type and freshness. Asking-only valuations are capped
   at 52% (56% with a CeX anchor) and shown as unverified.
6. **Recommend only if all gates pass**: at least 4 comparables, 55% model
   match, minimum profit/ROI/confidence, not faulty/wanted/bundle, not flagged
   for manual review, and either sold evidence or a strong five-sample asking
   valuation. A listing priced far below the asking median, or missing key
   storage/memory/chip specs, goes to review instead of the automatic feed.
7. **Purge/refresh** valuations after every successful source scan, at startup
   and after threshold/evidence/review changes. A version counter prevents a
   refresh from being skipped when two sources scan seconds apart.
8. **Keep contents honest**: console-only, missing-controller/charger/cable and
   similar listings receive a 12% resale discount and a visible risk reason.
9. **Resolve review outcomes**: an approved listing can become eligible without
   any artificial confidence boost; a rejected listing stays out of the feed.

Defaults are editable in the Settings page: £20 minimum profit, 12% minimum
ROI, 42% minimum confidence, 13.22% eBay fee, £4.50 outbound delivery, £1
packaging, zero other costs and the eBay resale channel. Vinted and Facebook
can be selected as the resale channel when that is where the item will be sold.

## Architecture

```
app/
  collectors/        independent Vinted, Gumtree, eBay and Facebook modules
  identification.py  parser, generation keys and risk/accessory rules
  valuation.py       comparable engine, ranges, fees, scoring and deal gate
  calibration.py     category calibration, holdout validation and status
  evidence.py        CSV evidence validation, import/export and coverage
  categories.py      verified keyword-free category catalogue per marketplace
  scanner.py         scheduling, backfills, ingestion, dedupe, health
  pricing_cex.py     optional CeX anchor
  notifications.py   Discord/browser notification dispatch
  api.py             JSON API
  main.py            FastAPI app + static dashboard serving
  db.py              SQLite schema and additive migrations
frontend/            React + TypeScript + Tailwind dashboard
  pages/EvidencePage.tsx  evidence ledger, imports and calibration status
tests/               focused regression tests and saved review fixtures
run.py               local launcher
start.bat            Windows one-click launcher
```

Data lives in `data/rigradar.db` by default (ignored by Git). Existing
`RIGRADAR_*` environment names are retained for compatibility; the display
name is FlipIntel.

## Tests

```bat
.venv\Scripts\python.exe -m pytest -q
```

The suite currently has **69 passing tests**. It covers category catalogue and
parser regressions, resumable backfill depth, overlap stopping, coverage
diagnostics, eBay keyword-free category requests, CSV preview/import/
export, row and byte limits, optional fields, duplicate suppression, invalid
records, idempotent updates, formula-safe export, evidence API permissions and
corrections, calibration with sufficient and insufficient evidence, holdout
validation, resale ranges, confidence caps, manual evidence upsert/delete,
review approval/rejection/correction and deal promotion, real saved review-title
fixtures, console games versus consoles, faceplates/controllers/Kinect and
other accessory traps, complete PCs versus loose GPUs, compact Ryzen parsing,
phone storage and MacBook variant handling, Gumtree JSON-LD versus promoted
cards, Gumtree empty-result versus parser failure, deep-discount risk review,
SSD/HDD keys, bundle detection, eBay missing credentials and parser edge cases,
scan performance metrics, refresh invalidation, database migration
compatibility, duplicate detection, settings and the HTTP API smoke path.

## API

- `GET /api/health`, `/api/overview`, `/api/deals`, `/api/deals/{id}`
- `POST /api/deals/{id}/save`, `/dismiss`, `/restore`
- `GET /api/scans`, `/api/sources`, `/api/settings`
- `GET /api/comps?product_key=...` — sold/asking evidence and summary
- `POST /api/comps` — add a verified sold comparable
- `DELETE /api/comps/{id}` — remove a manual comparable
- `GET /api/evidence` — browse saved completed sales with filters
- `GET /api/evidence/coverage` — per-product evidence strength and import history
- `GET /api/evidence/template` — downloadable CSV template
- `GET /api/evidence/export?product_key=...&source=user` — CSV export
- `POST /api/evidence/import` — validate/preview or commit a CSV (`commit=false|true`)
- `PATCH /api/evidence/{id}` — correct user-supplied evidence
- `DELETE /api/evidence/{id}` — delete user-supplied evidence
- `GET /api/calibration` — calibration status, factors and thresholds
- `PUT /api/settings`
- `POST /api/scan`

Interactive docs: <http://127.0.0.1:8765/docs>

## Known limitations

- **Vinted exposes only 10 pages per category branch.** Large branches are
  continuously covered at the newest edge, but are not exhaustively crawled;
  the Scanner page marks capped branches `partial` and small completed branches
  `full`. **eBay category browsing is implemented from the documented Browse API
  contract but has not been exercised live here** because no developer keys are
  configured; the dashboard labels it `unverified`. No eBay HTML is scraped.
- Category pages deliberately favour recall, so they include accessories, media
  and occasional off-category items. Identification, risk and deal gates still
  filter those; category discovery does not bypass them.
- No free, reliable automated UK sold-price feed is available. The engine now
  supports bulk and one-off sold evidence and only calibrates when the database
  contains enough matched sales, but the current local database has no saved
  sold evidence yet. Most live valuations therefore still start from asking
  prices and are capped at 52% confidence and labelled unverified.
- Calibration requires at least 2 sold observations and 4 recent asks for a
  product, at least 5 products per category, and held-out error under 15%. Until
  then the conservative fallback remains active and the Evidence page explains
  exactly what is missing.
- eBay completed-sales pages are not scraped: eBay directs automated access to
  its API, and the Browse API only exposes active listings. The Marketplace
  Insights API, which reports sold items, is restricted and not open to new
  users (it is also unavailable in some marketplaces); no CAPTCHA, anti-bot or
  paywall bypass is implemented. Genuine sold evidence therefore comes from
  optional CSV/one-off entry until approved API access exists.
- Gumtree promoted cards are deliberately excluded. They are not part of the
  JSON-LD ItemList and previously caused the wrong URL to be paired with a
  price. The full canonical listing is used instead.
- CeX is frequently blocked by Cloudflare. When unavailable, it is reported as
  blocked rather than silently ignored.
- Facebook Marketplace is intentionally unsupported.
- Model parsing is rule-based. It is conservative, but unusual titles can still
  be missed or grouped too broadly (especially games without a known title and
  generic drives that omit interface or capacity). Ambiguous items are kept for
  review rather than valued as deals.
- eBay has not been exercised end-to-end in this environment because no
  developer credentials are configured. Missing credentials are reported as
  `not_configured`; the token, quota, parser and authentication error paths are
  tested, and the Settings connection test performs a real API probe once keys
  are saved.
- A local single-process SQLite app, not a multi-user hosted service.

## Source reuse and licences

The implementation is original, informed by the MIT-licensed
[Simple-Python-Scrapy-Scrapers/ebay-scrapy-scraper](https://github.com/Simple-Python-Scrapy-Scrapers/ebay-scrapy-scraper)
and [samWidlund/resell-helper](https://github.com/samWidlund/resell-helper)
projects. No Scrapy runtime is required. Marketplace HTML/API formats can
change without notice; collectors are isolated so each can be repaired alone.

## Operational notes

- Respect each marketplace's terms and robots.txt. `RESPECT_ROBOTS=true` is
  the default and should stay enabled unless you have written permission.
- The scanner uses a descriptive user agent, conservative pacing and a circuit
  breaker. Do not lower intervals below the configured minimums.
- Back up `data/rigradar.db` if you care about saved deals and price history.
