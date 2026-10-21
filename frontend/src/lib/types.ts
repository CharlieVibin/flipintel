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
  sample_size: number;
  valuation_method: string | null;
  purchase_total: number;
  net_profit: number | null;
  roi: number | null;
  score: number;
  is_deal: boolean;
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
  url: string | null;
  observed_at: string | null;
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
    fees: number;
    shipping: number;
    packaging: number;
    collection: number;
  };
  comps?: Comp[];
  sample_size?: number;
  sold_count?: number;
  asking_count?: number;
  dispersion?: number | null;
  valuation_notes?: string[];
  assumptions?: Record<string, number>;
}

export interface DealDetail extends Deal {
  description: string;
  flags: string[];
  attrs: Record<string, unknown>;
  breakdown: Breakdown;
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
  interval_seconds: number;
}

export interface HealthResponse {
  status: string;
  time: string;
  version: string;
  scanner: {
    scanner_running: boolean;
    sources: SourceStatus[];
    cex: { enabled: boolean; status: string; error: string | null };
    last_refresh_at: string | null;
    last_refresh: { evaluated: number; deals: number } | null;
  };
  counts: { listings: number; active_listings: number; deals: number };
  last_successful_scan: string | null;
  ebay_configured: boolean;
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
  duration_ms: number | null;
  error: string | null;
  detail: { errors?: string[] };
}

export interface Settings {
  deal: { min_profit: number; min_roi: number; min_confidence: number };
  fees: {
    ebay_fvf: number;
    resale_shipping: number;
    packaging_cost: number;
    vinted_shipping: number;
    asking_to_sold_factor: number;
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
  notifications: { enabled: boolean; webhook_configured: boolean };
  environment: { ebay_configured: boolean; facebook_available: boolean; cex_enabled: boolean };
}

export type SortKey = "score" | "newest" | "profit" | "roi" | "confidence" | "price";

export interface DealFilters {
  category?: string;
  marketplace?: string;
  min_profit?: number;
  max_age_hours?: number;
  sort?: SortKey;
  q?: string;
  saved_only?: boolean;
  archived?: boolean;
  page?: number;
  limit?: number;
}
