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

- **Category-first discovery.** The scanner browses 28 hardware category
  targets directly — 15 Vinted device subcategories, 3 Gumtree hardware
  categories and 10 eBay hardware branches — with no search phrase required.
  The catalogue deliberately targets device branches rather than parent
  "accessories", games, audio and TV branches. Categories are scanned
  newest-first and tracked independently, with resumable backfills, adaptive
  3–20 minute intervals, overlap stopping and a safety page. Keyword discovery
  is an explicit hybrid-mode supplement, not part of the category cycle, so a
  category scan cannot quietly refill the feed with accessories and games.
- **Live collection** from Vinted UK and Gumtree UK with per-host rate limits,
  robots.txt checks, retries, circuit breakers and honest failure states.
- **Optional eBay UK** collection through the free official Browse API. Add
  developer keys to `.env`; no HTML scraping is used because eBay's robots
  policy prohibits it.
- **Automatic model identification** for GPUs, CPUs, memory, NVMe/SATA/2.5-inch/
  external SSDs, HDDs, motherboards, complete PCs, laptops, consoles, handhelds,
  monitors, phones and tablets. Generation, capacity, VRAM, interface and kit
  details are part of the product key where they are stated; legacy 8/16/32GB
  phone and tablet capacities are recognised as real variants. Accessories
  (cases, covers, bags, styluses), empty boxes, games, Kinect-style add-ons,
  faceplates, laptop mainboards, parts-only devices and wanted ads are excluded
  from deal recommendations; genuine console bundles remain identified as
  consoles. Titles are read structurally rather than by adding exclusion words:
  a model name inside an accessory title is not evidence of the device. Each
  listing also carries an **identity grade** (Strong / Moderate / Limited /
  Insufficient) with the basis that earned it (exact model, capacity, VRAM,
  variant, model number), separate from the price-evidence level.
- **Comparable-market valuation** that prefers recently completed sales,
  keeps sold and asking evidence separate, uses weighted medians/quartiles,
  trims outliers and supports complete-PC component fallback. Confidence is
  computed from identification quality, sample size, dispersion, source
  diversity and freshness, and is reported as a descriptive evidence level
  (Insufficient / Limited / Moderate / Strong) instead of a repeatable
  percentage. A single user-entered sale never becomes independent confirmation.
- **CeX UK pricing reference** (live, no HTML scraping). Product search uses the
  same public search index as uk.webuy.com and returns retail, cash and voucher
  offers, grade, stock and the last price change. Matching requires the exact
  model, capacity and variant: special editions, all-digital models, GPU Ti
  variants and accessories are rejected rather than blended. CeX retail is
  converted to private-resale level from the product's own observed asking
  market and is always labelled a retail reference, never a completed sale.
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
- **Resale strategies**: quick sale, balanced and maximum-profit targets, each
  with a suggested initial asking price and an expected final selling price.
  The deal's profit uses the configured strategy; the other two stay visible.
- **Permanent price memory.** Every valuation is appended to
  `valuation_snapshots` with its evidence and marketplaces. A product is reused
  for 7 days, then marked stale and scheduled for refresh, prioritising products
  attached to live deals and ending auctions. Concurrent research for the same
  product is deduplicated, and history is never overwritten. If re-identification
  moves a listing to another product key, the snapshot stored for the old key is
  invalidated rather than served, and market observations filed under an identity
  the listing no longer has stop feeding valuations; both rows are kept for audit.
- **Ending-soon auctions** through the official Browse API: whole hardware
  categories are requested sorted by ending time, rotating one category per
  request, and filtered locally to the configured window. A keyword auction
  search only saw titles matching its words; the same request budget now covers
  every auction ending in the branch, with current bid, bid count, postage, end
  time and a maximum profitable bid that honours fees, postage, minimum profit
  and the ROI tier. Auctions are discovered and refreshed every five minutes,
  and shortlisted auctions are re-read in the same pass while they are close to
  ending; keyword search remains as a fallback. Nothing is bid automatically
  and ended auctions leave the feed.
- **Dedicated Auctions workspace** separate from the Buy-It-Now deal feed. It
  shows every live discovered auction inside the saved preset, including ones
  that are not promoted: current bid, bid count, postage, total cost now, exact
  end time and live countdown, seller, condition, evidence grade and direct eBay
  link. Filters cover ending within 5/10/15/30/60 minutes, ending-soonest,
  current-bid and potential-profit sorting, and Verified / Needs verification /
  Rejected states. Profit and a maximum bid appear only for a valuation with
  moderate or strong compatible evidence. Ended, faulty, over-budget and
  disabled-category auctions are excluded or explicitly labelled, and scanner
  diagnostics show the last auction pass, source state and monitoring cadence.
- **The active preset also filters stored history.** Deal feed, overview
  statistics, review queue and auction lists are filtered at the SQL level by
  the saved category leaves, marketplace toggles and total-cost budget. A
  listing collected under an earlier preset cannot reappear as an opportunity
  or trigger a Discord alert after its category or budget is disabled. An
  **Active preset only** toggle (on by default) reveals those rows as clearly
  labelled history without re-scanning or re-alerting them; nothing is deleted.
  The scanner stores a derived taxonomy leaf per listing so filtering stays a
  simple indexed query, and existing rows are classified on startup.
- **My Flips**: mark a detected deal as purchased, record actual costs, price
  cuts and the final sale, and compare the realised result with the original
  prediction. Completed outcomes slowly adjust resale estimates per kind once at
  least three sales exist; a single bad flip cannot move the market.
- **Cost accounting**: purchase price, delivery/buyer fee, configurable resale
  channel, selling fees, outbound delivery, packaging, other costs and ROI.
  Purchases above the high-value threshold must clear a higher ROI.
- **Grouped review queue** for missing phone variants. Pending `variant_unclear`
  phones are grouped by exact model, with counts, price spread, source mix and
  examples. Opening a group filters the queue to those listings; explicit
  approve/reject actions work on up to 200 selected IDs at a time. Bulk approval
  resolves the review gate but adds no evidence and raises no confidence.
  A missing variant is a data gap, not an accusation: it lowers confidence and
  blocks an automatic recommendation, but only reaches review when the price is
  also far below the market. Normal-price phones with unstated storage no longer
  wait in the queue.
- **Manual-review queue** for implausibly cheap items, including unusually
  cheap memory/storage. These are excluded from automatic recommendations and
  can be inspected through the dashboard's Review filter.
- **Cross-marketplace duplicate flags** for likely cross-posts. Original rows
  and URLs remain intact.
- **Dashboard** with ranked Deal feed, a separate Auctions workspace, filters
  (condition, confidence, marketplace, age), sorting, deal drawer, evidence,
  verified-sold import, images, direct listing links, saved/dismissed deals,
  review queue, scanner health, scan history, scan setup and settings.
- **Typed Discord alerts**: new Buy-It-Now deals, Best Offer opportunities,
  auctions ending soon and material bid changes. Alerts include the image,
  listing URL, total cost, expected resale, net profit, ROI, evidence level,
  maximum offer/bid and ending time. Buy-It-Now alerts only fire for listings
  discovered since the last notification, inside the saved preset and promoted
  by the current profit/ROI/confidence thresholds. Auctions alert only inside
  the close margin (15 minutes by default, roughly ten plus scan margin), and
  never for ended auctions or ones above their supported ceiling. Alerts are
  deduplicated with a configurable cooldown.
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
| eBay UK | Working with API keys | Official Browse API only. Category search sends `category_ids` without a keyword, 200 items/page, 4 requests/cycle. Fixed-price and auction searches were both verified live against production eBay (HTTP 200); the auction request uses `sort=endingSoonest` and a local time window. |
| Facebook Marketplace | Not available | Requires a signed-in session and prohibits automated collection; no listings are fabricated. |
| CeX UK | Working | Live pricing through the public search index used by uk.webuy.com: retail, cash and voucher offers, grades and stock. No HTML scraping and no attempt to defeat access controls. HTTP 403/429 is reported as blocked and paused. |

All collectors can be repaired independently under `app/collectors/`.

## How a deal is calculated

1. **Identify** the listing against hardware model patterns and grade how well
   the title supports that identity. Ambiguous items are kept for market
   tracking but not valued; identity grading is independent of price evidence.
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
5. **Score evidence** from identification quality, sample size, price
   dispersion, source diversity and freshness. Asking-only evidence has a
   computed ceiling that rises with the number of independent observations and
   marketplaces; it is displayed as Insufficient, Limited, Moderate or Strong,
   and never presented as a completed sale. The deal also records its evidence
   basis (`sold`, `asking_only` or a CeX retail anchor) so an asking-only
   estimate cannot inherit the language of a completed sale.
6. **Recommend only if all gates pass**: at least 4 comparables, 55% model
   match, minimum profit/ROI/confidence (a higher ROI above the high-value
   threshold), not faulty/wanted/bundle, not flagged for manual review, and
   either sold evidence or a strong five-sample asking valuation. A listing
   priced far below the asking median, or missing key storage/memory/chip specs,
   goes to review instead of the automatic feed.
7. **Purge/refresh** valuations after every successful source scan, at startup
   and after threshold/evidence/review changes. A version counter prevents a
   refresh from being skipped when two sources scan seconds apart.
8. **Keep contents honest**: console-only, missing-controller/charger/cable and
   similar listings receive a 12% resale discount and a visible risk reason.
9. **Resolve review outcomes**: an approved listing can become eligible without
   any artificial confidence boost; a rejected listing stays out of the feed.

Defaults are editable in the Settings page: £20 minimum profit, 12% minimum
ROI (18% above £250 of tied-up capital), 42% minimum evidence score, 13.22%
eBay fee, £4.50 outbound delivery, £1 packaging, zero other costs and the eBay
resale channel. Vinted and Facebook can be selected as the resale channel when
that is where the item will be sold. The resale strategy, 7-day price reuse
window, CeX lookups per scan and the auction discovery window, Discord close
margin and scan interval are configurable
there too.

### Auction bid ceiling

For each auction the ceiling is the lower of two limits: expected proceeds after
selling fees, outbound postage and packaging minus the minimum profit, and the
same proceeds divided by `1 + required ROI`. The price used is eBay's
`currentBidPrice`, not a fixed-price field. Postage to you is included in the
total cost. An auction is shortlisted only while its current bid is at or below
that ceiling; no automatic bidding is implemented and the final decision is
manual.

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

The suite currently has **128 passing tests**. It covers grouped variant review
and bounded bulk decisions, category catalogue and
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
compatibility, duplicate detection, settings and the HTTP API smoke path,
identity grading and its regression fixture, comparable validation (accessories,
compatibility qualifiers, console hardware versus the console), stale snapshot
and observation invalidation, and category-first auction discovery.

Three read-only maintenance scripts sit in `scripts/`: `audit_identity.py`
reports identity drift and, with `--apply`, repairs it; `benchmark_identity.py`
compares the previous classifier with the current one on the labelled fixture;
`benchmark_auctions.py` measures auction discovery strategies against the live
Browse API.

## API

- `GET /api/health`, `/api/overview`, `/api/deals`, `/api/deals/{id}`
- `GET /api/deals?review=1&product_key=...&review_flag=variant_unclear` — filter the queue to one grouped model
- `GET /api/reviews/groups?kind=phone&flag=variant_unclear` — grouped pending variant reviews
- `POST /api/reviews/bulk` — explicit approve/reject for up to 200 pending listing IDs
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
  `full`. eBay category browsing and both auction/fixed-price searches have been
  exercised live against production keys; no eBay HTML is scraped.
- **Grouped review covers the dominant variant gap, not every queue reason.**
  Bulk actions are explicit-ID-only and capped at 200 rows; there is no
  filter-based bulk update. Approving a variant group does not add evidence or
  raise confidence, and the missing-variant risk note remains visible.
- Category pages deliberately favour recall, so they include accessories, media
  and occasional off-category items. Identification, risk and deal gates still
  filter those; category discovery does not bypass them.
- No free, reliable automated UK sold-price feed is available. The engine now
  supports bulk and one-off sold evidence and only calibrates when the database
  contains enough matched sales, but the current local database has no saved
  sold evidence yet. Most live valuations therefore still start from asking
  prices, carry a Limited/Moderate evidence level and are labelled
  asking-only. This is a real limitation, not an estimate of sold prices.
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
- CeX search is an undocumented public index with no contract. If it is
  blocked, rate-limited or changes shape, the quote is marked blocked/failed and
  paused; no price is invented and valuations continue from live comparables.
  CeX retail is not treated as a completed sale and its high-street premium is
  removed using the observed asking market when one exists.
- Facebook Marketplace is intentionally unsupported.
- Model parsing is rule-based. It is conservative, but unusual titles can still
  be missed or grouped too broadly (especially games without a known title and
  generic drives that omit interface or capacity). Ambiguous items are kept for
  review rather than valued as deals.
- eBay auctions are requested with `sort=endingSoonest` because the Browse API
  has no documented ending-time filter; the 30-minute window is applied locally.
  On a 3-request budget measured from this machine, three hardware categories
  returned 37 in-window auctions across 9 distinct products, against 18 auctions
  from 1 product for the previous keyword queries. Coverage is still limited by
  the request budget: one pass can only rotate through a few of the catalogue's
  category branches, so a short auction can end before its branch comes round.
  Missing credentials are still reported as `not_configured`; token, quota,
  parser and authentication failures are tested.
- A local single-process SQLite app, not a multi-user hosted service.

## Source reuse and licences

The implementation is original, informed by the MIT-licensed
[Simple-Python-Scrapy-Scrapers/ebay-scrapy-scraper](https://github.com/Simple-Python-Scrapy-Scrapers/ebay-scrapy-scraper)
and [samWidlund/resell-helper](https://github.com/samWidlund/resell-helper)
projects. No Scrapy runtime is required. Marketplace HTML/API formats can
change without notice; collectors are isolated so each can be repaired alone.

`app/pricing_cex.py` adapts the matching approach and measured threshold from
the MIT-licensed [cex-uk-prices](https://github.com/casareanderson/cex-uk-prices)
project by Christian Asare-Anderson. The reference copy was temporary and has
been removed; no runtime import depends on it. The full MIT notice is in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Operational notes

- Respect each marketplace's terms and robots.txt. `RESPECT_ROBOTS=true` is
  the default and should stay enabled unless you have written permission.
- The scanner uses a descriptive user agent, conservative pacing and a circuit
  breaker. Do not lower intervals below the configured minimums.
- Back up `data/rigradar.db` if you care about saved deals and price history.
