import { useEffect, useState } from "react";
import {
  ArrowUpRight,
  Bookmark,
  BookmarkCheck,
  CheckCircle2,
  CircleAlert,
  ExternalLink,
  Info,
  ReceiptText,
  ShieldCheck,
  Sparkles,
  X,
} from "lucide-react";
import { api, ApiError } from "../lib/api";
import type { DealDetail } from "../lib/types";
import { CATEGORY_LABELS, conditionLabel, confidenceTone, methodLabel, money, pct, timeAgo } from "../lib/format";
import { cn, ConfidenceMeter, MarketBadge, ProductImage, ScoreRing } from "./ui";

interface Props {
  dealId: number;
  onClose: () => void;
  onChanged: () => void;
  onToast: (message: string, tone?: "success" | "error" | "info") => void;
}

export function DealDrawer({ dealId, onClose, onChanged, onToast }: Props) {
  const [deal, setDeal] = useState<DealDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let active = true;
    setDeal(null);
    setError(null);
    api
      .deal(dealId)
      .then((data) => {
        if (active) setDeal(data);
      })
      .catch((err: unknown) => {
        if (active) setError(err instanceof ApiError ? err.message : "Could not load this deal");
      });
    return () => {
      active = false;
    };
  }, [dealId]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = previous;
    };
  }, [onClose]);

  async function toggleSave() {
    if (!deal || busy) return;
    setBusy(true);
    try {
      if (deal.saved) {
        await api.unsave(deal.id);
        setDeal({ ...deal, saved: false });
        onToast("Removed from saved deals");
      } else {
        await api.save(deal.id);
        setDeal({ ...deal, saved: true, dismissed: false });
        onToast("Deal saved to your shortlist");
      }
      onChanged();
    } catch {
      onToast("Could not update saved deals", "error");
    } finally {
      setBusy(false);
    }
  }

  async function dismiss() {
    if (!deal || busy) return;
    setBusy(true);
    try {
      await api.dismiss(deal.id);
      onToast("Deal dismissed");
      onChanged();
      onClose();
    } catch {
      onToast("Could not dismiss this deal", "error");
    } finally {
      setBusy(false);
    }
  }

  const tone = confidenceTone(deal?.confidence ?? 0);
  const breakdown = deal?.breakdown;
  const purchase = breakdown?.purchase;
  const resale = breakdown?.resale;
  const riskFlags = (deal?.flags ?? []).filter((flag) =>
    ["condition_caution", "faulty_or_parts", "accessory_or_box", "bundle", "model_unclear"].includes(flag),
  );

  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true" aria-label="Deal detail">
      <button
        className="absolute inset-0 bg-ink-950/80 backdrop-blur-sm"
        aria-label="Close deal detail"
        onClick={onClose}
      />
      <div className="relative flex h-full w-full max-w-2xl animate-rise flex-col border-l border-ink-600 bg-ink-900 shadow-2xl">
        <div className="flex items-center justify-between border-b border-ink-700 px-5 py-3">
          <div className="flex items-center gap-2">
            <span className="eyebrow">Deal analysis</span>
            {deal && <span className="numeric text-[10px] text-mist-500">#{deal.id.toString().padStart(5, "0")}</span>}
          </div>
          <div className="flex items-center gap-2">
            {deal && (
              <a href={deal.url} target="_blank" rel="noopener noreferrer" className="btn-secondary !px-3 !py-1.5 !text-xs">
                Open listing <ArrowUpRight className="h-3.5 w-3.5" />
              </a>
            )}
            <button className="icon-btn" onClick={onClose} aria-label="Close">
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto">
          {error ? (
            <div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center">
              <CircleAlert className="h-8 w-8 text-rose" />
              <p className="text-sm text-mist-300">{error}</p>
              <button className="btn-secondary" onClick={onClose}>Close</button>
            </div>
          ) : !deal ? (
            <DrawerSkeleton />
          ) : (
            <div className="animate-rise">
              <div className="relative aspect-[16/8] overflow-hidden">
                <ProductImage src={deal.image_url} alt={deal.title} category={deal.category} />
                <div className="absolute inset-0 bg-gradient-to-t from-ink-900 via-ink-900/20 to-transparent" />
                <div className="absolute inset-x-5 bottom-4 flex items-end justify-between gap-3">
                  <div>
                    <div className="mb-2 flex flex-wrap items-center gap-2">
                      <MarketBadge marketplace={deal.marketplace} label={deal.marketplace_label} />
                      <span className="chip border-ink-600 bg-ink-950/70 text-mist-300 backdrop-blur">
                        {conditionLabel(deal.condition)}
                      </span>
                      {deal.location && (
                        <span className="chip border-ink-600 bg-ink-950/70 text-mist-400 backdrop-blur">
                          {deal.location}
                        </span>
                      )}
                    </div>
                    <h2 className="max-w-xl text-xl font-semibold leading-tight tracking-tight text-mist-100">
                      {deal.title}
                    </h2>
                  </div>
                  <div className="shrink-0 rounded-2xl border border-ink-600/80 bg-ink-950/80 p-2 backdrop-blur">
                    <ScoreRing score={deal.score} size={54} />
                  </div>
                </div>
              </div>

              <div className="space-y-6 px-5 py-5">
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                  <Metric label="Asking" value={money(deal.price)} />
                  <Metric label="Landing cost" value={money(deal.purchase_total)} />
                  <Metric label="Resale estimate" value={money(deal.expected_resale)} tone="accent" />
                  <Metric
                    label="Net profit"
                    value={`${(deal.net_profit ?? 0) > 0 ? "+" : ""}${money(deal.net_profit)}`}
                    sub={pct(deal.roi)} 
                    tone={(deal.net_profit ?? 0) > 0 ? "accent" : "muted"}
                  />
                </div>

                <div className="panel-soft p-4">
                  <div className="flex items-start justify-between gap-4">
                    <div>
                      <p className="eyebrow mb-1">Deal thesis</p>
                      <p className="text-sm text-mist-200">
                        {deal.product_name ?? CATEGORY_LABELS[deal.category ?? "other"]} · {deal.sample_size} comparable
                        {deal.sample_size === 1 ? "" : "s"} · {methodLabel(deal.valuation_method)}
                      </p>
                    </div>
                    <div className="w-28 shrink-0">
                      <ConfidenceMeter value={deal.confidence} />
                    </div>
                  </div>
                  <ul className="mt-3 space-y-2">
                    {deal.reasons.map((reason, index) => (
                      <li key={index} className="flex items-start gap-2 text-[13px] leading-snug text-mist-300">
                        <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-lime-400" />
                        {reason}
                      </li>
                    ))}
                  </ul>
                  <p className={cn("mt-3 text-[11px]", tone.text)}>
                    {tone.label} · estimate range {money(deal.resale_low)}–{money(deal.resale_high)}
                  </p>
                </div>

                {purchase && resale && (
                  <div>
                    <div className="mb-2 flex items-center justify-between">
                      <h3 className="flex items-center gap-2 text-sm font-semibold text-mist-100">
                        <ReceiptText className="h-4 w-4 text-mist-400" /> Cost & resale breakdown
                      </h3>
                      <span className="text-[10px] text-mist-500">Assumptions editable in Settings</span>
                    </div>
                    <div className="overflow-hidden rounded-xl border border-ink-600/70">
                      <table className="w-full text-[12.5px]">
                        <tbody className="divide-y divide-ink-700/60">
                          <Row label="Purchase price" value={money(purchase.price)} />
                          <Row label="Delivery / buyer fee" value={money(purchase.shipping + purchase.buyer_fee)} />
                          <Row label="Total cost" value={money(purchase.total)} emphasis />
                          <Row label={`Resale via ${resale.channel}`} value={money(resale.expected)} />
                          <Row label="Selling fees" value={`−${money(resale.fees)}`} />
                          <Row label="Outbound delivery + packaging" value={`−${money(resale.shipping + resale.packaging)}`} />
                          <Row
                            label="Estimated net profit"
                            value={`${(deal.net_profit ?? 0) > 0 ? "+" : ""}${money(deal.net_profit)}`}
                            emphasis
                            accent={(deal.net_profit ?? 0) > 0}
                          />
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}

                {riskFlags.length > 0 && (
                  <div className="rounded-xl border border-ember/20 bg-ember/5 p-3.5">
                    <p className="flex items-center gap-2 text-xs font-semibold text-ember">
                      <CircleAlert className="h-3.5 w-3.5" /> Check before buying
                    </p>
                    <p className="mt-1 text-[12px] leading-relaxed text-mist-400">
                      {riskFlags.includes("faulty_or_parts") && "This listing mentions damage or faults. "}
                      {riskFlags.includes("condition_caution") && "The description flags condition caveats. "}
                      {riskFlags.includes("bundle") && "Bundle listings are not valued as a single item. "}
                      {riskFlags.includes("model_unclear") && "The exact model may be ambiguous. "}
                      {riskFlags.includes("accessory_or_box") && "This may be an accessory or packaging rather than the item. "}
                      Confirm the exact model, included parts and condition with the seller.
                    </p>
                  </div>
                )}

                <div>
                  <div className="mb-2 flex items-center justify-between">
                    <h3 className="flex items-center gap-2 text-sm font-semibold text-mist-100">
                      <ShieldCheck className="h-4 w-4 text-mist-400" /> Comparable evidence
                    </h3>
                    <span className="chip border-ink-600 bg-ink-800 text-mist-400">
                      {breakdown?.sold_count ? `${breakdown.sold_count} sold` : "Asking prices only"}
                    </span>
                  </div>
                  <div className="space-y-1.5">
                    {(breakdown?.comps ?? []).slice(0, 8).map((comp, index) => (
                      <a
                        key={`${comp.url}-${index}`}
                        href={comp.url ?? "#"}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="group flex items-center justify-between gap-3 rounded-lg border border-ink-700/60 bg-ink-850/50 px-3 py-2 transition hover:border-ink-500 hover:bg-ink-800/70"
                      >
                        <div className="min-w-0">
                          <p className="truncate text-[12px] text-mist-200">{comp.title}</p>
                          <p className="mt-0.5 text-[10px] text-mist-500">
                            {comp.marketplace} · {conditionLabel(comp.condition)} ·{" "}
                            {comp.is_sold ? "completed sale" : "live ask"} · {timeAgo(comp.observed_at)}
                          </p>
                        </div>
                        <div className="shrink-0 text-right">
                          <p className="numeric text-[12.5px] font-medium text-mist-100">{money(comp.price)}</p>
                          <p className="numeric text-[9px] text-mist-500">adj. {money(comp.adjusted_price)}</p>
                        </div>
                        <ExternalLink className="h-3 w-3 shrink-0 text-mist-600 transition group-hover:text-mist-300" />
                      </a>
                    ))}
                    {(breakdown?.comps ?? []).length === 0 && (
                      <div className="rounded-lg border border-ink-700 bg-ink-850/50 p-4 text-xs text-mist-400">
                        No comparables were available for this model. This listing is not recommended.
                      </div>
                    )}
                  </div>
                  {breakdown?.valuation_notes && breakdown.valuation_notes.length > 0 && (
                    <ul className="mt-3 space-y-1.5">
                      {breakdown.valuation_notes.map((note, index) => (
                        <li key={index} className="flex items-start gap-2 text-[11px] leading-relaxed text-mist-500">
                          <Info className="mt-0.5 h-3 w-3 shrink-0" /> {note}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>

                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="panel-soft p-3.5">
                    <p className="eyebrow mb-1.5">Listed</p>
                    <p className="text-sm text-mist-200">{timeAgo(deal.first_seen)}</p>
                    <p className="mt-1 text-[11px] text-mist-500">
                      Last seen {timeAgo(deal.last_seen)} · {deal.seller ? `Seller ${deal.seller}` : "Seller not shown"}
                    </p>
                  </div>
                  <div className="panel-soft p-3.5">
                    <p className="eyebrow mb-1.5">Model match</p>
                    <p className="text-sm text-mist-200">
                      {Math.round(deal.identification_confidence * 100)}% identification confidence
                    </p>
                    <p className="mt-1 text-[11px] text-mist-500">{deal.product_key ?? "Model not resolved"}</p>
                    {deal.attrs && Object.keys(deal.attrs).length > 0 && (
                      <p className="mt-1 truncate text-[10px] text-mist-600">
                        {Object.entries(deal.attrs)
                          .filter(([, value]) => value !== null && value !== undefined)
                          .slice(0, 4)
                          .map(([key, value]) => `${key}: ${String(value)}`)
                          .join(" · ")}
                      </p>
                    )}
                  </div>
                </div>

                {deal.description && (
                  <div>
                    <p className="eyebrow mb-1.5">Listing description</p>
                    <p className="max-h-32 overflow-y-auto whitespace-pre-line rounded-lg border border-ink-700/70 bg-ink-850/40 p-3 text-[12px] leading-relaxed text-mist-400">
                      {deal.description}
                    </p>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>

        {deal && (
          <div className="flex items-center gap-2 border-t border-ink-700 bg-ink-900/95 px-5 py-3 backdrop-blur">
            <button onClick={toggleSave} className="btn-secondary flex-1 !py-2.5" disabled={busy}>
              {deal.saved ? <BookmarkCheck className="h-4 w-4 text-lime-400" /> : <Bookmark className="h-4 w-4" />}
              {deal.saved ? "Saved" : "Save deal"}
            </button>
            <button
              onClick={dismiss}
              className="btn-secondary !px-3 !py-2.5 text-mist-400 hover:text-rose"
              disabled={busy}
              aria-label="Dismiss deal"
            >
              <X className="h-4 w-4" />
            </button>
            <a href={deal.url} target="_blank" rel="noopener noreferrer" className="btn-primary flex-1 !py-2.5">
              <Sparkles className="h-4 w-4" /> View listing
            </a>
          </div>
        )}
      </div>
    </div>
  );
}

function Metric({
  label,
  value,
  sub,
  tone = "default",
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: "default" | "accent" | "muted";
}) {
  return (
    <div className="panel-soft px-3 py-2.5">
      <p className="eyebrow">{label}</p>
      <p
        className={cn(
          "numeric mt-1 text-base font-semibold",
          tone === "accent" ? "text-lime-300" : tone === "muted" ? "text-mist-300" : "text-mist-100",
        )}
      >
        {value}
      </p>
      {sub && <p className="numeric mt-0.5 text-[10px] text-mist-500">{sub}</p>}
    </div>
  );
}

function Row({
  label,
  value,
  emphasis = false,
  accent = false,
}: {
  label: string;
  value: string;
  emphasis?: boolean;
  accent?: boolean;
}) {
  return (
    <tr className={cn(emphasis && "bg-ink-800/60")}>
      <td className={cn("px-3 py-2 text-mist-400", emphasis && "font-medium text-mist-200")}>{label}</td>
      <td
        className={cn(
          "numeric px-3 py-2 text-right",
          accent ? "font-semibold text-lime-300" : emphasis ? "font-semibold text-mist-100" : "text-mist-300",
        )}
      >
        {value}
      </td>
    </tr>
  );
}

function DrawerSkeleton() {
  return (
    <div className="space-y-4 p-5">
      <div className="skeleton aspect-[16/8] w-full rounded-xl" />
      <div className="grid grid-cols-4 gap-3">
        {[0, 1, 2, 3].map((item) => (
          <div key={item} className="skeleton h-16 rounded-xl" />
        ))}
      </div>
      <div className="skeleton h-32 rounded-xl" />
      <div className="skeleton h-48 rounded-xl" />
    </div>
  );
}
