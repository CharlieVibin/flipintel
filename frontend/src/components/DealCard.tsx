import { ArrowUpRight, Bookmark, BookmarkCheck, CircleAlert, Clock3, MapPin, RotateCcw, TrendingUp, X } from "lucide-react";
import type { Deal } from "../lib/types";
import { cn } from "./ui";
import { EvidenceMeter, MarketBadge, ProductImage, ScoreRing } from "./ui";
import { CATEGORY_LABELS, conditionLabel, daysAgo, methodLabel, money, pct } from "../lib/format";

interface Props {
  deal: Deal;
  onOpen: (id: number) => void;
  onSave?: (deal: Deal) => void;
  onDismiss?: (deal: Deal) => void;
  onRestore?: (deal: Deal) => void;
  archived?: boolean;
}

export function DealCard({ deal, onOpen, onSave, onDismiss, onRestore, archived = false }: Props) {
  const profit = deal.net_profit ?? 0;
  const review = deal.needs_review;
  const positive = profit > 0 && !review;
  const soldEvidence = ["sold_comps", "mixed_comps"].includes(deal.valuation_method ?? "");
  const evidenceLabel = soldEvidence
    ? "Sold evidence"
    : deal.valuation_method === "live_asking_comps"
      ? "Asking only"
      : deal.valuation_method === "component_model"
        ? "Component model"
        : methodLabel(deal.valuation_method);
  return (
    <article
      className={cn(
        "group panel relative flex cursor-pointer flex-col overflow-hidden transition duration-200",
        "hover:-translate-y-0.5 hover:border-ink-500 hover:shadow-glow",
      )}
      onClick={() => onOpen(deal.id)}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onOpen(deal.id);
        }
      }}
      tabIndex={0}
      role="button"
      aria-label={`${deal.title}, ${money(deal.price)}, ${review ? "needs manual review" : `estimated profit ${money(profit)}`}`}
    >
      <div className="relative aspect-[16/9] overflow-hidden">
        <ProductImage
          src={deal.image_url}
          alt={deal.title}
          category={deal.category}
          className="transition duration-700 group-hover:scale-[1.03]"
        />
        <div className="pointer-events-none absolute inset-0 bg-gradient-to-t from-ink-950/90 via-ink-950/10 to-ink-950/35" />
        <div className="absolute inset-x-3 top-3 flex items-start justify-between gap-2">
          <div className="flex flex-wrap items-center gap-1.5">
            <MarketBadge marketplace={deal.marketplace} label={deal.marketplace_label} />
            {deal.price_dropped_at && (
              <span className="chip border-sky-400/25 bg-sky-400/15 text-sky-200 backdrop-blur-md">
                <TrendingUp className="h-3 w-3 rotate-180" /> Price drop
              </span>
            )}
            {deal.saved && (
              <span className="chip border-lime-400/25 bg-ink-950/70 text-lime-300 backdrop-blur-md">
                <BookmarkCheck className="h-3 w-3" /> Saved
              </span>
            )}
            {deal.needs_review && (
              <span className="chip border-ember/30 bg-ink-950/80 text-ember backdrop-blur-md">
                <CircleAlert className="h-3 w-3" /> Review
              </span>
            )}
            {!deal.needs_review && deal.risk_level !== "low" && (
              <span
                className={cn(
                  "chip border-ink-600 bg-ink-950/80 backdrop-blur-md",
                  deal.risk_level === "high" ? "text-rose" : "text-amber-300",
                )}
                title={deal.risk_reasons?.[0] ?? "Marketplace risk assessment"}
              >
                Risk {Math.round(deal.risk_score)}
              </span>
            )}
            {deal.duplicate_of && (
              <span className="chip border-ink-500 bg-ink-950/80 text-mist-300 backdrop-blur-md">
                Possible duplicate
              </span>
            )}
          </div>
          <div className="rounded-full border border-ink-600/70 bg-ink-950/75 px-1.5 py-1 backdrop-blur-md">
            <ScoreRing score={deal.score} size={38} />
          </div>
        </div>
        <div className="absolute inset-x-4 bottom-3 flex items-end justify-between gap-3">
          <div className="min-w-0">
            <p className="font-mono text-[9px] uppercase tracking-[0.16em] text-mist-300/80">
              {CATEGORY_LABELS[deal.category ?? "other"]}
              {deal.product_name ? ` · ${deal.product_name}` : ""}
            </p>
            <h3 className="mt-0.5 line-clamp-2 text-[15px] font-semibold leading-snug text-mist-100">
              {deal.title}
            </h3>
          </div>
          <div
            className={cn(
              "shrink-0 rounded-xl border px-2.5 py-1.5 text-right backdrop-blur-md",
              review
                ? "border-ember/30 bg-ember/10 text-ember"
                : positive
                  ? "border-lime-400/30 bg-lime-400/10 text-lime-300"
                  : "border-ink-500 bg-ink-950/70 text-mist-300",
            )}
          >
            <p className="numeric text-sm font-semibold leading-none">
              {review ? "Review" : `${positive ? "+" : ""}${money(profit)}`}
            </p>
            <p className="mt-1 font-mono text-[9px] uppercase tracking-wider opacity-80">
              {review ? "Unverified" : `${pct(deal.roi, 0)} ROI`}
            </p>
          </div>
        </div>
      </div>

      <div className="flex flex-1 flex-col p-4">
        <div className="grid grid-cols-3 gap-2">
          <div>
            <p className="eyebrow">Asking</p>
            <p className="numeric mt-1 text-base font-semibold text-mist-100">{money(deal.price)}</p>
          </div>
          <div>
            <p className="eyebrow">Resale est.</p>
            <p className="numeric mt-1 text-base font-semibold text-mist-200">{money(deal.expected_resale)}</p>
          </div>
          <div>
            <p className="eyebrow">Deal</p>
            <p
              className={cn(
                "numeric mt-1 text-base font-semibold",
                (deal.discount_pct ?? 0) > 0 ? "text-lime-300" : "text-mist-300",
              )}
            >
              {deal.discount_pct ? `−${pct(deal.discount_pct)}` : "—"}
            </p>
          </div>
        </div>

        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <span
            className={cn(
              "chip border-ink-600 bg-ink-850/80 text-[9px]",
              soldEvidence ? "text-lime-300" : "text-ember",
            )}
            title={soldEvidence ? "At least one user-supplied completed sale supports this valuation" : "This valuation relies on live asking prices"}
          >
            {evidenceLabel}
          </span>
          {deal.net_profit_conservative != null && (
            <span className="chip border-sky-400/20 bg-sky-400/[0.06] text-sky-300" title="Net profit if the lower end of the supported resale range is achieved">
              Conservative {deal.net_profit_conservative >= 0 ? "+" : ""}{money(deal.net_profit_conservative)}
            </span>
          )}
        </div>

        <div className="mt-3 flex items-center justify-between gap-4">
          <div className="flex min-w-0 items-center gap-2 text-[11px] text-mist-400">
            <span className="inline-flex items-center gap-1">
              <Clock3 className="h-3 w-3" /> {daysAgo(deal.age_hours)}
            </span>
            <span className="h-1 w-1 rounded-full bg-ink-500" />
            <span className="truncate">{conditionLabel(deal.condition)}</span>
            <span className="h-1 w-1 shrink-0 rounded-full bg-ink-500" />
            <span className="shrink-0 text-mist-500">
              {deal.shipping > 0
                ? `${money(deal.shipping)} postage`
                : deal.marketplace === "vinted"
                  ? "Vinted delivery"
                  : "Delivery not listed"}
            </span>
            {deal.location && (
              <>
                <span className="h-1 w-1 shrink-0 rounded-full bg-ink-500" />
                <span className="inline-flex min-w-0 items-center gap-1">
                  <MapPin className="h-3 w-3 shrink-0" />
                  <span className="truncate">{deal.location.split(",")[0]}</span>
                </span>
              </>
            )}
          </div>
          <span className="numeric shrink-0 text-[10px] text-mist-500">
            {deal.sample_size} comps · {methodLabel(deal.valuation_method)}
            {deal.identity_level && deal.identity_level !== "strong" ? ` · identity ${deal.identity_level}` : ""}
          </span>
        </div>

        <div className="mt-3">
          <EvidenceMeter level={deal.evidence_level} compact />
        </div>

        <div className="mt-3 flex items-center justify-between border-t border-ink-700/70 pt-3">
          <div className="flex items-center gap-1">
            {onSave && (
              <button
                onClick={(event) => {
                  event.stopPropagation();
                  onSave(deal);
                }}
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-md px-2 py-1.5 text-[11px] font-medium transition",
                  deal.saved ? "text-lime-300 hover:bg-lime-400/10" : "text-mist-400 hover:bg-ink-700/60 hover:text-mist-100",
                )}
                aria-label={deal.saved ? "Remove from saved" : "Save deal"}
              >
                {deal.saved ? <BookmarkCheck className="h-3.5 w-3.5" /> : <Bookmark className="h-3.5 w-3.5" />}
                {deal.saved ? "Saved" : "Save"}
              </button>
            )}
            {archived && onRestore ? (
              <button
                onClick={(event) => {
                  event.stopPropagation();
                  onRestore(deal);
                }}
                className="inline-flex items-center gap-1.5 rounded-md px-2 py-1.5 text-[11px] font-medium text-mist-400 transition hover:bg-ink-700/60 hover:text-mist-100"
              >
                <RotateCcw className="h-3.5 w-3.5" /> Restore
              </button>
            ) : (
              onDismiss && (
                <button
                  onClick={(event) => {
                    event.stopPropagation();
                    onDismiss(deal);
                  }}
                  className="inline-flex items-center gap-1.5 rounded-md px-2 py-1.5 text-[11px] font-medium text-mist-400 transition hover:bg-ink-700/60 hover:text-rose"
                >
                  <X className="h-3.5 w-3.5" /> Dismiss
                </button>
              )
            )}
          </div>
          <a
            href={deal.url}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(event) => event.stopPropagation()}
            className="inline-flex items-center gap-1 rounded-md px-2 py-1.5 text-[11px] font-medium text-mist-300 transition hover:bg-ink-700/60 hover:text-mist-100"
          >
            View listing <ArrowUpRight className="h-3.5 w-3.5" />
          </a>
        </div>
      </div>
    </article>
  );
}
