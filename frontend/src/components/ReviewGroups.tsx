import { useEffect, useState } from "react";
import {
  ArrowRight,
  Check,
  ChevronDown,
  ChevronUp,
  CircleAlert,
  Loader2,
  Smartphone,
  X,
} from "lucide-react";
import { api } from "../lib/api";
import type { ReviewGroup, ReviewGroupsResponse } from "../lib/types";
import { marketplaceMeta, money, pct, timeAgo } from "../lib/format";
import { cn } from "./ui";

interface Props {
  refreshTick: number;
  activeKey?: string;
  onSelect: (productKey: string) => void;
  onClear: () => void;
  onReloadDeals: () => void;
  onRefreshSummary: () => void;
  onToast: (message: string, tone?: "success" | "error" | "info") => void;
}

export function ReviewGroups({
  refreshTick,
  activeKey,
  onSelect,
  onClear,
  onReloadDeals,
  onRefreshSummary,
  onToast,
}: Props) {
  const [data, setData] = useState<ReviewGroupsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    api
      .reviewGroups()
      .then((response) => {
        if (!active) return;
        setData(response);
        setError(null);
      })
      .catch((err: unknown) => {
        if (!active) return;
        setError(err instanceof Error ? err.message : "Could not load grouped reviews");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [refreshTick]);

  async function refreshGroups() {
    try {
      const response = await api.reviewGroups();
      setData(response);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not refresh grouped reviews");
    }
  }

  async function decide(group: ReviewGroup, decision: "approved" | "rejected") {
    if (busy) return;
    if (group.listings_truncated || group.listing_ids.length > (data?.bulk_limit ?? 200)) {
      onToast("This group is too large for one bulk action. Open it and work in smaller batches.", "info");
      return;
    }
    const noun = `${group.count} ${group.product_name} listing${group.count === 1 ? "" : "s"}`;
    const confirmation =
      decision === "approved"
        ? `Approve ${noun}?\n\nThis resolves the review gate but does not add evidence or raise confidence. The missing-variant risk note stays visible, and base-model pricing may not fit every storage size.`
        : `Reject ${noun}?\n\nThey will leave the pending queue and stay out of recommendations.`;
    if (!window.confirm(confirmation)) return;

    setBusy(group.product_key);
    try {
      const result = await api.bulkReview(group.listing_ids, decision);
      const verb = decision === "approved" ? "Approved" : "Rejected";
      const skipped = result.skipped ? `, ${result.skipped} already resolved` : "";
      onToast(`${verb} ${result.updated} listing${result.updated === 1 ? "" : "s"}${skipped}`, "success");
      if (activeKey === group.product_key) onClear();
      await refreshGroups();
      onReloadDeals();
      onRefreshSummary();
    } catch (err) {
      onToast(err instanceof Error ? err.message : "Could not save the bulk decision", "error");
    } finally {
      setBusy(null);
    }
  }

  if (loading && !data) {
    return (
      <div className="panel mb-4 flex items-center gap-3 px-4 py-3 text-xs text-mist-500">
        <Loader2 className="h-4 w-4 animate-spin" /> Grouping the review queue by model…
      </div>
    );
  }

  if (error) {
    return (
      <div className="panel mb-4 flex flex-wrap items-center gap-2 border-rose/20 px-4 py-3 text-xs text-mist-300">
        <CircleAlert className="h-4 w-4 text-rose" />
        <span>{error}</span>
        <button className="font-medium text-rose hover:underline" onClick={() => void refreshGroups()}>
          Retry
        </button>
      </div>
    );
  }

  if (!data) return null;
  const active = data.groups.find((group) => group.product_key === activeKey);

  if (!activeKey && data.total_listings === 0) return null;

  if (activeKey) {
    return (
      <section className="panel mb-4 overflow-hidden border-lime-400/20">
        <div className="flex flex-wrap items-start justify-between gap-3 px-4 py-3">
          <div className="flex min-w-0 gap-2.5">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-lime-400/20 bg-lime-400/10 text-lime-300">
              <Smartphone className="h-4 w-4" />
            </span>
            <div className="min-w-0">
              <p className="eyebrow">Variant group</p>
              <p className="truncate text-xs font-semibold text-mist-100">
                {active?.product_name ?? "Selected model"}
              </p>
              <p className="mt-0.5 text-[10px] leading-relaxed text-mist-500">
                {active
                  ? `${active.count} pending without a stated storage, memory or chip variant. Approving resolves the review gate only; confidence is not raised.`
                  : "Showing the pending review rows for the selected product."}
              </p>
            </div>
          </div>
          <button className="btn-ghost !py-1 !text-[10px] text-mist-400" onClick={onClear}>
            All models <ArrowRight className="h-3 w-3" />
          </button>
        </div>
        {active && (
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-ink-700/60 px-4 py-2.5 text-[10px] text-mist-500">
            <span>
              {money(active.price_low)}–{money(active.price_high)}
            </span>
            <span className="flex items-center gap-1.5">
              {active.marketplaces.map((marketplace) => (
                <span key={marketplace} className="chip border-ink-600 bg-ink-800 text-[9px] text-mist-300">
                  {marketplaceMeta(marketplace).label}
                </span>
              ))}
            </span>
            <span>Oldest {timeAgo(active.oldest_first_seen)}</span>
            <span>{pct(active.count / Math.max(1, data.total_listings), 0)} of variant gaps</span>
            <div className="ml-auto flex flex-wrap items-center gap-1.5">
              <button
                className="btn-secondary !py-1.5 !text-[10px]"
                onClick={() => void decide(active, "rejected")}
                disabled={Boolean(busy)}
                title="Reject every pending listing in this model group"
              >
                <X className="h-3 w-3" /> Reject all {active.count}
              </button>
              <button
                className="btn-primary !py-1.5 !text-[10px]"
                onClick={() => void decide(active, "approved")}
                disabled={Boolean(busy)}
                title="Approve every pending listing in this model group"
              >
                {busy === active.product_key ? (
                  <Loader2 className="h-3 w-3 animate-spin" />
                ) : (
                  <Check className="h-3 w-3" />
                )}
                Approve all {active.count}
              </button>
            </div>
          </div>
        )}
        {active?.examples[0] && (
          <p className="border-t border-ink-700/60 px-4 py-2 text-[10px] text-mist-600">
            Example: <span className="text-mist-400">{active.examples[0].title}</span>
          </p>
        )}
      </section>
    );
  }

  const visible = showAll ? data.groups : data.groups.slice(0, 6);
  return (
    <section className="panel mb-4 overflow-hidden border-ember/20">
      <div className="flex flex-wrap items-start justify-between gap-3 px-4 py-3">
        <div className="flex min-w-0 gap-2.5">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-ember/25 bg-ember/10 text-ember">
            <Smartphone className="h-4 w-4" />
          </span>
          <div className="min-w-0">
            <p className="eyebrow">Queue insight</p>
            <p className="text-xs font-semibold text-mist-100">
              Missing storage is concentrated in {data.total_groups.toLocaleString()} phone model
              {data.total_groups === 1 ? "" : "s"}
            </p>
            <p className="mt-0.5 max-w-2xl text-[10px] leading-relaxed text-mist-500">
              {data.description} Grouping shows whether one model family needs a decision instead of
              hundreds of individual cards.
            </p>
          </div>
        </div>
        <span className="chip border-ember/25 bg-ember/10 text-ember">
          {data.total_listings.toLocaleString()} pending
        </span>
      </div>

      <div>
        {visible.map((group) => (
          <div
            key={group.product_key}
            className="flex flex-wrap items-center gap-2 border-t border-ink-700/60 px-4 py-2.5"
          >
            <button
              className="flex min-w-0 flex-1 items-center gap-2.5 text-left"
              onClick={() => onSelect(group.product_key)}
              title={`Filter the queue to ${group.product_name}`}
            >
              <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-ink-600 bg-ink-800 text-mist-400">
                <Smartphone className="h-3.5 w-3.5" />
              </span>
              <div className="min-w-0">
                <p className="truncate text-[11px] font-medium text-mist-200">{group.product_name}</p>
                <p className="truncate text-[9px] text-mist-600">
                  {money(group.price_low)}–{money(group.price_high)} ·{" "}
                  {group.marketplaces.map((marketplace) => marketplaceMeta(marketplace).label).join(", ")}
                </p>
              </div>
            </button>
            <span className="chip shrink-0 border-ink-600 bg-ink-800 text-[9px] text-mist-300">
              {group.count}
            </span>
            <div className="flex shrink-0 items-center gap-1.5">
              <button
                className="btn-ghost !px-2 !py-1 !text-[10px] text-mist-400"
                onClick={() => onSelect(group.product_key)}
              >
                Review
              </button>
              <button
                className="btn-secondary !px-2 !py-1 !text-[10px]"
                onClick={() => void decide(group, "rejected")}
                disabled={Boolean(busy) || group.listings_truncated}
                title={`Reject all ${group.count} pending ${group.product_name} listings`}
              >
                {busy === group.product_key ? <Loader2 className="h-3 w-3 animate-spin" /> : <X className="h-3 w-3" />}
                Reject
              </button>
              <button
                className="btn-primary !px-2 !py-1 !text-[10px]"
                onClick={() => void decide(group, "approved")}
                disabled={Boolean(busy) || group.listings_truncated}
                title={`Approve all ${group.count} pending ${group.product_name} listings`}
              >
                <Check className="h-3 w-3" /> Approve
              </button>
            </div>
          </div>
        ))}
      </div>

      {(data.groups.length > 6 || data.total_groups > data.groups.length) && (
        <button
          className={cn(
            "flex w-full items-center justify-center gap-1.5 border-t border-ink-700/60 px-4 py-2 text-[10px] text-mist-500 transition hover:text-mist-300",
          )}
          onClick={() => setShowAll((value) => !value)}
        >
          {showAll ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
          {showAll
            ? "Show fewer models"
            : `Show more (${Math.min(40, data.groups.length)} of ${data.total_groups} models)`}
        </button>
      )}
    </section>
  );
}
