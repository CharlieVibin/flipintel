import type {
  DealDetail,
  DealFilters,
  DealsResponse,
  HealthResponse,
  Overview,
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
  settings: () => request<Settings>("/settings"),
  updateSettings: (payload: Record<string, unknown>) =>
    request<Settings>("/settings", { method: "PUT", body: JSON.stringify(payload) }),
  scan: (marketplace?: string) =>
    request<{ accepted: string[]; message: string }>("/scan", {
      method: "POST",
      body: JSON.stringify({ marketplace: marketplace ?? null }),
    }),
};
