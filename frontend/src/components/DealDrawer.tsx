import { useEffect, useState } from "react";
import {
  ArrowUpRight,
  Bookmark,
  BookmarkCheck,
  Check,
  CheckCircle2,
  CircleAlert,
  Database,
  ExternalLink,
  Info,
  Loader2,
  Pencil,
  Plus,
  ReceiptText,
  RotateCcw,
  ShieldCheck,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import { api, ApiError } from "../lib/api";
import type { CompsResponse, DealDetail } from "../lib/types";
import { CATEGORY_LABELS, conditionLabel, confidenceTone, methodLabel, money, pct, timeAgo, timeUntil } from "../lib/format";
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
  const [evidence, setEvidence] = useState<CompsResponse | null>(null);
  const [evidenceError, setEvidenceError] = useState<string | null>(null);
  const [compBusy, setCompBusy] = useState(false);
  const [reviewBusy, setReviewBusy] = useState(false);
  const [showCorrection, setShowCorrection] = useState(false);
  const [correction, setCorrection] = useState({ product_key: "", product_name: "", kind: "gpu" });
  const [compForm, setCompForm] = useState({
    title: "",
    price: "",
    condition: "good",
    postage: "0",
    url: "",
    notes: "",
  });

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
    if (!deal?.product_key) {
      setEvidence(null);
      return;
    }
    let active = true;
    api
      .comps(deal.product_key, deal.kind)
      .then((data) => {
        if (active) {
          setEvidence(data);
          setEvidenceError(null);
        }
      })
      .catch((err: unknown) => {
        if (active) setEvidenceError(err instanceof Error ? err.message : "Could not load evidence");
      });
    return () => {
      active = false;
    };
  }, [deal?.product_key, deal?.kind]);

  useEffect(() => {
    if (deal) {
      setCorrection({
        product_key: deal.product_key ?? "",
        product_name: deal.product_name ?? "",
        kind: deal.kind ?? "gpu",
      });
    }
  }, [deal?.id, deal?.product_key, deal?.product_name, deal?.kind]);

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

  async function addComp() {
    if (!deal?.product_key || compBusy) return;
    const price = Number(compForm.price);
    if (!Number.isFinite(price) || price <= 0) {
      onToast("Enter a valid sold price", "error");
      return;
    }
    setCompBusy(true);
    try {
      await api.addComp({
        product_key: deal.product_key,
        kind: deal.kind,
        title: compForm.title.trim() || `${deal.product_name ?? deal.product_key} completed sale`,
        price,
        condition: compForm.condition,
        postage: Number(compForm.postage) || 0,
        url: compForm.url.trim() || undefined,
        notes: compForm.notes.trim() || undefined,
        verified: true,
      });
      setCompForm({ title: "", price: "", condition: "good", postage: "0", url: "", notes: "" });
      const [updatedDeal, updatedEvidence] = await Promise.all([
        api.deal(deal.id),
        api.comps(deal.product_key, deal.kind),
      ]);
      setDeal(updatedDeal);
      setEvidence(updatedEvidence);
      onToast("Sold evidence saved — valuation refreshed", "success");
      onChanged();
    } catch (err) {
      onToast(err instanceof Error ? err.message : "Could not save sold evidence", "error");
    } finally {
      setCompBusy(false);
    }
  }

  async function removeComp(id: number) {
    if (!deal?.product_key || compBusy) return;
    setCompBusy(true);
    try {
      await api.deleteComp(id);
      const [updatedDeal, updatedEvidence] = await Promise.all([
        api.deal(deal.id),
        api.comps(deal.product_key, deal.kind),
      ]);
      setDeal(updatedDeal);
      setEvidence(updatedEvidence);
      onToast("Manual evidence removed", "success");
      onChanged();
    } catch (err) {
      onToast(err instanceof Error ? err.message : "Could not remove evidence", "error");
    } finally {
      setCompBusy(false);
    }
  }

  async function submitReview(decision: "approved" | "rejected" | "corrected") {
    if (!deal || reviewBusy) return;
    setReviewBusy(true);
    try {
      await api.review(deal.id, {
        decision,
        product_key: decision === "corrected" ? correction.product_key.trim().toLowerCase() : undefined,
        product_name: decision === "corrected" ? correction.product_name.trim() || undefined : undefined,
        kind: decision === "corrected" ? correction.kind : undefined,
        note:
          decision === "approved"
            ? "Checked manually; no independent evidence added by this decision."
            : decision === "rejected"
              ? "Rejected as an incorrect opportunity after manual review."
              : "Product identification corrected manually.",
      });
      const updated = await api.deal(deal.id);
      setDeal(updated);
      setShowCorrection(false);
      onChanged();
      onToast(
        decision === "approved"
          ? "Marked as genuine. Confidence was not increased."
          : decision === "rejected"
            ? "Listing rejected and removed from the pending review queue"
            : "Identification corrected; valuation evidence is recalculating",
        "success",
      );
    } catch (err) {
      onToast(err instanceof Error ? err.message : "Could not save the review decision", "error");
    } finally {
      setReviewBusy(false);
    }
  }

  async function undoReview() {
    if (!deal || reviewBusy) return;
    setReviewBusy(true);
    try {
      await api.undoReview(deal.id);
      setDeal(await api.deal(deal.id));
      onChanged();
      onToast("Review decision cleared");
    } catch {
      onToast("Could not clear the review decision", "error");
    } finally {
      setReviewBusy(false);
    }
  }

  async function markPurchased() {
    if (!deal) return;
    setBusy(true);
    try {
      await api.startFlip({
        listing_id: deal.id,
        purchase_price: deal.price,
        purchase_shipping: deal.shipping,
      });
      onToast("Added to My flips with the original prediction attached");
      onChanged();
    } catch (err) {
      onToast(err instanceof Error ? err.message : "Could not record this purchase", "error");
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
  const risk = breakdown?.risk;
  const evidenceInfo = breakdown?.evidence;
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

                {deal.needs_review && (
                  <div className="rounded-xl border border-ember/25 bg-ember/[0.06] p-3.5">
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <p className="flex items-center gap-2 text-xs font-semibold text-ember">
                        <CircleAlert className="h-3.5 w-3.5" /> Manual review required
                      </p>
                      {deal.review_decision && (
                        <span className="chip border-ink-500 bg-ink-800 text-mist-300">
                          {deal.review_decision === "approved" ? "Approved" : deal.review_decision === "rejected" ? "Rejected" : "Corrected"}
                        </span>
                      )}
                    </div>
                    <p className="mt-1 text-[11px] leading-relaxed text-mist-400">
                      {deal.review_reason ?? "This listing is suppressed from automatic recommendations until the price and contents are verified."}
                    </p>
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {deal.flags.map((flag) => (
                        <span key={flag} className="chip border-ink-500 bg-ink-900/70 font-mono text-[9px] text-mist-400">
                          {flag.replaceAll("_", " ")}
                        </span>
                      ))}
                    </div>
                    {!deal.review_decision ? (
                      <div className="mt-3 flex flex-wrap items-center gap-1.5">
                        <button className="btn-primary !py-1.5 !text-[11px]" onClick={() => void submitReview("approved")} disabled={reviewBusy}>
                          <Check className="h-3 w-3" /> Genuine opportunity
                        </button>
                        <button className="btn-secondary !py-1.5 !text-[11px]" onClick={() => void submitReview("rejected")} disabled={reviewBusy}>
                          <X className="h-3 w-3" /> Reject
                        </button>
                        <button className="btn-secondary !py-1.5 !text-[11px]" onClick={() => setShowCorrection((value) => !value)} disabled={reviewBusy}>
                          <Pencil className="h-3 w-3" /> Correct identification
                        </button>
                      </div>
                    ) : (
                      <button className="mt-2 inline-flex items-center gap-1 text-[10px] text-mist-500 hover:text-mist-300" onClick={() => void undoReview()} disabled={reviewBusy}>
                        <RotateCcw className="h-3 w-3" /> Undo decision
                      </button>
                    )}
                    {showCorrection && !deal.review_decision && (
                      <div className="mt-3 grid gap-2 rounded-lg border border-ink-600 bg-ink-900/70 p-3 sm:grid-cols-2">
                        <input
                          className="field !py-2"
                          placeholder="product:key"
                          value={correction.product_key}
                          onChange={(event) => setCorrection((value) => ({ ...value, product_key: event.target.value }))}
                        />
                        <input
                          className="field !py-2"
                          placeholder="Product name"
                          value={correction.product_name}
                          onChange={(event) => setCorrection((value) => ({ ...value, product_name: event.target.value }))}
                        />
                        <select
                          className="field !py-2"
                          value={correction.kind}
                          onChange={(event) => setCorrection((value) => ({ ...value, kind: event.target.value }))}
                        >
                          {["gpu", "cpu", "ram", "ssd", "motherboard", "pc", "laptop", "console", "monitor", "phone", "tablet"].map((kind) => (
                            <option key={kind} value={kind}>{CATEGORY_LABELS[kind] ?? kind}</option>
                          ))}
                        </select>
                        <button className="btn-primary !py-2" onClick={() => void submitReview("corrected")} disabled={reviewBusy || !correction.product_key.trim()}>
                          Save correction
                        </button>
                        <p className="text-[9px] leading-relaxed text-mist-500 sm:col-span-2">
                          Correcting identification changes which comparables are used. It does not raise the valuation
                          confidence or convert this listing into a recommendation by itself.
                        </p>
                      </div>
                    )}
                  </div>
                )}

                {deal.duplicate_of && (
                  <div className="rounded-xl border border-ink-600 bg-ink-850/60 p-3 text-[11px] text-mist-400">
                    Possible cross-marketplace duplicate of listing #{deal.duplicate_of}. Both original records are preserved.
                  </div>
                )}

                {risk && risk.score >= 30 && (
                  <div
                    className={cn(
                      "rounded-xl border p-3.5",
                      risk.level === "high"
                        ? "border-rose/30 bg-rose/[0.06]"
                        : "border-amber-400/25 bg-amber-400/[0.05]",
                    )}
                  >
                    <div className="flex items-center justify-between gap-3">
                      <p className={cn("flex items-center gap-2 text-xs font-semibold", risk.level === "high" ? "text-rose" : "text-amber-300")}>
                        <CircleAlert className="h-3.5 w-3.5" /> Marketplace risk {Math.round(risk.score)}/100
                      </p>
                      <span className="chip border-ink-600 bg-ink-900/70 text-[9px] uppercase text-mist-400">
                        {risk.level}
                      </span>
                    </div>
                    <p className="mt-1 text-[11px] leading-relaxed text-mist-400">
                      This is a verification score, not an accusation. A low price alone is never treated as proof of fraud.
                    </p>
                    <ul className="mt-2 space-y-1.5">
                      {risk.reasons.map((reason) => (
                        <li key={reason} className="flex items-start gap-2 text-[11px] leading-relaxed text-mist-400">
                          <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-mist-500" /> {reason}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

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
                  <p className="mt-3 text-[11px] text-mist-400">
                    Evidence: <span className="font-medium text-mist-200">{deal.evidence_level ?? "insufficient"}</span>
                    {evidenceInfo?.sources && Object.keys(evidenceInfo.sources).length > 0 && (
                      <> from {Object.entries(evidenceInfo.sources).map(([name, count]) => `${name} ${count}`).join(", ")}</>
                    )}
                    {deal.valuation_refreshed_at ? ` · price memory refreshed ${timeAgo(deal.valuation_refreshed_at)}` : " · not yet in price memory"}
                  </p>
                  {resale?.strategies && (
                    <p className="mt-1 text-[10px] leading-relaxed text-mist-500">
                      Resale strategies: quick {money(resale.strategies.quick?.expected)} · balanced {money(resale.strategies.balanced?.expected)} · maximum {money(resale.strategies.max?.expected)}.
                      {resale.initial_ask ? ` Start the balanced listing at about ${money(resale.initial_ask)}; the expected final sale is the lower figure.` : ""}
                      {resale.holding_days ? ` Typical selling time ${resale.holding_days} days.` : ""}
                    </p>
                  )}
                  {deal.is_auction && (
                    <p className="mt-1 text-[10px] leading-relaxed text-amber-300/80">
                      Auction ending {timeUntil(deal.auction_end_at)} · current bid {money(deal.current_bid ?? deal.price)} · {deal.bid_count ?? 0} bid(s).
                      {deal.max_bid ? ` Maximum profitable bid ${money(deal.max_bid)} — bid yourself before it ends.` : " No profitable bid ceiling at this price."}
                    </p>
                  )}
                  <p className={cn("mt-3 text-[11px]", tone.text)}>
                    {tone.label}
                    {resale?.range_supported && deal.resale_low != null && deal.resale_high != null
                      ? ` · supported range ${money(deal.resale_low)}–${money(deal.resale_high)}`
                      : " · resale range unavailable: evidence is too thin or dispersed"}
                  </p>
                  {resale?.range_supported && (
                    <p className="mt-1 text-[10px] leading-relaxed text-mist-500">
                      Conservative / central / optimistic resale: {money(resale.conservative)} / {money(resale.central)} / {money(resale.optimistic)}. {resale.range_basis}
                    </p>
                  )}
                  {evidenceInfo?.confidence_low != null && evidenceInfo?.confidence_high != null && (
                    <p className="mt-1 text-[10px] leading-relaxed text-mist-500">
                      80% evidence interval {money(evidenceInfo.confidence_low)}–{money(evidenceInfo.confidence_high)} from {evidenceInfo.sold_evidence ? "completed sales" : "live asking prices"}.
                      {evidenceInfo.trend_note ? ` ${evidenceInfo.trend_note}` : ""}
                    </p>
                  )}
                  {!evidenceInfo?.sold_evidence && (
                    <p className="mt-1 text-[10px] font-medium leading-relaxed text-amber-300/80">
                      Asking-only valuation: no completed-sale evidence supports this resale estimate yet.
                    </p>
                  )}
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
                          {Boolean(resale.other) && <Row label="Other / refurbishment costs" value={`−${money(resale.other)}`} />}
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

                <div>
                  <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                    <h3 className="flex items-center gap-2 text-sm font-semibold text-mist-100">
                      <Database className="h-4 w-4 text-mist-400" /> Verified sold evidence
                    </h3>
                    <span className="chip border-ink-600 bg-ink-800 text-mist-400">
                      {evidence?.summary.manual_count ?? 0} manual · {evidence?.summary.sold_count ?? 0} sold
                    </span>
                  </div>
                  {evidenceError && <p className="mb-2 text-[11px] text-rose">{evidenceError}</p>}
                  <div className="space-y-1.5">
                    {(evidence?.comps ?? []).filter((comp) => comp.is_sold).slice(0, 12).map((comp) => (
                      <div
                        key={comp.id}
                        className="flex items-center justify-between gap-3 rounded-lg border border-lime-400/15 bg-lime-400/[0.03] px-3 py-2"
                      >
                        <div className="min-w-0">
                          <p className="truncate text-[12px] text-mist-200">{comp.title}</p>
                          <p className="mt-0.5 text-[10px] text-mist-500">
                            {comp.source === "manual" ? "User entered" : comp.source === "manual_csv" ? "CSV import" : "Marketplace sale"} · {conditionLabel(comp.condition_norm)} ·{" "}
                            {timeAgo(comp.observed_at)}
                          </p>
                        </div>
                        <div className="flex shrink-0 items-center gap-2">
                          <p className="numeric text-[12.5px] font-medium text-lime-300">{money(comp.price)}</p>
                          {comp.url && (
                            <a href={comp.url} target="_blank" rel="noopener noreferrer" aria-label="Open sold evidence" className="text-mist-500 hover:text-mist-200">
                              <ExternalLink className="h-3 w-3" />
                            </a>
                          )}
                          {["manual", "manual_csv"].includes(comp.source) && (
                            <button
                              onClick={() => void removeComp(comp.id)}
                              disabled={compBusy}
                              className="text-mist-600 hover:text-rose disabled:opacity-50"
                              aria-label="Delete user-supplied evidence"
                            >
                              <Trash2 className="h-3 w-3" />
                            </button>
                          )}
                        </div>
                      </div>
                    ))}
                    {!evidence?.comps.some((comp) => comp.is_sold) && (
                      <div className="rounded-lg border border-ink-700 bg-ink-850/50 p-3 text-[11px] leading-relaxed text-mist-400">
                        No completed-sale evidence yet. Add a verified sold price below; the valuation will prefer it over asking prices.
                      </div>
                    )}
                  </div>

                  {deal.product_key ? (
                    <div className="mt-3 rounded-xl border border-ink-700 bg-ink-900/70 p-3.5">
                      <p className="mb-2 flex items-center gap-1.5 text-[11px] font-medium text-mist-300">
                        <Plus className="h-3.5 w-3.5" /> Add verified sold price for {deal.product_key}
                      </p>
                      <div className="grid gap-2 sm:grid-cols-2">
                        <input
                          className="field !py-2"
                          placeholder="Sold title (e.g. RTX 4070 12GB)"
                          value={compForm.title}
                          onChange={(event) => setCompForm((prev) => ({ ...prev, title: event.target.value }))}
                        />
                        <div className="grid grid-cols-2 gap-2">
                          <input
                            className="field !py-2"
                            inputMode="decimal"
                            placeholder="Sold price £"
                            value={compForm.price}
                            onChange={(event) => setCompForm((prev) => ({ ...prev, price: event.target.value }))}
                          />
                          <input
                            className="field !py-2"
                            inputMode="decimal"
                            placeholder="Postage £"
                            value={compForm.postage}
                            onChange={(event) => setCompForm((prev) => ({ ...prev, postage: event.target.value }))}
                          />
                        </div>
                        <select
                          className="field !py-2"
                          value={compForm.condition}
                          onChange={(event) => setCompForm((prev) => ({ ...prev, condition: event.target.value }))}
                          aria-label="Sold item condition"
                        >
                          <option value="new">New</option>
                          <option value="like_new">Like new</option>
                          <option value="excellent">Excellent</option>
                          <option value="very_good">Very good</option>
                          <option value="good">Good</option>
                          <option value="used">Used</option>
                          <option value="fair">Fair</option>
                          <option value="poor">Poor</option>
                        </select>
                        <input
                          className="field !py-2"
                          placeholder="Evidence URL (optional)"
                          value={compForm.url}
                          onChange={(event) => setCompForm((prev) => ({ ...prev, url: event.target.value }))}
                        />
                      </div>
                      <input
                        className="field mt-2 !py-2"
                        placeholder="Notes (optional, e.g. eBay sold 14 Oct, 12GB model)"
                        value={compForm.notes}
                        onChange={(event) => setCompForm((prev) => ({ ...prev, notes: event.target.value }))}
                      />
                      <div className="mt-2 flex items-center justify-between gap-2">
                        <p className="text-[10px] text-mist-500">Only add prices you have actually seen for a completed sale.</p>
                        <button className="btn-secondary !py-1.5 !text-[11px]" onClick={() => void addComp()} disabled={compBusy}>
                          {compBusy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Plus className="h-3.5 w-3.5" />}
                          Save sold price
                        </button>
                      </div>
                    </div>
                  ) : (
                    <p className="mt-3 text-[11px] text-mist-500">This listing has no resolved product key, so it cannot be valued or given manual evidence.</p>
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
            <button onClick={markPurchased} className="btn-secondary !px-3 !py-2.5" disabled={busy} title="Record this purchase in My flips">
              <Check className="h-4 w-4 text-lime-300" />
              <span className="hidden sm:inline">Purchased</span>
            </button>
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
