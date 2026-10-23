import { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowRight,
  ArrowUpRight,
  BellRing,
  CircleAlert,
  Clock3,
  Gauge,
  RefreshCw,
  Radar,
  ScanSearch,
  Sparkles,
  TrendingUp,
  Wallet,
} from "lucide-react";
import type { Deal, DealFilters, HealthResponse, Overview } from "../lib/types";
import { api } from "../lib/api";
import { useDeals } from "../hooks/useDeals";
import { DealCard } from "../components/DealCard";
import { FilterBar } from "../components/FilterBar";
import { EmptyRadar, SkeletonCard, StatusPill, cn } from "../components/ui";
import { clockTime, money, pct, timeAgo } from "../lib/format";

interface Props {
  overview: Overview | null;
  health: HealthResponse | null;
  refreshTick: number;
  onSelectDeal: (id: number) => void;
  onToast: (message: string, tone?: "success" | "error" | "info") => void;
  onRefreshSummary: () => void;
  onScanNow: () => void;
  onNavigate: (tab: string) => void;
}

export function DealsPage({
  overview,
  health,
  refreshTick,
  onSelectDeal,
  onToast,
  onRefreshSummary,
  onScanNow,
  onNavigate,
}: Props) {
  const [filters, setFilters] = useState<DealFilters>({ sort: "score" });
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const seenRef = useRef<Set<number> | null>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedQuery(query.trim()), 320);
    return () => window.clearTimeout(timer);
  }, [query]);

  const effectiveFilters = useMemo(
    () => ({ ...filters, q: debouncedQuery || undefined }),
    [filters, debouncedQuery],
  );
  const { deals, total, loading, loadingMore, error, hasMore, loadMore, reload, updateDeal, removeDeal } = useDeals(
    effectiveFilters,
    refreshTick,
  );

  useEffect(() => {
    if (!deals.length) return;
    const stored = window.localStorage.getItem("rr-seen-deals");
    let seen = seenRef.current;
    if (!seen) {
      seen = new Set<number>(stored ? (JSON.parse(stored) as number[]) : []);
      seenRef.current = seen;
      if (!stored) {
        const ids = deals.map((deal) => deal.id);
        window.localStorage.setItem("rr-seen-deals", JSON.stringify(ids.slice(0, 500)));
        return;
      }
    }
    const fresh = deals.filter((deal) => !seen!.has(deal.id));
    const allIds = Array.from(new Set([...Array.from(seen), ...deals.map((deal) => deal.id)]));
    window.localStorage.setItem("rr-seen-deals", JSON.stringify(allIds.slice(0, 500)));
    if (
      fresh.length > 0 &&
      document.hidden &&
      window.localStorage.getItem("rr-browser-notifications") === "1" &&
      "Notification" in window &&
      window.Notification.permission === "granted"
    ) {
      fresh.slice(0, 3).forEach((deal) => {
        const notification = new window.Notification(
          `${deal.marketplace_label}: ${money(deal.net_profit)} potential profit`,
          {
            body: `${deal.title}\n${money(deal.price)} → est. ${money(deal.expected_resale)} resale`,
            tag: `flipintel-${deal.id}`,
          },
        );
        notification.onclick = () => {
          window.open(deal.url, "_blank", "noopener");
        };
      });
    }
  }, [deals]);

  async function handleSave(deal: Deal) {
    try {
      if (deal.saved) {
        await api.unsave(deal.id);
        updateDeal(deal.id, { saved: false });
        onToast("Removed from saved");
      } else {
        await api.save(deal.id);
        updateDeal(deal.id, { saved: true });
        onToast("Saved to your shortlist");
      }
      onRefreshSummary();
    } catch {
      onToast("Could not update this deal", "error");
    }
  }

  async function handleDismiss(deal: Deal) {
    try {
      await api.dismiss(deal.id);
      removeDeal(deal.id);
      onToast("Deal dismissed");
      onRefreshSummary();
    } catch {
      onToast("Could not dismiss this deal", "error");
    }
  }

  const sources = health?.scanner.sources ?? [];
  const publicSources = sources.filter((source) => source.marketplace !== "facebook");
  const failing = sources.filter(
    (source) => source.enabled && ["blocked", "error", "degraded"].includes(source.state),
  );

  return (
    <div>
      <Hero
        overview={overview}
        health={health}
        onScanNow={onScanNow}
        onNavigate={onNavigate}
      />

      <div className="mb-5 grid grid-cols-2 gap-3 xl:grid-cols-4">
        <Stat
          icon={Wallet}
          label="Potential profit"
          value={money(overview?.potential_profit ?? 0)}
          sub={`${overview?.deals ?? 0} live opportunities`}
          accent
          loading={!overview}
        />
        <Stat
          icon={Sparkles}
          label="New today"
          value={`${overview?.new_deals_24h ?? 0}`}
          sub={
            overview?.review_count
              ? `${overview.review_count} need manual review`
              : "Qualifying deals in 24h"
          }
          loading={!overview}
        />
        <Stat
          icon={TrendingUp}
          label="Average ROI"
          value={pct(overview?.avg_roi ?? 0, 0)}
          sub={`${pct(overview?.avg_discount ?? 0, 0)} under model value`}
          loading={!overview}
        />
        <Stat
          icon={ScanSearch}
          label="Market coverage"
          value={(overview?.active_listings ?? 0).toLocaleString()}
          sub={`${overview?.tracked_models ?? 0} models · ${overview?.scans_24h ?? 0} scans today`}
          loading={!overview}
        />
      </div>

      <div className="panel mb-5 flex flex-wrap items-center justify-between gap-3 px-4 py-3">
        <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
          <span className="flex items-center gap-2 text-[11px] font-medium text-mist-300">
            <span className="relative flex h-2 w-2">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-lime-400 opacity-50" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-lime-400" />
            </span>
            Market pulse
          </span>
          {publicSources.map((source) => (
            <div key={source.marketplace} className="flex items-center gap-2 text-[11px]">
              <span className="text-mist-400">{source.label}</span>
              <span className={source.last_success_at ? "text-mist-200" : "text-mist-500"}>
                {source.last_success_at ? timeAgo(source.last_success_at) : "awaiting first scan"}
              </span>
            </div>
          ))}
          {health?.scanner.cex && (
            <div className="flex items-center gap-2 text-[11px]">
              <span className="text-mist-400">CeX</span>
              <span className={cn(health.scanner.cex.status === "ok" ? "text-lime-300" : "text-mist-500")}>
                {health.scanner.cex.status === "ok" ? "quotes live" : health.scanner.cex.status === "disabled" ? "optional" : "unavailable"}
              </span>
            </div>
          )}
        </div>
        <button onClick={() => onNavigate("scanners")} className="btn-ghost !py-1 !text-[11px] text-mist-400">
          Scanner health <ArrowRight className="h-3 w-3" />
        </button>
      </div>

      {failing.length > 0 && (
        <div className="mb-5 flex flex-wrap items-center gap-2 rounded-xl border border-ember/20 bg-ember/[0.04] px-4 py-3 text-xs text-mist-300">
          <CircleAlert className="h-4 w-4 text-ember" />
          <span>
            {failing.length} source{failing.length > 1 ? "s" : ""} need attention:{" "}
            {failing.map((source) => `${source.label} (${source.state})`).join(", ")}.
          </span>
          <button className="font-medium text-ember hover:underline" onClick={() => onNavigate("scanners")}>
            View details
          </button>
        </div>
      )}

      {Boolean(overview?.review_count) && !filters.review && (
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-ember/20 bg-ember/[0.04] px-4 py-3 text-xs text-mist-300">
          <span className="flex items-center gap-2">
            <CircleAlert className="h-4 w-4 text-ember" />
            {overview?.review_count} listing{overview?.review_count === 1 ? "" : "s"} suppressed because the price is implausibly cheap and needs a human check.
          </span>
          <button
            className="font-medium text-ember hover:underline"
            onClick={() => setFilters((prev) => ({ ...prev, review: true }))}
          >
            Inspect review queue
          </button>
        </div>
      )}

      <FilterBar
        filters={effectiveFilters}
        onChange={(patch) => setFilters((prev) => ({ ...prev, ...patch }))}
        onReset={() => {
          setFilters({ sort: "score" });
          setQuery("");
        }}
        query={query}
        onQueryChange={setQuery}
        total={total}
        loading={loading}
        categories={overview?.categories ?? []}
        reviewCount={overview?.review_count ?? 0}
      />

      <div className="mt-4">
        {error ? (
          <div className="panel flex flex-col items-center gap-3 px-6 py-14 text-center">
            <CircleAlert className="h-7 w-7 text-rose" />
            <p className="text-sm text-mist-300">{error}</p>
            <button className="btn-secondary" onClick={reload}>
              <RefreshCw className="h-4 w-4" /> Try again
            </button>
          </div>
        ) : loading ? (
          <div className="grid gap-4 md:grid-cols-2 2xl:grid-cols-3">
            {Array.from({ length: 6 }).map((_, index) => (
              <SkeletonCard key={index} />
            ))}
          </div>
        ) : deals.length === 0 ? (
          <EmptyDeals
            health={health}
            overview={overview}
            filtered={Boolean(
              query || filters.category || filters.marketplace || filters.condition || filters.min_profit ||
              filters.min_confidence || filters.max_age_hours || filters.review,
            )}
            onReset={() => {
              setFilters({ sort: "score" });
              setQuery("");
            }}
            onScanNow={onScanNow}
            onNavigate={onNavigate}
          />
        ) : (
          <>
            <div className="mb-3 flex items-center justify-between gap-2">
              <div className="flex items-center gap-2 text-[11px] text-mist-500">
                <Gauge className="h-3.5 w-3.5" />
                Confidence-weighted ranking · {total.toLocaleString()} opportunities
              </div>
              <div className="hidden items-center gap-2 text-[11px] text-mist-500 sm:flex">
                <Clock3 className="h-3 w-3" /> Updated {clockTime(health?.time)}
              </div>
            </div>
            <div className="grid gap-4 md:grid-cols-2 2xl:grid-cols-3">
              {deals.map((deal) => (
                <DealCard
                  key={deal.id}
                  deal={deal}
                  onOpen={onSelectDeal}
                  onSave={handleSave}
                  onDismiss={handleDismiss}
                />
              ))}
            </div>
            {hasMore && (
              <div className="mt-6 flex justify-center">
                <button className="btn-secondary px-6" onClick={loadMore} disabled={loadingMore}>
                  {loadingMore ? "Loading more…" : `Load more (${total - deals.length} remaining)`}
                </button>
              </div>
            )}
            <div className="mt-8 flex flex-wrap items-center justify-between gap-3 border-t border-ink-700/60 pt-4 text-[10px] leading-relaxed text-mist-600">
              <p className="max-w-2xl">
                Resale estimates are modelled from live market data and selling costs; they are not guarantees.
                Always verify the exact model, condition and included accessories before buying.
              </p>
              <button onClick={() => onNavigate("settings")} className="hover:text-mist-300">
                Tune deal thresholds <ArrowUpRight className="inline h-3 w-3" />
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function Hero({
  overview,
  health,
  onScanNow,
  onNavigate,
}: {
  overview: Overview | null;
  health: HealthResponse | null;
  onScanNow: () => void;
  onNavigate: (tab: string) => void;
}) {
  const sourceCount = health?.scanner.sources.filter((source) => source.available).length ?? 0;
  return (
    <section className="panel relative mb-5 overflow-hidden">
      <div className="pointer-events-none absolute inset-0 grid-noise opacity-70" />
      <div className="pointer-events-none absolute -right-32 -top-36 h-96 w-96 rounded-full bg-lime-400/[0.055] blur-3xl" />
      <div className="relative grid gap-8 p-6 lg:grid-cols-[1.45fr_1fr] lg:p-8">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="chip border-lime-400/25 bg-lime-400/10 text-lime-300">
              <span className="h-1.5 w-1.5 animate-pulse-soft rounded-full bg-lime-400" />
              Live market radar
            </span>
            <span className="eyebrow">{sourceCount} sources online</span>
          </div>
          <h1 className="mt-4 max-w-2xl text-3xl font-semibold leading-[1.08] tracking-[-0.035em] text-mist-100 sm:text-[40px]">
            The good stuff,
            <br />
            <span className="text-lime-300">before anyone else.</span>
          </h1>
          <p className="mt-4 max-w-xl text-sm leading-relaxed text-mist-400">
            FlipIntel scans Vinted, Gumtree and eBay UK, identifies the exact hardware, values it against
            the live market and surfaces only the listings with real profit left after fees.
          </p>
          <div className="mt-6 flex flex-wrap items-center gap-2">
            <button onClick={onScanNow} className="btn-primary">
              <Radar className="h-4 w-4" /> Scan now
            </button>
            <button onClick={() => onNavigate("scanners")} className="btn-secondary">
              How valuations work
            </button>
            {overview?.best && (
              <button
                onClick={() => onNavigate("scanners")}
                className="hidden items-center gap-2 rounded-lg px-2 py-2 text-[11px] text-mist-500 transition hover:text-mist-300 md:flex"
              >
                <BellRing className="h-3.5 w-3.5" />
                Best live: {money(overview.best.net_profit)} profit · {pct(overview.best.roi, 0)} ROI
              </button>
            )}
          </div>
          <div className="mt-7 flex flex-wrap items-center gap-x-7 gap-y-3 border-t border-ink-700/60 pt-4">
            <MiniStat label="Last successful scan" value={health?.last_successful_scan ? timeAgo(health.last_successful_scan) : "starting…"} />
            <MiniStat
              label="Scanner status"
              value={health?.scanner.scanner_running ? "Running" : "Paused"}
              accent={Boolean(health?.scanner.scanner_running)}
            />
            <MiniStat label="Confidence floor" value={pct(health?.settings_thresholds.min_confidence ?? 0.42, 0)} />
          </div>
        </div>

        <div className="relative mx-auto flex w-full max-w-[330px] items-center justify-center lg:max-w-none">
          <div className="relative aspect-square w-full max-w-[300px]">
            <div className="absolute inset-0 rounded-full border border-lime-400/10" />
            <div className="absolute inset-[13%] rounded-full border border-lime-400/10" />
            <div className="absolute inset-[27%] rounded-full border border-lime-400/15" />
            <div className="absolute inset-[41%] rounded-full border border-lime-400/20" />
            <div className="absolute left-1/2 top-0 h-full w-px -translate-x-1/2 bg-gradient-to-b from-transparent via-lime-400/10 to-transparent" />
            <div className="absolute left-0 top-1/2 h-px w-full -translate-y-1/2 bg-gradient-to-r from-transparent via-lime-400/10 to-transparent" />
            <div
              className="absolute inset-[3%] rounded-full opacity-70"
              style={{
                background: "conic-gradient(from 0deg, transparent 0deg, transparent 300deg, rgba(61,220,132,0.16) 350deg, rgba(61,220,132,0.45) 360deg)",
                animation: "spin 12s linear infinite",
                maskImage: "radial-gradient(circle, transparent 12%, black 13%)",
              }}
            />
            <div className="absolute inset-0 flex items-center justify-center">
              <div className="relative flex h-20 w-20 items-center justify-center rounded-full border border-lime-400/30 bg-ink-900/90 shadow-glow">
                <Radar className="h-8 w-8 text-lime-300" strokeWidth={1.4} />
              </div>
            </div>
            <RadarDot className="left-[19%] top-[28%]" label="Gumtree" delay="0s" />
            <RadarDot className="left-[70%] top-[23%]" label="Vinted" delay="0.8s" />
            <RadarDot className="left-[76%] top-[67%]" label="eBay" delay="1.6s" />
            <RadarDot className="left-[26%] top-[72%]" label="CeX" delay="2.2s" muted />
            <div className="absolute bottom-0 left-0 right-0 flex items-center justify-between px-2 font-mono text-[9px] uppercase tracking-[0.18em] text-mist-500">
              <span>Scanning / 5 min</span>
              <span className="text-lime-400/70">UK · GBP</span>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

function RadarDot({
  className,
  label,
  delay,
  muted = false,
}: {
  className: string;
  label: string;
  delay: string;
  muted?: boolean;
}) {
  return (
    <div className={cn("absolute flex items-center gap-1.5", className)}>
      <span
        className={cn(
          "relative flex h-2 w-2",
          muted ? "opacity-50" : "",
        )}
      >
        <span className={cn("absolute inline-flex h-full w-full animate-ping rounded-full", muted ? "bg-mist-500" : "bg-lime-400")} style={{ animationDelay: delay }} />
        <span className={cn("relative inline-flex h-2 w-2 rounded-full", muted ? "bg-mist-500" : "bg-lime-300")} />
      </span>
      <span className="rounded border border-ink-600/70 bg-ink-950/80 px-1.5 py-0.5 text-[9px] text-mist-400 backdrop-blur">
        {label}
      </span>
    </div>
  );
}

function MiniStat({ label, value, accent = false }: { label: string; value: string; accent?: boolean }) {
  return (
    <div>
      <p className="eyebrow">{label}</p>
      <p className={cn("mt-1 text-xs font-medium", accent ? "text-lime-300" : "text-mist-200")}>{value}</p>
    </div>
  );
}

function Stat({
  icon: Icon,
  label,
  value,
  sub,
  accent = false,
  loading = false,
}: {
  icon: typeof Wallet;
  label: string;
  value: string;
  sub: string;
  accent?: boolean;
  loading?: boolean;
}) {
  return (
    <div className="panel relative overflow-hidden px-4 py-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="eyebrow">{label}</p>
          {loading ? (
            <div className="skeleton mt-2 h-7 w-24" />
          ) : (
            <p className={cn("numeric mt-1.5 text-2xl font-semibold tracking-tight", accent ? "text-lime-300" : "text-mist-100")}>
              {value}
            </p>
          )}
          <p className="mt-1 text-[10px] text-mist-500">{sub}</p>
        </div>
        <span className={cn("flex h-8 w-8 items-center justify-center rounded-lg border", accent ? "border-lime-400/20 bg-lime-400/10 text-lime-300" : "border-ink-600 bg-ink-800 text-mist-400")}>
          <Icon className="h-4 w-4" />
        </span>
      </div>
    </div>
  );
}

function EmptyDeals({
  health,
  overview,
  filtered,
  onReset,
  onScanNow,
  onNavigate,
}: {
  health: HealthResponse | null;
  overview: Overview | null;
  filtered: boolean;
  onReset: () => void;
  onScanNow: () => void;
  onNavigate: (tab: string) => void;
}) {
  if (filtered) {
    return (
      <EmptyRadar
        title="No deals match those filters"
        description="The scanner is working, but nothing in this slice clears your profit, age or source filters. Widen the filters to see the full feed."
        action={
          <button className="btn-secondary mt-1" onClick={onReset}>
            Clear filters
          </button>
        }
      />
    );
  }
  const lastScan = overview?.last_scan;
  return (
    <EmptyRadar
      title={lastScan ? "No qualifying deals right now" : "Warming up the radar"}
      description={
        lastScan
          ? `The scanner has checked ${(overview?.active_listings ?? 0).toLocaleString()} live listings and found nothing that clears the current profit, ROI and confidence thresholds. It will keep looking automatically.`
          : "The first scans are running now. FlipIntel needs a few passes to build comparable-market evidence before it recommends anything — no invented listings, no fake valuations."
      }
      action={
        <div className="mt-1 flex flex-wrap justify-center gap-2">
          <button className="btn-primary" onClick={onScanNow}>
            <Radar className="h-4 w-4" /> Scan now
          </button>
          <button className="btn-secondary" onClick={() => onNavigate("scanners")}>
            Scanner status
          </button>
        </div>
      }
    />
  );
}
