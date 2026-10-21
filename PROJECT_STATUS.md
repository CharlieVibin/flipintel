# FlipIntel — Project Status

**Last updated:** 2026-10-22
**Workspace:** `C:\Users\Charr\Documents\Freebuff web`
**Runtime:** Python 3.10, FastAPI, SQLite, React 18 + Vite + Tailwind
**Live dashboard:** http://127.0.0.1:8765 (while the local server is running)

## Completed and verified

- [x] FastAPI backend with SQLite WAL persistence and a single guarded connection.
- [x] Independent collectors for Vinted UK and Gumtree UK, with robots.txt
      checks, per-host pacing, retries, 429/5xx handling and a circuit breaker.
- [x] Free official eBay UK Browse API collector. It reports `not_configured`
      until `EBAY_CLIENT_ID` and `EBAY_CLIENT_SECRET` are present. No HTML
      scraping is used.
- [x] Facebook collector stub that reports `unavailable`; it does not fabricate
      listings.
- [x] Category-level discovery queries, rotating cursors, automatic model-level
      comparable enrichment and duplicate protection by `(marketplace, external_id)`.
- [x] Price-history records, price-drop detection, last-seen tracking and stale
      listing maintenance.
- [x] Model identification for GPUs, CPUs, RAM, SSDs, motherboards, PCs,
      laptops, consoles, monitors, phones and tablets.
- [x] Accessory/game/empty-box rejection for consoles, including joysticks,
      faceplates, docks, hubs, heatsinks and boxed games.
- [x] Complete-PC versus loose-GPU separation using title-level CPU/GPU/system
      cues; existing rows are re-identified on startup without deleting data.
- [x] Comparable valuation with condition factors, asking-to-sold haircut,
      weighted quartiles, outlier trimming, evidence confidence and
      component fallback for complete PCs.
- [x] Cost model: purchase, delivery/buyer fee, eBay FVF, outbound shipping,
      packaging, net profit and ROI.
- [x] Manual-review gate for implausibly cheap listings (including unusually
      cheap RAM/SSD and high-value items below 40% of model resale); these are
      not emitted as automatic deals.
- [x] React dashboard: ranked feed, images, source badges, profit/ROI/resale,
      filters, sorting, search, deal drawer, comparable evidence, direct listing
      links, save/dismiss/restore, scanner health, scan history and settings.
- [x] Discord webhook and browser-notification support (optional; disabled by
      default).
- [x] Windows `start.bat` launcher and manual README instructions.
- [x] Focused pytest suite: **6 passed** (accessory/game rejection, PC/GPU
      split, console matching, suspicious-price gate, eBay item parser, API
      health/empty feed).
- [x] Live collection validated against real Vinted and Gumtree responses.

## Verified live state at last check

| Metric | Value |
| --- | --- |
| Active listings in local DB | 4,218 |
| Qualifying deals | 30 |
| Sources healthy | Vinted, Gumtree |
| eBay | `not_configured` — free developer keys not set |
| CeX | HTTP 403 from this network; surfaced honestly |
| Facebook | Paused/unavailable; requires authenticated session |
| Tests | 6 passed, 0 failed |

Real listing data is intentionally not committed: `data/` is ignored, so the
1,196+ collected rows (now 4,218) remain local and are not uploaded.

## Marketplace status

| Source | Collection | Notes |
| --- | --- | --- |
| Vinted UK | Working | Public catalogue HTML, 96 cards per query. |
| Gumtree UK | Working | Server-rendered search results. |
| eBay UK | Code complete, needs keys | Official Browse API. Add credentials to `.env` and restart. |
| Facebook Marketplace | Unsupported | Login required; automated collection is not attempted. |
| CeX UK | Blocked here | Optional anchor only. Cloudflare 403 in this workspace. |

## Known bugs / limitations

1. **No free sold-price feed.** Values are mostly modelled from live asking
   prices with a 90% negotiation adjustment. The schema and UI support sold
   evidence, but no source currently supplies it. This is the largest accuracy
   limitation.
2. **Model grouping can still be too broad.** Generic iPad/MacBook variants and
   unusual title formats can share a product key despite different generations.
   Generic iPads without a chip/generation are now intentionally left unvalued.
3. **Vinted descriptions are not exposed** in the catalogue cards, so condition
   and accessory detection relies mostly on titles.
4. **eBay has not been exercised end-to-end** because no API credentials are
   configured. The token/request/parser path is unit-tested at the item level.
5. **CeX is unavailable from this network.** The app correctly degrades to live
   marketplace comparables with capped confidence.
6. **Duplicate listings across marketplaces** are separate rows. The same item
   can appear more than once if a seller cross-posts.
7. **Scan breadth versus latency.** Twelve queries per source takes roughly
   20 seconds per Vinted/Gumtree cycle. That is intentional; reducing pacing
   risks blocks.
8. **Single-user SQLite architecture.** Not designed for multiple concurrent
   writers or hosted multi-tenancy.

## How to start

Windows one-click:

```bat
start.bat
```

Manual:

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

Then open http://127.0.0.1:8765.

Optional eBay keys: copy `.env.example` to `.env`, fill
`EBAY_CLIENT_ID` / `EBAY_CLIENT_SECRET`, restart. Never commit `.env`.

## Tests

```bat
.venv\Scripts\python.exe -m pytest -q
```

## Exact next tasks (in order)

1. **Add a real sold-price source or a manual sold-comps import.** Without it,
   every recommendation is ask-price based. Prefer the eBay Marketplace
   Insights API if access is approved, otherwise allow CSV import into
   `market_comps` and surface those rows as sold evidence.
2. **Complete an eBay Browse API end-to-end run** with real UK developer keys:
   token → search → parser → scan history → deal feed. Verify shipping filters
   and currency rejection.
3. **Improve generation-aware model keys** for iPads, MacBooks and console
   bundles; add regression fixtures from real false positives.
4. **Add source-level collection tests with saved HTML/JSON fixtures** so parser
   changes can be caught without hitting marketplaces.
5. **Add a manual-review queue in the UI** for flagged-but-not-deal listings,
   instead of only suppressing them from the feed.
6. **Consider a PostgreSQL adapter** only if multi-user hosting becomes a
   requirement; keep SQLite for the local product.

## Git / handoff

The workspace was not a Git repository when this status was written. The
intended remote is the connected FlipIntel GitHub repository. If the push could
not be authenticated from this environment, initialise/commit locally first,
then push from a machine with GitHub credentials; never commit `.env`, `data/`,
`.venv/`, `frontend/node_modules/` or `frontend/dist/`.
