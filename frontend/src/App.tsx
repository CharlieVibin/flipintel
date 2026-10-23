import { useCallback, useEffect, useRef, useState } from "react";
import {
  Activity,
  Bookmark,
  CircleAlert,
  Database,
  LayoutGrid,
  RefreshCw,
  Wallet,
  Radar,
  Settings2,
  SlidersHorizontal,
} from "lucide-react";
import { api } from "./lib/api";
import type { HealthResponse, Overview } from "./lib/types";
import { clockTime, timeAgo } from "./lib/format";
import { cn, Logo, Toast, type ToastData } from "./components/ui";
import { DealDrawer } from "./components/DealDrawer";
import { DealsPage } from "./pages/DealsPage";
import { EvidencePage } from "./pages/EvidencePage";
import { FlipsPage } from "./pages/FlipsPage";
import { SavedPage } from "./pages/SavedPage";
import { ScannersPage } from "./pages/ScannersPage";
import { SettingsPage } from "./pages/SettingsPage";

const TABS = ["deals", "saved", "flips", "evidence", "scanners", "settings"] as const;
type Tab = (typeof TABS)[number];

const TAB_META: Record<Tab, { label: string; title: string; subtitle: string; icon: typeof LayoutGrid }> = {
  deals: { label: "Deal feed", title: "Deal desk", subtitle: "Ranked opportunities from live UK listings", icon: LayoutGrid },
  saved: { label: "Shortlist", title: "Shortlist", subtitle: "Saved and dismissed opportunities", icon: Bookmark },
  flips: { label: "My flips", title: "My flips", subtitle: "Purchases, price cuts and realised results", icon: Wallet },
  evidence: { label: "Evidence", title: "Evidence lab", subtitle: "Completed sales, imports and valuation calibration", icon: Database },
  scanners: { label: "Scanners", title: "Scanner health", subtitle: "Collection status, coverage and scan history", icon: Activity },
  settings: { label: "Settings", title: "Settings", subtitle: "Deal thresholds, costs and scan cadence", icon: SlidersHorizontal },
};

function tabFromHash(): Tab {
  const value = window.location.hash.replace(/^#\/?/, "").split("?")[0] as Tab;
  return TABS.includes(value) ? value : "deals";
}

export default function App() {
  const [tab, setTab] = useState<Tab>(tabFromHash);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [refreshTick, setRefreshTick] = useState(0);
  const [selectedDeal, setSelectedDeal] = useState<number | null>(null);
  const [toast, setToast] = useState<ToastData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [syncedAt, setSyncedAt] = useState<Date | null>(null);
  const [initialLoading, setInitialLoading] = useState(true);
  const loadedRef = useRef(false);
  const toastTimer = useRef<number | null>(null);
  const requestRef = useRef(0);

  const pushToast = useCallback((message: string, tone: "success" | "error" | "info" = "success") => {
    setToast({ id: Date.now(), message, tone });
    if (toastTimer.current) window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToast(null), 5000);
  }, []);

  const fetchSummary = useCallback(async () => {
    const requestId = ++requestRef.current;
    try {
      const [healthResponse, overviewResponse] = await Promise.all([api.health(), api.overview()]);
      if (requestId !== requestRef.current) return;
      setHealth(healthResponse);
      setOverview(overviewResponse);
      setError(null);
      setSyncedAt(new Date());
    } catch (err) {
      if (requestId !== requestRef.current) return;
      setError(err instanceof Error ? err.message : "Could not reach the local scanner");
    } finally {
      if (requestId === requestRef.current && !loadedRef.current) {
        loadedRef.current = true;
        setInitialLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    void fetchSummary();
    const interval = window.setInterval(() => {
      if (!document.hidden) void fetchSummary();
    }, 30_000);
    const onVisible = () => {
      if (!document.hidden) void fetchSummary();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.clearInterval(interval);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [fetchSummary]);

  useEffect(() => {
    const onHash = () => setTab(tabFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const navigate = useCallback((next: string) => {
    const target = TABS.includes(next as Tab) ? (next as Tab) : "deals";
    setTab(target);
    if (window.location.hash !== `#${target}`) window.location.hash = target;
    window.scrollTo({ top: 0, behavior: "smooth" });
  }, []);

  const handleRefresh = useCallback(async () => {
    setRefreshing(true);
    setRefreshTick((value) => value + 1);
    await fetchSummary();
    window.setTimeout(() => setRefreshing(false), 650);
  }, [fetchSummary]);

  const handleScanNow = useCallback(async () => {
    if (scanning) return;
    setScanning(true);
    try {
      const response = await api.scan();
      pushToast(response.message, response.accepted.length ? "success" : "info");
      window.setTimeout(() => {
        void fetchSummary();
        setRefreshTick((value) => value + 1);
      }, 2400);
    } catch (err) {
      pushToast(err instanceof Error ? err.message : "Could not start a scan", "error");
    } finally {
      window.setTimeout(() => setScanning(false), 1600);
    }
  }, [fetchSummary, pushToast, scanning]);

  const handleDrawerChanged = useCallback(() => {
    setRefreshTick((value) => value + 1);
    void fetchSummary();
  }, [fetchSummary]);

  const meta = TAB_META[tab];
  const sources = health?.scanner.sources ?? [];
  const scanSources = sources.filter((source) => source.marketplace !== "facebook");

  if (initialLoading) {
    return (
      <div className="min-h-screen bg-ink-950">
        <div className="mx-auto max-w-[1440px] px-5 py-8">
          <div className="mb-8 flex items-center justify-between">
            <Logo />
            <div className="skeleton h-8 w-28" />
          </div>
          <div className="skeleton mb-5 h-60 rounded-2xl" />
          <div className="mb-5 grid grid-cols-2 gap-3 lg:grid-cols-4">
            {[0, 1, 2, 3].map((item) => (
              <div key={item} className="skeleton h-24 rounded-2xl" />
            ))}
          </div>
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {[0, 1, 2, 3, 4, 5].map((item) => (
              <div key={item} className="skeleton h-80 rounded-2xl" />
            ))}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen">
      <Sidebar
        tab={tab}
        onNavigate={navigate}
        dealCount={overview?.deals ?? 0}
        sources={scanSources}
        ebayConfigured={health?.ebay_configured ?? false}
        scannerRunning={health?.scanner.scanner_running ?? false}
      />

      <div className="lg:pl-[248px]">
        <header className="sticky top-0 z-30 border-b border-ink-700/70 bg-ink-950/80 backdrop-blur-xl">
          <div className="mx-auto flex h-16 max-w-[1500px] items-center justify-between gap-3 px-4 lg:px-8">
            <div className="flex min-w-0 items-center gap-3">
              <div className="lg:hidden">
                <Logo compact />
              </div>
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <h1 className="truncate text-sm font-semibold text-mist-100">{meta.title}</h1>
                  <span className="hidden items-center gap-1.5 rounded-full border border-lime-400/20 bg-lime-400/5 px-2 py-0.5 text-[9px] font-medium uppercase tracking-wider text-lime-300 sm:inline-flex">
                    <span className="h-1 w-1 animate-pulse-soft rounded-full bg-lime-400" /> Live
                  </span>
                </div>
                <p className="truncate text-[10px] text-mist-500">{meta.subtitle}</p>
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-1.5">
              <span className="mr-1 hidden text-[10px] text-mist-500 md:block">
                Synced {syncedAt ? clockTime(syncedAt.toISOString()) : "—"}
              </span>
              <button
                className="icon-btn"
                onClick={() => void handleRefresh()}
                title="Refresh dashboard"
                aria-label="Refresh dashboard"
              >
                <RefreshCw className={cn("h-4 w-4", refreshing && "animate-spin")} />
              </button>
              <button className="btn-primary !px-3 !py-2 !text-xs" onClick={() => void handleScanNow()} disabled={scanning}>
                <Radar className={cn("h-3.5 w-3.5", scanning && "animate-spin")} />
                <span className="hidden sm:inline">{scanning ? "Scanning…" : "Scan now"}</span>
                <span className="sm:hidden">Scan</span>
              </button>
            </div>
          </div>
        </header>

        <main className="mx-auto max-w-[1500px] px-4 pb-24 pt-5 lg:px-8 lg:pb-10">
          {error && (
            <div className="mb-4 flex flex-wrap items-center gap-2 rounded-xl border border-rose/25 bg-rose/[0.04] px-4 py-3 text-xs text-mist-300">
              <CircleAlert className="h-4 w-4 text-rose" />
              <span>{error}. Showing the last known state.</span>
              <button className="font-medium text-rose hover:underline" onClick={() => void fetchSummary()}>
                Retry
              </button>
            </div>
          )}

          {tab === "deals" && (
            <DealsPage
              overview={overview}
              health={health}
              refreshTick={refreshTick}
              onSelectDeal={setSelectedDeal}
              onToast={pushToast}
              onRefreshSummary={() => void fetchSummary()}
              onScanNow={() => void handleScanNow()}
              onNavigate={navigate}
            />
          )}
          {tab === "saved" && (
            <SavedPage
              refreshTick={refreshTick}
              onSelectDeal={setSelectedDeal}
              onToast={pushToast}
              onRefreshSummary={() => void fetchSummary()}
              onNavigate={navigate}
            />
          )}
          {tab === "flips" && (
            <FlipsPage
              refreshTick={refreshTick}
              onToast={pushToast}
              onRefreshSummary={() => {
                void fetchSummary();
                setRefreshTick((value) => value + 1);
              }}
            />
          )}
          {tab === "evidence" && (
            <EvidencePage
              refreshTick={refreshTick}
              onToast={pushToast}
              onRefreshSummary={() => {
                void fetchSummary();
                setRefreshTick((value) => value + 1);
              }}
            />
          )}
          {tab === "scanners" && (
            <ScannersPage
              health={health}
              refreshTick={refreshTick}
              onToast={pushToast}
              onRefresh={() => {
                void fetchSummary();
                setRefreshTick((value) => value + 1);
              }}
            />
          )}
          {tab === "settings" && (
            <SettingsPage
              onToast={pushToast}
              onRefreshSummary={() => {
                void fetchSummary();
                setRefreshTick((value) => value + 1);
              }}
            />
          )}
        </main>
      </div>

      <MobileNav tab={tab} onNavigate={navigate} dealCount={overview?.deals ?? 0} />

      {selectedDeal !== null && (
        <DealDrawer
          dealId={selectedDeal}
          onClose={() => setSelectedDeal(null)}
          onChanged={handleDrawerChanged}
          onToast={pushToast}
        />
      )}

      {toast && (
        <div className="pointer-events-none fixed bottom-24 right-4 z-[60] lg:bottom-6 lg:right-6">
          <Toast toast={toast} onClose={() => setToast(null)} />
        </div>
      )}
    </div>
  );
}

function Sidebar({
  tab,
  onNavigate,
  dealCount,
  sources,
  ebayConfigured,
  scannerRunning,
}: {
  tab: Tab;
  onNavigate: (tab: string) => void;
  dealCount: number;
  sources: NonNullable<HealthResponse["scanner"]["sources"]>;
  ebayConfigured: boolean;
  scannerRunning: boolean;
}) {
  return (
    <aside className="fixed inset-y-0 left-0 z-40 hidden w-[248px] flex-col border-r border-ink-700/80 bg-ink-950/70 backdrop-blur-xl lg:flex">
      <div className="flex h-16 items-center border-b border-ink-700/60 px-5">
        <button onClick={() => onNavigate("deals")} aria-label="FlipIntel home">
          <Logo />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-3 py-5">
        <p className="eyebrow px-2.5">Workspace</p>
        <nav className="mt-2 space-y-0.5">
          {TABS.map((key) => {
            const item = TAB_META[key];
            const Icon = item.icon;
            const active = tab === key;
            return (
              <button
                key={key}
                onClick={() => onNavigate(key)}
                className={cn(
                  "group relative flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2.5 text-[12.5px] font-medium transition",
                  active ? "bg-ink-800 text-mist-100" : "text-mist-400 hover:bg-ink-850 hover:text-mist-200",
                )}
              >
                {active && <span className="absolute left-0 top-1/2 h-5 w-[2px] -translate-y-1/2 rounded-full bg-lime-400" />}
                <Icon className={cn("h-4 w-4", active ? "text-lime-300" : "text-mist-500 group-hover:text-mist-300")} />
                {item.label}
                {key === "deals" && dealCount > 0 && (
                  <span className="ml-auto rounded-full border border-lime-400/20 bg-lime-400/10 px-1.5 py-0.5 font-mono text-[9px] text-lime-300">
                    {dealCount}
                  </span>
                )}
              </button>
            );
          })}
        </nav>

        <div className="mt-7 border-t border-ink-700/60 pt-4">
          <div className="flex items-center justify-between px-2.5">
            <p className="eyebrow">Sources</p>
            <span className={cn("h-1.5 w-1.5 rounded-full", scannerRunning ? "animate-pulse bg-lime-400" : "bg-mist-600")} />
          </div>
          <div className="mt-2 space-y-1">
            {sources.map((source) => (
              <div key={source.marketplace} className="flex items-center justify-between gap-2 rounded-md px-2.5 py-1.5">
                <span className="flex items-center gap-2 text-[11px] text-mist-400">
                  <SourceDot state={source.state} available={source.available} />
                  {source.label}
                </span>
                <span className="text-[9px] text-mist-600">
                  {source.marketplace === "ebay" && !ebayConfigured
                    ? "API key"
                    : source.last_success_at
                      ? timeAgo(source.last_success_at)
                      : "waiting"}
                </span>
              </div>
            ))}
            <div className="flex items-center justify-between gap-2 rounded-md px-2.5 py-1.5">
              <span className="flex items-center gap-2 text-[11px] text-mist-500">
                <span className="h-1.5 w-1.5 rounded-full bg-mist-600" /> Facebook
              </span>
              <span className="text-[9px] text-mist-600">restricted</span>
            </div>
          </div>
          <button
            onClick={() => onNavigate("scanners")}
            className="mt-2 flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-[10px] text-mist-500 transition hover:text-mist-300"
          >
            <Activity className="h-3 w-3" /> View scanner health
          </button>
        </div>

        <div className="mt-6 rounded-xl border border-lime-400/15 bg-gradient-to-br from-lime-400/[0.07] to-transparent p-3">
          <p className="flex items-center gap-1.5 text-[10px] font-medium text-lime-300">
            <Radar className="h-3.5 w-3.5" /> Automatic discovery
          </p>
          <p className="mt-1.5 text-[10px] leading-relaxed text-mist-400">
            No saved searches. The scanner rotates broad categories and automatically enriches models with
            thin comparables.
          </p>
        </div>
      </div>

      <div className="border-t border-ink-700/60 p-4">
        <div className="flex items-center justify-between text-[9px] text-mist-600">
          <span>FlipIntel v1.0</span>
          <div className="flex items-center gap-2">
            <a href="/docs" target="_blank" rel="noopener noreferrer" className="hover:text-mist-400">
              API
            </a>
            <span>·</span>
            <span>Local-first</span>
          </div>
        </div>
      </div>
    </aside>
  );
}

function SourceDot({ state, available }: { state: string; available: boolean }) {
  if (!available) return <span className="h-1.5 w-1.5 rounded-full bg-mist-600" />;
  const colour =
    state === "healthy"
      ? "bg-lime-400"
      : state === "scanning"
        ? "bg-sky-400 animate-pulse"
        : state === "degraded"
          ? "bg-ember"
          : ["blocked", "error"].includes(state)
            ? "bg-rose"
            : "bg-mist-500";
  return <span className={cn("h-1.5 w-1.5 rounded-full", colour)} />;
}

function MobileNav({ tab, onNavigate, dealCount }: { tab: Tab; onNavigate: (tab: string) => void; dealCount: number }) {
  return (
    <nav className="fixed inset-x-0 bottom-0 z-40 border-t border-ink-700/80 bg-ink-950/90 pb-[env(safe-area-inset-bottom)] backdrop-blur-xl lg:hidden">
      <div className="mx-auto flex max-w-md items-center justify-around px-2 py-1.5">
        {TABS.map((key) => {
          const item = TAB_META[key];
          const Icon = item.icon;
          const active = tab === key;
          return (
            <button
              key={key}
              onClick={() => onNavigate(key)}
              className={cn(
                "relative flex min-w-[64px] flex-col items-center gap-1 rounded-lg px-2 py-1.5 text-[9px] font-medium transition",
                active ? "text-lime-300" : "text-mist-500",
              )}
            >
              <Icon className="h-5 w-5" />
              {key === "deals" && dealCount > 0 && (
                <span className="absolute right-2.5 top-0.5 h-1.5 w-1.5 rounded-full bg-lime-400" />
              )}
              {item.label.split(" ")[0]}
            </button>
          );
        })}
      </div>
    </nav>
  );
}
