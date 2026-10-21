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

- **Live collection** from Vinted UK and Gumtree UK with per-host rate limits,
  robots.txt checks, retries, circuit breakers and honest failure states.
- **Optional eBay UK** collection through the free official Browse API. Add
  developer keys to `.env`; no HTML scraping is used because eBay's robots
  policy prohibits it.
- **Automatic model identification** for GPUs, CPUs, memory, SSDs,
  motherboards, complete PCs, laptops, consoles, monitors, phones and tablets.
  Accessories, empty boxes, games, bundles and faulty/wanted ads are excluded
  from deal recommendations.
- **Comparable-market valuation** with condition adjustment, asking-to-sold
  haircut, outlier trimming, confidence scoring and complete-PC component
  fallback.
- **Cost accounting**: purchase price, delivery/buyer fee, eBay final value
  fee, outbound delivery, packaging and ROI.
- **Manual-review guard** for implausibly cheap items, including unusually
  cheap memory/storage, so they do not appear as automatic profits.
- **Dashboard** with ranked feed, filters, sorting, deal drawer, evidence,
  images, direct listing links, saved/dismissed deals, scanner health, scan
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
| Vinted UK | Working | Public catalogue HTML; 96 cards per query. |
| Gumtree UK | Working | Server-rendered search cards. |
| eBay UK | Requires free API keys | Official Browse API only. |
| Facebook Marketplace | Not available | Requires a signed-in session and prohibits automated collection; no listings are fabricated. |
| CeX UK | Often blocked | Used only as an optional price anchor. Cloudflare returned HTTP 403 in testing; valuations continue from live comparables. |

All collectors can be repaired independently under `app/collectors/`.

## How a deal is calculated

1. **Identify** the listing against hardware model patterns. Ambiguous items
   are kept for market tracking but not valued.
2. **Collect comparables** with the same canonical product key and kind.
   Asking prices are labelled as asks and discounted by the configured
   `asking_to_sold_factor` (default 90%). Sold/CeX rows use separate evidence
   fields when a source provides them.
3. **Normalise** for condition, trim outliers with weighted quartiles and
   estimate the realistic good-condition resale value.
4. **Subtract costs**: purchase price, delivery/buyer fee, eBay final value
   fee, outbound shipping and packaging.
5. **Score confidence** from identification quality, sample size, price
   dispersion, evidence type and freshness.
6. **Recommend only if all gates pass**: at least 4 comparables, 55% model
   match, minimum profit/ROI/confidence, not faulty/wanted/bundle, and not
   flagged as implausibly cheap.
7. **Purge/refresh** valuations after every successful source scan and after
   threshold changes.

Defaults are editable in the Settings page: £20 minimum profit, 12% minimum
ROI, 42% minimum confidence, 13.22% eBay fee, £4.50 outbound delivery and £1
packaging.

## Architecture

```
app/
  collectors/        independent Vinted, Gumtree, eBay and Facebook modules
  identification.py  model parser and risk/accessory rules
  valuation.py       comparable engine, fees, scoring and deal gate
  scanner.py         scheduling, ingestion, dedupe, enrichment, health
  pricing_cex.py     optional CeX anchor
  notifications.py   Discord/browser notification dispatch
  api.py             JSON API
  main.py            FastAPI app + static dashboard serving
  db.py              SQLite schema and settings
frontend/            React + TypeScript + Tailwind dashboard
tests/               focused regression tests
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

The suite covers accessory/game rejection, complete-PC versus GPU
identification, console matching, the implausible-price review gate, eBay
Browse item parsing and the HTTP API smoke path.

## API

- `GET /api/health`, `/api/overview`, `/api/deals`, `/api/deals/{id}`
- `POST /api/deals/{id}/save`, `/dismiss`, `/restore`
- `GET /api/scans`, `/api/sources`, `/api/settings`
- `PUT /api/settings`
- `POST /api/scan`

Interactive docs: <http://127.0.0.1:8765/docs>

## Known limitations

- No free, reliable UK sold-price feed is available. The engine distinguishes
  sold and asking evidence but currently estimates mostly from live asks with a
  negotiation haircut. This caps confidence and should be improved before
  treating returns as guaranteed.
- CeX is frequently blocked by Cloudflare. When unavailable, it is reported as
  blocked rather than silently ignored.
- Facebook Marketplace is intentionally unsupported.
- Model parsing is rule-based. It is conservative, but unusual titles can still
  be missed or grouped too broadly (especially iPads and MacBooks).
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
