import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import type { Deal, DealFilters, Thresholds } from "../lib/types";

interface State {
  deals: Deal[];
  total: number;
  thresholds: Thresholds | null;
  loading: boolean;
  loadingMore: boolean;
  error: string | null;
  hasMore: boolean;
}

export function useDeals(filters: DealFilters, refreshTick: number) {
  const [state, setState] = useState<State>({
    deals: [],
    total: 0,
    thresholds: null,
    loading: true,
    loadingMore: false,
    error: null,
    hasMore: false,
  });
  const key = JSON.stringify(filters);
  const pageRef = useRef(1);
  const abortRef = useRef<AbortController | null>(null);

  const load = useCallback(
    async (page: number, append: boolean) => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      setState((prev) => ({
        ...prev,
        loading: !append,
        loadingMore: append,
        error: null,
        ...(append ? {} : { deals: [] }),
      }));
      try {
        const response = await api.deals({ ...filters, page, limit: filters.limit ?? 24 });
        if (controller.signal.aborted) return;
        pageRef.current = page;
        setState((prev) => ({
          deals: append ? [...prev.deals, ...response.deals] : response.deals,
          total: response.total,
          thresholds: response.thresholds,
          loading: false,
          loadingMore: false,
          error: null,
          hasMore: response.has_more,
        }));
      } catch (error) {
        if (controller.signal.aborted) return;
        setState((prev) => ({
          ...prev,
          loading: false,
          loadingMore: false,
          error: error instanceof Error ? error.message : "Could not load deals",
        }));
      }
    },
    [key, refreshTick],
  );

  useEffect(() => {
    void load(1, false);
    return () => abortRef.current?.abort();
  }, [load, refreshTick]);

  const loadMore = useCallback(() => {
    if (state.loading || state.loadingMore || !state.hasMore) return;
    void load(pageRef.current + 1, true);
  }, [load, state.hasMore, state.loading, state.loadingMore]);

  const reload = useCallback(() => {
    void load(1, false);
  }, [load]);

  const updateDeal = useCallback((id: number, patch: Partial<Deal>) => {
    setState((prev) => ({ ...prev, deals: prev.deals.map((deal) => (deal.id === id ? { ...deal, ...patch } : deal)) }));
  }, []);

  const removeDeal = useCallback((id: number) => {
    setState((prev) => ({ ...prev, deals: prev.deals.filter((deal) => deal.id !== id), total: Math.max(0, prev.total - 1) }));
  }, []);

  return { ...state, loadMore, reload, updateDeal, removeDeal };
}
