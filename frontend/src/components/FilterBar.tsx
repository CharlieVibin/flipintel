import { Search, SlidersHorizontal, X } from "lucide-react";
import type { DealFilters, SortKey } from "../lib/types";
import { CATEGORY_LABELS, MARKETPLACE_META } from "../lib/format";
import { cn } from "./ui";

interface Props {
  filters: DealFilters;
  onChange: (patch: Partial<DealFilters>) => void;
  onReset: () => void;
  query: string;
  onQueryChange: (value: string) => void;
  total: number;
  loading: boolean;
  categories: { category: string; count: number }[];
}

const MARKETPLACES = ["vinted", "gumtree", "ebay"];

export function FilterBar({ filters, onChange, onReset, query, onQueryChange, total, loading, categories }: Props) {
  const active =
    Boolean(filters.category || filters.marketplace || filters.min_profit || filters.max_age_hours) ||
    query.trim().length > 0;
  return (
    <div className="panel sticky top-3 z-20 p-3 backdrop-blur-xl">
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative min-w-[180px] flex-1">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-mist-500" />
            <input
              className="field !py-2 !pl-9 !pr-8"
              placeholder="Search models, titles, keywords…"
              value={query}
              onChange={(event) => onQueryChange(event.target.value)}
              aria-label="Search deals"
            />
            {query && (
              <button
                onClick={() => onQueryChange("")}
                className="absolute right-2.5 top-1/2 -translate-y-1/2 text-mist-500 hover:text-mist-200"
                aria-label="Clear search"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            )}
          </div>

          <select
            className="field !w-auto !py-2"
            value={filters.sort ?? "score"}
            onChange={(event) => onChange({ sort: event.target.value as SortKey })}
            aria-label="Sort deals"
          >
            <option value="score">Best opportunity</option>
            <option value="newest">Newest listed</option>
            <option value="profit">Highest profit</option>
            <option value="roi">Highest ROI</option>
            <option value="confidence">Most confident</option>
            <option value="price">Lowest price</option>
          </select>

          <select
            className="field !w-auto !py-2"
            value={filters.min_profit ?? 0}
            onChange={(event) => onChange({ min_profit: Number(event.target.value) })}
            aria-label="Minimum profit"
          >
            <option value={0}>Any profit</option>
            <option value={25}>£25+ profit</option>
            <option value={50}>£50+ profit</option>
            <option value={100}>£100+ profit</option>
            <option value={200}>£200+ profit</option>
          </select>

          <select
            className="field !w-auto !py-2"
            value={filters.max_age_hours ?? 0}
            onChange={(event) => onChange({ max_age_hours: Number(event.target.value) })}
            aria-label="Listing age"
          >
            <option value={0}>Any age</option>
            <option value={6}>Last 6 hours</option>
            <option value={24}>Last 24 hours</option>
            <option value={72}>Last 3 days</option>
            <option value={168}>Last 7 days</option>
          </select>

          {active && (
            <button onClick={onReset} className="btn-ghost !px-2.5 !py-2 text-xs text-mist-400">
              <X className="h-3.5 w-3.5" /> Reset
            </button>
          )}
        </div>

        <div className="flex items-center gap-2 overflow-x-auto no-scrollbar">
          <span className="hidden shrink-0 items-center gap-1.5 pr-1 text-[10px] font-medium uppercase tracking-wider text-mist-500 sm:flex">
            <SlidersHorizontal className="h-3 w-3" /> Market
          </span>
          <button
            className={cn("filter-chip", !filters.marketplace && "filter-chip-active")}
            onClick={() => onChange({ marketplace: undefined })}
          >
            All sources
          </button>
          {MARKETPLACES.map((key) => {
            const meta = MARKETPLACE_META[key];
            return (
              <button
                key={key}
                className={cn("filter-chip", filters.marketplace === key && "filter-chip-active")}
                onClick={() => onChange({ marketplace: filters.marketplace === key ? undefined : key })}
              >
                <span className={cn("h-1.5 w-1.5 rounded-full", meta?.dot)} />
                {meta?.label ?? key}
              </button>
            );
          })}
          <span className="mx-1 h-4 w-px shrink-0 bg-ink-600" />
          <button
            className={cn("filter-chip", !filters.category && "filter-chip-active")}
            onClick={() => onChange({ category: undefined })}
          >
            All hardware
          </button>
          {categories.slice(0, 8).map((category) => (
            <button
              key={category.category}
              className={cn("filter-chip", filters.category === category.category && "filter-chip-active")}
              onClick={() =>
                onChange({ category: filters.category === category.category ? undefined : category.category })
              }
            >
              {CATEGORY_LABELS[category.category] ?? category.category}
              <span className="numeric text-[9px] opacity-60">{category.count}</span>
            </button>
          ))}
        </div>
      </div>
      <div className="mt-2 hidden items-center justify-between border-t border-ink-700/60 pt-2 text-[10px] text-mist-500 sm:flex">
        <span>
          {loading ? "Refreshing opportunities…" : `${total.toLocaleString()} qualifying deal${total === 1 ? "" : "s"}`}
        </span>
        <span>Ranked by profit, ROI, confidence and freshness</span>
      </div>
    </div>
  );
}
