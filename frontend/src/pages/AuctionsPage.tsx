import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowUpRight,
  Check,
  CircleAlert,
  Clock3,
  Gavel,
  RefreshCw,
  ShieldCheck,
  ShieldQuestion,
  ShieldX,
  SlidersHorizontal,
  Timer,
  Wallet,
} from "lucide-react";
import { api } from "../lib/api";
import type {
  Auction,
  AuctionDiagnostics,
  AuctionStatus,
  AuctionsResponse,
  HealthResponse,
  PresetSummary,
} from "../lib/types";
import { clockTime, conditionLabel, money, timeAgo } from "../lib/format";
import { EmptyRadar, EvidenceMeter, ProductImage, StatusPill, cn } from "../components/ui";

type StatusFilter = AuctionStatus | "all";

interface Filters {
  ending_within: number;
  sort: "ending" | "bid" | "profit" | "newest";
  status: StatusFilter;
  active_preset: boolean;
}

interface Props {
  health: HealthResponse | null;
  refreshTick: number;
  onSelectDeal: (id: number) => void;
  onToast: (message: string, tone?: "success" | "error" | "info") => void;
  onRefreshSummary: () => void;
  onNavigate: (tab: string) => void;
}

const STATUS_META: Record<AuctionStatus, { label: string; badge: string; icon: typeof ShieldCheck }> = {
  verified: {
    label: "Verified opportunity",
    badge: "border-lime-400/30 bg-lime-400/10 text-lime-300",
    icon: ShieldCheck,
  },
  needs_verification: {
    label: "Needs pricing verification",
    badge: "border-ember/30 bg-ember/10 text-ember",
    icon: ShieldQuestion,
  },
  rejected: {
    label: "Not recommended",
    badge: "border-rose/30 bg-rose/10 text-rose",
    icon: ShieldX,
  },
};

function remainingLabel(endsAt: string | null, now: number): string {
  if (!endsAt) return "End time unknown";
  const target = new Date(endsAt).getTime();
  if (Number.isNaN(target)) return "End time unknown";
  const seconds = Math.round((target - now) / 1000);
  if (seconds <= 0) return "Ended";
  if (seconds < 60) return `${seconds}s left`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s left`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m left`;
  return `${Math.floor(seconds / 86400)}d ${Math.floor((seconds % 86400) / 3600)}h left`;
}

export function AuctionsPage({
  health,
  refreshTick,
  onSelectDeal,
  onToast,
  onRefreshSummary,
  onNavigate,
}: Props) {
  const [filters, setFilters] = useState<Filters>({
    ending_within: 0,
    sort: "ending",
    status: "all",
    active_preset: true,
  });
  const [data, setData] = useState<AuctionsResponse | null>(null);
  const [extra, setExtra] = useState<Auction[]>([]);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const requestRef = useRef(0);

  const load = useCallback(
    async (nextPage: number, append: boolean) => {
      const requestId = ++requestRef.current;
      if (append) setLoadingMore(true);
      else setLoading(true);
      try {
        const response = await api.auctions({ ...filters, page: nextPage, limit: 48 });
        if (requestId !== requestRef.current) return;
        setData(response);
        setPage(nextPage);
        setExtra((prev) => (append ? [...prev, ...response.auctions] : response.auctions));
        setError(null);
      } catch (err) {
        if (requestId !== requestRef.current) return;
        setError(err instanceof Error ? err.message : "Could not load auctions");
      } finally {
        if (requestId === requestRef.current) {
          setLoading(false);
          setLoadingMore(false);
        }
      }
    },
    [filters],
  );

  useEffect(() => {
    void load(1, false);
  }, [load, refreshTick]);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  const handleRefresh = async () => {
    if (refreshing) return;
    setRefreshing(true);
    try {
      const response = await api.refreshAuctions();
      onToast(response.message, response.accepted ? "success" : "info");
      window.setTimeout(() => {
        void load(1, false);
        onRefreshSummary();
      }, 7000);
      window.setTimeout(() => {
        void load(1, false);
        setRefreshing(false);
      }, 22000);
    } catch (err) {
      setRefreshing(false);
      onToast(err instanceof Error ? err.message : "Could not start an auction refresh", "error");
    }
  };

  const auctions = useMemo(() => extra, [extra]);
  const diagnostics = data?.diagnostics ?? null;
  const ebay = health?.scanner.sources.find((source) => source.marketplace === "ebay");

  return (
    <div>
      <div className="mb-4 grid grid-cols-2 gap-3 xl:grid-cols-4">
        <Stat
          icon={Gavel}
          label="Live auctions"
          value={(data?.counts.all ?? 0).toLocaleString()}
          sub={filters.active_preset ? "Inside the active preset" : "All live auctions (history mode)"}
          accent
        />
        <Stat icon={ShieldCheck} label="Verified" value={(data?.counts.verified ?? 0).toLocaleString()} sub="Supported resale and bid ceiling" />
        <Stat icon={ShieldQuestion} label="Needs verification" value={(data?.counts.needs_verification ?? 0).toLocaleString()} sub="No defensible bid ceiling yet" />
        <Stat
          icon={ShieldX}
          label="Not recommended"
          value={(data?.counts.rejected ?? 0).toLocaleString()}
          sub={filters.active_preset ? "Faulty, over bid or above budget" : "Includes outside-preset history"}
        />
      </div>

      <div className="panel mb-4 p-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="chip border-lime-400/25 bg-lime-400/10 text-lime-300">
            <span className={cn("h-1.5 w-1.5 rounded-full", ebay?.state === "blocked" ? "bg-rose" : "bg-lime-400")} />
            eBay auctions
          </span>
          <span className="flex items-center gap-1.5 text-[11px] text-mist-400">
            <Clock3 className="h-3 w-3" />
            {diagnostics?.last_scan?.finished_at
              ? `Last refresh ${timeAgo(diagnostics.last_scan.finished_at)}`
              : ebay?.last_success_at
                ? `Source healthy ${timeAgo(ebay.last_success_at)}`
                : "Waiting for the first auction pass"}
          </span>
          {diagnostics && !diagnostics.settings.alerts_enabled && (
            <span className="chip border-ember/30 bg-ember/10 text-ember">
              <CircleAlert className="h-3 w-3" /> Auction monitoring disabled
            </span>
          )}
          <button
            className="btn-secondary ml-auto !py-2 text-xs"
            onClick={() => void handleRefresh()}
            disabled={refreshing}
            title="Request an auction pass now"
          >
            <RefreshCw className={cn("h-3.5 w-3.5", refreshing && "animate-spin")} />
            {refreshing ? "Refreshing…" : "Refresh auctions"}
          </button>
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-ink-700/60 pt-3">
          <select
            className="field !w-auto !py-2"
            value={filters.ending_within}
            onChange={(event) => setFilters((prev) => ({ ...prev, ending_within: Number(event.target.value) }))}
            aria-label="Ending within"
          >
            <option value={0}>Any end time</option>
            <option value={5}>Ending within 5 min</option>
            <option value={10}>Ending within 10 min</option>
            <option value={15}>Ending within 15 min</option>
            <option value={30}>Ending within 30 min</option>
            <option value={60}>Ending within 60 min</option>
          </select>
          <select
            className="field !w-auto !py-2"
            value={filters.sort}
            onChange={(event) => setFilters((prev) => ({ ...prev, sort: event.target.value as Filters["sort"] }))}
            aria-label="Sort auctions"
          >
            <option value="ending">Ending soonest</option>
            <option value="bid">Lowest current bid</option>
            <option value="profit">Potential profit</option>
            <option value="newest">Newest discovered</option>
          </select>

          <div className="flex flex-wrap items-center gap-1.5">
            {(["all", "verified", "needs_verification", "rejected"] as StatusFilter[]).map((status) => (
              <button
                key={status}
                className={cn("filter-chip", filters.status === status && "filter-chip-active")}
                onClick={() => setFilters((prev) => ({ ...prev, status }))}
              >
                {status === "all"
                  ? "All"
                  : status === "needs_verification"
                    ? "Needs verification"
                    : status === "verified"
                      ? "Verified"
                      : "Rejected"}
                {data ? <span className="numeric text-[9px] opacity-60">{data.counts[status] ?? 0}</span> : null}
              </button>
            ))}
          </div>

          <button
            className={cn("filter-chip ml-auto", filters.active_preset && "filter-chip-active")}
            onClick={() => setFilters((prev) => ({ ...prev, active_preset: !prev.active_preset }))}
            aria-pressed={filters.active_preset}
            title="Apply the saved category, marketplace and budget rules to stored auctions"
          >
            <Check className={cn("h-3 w-3", !filters.active_preset && "opacity-40")} />
            Active preset only
          </button>
        </div>

        {filters.active_preset && (data?.preset.hidden ?? 0) > 0 && (
          <p className="mt-2 text-[10.5px] text-mist-500">
            {data?.preset.hidden.toLocaleString()} stored auction
            {data?.preset.hidden === 1 ? "" : "s"} outside the preset{" "}
            {data?.preset.hidden === 1 ? "is" : "are"} hidden. Turn off Active preset only to inspect them
            as history.
          </p>
        )}
        {!filters.active_preset && (
          <p className="mt-2 text-[10.5px] text-ember">
            History mode: auctions outside the active preset are labelled and shown for inspection only. They
            are never counted as opportunities.
          </p>
        )}
      </div>

      {error ? (
        <div className="panel flex flex-col items-center gap-3 px-6 py-14 text-center">
          <CircleAlert className="h-7 w-7 text-rose" />
          <p className="text-sm text-mist-300">{error}</p>
          <button className="btn-secondary" onClick={() => void load(1, false)}>
            <RefreshCw className="h-4 w-4" /> Try again
          </button>
        </div>
      ) : loading ? (
        <div className="grid gap-3 xl:grid-cols-2">
          {Array.from({ length: 4 }).map((_, index) => (
            <div key={index} className="panel h-56 skeleton rounded-2xl" />
          ))}
        </div>
      ) : auctions.length === 0 ? (
        <EmptyAuctions
          diagnostics={diagnostics}
          preset={data?.preset ?? null}
          filters={filters}
          onRefresh={() => void handleRefresh()}
          onNavigate={onNavigate}
        />
      ) : (
        <>
          <div className="mb-3 flex items-center justify-between gap-2 text-[11px] text-mist-500">
            <span className="flex items-center gap-2">
              <GaugeIcon />
              {data?.total.toLocaleString() ?? 0} auction{data?.total === 1 ? "" : "s"} matching these filters
            </span>
            <span>Current bid is not a guaranteed purchase price</span>
          </div>
          <div className="grid gap-3 xl:grid-cols-2">
            {auctions.map((auction) => (
              <AuctionCard
                key={auction.id}
                auction={auction}
                now={now}
                onOpen={() => onSelectDeal(auction.id)}
              />
            ))}
          </div>
          {data?.has_more && (
            <div className="mt-6 flex justify-center">
              <button
                className="btn-secondary px-6"
                onClick={() => void load(page + 1, true)}
                disabled={loadingMore}
              >
                {loadingMore ? "Loading more…" : "Load more auctions"}
              </button>
            </div>
          )}
          <Diagnostics diagnostics={diagnostics} preset={data?.preset ?? null} />
        </>
      )}
    </div>
  );
}

function AuctionCard({
  auction,
  now,
  onOpen,
}: {
  auction: Auction;
  now: number;
  onOpen: () => void;
}) {
  const status = STATUS_META[auction.verification_status];
  const Icon = status.icon;
  const endsIn = remainingLabel(auction.ends_at, now);
  const urgent = auction.seconds_remaining !== null && auction.seconds_remaining <= 15 * 60;
  return (
    <article
      className={cn(
        "panel relative flex cursor-pointer gap-3 overflow-hidden p-3 transition hover:border-ink-500 hover:shadow-glow",
        auction.outside_preset && "border-ember/30",
        auction.verification_status === "verified" && "border-lime-400/25",
      )}
      onClick={onOpen}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onOpen();
        }
      }}
      tabIndex={0}
      role="button"
      aria-label={`${auction.title}, current bid ${money(auction.current_bid ?? auction.price)}, ${status.label}`}
    >
      <div className="h-28 w-28 shrink-0 overflow-hidden rounded-xl sm:h-32 sm:w-36">
        <ProductImage src={auction.image_url} alt={auction.title} category={auction.category} />
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className={cn("chip", status.badge)} title={auction.verification_reason}>
            <Icon className="h-3 w-3" /> {status.label}
          </span>
          <span
            className={cn(
              "chip",
              urgent
                ? "border-rose/35 bg-rose/10 text-rose"
                : "border-ink-500 bg-ink-800/70 text-mist-300",
            )}
          >
            <Timer className="h-3 w-3" /> {endsIn}
          </span>
          {auction.outside_preset && (
            <span className="chip border-ember/35 bg-ember/10 text-ember" title={auction.preset_reasons?.join("; ")}>
              Outside preset
            </span>
          )}
        </div>

        <h3 className="mt-1.5 line-clamp-2 text-[13.5px] font-semibold leading-snug text-mist-100">
          {auction.title}
        </h3>
        <p className="mt-0.5 truncate font-mono text-[9px] uppercase tracking-[0.14em] text-mist-500">
          {auction.identified_variant ?? auction.product_name ?? "Variant not established"}
        </p>

        <div className="mt-2 grid grid-cols-3 gap-2 rounded-lg border border-ink-700/70 bg-ink-900/50 px-2.5 py-2">
          <div>
            <p className="eyebrow">Current bid</p>
            <p className="numeric text-sm font-semibold text-mist-100">
              {money(auction.current_bid ?? auction.price)}
            </p>
            <p className="text-[9px] text-mist-500">
              {auction.bid_count ?? 0} bid{auction.bid_count === 1 ? "" : "s"}
            </p>
          </div>
          <div>
            <p className="eyebrow">Postage</p>
            <p className="numeric text-sm font-semibold text-mist-200">{money(auction.shipping)}</p>
            <p className="text-[9px] text-mist-500">
              {auction.cost_basis === "postage_unknown" ? "not stated" : "listed"}
            </p>
          </div>
          <div>
            <p className="eyebrow">Total now</p>
            <p className="numeric text-sm font-semibold text-lime-300">{money(auction.total_cost)}</p>
            <p className="text-[9px] text-mist-500">bid + postage</p>
          </div>
        </div>

        <div className="mt-2 grid grid-cols-2 gap-2">
          <div className="rounded-lg border border-ink-700/70 px-2.5 py-2">
            <p className="eyebrow">Estimated resale</p>
            <p className="numeric mt-0.5 text-sm text-mist-100">
              {auction.expected_resale !== null ? money(auction.expected_resale) : "—"}
            </p>
            <EvidenceMeter level={auction.evidence_level} compact />
          </div>
          <div
            className={cn(
              "rounded-lg border px-2.5 py-2",
              auction.profit_supported
                ? "border-lime-400/20 bg-lime-400/[0.04]"
                : "border-ink-700/70 bg-ink-900/40",
            )}
          >
            {auction.profit_supported ? (
              <>
                <p className="eyebrow">Profit at current bid</p>
                <p className="numeric mt-0.5 text-sm font-semibold text-lime-300">
                  {money(auction.net_profit)}
                </p>
                <p className="text-[9px] text-mist-500">Max bid {money(auction.max_bid)}</p>
              </>                ) : (
                  <>
                    <p className="eyebrow">Profit</p>
                    <p className="mt-0.5 text-[11px] leading-snug text-mist-400">
                      {auction.verification_status === "rejected"
                        ? "No supported profit at this bid."
                        : "Withheld until the exact variant has compatible evidence."}
                    </p>
                  </>
                )}
          </div>
        </div>

        <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-[9.5px] text-mist-500">
          <span className="truncate">
            {auction.seller || "Seller unknown"} · {conditionLabel(auction.condition)}
            {auction.location ? ` · ${auction.location}` : ""}
          </span>
          <span className="flex items-center gap-2">
            <span>Ends {clockTime(auction.ends_at)}</span>
            <a
              href={auction.url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 rounded-md border border-ink-600 px-1.5 py-0.5 text-mist-300 hover:text-lime-300"
              onClick={(event) => event.stopPropagation()}
            >
              eBay <ArrowUpRight className="h-2.5 w-2.5" />
            </a>
          </span>
        </div>
        {!auction.profit_supported && auction.verification_reason && (
          <p className="mt-1 line-clamp-1 text-[9.5px] text-ember/80">{auction.verification_reason}</p>
        )}
      </div>
    </article>
  );
}

function Diagnostics({
  diagnostics,
  preset,
}: {
  diagnostics: AuctionDiagnostics | null;
  preset: PresetSummary | null;
}) {
  if (!diagnostics) return null;
  return (
    <div className="panel mt-4 grid gap-3 p-4 sm:grid-cols-2 xl:grid-cols-4">
      <div>
        <p className="eyebrow">eBay source</p>
        <div className="mt-1.5">
          <StatusPill state={diagnostics.source.state} />
        </div>
        <p className="mt-1 text-[10px] text-mist-500">
          {diagnostics.source.last_success_at
            ? `Last success ${timeAgo(diagnostics.source.last_success_at)}`
            : "No auction success recorded"}
        </p>
        {diagnostics.source.last_error && (
          <p className="mt-1 line-clamp-2 text-[10px] text-rose">{diagnostics.source.last_error}</p>
        )}
      </div>
      <div>
        <p className="eyebrow">Last auction pass</p>
        <p className="mt-1 text-[11px] text-mist-200">
          {diagnostics.last_scan
            ? `${diagnostics.last_scan.status} · ${diagnostics.last_scan.items_found} found · ${diagnostics.last_scan.items_new} new`
            : "Not run yet"}
        </p>
        <p className="mt-1 text-[10px] text-mist-500">
          {diagnostics.last_scan?.finished_at
            ? `Finished ${timeAgo(diagnostics.last_scan.finished_at)} in ${diagnostics.last_scan.duration_ms ?? 0}ms`
            : "The first pass starts after the saved configuration exists."}
        </p>
      </div>
      <div>
        <p className="eyebrow">Monitoring cadence</p>
        <p className="mt-1 text-[11px] text-mist-200">
          Every {diagnostics.settings.scan_seconds}s · urgent {diagnostics.settings.urgent_seconds}s
        </p>
        <p className="mt-1 text-[10px] text-mist-500">
          Discovery window {diagnostics.settings.window_minutes} min · alerts{" "}
          {diagnostics.settings.alerts_enabled ? "enabled" : "disabled"}
        </p>
      </div>
      <div>
        <p className="eyebrow">Active preset</p>
        <p className="mt-1 text-[11px] text-mist-200">
          {preset ? `${preset.enabled_count.toLocaleString()} categories · ${money(preset.min_price)}–${money(preset.max_price)}` : "Loading"}
        </p>
        <p className="mt-1 text-[10px] text-mist-500">
          {preset
            ? Object.entries(preset.marketplaces)
                .filter(([, on]) => on)
                .map(([name]) => name)
                .join(" / ") || "No marketplaces"
            : ""}
        </p>
      </div>
    </div>
  );
}

function EmptyAuctions({
  diagnostics,
  preset,
  filters,
  onRefresh,
  onNavigate,
}: {
  diagnostics: AuctionDiagnostics | null;
  preset: PresetSummary | null;
  filters: Filters;
  onRefresh: () => void;
  onNavigate: (tab: string) => void;
}) {
  const reason = !preset?.confirmed
    ? "No saved scan configuration exists, so auction discovery is paused."
    : preset && !preset.marketplaces.ebay
      ? "eBay is disabled in the active scan preset, so auction discovery is paused."
      : diagnostics && !diagnostics.settings.alerts_enabled
        ? "Auction monitoring is disabled in Settings."
        : filters.ending_within > 0
          ? `No auctions inside the active preset end in the next ${filters.ending_within} minutes.`
          : "No auctions have been discovered inside the active preset yet.";
  return (
    <div>
      <EmptyRadar
        title={preset?.confirmed ? "No auctions match these filters" : "Auction discovery needs configuration"}
        description={`${reason} Current bids are never treated as guaranteed purchase prices, and no bid ceiling is invented without compatible evidence.`}
        action={
          <div className="mt-1 flex flex-wrap justify-center gap-2">
            <button className="btn-primary" onClick={onRefresh}>
              <RefreshCw className="h-4 w-4" /> Refresh auctions
            </button>
            <button className="btn-secondary" onClick={() => onNavigate("scan-config")}>
              <SlidersHorizontal className="h-4 w-4" /> Scan setup
            </button>
          </div>
        }
      />
      <Diagnostics diagnostics={diagnostics} preset={preset} />
    </div>
  );
}

function Stat({
  icon: Icon,
  label,
  value,
  sub,
  accent = false,
}: {
  icon: typeof Wallet;
  label: string;
  value: string;
  sub: string;
  accent?: boolean;
}) {
  return (
    <div className="panel px-4 py-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="eyebrow">{label}</p>
          <p className={cn("numeric mt-1.5 text-2xl font-semibold tracking-tight", accent ? "text-lime-300" : "text-mist-100")}>
            {value}
          </p>
          <p className="mt-1 text-[10px] text-mist-500">{sub}</p>
        </div>
        <span className={cn("flex h-8 w-8 items-center justify-center rounded-lg border", accent ? "border-lime-400/20 bg-lime-400/10 text-lime-300" : "border-ink-600 bg-ink-800 text-mist-400")}>
          <Icon className="h-4 w-4" />
        </span>
      </div>
    </div>
  );
}

function GaugeIcon() {
  return (
    <span className="flex h-4 w-4 items-center justify-center rounded-full border border-lime-400/30 text-lime-300">
      <span className="h-1.5 w-1.5 rounded-full bg-lime-400" />
    </span>
  );
}
