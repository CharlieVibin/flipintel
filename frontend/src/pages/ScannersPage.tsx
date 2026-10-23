import { useEffect, useState } from "react";
import {
  Activity,
  ArrowUpRight,
  Ban,
  CircleCheck,
  CircleDashed,
  Clock3,
  Database,
  KeyRound,
  Play,
  Radar,
  RefreshCw,
  ServerCog,
  Timer,
  TriangleAlert,
} from "lucide-react";
import { api } from "../lib/api";
import type { HealthResponse, ScanRun, SourceStatus } from "../lib/types";
import { cn, StatusPill } from "../components/ui";
import { formatDuration, marketplaceMeta, timeAgo, timeUntil } from "../lib/format";

interface Props {
  health: HealthResponse | null;
  refreshTick: number;
  onToast: (message: string, tone?: "success" | "error" | "info") => void;
  onRefresh: () => void;
}

function nextScanLabel(value: string | null): string {
  if (!value) return "—";
  const target = new Date(value).getTime();
  const diff = target - Date.now();
  if (Number.isNaN(target)) return "—";
  if (diff <= 0) return "due now";
  if (diff < 60_000) return `in ${Math.ceil(diff / 1000)}s`;
  if (diff < 3_600_000) return `in ${Math.ceil(diff / 60_000)}m`;
  return `in ${Math.ceil(diff / 3_600_000)}h`;
}

export function ScannersPage({ health, refreshTick, onToast, onRefresh }: Props) {
  const [scans, setScans] = useState<ScanRun[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [scanAllBusy, setScanAllBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    api
      .scans()
      .then((response) => {
        if (active) setScans(response.scans);
      })
      .catch(() => {
        if (active) setError("Could not load scan history");
      });
    return () => {
      active = false;
    };
  }, [refreshTick]);

  async function scanOne(marketplace?: string) {
    if (marketplace) setBusy(marketplace);
    else setScanAllBusy(true);
    try {
      const response = await api.scan(marketplace);
      onToast(response.message || "Scan queued", response.accepted.length ? "success" : "info");
      window.setTimeout(onRefresh, 1800);
    } catch (err) {
      onToast(err instanceof Error ? err.message : "Could not start scan", "error");
    } finally {
      if (marketplace) window.setTimeout(() => setBusy(null), 900);
      else window.setTimeout(() => setScanAllBusy(false), 900);
    }
  }

  const sources = health?.scanner.sources ?? [];
  const coverage = health?.scanner.coverage;
  const scansToday = scans.filter((scan) => scan.started_at.slice(0, 10) === new Date().toISOString().slice(0, 10)).length;

  return (
    <div>
      <div className="mb-5 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="eyebrow mb-1.5">Operations</p>
          <h1 className="text-2xl font-semibold tracking-tight text-mist-100">Scanner health</h1>
          <p className="mt-1 max-w-2xl text-sm text-mist-400">
            Every source scans independently with its own rate limit and circuit breaker. Last-success times below
            are real completions, never scheduled attempts.
          </p>
        </div>
        <button
          className="btn-primary"
          onClick={() => void scanOne()}
          disabled={scanAllBusy}
        >
          <Play className={cn("h-4 w-4", scanAllBusy && "animate-pulse")} />
          {scanAllBusy ? "Starting…" : "Scan all sources"}
        </button>
      </div>

      <div className="mb-5 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <OpsStat icon={Database} label="Active listings" value={(health?.counts.active_listings ?? 0).toLocaleString()} />
        <OpsStat icon={Radar} label="Deals found" value={(health?.counts.deals ?? 0).toLocaleString()} accent />
        <OpsStat
          icon={Activity}
          label="Review queue"
          value={(health?.counts.review ?? 0).toLocaleString()}
          sub={(health?.counts.duplicates ?? 0) ? `${health?.counts.duplicates} probable duplicate(s)` : "No duplicate flags"}
        />
        <OpsStat
          icon={CircleCheck}
          label="Last refresh"
          value={health?.scanner.last_refresh ? `${health.scanner.last_refresh.deals} deals` : "—"}
          sub={health?.scanner.last_refresh_at ? timeAgo(health.scanner.last_refresh_at) : undefined}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        {sources.map((source) => (
          <SourceCard
            key={source.marketplace}
            source={source}
            busy={busy === source.marketplace}
            onScan={() => void scanOne(source.marketplace)}
            ebayConfigured={health?.ebay_configured ?? false}
          />
        ))}
        <div className="panel p-5">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="eyebrow">Valuation anchor</p>
              <h3 className="mt-1 text-base font-semibold text-mist-100">CeX UK</h3>
            </div>
            <span
              className={cn(
                "chip",
                health?.scanner.cex.status === "ok"
                  ? "border-lime-400/25 bg-lime-400/10 text-lime-300"
                  : health?.scanner.cex.status === "blocked"
                    ? "border-ember/25 bg-ember/10 text-ember"
                    : "border-ink-500 bg-ink-700/40 text-mist-400",
              )}
            >
              {health?.scanner.cex.status === "ok"
                ? "Live quotes"
                : health?.scanner.cex.status === "blocked"
                  ? "Blocked by CeX"
                  : health?.scanner.cex.status === "disabled"
                    ? "Optional · off"
                    : "Unavailable"}
            </span>
          </div>
          <p className="mt-3 text-[12px] leading-relaxed text-mist-400">
            Used only as an optional resale anchor for models with active listings. CeX sits behind Cloudflare
            and may refuse this network; quotes are cached for 24 hours when available.
          </p>
          {health?.scanner.cex.error && (
            <p className="mt-3 rounded-lg border border-ink-700 bg-ink-900/60 p-2.5 text-[11px] leading-relaxed text-mist-500">
              {health.scanner.cex.error}
            </p>
          )}
          <div className="mt-4 flex items-center gap-2 text-[11px] text-mist-500">
            <CircleDashed className="h-3.5 w-3.5" />
            {health?.scanner.cex.status === "blocked"
              ? "Deals are still valued from live marketplace comparables; confidence is capped without sold evidence."
              : "CeX is never treated as a successful listing source."}
          </div>
        </div>
      </div>

      {coverage && (
        <div className="panel mt-5 overflow-hidden">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-ink-700 px-5 py-4">
            <div>
              <h2 className="text-sm font-semibold text-mist-100">Category coverage</h2>
              <p className="mt-0.5 text-[11px] text-mist-500">
                What the collectors actually found, not just what the search list asks for. Thin categories are
                prioritised in the next query cycle.
              </p>
            </div>
            <span className="chip border-ink-600 bg-ink-800 text-mist-400">
              {coverage.covered_categories}/{coverage.categories.length} covered · {coverage.queries_per_cycle} queries/cycle
            </span>
          </div>
          <div className="grid gap-px bg-ink-700/60 sm:grid-cols-2 lg:grid-cols-4">
            {coverage.categories.map((category) => (
              <div key={category.key} className="bg-ink-900 px-4 py-3">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[11px] font-medium text-mist-200">{category.label}</span>
                  <span className={cn(
                    "chip border-ink-600 bg-ink-800 text-[9px]",
                    category.coverage === "covered" ? "text-lime-300" : category.coverage === "thin" ? "text-ember" : "text-mist-500",
                  )}>
                    {category.coverage}
                  </span>
                </div>
                <div className="mt-2 flex items-end justify-between gap-2">
                  <p className="numeric text-lg font-semibold text-mist-100">{category.active.toLocaleString()}</p>
                  <p className="text-right text-[9px] leading-relaxed text-mist-500">
                    +{category.new_24h} new / 24h<br />
                    {category.valued.toLocaleString()} valued · {category.deals} deals
                  </p>
                </div>
              </div>
            ))}
          </div>
          <div className="flex flex-wrap gap-x-4 gap-y-1 border-t border-ink-700 px-5 py-3 text-[10px] text-mist-500">
            <span>{coverage.active_total.toLocaleString()} active listings across measured categories</span>
            <span>{coverage.thin_categories} thin</span>
            <span>{coverage.empty_categories} empty</span>
            <span>{coverage.query_categories} discovery categories configured</span>
          </div>
        </div>
      )}

      {scans[0]?.detail?.query_timings?.length ? (
        <div className="panel mt-5 overflow-hidden">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-ink-700 px-5 py-4">
            <div>
              <h2 className="text-sm font-semibold text-mist-100">Latest query timings</h2>
              <p className="mt-0.5 text-[11px] text-mist-500">
                {marketplaceMeta(scans[0].marketplace).label} · collector {formatDuration(scans[0].detail.collector_ms ?? null)} · total {formatDuration(scans[0].duration_ms)}
              </p>
            </div>
            <span className="chip border-ink-600 bg-ink-800 text-mist-400">
              {scans[0].detail.query_timings.length} queries
            </span>
          </div>
          <div className="grid gap-4 p-4 lg:grid-cols-[280px_minmax(0,1fr)]">
            <div className="space-y-2">
              <p className="eyebrow">Stage breakdown</p>
              {Object.entries(scans[0].detail.stage_ms ?? {}).map(([stage, ms]) => (
                <div key={stage} className="flex items-center justify-between gap-2 text-[11px]">
                  <span className="capitalize text-mist-400">{stage.replaceAll("_", " ")}</span>
                  <span className="numeric text-mist-300">{formatDuration(ms)}</span>
                </div>
              ))}
              <p className="border-t border-ink-700 pt-2 text-[9px] leading-relaxed text-mist-600">
                Collection is paced sequentially per host. Ingestion, duplicate detection and valuation are measured
                separately so network latency is not mistaken for processing cost.
              </p>
            </div>
            <div className="space-y-1.5">
              {[...scans[0].detail.query_timings]
                .sort((a, b) => b.ms - a.ms)
                .slice(0, 8)
                .map((timing) => {
                  const max = Math.max(...scans[0].detail.query_timings!.map((item) => item.ms), 1);
                  return (
                    <div key={`${timing.query}-${timing.ms}`}>
                      <div className="flex items-center justify-between gap-2 text-[10px]">
                        <span className="truncate text-mist-400">{timing.query}</span>
                        <span className="numeric shrink-0 text-mist-500">{timing.items} rows · {formatDuration(timing.ms)}</span>
                      </div>
                      <div className="mt-1 h-1 overflow-hidden rounded-full bg-ink-700">
                        <div className={cn("h-full rounded-full", timing.status === "ok" ? "bg-lime-400/60" : "bg-ember/60")} style={{ width: `${Math.max(3, (timing.ms / max) * 100)}%` }} />
                      </div>
                    </div>
                  );
                })}
            </div>
          </div>
        </div>
      ) : null}

      <div className="panel mt-5 overflow-hidden">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-ink-700 px-5 py-4">
          <div>
            <h2 className="text-sm font-semibold text-mist-100">Recent scan runs</h2>
            <p className="mt-0.5 text-[11px] text-mist-500">Actual requests, parsed rows and failures — nothing is hidden.</p>
          </div>
          <button
            className="btn-ghost !py-1.5 !text-xs"
            onClick={() => {
              api.scans().then((response) => setScans(response.scans)).catch(() => setError("Could not refresh scans"));
              onRefresh();
            }}
          >
            <RefreshCw className="h-3.5 w-3.5" /> Refresh
          </button>
        </div>
        {error ? (
          <p className="p-6 text-sm text-rose">{error}</p>
        ) : scans.length === 0 ? (
          <div className="px-5 py-10 text-center text-sm text-mist-400">
            No scans recorded yet. The first cycle starts automatically when the app launches.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] text-left text-[12px]">
              <thead className="border-b border-ink-700/70 text-[10px] uppercase tracking-wider text-mist-500">
                <tr>
                  <th className="px-5 py-2.5 font-medium">Started</th>
                  <th className="px-3 py-2.5 font-medium">Source</th>
                  <th className="px-3 py-2.5 font-medium">Result</th>
                  <th className="px-3 py-2.5 text-right font-medium">Found</th>
                  <th className="px-3 py-2.5 text-right font-medium">New</th>
                  <th className="px-3 py-2.5 text-right font-medium">Drops</th>
                  <th className="px-3 py-2.5 text-right font-medium">Dup.</th>
                  <th className="px-3 py-2.5 text-right font-medium">Duration</th>
                  <th className="px-5 py-2.5 font-medium">Detail</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-ink-700/50">
                {scans.slice(0, 20).map((scan) => (
                  <tr key={scan.id} className="transition hover:bg-ink-800/40">
                    <td className="whitespace-nowrap px-5 py-2.5 text-mist-400">
                      {new Date(scan.started_at).toLocaleString("en-GB", {
                        day: "2-digit",
                        month: "short",
                        hour: "2-digit",
                        minute: "2-digit",
                      })}
                    </td>
                    <td className="px-3 py-2.5">
                      <span className="text-mist-200">{marketplaceMeta(scan.marketplace).label}</span>
                    </td>
                    <td className="px-3 py-2.5">
                      <StatusPill state={scan.status} />
                    </td>
                    <td className="numeric px-3 py-2.5 text-right text-mist-300">{scan.items_found}</td>
                    <td className="numeric px-3 py-2.5 text-right text-lime-300/90">{scan.items_new}</td>
                    <td className="numeric px-3 py-2.5 text-right text-mist-300">{scan.price_drops}</td>
                    <td className="numeric px-3 py-2.5 text-right text-mist-400">{scan.duplicates ?? 0}</td>
                    <td className="numeric px-3 py-2.5 text-right text-mist-400">{formatDuration(scan.duration_ms)}</td>
                    <td className="max-w-[240px] px-5 py-2.5">
                      {scan.error ? (
                        <span className="flex items-center gap-1 truncate text-rose" title={scan.error}>
                          <TriangleAlert className="h-3 w-3 shrink-0" />
                          <span className="truncate">{scan.error}</span>
                        </span>
                      ) : (
                        <span className="block truncate text-mist-600" title={scan.queries.join(", ")}>
                          {scan.queries.join(" · ")}
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

function OpsStat({
  icon: Icon,
  label,
  value,
  sub,
  accent = false,
}: {
  icon: typeof Database;
  label: string;
  value: string;
  sub?: string;
  accent?: boolean;
}) {
  return (
    <div className="panel px-4 py-3.5">
      <div className="flex items-start justify-between gap-2">
        <div>
          <p className="eyebrow">{label}</p>
          <p className={cn("numeric mt-1 text-xl font-semibold", accent ? "text-lime-300" : "text-mist-100")}>{value}</p>
          {sub && <p className="mt-0.5 text-[10px] text-mist-500">{sub}</p>}
        </div>
        <Icon className="h-4 w-4 text-mist-500" />
      </div>
    </div>
  );
}

function SourceCard({
  source,
  busy,
  onScan,
  ebayConfigured,
}: {
  source: SourceStatus;
  busy: boolean;
  onScan: () => void;
  ebayConfigured: boolean;
}) {
  const disabled = !source.available || !source.enabled;
  const isEbay = source.marketplace === "ebay";
  const isFacebook = source.marketplace === "facebook";
  return (
    <div className={cn("panel p-5", !source.available && "opacity-90")}>
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2.5">
          <span className={cn("flex h-9 w-9 items-center justify-center rounded-lg border", marketplaceMeta(source.marketplace).className)}>
            <ServerCog className="h-4 w-4" />
          </span>
          <div>
            <h3 className="text-sm font-semibold text-mist-100">{source.label}</h3>
            <p className="text-[10px] text-mist-500">
              {source.enabled ? `Every ${Math.round(source.interval_seconds / 60)} min` : "Paused in settings"}
            </p>
          </div>
        </div>
        <StatusPill state={source.state} running={source.running || busy} />
      </div>

      <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 text-[11px]">
        <div>
          <dt className="text-mist-500">Last successful scan</dt>
          <dd className="mt-0.5 flex items-center gap-1.5 text-mist-200">
            <CircleCheck className="h-3 w-3 text-lime-400" />
            {source.last_success_at ? timeAgo(source.last_success_at) : "Never"}
          </dd>
        </div>
        <div>
          <dt className="text-mist-500">Next scheduled</dt>
          <dd className="mt-0.5 flex items-center gap-1.5 text-mist-300">
            <Timer className="h-3 w-3 text-mist-500" />
            {source.enabled ? nextScanLabel(source.next_scan_at) : "—"}
          </dd>
        </div>
        <div>
          <dt className="text-mist-500">Last attempt</dt>
          <dd className="mt-0.5 text-mist-300">{source.last_attempt_at ? timeAgo(source.last_attempt_at) : "Never"}</dd>
        </div>
        <div>
          <dt className="text-mist-500">Last run</dt>
          <dd className="numeric mt-0.5 text-mist-300">{formatDuration(source.last_duration_ms)}</dd>
        </div>
        <div>
          <dt className="text-mist-500">Listings parsed</dt>
          <dd className="numeric mt-0.5 text-mist-300">{source.listings_seen.toLocaleString()}</dd>
        </div>
        <div>
          <dt className="text-mist-500">Failures / backoff</dt>
          <dd className={cn("mt-0.5", source.consecutive_failures || source.backoff_seconds ? "text-ember" : "text-mist-300")}>
            {source.backoff_seconds
              ? `Cooling down ${Math.ceil(source.backoff_seconds / 60)}m`
              : source.consecutive_failures
                ? `${source.consecutive_failures} consecutive`
                : "None"}
          </dd>
        </div>
      </dl>

      {isEbay && (
        <div className={cn(
          "mt-3 flex items-center justify-between gap-2 rounded-lg border px-3 py-2 text-[10px]",
          ebayConfigured
            ? "border-lime-400/20 bg-lime-400/[0.04] text-lime-300"
            : "border-ember/20 bg-ember/[0.04] text-ember",
        )}>
          <span className="flex items-center gap-1.5"><KeyRound className="h-3 w-3" /> API credentials</span>
          <span>{ebayConfigured ? "Configured · EBAY_GB / GBP" : "Not configured"}</span>
        </div>
      )}

      {source.api_usage && (
        <div className="mt-3 rounded-lg border border-ink-700 bg-ink-900/50 px-3 py-2 text-[10px] text-mist-500">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span>{isEbay ? "API calls" : "Requests"} today</span>
            <span className="numeric text-mist-300">
              {source.api_usage.today.requests} requests · {source.api_usage.today.errors} errors
            </span>
          </div>
          <div className="mt-1 flex flex-wrap items-center justify-between gap-2">
            <span>Last {source.api_usage.window_days} days</span>
            <span className="numeric text-mist-300">
              {source.api_usage.window_requests} / {source.api_usage.window_errors} errors
            </span>
          </div>
          <div className="mt-1 flex flex-wrap items-center justify-between gap-2">
            <span>Last request</span>
            <span className="text-mist-400">
              {source.api_usage.last_request_at ? timeAgo(source.api_usage.last_request_at) : "None yet"}
            </span>
          </div>
          <div className="mt-1 flex flex-wrap items-center justify-between gap-2">
            <span>Last successful request</span>
            <span className="text-mist-400">
              {source.api_usage.last_success_at ? timeAgo(source.api_usage.last_success_at) : "None recorded"}
            </span>
          </div>
          <div className="mt-1 flex items-center justify-between gap-2 border-t border-ink-700/60 pt-1">
            <span>{isEbay ? "Provider quota" : "Quota"}</span>
            <span className={cn(
              "numeric text-right",
              source.api_usage.quota.state === "low" || source.api_usage.quota.state === "exhausted" ? "text-ember" : "text-mist-400",
            )}>
              {source.api_usage.quota.state === "unknown"
                ? "unknown — local counters shown"
                : source.api_usage.quota.remaining != null
                  ? `${source.api_usage.quota.remaining}${source.api_usage.quota.limit ? ` / ${source.api_usage.quota.limit}` : ""} remaining${source.api_usage.quota.reset_at ? ` · resets ${timeUntil(source.api_usage.quota.reset_at)}` : ""}`
                  : source.api_usage.quota.state}
            </span>
          </div>
          {source.api_usage.history.length > 0 && (
            <div className="mt-2 border-t border-ink-700/60 pt-2">
              <div className="mb-1 flex items-center justify-between gap-2">
                <span>Daily requests</span>
                <span>{source.api_usage.history.length} day(s)</span>
              </div>
              <div className="flex items-end gap-1" aria-label="Daily request history">
                {source.api_usage.history.map((day) => {
                  const max = Math.max(...source.api_usage!.history.map((item) => item.requests), 1);
                  return (
                    <div key={day.day} className="flex-1" title={`${day.day}: ${day.requests} requests, ${day.errors} errors`}>
                      <div className="flex h-5 items-end overflow-hidden rounded-sm bg-ink-800">
                        <div
                          className={cn("w-full", day.errors > 0 ? "bg-ember/60" : "bg-sky-400/50")}
                          style={{ height: `${Math.max(8, (day.requests / max) * 100)}%` }}
                        />
                      </div>
                      <p className="mt-0.5 text-center text-[8px] text-mist-600">{day.day.slice(5)}</p>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      )}

      {(source.last_error || source.availability_reason) && (
        <div className="mt-4 rounded-lg border border-ink-700 bg-ink-900/50 p-3 text-[11px] leading-relaxed text-mist-400">
          <p className="mb-1 flex items-center gap-1.5 font-medium text-mist-300">
            {source.available ? <TriangleAlert className="h-3 w-3 text-ember" /> : <Ban className="h-3 w-3 text-mist-500" />}
            {source.available ? "Last error" : "Source unavailable"}
          </p>
          {source.last_error ?? source.availability_reason}
          {isEbay && !ebayConfigured && (
            <div className="mt-2.5 rounded-md border border-ink-700 bg-ink-950/60 p-2.5 font-mono text-[10px] text-mist-300">
              <p className="mb-1 flex items-center gap-1.5 font-sans text-[10px] font-medium text-ember">
                <KeyRound className="h-3 w-3" /> Free eBay developer keys
              </p>
              EBAY_CLIENT_ID=…<br />
              EBAY_CLIENT_SECRET=…
              <a
                href="https://developer.ebay.com/my/keys"
                target="_blank"
                rel="noopener noreferrer"
                className="mt-2 flex items-center gap-1 font-sans text-[10px] text-sky-300 hover:underline"
              >
                Open eBay developer keys <ArrowUpRight className="h-3 w-3" />
              </a>
              <p className="mt-1.5 font-sans text-[9px] leading-relaxed text-mist-500">
                Save both values in Settings → Data sources & credentials. They activate without a restart and
                secrets are never shown in this dashboard.
              </p>
            </div>
          )}
          {isFacebook && (
            <p className="mt-2 text-mist-500">
              Facebook requires an authenticated session and prohibits automated collection. The app never
              fabricates Facebook listings.
            </p>
          )}
        </div>
      )}

      <div className="mt-4 flex items-center gap-2">
        <button className="btn-secondary flex-1 !py-2" onClick={onScan} disabled={disabled || busy}>
          <Play className={cn("h-3.5 w-3.5", busy && "animate-pulse")} />
          {busy ? "Starting…" : "Scan now"}
        </button>
        <a
          className="btn-ghost !px-2.5 !py-2 text-mist-500"
          href={
            isFacebook
              ? "https://www.facebook.com/marketplace"
              : source.marketplace === "ebay"
                ? "https://www.ebay.co.uk"
                : source.marketplace === "gumtree"
                  ? "https://www.gumtree.com"
                  : "https://www.vinted.co.uk"
          }
          target="_blank"
          rel="noopener noreferrer"
          aria-label={`Open ${source.label}`}
        >
          <ArrowUpRight className="h-4 w-4" />
        </a>
      </div>
      {isEbay && !ebayConfigured && (
        <p className="mt-2 text-center text-[10px] text-mist-500">
          No HTML scraping: eBay requires its official, free API.
        </p>
      )}
      {!isEbay && !isFacebook && (
        <p className="mt-2 flex items-center justify-center gap-1.5 text-[10px] text-mist-500">
          <Clock3 className="h-3 w-3" /> Paced with a per-host limiter and circuit breaker
        </p>
      )}
    </div>
  );
}
