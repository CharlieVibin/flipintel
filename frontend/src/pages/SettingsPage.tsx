import { useEffect, useState, type ReactNode } from "react";
import {
  Bell,
  BellRing,
  Check,
  CircleAlert,
  Database,
  ExternalLink,
  Gauge,
  KeyRound,
  Loader2,
  Save,
  ShieldCheck,
  SlidersHorizontal,
  Timer,
  Trash2,
  Wallet,
} from "lucide-react";
import { api } from "../lib/api";
import type { EbayConnectionTest, EbayCredentialsStatus, Settings } from "../lib/types";
import { cn } from "../components/ui";

interface Props {
  onToast: (message: string, tone?: "success" | "error" | "info") => void;
  onRefreshSummary: () => void;
}

type NumericKey =
  | "min_profit"
  | "min_roi_pct"
  | "min_confidence_pct"
  | "ebay_fvf_pct"
  | "resale_shipping"
  | "packaging_cost"
  | "other_costs"
  | "vinted_shipping"
  | "asking_to_sold_pct"
  | "min_roi_high_value_pct"
  | "high_value_threshold"
  | "history_reuse_days"
  | "cex_refresh_limit"
  | "auction_window_minutes"
  | "auction_scan_seconds"
  | "scan_interval_vinted"
  | "scan_interval_gumtree"
  | "scan_interval_ebay"
  | "queries_per_cycle";

const EMPTY_NUMBERS: Record<NumericKey, string> = {
  min_profit: "20",
  min_roi_pct: "12",
  min_confidence_pct: "42",
  ebay_fvf_pct: "13.22",
  resale_shipping: "4.50",
  packaging_cost: "1.00",
  other_costs: "0.00",
  vinted_shipping: "3.49",
  asking_to_sold_pct: "85",
  min_roi_high_value_pct: "18",
  high_value_threshold: "250",
  history_reuse_days: "7",
  cex_refresh_limit: "6",
  auction_window_minutes: "30",
  auction_scan_seconds: "600",
  scan_interval_vinted: "240",
  scan_interval_gumtree: "300",
  scan_interval_ebay: "300",
  queries_per_cycle: "12",
};

export function SettingsPage({ onToast, onRefreshSummary }: Props) {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [numbers, setNumbers] = useState<Record<NumericKey, string>>(EMPTY_NUMBERS);
  const [enabled, setEnabled] = useState({ vinted: true, gumtree: true, ebay: true });
  const [resaleChannel, setResaleChannel] = useState<"ebay" | "vinted" | "facebook">("ebay");
  const [resaleStrategy, setResaleStrategy] = useState<"quick" | "balanced" | "max">("balanced");
  const [notify, setNotify] = useState(false);
  const [webhook, setWebhook] = useState("");
  const [removeWebhook, setRemoveWebhook] = useState(false);
  const [browserNotify, setBrowserNotify] = useState(
    typeof window !== "undefined" && window.localStorage.getItem("rr-browser-notifications") === "1",
  );
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [ebayForm, setEbayForm] = useState({
    client_id: "",
    client_secret: "",
    environment: "production",
  });
  const [ebayBusy, setEbayBusy] = useState(false);
  const [ebayTest, setEbayTest] = useState<EbayConnectionTest | null>(null);
  const [ebayError, setEbayError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    api
      .settings()
      .then((data) => {
        if (!active) return;
        setSettings(data);
        setNumbers({
          min_profit: String(data.deal.min_profit),
          min_roi_pct: String(round(data.deal.min_roi * 100, 2)),
          min_confidence_pct: String(round(data.deal.min_confidence * 100, 1)),
          ebay_fvf_pct: String(round(data.fees.ebay_fvf * 100, 2)),
          resale_shipping: String(data.fees.resale_shipping),
          packaging_cost: String(data.fees.packaging_cost),
          other_costs: String(data.fees.other_costs),
          vinted_shipping: String(data.fees.vinted_shipping),
          asking_to_sold_pct: String(round(data.fees.asking_to_sold_factor * 100, 1)),
          min_roi_high_value_pct: String(round(data.fees.min_roi_high_value * 100, 1)),
          high_value_threshold: String(data.fees.high_value_threshold),
          history_reuse_days: String(data.pricing.history_reuse_days),
          cex_refresh_limit: String(data.pricing.cex_refresh_limit),
          auction_window_minutes: String(data.auctions.window_minutes),
          auction_scan_seconds: String(data.auctions.scan_seconds),
          scan_interval_vinted: String(data.scanning.scan_interval_vinted),
          scan_interval_gumtree: String(data.scanning.scan_interval_gumtree),
          scan_interval_ebay: String(data.scanning.scan_interval_ebay),
          queries_per_cycle: String(data.scanning.queries_per_cycle),
        });
        setEnabled({
          vinted: data.scanning.enabled_vinted,
          gumtree: data.scanning.enabled_gumtree,
          ebay: data.scanning.enabled_ebay,
        });
        setResaleChannel(data.fees.resale_channel);
        setResaleStrategy(data.fees.resale_strategy);
        setNotify(data.notifications.enabled);
      })
      .catch((error: unknown) => {
        if (active) setLoadError(error instanceof Error ? error.message : "Could not load settings");
      });
    return () => {
      active = false;
    };
  }, []);

  function updateNumber(key: NumericKey, value: string) {
    setNumbers((prev) => ({ ...prev, [key]: value }));
    setDirty(true);
  }

  async function save() {
    setSaving(true);
    try {
      const payload: Record<string, unknown> = {
        min_profit: num(numbers.min_profit, 20),
        min_roi: num(numbers.min_roi_pct, 12) / 100,
        min_confidence: num(numbers.min_confidence_pct, 42) / 100,
        ebay_fvf: num(numbers.ebay_fvf_pct, 13.22) / 100,
        resale_channel: resaleChannel,
        resale_shipping: num(numbers.resale_shipping, 4.5),
        packaging_cost: num(numbers.packaging_cost, 1),
        other_costs: num(numbers.other_costs, 0),
        vinted_shipping: num(numbers.vinted_shipping, 3.49),
        asking_to_sold_factor: num(numbers.asking_to_sold_pct, 85) / 100,
        resale_strategy: resaleStrategy,
        min_roi_high_value: num(numbers.min_roi_high_value_pct, 18) / 100,
        high_value_threshold: num(numbers.high_value_threshold, 250),
        history_reuse_days: Math.max(1, Math.round(num(numbers.history_reuse_days, 7))),
        cex_refresh_limit: Math.max(0, Math.round(num(numbers.cex_refresh_limit, 6))),
        auction_window_minutes: Math.max(5, Math.round(num(numbers.auction_window_minutes, 30))),
        auction_scan_seconds: Math.max(120, Math.round(num(numbers.auction_scan_seconds, 600))),
        scan_interval_vinted: Math.max(120, Math.round(num(numbers.scan_interval_vinted, 240))),
        scan_interval_gumtree: Math.max(120, Math.round(num(numbers.scan_interval_gumtree, 300))),
        scan_interval_ebay: Math.max(120, Math.round(num(numbers.scan_interval_ebay, 300))),
        queries_per_cycle: Math.min(12, Math.max(1, Math.round(num(numbers.queries_per_cycle, 4)))),
        enabled_vinted: enabled.vinted,
        enabled_gumtree: enabled.gumtree,
        enabled_ebay: enabled.ebay,
        notifications_enabled: notify,
      };
      if (removeWebhook) {
        payload.discord_webhook_url = "";
      } else if (webhook.trim()) {
        payload.discord_webhook_url = webhook.trim();
      }
      const updated = await api.updateSettings(payload);
      setSettings(updated);
      setWebhook("");
      setRemoveWebhook(false);
      setDirty(false);
      onToast("Settings saved — valuations refreshed", "success");
      onRefreshSummary();
    } catch (error) {
      onToast(error instanceof Error ? error.message : "Could not save settings", "error");
    } finally {
      setSaving(false);
    }
  }

  async function saveEbay() {
    if (!ebayForm.client_id.trim() || !ebayForm.client_secret.trim()) {
      setEbayError("Enter both the client ID and client secret");
      return;
    }
    setEbayBusy(true);
    setEbayError(null);
    setEbayTest(null);
    try {
      const response = await api.saveEbayCredentials({
        client_id: ebayForm.client_id.trim(),
        client_secret: ebayForm.client_secret.trim(),
        environment: ebayForm.environment,
      });
      setSettings(response.settings);
      setEbayForm((value) => ({ ...value, client_secret: "" }));
      onToast("eBay credentials saved securely to the local .env", "success");
      const test = await api.testEbayConnection();
      setEbayTest(test);
      if (test.ok) {
        onToast(
          `eBay connected: ${test.sample_count ?? 0} UK listings returned`,
          "success",
        );
      } else {
        setEbayError(test.error ?? "eBay rejected the connection test");
      }
      onRefreshSummary();
    } catch (error) {
      setEbayError(error instanceof Error ? error.message : "Could not save eBay credentials");
      onToast("Could not save eBay credentials", "error");
    } finally {
      setEbayBusy(false);
    }
  }

  async function testEbay() {
    setEbayBusy(true);
    setEbayError(null);
    try {
      const test = await api.testEbayConnection();
      setEbayTest(test);
      if (test.ok) {
        onToast(`eBay connected: ${test.sample_count ?? 0} UK listings returned`, "success");
      } else {
        setEbayError(test.error ?? "eBay connection test failed");
      }
    } catch (error) {
      setEbayError(error instanceof Error ? error.message : "eBay connection test failed");
    } finally {
      setEbayBusy(false);
    }
  }

  async function removeEbay() {
    setEbayBusy(true);
    setEbayError(null);
    setEbayTest(null);
    try {
      const response = await api.removeEbayCredentials();
      setSettings(response.settings);
      setEbayForm({ client_id: "", client_secret: "", environment: "production" });
      onToast("eBay credentials removed", "success");
      onRefreshSummary();
    } catch (error) {
      setEbayError(error instanceof Error ? error.message : "Could not remove eBay credentials");
    } finally {
      setEbayBusy(false);
    }
  }

  async function enableBrowserNotifications() {
    if (!("Notification" in window)) {
      onToast("This browser does not support desktop notifications", "error");
      return;
    }
    const permission = await window.Notification.requestPermission();
    if (permission === "granted") {
      window.localStorage.setItem("rr-browser-notifications", "1");
      setBrowserNotify(true);
      new window.Notification("FlipIntel alerts enabled", {
        body: "You will be notified when new qualifying deals appear while the dashboard is in the background.",
      });
      onToast("Browser notifications enabled", "success");
    } else {
      window.localStorage.setItem("rr-browser-notifications", "0");
      setBrowserNotify(false);
      onToast("Notification permission was not granted", "error");
    }
  }

  const webhookConfigured = settings?.notifications.webhook_configured && !removeWebhook;

  if (loadError) {
    return (
      <div className="panel flex flex-col items-center gap-3 p-12 text-center">
        <CircleAlert className="h-7 w-7 text-rose" />
        <p className="text-sm text-mist-300">{loadError}</p>
      </div>
    );
  }

  if (!settings) {
    return (
      <div className="space-y-4">
        <div className="skeleton h-24 rounded-2xl" />
        <div className="grid gap-4 lg:grid-cols-2">
          <div className="skeleton h-64 rounded-2xl" />
          <div className="skeleton h-64 rounded-2xl" />
        </div>
      </div>
    );
  }

  return (
    <div className="pb-6">
      <div className="mb-5 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="eyebrow mb-1.5">Controls</p>
          <h1 className="text-2xl font-semibold tracking-tight text-mist-100">Settings</h1>
          <p className="mt-1 max-w-2xl text-sm text-mist-400">
            Tune what counts as a deal, the resale-cost assumptions and how often each marketplace is scanned.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {dirty && <span className="chip border-ember/25 bg-ember/5 text-ember">Unsaved changes</span>}
          <button className="btn-primary" onClick={() => void save()} disabled={saving || !dirty}>
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
            {saving ? "Saving…" : "Save changes"}
          </button>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Section icon={SlidersHorizontal} title="Deal thresholds" description="Only listings clearing all three gates are shown as deals.">
          <Field label="Minimum net profit" suffix="£" value={numbers.min_profit} onChange={(v) => updateNumber("min_profit", v)} />
          <Field label="Minimum ROI" suffix="%" value={numbers.min_roi_pct} onChange={(v) => updateNumber("min_roi_pct", v)} />
          <Field
            label="Minimum confidence"
            suffix="%"
            value={numbers.min_confidence_pct}
            onChange={(v) => updateNumber("min_confidence_pct", v)}
            hint="Low confidence means few comparables or a weak model match."
          />
          <div className="rounded-lg border border-ink-700 bg-ink-900/50 p-3 text-[11px] leading-relaxed text-mist-500">
            Deals also require at least four comparable listings and 55% model-match confidence. Faulty, wanted
            and bundle ads are never recommended.
          </div>
        </Section>

        <Section icon={Wallet} title="Resale costs" description="Applied to every valuation. Known fees are separated from estimates; nothing is silently treated as zero.">
          <div>
            <label className="label">Resale channel</label>
            <select
              className="field"
              value={resaleChannel}
              onChange={(event) => {
                setResaleChannel(event.target.value as "ebay" | "vinted" | "facebook");
                setDirty(true);
              }}
            >
              <option value="ebay">eBay UK — final value fee applies</option>
              <option value="vinted">Vinted — no seller selling fee</option>
              <option value="facebook">Facebook Marketplace — local collection</option>
            </select>
          </div>
          <Field label="eBay final value fee" suffix="%" value={numbers.ebay_fvf_pct} onChange={(v) => updateNumber("ebay_fvf_pct", v)} />
          <Field label="Outbound delivery" suffix="£" value={numbers.resale_shipping} onChange={(v) => updateNumber("resale_shipping", v)} />
          <Field label="Packaging" suffix="£" value={numbers.packaging_cost} onChange={(v) => updateNumber("packaging_cost", v)} />
          <Field label="Other / refurbishment" suffix="£" value={numbers.other_costs} onChange={(v) => updateNumber("other_costs", v)} hint="Repair, cleaning or other known costs. Keep at zero if unknown." />
          <Field label="Vinted delivery estimate" suffix="£" value={numbers.vinted_shipping} onChange={(v) => updateNumber("vinted_shipping", v)} />
          <Field
            label="Asking → sold adjustment"
            suffix="%"
            value={numbers.asking_to_sold_pct}
            onChange={(v) => updateNumber("asking_to_sold_pct", v)}
            hint="Conservative fallback for live asking prices. Categories with validated completed-sale calibration override this automatically."
          />
          <div>
            <label className="label">Resale strategy</label>
            <select
              className="field"
              value={resaleStrategy}
              onChange={(event) => {
                setResaleStrategy(event.target.value as "quick" | "balanced" | "max");
                setDirty(true);
              }}
            >
              <option value="quick">Quick sale — lower price, faster turnover</option>
              <option value="balanced">Balanced — realistic price and selling time</option>
              <option value="max">Maximum profit — higher ask, slower sale</option>
            </select>
          </div>
          <Field
            label="Minimum ROI for high-value stock"
            suffix="%"
            value={numbers.min_roi_high_value_pct}
            onChange={(v) => updateNumber("min_roi_high_value_pct", v)}
            hint="Applied when the total purchase cost is above the high-value threshold."
          />
          <Field
            label="High-value threshold"
            suffix="£"
            value={numbers.high_value_threshold}
            onChange={(v) => updateNumber("high_value_threshold", v)}
          />
        </Section>

        <Section icon={Database} title="Price memory & auctions" description="Products are researched once, reused across marketplaces and refreshed after the reuse window.">
          <Field
            label="Price reuse window"
            suffix="days"
            value={numbers.history_reuse_days}
            onChange={(v) => updateNumber("history_reuse_days", v)}
            hint="After this the price is marked stale and scheduled for refresh. History is never deleted."
          />
          <Field
            label="CeX lookups per scan"
            suffix="products"
            value={numbers.cex_refresh_limit}
            onChange={(v) => updateNumber("cex_refresh_limit", v)}
            hint="Prioritises products attached to live deals and ending auctions."
          />
          <Field
            label="Auction ending window"
            suffix="min"
            value={numbers.auction_window_minutes}
            onChange={(v) => updateNumber("auction_window_minutes", v)}
            hint="Only auctions ending inside this window enter the watchlist and alerts."
          />
          <Field
            label="Auction scan interval"
            suffix="sec"
            value={numbers.auction_scan_seconds}
            onChange={(v) => updateNumber("auction_scan_seconds", v)}
            hint="Shortened automatically to five minutes while shortlisted auctions are close to ending."
          />
        </Section>

        <Section icon={Timer} title="Scanning cadence" description="Per-source schedules with jitter and a circuit breaker. Minimum 120 seconds.">
          <Field label="Vinted interval" suffix="sec" value={numbers.scan_interval_vinted} onChange={(v) => updateNumber("scan_interval_vinted", v)} />
          <Field label="Gumtree interval" suffix="sec" value={numbers.scan_interval_gumtree} onChange={(v) => updateNumber("scan_interval_gumtree", v)} />
          <Field label="eBay interval" suffix="sec" value={numbers.scan_interval_ebay} onChange={(v) => updateNumber("scan_interval_ebay", v)} />
          <Field label="Queries per cycle" suffix="per source" value={numbers.queries_per_cycle} onChange={(v) => updateNumber("queries_per_cycle", v)} />
          <div className="mt-1 space-y-2">
            <Toggle
              label="Vinted scanning"
              description="Public catalogue pages; 96 listings per query."
              checked={enabled.vinted}
              onChange={(value) => {
                setEnabled((prev) => ({ ...prev, vinted: value }));
                setDirty(true);
              }}
            />
            <Toggle
              label="Gumtree scanning"
              description="Server-rendered search cards."
              checked={enabled.gumtree}
              onChange={(value) => {
                setEnabled((prev) => ({ ...prev, gumtree: value }));
                setDirty(true);
              }}
            />
            <Toggle
              label="eBay scanning"
              description={settings.environment.ebay_configured ? "Official Browse API is configured." : "Requires free eBay developer API keys."}
              checked={enabled.ebay}
              onChange={(value) => {
                setEnabled((prev) => ({ ...prev, ebay: value }));
                setDirty(true);
              }}
            />
          </div>
        </Section>

        <Section icon={Bell} title="Alerts" description="Optional. Browser alerts need this dashboard open in a background tab.">
          <Toggle
            label="Discord alerts"
            description="Post new qualifying deals to a Discord webhook."
            checked={notify}
            onChange={(value) => {
              setNotify(value);
              setDirty(true);
            }}
          />
          <div>
            <label className="label">Discord webhook URL</label>
            <div className="flex gap-2">
              <input
                type="password"
                className="field"
                placeholder={webhookConfigured ? "Webhook saved — enter a new URL to rotate" : "https://discord.com/api/webhooks/…"}
                value={webhook}
                onChange={(event) => {
                  setWebhook(event.target.value);
                  setDirty(true);
                }}
                autoComplete="off"
              />
              {settings.notifications.webhook_configured && !removeWebhook && (
                <button
                  type="button"
                  className="icon-btn h-[42px] w-[42px] shrink-0 text-mist-500 hover:text-rose"
                  aria-label="Remove Discord webhook"
                  onClick={() => {
                    setRemoveWebhook(true);
                    setWebhook("");
                    setDirty(true);
                  }}
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              )}
            </div>
            <p className="mt-1.5 text-[10px] text-mist-500">
              {removeWebhook
                ? "The saved webhook will be removed when you save."
                : webhookConfigured
                  ? "A webhook is saved. It is never displayed again."
                  : "Stored locally in SQLite. Never leaves this machine except to Discord."}
            </p>
          </div>
          <div className="rounded-lg border border-ink-700 bg-ink-900/50 p-3">
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="text-xs font-medium text-mist-200">Browser notifications</p>
                <p className="mt-0.5 text-[10px] text-mist-500">
                  {browserNotify ? "Enabled for this browser." : "Notify when fresh deals appear in the background."}
                </p>
              </div>
              <button className="btn-secondary !px-3 !py-1.5 !text-[11px]" onClick={() => void enableBrowserNotifications()}>
                {browserNotify ? <Check className="h-3.5 w-3.5 text-lime-400" /> : <BellRing className="h-3.5 w-3.5" />}
                {browserNotify ? "Enabled" : "Enable"}
              </button>
            </div>
            {browserNotify && (
              <button
                className="mt-2 text-[10px] text-mist-500 hover:text-mist-300"
                onClick={() => {
                  window.localStorage.setItem("rr-browser-notifications", "0");
                  setBrowserNotify(false);
                  onToast("Browser notifications disabled");
                }}
              >
                Turn off browser notifications
              </button>
            )}
          </div>
        </Section>

        <Section icon={ShieldCheck} title="Data sources & credentials" description="Credentials live in the local .env, never in the database or browser.">
          <EbayCredentialCard
            status={settings.environment.ebay}
            form={ebayForm}
            onChange={setEbayForm}
            busy={ebayBusy}
            test={ebayTest}
            error={ebayError}
            onSave={() => void saveEbay()}
            onTest={() => void testEbay()}
            onRemove={() => void removeEbay()}
          />
          <StatusRow
            icon={Gauge}
            label="CeX price anchor"
            ok={settings.environment.cex_enabled}
            okText="Enabled (optional)"
            offText="Disabled"
            note="CeX sits behind Cloudflare. If blocked, comparables still value the listing with lower confidence."
          />
          <StatusRow
            icon={CircleAlert}
            label="Facebook Marketplace"
            ok={false}
            okText=""
            offText="Not available"
            note="Requires a signed-in session; automated collection is not supported. No Facebook listings are invented."
          />
          <div className="rounded-lg border border-ink-700 bg-ink-900/50 p-3 text-[11px] leading-relaxed text-mist-500">
            Vinted and Gumtree need no credentials. All collectors honour robots.txt, use a per-host rate limiter
            and record failures honestly. Set <code className="text-mist-300">RESPECT_ROBOTS=true</code> in .env to keep
            this behaviour.
          </div>
        </Section>
      </div>

      <div className="mt-5 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-ink-700 bg-ink-900/60 px-4 py-3">
        <p className="max-w-xl text-[11px] leading-relaxed text-mist-500">
          Changing thresholds recalculates every active listing immediately. Fee and shipping assumptions only
          affect future valuations and newly computed profit.
        </p>
        <button className="btn-primary" onClick={() => void save()} disabled={saving || !dirty}>
          {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
          Save changes
        </button>
      </div>
    </div>
  );
}

function EbayCredentialCard({
  status,
  form,
  onChange,
  busy,
  test,
  error,
  onSave,
  onTest,
  onRemove,
}: {
  status: EbayCredentialsStatus;
  form: { client_id: string; client_secret: string; environment: string };
  onChange: (value: { client_id: string; client_secret: string; environment: string }) => void;
  busy: boolean;
  test: EbayConnectionTest | null;
  error: string | null;
  onSave: () => void;
  onTest: () => void;
  onRemove: () => void;
}) {
  return (
    <div className="rounded-lg border border-ink-700 bg-ink-900/40 p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2">
          <KeyRound className="h-3.5 w-3.5 text-mist-500" />
          <span className="text-xs font-medium text-mist-200">eBay Browse API</span>
        </div>
        <span className={cn("chip", status.configured ? "border-lime-400/20 bg-lime-400/5 text-lime-300" : "border-ink-500 bg-ink-700/40 text-mist-500")}>
          {status.configured ? `Configured${status.client_id_hint ? ` · ${status.client_id_hint}` : ""}` : "Free developer keys required"}
        </span>
      </div>
      <p className="mt-1.5 text-[10px] leading-relaxed text-mist-500">
        Official UK Browse API only; HTML scraping is never attempted. Keys are written to the gitignored local
        <code className="mx-1 text-mist-300">.env</code> with owner-only permissions and are never returned to the browser.
      </p>

      <div className="mt-3 grid gap-2">
        <div className="grid gap-2 sm:grid-cols-2">
          <input
            className="field !py-2"
            placeholder={status.configured ? "Client ID saved — enter a new one to rotate" : "EBAY_CLIENT_ID"}
            value={form.client_id}
            onChange={(event) => onChange({ ...form, client_id: event.target.value })}
            autoComplete="off"
            spellCheck={false}
          />
          <input
            className="field !py-2"
            type="password"
            placeholder={status.configured ? "Secret saved — enter a new secret to rotate" : "EBAY_CLIENT_SECRET"}
            value={form.client_secret}
            onChange={(event) => onChange({ ...form, client_secret: event.target.value })}
            autoComplete="new-password"
          />
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select
            className="field !w-auto !py-2 !text-[11px]"
            value={form.environment}
            onChange={(event) => onChange({ ...form, environment: event.target.value })}
            aria-label="eBay API environment"
          >
            <option value="production">Production</option>
            <option value="sandbox">Sandbox</option>
          </select>
          <button className="btn-primary !py-2 !text-[11px]" onClick={onSave} disabled={busy}>
            {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ShieldCheck className="h-3.5 w-3.5" />}
            Save & test connection
          </button>
          {status.configured && (
            <button className="btn-secondary !py-2 !text-[11px]" onClick={onTest} disabled={busy}>
              Test connection
            </button>
          )}
          {status.can_clear && (
            <button
              className="inline-flex items-center gap-1 rounded-md px-2 py-2 text-[11px] text-mist-500 transition hover:bg-ink-700/60 hover:text-rose disabled:opacity-50"
              onClick={onRemove}
              disabled={busy}
            >
              <Trash2 className="h-3.5 w-3.5" /> Remove
            </button>
          )}
          <a href="https://developer.ebay.com/my/keys" target="_blank" rel="noopener noreferrer" className="ml-auto inline-flex items-center gap-1 text-[10px] text-mist-400 hover:text-mist-200">
            Developer portal <ExternalLink className="h-2.5 w-2.5" />
          </a>
        </div>
      </div>

      {test?.ok && (
        <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-lime-400/20 bg-lime-400/[0.04] px-2.5 py-2 text-[10px] text-lime-300">
          <span className="inline-flex items-center gap-1"><Check className="h-3 w-3" /> Connected</span>
          <span>{test.marketplace} · {test.currency}</span>
          <span>{test.sample_count ?? 0} live UK listings in test query</span>
          <span>{test.latency_ms ?? 0} ms</span>
          <span className="text-mist-500">Results not inserted</span>
        </div>
      )}
      {(error || (test && !test.ok)) && (
        <div className="mt-2 flex items-start gap-1.5 rounded-md border border-rose/20 bg-rose/[0.04] px-2.5 py-2 text-[10px] leading-relaxed text-rose">
          <CircleAlert className="mt-0.5 h-3 w-3 shrink-0" />
          {error ?? test?.error ?? "Connection test failed"}
        </div>
      )}
      {status.configured && (
        <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[9px] text-mist-600">
          <span>Source: {status.credential_source}</span>
          <span>Environment: {status.environment}</span>
          <span>Stored: {status.storage}</span>
        </div>
      )}
    </div>
  );
}

function Section({
  icon: Icon,
  title,
  description,
  children,
}: {
  icon: typeof Bell;
  title: string;
  description: string;
  children: ReactNode;
}) {
  return (
    <section className="panel p-5">
      <div className="mb-4 flex items-start gap-2.5">
        <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-ink-600 bg-ink-800 text-mist-400">
          <Icon className="h-4 w-4" />
        </span>
        <div>
          <h2 className="text-sm font-semibold text-mist-100">{title}</h2>
          <p className="mt-0.5 text-[11px] leading-relaxed text-mist-500">{description}</p>
        </div>
      </div>
      <div className="space-y-3">{children}</div>
    </section>
  );
}

function Field({
  label,
  suffix,
  value,
  onChange,
  hint,
}: {
  label: string;
  suffix: string;
  value: string;
  onChange: (value: string) => void;
  hint?: string;
}) {
  return (
    <div>
      <label className="label">{label}</label>
      <div className="flex items-center rounded-lg border border-ink-600 bg-ink-900/80 focus-within:border-lime-400/60 focus-within:ring-1 focus-within:ring-lime-400/30">
        <input
          className="w-full bg-transparent px-3.5 py-2.5 text-sm text-mist-100 outline-none"
          value={value}
          inputMode="decimal"
          onChange={(event) => onChange(event.target.value)}
        />
        <span className="shrink-0 pr-3 font-mono text-[10px] uppercase tracking-wider text-mist-500">{suffix}</span>
      </div>
      {hint && <p className="mt-1 text-[10px] text-mist-500">{hint}</p>}
    </div>
  );
}

function Toggle({
  label,
  description,
  checked,
  onChange,
}: {
  label: string;
  description: string;
  checked: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <div className="flex items-center justify-between gap-4 rounded-lg border border-ink-700 bg-ink-900/40 px-3 py-2.5">
      <div>
        <p className="text-xs font-medium text-mist-200">{label}</p>
        <p className="mt-0.5 text-[10px] leading-relaxed text-mist-500">{description}</p>
      </div>
      <button
        role="switch"
        aria-checked={checked}
        aria-label={label}
        onClick={() => onChange(!checked)}
        className={cn(
          "relative h-5 w-9 shrink-0 rounded-full border transition",
          checked ? "border-lime-400/40 bg-lime-400/80" : "border-ink-500 bg-ink-700",
        )}
      >
        <span
          className={cn(
            "absolute top-0.5 h-3.5 w-3.5 rounded-full transition-all",
            checked ? "left-[18px] bg-ink-950" : "left-0.5 bg-mist-400",
          )}
        />
      </button>
    </div>
  );
}

function StatusRow({
  icon: Icon,
  label,
  ok,
  okText,
  offText,
  note,
  link,
}: {
  icon: typeof Bell;
  label: string;
  ok: boolean;
  okText: string;
  offText: string;
  note: string;
  link?: string;
}) {
  return (
    <div className="rounded-lg border border-ink-700 bg-ink-900/40 p-3">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Icon className="h-3.5 w-3.5 text-mist-500" />
          <span className="text-xs font-medium text-mist-200">{label}</span>
        </div>
        <span className={cn("chip", ok ? "border-lime-400/20 bg-lime-400/5 text-lime-300" : "border-ink-500 bg-ink-700/40 text-mist-500")}>
          {ok ? okText : offText}
        </span>
      </div>
      <p className="mt-1.5 text-[10px] leading-relaxed text-mist-500">{note}</p>
      {link && (
        <a href={link} target="_blank" rel="noopener noreferrer" className="mt-1.5 inline-flex items-center gap-1 text-[10px] text-mist-400 hover:text-mist-200">
          Open developer portal <ExternalLink className="h-2.5 w-2.5" />
        </a>
      )}
    </div>
  );
}

function num(value: string, fallback: number): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function round(value: number, digits: number): number {
  const factor = 10 ** digits;
  return Math.round(value * factor) / factor;
}
