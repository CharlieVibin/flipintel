# FlipIntel — Project Status

**Last updated:** 2026-10-23
**Workspace:** `C:\Users\Charr\Documents\Freebuff web`
**Runtime:** Python 3.10, FastAPI, SQLite, React 18 + Vite + Tailwind
**Live dashboard:** http://127.0.0.1:8765 (while the local server is running)

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
| Active listings in local DB | 39,125 |
| Qualifying deals | 1 |
| Manual review queue (pending) | 2,067 |
| Missing-storage phone variants | 968 across 60 models |
| Probable cross-market duplicates | 41 |
| Tracked product keys | 1,664 |
| Category-discovered listings | 17,574 |
| Completed-sale evidence rows | 0 |
| Sources healthy | Vinted, Gumtree |
| eBay | `not_configured` — free developer keys not set |
| CeX | HTTP 403 from this network; surfaced honestly |
| Facebook | Unavailable; requires authenticated session |
| Tests | 72 passed, 0 failed |
| Frontend build | Passing |

The current deal is a £200 PlayStation 5 console supported by live asking
prices, around 29% below the modelled resale. It is capped at 52% confidence
and labelled "Asking only". The remaining deep discounts are in the review queue
rather than being presented as top-ranked profit.

## Valuation evidence status

| Evidence | Works? | Notes |
| --- | --- | --- |
| Live asking prices (Vinted/Gumtree/eBay) | Yes | Automatically collected; lower-tail trimmed, condition-normalised and clearly labelled asking-only. |
| Bulk CSV completed sales | Yes | Evidence lab → choose CSV → preview → commit. Validated, idempotent, provenance retained. |
| Manual verified sold prices | Yes | Deal drawer → Verified sold evidence, or `POST /api/comps`; valuation refreshes immediately. |
| Category calibration | Ready, inactive | No matched sold evidence yet. API reports `insufficient` and keeps the 85% fallback. |
| eBay Browse API active listings | Code complete, needs keys | Active asks only; Browse API does not supply completed sales. |
| eBay Marketplace Insights (sold items) | Not available | eBay documents the API as restricted and not open to new users; no bypass is attempted. |
| eBay UK completed-sales pages | Not implemented | eBay directs automated access to its API and protects sold-search HTML. |
| CeX retail anchor | Usually blocked here | HTTP 403 in this network; optional fallback only. |

No automated, legitimate, free sold-price source is available to this workspace.
The honest automatic path is robust asking-price evidence plus confidence caps and
review. Manual CSV remains optional and is not required for scanning or valuation.

## Marketplace status

| Source | Collection | Notes |
| --- | --- | --- |
| Vinted UK | Working | Category browse verified on all 10 branches; up to 96 cards/page, newest first, marketplace cap at page 10. |
| Gumtree UK | Working | Category browse verified on all 6 branches; 30 cards/page. JSON-LD is authoritative on search pages, per-card anchors on category pages. |
| eBay UK | Code complete, needs keys | Official Browse API category search (`category_ids`, no keyword), 200/page, 4 requests/cycle, quota/backoff. Not exercised live. |
| Facebook Marketplace | Unsupported | Login required; automated collection is not attempted. |
| CeX UK | Blocked here | Optional anchor only. Cloudflare 403 in this workspace. |

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
   at 52% confidence. Import real sales to change that.
2. **Deep discounts intentionally go to review.** A listing below 65% of its
   comparable median, or with missing storage/RAM/chip details, is not called a
   top deal automatically. This is a deliberate precision-over-volume choice; the
   review queue is the path to promote a verified genuine bargain.
3. **Calibration needs volume.** At least 2 sold observations and 4 recent asks per
   matched product, at least 5 products per category and held-out error below 15%.
   Otherwise it stays inactive and explains why.
4. **eBay has not been exercised end-to-end** because no API keys are configured.
   Token, missing-credential, parser, quota and authentication paths are tested.
5. **CeX is unavailable from this network.** The app reports the 403 rather than
   pretending the anchor worked.
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

Last verified: **72 passed, 0 failed**; frontend `tsc -b && vite build` passes.

## Exact next tasks (in order)

1. **Import or collect genuine sold evidence** for the current deal categories
   (PS5, ROG Ally, RTX 5070 Ti, MacBook Air M2) so calibration can be assessed on
   real data. Bulk CSV remains optional; eBay Marketplace Insights cannot be used
   without approved access.
2. **Exercise eBay Browse API end to end** with real UK developer keys: token →
   search → parser → scan history → deal feed. Verify GBP/UK filters, shipping,
   images, usage counters and duplicate prevention.
3. **Extend grouped triage to other recurring review reasons.** If the queue
   remains large after the variant groups are worked, measure the next dominant
   reason (price anomaly, accessory or condition caveat) before adding any new
   bulk path.
4. **Add live-fixture regressions as review cases appear.** This session added
   phone case/protector brands and `256G` shorthand; multi-item console bundles,
   console-only wording, compact Intel CPU names and ambiguous MacBook years are
   already covered by focused fixtures.
5. **Consider a PostgreSQL adapter** only if multi-user hosting becomes a
   requirement; keep SQLite for the local product.

## Git / handoff

Remote: <https://github.com/CharlieVibin/flipintel> (public, default branch
`main`). The local `main` branch is the working FlipIntel implementation and was
pushed as the repository's first commit on 2026-10-23 (`22d2ea2`), so no remote
history was overwritten. `main` tracks `origin/main`; local and remote trees
match.

Earlier notes referenced `bboyrican20/flipintel`; that was the wrong remote and
is no longer configured. No force-push is needed or recommended.

Never stage `.env`, `data/`, `.venv/`, `node_modules/`, or `frontend/dist/`.
