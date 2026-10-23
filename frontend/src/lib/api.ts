import type {
  BulkReviewResult,
  CalibrationReport,
  CompInput,
  CompsResponse,
  DealDetail,
  DealFilters,
  DealsResponse,
  Flip,
  FlipPatch,
  FlipSummary,
  FlipsResponse,
  EbayConnectionTest,
  EbayCredentialsStatus,
  EvidenceCoverage,
  EvidenceImportResult,
  EvidenceResponse,
  HealthResponse,
  Overview,
  ProductPricing,
  ReviewGroupsResponse,
  ScanRun,
  Settings,
} from "./types";

const BASE = "/api";

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(options?.headers ?? {}) },
    ...options,
  });
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") message = body.detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(message, response.status);
  }
  return (await response.json()) as T;
}

export function queryString(params: Record<string, unknown>): string {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === "" || value === false) return;
    if (key === "saved_only" && value === false) return;
    search.set(key, String(value));
  });
  const text = search.toString();
  return text ? `?${text}` : "";
}

export const api = {
  health: () => request<HealthResponse>("/health"),
  overview: () => request<Overview>("/overview"),
  deals: (filters: DealFilters = {}) =>
    request<DealsResponse>(`/deals${queryString(filters as Record<string, unknown>)}`),
  deal: (id: number) => request<DealDetail>(`/deals/${id}`),
  save: (id: number) => request<{ saved: boolean }>(`/deals/${id}/save`, { method: "POST" }),
  unsave: (id: number) => request<{ saved: boolean }>(`/deals/${id}/save`, { method: "DELETE" }),
  dismiss: (id: number, reason?: string) =>
    request<{ dismissed: boolean }>(`/deals/${id}/dismiss${reason ? `?reason=${encodeURIComponent(reason)}` : ""}`, {
      method: "POST",
    }),
  restore: (id: number) => request<{ dismissed: boolean }>(`/deals/${id}/restore`, { method: "POST" }),
  scans: () => request<{ scans: ScanRun[] }>("/scans"),
  comps: (productKey: string, kind?: string | null) =>
    request<CompsResponse>(
      `/comps${queryString({ product_key: productKey, kind: kind ?? undefined, limit: 60 })}`,
    ),
  addComp: (payload: CompInput) =>
    request<{ comp: unknown; refreshed: boolean }>("/comps", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  deleteComp: (id: number) => request<{ deleted: boolean }>(`/comps/${id}`, { method: "DELETE" }),
  evidence: (filters: Record<string, unknown> = {}) =>
    request<EvidenceResponse>(`/evidence${queryString(filters)}`),
  evidenceCoverage: () => request<EvidenceCoverage>("/evidence/coverage"),
  importEvidence: (payload: { filename: string; content: string; commit: boolean }) =>
    request<EvidenceImportResult>("/evidence/import", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateEvidence: (id: number, payload: Record<string, unknown>) =>
    request<{ comp: unknown }>(`/evidence/${id}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  deleteEvidence: (id: number) => request<{ deleted: boolean }>(`/evidence/${id}`, { method: "DELETE" }),
  calibration: () => request<CalibrationReport>("/calibration"),
  review: (id: number, payload: { decision: string; note?: string; product_key?: string; product_name?: string; kind?: string }) =>
    request<{ decision: string; confidence_boosted: boolean }>(`/deals/${id}/review`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  undoReview: (id: number) => request<{ decision: null }>(`/deals/${id}/review`, { method: "DELETE" }),
  reviewGroups: () => request<ReviewGroupsResponse>("/reviews/groups?kind=phone&flag=variant_unclear"),
  bulkReview: (listingIds: number[], decision: "approved" | "rejected", note?: string) =>
    request<BulkReviewResult>("/reviews/bulk", {
      method: "POST",
      body: JSON.stringify({ listing_ids: listingIds, decision, note }),
    }),
  evidenceTemplateUrl: () => `${BASE}/evidence/template`,
  evidenceExportUrl: (filters: Record<string, unknown> = {}) =>
    `${BASE}/evidence/export${queryString(filters)}`,
  settings: () => request<Settings>("/settings"),
  updateSettings: (payload: Record<string, unknown>) =>
    request<Settings>("/settings", { method: "PUT", body: JSON.stringify(payload) }),
  saveEbayCredentials: (payload: { client_id: string; client_secret: string; environment: string }) =>
    request<{ credentials: EbayCredentialsStatus; settings: Settings }>("/settings/ebay", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  testEbayConnection: () => request<EbayConnectionTest>("/settings/ebay/test", { method: "POST" }),
  removeEbayCredentials: () =>
    request<{ credentials: EbayCredentialsStatus; settings: Settings }>("/settings/ebay", { method: "DELETE" }),
  scan: (marketplace?: string) =>
    request<{ accepted: string[]; message: string }>("/scan", {
      method: "POST",
      body: JSON.stringify({ marketplace: marketplace ?? null }),
    }),
  flips: (status = "all") => request<FlipsResponse>(`/flips${queryString({ status })}`),
  startFlip: (payload: Record<string, unknown>) =>
    request<{ flip: Flip; summary: FlipSummary }>("/flips", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateFlip: (id: number, payload: FlipPatch) =>
    request<{ flip: Flip; summary: FlipSummary }>(`/flips/${id}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  flipPrice: (id: number, price: number) =>
    request<{ flip: Flip }>(`/flips/${id}/price`, {
      method: "POST",
      body: JSON.stringify({ price }),
    }),
  productPricing: (productKey: string) =>
    request<ProductPricing>(`/products/${encodeURIComponent(productKey)}`),
};
