# FlipIntel — Project Status

**Last updated:** 2026-10-25
**Workspace:** `C:\Users\Charr\Documents\Freebuff web`
**Runtime:** Python 3.10, FastAPI, SQLite, React 18 + Vite + Tailwind
**Live dashboard:** http://127.0.0.1:8765 (while the local server is running)

## Session 2026-10-25 — Review queue and product eligibility repair

Phase 6 left 660 pending reviews, and real dashboard testing still showed a CPU
cooler priced as a Ryzen processor, six near-identical generic `1TB-128TB`
drives, duplicate cards and unprofitable rows in Human Review. This pass traced
the actual pipeline and fixed the causes rather than rewriting the phase.

### What was wrong

1. **Cooler-only titles were read as the processor.** `ACCESSORY_TITLE_RE` had
   `cooler only` and `fan only`, but the accessory branch was gated by
   `title_accessory and not gpu and not cpu`. A listing that named a CPU could
therefore never be a part, and `COOLER/FAN ONLY` did not match the single-token
patterns. The title `AMD Ryzen 7 3700X COOLER/FAN ONLY no CPU` became
`cpu:amd:ryzen-7-3700x` with no flags and entered review.
2. **Capacity ranges were read as a fixed capacity.** `1TB-128TB` parsed as
   1TB, producing an `hdd:external:1tb` identity and a £39.66 estimate for a
   £16.99 variation listing. There was no range/variation detection at all.
3. **Same-seller relists were never compared.** `_mark_duplicates` skipped every
   same-marketplace pair, so one eBay seller's four identical generic drives
   produced four separate review cards and four identical review cards.
4. **Review was a by-product of risk, not a decision.** `needs_review` was set
   for any price anomaly or medium-risk signal; `eligibility.assess` then mapped
   every such row with positive profit to `needs_verification`. Thin margins, a
   family-level identity and weak evidence all became manual work, and the review
   endpoints accepted any state that was not exactly `excluded`.

### What changed

- `identification.py`: subject exclusion (`cooler/fan/heatsink only`, `no CPU`,
  `CPU not included`) clears the model identity and records `parts_only`; a
  capacity range is a variation listing with `capacity_range` and
  `suspicious_capacity` and no exact key; unbranded portable storage records
  `unbranded_storage`; storage media, HDD trays and compatibility mentions keep
  their own product class; game titles (`Donkey Kong Bananza`, `W2K20`,
  `Planet Coaster Console Edition`) stay games. `Unopened Box` no longer makes a
  sealed processor an accessory.
- `eligibility.py` is the single gate. It now excludes duplicates, capacity
  ranges, unbranded storage and parts before valuation, and only returns
  `needs_verification` when a human answer could unlock a plausible deal:
  margin at or above the saved profit/ROI thresholds, valuation confidence at
  least 0.40, and no family-level identity unless the upside is at least twice
  the configured profit and confidence is 0.50+.
- `dedup.py`: one shared duplicate implementation. It links same-seller,
  same-title, same-price relists (the mass-storage case) and keeps the existing
  conservative cross-marketplace comparison. Rows are linked via `duplicate_of`
  and never merged or deleted.
- `api.py`: the review queue, grouped reviews, bulk actions and both review
  counters now require the persisted `eligibility = 'needs_verification'` state.
  A stale `needs_review` flag on an excluded row cannot be shown anywhere.
- `valuation.py`: `_store_result` is shared by the normal refresh and a new
  `revalue_ids()` targeted repair, so a reconciliation can value an explicit set
  of rows without starting or resuming the 113k-row full pass.
- `scripts/reconcile_review_queue.py`: resumable, idempotent repair of only
  visible recommendations/reviews plus the titles affected by the new rules. It
  rewrites identity, links duplicates and revalues that bounded set; raw
  listings, observations, snapshots and Flips are untouched.

### Measured repair on the live database

| Measure | Before | After |
|---|---:|---:|
| Pending Human Review | 660 | **164 live** (162 after reconciliation) |
| Pending review inside `100gbp starter` | — | **15** |
| Recommended deals | 191 | 189 |
| Excluded listings | 112,931 | 113,431 |
| Duplicate links | 547 | 547 |

The reconciliation removed 498 review rows and added none; the startup
background re-check later re-opened 2 rows after re-identifying them, leaving
164 live. Their current reasons:

| Reason | Rows |
|---|---:|
| Margin below the level that justifies review | 272 |
| Family-level identity, too weak to ask about a specific product | 124 |
| Valuation evidence too weak to resolve by review | 55 |
| Implausible/unverifiable storage capacity | 17 |
| Generic unbranded portable storage | 16 |
| Game software | 4 |
| Spare parts/replacement component | 3 |
| Duplicate of a tracked relist | 2 |
| Physical product could not be established | 2 |
| Accessory / storage media / insufficient evidence | 3 |

The specific reported examples now read:

- `AMD Ryzen 7 3700X COOLER/FAN ONLY no CPU` → identity none, `sale_role=parts`,
  excluded as a spare part, no resale estimate.
- `1TB-128TB ...` and `1TB-4TB ...` (six listings) → identity none,
  excluded as a capacity range; three relists also point at the earliest row.
- `Donkey Kong Bananza Switch 2`, `W2K20 Xbox one X` and
  `Planet Coaster Console Edition PS5 ... Loose Disc` → game, excluded.
- `32MB PSP Sony Memory Card Stick Duo ...` → removable media, excluded.
- `DELL Equallogic 4TB HDD Tray` → accessory, excluded.

Live check after the repair (`http://127.0.0.1:8765`): `/api/deals?review=1`
returned 15 rows in the active preset and none of the failure titles;
`/api/deals?review=1&active_preset=false` returned 164; the deal feed and the
Auctions tab (231 live auctions) still answered normally. `/api/health` response
and startup stayed in the low single-digit seconds; the repair is a separate
script, not part of startup.

### Limitations of this pass

- The pattern sweep repaired 1,308 active rows (visible rows plus the titles
  affected by the new rules), not the whole history. A listing whose title does
  not match the new rules keeps its stored identity until it is scanned again;
  the full 113k history was deliberately not reprocessed.
- 189 recommended deals remain; two previously recommended RTX 3050 PCs dropped
  out because their current ROI is below the 18% higher-value threshold, not
  because of the review gate.
- 162 reviews outside the active preset still exist. They have a plausible
  margin and a resolved identity, but many are deep discounts on consoles and
  GPUs that genuinely need a human answer. They are not visible under
  `100gbp starter`.
- Coolers are identified as parts and excluded. There is still no cooler product
  key or cooler resale evidence, so enabling a cooling category cannot price one.
- Game detection remains a named-title lexicon plus media cues; new games can
  still be misread until their franchise is added.
- No completed-sale evidence exists yet, so these valuations remain
  asking-price estimates.

### Tests

220 backend tests pass, including the new `tests/test_review_eligibility.py`
(10 tests covering cooler-only, included cooler, capacity ranges, unbranded vs
branded storage, games, storage media, drive trays, review-margin gating,
same-seller relists, the review API's authoritative state and the valuation
clearing a now-excluded review). The 240-listing benchmark still passes; its
`73010` label was corrected because a `1TB-4TB` range has no single identity.

## Session 2026-10-25 — Phase 6: reliable identity, eligibility and fast startup

This pass fixed the two-minute startup and the product-class failures that were
letting accessories, complete computers and single implausible asks set resale
prices. It did not rebuild the application, reset the database, delete historical
observations or add a paid data source.

### Startup and maintenance

- Identification is now versioned per listing (`identity_version` + a content
  hash). A restart re-identifies only rows whose version or meaningful content
  changed, and the pass batches/commits with a resumable cursor. The scanner no
  longer performs the whole history synchronously before the HTTP port opens.
- Measured on the live 113k-row database: HTTP `/api/health` answered in
  **6.8 s** on the final warm restart and **5.3 s** on the preceding restart;
  the no-op background maintenance took **6.6 s**. A version migration that
  changed 1,395 identities took **264 s** in total (82 s identification, 182 s
  valuation) while the dashboard was already serving.
- The earlier cold benchmark on the same database was 8.7 s to HTTP readiness
  and 113.9 s for the background pass. Collection is still rate-limited by the
  marketplaces, not by startup work.

### Why the Steam Deck LCD displayed OLED comparables

Two separate faults were present:

1. Product identity did not always carry the LCD/OLED attribute, so an
   unqualified `Steam Deck` key pooled both display technologies. The structured
   variant comparator now rejects a display mismatch before price weighting.
2. The deal panel showed rejected rows next to accepted rows. The drawer now
   separates accepted basis comparables from rejected rows and states why each
   was rejected; only accepted rows are marked as contributing.

The original `Steam deck 1tb lcd` listing (£180, Gumtree) is now
`console:steam-deck-lcd:1tb:upgraded`. Its previous £290.42 estimate is gone.
There is currently **no supported estimate**: the only exact LCD 1TB ask is
£79.88, which is below 40% of the £321.45 median of 24 other Steam Deck LCD
listings. That ask is rejected as a likely incomplete/faulty/scam outlier and the
old £69.28 snapshot is invalidated. No OLED price is used. For context, the
separate LCD keys are £255.00 (64GB, 5 asks), £284.61 (256GB, 11 asks, moderate)
and £323.00 (512GB, 1 ask, insufficient); those values are never copied onto the
1TB key.

### Identity and eligibility

- Identification version **6** adds Apple desktop families (Mac mini, Mac Studio,
  iMac, Mac Pro), Windows specification-dense complete systems, and console
  bundles containing an expansion SSD as console/bundle rather than a loose
  drive. A complete Mac mini with a 512GB SSD no longer becomes an SSD
  comparable, and `Xbox Series X ... Expansion SSD` is no longer an NVMe market
  observation.
- **1,395** records changed identity in the first Phase 6 pass (163 more in the
  final console pass); **1,675** identity revisions
  were recorded across the migration, **539** of them moved to a different
  product key, and **4** previously promoted deals were re-identified.
- `app/eligibility.py` is a backend gate before valuation: the physical product,
  sale role (device/component/accessory/parts/packaging/media/game/bundle),
  condition/functionality, preset membership, profit and evidence must all
  support a recommendation. Frontend hiding alone is not used.
- Final live eligibility: **191 recommended**, **660 needs verification**,
  **112,838 excluded**, **131 unset** legacy rows. Main exclusion reasons:
  40,748 product not established; 26,583 accessory/part; 16,748 no expected
  profit; 10,777 exact model missing; 4,595 game software; 4,227 removable media;
  3,053 insufficient compatible evidence; 1,815 bundle; 1,615 spare parts;
  1,430 below thresholds; 790 faulty; 296 packaging; 74 implausible capacity.
- Recommendations fell from **206 to 191** and the active review queue from
  **1,221 to 660**. This is intentional precision-over-volume behaviour, not a
  claim that every remaining recommendation is profitable.

### Valuation integrity

- Comparable pools exclude accessories, packaging, games, media, spare parts,
  faulty/untested rows and complete-system contamination before aggregation.
- **Storage peer guard:** an SSD asking price more than twice the median of other
  drives at the same capacity and interface is rejected as an OEM/outlier ask.
  The peer index is built once per refresh; per-key queries had made a repair
  pass unacceptably slow. Live SSD snapshots above £200 fell from **25 to 6**,
  all six being genuine 4TB drives.
- **Sparse-family guard:** for a pool with fewer than three distinct sources, a
  GPU/console/phone/tablet ask below 40% of the median of its own model family is
  rejected as a sanity check. The family median is never substituted as the
  estimate. This retired the £69.28 Steam Deck LCD, the £85.87 iPhone 15 Plus and
  the £7.78 iPad Air M4 estimates rather than presenting them as values.
- Re-evaluating a key that no longer has a supported estimate invalidates its
  live snapshot (`no supported estimate after revaluation`) instead of leaving
  the old price active for another seven days. Raw snapshots and observations are
  retained; nothing is deleted.

### Corrected examples on the live database

| Product | Before | After |
|---|---:|---:|
| Steam Deck LCD 1TB upgraded (target) | £290.42 from OLED-contaminated pool | no estimate; exact LCD ask rejected as low outlier |
| Steam Deck LCD 256GB | £290.42 mixed | £284.61, 11 exact LCD asks, moderate |
| Steam Deck OLED 512GB | £412–440 mixed | £412.15, 17 exact OLED asks, moderate |
| 256GB NVMe SSD | £261 implausible | £23.14 generic; £18.50 for the SK Hynix key |
| 512GB NVMe SSD | £342–442 | £47.94 on live deals; old high snapshots retired |
| Samsung 1TB / 2TB NVMe | £403.36 / £428.61 | both invalidated; no supported estimate |
| 4TB Samsung 990 Pro | not previously separated | £358.89, limited, 4 exact asks |
| RTX 4060 8GB / 4060 Ti 8GB | shared generic pools | £267.48 moderate / £255.00 insufficient, separate keys |
| RTX 4070 12GB / 4070 Ti / 4070 Super | variant risk | £522.67 limited / £460.00 limited / £489.80 insufficient, separate keys |
| RTX 3060 12GB | £247.92 earlier | £181.28 limited, 10 exact asks |
| Gigabyte Z790 AORUS Master X | implausibly low | no estimate rather than a wrong one; listing is faulty/parts-flagged |

Historical data remains intact: listings **113,820**, active **113,654**,
valuation snapshots **11,382** (2,923 invalidated), market observations
**72,458** (4,081 invalidated). The increase is normal scanning during the
session; no history was reset. The two broken capacity keys (84TB/68TB style)
are now zero.

### Tests and verification

- `.venv\Scripts\python.exe -m pytest -q`: **210 passed, 0 failed**.
- `cd frontend && npm run build`: TypeScript and Vite build pass (403 kB JS,
  49.58 kB CSS).
- Live final restart: HTTP ready in 6.8 s; background maintenance idle in 6.6 s;
  `/api/health` reports 113,654 active listings. The full 113,641-row migration
  completed in 264 s with no scanner/migration race.
- New tests cover the Mac mini/Studio/iMac and Windows-system SSD confusion,
  console+expansion-SSD bundles, sparse-family outlier rejection, storage peer
  outlier rejection, snapshot retirement and the resumable/incremental startup
  path. The independent 240-listing benchmark still passes its precision/recall
  and kind-accuracy thresholds.

### Remaining limitations

- A full reconciliation after a **valuation rule change** is still expensive on
  this database (264 s for 113k rows, entirely background). Normal restarts are
  incremental and took 6.6 s; a logic change uses a targeted key repair where
  possible rather than re-evaluating every listing.
- 131 legacy deals have no eligibility value because they are outside the
  current valuation universe; they are not shown as recommendations.
- The 40% family floor and 2x storage ceiling are sanity guards, not market
  models. They require at least five/six sibling observations and only affect
  sparse or extreme pools. A genuinely rare product with no siblings remains
  insufficient rather than being guessed.
- The six remaining live SSD snapshots above £200 are 4TB products; they are
  plausible capacity-driven values, not leftovers from the 256GB/512GB failures.
- There is still no completed-sale evidence in this database. Every displayed
  estimate is asking-only and is labelled accordingly; calibration remains
  inactive until genuine sold observations are imported.
- RTX keys are separated when the title states VRAM or a Ti/Super suffix. A
  generic RTX 4070 key without VRAM remains a provisional family match and should
  be verified against the exact card before purchase.

## Session 2026-10-24 — Schedule and alert tuning

Requested cadence and alert changes, with no pricing-model work:

- Fixed-price discovery now runs a discovery pass every **30 minutes**
  (`category_cycle_seconds=1800`, `scan_interval_*=1800` for keyword mode).
  Category-first discovery still rotates a request budget per pass, so each
  selected leaf is revisited across later passes rather than all at once.
- eBay auction discovery, rechecking and expiry now run every **5 minutes**
  (`auction_scan_seconds=300`, `auction_urgent_seconds=300`).
- Discord auction alerts now have their own close margin,
  `auction_alert_window_minutes` (default **15 minutes**, editable in Settings).
  The wider `auction_window_minutes` still controls what the Auctions desk
  discovers and displays. The margin is enforced in the alert query and again at
  send time, so a wide discovery pass or a late scan cannot alert early.
- Auctions must still be `is_deal=1`, unreviewed, inside the saved preset, at or
  below the supported maximum bid and backed by the existing evidence gate
  (Limited or better, with a stored ceiling). Buy-It-Now alerts keep their
  behaviour: only rows with no prior notification, inside the preset and flagged
  as a deal are pushed, so re-scans do not repeat old offers.
- Live database settings were updated (not reset) so the running app uses the
  new cadence immediately; restart is only needed for the new alert-window code
  and Settings field. `test_auction_alerts_wait_for_the_close_margin` covers the
  window. Tests: 193 passed, frontend build passes.

## Session 2026-10-24 — Dedicated Auctions tab and preset filtering for stored listings

This session made the saved scan preset apply to data that was already in the
database, and added a real auction workspace. No pricing model, historical
observation or legacy collector was replaced.

### Why old listings were still visible

The scanner applied category, marketplace and budget rules before a **new**
marketplace request, but the deal, overview, review and alert queries never
looked at the saved preset again. A listing collected under an earlier preset
therefore stayed in the feed after its category was disabled or its price left
the budget, regardless of when it was discovered. The fix is at the SQL layer,
not a frontend hide.

- `listings.scan_leaf` now stores the taxonomy leaf derived from the stored
  identity, title and marketplace. It is an additive column with an index; the
  111,863 existing rows were classified on startup in about two seconds.
  Identity corrections and the existing re-identification pass update it.
- `app/visibility.py` owns the one definition of preset membership: enabled
  leaves, enabled marketplaces, and total purchase cost (Vinted buyer-protection
  fee and estimated delivery included, eBay/Gumtree postage included, auction
  bids used instead of Buy-It-Now prices). `app/api.py` applies that filter to
  deal feed, overview statistics, review counts/groups and auction lists;
  `app/notifications.py` applies it before any Discord offer.
- Pagination, `total`, `has_more`, category/marketplace statistics and the
  dashboard cards all derive from the filtered query, so counts cannot disagree
  with the rows. The response also reports how many stored rows the preset
  hides.
- **Active preset only** is on by default on the Deal feed. Turning it off
  returns the same stored rows in history mode, labelled **Outside preset** with
  the concrete reason (disabled category, disabled marketplace, over/under
  budget). History mode does not re-scan, re-value or alert. Nothing is deleted.
- The Shortlist is intentionally not preset-filtered: a user's explicit save
  remains reachable, with an outside-preset label when relevant. My Flips and all
  completed records are untouched.

### Auctions workspace

`AuctionsPage.tsx` is a separate tab backed by `GET /api/auctions`, using the
existing eBay Browse auction scanner rather than a second collector. It shows
all live discovered auctions inside the preset, not only promoted ones:
image, title, exact identified model, current bid, bid count, postage, total
cost now, exact end time, live countdown, seller, condition, evidence grade,
resale estimate, direct eBay link and the scanner's last-pass diagnostics.

- Filters are server-side: ending within 5/10/15/30/60 minutes, ending soonest,
  current bid, potential profit, newest, and Verified / Needs verification /
  Rejected. Refresh calls `POST /api/auctions/refresh`, which starts one
  guarded auction pass through the existing scanner.
- Verification is descriptive, not a fake probability: a verified row needs a
  product identity, moderate or strong compatible evidence, a supported maximum
  bid and a promoted deal. Insufficient/limited evidence, a missing identity or
  an unresolved review stays **Needs verification**; faulty, over-budget or
  above-ceiling rows are **Rejected** with the reason. Profit and maximum bid are
  withheld in the UI and marked `profit_supported: false` unless verified.
- Auctions are excluded from the default deal feed, so a current bid is never
  presented as a Buy-It-Now purchase price. Saved auctions remain in the
  Shortlist. The auction scanner still obeys the saved categories, eBay toggle,
  budget, ending window and recheck/alert infrastructure.

### Live measurements on the existing database

Using the user's **`100gbp starter`** preset (28 leaves, £0–£100, eBay + Vinted +
Gumtree):

| Measure | Result |
|---|---:|
| Stored listings classified | 111,863 |
| Active non-auction listings | 110,785 |
| Active non-auction listings inside preset | 30,267 |
| Deal-feed rows before filtering (history) | 199 |
| Deal-feed rows shown by default | 11 |
| Previously visible deal opportunities hidden | 188 |
| Live upcoming auctions | 499 |
| Upcoming auctions inside preset | 264 |
| Auctions hidden by preset | 235 |
| Verified / Needs verification / Rejected (in preset) | 0 / 222 / 42 |
| Review rows inside preset | 352 |
| Selected eBay auction categories | 25 |

There are currently **zero verified auctions inside this preset**. The five
promoted auction deals in the database are a Switch OLED, Xbox Series X, Switch
2, Galaxy S24 and iPhone 17 Pro: every one is a disabled category or above the
£100 budget. The tab labels them as outside preset/history instead of promoting
them, which is the intended result of the fix.

Historical data is intact: `listings` 111,863, `price_history` 115,041,
`market_observations` 50,123, `valuation_snapshots` 7,976, `external_quotes`
135, `cex_price_history` 174. The derived `scan_leaf` and filter work did not
delete or rewrite any of those rows. The database grew only through normal
scanning during the session.

### Verification

- `python -m pytest -q`: **192 passed** (187 previous, 5 new in
  `tests/test_listing_visibility.py`). The new tests cover total-cost budget
  including Vinted fees and postage, category/marketplace toggles, immediate
  preset changes without a restart, history mode labels, overview/health counts,
  auction verification states, ending-window and status filters, auction/deal
  separation, Discord preset gating, and legacy-row classification.
- `cd frontend && npm run build`: TypeScript and Vite build pass.
- Live API checks against the real database confirmed the counts above and that
  the default deal feed returns only in-preset rows while history mode returns
  the full stored set with reasons. A browser pass at `#auctions` showed 264
  live in-preset auctions with live countdowns, current bids, total cost and
  evidence, and toggling **Active preset only** switched to 499 auctions with
  outside-preset rows labelled. The Deal feed showed 11 rows and
  "188 stored listings outside this preset are hidden"; toggling history mode
  returned 199 rows with an **Outside preset** badge.

Remaining limitations:

- An auction is verified only when its existing valuation has moderate/strong
  evidence and a supported ceiling. With the current £100 PC-parts preset, no
  auction clears that bar, so the tab deliberately shows a large
  "needs verification" group rather than inventing opportunities.
- The derived leaf is recalculated by the existing identity pass. If an operator
  changes the taxonomy without restarting, run the re-identification/backfill
  path before relying on the new mapping.
- The Auctions tab's Refresh starts the existing scanner pass; a pass can take
  tens of seconds and the page reloads automatically after it. It is not a
  real-time bid stream.
- This phase did not change the pricing/evidence model, eBay/Vinted/Gumtree
  collectors, CeX integration or Discord formatting.

- Startup fix found while relaunching:
  `PricingDatabase.invalidate_contaminated_observations()` compared
  `'listing:' || l.id` inside a correlated `EXISTS`,
  which cannot use the listing primary key. On the live database (111,863
  listings, 50,123 observations) that scan never finished, so the server never
  bound its port. The update now joins the listing id directly
  (`CAST(substr(source_id, 9) AS INTEGER)`): 0.15s instead of hanging, still
  idempotent, still leaves raw observations in place. Boot on this database is
  now about 2m20s (50s re-identification plus the startup valuation refresh), so
  the dashboard takes a couple of minutes to answer after `run.py` starts.
- A second live pass after the startup fix returned 271 live in-preset auctions
  (0 verified, 229 awaiting verification, 42 rejected), 12 in-preset deal rows
  out of 200 stored, and no auction rows in the deal feed at all.

Commit: `e470b0e` — *Add auctions workspace and preset filtering for stored
listings* — pushed to `main`, followed by the startup-query fix recorded in the
commit that updated this file.

## Session 2026-10-23 — Pre-scan category toggles, presets and budget controls

This session added a persistent control plane in front of every automated
category scan. It does not replace the category-first collectors, reset the
database or change historical observations.

### Taxonomy and persistence

- `app/scan_taxonomy.py` defines **29 groups / 145 selectable leaves / 174 nodes**,
  covering PC components, phones, computers, consoles, tablets, peripherals,
  cameras, audio, TVs, networking/smart home, storage, wearables and a
  deliberately broad Other Electronics group. Parent and leaf toggles are
  independent, and a parent toggle controls the whole subtree.
- The taxonomy keeps an explicit marketplace mapping for every leaf. Current
  coverage: Vinted **99 discovered branches → 128 mapped leaves**, Gumtree **7 →
  145**, eBay Browse **102 → 132**. The coverage endpoint reports
  `unmapped_leaves: 0` and no live category without a taxonomy mapping.
- `app/scan_config.py` persists the confirmed flag, budget, marketplace
  switches, selected leaves and named presets in the `scan_config` table. The
  schema is created idempotently on startup. The initial default is the Budget
  Electronics preset: 21 leaves, £0–£100, all three marketplaces. A saved
  configuration survives restarts and is reloaded by every scan loop.
- Built-in presets: Budget Electronics, PC Components, Graphics Cards, Phones,
  Laptops, Complete PCs, Consoles, Tablets & Displays, Gaming Peripherals,
  Photography & Audio and All Electronics. Custom configurations can be saved,
  renamed, duplicated and deleted. All Electronics covers all 145 leaves.

### Enforcement and efficiency

The scanner now requires a confirmed configuration. Before a category scan it
resolves enabled leaves to marketplace category IDs, sends server-side price
filters where the source supports them, rejects disabled leaves and over-budget
total costs before storage/enrichment, and deduplicates the resulting request
set. A disabled category therefore cannot trigger comparable research or
valuation.

Ending-soon auction discovery now uses the same configuration instead of a
separate all-branch rotation. It refuses to start without a saved configuration,
stops when eBay is disabled, rotates only the enabled eBay branches, applies the
same GBP auction price window, filters shortlisted auctions by enabled leaf and
saved total-cost budget, passes only those shortlisted listing ids to Discord
alerts, and filters over-budget bids again during ingest. The existing auction
monitor, ending-window rule and recheck of known shortlisted auctions are
preserved; changing a toggle takes effect on the next auction pass without a
restart.

Measured request coverage on the current configuration (28 leaves selected):

| Source | All catalogue branches | Selected requests |
|---|---:|---:|
| Vinted | 99 | 20 |
| Gumtree | 7 | 2 |
| eBay Browse | 102 | 25 |
| **Total** | **208** | **47** |

That is a **77% reduction** before broad-branch overcollection is filtered
locally. Gumtree only exposes seven usable branches; its audio/headphone and
peripheral mappings are intentionally broad and are documented in the coverage
report. Buyer-protection fees and known delivery are included in the Vinted
budget calculation; unknown costs are not treated as verified.

### Dashboard

`ScanConfigPage.tsx` adds the **Scan setup** workspace: red unconfigured banner,
active preset, enabled leaf/group count, marketplace counts, min/max budget,
scanner status, Save Configuration, Start Scan, search, parent Enable/Disable
all controls, coverage report and preset create/rename/duplicate/delete actions.
When the configuration has never been saved the scanner pauses before any
marketplace request and the page stays in the red "not configured" state.

### Verification

- `python -m pytest -q`: **187 passed** (including `tests/test_scan_config.py`
  for persistence, parent/child toggles, budget arithmetic, presets, request
  reduction, confirmation gating and auction category/budget enforcement).
- `cd frontend && npm run build`: TypeScript and Vite build pass.
- Live API and browser checks: `GET /api/scan-config`, save, preset selection,
  category filters, dashboard red/green states, and scanner health. After the
  auction change the live ending-soon Browse requests used only configured eBay
  branches (for example `1244`, `16145`, `44980`, `182093`) and carried
  `price:[1..100]`; no request used a category outside the saved 25-branch eBay
  set. The user's `100gbp starter` custom preset and its 28 leaves were left
  intact.
- A cold start with a confirmed configuration re-identifies and revalues the
  existing database before uvicorn starts serving; on this 549 MB database that
  takes about **9 minutes**. This is existing maintenance behaviour, not a
  scanner request and not data loss. Subsequent restarts use the same path.

Remaining limitation: when an eBay category auction request fails, the existing
keyword fallback is still used so monitoring is not left blind. That fallback
cannot express category IDs, so it may overcollect briefly; disabled product
classes are still rejected before storage and valuation, and the fallback is
reported in scan diagnostics.

## Session 2026-10-23 — Variant integrity and the Steam Deck LCD failure

This session fixed a real valuation failure without resetting the database or
removing any historical observation. The target was a live Gumtree listing,
`Steam deck 1tb lcd`, asking £180. The old pipeline valued it at £290.42 and
promoted it with £66.23 estimated profit.

### What actually happened

1. The listing was identified as `console:steam-deck`, not an LCD 1TB. Its title
   said `1tb` but the identifier had no display or storage-origin attribute, so
   it fell back to the generic Steam Deck key.
2. That generic key held 66 recent active asks: 54 non-OLED and **12 OLED**,
   spanning 64GB, 256GB, 512GB and 1TB. The old comparable validator checked
   product class, capacity and accessories, but had no structured display or
   variant check, so every OLED row was accepted into the pool.
3. The valuation was a lower-quartile asking-price estimate over that mixed
   pool. The £290.42 was **not** an average OLED price, but the OLED asks were
   accepted evidence, contributed weight and widened the distribution. The
   result was not an LCD 1TB valuation under any interpretation.
4. The drawer displayed the same accepted pool, including the OLED rows, so the
   interface was accurately showing contaminated evidence.

The corrected identity is `console:steam-deck-lcd:1tb:upgraded` with strong
identity (exact model, display and capacity parsed). It now has **one** exact
compatible ask: a Vinted `Steam Deck LCD with 1tb` at £79.88, adjusted to £69.28.
That is marked **insufficient evidence**, has no supported range, and is not a
deal (`net_profit` -£125.68). The single cheap ask is preserved but cannot be
used to claim a confident profit. The old generic `console:steam-deck` bucket
still exists for titles that state no display; it is flagged `variant_unclear`
and is not used to value the exact LCD listing.

### Structured variant identities

New `app/variants.py` derives a category-specific identity from the title and
stored attributes. It is not a list of product-name exceptions:

- Steam Deck: LCD/OLED, 64/256/512GB/1TB, factory versus user-upgraded storage.
- NVIDIA/AMD GPUs: model, Ti/Super/XT/XTX suffix, VRAM, desktop versus laptop.
  Unique factory VRAM capacities are inferred when omitted (RTX 4070 = 12GB);
  multi-capacity models (RTX 3060 8/12GB, RTX 4060 Ti 8/16GB, RTX 3080
  10/12GB) stay unknown and therefore uncertain.
- Consoles: PS5 base/Slim/Pro, Digital versus Disc, Xbox Series S/X, Switch /
  OLED / Switch 2, PS4 Pro/Slim. Console storage is compared when both titles
  state it.
- Phones/tablets: exact model, Pro/Plus/Max/Ultra suffix and storage.
- RAM: DDR4 versus DDR5, total capacity, module form factor, ECC, speed and
  single-stick versus 2x/4x kit arrangement.
- Storage: capacity, NVMe/SATA/USB interface and form factor.
- Condition: faulty and refurbished stock cannot price working used hardware.

### Compatibility before pricing

`split_pool` now returns `exact`, `uncertain` or `incompatible` for every row.
A conflicting critical attribute is rejected regardless of title similarity,
price or seller. An unstated attribute is accepted only provisionally and is
never treated as exact. The interface shows the identified variant, the match
quality, the reason, the weight, the adjustment reason, the date, the source and
the link. Rejected rows are listed separately. CeX retail/cash/voucher is a
separate reference, never completed-sale evidence.

The promotion gate allows uncertain rows only as a small minority: at least 3
exact comps and no more than 25% uncertain (with a floor of 2). This restored
support for products with 90+ exact comps and a handful of titles that omitted
storage, while still rejecting generic PS5 pools where 65 of 133 rows had an
unstated disc/digital edition.

### Database repair and revaluation

All changes are additive; raw listings, observations and snapshots were kept.

| Repair measure | Result |
| --- | ---: |
| Listings audited | 74,489 |
| Listings whose identity changed | 614 (609 initial + 5 RAM refinement) |
| Affected product keys | 449 |
| Market observations reassigned | 429 |
| Market observations invalidated but retained | 15 |
| Cached valuation snapshots newly invalidated | 305 (291 first pass + 14 RAM grouping) |
| Total invalidated snapshots retained | 672 |
| Completed-sale comps reassigned (none exist) | 0 |
| Second audit after repair | 0 changes, idempotent |

Largest identity corrections: 25 PS5 Slim listings moved to the Digital key,
32 generic Steam Deck LCD listings and 25 generic OLED listings gained their
display/capacity key, and 187 titles previously classed as laptops were
reclassified as RAM. Five of the old promoted deals sat on affected keys; two
had their product key rewritten (the Steam Deck target and a 256GB Steam Deck).
A full revaluation evaluated 74,395 active listings after the repair.

### Before/after measurements

| Product key | Before | After | Evidence |
| --- | ---: | ---: | --- |
| Steam Deck LCD 1TB upgraded (target) | £290.42 mixed pool, 53 comps | £69.28, **insufficient**, 1 exact ask | One Vinted LCD 1TB ask at £79.88; no promotion |
| Steam Deck LCD 64GB | generic £284.61–290.42 | £255.00 | 6 exact asks, limited |
| Steam Deck LCD 256GB | generic £284.61–290.42 | £290.42 | 12 exact asks, moderate |
| Steam Deck LCD 512GB | generic £284.61–290.42 | £323.00 | 2 exact asks, insufficient |
| Steam Deck OLED 512GB | generic £284.61–290.42 | £374.70 | 17 exact asks, moderate |
| Steam Deck OLED 1TB | generic £284.61–290.42 | £467.50 | 8 exact asks, moderate |
| RTX 4060 8GB | £267.48 | £267.48 | 8 comps; key unchanged |
| RTX 4060 Ti 8GB | £527.00 | £527.00 | 1 comp; insufficient |
| RTX 4070 12GB | £441.99 | £441.99 | 7 comps; key unchanged |
| RTX 4070 Ti 12GB | £546.43 | £546.43 | 1 comp; insufficient |
| RTX 4070 Super 12GB | £577.92 | £577.92 | 3 comps; key unchanged |
| RTX 4070 Ti Super 16GB | £366.52 | £366.52 | 2 comps; insufficient |
| RTX 3060 12GB | £158.66 | £158.66 | 10 comps; key unchanged |

The live database had already separated the common GPU suffixes by product key,
so the audit found no GPU key to rewrite and the GPU estimates did not change.
That is the measured result; the new validator is regression-tested against the
GPU conflicts so future title-similarity matches cannot enter those pools.

Promoted deals went from **74 to 67** after correction: 11 old promotions no
longer pass the gate and 4 became eligible. The 11 demotions are the Steam Deck
LCD target, seven generic PS5 listings and one PS5 Slim with an unstated
disc/digital edition, an Xbox Series S 1TB whose corrected margin fell below
£20, and an HP desktop whose margin fell below £20. The seven generic PS5s are
held as candidates, not deleted; the edition must be confirmed before they can
be called opportunities. Three RTX 5070 complete PCs and a PS5 Digital became
eligible through the corrected valuation. No incompatible comparable remains in
any promoted deal.

### Evidence uncertainty after the fix

- 5,445 live product snapshots: 2,493 limited, 965 moderate, **1,987
  insufficient**. 1,841 keys have only one or two comparable observations.
- 67 promoted deals: 35 limited and 32 moderate; all are asking-only because the
  database still has zero completed sales.
- 14 promoted deals include a small uncertain minority; no promoted deal is
  dominated by uncertain evidence, and every displayed accepted comparable has
  `contributes=true`. Across all 74,395 active deal rows, no displayed accepted
  comparable had `contributes=false`.
- The target's single £79.88 ask is a candidate for targeted research, not a
  resale estimate. The UI now displays "Insufficient" instead of a prominent
  resale figure for insufficient-evidence rows.

### Tests and holdout benchmark

- `pytest -q`: **175 passed**.
- `frontend`: `tsc -b && vite build` passes.
- Independent hand-labelled holdout: **69 pairs** (52 incompatible, 15 exact, 2
  uncertain; Steam Deck, NVIDIA/AMD, consoles, phones/tablets, RAM, storage,
  complete systems, faulty and refurbished). Rejection precision/recall 1.00 and
  exact precision/recall 1.00 on this set. This is a 69-pair sample, not a claim
  of universal accuracy.
- The end-to-end test simulates the old contamination by filing six OLED
  listings under the LCD key, proves all six are rejected, then adds six genuine
  LCD asks and proves the resulting estimate uses only them.
- A separate pipeline test proves an OLED-only LCD candidate has no valuation,
  is not promoted, and sends **zero** Discord notifications.
- The audit is idempotent: a second report run after all repairs showed zero
  identity changes, zero reassignments and zero unresolved evidence.

### Remaining limitations

1. **No completed sales.** All automatic valuations are asking-price estimates.
   The calibration path remains ready but inactive.
2. **The LCD 1TB target has one exact ask.** The system correctly refuses to
   promote it, but it cannot invent a market. Targeted research is needed.
3. **Console edition ambiguity still blocks generic PS5s.** Seven previously
   promoted PS5s are held because their titles do not state Disc or Digital.
   That is a precision choice; review can correct the identity.
4. **Many thin products remain.** 1,841 product keys have one or two comps and
   are labelled insufficient. This is disclosure, not a hidden confidence score.
5. **The holdout is hand-labelled and finite.** It covers 69 pairs; it does not
   prove every future marketplace title will parse correctly.

### Verification performed

- `GET /api/deals/2594` returns the exact LCD 1TB key, one contributing comparable,
  `evidence_level=insufficient`, `is_deal=false` and no rejected OLED rows (they
  were reassigned to OLED keys before pricing, so they never entered the pool).
- SQLite `PRAGMA integrity_check`: `ok`; `foreign_key_check`: clean; journal WAL.
- The repaired database still has 74,489 listings, 3,978 tracked product keys and
  113 CeX quotes; no historical table was reset.

## Session 2026-10-23 — Resale evidence, auction profitability and a 240-listing benchmark

This phase tested whether the resale numbers are real instead of assuming a
formula change made them better. The database, collectors and history were kept.
All estimates below were re-measured after a full 74,395-listing reconciliation.

### Asking-price valuation now strips retail, duplicates and one-market dominance

- New-condition asks are kept out of the used-market basis. They are used only
  as a retail ceiling, and only when at least three exist (92% of their median).
  Sealed stock no longer makes a used item look more valuable.
- Byte-identical offers are collapsed across accounts, each marketplace is
  capped at 55% of total pool weight, and one seller is capped within its pool.
  eBay's volume can no longer define the whole market.
- Established eBay sellers (95%+ feedback with real history) carry weight 2 and
  smaller sellers weight 1. CeX carries weight 2 as a separate reference and is
  never counted as a sold comparable. CeX retail, cash and voucher values stay
  separate in `external_quotes` / `cex_price_history`; retail is converted to
  private-resale level from the product's own observed asks (clamped 0.40–0.95).
- The three resale strategies are explicit: quick, balanced (default) and
  maximum-profit ask. A tested/serviced premium is measured from this product's
  own market (minimum 3 listings per side, capped 15%) and applied only to the
  maximum ask; it is never an assumed percentage.
- The supported range now comes from the same conditioned distribution as the
  central estimate, so a low bound can no longer exceed the estimate.
- A new/sealed candidate is capped at the CeX retail price. Without that ceiling
  a sealed PS5 Pro was estimated at £832.66 while CeX sold the same boxed
  console for £790; the estimate, range and all three strategies now stop at
  the shop retail reference. A private sealed item cannot be assumed to fetch
  more than a shop charges.
- Product-price memory snapshots the market estimate before listing-specific
  contents discounts or learned flip adjustments. Those belong to one deal and
  must not make the next listing of the same product cheaper.
- A form-factor guard was added to comparable validation. Bare "desktop" and
  "SFF" describe a component, not a complete computer; only a real system
  family or configuration is rejected. This raised accepted comparables from
  147,490 public rows to the same cap with 3,357 rejections instead of 9,046.
  Remaining rejections: 2,141 faulty/parts/packaging, 1,192 capacity or variant
  mismatch, 20 complete systems, 4 portable devices.
- Identity fixes that were changing valuations: DDR modules sold as "desktop
  memory" are components, 2x16GB kits are exact products while an unparsed
  "64GB (2x32)" lot is a bundle, a Dell Pro Max 16 with 64GB RAM is a laptop,
  and Pentium/Celeron/Athlon mini-PCs are machines rather than the SSD inside
  them. The last case previously valued a whole Stone NT310-H at £221 as a
  128GB SSD.

### The four previously questionable estimates, re-audited

| Product | Before | Now | Live reference | Verdict |
| --- | ---: | ---: | --- | --- |
| RTX 4060 8GB | £327.53 | £267.48 (range 255.00–272.96, moderate, 8 comps) | CeX retail £300, cash £207, voucher £237; private asks £190–380 | Realistic. Between CeX cash and retail, below most private asks. |
| RTX 4070 12GB | £589.32 | £441.99 (range 411.03–570.46, limited, 7 comps) | CeX retail £590, cash £407, voucher £466; private asks £425–568 | Realistic. The old figure equalled CeX retail; a private used sale should not. |
| Xbox Series S | £255.00 | £208.25 (range 187.42–244.34, moderate, 115 comps) | CeX retail £420, cash £239, voucher £289; asks £239–364 | Slightly conservative versus CeX cash but defensible for a private sale. |
| PS5 Pro 2TB | £829.34 | £624.49 (range 546.43–676.10, moderate, 13 comps) | CeX retail £790, cash/voucher £624; asks £550–955 | Realistic. The old figure was above CeX retail, which was the warning sign. |

Every one of these is still **asking-only**: there are zero completed sales in
the database. The numbers are now defensible used-market estimates, not verified
resale proceeds.

### Auction selection now requires a profitable, evidenced deal

- Discovery results are re-fetched by ID before scanning more (`get_items`, one
  request, at most 20 items). Ended auctions become stale and cannot alert.
- Auction alerts and the urgent recheck shortlist now require `is_deal=1`,
  `needs_review=0` and evidence of at least Limited. A budget-too-low bid is no
  longer treated as an opportunity merely because its current bid is far below
  the stored estimate.
- Live measurement with the same 3-request category budget: 23–25 auctions inside
  30 minutes, 10 distinct products, 6 with fresh price memory. Only 1 was below
  its profitable ceiling, and it had 2 comparables / Insufficient evidence — so
  **0 alert-worthy auctions at that moment**, not 37. With 5 requests: 41
  auctions, 19 products, 12 valued, 2 below ceiling, both Insufficient (1 and 2
  comps) — again 0. More discovered auctions did not mean more profit.
- The Discord label "Net profit at ceiling" was wrong (it showed profit at the
  current bid); it now says current bid. Evidence is shown as Limited / Moderate
  / Strong, never as a confidence percentage that could be read as a probability.

### A 240-listing identity benchmark replaces the 27-example sample

- `scripts/benchmark_listings.py` samples 240 real active listings with a fixed
  seed across marketplace × kind × price band. An independent coarse rule oracle
  labels only unambiguous cases; 127 of 240 stayed **unverified** and were
  excluded from accuracy denominators. Manual overrides record every reviewed
  oracle error, not model output as truth.
- Results: kind accuracy 99/106 (93.4%); valued-decision precision 89/89 (100%);
  recall 89/106 (84.0%); 0 false positives and 17 false negatives on verified
  rows. The one remaining FP candidate was a complete gaming PC that the oracle
  misread because its title said "Stream/ game".
- Systematic weaknesses it exposed: spec-less laptop titles (Dell Inspiron 15,
  ThinkPad R61, HP Stream 14) get no key; legacy DDR2/SO-DIMM part numbers and
  Raspberry Pi RAM are unsupported; component bundles and unusual models are
  conservatively left unvalued. These are recall gaps, not fake deals.

### Permanent price memory

- Snapshots now refresh when older than the reuse window even if no listing
  field changed, preserving every historical row. Current database: 5,966
  snapshots, 367 invalidated rows retained for audit, 1,597 product keys with
  more than one historical snapshot and 25,186 market observations.
- `tests/test_price_memory.py` proves an expired snapshot is re-timestamped
  without losing history, and that a listing's missing-contents discount never
  enters the shared product memory. `tests/test_valuation_integrity.py` covers
  deduplication, seller/market caps, new-stock ceilings and range consistency.
- The drawer now labels each comparable as asking, completed sale or CeX retail
  reference. It already showed sources, dates, conditions, rejected comparables
  with reasons, strategies and the last refresh; CeX is now distinguished
  instead of being mislabelled as a live ask.

### Measured state and verification

- 74,489 listings stored (74,395 active); 74 promoted deals (23 Limited, 51
  Moderate), 934 rows awaiting review; every promoted deal is asking-only, low
  risk, non-bundle, non-faulty and not a wanted advert.
- `pytest -q`: **142 passed**. `npm run build` (tsc + vite): clean.
- Live eBay Browse API calls were used for the auction measurements and for the
  GPU CeX references; no sold evidence was fabricated.

## Session 2026-10-23 — Identity integrity and honest evidence

This session made the identity behind every valuation defensible. The deal
numbers below were measured after the repair pass, not projected.

### Identity is now graded and structurally parsed

- Every listing stores `identity_level` (strong / moderate / limited /
  insufficient) and `identity_basis` (`exact_model`, `capacity_parsed`,
  `vram_parsed`, `variant_parsed`, `model_number`, `cpu_or_gpu_parsed`). The
  grade describes how well the title supports the key; it is separate from the
  old confidence number and is never presented as a probability.
- The classifier reads the subject phrase, so a compatibility qualifier
  ("for Nintendo Switch 2", "fits Dell Optiplex") can no longer donate its
  device to an accessory. Console hardware nouns (controller, dock, faceplate,
  game, media card) win over the console name; complete systems built from a
  component list ("Ultra 5 235 16GB 512GB Win11") are PCs, not CPUs; named
  tablets are devices, not a CPU + RAM + SSD bundle; monitors, network gear and
  bare model lines have their own branches instead of folding into PCs.
- `app/comparables.py` now validates each comparable against the candidate
  before it can price it, and records a rejection reason. Capacity mismatches,
  Ti/non-Ti and Digital variants, accessories, parts-only and compatibility
  listings are rejected instead of blended.
- Regression fixture `tests/fixtures/identity_regression.json` holds 27
  collected titles. The previous classifier scores 18/27 (66.7%); the current
  one 27/27 (100%). `scripts/benchmark_identity.py` reproduces this against
  `HEAD:app/identification.py`.
- `scripts/audit_identity.py` re-applies the classifier to stored rows. Report
  mode is read-only; `--apply` rewrites identities, invalidates snapshots for
  superseded product keys and retires observations filed under an identity the
  listing no longer has. Raw rows are never deleted. Re-running it now reports
  0 pending changes.

### Valuation says what it is based on

- Deals expose `valuation_basis` (`sold`, `asking_only`, `cex_anchor`),
  `evidence_score`, `rank_score` and `rank_tier`, alongside the existing
  evidence level. An asking-only estimate reads as asking-only in the API and
  the drawer.
- The flat asking-only ceiling is gone. It capped every opportunity at 63% by
  arithmetic, so nothing could ever rank higher however strong its sample was.
  Disclosure is now the basis plus the evidence level; the UI never converts
  either into a sale probability.
- A "new" premium is scaled by whether the comparable pool actually contains
  new stock, so a new RTX 4060 cannot be priced from eight used cards.
- `valuation_snapshots` and `market_observations` gained `invalidated_at` /
  `invalid_reason`. `PricingDatabase.latest()` only returns live snapshots, and
  the startup re-identification pass invalidates the old key's cache when a
  listing changes identity. History stays for audit.

### Auction discovery moved to categories

- `EbayCollector.search_auction_categories` requests whole hardware categories
  with `endingSoonest`, rotates one category per request and enforces the
  window locally. Keyword search remains the fallback when the API refuses the
  category filter.
- Measured live with the same 3-request budget: keyword returned 18 in-window
  auctions from 1 distinct product; categories returned 37 from 9 products,
  3 of them at moderate identity or better. Coverage is still budget-bound —
  one pass rotates through only a few of the seven catalogue branches.

### Measured state

- 72,848 listings stored across 3,655 product keys; 18,932 have a usable
  identity (1,020 strong, 9,596 moderate, 8,316 limited); 53,916 remain
  insufficient and are not valued.
- The repair pass retired 1,486 market observations that were filed under an
  identity the listing no longer has and invalidated 136 snapshots on those
  keys; 3,668 snapshots remain live. Nothing was deleted.
- 66 active promoted deals at the time of the check, all identity-verified
  against a fresh classifier pass, confidence 0.589–0.860, sample ≥ 5, all
  asking-only evidence (37 moderate, 29 limited). Rank tiers: 43 decent, 13
  speculative, 10 strong. Live scans keep changing these counts; the ratios are
  what matter.
- 962 listings await review. They are mostly missing-variant or deep-discount
  rows, not parser failures; bulk decisions remain explicit-ID and capped.

### Verification

- `pytest -q`: 128 passed. `npm run build` (tsc + vite): clean.
- Live server (`run.py`) serves the dashboard and `/api/deals` with the new
  fields; a fresh classifier pass over all promoted deals found 0 mismatched
  identities, bundles or faulty rows.
- The identity audit is idempotent, and both benchmarks run read-only against
  the current database.
- Still true: no completed-sale evidence exists locally, so every current
  estimate is asking-only and should be read as such.

## Session 2026-10-23 — CeX, price memory, auctions and personal flips

This session implemented the intelligence layer on top of the existing deal
engine. It did not reset the database or rebuild any collector.

### CeX UK is now a working, exact-identity pricing source

- **Live, unauthenticated search index.** `uk.webuy.com` renders prices from
  `search.webuy.io` (`prod_cex_uk`). A plain POST returns the same rows with
  retail (`sellPrice`), cash (`cashPriceCalculated`), voucher
  (`exchangePriceCalculated`), grade, box ID, category, stock and last price
  change. Verified live from this machine: HTTP 200 for consoles, phones, GPUs
  and handhelds. No HTML scraping and no access-control bypass.
- **Matching was the hard part, and the first live run proved it.** A plain
  "best score wins" rule returned a £700 Halo Infinite Xbox Series X for a
  generic Series X product and a £600 Ghost of Yotei PS5 Slim. It also matched
  "Xbox Series S" against "Xbox Series X" because one-character tokens were
  being dropped. The fix has four parts: single-letter model tokens are kept;
  model tokens are a veto; variant/capacity tokens must match exactly; and the
  quote comes from the least-decorated product cohort (packaging/colour words
  only), with special editions and all-digital models excluded and named in the
  note. A cohort whose own prices disagree by more than 25% is rejected rather
  than blended.
- **Verified live selections:** Xbox Series X 1TB boxed £570 (not £700 Halo, not
  Series S), PS5 Slim 1TB boxed £550 (not £600 Ghost of Yotei), Switch OLED 64GB
  £195, RTX 4060 8GB £300 vs RTX 4060 Ti £375, iPhone 15 Pro 128GB £495.
- **Failure handling.** 401/403/429 mark the source blocked and pause it for an
  hour; network and shape errors fail without a price. `external_quotes` stores
  the status, `cex_price_history` stores each grade observation, and no price is
  fabricated on failure. Negative results (product genuinely not stocked) are
  cached for the reuse window so junk keys are not re-queried every scan.
- **Valuation integration.** CeX is a separate `cex_anchor` source, never a
  sold comparable. Its weight is 2 (configurable), but its retail price is first
  converted to private-resale level using the product's own observed asking
  market (clamped 0.40–0.95 of retail). That replaced the fixed 0.80 factor,
  which overvalued consoles (Xbox retail × 0.8 ≫ private market) and
  undervalued handhelds. With no asking market, CeX is the sole anchor and the
  estimate is labelled as such.
- The temporary `third_party/cex-uk-prices` folder was removed after the port.
  MIT attribution and the full licence text are in `THIRD_PARTY_NOTICES.md`.

### Permanent product-price memory

- `valuation_snapshots` appends one row per product and refresh (resale, range,
  confidence, evidence level, sample, sold count, marketplaces, strategies,
  method). Rows are never overwritten; `GET /api/products/<key>` returns the
  latest, the full recent history and the CeX quote/history.
- Reuse is 7 days (`history_reuse_days`). After that the snapshot is stale and
  the scanner schedules a refresh, deals/ending auctions first, then models
  with the most active listings. `pricing_research` suppresses simultaneous
  research of the same product (15-minute abandoned-claim window).
- Every product's valuation is reused across marketplaces because the key is
  marketplace-independent; a Vinted listing and an eBay listing of the same
  identity read the same snapshot.
- Deal rows carry `valuation_refreshed_at`, shown in the drawer as "price
  memory refreshed …".

### Evidence weighting, strategies and honest confidence

- Seller weight is derived from eBay feedback: ≥98.5% and ≥100 feedback = 2,
  below 95% = 0.5, otherwise 1. A single seller's combined weight is capped at
  30% of the asking pool, so a high-volume seller cannot set the market alone.
- Source diversity and recency feed the score; the fixed repeated "52%" cap is
  gone. Asking-only evidence has a computed ceiling that varies with sample and
  marketplace count, and the UI shows an evidence level plus source counts.
- Three resale strategies are stored per deal and shown with the underlying
  distribution: quick (lower quartile), balanced (current estimate) and maximum
  (upper distribution, only when dispersion supports it). Each has a suggested
  initial asking price separate from the expected final sale.
- Completed flips adjust resale per kind only after 3 sales, shrunk by sample
  count and clamped to ±10% (`app/flips.py`).

### eBay auctions and discovery

- eBay was **live** in this environment. Fixed-price category discovery was
  already working; the parsers were extended for auctions. The critical bug
  found live: Browse returns `price: null` for auctions and puts the figure in
  `currentBidPrice`, so every auction was being rejected. That is fixed and
  verified against production responses (HTTP 200, 50 auctions returned,
  bids/end dates/bid counts parsed).
- Ending-soon scanning uses `buyingOptions:{AUCTION}`, `sort=endingSoonest`,
  limit 200, then filters locally to the configured window because Browse has no
  documented ending-time filter. Two targeted queries per pass rotate from a
  hardware list; broad terms such as "gpu" and "cpu" were measured to return
  coolers and food processors and were removed.
- Auction rows store end time, current bid, bid count and next minimum bid. The
  deal row stores `max_bid`: the lower of (proceeds after selling fees/postage −
  minimum profit) and (proceeds / (1 + required ROI)), with the inbound postage
  included in cost. Shortlisted auctions are rechecked every 5 minutes while
  close to ending. Ended auctions are marked stale and leave the feed.
- No automatic bidding exists anywhere in the codebase.

### My Flips

- New table and API: mark a deal purchased (from the drawer), record actual
  costs, price cuts, listing/sale dates, final price, fees, postage, unsold
  status, and thumbs-up/down with a note. The pre-purchase prediction is copied
  at creation and never rewritten; realised profit, ROI, prediction error and
  days-to-sell are computed from the record.
- Dashboard page: summary (realised vs predicted, mean absolute error, capital
  tied up, average days to sell, learned adjustments), status filters and a
  manual-entry form.

### Alerts

- Discord alerts are typed: new BIN deal, Best Offer (when the API reports it),
  auction ending soon, and a bid increase while still under the ceiling. Each
  includes image, URL, cost, resale, profit, ROI, evidence level and the
  auction/offer ceiling. Auction alerts deduplicate through `auction_alerts`
  with a configurable cooldown (default 180 minutes) and never send ended
  auctions.

### Measured results (live database, 70,915 active listings)

| Metric | Value |
|---|---:|
| Full reconciliation | 70,616 listings in **128.8 s** |
| Automatic deals after the change | 68 |
| Manual review | 913 |
| Stored valuation snapshots | 1,762 products |
| CeX live quotes | verified for consoles/phones/GPUs/handhelds |
| eBay live | category BIN and auction searches both HTTP 200 |
| Auction rows with end/bid data | 48 at first live auction scan (2 queries) |
| Tests | **90 passed** |

An earlier version of the snapshot refresh updated `deals` once per product key;
that scanned the 70k-row table thousands of times and pushed a pass past seven
minutes. It is now one set-based statement with an index on
`deals(product_key)`, which brought the full pass back to ~2 minutes.

### Live verification performed (not mocked)

- CeX: multiple live `search.webuy.io` requests returned HTTP 200 and exact
  product rows; graded retail/cash/voucher values were stored in
  `external_quotes` and `cex_price_history` during the scanner run.
- eBay: token request HTTP 200; fixed-price category requests HTTP 200; auction
  request with `buyingOptions:{AUCTION}` and `sort=endingSoonest` HTTP 200,
  returning current bids, bid counts and end dates. The auction parser was run
  against that production response and 15 in-window auctions were parsed for a
  240-minute test window; the live 30-minute scan ingested and valued 5.
- Dashboard API: `/api/health` exposes `pricing` and `auctions`; `/api/deals`
  returns `evidence_level`, strategy values, `max_bid`, `estimated_days` and
  `valuation_refreshed_at`; `/api/products/<key>` returns snapshot history and
  CeX quote/history.
- My Flips was exercised against the live server: create from a real deal
  (prediction £829.34 resale / £213.90 profit), record a £760 sale with £100
  fees and £12 postage → net £148, ROI 29.6%, prediction error −£65.90, stored
  feedback and price change. The test rows were then deleted, leaving the
  user's flip history empty.

### Honest limits of this session

- **Auction supply is thin in the 30-minute window.** Two targeted queries
  returned 5 auctions on the first pass; none was below its profitable ceiling
  (e.g. a PS5 at £402 against a £209 ceiling). The shortlist was therefore empty,
  which is the honest outcome, not a parser failure. Broader coverage needs more
  queries per pass and therefore more API budget.
- **CeX retail is a reference, not a sale.** With no completed-sale evidence the
  XBOX/PS5 asking-only estimates still carry Limited/Moderate evidence. CeX cash
  and voucher offers are stored and displayed but are not used as resale values.
- **No genuine property sold data exists in this database.** The estimator
  remains asking-price based; this session did not invent sold prices.
- **eBay Best Offer support depends on the API returning `BEST_OFFER` in
  `buyingOptions`.** The maximum offer equals the same profit/ROI ceiling as a
  bid; it is not a separate negotiation model.
- **The legacy 547 `is_auction` rows** collected before the auction parser fix
  have no end time. They are not shortlisted (the watchlist requires an end
  time) and will age out through normal stale-listing maintenance.
- **Price memory is per product key, not per listing.** A product with several
  variants under one key (where identification could not separate them) shares
  one snapshot; the variant review gate still blocks automatic deals for those
  listings.
- Live CeX requests come from this network, so a future 403 is possible. It is
  surfaced on the Scanner page and pauses lookups instead of failing scans.

### Exact continuation point

1. Add more targeted auction queries (or paginate) and measure whether the
   30-minute window is reliably non-empty; the current loop and ceiling math are
   finished and tested.
2. Optionally convert CeX cash/voucher offers into a separate `trade_in_value`
   field on the deal drawer; they are stored already.
3. Import or record real sold evidence so the evidence level can reach Strong;
   nothing in the new code blocks that.

## Session 2026-10-23 — discovery and deal-pipeline audit

Before changing thresholds, the pipeline was measured listing by listing on a
frozen 60,863-listing snapshot (`scripts/audit_pipeline.py`, read-only). The
same snapshot was then re-identified and revalued with the previous code and
with the new code, each with a full reconciliation. This is the first
apples-to-apples comparison on identical collected listings.

### Audit findings (same 60,863 listings, previous code)

- 11,437 listings had an exact product identity (18.8%); 10,026 were valued and
  8,023 had at least four comparables. Only **21** cleared every deal gate, while
  **2,570** sat in manual review.
- The dominant review reason was not a suspicious price. It was
  `variant_unclear`: the risk model sent *every* phone/tablet with an unstated
  capacity to review, including hundreds priced at the normal market level.
- 1,993 review rows carried the two variant sentences as their entire review
  reason string. The queue was bookkeeping, not triage.
- Identification lost real listings for mechanical reasons: 8/16/32GB legacy
  capacities were ignored (iPhone 6/7, iPad 2–5), games such as `MW2 ps3` and
  `Skyrim - PS3` were read as consoles, cases containing `Plus` (`iPhone 16 Plus
  Phone Cases`) were read as handsets, and a laptop mainboard became a laptop.
- 48,282 listings had no identity. Sampling them confirmed that most are phone
  cases, games, media, cables and unrelated products; the console cue alone
  matched 8,170 of them. Category scanning was also pulling whole accessory and
  games branches (Vinted Electronics, eBay Video Games, Gumtree audio/cameras).

### What changed

- [x] **Discovery targets devices, not departments.** The catalogue is now 28
      hardware targets: 15 Vinted device subcategories (laptops, desktops, GPUs,
      CPUs, RAM, motherboards, SSDs/HDDs, monitors, phones, tablets, consoles,
      routers/mesh/switches), 3 Gumtree hardware categories and 10 eBay hardware
      branches. Accessories, games, audio, cameras and TV branches are gone.
- [x] **Keyword discovery is hybrid-only.** In category mode the old two-keyword
      supplement no longer runs, so a category cycle cannot refill the feed with
      accessories and games through the back door. It remains available when the
      scanner is deliberately set to hybrid discovery.
- [x] **New noise is not stored.** New listings that identify as an accessory,
      game, packaging, compatibility item, parts-only device, wanted ad or
      multi-item lot are skipped before insert. Existing rows are untouched and
      expire normally, so collected history is preserved.
- [x] **Structural accessory detection.** A model name inside an accessory title
      is no longer evidence of the device. `plus` is treated as a model suffix,
      not a bundle conjunction; a bundled accessory must follow an explicit
      "with/includes" phrase; accessory nouns before that phrase, case lots,
      covers, bags, styluses, `etui`/`hulle`/`funda`, swivel bases and
      packaging-only colour descriptions are excluded. `Pixel 8 Phone Case with
      Stand` and `iPhone 16 Plus Phone Cases` no longer become handsets.
- [x] **Games versus consoles.** A short title with an unexplained word (`MW2
      ps3`, `PS3 Singstar`, `Skyrim - PS3`) is no longer hardware; `PS5 with 2
      games` and `PlayStation 5 Slim + 4 Games` remain console bundles; `PS5
      game bundle` remains a game bundle. A component-first title (`RAM 64GB …
      (PS5 Pro exchange)`) can no longer become a PS5.
- [x] **Variant resolution where evidence exists.** Legacy 8/16/32GB capacities
      now form real phone/tablet keys, resolving older iPhones and iPads instead
      of queueing them. Laptop mainboards are parts, not laptops, and a CPU sold
      with RAM, SSD and Windows is a complete machine rather than a £60 CPU.
- [x] **Cracked devices are valued as fair, not used.** `cracked/chipped` sets
      condition `fair` and a caution flag, so a cracked-screen handset is no
      longer compared with good-condition stock and promoted as a bargain.
- [x] **Review is triage, not a variant to-do list.** `variant_unclear` still
      lowers confidence, still blocks automatic recommendation and still appears
      in the listing's risk reasons. On its own it no longer opens a manual
      review; only a genuine price anomaly does. Normal-price phones with
      unstated storage leave the queue.
- [x] **Deep-discount review compares like with like.** The "far below market"
      review trigger now also requires the asking price to be below 60% of the
      listing's own modelled resale, instead of only below the ask-pool median.
      The pool median is pulled up by boxed/new stock, which was sending genuine
      used console deals (Xbox Series X at £250 vs £340 modelled) to review.

### Benchmark (identical 60,863 listings, full revaluation)

| Metric | Previous code | New code | Change |
|---|---:|---:|---:|
| Exact product identities | 11,437 (18.8%) | 14,408 (23.7%) | +2,971 (+26%) |
| Distinct products | 2,599 | 3,094 | +495 (+19%) |
| Valued listings | 10,026 | 12,749 | +2,723 (+27%) |
| At least 4 comparables | 8,023 | 10,332 | +2,309 (+29%) |
| Automatic deals | 21 | 37 | +16 |
| Manual review queue | 2,570 | 784 | −1,786 (−69%) |
| Unexplained listings | 48,282 | 45,404 | −2,878 |

Deal-set comparison: 13 deals are common, **24 are new** and **8 left**. Three
of the eight removals were false positives found by the audit (a RAM kit priced
as a PS5 Pro, a Dell laptop mainboard priced as a laptop, and a cracked-screen
S22 Ultra priced as good condition). The other five stopped passing after the
comparison pool changed or the bundle gate applied; the largest was a PS5 Slim
+ 4 games bundle at £280. New deals are mostly consoles, phones, GPUs and
complete PCs (RTX 5070 Ti £450, PS5 Pro £500, iPhone 15 Pro £204, Steam Deck 1TB
£180, Xbox Series S £140–150), plus two RAM kits and two SSDs.

### Honest limits

- Every valuation is an **asking-price estimate**. No completed-sale evidence is
  invented; deals with no sold support say so. Vinted still has no sold feed.
- The riskiest new deals are cheap handsets and a Switch OLED at £79. Their
  prices sit within the current asking-market spread, so the existing risk model
  does not flag them; they are not evidence of a completed sale either. The
  confidence and review details remain visible in the drawer.
- 45,404 rows still have no identity. Most are accessory/game/media rows
  already collected; new scans no longer store them. eBay category discovery is
  still unverified without API keys.

### Verification

- **76 tests pass**, including new regressions for accessory-first titles,
  legacy capacities, console games vs bundles, laptop mainboards, cracked
  condition and variant-only review policy.
- Frontend `tsc -b && vite build` passes.
- Live database (preserved, not reset): the new rules were applied with a full
  reconciliation. At the last check the running app reported **62,771 active
  listings, 37 deals and 816 review items**; all three marketplaces were healthy.
- A pre-audit copy was kept at `data/rigradar.pre-audit.db`; the audit script
  (`scripts/audit_pipeline.py`) is read-only and can be rerun against any
  database copy to reproduce these numbers.

## Session 2026-10-23 — grouped review queue

The review queue reached roughly 2,000 rows, and roughly half of them were the
same failure repeated by model: a phone listing that never stated its storage.
One card per listing made that pattern invisible. Reviews can now be grouped,
filtered and resolved in explicit batches without weakening the deal gate.

### What changed

- [x] **Grouped variant audit.** `GET /api/reviews/groups` groups pending
      `variant_unclear` phones by `product_key` and returns the count, price
      range, marketplaces, oldest/newest sighting, short examples and the
      explicit listing IDs. Only pending rows with no existing decision are
      included.
- [x] **Drill-down without losing the review filter.** The deal API accepts
      `product_key` and `review_flag`, so selecting a group shows exactly that
      model's missing-storage rows rather than every pending review for the
      model. Clearing the group restores the full queue.
- [x] **Bulk decisions are explicit and bounded.** `POST /api/reviews/bulk`
      accepts at most 200 listing IDs, only applies to rows that are still
      active and pending, skips stale or already-decided IDs and reports the
      counts. There is no filter-based bulk update, so a changed queue cannot
      silently widen an action. Bulk corrections are deliberately unsupported;
      each corrected product key needs its own evidence.
- [x] **Bulk approval does not inflate confidence.** One valuation refresh runs
      after a batch of approvals. The missing-variant risk note remains, so an
      approved listing still has to clear the normal confidence and evidence
      gates before it can be recommended.
- [x] **Review-group panel in the deal feed.** The review queue now shows the
      models with the most missing-storage listings, their price spread and
      source mix, plus Review / Reject / Approve actions. The active group has a
      compact header with counts, age, share of variant gaps and the same
      actions. Destructive actions require an explicit confirmation.
- [x] **Parser cleanup found during the review.** Common `256G` phone shorthand
      now identifies as 256GB instead of `variant_unclear`. Case/protector
      brands (`BodyGuardz`, `Caudabe`, `Velvet Caviar`, `Loopy`) and plural
      `protectors` are treated as accessories, not handsets. Startup
      re-identification applied the fix to existing rows.

### Verification

- **72 tests pass** (69 before this session's additions). New coverage:
  grouping by model, exclusion of decided and known-storage rows, filter by
  product key and variant flag, bulk idempotency, stale-ID skipping, rejection
  and approval paths, the 200-row cap, and rejection of corrections in bulk.
- Frontend `tsc -b && vite build` passes.
- Live API on the running database returned **60 model groups / 968
  missing-storage listings** at the last check, while the full pending review
  queue was **2,067**. The grouped panel rendered those groups, and selecting
  iPhone 11 filtered the feed to its 56 pending variant rows.
- Re-identification at startup changed 33 existing rows; Vinted and Gumtree
  remained healthy and the scanner continued running.

### Current queue shape

The dominant modelled families are recent iPhones (11 through 17 Pro Max),
but the group view also exposes small groups and the examples make clear when
an individual title is still an accessory or a box rather than a handset.
Grouping is an audit aid, not an automatic approval: bulk approval is offered
with the same warning that no evidence or confidence is added.

## Session 2026-10-23 — category-first incremental discovery

Keyword searches are now the secondary discovery path. The primary path is a
verified category catalogue with resumable backfills and newest-first
incremental scans.

### What changed

- [x] **New category catalogue** in `app/categories.py`: 34 browse targets
      (10 Vinted, 6 Gumtree, 18 eBay). Category IDs come from Vinted's public
      catalogue sitemap and live Gumtree navigation; eBay entries use documented
      branch/leaf IDs. Gumtree's `cds-dvds-games-books` branch was dropped because
      it is media, not hardware.
- [x] **Keyword-free collectors.** Vinted uses `/catalog/<id>-<slug>` with
      `order=newest_first` (96 cards/page). Gumtree uses `/for-sale/<slug>` with
      `sort=newest` (30 cards/page). eBay Browse sends `category_ids` with no `q`,
      `sort=newlyListed`, limit 200 and one category per request.
- [x] **Live verification of every Vinted and Gumtree category target.** All 10
      Vinted branches returned 96 cards and all 6 Gumtree branches returned
      29–30 cards on page 1 (HTTP 200). Vinted stops serving pages after page 10;
      that cap is treated as partial coverage, not as the end of the branch.
- [x] **Incremental state per marketplace/category** (`category_scans`,
      `listing_categories`): mode, backfill cursor, depth, interval, due time,
      last items/new/duplicates/requests/duration/error and coverage.
- [x] **Separate backfill phase.** One unfinished category runs per cycle to the
      configured depth (10 pages), then normal incrementals resume. Backfills are
      resumable and a capped pass is marked `partial`; a branch that genuinely
      ends inside the depth window is marked `full`.
- [x] **Newest-first incremental scans** use one page across due categories and
      stop after a safety page when the overlap threshold (75%) is reached.
      Page 1 stops immediately only when it contains no new listings at all, so
      delayed/reordered results cannot hide a second fresh page.
- [x] **Dedupe inside a pass and across categories.** Known IDs from SQLite plus
      IDs already seen in the same pass feed the overlap rate; the database's
      marketplace/external-id constraint prevents duplicate rows.
- [x] **Adaptive intervals** of 3–20 minutes based on new-listing yield, with
      priority categories first. Inactive categories back off to 20 minutes;
      high-yield categories stay at 3 minutes. A 12-request cycle budget and a
      cycle scheduler prevent stampedes.
- [x] **eBay quota guard.** eBay is capped at 4 Browse requests per cycle
      (Vinted/Gumtree: 12). Existing quota headers, 429 handling and low-quota
      pausing still apply.
- [x] **Keyword searches retained as a secondary mechanism.** Two rotating
      phrase searches per cycle cover miscategorised listings and gather
      valuation comparables. They no longer decide what the scanner sees.
- [x] **Diagnostics** in Scanner health: per-marketplace verification badge,
      full/partial coverage counts, due categories, per-category mode, pages,
      interval, new, duplicate rate, last success, next due, last error and a
      direct browse link. The latest pass shows overlap, stop reason and rows.

### Measured coverage gain

- The catalogue gained **11,657 category-discovered listings**; 11,654 of them
  remain active. Total active listings rose to **32,849**.
- **80.5%** of those category-discovered titles contain none of the 89
  configured keyword phrases verbatim (for example projectors, smart TVs and
  older GPUs). Keyword search may still surface some of them through relevance,
  but the phrase list is demonstrably not a complete discovery mechanism.
- All selected Vinted/Gumtree branches produced stock on the first live request.
  No empty or unsupported branch was added to the catalogue.

### Benchmark: keyword vs category on matched live snapshots

Method: copy the live database, issue exactly 6 newest-first requests, ingest
the results into the copy, and compare. Keyword runs use six broad phrases;
category runs use page 1 of six branches. Repeat rate includes rows already in
the database plus repeats inside the same result set.

| Source | Method | Requests | Rows | New | New/request | Req/new | Repeat rate | Time | Categories |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Vinted | keyword | 6 | 576 | 129 | 21.5 | 0.047 | 77.6% | 9.9 s | 0 |
| Vinted | category | 6 | 576 | 130 | 21.7 | 0.046 | 77.4% | 8.4 s | 6 |
| Gumtree | keyword | 6 | 184 | 51 | 8.5 | 0.118 | 72.3% | 10.2 s | 0 |
| Gumtree | category | 6 | 179 | 28 | 4.7 | 0.214 | 84.4% | 10.2 s | 6 |

Initial live backfill throughput was much higher because it reached pages the
keyword scanner had never covered:

| Source | Requests | Rows | New | New/request | Duration |
| --- | ---: | ---: | ---: | ---: | ---: |
| Vinted (2 branches × 4 pages) | 8 | 768 | 726 | 90.8 | 13.4 s |
| Gumtree (2 branches × 4 pages) | 8 | 240 | 142 | 17.8 | 21.4 s |

Interpretation: in a mature, category-scanned database the two methods have
similar page-1 yield on Vinted; Gumtree's broad keyword searches still beat six
category page-1s for raw new rows. The category win is breadth and title
independence, not an automatic multiplier on every request. Keyword searches
remain useful as a secondary comparator/miscategorisation path, which is why
both are kept. The 11.6k category finds and 80.5% phrase-miss rate are the
measured coverage improvement.

### Verification and limitations

- Regression suite: **69 passed, 0 failed**; frontend `tsc -b && vite build`
  passes. New tests cover category URL/card parsing, bounded backfill depth,
  one-category backfill scheduling, page-1 overlap stop, partial coverage,
  eBay `category_ids` without `q`, and category diagnostics.
- **eBay category discovery is implemented but not verified live** because this
  workspace has no eBay developer keys. The dashboard labels it `unverified`
  rather than pretending it works. No eBay Browse calls were made; quota
  consumption is therefore zero in this pass.
- **Vinted exposes at most 10 pages per branch**, so large branches are
  continuously covered at the newest edge but are not exhaustively crawled. The
  UI marks these `partial` unless a branch genuinely ended inside the window.
- **Gumtree** has no page cap observed in this pass; backfills stop at the
  configured 10-page depth and are marked partial when more pages exist.
- Category pages contain accessories, media and occasional off-category items
  (for example phone cases in the phones branch). Identity/risk rules and the
  deal gate still filter them; category discovery deliberately favours recall.
- **No API quota was consumed** by Vinted/Gumtree (HTML, not API) and eBay was
  unconfigured. The eBay per-cycle cap is 4 requests to stay inside a free
daily quota once keys are added.

## Session 2026-10-23 — reliability pass

This pass fixed several real data-integrity and false-positive causes observed
against live marketplace pages. It did not replace any working collector,
delete listings or reset the database.

### Fixed and verified

- [x] **Gumtree data corruption fixed.** Gumtree search pages contained 26 cards
      but only 25 JSON-LD URLs; a promoted card shifted every later pairing and
      the old fuzzy matcher attached the wrong URL to a title/price. One tracked
      URL alternated between a £150 ROG Ally and a £390 ROG Ally X on successive
      scans. The collector now reads the authoritative JSON-LD ItemList (canonical
      URL, title, price, image, description, condition) and ignores promoted
      cards. The HTML-card path remains only as a fallback with a strict
      multi-token URL match; unmatched cards are dropped. Live verification:
      25 unique listings parsed for "rog ally"; the previously corrupted row
      returned to its correct £390 ROG Ally X title, description and URL.
- [x] **Gumtree empty searches no longer look like parser failures.** Live
      "0 ads for intel arc gpu" pages are classified as genuinely empty. The
      obsolete degraded scan disappeared on the next live cycle.
- [x] **Valuation refresh cannot be skipped after a second source ingests.**
      Refresh used to be suppressed by a 45-second timer; if two collectors'
      ingests landed seconds apart, the second source's price/identity changes
      stayed behind a stale deal row. A monotonic data-version counter now makes
      refresh run whenever listing data changed, and startup always revalues
      once so previous-session deal rows cannot outlive changed rules.
- [x] **Complete PCs no longer classified as loose GPUs.** Real listings using
      "Gaming Computer PC Desktop" or compact "Ryzen 5600X" were in the GPU
      comparable pool at full-system prices. PC cues and compact Ryzen parsing
      were fixed; both live titles now identify as `pc:` systems.
- [x] **Storage/variant-aware phones and MacBooks.** Bare capacities such as
      "iPhone 13 128" are parsed, and storage is part of the product key for
      iPhones, Galaxy and Pixel phones. Missing phone storage, iPad storage,
      MacBook RAM/storage, or the Intel-versus-M1 ambiguity on 2020 MacBooks adds
      `variant_unclear`. These listings stay identified but go to review instead
      of an automatic deal.
- [x] **Contents caveats affect value.** "Console only", "no controller or
      cables", "missing charger" and similar wording adds `contents_incomplete`,
      reduces the supported resale estimate and range by 12%, and records a
      visible risk reason. A complete and an incomplete console are no longer
      valued identically.
- [x] **Risk scoring no longer relies on a single bad comparable.** The price
      anomaly compares the candidate with the comparable median (robust to one
      junk/scam ask) instead of requiring a low percentile. Deep asking-only
      discounts (≤65% of median) go to review; sold evidence is exempt. The
      "percentile 0" truthiness bug that silently disabled this review path was
      fixed.
- [x] **Asking pools trim desperate low outliers.** A Tukey lower fence removes
      isolated £40–£60 asks that do not represent the working market. It is
      applied per pool, not by a fixed fraction of the median, so a genuine
      downward trend is not deleted.
- [x] **Review approvals are no longer dead ends.** Approved/corrected listings
      leave the review gate and can become eligible deals without any confidence
      boost; the automatic discount penalty remains. Rejected listings stay out.
      The review-history queries now retain approved items after they become
      deals. Approval, correction and undo trigger revaluation immediately.
- [x] **Gumtree descriptions and conditions are now stored.** This improves
      contents/fault/accessory detection compared with title-only parsing.
- [x] **Valuation is now incremental.** Each listing carries a fingerprint of
      the fields evaluation reads, and each product key carries a signature of
      its comparable pool (listing counts and price sums by condition, market
      observations, sold evidence, CeX quotes and the GPU dependency used by
      low-comp complete PCs). A refresh re-evaluates only new/changed listings
      and keys whose evidence moved. Stale deal rows are cleared after the pass,
      not before, so the feed never empties. A full pass still runs on first
      start, after any evaluation-logic change (`VALUATION_REVISION`) and every
      six hours, which also absorbs time-decay and aged-out evidence.
- [x] **Risk scoring uses the full comparable distribution.** It previously
      measured price position against the twelve-row display slice, so a verdict
      could flip depending on which marketplace was refreshed last. It now uses
      the complete condition-adjusted pool carried by the estimate, falling back
      to the display rows only when no pool values exist.

### Regression coverage added

- Gumtree JSON-LD parsing with a promoted £829.99 card that must not borrow
  either canonical URL.
- Gumtree strict fallback URL matching and "0 ads" empty-result classification.
- Refresh invalidation after ingest.
- Complete PC versus GPU for the two live titles that regressed.
- Compact Ryzen model keys.
- Phone storage parsing and unknown-storage review flags.
- MacBook M2 / 2020 chip ambiguity flags.
- Contents-incomplete detection for console-only listings.
- Deep-discount review at percentile zero, and review approval promotion without
  a confidence increase.
- Asking-pool low-outlier trimming.
- Incremental refresh: a no-op pass evaluates nothing, a price change only
  re-evaluates its own product key, new sold evidence re-evaluates the affected
  key, and the six-hour reconciliation forces a full pass.
- Risk scoring against the full comparable distribution rather than the
  twelve-row display slice.

## Session 2026-10-26 — eBay coverage, untested RAM and auction review repair

### eBay UK discovery

The fixed-price pass was searching the right kind of category but spending only
a few requests per cycle and mixing storage leaves into one broad parent. The
corrected settings now reserve 1,600 of the documented 5,000 daily Browse calls
for auctions and divide the remainder across the cycles left in the day. The
saved configuration has 18 enabled leaves, which resolve to **24 eBay browse
branches** (previously 16). The per-cycle budget is derived from the remaining
allowance rather than the old hard-coded four calls.

Real eBay leaf categories now used where the old mappings pointed at a mixed
parent:

- SSDs: `175669` Solid State Drives (not the HDD/SSD/NAS parent `182085`)
- Internal HDDs: `56083`
- Other storage: `175670` and `182093`
- Network cards: `20318` and `42196` in addition to `45002`
- Computer cases: `42014` in addition to `175674`
- Capture devices: `175679` in addition to `3761`
- Server memory: `11210` in addition to `170083`

Each category has a newest page, a rotating deep page and rotating price bands,
so mid-range stock is not buried under the cheapest items. A fresh run on
2026-10-26 found **1,643 new listings in the first 45 minutes** (1,637 eBay Buy
It Now, 4 Vinted, 1 Gumtree, 1 eBay auction). A live top-of-page audit of 1,135
unique eBay results found 909 already stored (80.1%), up from 895 before the
leaf mapping and targeted scan. The remaining 226 were 80 outside the saved
preset, 12 over the saved purchase budget, 126 with no exact product identity
and only **8 addressable misses** inside an enabled branch, down from 25.

Browse usage was **123 calls** at the end of the pass, so the documented 5,000
allowance is not the limiting factor. eBay does not return a usable remaining
quota header on these Browse responses; the local request ledger is the source
of truth until the developer portal is checked. The eBay UK price filter needs
`priceCurrency:GBP`; without it eBay silently ignores the numeric range. This
was verified live and is now covered by a regression test.

### Untested RAM and laptop comparables

The eBay condition label (`For parts or not working`) was being copied into the
listing description. Identification then treated the label as seller-confirmed
fault evidence. Search summaries now keep the label in `condition_raw` only,
and a generic condition label alone is not fault evidence. Seller detail is
fetched for a bounded set of condition-ambiguous listings. The targeted RAM
repair corrected 69 stored RAM conditions, revalued 72 rows and retired no
historic observations. There are no remaining RAM deals with the old £6-ish
resale estimate. A live Corsair Vengeance RGB Pro 8GB DDR4 listing that actually
states it will not POST is still correctly rejected as faulty.

Laptop RAM was being rejected because its title contained the word `laptop`.
The comparable gate now lets the classifier decide whether the title is a
complete portable device or a component. Across the stored RAM observations,
773 titles contained `laptop`/`notebook`/`tablet`/`phone` and would previously
have been rejected on that word alone; **766 are now accepted**, and the other
seven are rejected for real reasons (faulty/parts, or a capacity mismatch).
The live database has 729 laptop-RAM listings and 366 laptop-RAM valuation
snapshots, with typical sample sizes of 15–46 observations.

### Auctions and review

Live auctions no longer enter Human Review. The API filters them from the review
list, count and bulk decisions, and a targeted repair cleared the stored
auction review flags (0 remain). The five-minute auction loop, the 15-minute
alert window and the separate Auctions tab are unchanged.

### New-listing gate

All 1,643 listings collected on 2026-10-26 were automatically excluded; none
were promoted and none entered Human Review. The dominant reasons were: 502
where the physical product could not be established, 451 accessories or parts,
306 with no expected profit after costs, 300 without an exact model, 30 with
insufficient compatible evidence and 7 below the saved thresholds. This is the
intended precision-over-volume behaviour, not a claim that every excluded item
is junk. The 126 unidentified items in the coverage audit show the remaining
identification gap, especially cases, capture devices and network cards.

### Verification

- Backend: **230 passed, 0 failed** (`python -m pytest -q`).
- Frontend: `npm run build` passed (`tsc -b && vite build`).
- Database: 128,160 listings retained; no reset or deletion of historical
  observations, snapshots, flips or review decisions.
- Live dashboard: API health 200 and frontend 200 after restart; startup
  maintenance completed in 1.0 s because the recent valuation was reused.

### Remaining limitations

- The live quota header is unavailable, so the 5,000 allowance is a documented
  default rather than an observed remaining balance.
- The full valuation reconciliation is still expensive on this 128k-listing
  database. A scan can spend minutes revaluing changed keys; the startup path now
  skips an already-current pass, but the six-hour full pass still needs a
  chunked scheduler that yields to HTTP.
- 126 of 1,135 audited live results had no exact product identity. Adding
  identities for cases, capture devices and network cards is the next coverage
  win, not a reason to weaken the eligibility gate.

## Previously completed and still working

- [x] FastAPI backend with SQLite WAL persistence and additive migrations.
- [x] Independent Vinted UK and Gumtree UK collectors with robots.txt checks,
      per-host pacing, retries, 429/5xx handling and a circuit breaker.
- [x] Official eBay UK Browse API collector. It reports `not_configured` until
      `EBAY_CLIENT_ID` / `EBAY_CLIENT_SECRET` are saved. No eBay HTML scraping.
- [x] Facebook collector stub that reports `unavailable`; no fabricated rows.
- [x] Settings → Data sources saves eBay keys in the gitignored `.env`
      atomically with owner-only permissions, activates the collector without a
      restart, supports a real read-only connection probe and clears keys safely.
- [x] API usage accounting: daily request/error counters, last request/success,
      provider quota headers when present, quota state `unknown` when absent,
      low/exhausted pausing and clear authentication/rate-limit messages.
- [x] Category-first discovery with a secondary rotating phrase search across
      GPUs, CPUs, RAM, SSDs/HDDs, complete PCs, laptops, consoles/handhelds/retro,
      motherboards, monitors, phones, tablets and networking. Vinted category
      pages return up to 96 cards; Gumtree 30; eBay Browse up to 200 per request.
- [x] Stage-level scan telemetry and per-query timings, parsed rows, new versus
      duplicate counts, errors, last success/duration and next scan.
- [x] Generation/configuration-aware identification for GPUs (VRAM), CPUs,
      RAM, storage, consoles, PCs, MacBooks, iPads, phones and monitors, with
      accessory/box/faceplate/controller/game/Kinect/compatibility protections.
- [x] Completed-sale evidence engine: CSV import/export with validation and
      preview, one-off sold entry, provenance, duplicate suppression, confidence
      caps, evidence-gated category calibration and held-out validation.
- [x] Review queue with approve/reject/correct, persisted decisions, corrections
      that survive rescans, and automatic-exclusion from the deal feed.
- [x] Cost model: purchase price, delivery/buyer fee, resale channel fees,
      outbound delivery, packaging and other costs.
- [x] React dashboard: ranked feed, deal drawer, comparable evidence, review
      queue, Evidence Lab, scanner health/API usage, scan history and settings.
- [x] Discord webhook/browser notifications (optional).
- [x] Production frontend build passes.

## Verified live state at last check

| Metric | Value |
| --- | --- |
| Active listings in local DB | 74,395 |
| Qualifying deals | 67 |
| Manual review queue (pending) | 937 |
| Missing-storage phone variants | see grouped review |
| Probable cross-market duplicates | 63 |
| Tracked product keys | 3,978 |
| Category-discovered listings | 50,969 |
| Completed-sale evidence rows | 0 |
| Sources healthy | Vinted, Gumtree, eBay |
| eBay | Live with production keys (category BIN + category auctions verified) |
| CeX | Live via the public search index; retail/cash/voucher stored |
| Facebook | Unavailable; requires authenticated session |
| Tests | 175 passed, 0 failed |
| Frontend build | Passing |

The highest-ranked current promotion is an ASUS ProArt RTX 5090 32GB with a
£5,515 resale estimate from 7 comps (limited evidence); it is still an asking-only
estimate, not a completed sale. All 67 promotions are labelled Asking only and
none claims a completed sale. Deep discounts that fail a gate stay in the review
queue rather than being presented as top-ranked profit.

## Valuation evidence status

| Evidence | Works? | Notes |
| --- | --- | --- |
| Live asking prices (Vinted/Gumtree/eBay) | Yes | Automatically collected; lower-tail trimmed, condition-normalised and clearly labelled asking-only. |
| Bulk CSV completed sales | Yes | Evidence lab → choose CSV → preview → commit. Validated, idempotent, provenance retained. |
| Manual verified sold prices | Yes | Deal drawer → Verified sold evidence, or `POST /api/comps`; valuation refreshes immediately. |
| Category calibration | Ready, inactive | No matched sold evidence yet. API reports `insufficient` and keeps the 85% fallback. |
| eBay Browse API active listings | Live | Active asks and auctions from production keys; Browse API does not supply completed sales. |
| eBay Marketplace Insights (sold items) | Not available | eBay documents the API as restricted and not open to new users; no bypass is attempted. |
| eBay UK completed-sales pages | Not implemented | eBay directs automated access to its API and protects sold-search HTML. |
| CeX retail anchor | Live | Public search index; converted to private-resale level and labelled a retail anchor, never a sale. |

No automated, legitimate, free sold-price source is available to this workspace.
The honest automatic path is robust asking-price evidence plus confidence caps and
review. Manual CSV remains optional and is not required for scanning or valuation.

## Marketplace status

| Source | Collection | Notes |
| --- | --- | --- |
| Vinted UK | Working | Category browse verified on all 10 branches; up to 96 cards/page, newest first, marketplace cap at page 10. |
| Gumtree UK | Working | Category browse verified on all 6 branches; 30 cards/page. JSON-LD is authoritative on search pages, per-card anchors on category pages. |
| eBay UK | Working | Official Browse API category search (`category_ids`, no keyword), 200/page, 4 requests/cycle, quota/backoff; category auction search rotates one branch per request. |
| Facebook Marketplace | Unsupported | Login required; automated collection is not attempted. |
| CeX UK | Working | Undocumented public search index; retail/cash/voucher by grade, paused on 403/429. |

## Measured scan performance (keyword-cycle baseline, before category discovery)

| Source | Items | Total | Collect | Ingest | Valuation | Duplicates |
| --- | --- | --- | --- | --- | --- | --- |
| Vinted | 1,144 | 22.6 s | 18.9 s | 0.5 s | 2.6 s | 0.005 s |
| Gumtree | 304 | 25.4 s | 22.2 s | 0.2 s | 1.6 s | 0.005 s |

That baseline ran 12 keyword queries. Category-first cycles now issue up to 12
category page requests plus two secondary searches; live category scans measured
13–21 s for a backfill pass and 6–14 s for an incremental pass (see the benchmark
above). Collection remains paced at roughly 1.4–1.8 seconds per request; that is
deliberate rate-limit protection, not an application bottleneck. Valuation is
incremental: only changed listings and product keys whose comparable pool, sold
evidence or CeX anchor moved are recomputed. The six-hour full reconciliation
takes about 25 s at ~21k listings and runs in the same serialised refresh slot.
Measured at that time on a copy of the live database: first/full pass **25.3 s**
for 20,811 deal rows, a no-op pass **0.7 s**, and a single price change **0.7 s**
(four listings in one key). The incrementally maintained deal rows then matched
a fresh full recomputation exactly (0 differences).

## Known limitations

1. **No saved sold evidence yet.** The evidence and calibration systems are ready,
   but this database has zero completed sales. All current deals are asking-only
   at Limited or Moderate evidence; the drawer states that limitation. Import real
   sales to change it.
2. **Deep discounts intentionally go to review.** A listing below 65% of its
   comparable median, or with missing storage/RAM/chip details, is not called a
   top deal automatically. This is a deliberate precision-over-volume choice; the
   review queue is the path to promote a verified genuine bargain.
3. **Calibration needs volume.** At least 2 sold observations and 4 recent asks per
   matched product, at least 5 products per category and held-out error below 15%.
   Otherwise it stays inactive and explains why.
4. **eBay is live but rotation-bound.** Category fixed-price search and category
   auctions were exercised against production keys. Auction coverage is limited
   by the request budget, so one pass only reaches a few category branches and a
   short auction can end before its branch comes round.
5. **CeX is live but unverified by contract.** It is an undocumented public
   search index; when it is blocked or changes shape the quote is marked failed
   and valuations continue from live comparables.
6. **Vinted descriptions are not exposed** in catalogue cards, so condition and
   contents detection there relies on titles and card condition. Gumtree now
   supplies descriptions.
7. **Valuation refresh is incremental with a six-hour full reconciliation.**
   A scan re-evaluates only changed listings and affected product keys, so the
   usual cost is 1–3 seconds. The full pass (~25 s) still runs on first start,
   after evaluation-logic changes and every six hours to absorb recency decay;
   the first pass after an upgrade is therefore slower.
8. **Duplicate detection is conservative** and only flags different-marketplace
   rows with matching product keys, similar titles and prices within 2.5%.
9. **Rule-based identification can still miss unusual titles.** New review cases
   should become fixtures instead of one-off keyword patches.
10. **Single-user SQLite architecture**, not designed for multi-writer hosting.
11. **The review queue is still large outside missing-storage phones.** Grouping
    covers the dominant variant gap; price anomalies, accessories and other
    reasons still appear individually. Bulk actions are explicit and bounded to
    200 rows per request, and approving a group does not add evidence or raise
    confidence.

## Tests and build

```bat
.venv\Scripts\python.exe -m pytest -q
cd frontend
npm run build
```

Last verified: **230 passed, 0 failed**; frontend `tsc -b && vite build` passes.
The review-queue repair also re-ran the bounded reconciliation against the live
database (`scripts/reconcile_review_queue.py`) and verified the running API.

## Exact next tasks (in order)

1. **Import or collect genuine sold evidence** for the current deal categories
   (PS5, ROG Ally, RTX 5070 Ti, MacBook Air M2) so calibration can be assessed on
   real data. Bulk CSV remains optional; eBay Marketplace Insights cannot be used
   without approved access.
2. **Track the new price-memory history.** 1,579 keys already have more than one
   snapshot; use them to surface genuine trends and stale-market warnings without
   treating an old price as current.
3. **Extend auction coverage inside the same budget.** The category rotation now
   reaches 10–19 products per pass; measure whether a smaller ending-soon window
   or priority-weighted rotation improves the count of *usable* opportunities
   rather than the raw auction count.
4. **Add fixtures for the 240-benchmark recall gaps:** spec-less laptop titles
   (Dell Inspiron 15, ThinkPad R61, HP Stream 14), legacy DDR2/SO-DIMM module
   part numbers, and the newly supported Pentium/Celeron/Athlon mini-PCs.
5. **Consider a PostgreSQL adapter** only if multi-user hosting becomes a
   requirement; keep SQLite for the local product.

## Git / handoff

Remote: <https://github.com/CharlieVibin/flipintel> (public, default branch
`main`). The local `main` branch is the working FlipIntel implementation and was
pushed as the repository's first commit on 2026-10-23 (`22d2ea2`). The Phase 5
work was pushed as `3d9e41b` (used-market evidence and the 240-listing
benchmark), `bca0fa1` (form-factor rejection, mini-PC identity and evidence-gated
auction alerts) and `5c3b51d` (sealed resale capped at CeX retail). The variant
integrity repair and its database revaluation were pushed as `123f60e`. The
pre-scan category, preset and budget controls were pushed as `c7736b0`; the
auction configuration enforcement followed as `786f87a`, with Discord auction
alert gating as `4dfb1fa`. The Phase 6 identity/startup work is `9114f6e` and
the review-queue eligibility repair is `fc8ec4a`. The eBay coverage, untested
RAM and auction-review repair is `e9baa1f`. `main` tracks
`origin/main`; local and remote trees match after each push.

Earlier notes referenced `bboyrican20/flipintel`; that was the wrong remote and
is no longer configured. No force-push is needed or recommended.

Never stage `.env`, `data/`, `.venv/`, `node_modules/`, or `frontend/dist/`.
