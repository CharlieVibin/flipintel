import { useMemo, useState } from "react";
import { Archive, Bookmark, RotateCcw, X } from "lucide-react";
import { api } from "../lib/api";
import { useDeals } from "../hooks/useDeals";
import { DealCard } from "../components/DealCard";
import { EmptyRadar, SkeletonCard, cn } from "../components/ui";
import type { Deal } from "../lib/types";

interface Props {
  refreshTick: number;
  onSelectDeal: (id: number) => void;
  onToast: (message: string, tone?: "success" | "error" | "info") => void;
  onRefreshSummary: () => void;
  onNavigate: (tab: string) => void;
}

export function SavedPage({ refreshTick, onSelectDeal, onToast, onRefreshSummary, onNavigate }: Props) {
  const [mode, setMode] = useState<"saved" | "archived">("saved");
  const filters = useMemo(
    () => ({ saved_only: true, archived: mode === "archived", sort: "newest" as const, limit: 36 }),
    [mode],
  );
  const { deals, total, loading, error, reload, removeDeal, updateDeal } = useDeals(filters, refreshTick);

  async function handleSave(deal: Deal) {
    try {
      await api.unsave(deal.id);
      removeDeal(deal.id);
      onToast("Removed from saved");
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

  async function handleRestore(deal: Deal) {
    try {
      await api.restore(deal.id);
      removeDeal(deal.id);
      onToast("Deal restored to the feed");
      onRefreshSummary();
    } catch {
      onToast("Could not restore this deal", "error");
    }
  }

  return (
    <div>
      <div className="mb-5 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="eyebrow mb-1.5">Your shortlist</p>
          <h1 className="text-2xl font-semibold tracking-tight text-mist-100">Deals worth a second look</h1>
          <p className="mt-1 max-w-xl text-sm text-mist-400">
            Saved opportunities stay here while the scanner keeps looking. Dismissed deals are removed from the main feed.
          </p>
        </div>
        <div className="flex items-center gap-1 rounded-xl border border-ink-600 bg-ink-850 p-1">
          <button
            onClick={() => setMode("saved")}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-xs font-medium transition",
              mode === "saved" ? "bg-ink-700 text-mist-100" : "text-mist-400 hover:text-mist-200",
            )}
          >
            <Bookmark className="h-3.5 w-3.5" /> Saved
          </button>
          <button
            onClick={() => setMode("archived")}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-xs font-medium transition",
              mode === "archived" ? "bg-ink-700 text-mist-100" : "text-mist-400 hover:text-mist-200",
            )}
          >
            <Archive className="h-3.5 w-3.5" /> Dismissed
          </button>
        </div>
      </div>

      <div className="mb-4 flex items-center justify-between text-[11px] text-mist-500">
        <span>{loading ? "Loading…" : `${total} ${mode === "saved" ? "saved" : "dismissed"} deal${total === 1 ? "" : "s"}`}</span>
        {mode === "saved" && (
          <button onClick={() => onNavigate("deals")} className="inline-flex items-center gap-1.5 hover:text-mist-300">
            Back to live feed <RotateCcw className="h-3 w-3" />
          </button>
        )}
      </div>

      {error ? (
        <div className="panel p-8 text-center text-sm text-mist-300">
          {error}
          <button className="btn-secondary ml-3 !py-1.5" onClick={reload}>Retry</button>
        </div>
      ) : loading ? (
        <div className="grid gap-4 md:grid-cols-2 2xl:grid-cols-3">
          {Array.from({ length: 3 }).map((_, index) => (
            <SkeletonCard key={index} />
          ))}
        </div>
      ) : deals.length === 0 ? (
        <EmptyRadar
          title={mode === "saved" ? "Nothing saved yet" : "No dismissed deals"}
          description={
            mode === "saved"
              ? "Tap the bookmark icon on any deal to keep it here. Saved deals stay on your shortlist even as the feed refreshes."
              : "Dismissed deals disappear from the live feed. If you change your mind, dismissed deals can be restored from here."
          }
          action={
            <button className="btn-secondary mt-1" onClick={() => onNavigate("deals")}>
              Browse live deals
            </button>
          }
        />
      ) : (
        <div className="grid gap-4 md:grid-cols-2 2xl:grid-cols-3">
          {deals.map((deal) => (
            <DealCard
              key={deal.id}
              deal={deal}
              onOpen={onSelectDeal}
              onSave={mode === "saved" ? handleSave : undefined}
              onDismiss={mode === "saved" ? handleDismiss : undefined}
              onRestore={mode === "archived" ? handleRestore : undefined}
              archived={mode === "archived"}
            />
          ))}
        </div>
      )}

      {mode === "archived" && deals.length > 0 && (
        <p className="mt-4 flex items-center gap-2 text-[11px] text-mist-500">
          <X className="h-3.5 w-3.5" /> Restoring a deal puts it back on the live feed and recomputes nothing —
          the valuation is unchanged.
        </p>
      )}
    </div>
  );
}
