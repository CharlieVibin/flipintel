# FlipIntel — Project Status

**Last updated:** 2026-10-23
**Workspace:** `C:\Users\Charr\Documents\Freebuff web`
**Runtime:** Python 3.10, FastAPI, SQLite, React 18 + Vite + Tailwind
**Live dashboard:** http://127.0.0.1:8765 (while the local server is running)

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
- [x] Category-rotating discovery across GPUs, CPUs, RAM, SSDs/HDDs, complete
      PCs, laptops, consoles/handhelds/retro, motherboards, monitors, phones,
      tablets and networking. Vinted paginates up to 96 cards/query; Gumtree and
      eBay return their structured first page per query.
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
| Active listings in local DB | 20,535 |
| Qualifying deals | 3 |
| Manual review queue (pending) | 1,469 |
| Probable cross-market duplicates | 29 |
| Tracked product keys | 1,529 |
| Completed-sale evidence rows | 0 |
| Sources healthy | Vinted, Gumtree |
| eBay | `not_configured` — free developer keys not set |
| CeX | HTTP 403 from this network; surfaced honestly |
| Facebook | Unavailable; requires authenticated session |
| Tests | 55 passed, 0 failed |
| Frontend build | Passing |

The three current deals are all PS5 listings supported by live asking prices.
Every one is capped at 52% confidence and labelled "Asking only". The two £200
listings are around 30% below the modelled resale; the £180 console-only listing
has received the 12% contents discount. The remaining deep discounts are in the
review queue rather than being presented as top-ranked profit.

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
| Vinted UK | Working | Public catalogue HTML, up to 96 cards per query; title/brand/condition from cards. |
| Gumtree UK | Working | JSON-LD ItemList is authoritative; descriptions, conditions, images and canonical URLs. Promoted cards excluded. |
| eBay UK | Code complete, needs keys | Official Browse API, UK marketplace, GBP, fixed-price, newly listed, quota/backoff. |
| Facebook Marketplace | Unsupported | Login required; automated collection is not attempted. |
| CeX UK | Blocked here | Optional anchor only. Cloudflare 403 in this workspace. |

## Measured scan performance (latest successful cycle)

| Source | Items | Total | Collect | Ingest | Valuation | Duplicates |
| --- | --- | --- | --- | --- | --- | --- |
| Vinted | 1,094 | 39.4 s | 19.0 s | 0.9 s | 17.4 s | 0.005 s |
| Gumtree | 291 | 52.6 s | 21.9 s | 1.2 s | 28.8 s | 0.005 s |

Collection is paced at roughly 1.4–1.8 seconds per query across 12 queries; that
is deliberate rate-limit protection, not an application bottleneck. Valuation is
a full recomputation of ~20k active listings and serialises across the two
sources; a second scan can wait for the first refresh. This is the main remaining
performance cost and is a candidate for incremental refresh.

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
7. **Valuation refresh is a full pass**, so concurrent source scans can make the
   later scan wait 15–30 seconds. It is correct but not yet incremental.
8. **Duplicate detection is conservative** and only flags different-marketplace
   rows with matching product keys, similar titles and prices within 2.5%.
9. **Rule-based identification can still miss unusual titles.** New review cases
   should become fixtures instead of one-off keyword patches.
10. **Single-user SQLite architecture**, not designed for multi-writer hosting.

## Tests and build

```bat
.venv\Scripts\python.exe -m pytest -q
cd frontend
npm run build
```

Last verified: **55 passed, 0 failed**; frontend `tsc -b && vite build` passes.

## Exact next tasks (in order)

1. **Incremental valuation.** Recompute only new/changed listings and the product
   keys whose comparable pools changed, with a periodic full reconciliation. This
   removes the 15–30 second serial refresh from each source scan.
2. **Import or collect genuine sold evidence** for the current deal categories
   (PS5, ROG Ally, RTX 5070 Ti, MacBook Air M2) so calibration can be assessed on
   real data. Bulk CSV remains optional; eBay Marketplace Insights cannot be used
   without approved access.
3. **Exercise eBay Browse API end to end** with real UK developer keys: token →
   search → parser → scan history → deal feed. Verify GBP/UK filters, shipping,
   images, usage counters and duplicate prevention.
4. **Reduce review-queue noise.** Group variant-unclear phones by model so the
   user can see whether a missing storage figure affects many listings, and add
   bulk review actions if the queue stays large.
5. **Add more live-fixture regressions** from future review cases: multi-item
   console bundles, "console only" wording variants, compact Intel CPU names and
   ambiguous MacBook years.
6. **Consider a PostgreSQL adapter** only if multi-user hosting becomes a
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
