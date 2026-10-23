import { useCallback, useEffect, useState } from "react";
import { Check, ChevronDown, Plus, ThumbsDown, ThumbsUp, Trash2 } from "lucide-react";
import { api } from "../lib/api";
import type { Flip, FlipPatch, FlipSummary } from "../lib/types";
import { money, pct, timeAgo } from "../lib/format";
import { cn, EmptyRadar, SectionHeading } from "../components/ui";

const STATUSES = ["all", "held", "listed", "sold", "unsold"] as const;

export function FlipsPage({
  refreshTick,
  onToast,
  onRefreshSummary,
}: {
  refreshTick: number;
  onToast: (message: string, tone?: "success" | "error" | "info") => void;
  onRefreshSummary: () => void;
}) {
  const [flips, setFlips] = useState<Flip[]>([]);
  const [summary, setSummary] = useState<FlipSummary | null>(null);
  const [status, setStatus] = useState<(typeof STATUSES)[number]>("all");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<number | null>(null);
  const [showManual, setShowManual] = useState(false);

  const load = useCallback(async () => {
    try {
      const response = await api.flips(status);
      setFlips(response.flips);
      setSummary(response.summary);
    } catch (error) {
      onToast(error instanceof Error ? error.message : "Could not load flips", "error");
    } finally {
      setLoading(false);
    }
  }, [status, onToast]);

  useEffect(() => {
    void load();
  }, [load, refreshTick]);

  const patch = useCallback(
    async (flip: Flip, changes: FlipPatch) => {
      setBusy(flip.id);
      try {
        const response = await api.updateFlip(flip.id, changes);
        setSummary(response.summary);
        await load();
        onRefreshSummary();
        onToast("Flip updated");
      } catch (error) {
        onToast(error instanceof Error ? error.message : "Update failed", "error");
      } finally {
        setBusy(null);
      }
    },
    [load, onToast, onRefreshSummary],
  );

  return (
    <div className="space-y-5">
      <SectionHeading
        eyebrow="Personal history"
        title="My flips"
        description="Every purchase, price cut and sale, compared against the prediction made before you bought it."
      />

      {summary && (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <Stat label="Realised profit" value={money(summary.realised_profit)} tone={summary.realised_profit >= 0 ? "good" : "bad"} />
          <Stat label="Predicted for the same flips" value={money(summary.predicted_profit)} />
          <Stat
            label="Average prediction error"
            value={summary.prediction_mae === null ? "—" : money(summary.prediction_mae)}
            hint={`${summary.counts.sold} sold · ${summary.counts.held + summary.counts.listed} open`}
          />
          <Stat
            label="Capital tied up"
            value={money(summary.capital_tied_up)}
            hint={summary.average_days_to_sell !== null ? `${summary.average_days_to_sell} days average to sell` : "No sales yet"}
          />
        </div>
      )}

      {summary && Object.keys(summary.learned_factors).length > 0 && (
        <p className="rounded-xl border border-ink-700/70 bg-ink-900/40 px-4 py-3 text-xs text-mist-400">
          Resale estimates have been adjusted from your own completed sales:{" "}
          {Object.entries(summary.learned_factors)
            .map(([kind, factor]) => `${kind} ${factor >= 1 ? "+" : ""}${((factor - 1) * 100).toFixed(1)}%`)
            .join(", ")}
          . At least three completed flips are required before an adjustment applies.
        </p>
      )}

      <div className="flex flex-wrap items-center gap-2">
        {STATUSES.map((key) => (
          <button
            key={key}
            onClick={() => setStatus(key)}
            className={cn("chip capitalize", status === key && "chip-active")}
          >
            {key}
          </button>
        ))}
        <button className="btn-ghost ml-auto !px-3 !py-1.5 !text-xs" onClick={() => setShowManual((value) => !value)}>
          <Plus className="h-3.5 w-3.5" /> Add a flip manually
        </button>
      </div>

      {showManual && <ManualFlip onCreated={() => { setShowManual(false); void load(); onRefreshSummary(); }} onToast={onToast} />}

      {loading ? (
        <div className="panel p-8 text-center text-sm text-mist-500">Loading your flips…</div>
      ) : flips.length === 0 ? (
        <EmptyRadar
          title="No flips recorded yet"
          description="Mark a deal as purchased from its drawer, or add one manually, and FlipIntel will compare each prediction with the real result."
        />
      ) : (
        <div className="space-y-3">
          {flips.map((flip) => (
            <FlipRow key={flip.id} flip={flip} busy={busy === flip.id} onPatch={patch} onToast={onToast} />
          ))}
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, hint, tone }: { label: string; value: string; hint?: string; tone?: "good" | "bad" }) {
  return (
    <div className="panel px-4 py-3">
      <p className="eyebrow">{label}</p>
      <p className={cn("mt-1 font-mono text-lg", tone === "good" ? "text-lime-300" : tone === "bad" ? "text-rose" : "text-mist-100")}>
        {value}
      </p>
      {hint && <p className="mt-0.5 text-[11px] text-mist-500">{hint}</p>}
    </div>
  );
}

function FlipRow({
  flip,
  busy,
  onPatch,
  onToast,
}: {
  flip: Flip;
  busy: boolean;
  onPatch: (flip: Flip, changes: FlipPatch) => Promise<void>;
  onToast: (message: string, tone?: "success" | "error" | "info") => void;
}) {
  const [open, setOpen] = useState(false);
  const [sale, setSale] = useState({
    final_price: flip.final_price?.toString() ?? "",
    fees: flip.fees?.toString() ?? "",
    postage: flip.postage?.toString() ?? "",
  });

  const submitSale = async () => {
    const final = Number(sale.final_price);
    if (!Number.isFinite(final) || final <= 0) {
      onToast("Enter the price the item sold for", "error");
      return;
    }
    await onPatch(flip, {
      status: "sold",
      final_price: final,
      fees: Number(sale.fees) || 0,
      postage: Number(sale.postage) || 0,
    });
  };

  return (
    <div className="panel overflow-hidden">
      <div className="flex flex-wrap items-start gap-3 p-4">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <p className="truncate text-sm font-medium text-mist-100">{flip.title}</p>
            <span className={cn("chip !px-1.5 !py-0 !text-[9px] uppercase", statusClass(flip.status))}>{flip.status}</span>
            {flip.kind && <span className="text-[10px] uppercase tracking-wide text-mist-500">{flip.kind}</span>}
          </div>
          <p className="mt-1 text-[11px] text-mist-500">
            Bought {money(flip.total_invested)} · {flip.price_changes.length} price change
            {flip.price_changes.length === 1 ? "" : "s"} · updated {timeAgo(flip.updated_at)}
          </p>
        </div>
        <div className="text-right">
          <p className="font-mono text-sm text-mist-200">
            {flip.net_profit === null ? "Open" : money(flip.net_profit)}
          </p>
          <p className="text-[10px] text-mist-500">
            {flip.roi === null ? `predicted ${money(flip.predicted_profit)}` : `${pct(flip.roi * 100)} ROI`}
          </p>
        </div>
        <button className="icon-btn" onClick={() => setOpen((value) => !value)} aria-label="Flip details">
          <ChevronDown className={cn("h-4 w-4 transition", open && "rotate-180")} />
        </button>
      </div>

      {open && (
        <div className="space-y-4 border-t border-ink-700/60 px-4 py-4 text-xs">
          <div className="grid gap-3 sm:grid-cols-3">
            <Info label="Predicted resale" value={money(flip.predicted_resale)} />
            <Info label="Predicted profit" value={money(flip.predicted_profit)} />
            <Info
              label="Actual vs predicted"
              value={flip.prediction_error === null ? "—" : money(flip.prediction_error)}
              tone={flip.prediction_error !== null ? (flip.prediction_error >= 0 ? "good" : "bad") : undefined}
            />
            <Info label="Resale evidence" value={flip.predicted_evidence ?? "not recorded"} />
            <Info label="Days to sell" value={flip.days_to_sell === null ? "—" : String(flip.days_to_sell)} />
            <Info label="Ask price" value={money(flip.ask_price)} />
          </div>

          {flip.prediction_reasons.length > 0 && (
            <ul className="space-y-1 text-[11px] text-mist-500">
              {flip.prediction_reasons.slice(0, 4).map((reason, index) => (
                <li key={index}>· {reason}</li>
              ))}
            </ul>
          )}

          <div className="flex flex-wrap items-end gap-2">
            {flip.status === "held" && (
              <button className="btn-ghost !px-2.5 !py-1.5 !text-[11px]" disabled={busy} onClick={() => void onPatch(flip, { status: "listed" })}>
                <Check className="h-3.5 w-3.5" /> Mark listed
              </button>
            )}
            {flip.status !== "sold" && (
              <>
                <Field label="Sold for" value={sale.final_price} onChange={(value) => setSale({ ...sale, final_price: value })} />
                <Field label="Fees" value={sale.fees} onChange={(value) => setSale({ ...sale, fees: value })} />
                <Field label="Postage" value={sale.postage} onChange={(value) => setSale({ ...sale, postage: value })} />
                <button className="btn-primary !px-2.5 !py-1.5 !text-[11px]" disabled={busy} onClick={() => void submitSale()}>
                  <Check className="h-3.5 w-3.5" /> Record sale
                </button>
              </>
            )}
            <button className="btn-ghost !px-2.5 !py-1.5 !text-[11px]" disabled={busy} onClick={() => void onPatch(flip, { status: "unsold" })}>
              <Trash2 className="h-3.5 w-3.5" /> Unsold
            </button>
            <span className="mx-1 text-mist-600">Was this recommendation right?</span>
            <button
              className={cn("icon-btn", flip.feedback === 1 && "text-lime-300")}
              onClick={() => void onPatch(flip, { feedback: flip.feedback === 1 ? 0 : 1 })}
              aria-label="Good recommendation"
            >
              <ThumbsUp className="h-3.5 w-3.5" />
            </button>
            <button
              className={cn("icon-btn", flip.feedback === -1 && "text-rose")}
              onClick={() => void onPatch(flip, { feedback: flip.feedback === -1 ? 0 : -1 })}
              aria-label="Bad recommendation"
            >
              <ThumbsDown className="h-3.5 w-3.5" />
            </button>
          </div>

          {flip.price_changes.length > 0 && (
            <p className="text-[11px] text-mist-500">
              Price history: {flip.price_changes.map((change) => money(change.price)).join(" → ")}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

function Info({ label, value, tone }: { label: string; value: string; tone?: "good" | "bad" }) {
  return (
    <div>
      <p className="eyebrow">{label}</p>
      <p className={cn("mt-0.5 font-mono text-xs", tone === "good" ? "text-lime-300" : tone === "bad" ? "text-rose" : "text-mist-200")}>
        {value}
      </p>
    </div>
  );
}

function Field({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return (
    <label className="block">
      <span className="text-[10px] uppercase tracking-wide text-mist-500">{label}</span>
      <input
        className="input mt-1 !w-24 !py-1 !text-xs"
        inputMode="decimal"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </label>
  );
}

function ManualFlip({
  onCreated,
  onToast,
}: {
  onCreated: () => void;
  onToast: (message: string, tone?: "success" | "error" | "info") => void;
}) {
  const [title, setTitle] = useState("");
  const [price, setPrice] = useState("");
  const [resale, setResale] = useState("");
  const [kind, setKind] = useState("gpu");
  const [saving, setSaving] = useState(false);

  const submit = async () => {
    if (!title.trim()) {
      onToast("Give the flip a title", "error");
      return;
    }
    setSaving(true);
    try {
      await api.startFlip({
        title: title.trim(),
        kind,
        purchase_price: Number(price) || 0,
        predicted_resale: Number(resale) || null,
        predicted_profit: resale && price ? Number(resale) - Number(price) : null,
      });
      setTitle("");
      setPrice("");
      setResale("");
      onToast("Flip added");
      onCreated();
    } catch (error) {
      onToast(error instanceof Error ? error.message : "Could not add the flip", "error");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="panel grid gap-3 p-4 sm:grid-cols-2 lg:grid-cols-5">
      <label className="block lg:col-span-2">
        <span className="eyebrow">Item</span>
        <input className="input mt-1" value={title} onChange={(event) => setTitle(event.target.value)} placeholder="e.g. RTX 3080 Founders Edition" />
      </label>
      <label className="block">
        <span className="eyebrow">Cost</span>
        <input className="input mt-1" inputMode="decimal" value={price} onChange={(event) => setPrice(event.target.value)} placeholder="240" />
      </label>
      <label className="block">
        <span className="eyebrow">Expected resale</span>
        <input className="input mt-1" inputMode="decimal" value={resale} onChange={(event) => setResale(event.target.value)} placeholder="320" />
      </label>
      <div className="flex items-end gap-2">
        <label className="block flex-1">
          <span className="eyebrow">Kind</span>
          <select className="input mt-1" value={kind} onChange={(event) => setKind(event.target.value)}>
            {["gpu", "cpu", "ram", "ssd", "motherboard", "pc", "laptop", "console", "monitor", "phone", "tablet", "networking"].map((value) => (
              <option key={value} value={value}>{value}</option>
            ))}
          </select>
        </label>
        <button className="btn-primary !px-3 !py-2 !text-xs" disabled={saving} onClick={() => void submit()}>
          <Plus className="h-3.5 w-3.5" /> Add
        </button>
      </div>
    </div>
  );
}

function statusClass(status: Flip["status"]): string {
  if (status === "sold") return "border-lime-400/30 bg-lime-400/10 text-lime-300";
  if (status === "unsold") return "border-rose/30 bg-rose/10 text-rose";
  if (status === "listed") return "border-sky-400/30 bg-sky-400/10 text-sky-300";
  return "border-ink-600 bg-ink-800 text-mist-400";
}

export default FlipsPage;
