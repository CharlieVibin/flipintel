export type Marketplace = "vinted" | "gumtree" | "ebay" | "facebook";

export interface Deal {
  id: number;
  marketplace: Marketplace;
  marketplace_label: string;
  external_id: string;
  url: string;
  title: string;
  image_url: string | null;
  price: number;
  shipping: number;
  condition: string;
  condition_raw: string;
  location: string;
  seller: string;
  kind: string | null;
  category: string | null;
  product_key: string | null;
  product_name: string | null;
  identification_confidence: number;
  is_faulty: boolean;
  is_wanted: boolean;
  is_auction: boolean;
  auction_end_at?: string | null;
  current_bid?: number | null;
  bid_count?: number | null;
  min_next_bid?: number | null;
  is_bundle: boolean;
  duplicate_of: number | null;
  duplicate_score: number | null;
  posted_at: string | null;
  first_seen: string;
  last_seen: string;
  price_dropped_at: string | null;
  previous_price: number | null;
  age_hours: number;
  expected_resale: number | null;
  resale_low: number | null;
  resale_high: number | null;
  confidence: number;
  evidence_level?: "insufficient" | "limited" | "moderate" | "strong" | null;
  sample_size: number;
  valuation_method: string | null;
  valuation_refreshed_at?: string | null;
  resale_strategy?: "quick" | "balanced" | "max" | null;
  resale_quick?: number | null;
  resale_balanced?: number | null;
  resale_max?: number | null;
  max_bid?: number | null;
  estimated_days?: number | null;
  purchase_total: number;
  net_profit: number | null;
  net_profit_conservative?: number | null;
  net_profit_optimistic?: number | null;
  roi: number | null;
  score: number;
  is_deal: boolean;
  needs_review: boolean;
  review_reason: string | null;
  risk_score: number;
  risk_level: "low" | "medium" | "high";
  risk_reasons: string[];
  review_decision?: "approved" | "rejected" | "corrected" | null;
  review_decided_at?: string | null;
  reasons: string[];
  computed_at: string | null;
  saved: boolean;
  dismissed: boolean;
  discount_pct: number | null;
}

export interface Comp {
  title: string;
  price: number;
  adjusted_price: number;
  marketplace: string;
  condition: string;
  is_sold: boolean;
  evidence?: "manual_sold" | "imported_sold" | "sold" | "asking" | "cex";
  source?: string;
  postage?: number;
  notes?: string | null;
  url: string | null;
  observed_at: string | null;
}

export interface CompItem {
  id: number;
  product_key: string;
  marketplace: string;
  source: string;
  source_id: string;
  title: string;
  price: number;
  kind: string | null;
  condition_norm: string;
  is_sold: boolean;
  postage: number;
  notes: string | null;
  verified: boolean;
  evidence_confidence: number;
  evidence: "manual_sold" | "imported_sold" | "sold" | "asking" | "cex";
  evidence_type?: string;
  url: string | null;
  observed_at: string;
  created_at: string | null;
}

export interface CompsResponse {
  product_key: string;
  comps: CompItem[];
  summary: { sold_count: number; asking_count: number; manual_count: number; sold_median: number | null };
}

export interface CompInput {
  product_key: string;
  product_name?: string;
  title: string;
  price: number;
  condition: string;
  postage?: number;
  kind?: string | null;
  url?: string;
  source_id?: string;
  observed_at?: string;
  notes?: string;
  verified?: boolean;
}

export interface Breakdown {
  purchase?: {
    price: number;
    shipping: number;
    buyer_fee: number;
    total: number;
  };
  resale?: {
    channel: string;
    expected: number | null;
    low: number | null;
    high: number | null;
    range_supported?: boolean;
    range_basis?: string;
    conservative?: number | null;
    central?: number | null;
    optimistic?: number | null;
    fees: number;
    shipping: number;
    packaging: number;
    collection: number;
    other?: number;
    strategy?: "quick" | "balanced" | "max";
    initial_ask?: number | null;
    strategies?: Record<string, { expected: number; ask: number }>;
    holding_days?: number | null;
    max_bid?: number | null;
  };
  comps?: Comp[];
  sample_size?: number;
  sold_count?: number;
  asking_count?: number;
  dispersion?: number | null;
  valuation_notes?: string[];
  assumptions?: Record<string, number | string>;
  risk?: { score: number; level: string; reasons: string[]; signals: string[]; price_percentile: number | null; review_recommended: boolean };
  evidence?: {
    sold_evidence: boolean;
    sold_count: number;
    asking_count: number;
    method: string;
    level?: "insufficient" | "limited" | "moderate" | "strong";
    sources?: Record<string, number>;
    confidence: number;
    confidence_low?: number | null;
    confidence_high?: number | null;
    trend_pct?: number | null;
    trend_note?: string;
  };
}

export interface DealDetail extends Deal {
  description: string;
  flags: string[];
  attrs: Record<string, unknown>;
  breakdown: Breakdown;
  review_note?: string | null;
  price_history: { price: number; observed_at: string }[];
}

export interface DealsResponse {
  deals: Deal[];
  total: number;
  page: number;
  limit: number;
  has_more: boolean;
  generated_at: string;
  thresholds: Thresholds;
}

export interface ReviewGroupExample {
  id: number;
  title: string;
  price: number;
  marketplace: Marketplace;
  url: string;
  image_url: string | null;
  first_seen: string;
}

export interface ReviewGroup {
  product_key: string;
  product_name: string;
  count: number;
  price_low: number;
  price_high: number;
  oldest_first_seen: string | null;
  newest_first_seen: string | null;
  marketplaces: Marketplace[];
  listing_ids: number[];
  listings_truncated: boolean;
  examples: ReviewGroupExample[];
}

export interface ReviewGroupsResponse {
  kind: string;
  flag: "variant_unclear";
  label: string;
  description: string;
  groups: ReviewGroup[];
  total_groups: number;
  total_listings: number;
  bulk_limit: number;
  generated_at: string;
}

export interface BulkReviewResult {
  decision: "approved" | "rejected";
  updated: number;
  skipped: number;
  listing_ids: number[];
  remaining: number;
}

export interface Thresholds {
  min_profit: number;
  min_roi: number;
  min_confidence: number;
}

export interface SourceStatus {
  marketplace: Marketplace;
  label: string;
  enabled: boolean;
  available: boolean;
  availability_reason: string | null;
  state: string;
  running: boolean;
  last_attempt_at: string | null;
  last_success_at: string | null;
  last_error: string | null;
  consecutive_failures: number;
  next_scan_at: string | null;
  listings_seen: number;
  last_duration_ms: number | null;
  backoff_seconds?: number;
  interval_seconds: number;
  api_usage?: ApiUsage;
}

export interface ApiUsage {
  marketplace: string;
  today: {
    requests: number;
    errors: number;
    last_status: number | null;
    last_request_at: string | null;
    last_success_at?: string | null;
  };
  last_request_at?: string | null;
  last_success_at?: string | null;
  window_days: number;
  window_requests: number;
  window_errors: number;
  quota: { state: string; remaining: number | null; limit: number | null; reset_at: string | null; note: string };
  history: {
    day: string;
    requests: number;
    errors: number;
    last_status: number | null;
    last_success_at?: string | null;
  }[];
}

export interface CoverageCategory {
  key: string;
  label: string;
  active: number;
  new_24h: number;
  valued: number;
  deals: number;
  confidence: number;
  last_seen: string | null;
  coverage: "empty" | "thin" | "covered";
}

export interface Coverage {
  categories: CoverageCategory[];
  active_total: number;
  covered_categories: number;
  thin_categories: number;
  empty_categories: number;
  query_categories: number;
  queries_per_cycle: number;
  generated_at: string;
}

export interface HealthResponse {
  status: string;
  time: string;
  version: string;
  scanner: {
    scanner_running: boolean;
    sources: SourceStatus[];
    cex: {
      enabled: boolean;
      status: string;
      error: string | null;
      last_success_at?: string | null;
      reuse_hours?: number;
      stats?: { observations: number; products: number };
    };
    pricing?: {
      snapshots: number;
      products: number;
      latest_snapshot_at: string | null;
      running_research: number;
      reuse_days: number;
    };
    auctions?: {
      alerts_enabled: boolean;
      window_minutes: number;
      scan_seconds: number;
      urgent_seconds: number;
      shortlisted: Array<{
        id: number;
        title: string;
        price: number;
        current_bid: number | null;
        bid_count: number | null;
        auction_end_at: string | null;
        expected_resale: number | null;
        net_profit: number | null;
        max_bid: number | null;
        evidence_level: string | null;
      }>;
    };
    coverage?: Coverage;
    discovery?: Discovery;
    last_refresh_at: string | null;
    last_refresh: { evaluated: number; deals: number; review?: number; observed?: number } | null;
  };
  counts: { listings: number; active_listings: number; deals: number; review?: number; duplicates?: number };
  last_successful_scan: string | null;
  review_decisions?: Record<string, number>;
  ebay_configured: boolean;
  ebay_setup?: { configured: boolean; environment: string; marketplace: string; credential_source: string | null };
  settings_thresholds: Thresholds;
}

export interface Overview {
  deals: number;
  potential_profit: number;
  avg_roi: number;
  avg_discount: number;
  best_profit: number;
  avg_confidence: number;
  new_deals_24h: number;
  review_count: number;
  sold_evidence: number;
  manual_evidence: number;
  duplicate_listings: number;
  best: {
    id: number;
    title: string;
    price: number;
    marketplace: string;
    image_url: string | null;
    category: string | null;
    expected_resale: number;
    net_profit: number;
    roi: number;
    score: number;
    confidence: number;
  } | null;
  categories: { category: string; count: number }[];
  marketplaces: { marketplace: string; count: number }[];
  sources: { marketplace: string; state: string; last_success_at: string | null; last_error: string | null; next_scan_at: string | null; consecutive_failures: number }[];
  active_listings: number;
  tracked_models: number;
  scans_24h: number;
  last_scan: string | null;
  thresholds: Thresholds;
}

export interface DiscoveryCategory {
  category_id: string;
  label: string;
  coverage: "full" | "partial";
  state: string;
  mode: "backfill" | "incremental";
  supported: boolean;
  verified: boolean;
  backfill_done: boolean;
  pages_scanned: number;
  last_scan_at: string | null;
  last_success_at: string | null;
  last_error: string | null;
  items: number;
  new: number;
  duplicates: number;
  duplicate_rate: number;
  new_rate: number;
  requests: number;
  interval_seconds: number;
  next_due_at: string | null;
  scan_url: string | null;
}

export interface DiscoveryMarketplace {
  marketplace: Marketplace;
  label: string;
  supported: boolean;
  verified: boolean;
  mode: string;
  enabled: boolean;
  categories: DiscoveryCategory[];
  total_categories: number;
  full_categories: number;
  partial_categories: number;
  scanned_categories: number;
  due_categories: number;
  requests_last_cycle: number;
  new_last_cycle: number;
  coverage_note: string;
}

export interface Discovery {
  marketplaces: DiscoveryMarketplace[];
  generated_at: string;
  discovery_mode: string;
}

export interface ScanRun {
  id: number;
  marketplace: string;
  started_at: string;
  finished_at: string | null;
  status: string;
  queries: string[];
  items_found: number;
  items_new: number;
  price_drops: number;
  duplicates?: number;
  duration_ms: number | null;
  error: string | null;
  detail: {
    errors?: string[];
    query_timings?: { query: string; status: string; items: number; pages: number; ms: number; error?: string }[];
    categories?: {
      category_id: string;
      label: string;
      coverage: string;
      mode: string;
      pages: number;
      items: number;
      new: number;
      known: number;
      requests: number;
      overlap: number | null;
      stop: string | null;
      error: string | null;
      duration_ms?: number;
      next_interval?: number;
      backfill_done?: boolean;
    }[];
    keyword_supplement?: { queries: string[]; status: string; items: number; new: number; error: string | null } | null;
    stage_ms?: Record<string, number>;
    collector_ms?: number;
    core_ms?: number;
    total_ms?: number;
    listings_processed?: number;
  };
}

export interface Settings {
  deal: { min_profit: number; min_roi: number; min_confidence: number };
  fees: {
    ebay_fvf: number;
    resale_channel: "ebay" | "vinted" | "facebook";
    resale_shipping: number;
    packaging_cost: number;
    other_costs: number;
    vinted_shipping: number;
    asking_to_sold_factor: number;
    resale_strategy: "quick" | "balanced" | "max";
    min_roi_high_value: number;
    high_value_threshold: number;
  };
  pricing: { history_reuse_days: number; cex_refresh_limit: number };
  auctions: {
    scan_seconds: number;
    urgent_seconds: number;
    window_minutes: number;
    alerts_enabled: boolean;
  };
  scanning: {
    scan_interval_vinted: number;
    scan_interval_gumtree: number;
    scan_interval_ebay: number;
    queries_per_cycle: number;
    enabled_vinted: boolean;
    enabled_gumtree: boolean;
    enabled_ebay: boolean;
  };
  notifications: { enabled: boolean; webhook_configured: boolean; cooldown_minutes?: number };
  environment: {
    ebay_configured: boolean;
    facebook_available: boolean;
    cex_enabled: boolean;
    ebay: EbayCredentialsStatus;
  };
}

export interface EbayCredentialsStatus {
  configured: boolean;
  environment: string;
  client_id_hint: string | null;
  credential_source: string | null;
  storage: string;
  can_clear: boolean;
}

export interface EbayConnectionTest {
  ok: boolean;
  authenticated: boolean;
  error?: string;
  kind?: string;
  http_status?: number | null;
  latency_ms?: number;
  environment?: string;
  marketplace?: string;
  currency?: string;
  sample_count?: number;
  pages?: number;
  note?: string;
  credentials: EbayCredentialsStatus;
  tested_at: string;
}

export interface EvidenceItem {
  id: number;
  product_key: string;
  product_name: string | null;
  title: string;
  marketplace: string;
  source: string;
  source_id: string;
  price: number;
  postage: number;
  kind: string | null;
  condition_norm: string;
  condition_raw: string | null;
  is_sold: boolean;
  notes: string | null;
  verified: boolean;
  evidence_confidence: number;
  evidence_type: string;
  url: string | null;
  observed_at: string;
  created_at: string | null;
  updated_at?: string | null;
  currency: string;
  variants: string | null;
  import_id: number | null;
}

export interface EvidenceResponse {
  evidence: EvidenceItem[];
  total: number;
  limit: number;
  offset: number;
  page: number;
}

export interface EvidenceProduct {
  product_key: string;
  product_name: string | null;
  kind: string | null;
  sold_count: number;
  user_count: number;
  independent_count: number;
  imported_count: number;
  min_price: number;
  max_price: number;
  last_sale_at: string;
  evidence_level: "thin" | "moderate" | "strong";
  usable_for_calibration: boolean;
}

export interface EvidenceCoverage {
  products: EvidenceProduct[];
  imports: {
    id: number;
    filename: string;
    created_at: string;
    total_rows: number;
    imported: number;
    updated: number;
    skipped: number;
    rejected: number;
  }[];
  totals: {
    total: number;
    user_supplied: number;
    independent: number;
    products_with_evidence: number;
    strong_products: number;
    calibration_ready_products: number;
  };
}

export interface CalibrationCategory {
  category: string;
  label: string;
  status: string;
  active: boolean;
  products: number;
  training_products: number;
  holdout_products: number;
  sold_observations: number;
  asking_observations: number;
  independent_sold: number;
  user_sold: number;
  raw_factor: number | null;
  factor: number | null;
  confidence: number;
  holdout_mae_pct: number | null;
  holdout_bias_pct: number | null;
  reason: string;
  evidence_basis: string;
}

export interface CalibrationReport {
  status: string;
  active: boolean;
  fallback_factor: number;
  global_factor: number | null;
  matched_products: number;
  sold_observations: number;
  asking_observations: number;
  independent_sold: number;
  user_sold: number;
  categories: CalibrationCategory[];
  category_factors: Record<string, number>;
  reason: string;
  calculated_at: string | null;
  thresholds: Record<string, unknown>;
}

export interface EvidenceImportRow {
  row: number;
  action: "imported" | "updated" | "skipped";
  reason: string;
  warnings: string[];
  product_key: string;
  product_name: string | null;
  title: string;
  marketplace: string;
  price: number;
  postage: number;
  condition: string;
  observed_at: string;
  currency: string;
  kind: string;
  source_id: string;
}

export interface EvidenceImportResult {
  filename: string;
  commit: boolean;
  import_id: number | null;
  total_rows: number;
  counts: { imported: number; updated: number; skipped: number; rejected: number };
  rows: EvidenceImportRow[];
  rejected: { row: number; reason: string }[];
  warnings: string[];
  truncated: boolean;
  limits: { max_bytes: number; max_rows: number };
}

export type SortKey = "score" | "newest" | "profit" | "roi" | "confidence" | "price";

export interface DealFilters {
  category?: string;
  marketplace?: string;
  condition?: string;
  min_profit?: number;
  max_age_hours?: number;
  sort?: SortKey;
  q?: string;
  saved_only?: boolean;
  archived?: boolean;
  review?: boolean;
  review_status?: "pending" | "approved" | "rejected" | "corrected" | "all";
  product_key?: string;
  review_flag?: "variant_unclear";
  min_confidence?: number;
  page?: number;
  limit?: number;
}

export interface Flip {
  id: number;
  listing_id: number | null;
  product_key: string | null;
  title: string;
  kind: string | null;
  marketplace: string | null;
  condition_norm: string | null;
  purchase_price: number;
  purchase_shipping: number;
  preparation_cost: number;
  other_costs: number;
  ask_price: number | null;
  final_price: number | null;
  fees: number;
  postage: number;
  channel: string | null;
  status: "held" | "listed" | "sold" | "unsold";
  listed_at: string | null;
  sold_at: string | null;
  predicted_resale: number | null;
  predicted_profit: number | null;
  predicted_confidence: number | null;
  predicted_evidence: string | null;
  prediction_reasons: string[];
  feedback: number | null;
  feedback_note: string | null;
  total_invested: number;
  net_profit: number | null;
  roi: number | null;
  prediction_error: number | null;
  days_to_sell: number | null;
  price_changes: { price: number; changed_at: string }[];
  created_at: string;
  updated_at: string;
}

export interface FlipSummary {
  counts: { held: number; listed: number; sold: number; unsold: number };
  realised_profit: number;
  predicted_profit: number;
  prediction_mae: number | null;
  average_days_to_sell: number | null;
  capital_tied_up: number;
  learned_factors: Record<string, number>;
  feedback: { up: number; down: number };
}

export interface FlipsResponse {
  flips: Flip[];
  summary: FlipSummary;
  generated_at: string;
}

export interface FlipPatch {
  purchase_price?: number;
  purchase_shipping?: number;
  preparation_cost?: number;
  other_costs?: number;
  ask_price?: number;
  final_price?: number;
  fees?: number;
  postage?: number;
  channel?: string;
  status?: Flip["status"];
  condition_norm?: string;
  sold_at?: string;
  feedback?: number;
  feedback_note?: string;
}

export interface ProductPricing {
  product_key: string;
  pricing: {
    snapshot: {
      resale_good: number;
      low: number | null;
      high: number | null;
      confidence: number;
      evidence_level: string;
      sample_size: number;
      sold_count: number;
      method: string;
      refreshed_at: string;
      marketplaces: Record<string, number>;
      strategies?: Record<string, { expected: number; ask: number }>;
    } | null;
    stale: boolean;
    reuse_days: number;
    history: { resale_good: number; confidence: number; refreshed_at: string }[];
  };
  cex: {
    title: string | null;
    sell_price: number | null;
    cash_price: number | null;
    voucher_price: number | null;
    grade: string | null;
    status: string;
    fetched_at: string;
    url: string | null;
    sample_count: number;
  } | null;
  cex_history: {
    matched_name: string;
    grade: string;
    sell_price: number;
    cash_price: number;
    voucher_price: number;
    condition: string;
    observed_at: string;
  }[];
}
