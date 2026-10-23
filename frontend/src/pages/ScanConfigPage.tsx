import { useEffect, useMemo, useState } from "react";
import {
  Check,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  Copy,
  Layers3,
  Loader2,
  Minus,
  Pencil,
  Radar,
  Save,
  Search,
  ShieldAlert,
  Trash2,
} from "lucide-react";
import { api } from "../lib/api";
import type {
  HealthResponse,
  ScanCategoryNode,
  ScanConfiguration,
  ScanPresetConfig,
} from "../lib/types";
import { cn } from "../components/ui";

interface Props {
  health: HealthResponse | null;
  onToast: (message: string, tone?: "success" | "error" | "info") => void;
  onRefreshSummary: () => void;
}

const MARKETPLACES = [
  { key: "vinted", label: "Vinted", note: "Server-side price filter" },
  { key: "gumtree", label: "Gumtree", note: "Server-side price filter" },
  { key: "ebay", label: "eBay UK", note: "Browse API price filter" },
] as const;

export function ScanConfigPage({ health, onToast, onRefreshSummary }: Props) {
  const [config, setConfig] = useState<ScanConfiguration | null>(null);
  const [enabled, setEnabled] = useState<Set<string>>(new Set());
  const [minPrice, setMinPrice] = useState("0");
  const [maxPrice, setMaxPrice] = useState("100");
  const [marketplaces, setMarketplaces] = useState({ vinted: true, gumtree: true, ebay: true });
  const [presetSlug, setPresetSlug] = useState("custom");
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [starting, setStarting] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [presetName, setPresetName] = useState("");
  const [renameName, setRenameName] = useState("");
  const [busyPreset, setBusyPreset] = useState(false);
  const [coverageOpen, setCoverageOpen] = useState(false);

  useEffect(() => {
    let active = true;
    api
      .scanConfig()
      .then((data) => {
        if (!active) return;
        applyConfig(data);
        setExpanded(new Set(data.nodes.filter((node) => node.parent === null).slice(0, 3).map((node) => node.key)));
      })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : "Could not load scan configuration");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  function applyConfig(data: ScanConfiguration) {
    setConfig(data);
    setEnabled(new Set(data.categories));
    setMinPrice(String(data.min_price));
    setMaxPrice(String(data.max_price));
    setMarketplaces(data.marketplaces);
    setPresetSlug(data.active_preset);
    setDirty(false);
    const custom = data.presets.find((preset) => preset.slug === data.active_preset && !preset.builtin);
    setRenameName(custom?.name ?? "");
  }

  const nodeByKey = useMemo(() => {
    const map = new Map<string, ScanCategoryNode>();
    config?.nodes.forEach((node) => map.set(node.key, node));
    return map;
  }, [config]);

  const children = useMemo(() => {
    const map = new Map<string | null, ScanCategoryNode[]>();
    config?.nodes.forEach((node) => {
      const list = map.get(node.parent) ?? [];
      list.push(node);
      map.set(node.parent, list);
    });
    return map;
  }, [config]);

  const leafKeys = useMemo(
    () => new Set(config?.nodes.filter((node) => !node.is_group).map((node) => node.key) ?? []),
    [config],
  );

  const descendants = (key: string): string[] => {
    const result: string[] = [];
    const walk = (parent: string) => {
      for (const child of children.get(parent) ?? []) {
        if (child.is_group) walk(child.key);
        else result.push(child.key);
      }
    };
    walk(key);
    return result;
  };

  const stateFor = (key: string): "all" | "some" | "none" => {
    const node = nodeByKey.get(key);
    if (!node) return "none";
    if (!node.is_group) return enabled.has(key) ? "all" : "none";
    const leaves = descendants(key);
    const selected = leaves.filter((leaf) => enabled.has(leaf)).length;
    if (selected === leaves.length && leaves.length > 0) return "all";
    return selected > 0 ? "some" : "none";
  };

  const enabledLeaves = useMemo(
    () => [...enabled].filter((key) => leafKeys.has(key)),
    [enabled, leafKeys],
  );

  const activeCustomPreset = useMemo(
    () => config?.presets.find((preset) => preset.slug === presetSlug && !preset.builtin) ?? null,
    [config, presetSlug],
  );

  function markCustom() {
    setPresetSlug("custom");
    setDirty(true);
  }

  function toggleLeaf(key: string) {
    setEnabled((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
    markCustom();
  }

  function setGroup(key: string, on: boolean) {
    const leaves = descendants(key);
    setEnabled((current) => {
      const next = new Set(current);
      leaves.forEach((leaf) => (on ? next.add(leaf) : next.delete(leaf)));
      return next;
    });
    markCustom();
  }

  function toggleExpanded(key: string) {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  function choosePreset(preset: ScanPresetConfig) {
    setEnabled(new Set(preset.categories));
    setMinPrice(String(preset.min_price));
    setMaxPrice(String(preset.max_price));
    setMarketplaces(preset.marketplaces);
    setPresetSlug(preset.slug);
    setRenameName(preset.builtin ? "" : preset.name);
    setDirty(true);
  }

  function validate(): string | null {
    const min = Number(minPrice);
    const max = Number(maxPrice);
    if (!Number.isFinite(min) || !Number.isFinite(max)) return "Enter a valid minimum and maximum price";
    if (min < 0 || max < 0) return "Prices cannot be negative";
    if (max <= 0) return "Set a maximum purchase price";
    if (min > max) return "Minimum price cannot exceed the maximum";
    if (enabledLeaves.length === 0) return "Enable at least one category before saving";
    if (!Object.values(marketplaces).some(Boolean)) return "Select at least one marketplace";
    return null;
  }

  async function save(): Promise<ScanConfiguration | null> {
    const problem = validate();
    if (problem) {
      onToast(problem, "error");
      return null;
    }
    setSaving(true);
    try {
      const updated = await api.updateScanConfig({
        categories: enabledLeaves,
        min_price: Number(minPrice),
        max_price: Number(maxPrice),
        marketplaces,
        active_preset: presetSlug,
        confirmed: true,
      });
      applyConfig(updated);
      onToast("Scan configuration saved — next scans will use it", "success");
      onRefreshSummary();
      return updated;
    } catch (reason) {
      onToast(reason instanceof Error ? reason.message : "Could not save scan configuration", "error");
      return null;
    } finally {
      setSaving(false);
    }
  }

  async function startScan() {
    setStarting(true);
    try {
      if (dirty || !config?.confirmed) {
        const saved = await save();
        if (!saved) return;
      }
      const response = await api.scan();
      onToast(response.message, response.accepted.length ? "success" : "info");
      onRefreshSummary();
    } catch (reason) {
      onToast(reason instanceof Error ? reason.message : "Could not start a scan", "error");
    } finally {
      setStarting(false);
    }
  }

  async function createPreset() {
    const name = presetName.trim();
    if (!name) {
      onToast("Give the preset a name", "error");
      return;
    }
    setBusyPreset(true);
    try {
      const response = await api.createScanPreset({
        name,
        base_slug: undefined,
        config: {
          categories: enabledLeaves,
          min_price: Number(minPrice),
          max_price: Number(maxPrice),
          marketplaces,
          description: "Saved from the Scan Configuration page",
        },
      });
      setConfig((current) => (current ? { ...current, presets: response.presets } : current));
      setPresetSlug(response.preset.slug);
      setRenameName(response.preset.name);
      setPresetName("");
      setDirty(true);
      onToast(`Preset “${response.preset.name}” saved`, "success");
    } catch (reason) {
      onToast(reason instanceof Error ? reason.message : "Could not save preset", "error");
    } finally {
      setBusyPreset(false);
    }
  }

  async function duplicatePreset() {
    if (!activeCustomPreset) return;
    setBusyPreset(true);
    try {
      const response = await api.createScanPreset({
        name: `${activeCustomPreset.name} copy`.slice(0, 80),
        base_slug: activeCustomPreset.slug,
      });
      setConfig((current) => (current ? { ...current, presets: response.presets } : current));
      setPresetSlug(response.preset.slug);
      setRenameName(response.preset.name);
      setDirty(true);
      onToast("Preset duplicated", "success");
    } catch (reason) {
      onToast(reason instanceof Error ? reason.message : "Could not duplicate preset", "error");
    } finally {
      setBusyPreset(false);
    }
  }

  async function renamePreset() {
    if (!activeCustomPreset) return;
    const name = renameName.trim();
    if (!name || name === activeCustomPreset.name) return;
    setBusyPreset(true);
    try {
      const response = await api.updateScanPreset(activeCustomPreset.id!, { name });
      setConfig((current) => (current ? { ...current, presets: response.presets } : current));
      setRenameName(name);
      onToast("Preset renamed", "success");
    } catch (reason) {
      onToast(reason instanceof Error ? reason.message : "Could not rename preset", "error");
    } finally {
      setBusyPreset(false);
    }
  }

  async function deletePreset() {
    if (!activeCustomPreset?.id) return;
    if (!window.confirm(`Delete the preset “${activeCustomPreset.name}”?`)) return;
    setBusyPreset(true);
    try {
      const response = await api.deleteScanPreset(activeCustomPreset.id);
      setConfig((current) => (current ? { ...current, presets: response.presets } : current));
      setPresetSlug("custom");
      setRenameName("");
      setDirty(true);
      onToast("Preset deleted", "success");
    } catch (reason) {
      onToast(reason instanceof Error ? reason.message : "Could not delete preset", "error");
    } finally {
      setBusyPreset(false);
    }
  }

  const sources = health?.scanner.sources ?? [];
  const scannerRunning = health?.scanner.scanner_running ?? false;
  const confirmed = config?.confirmed ?? false;
  const enabledGroups = config
    ? config.nodes.filter((node) => node.parent === null && stateFor(node.key) !== "none").length
    : 0;
  const marketSummary = config
    ? config.nodes
        .filter((node) => leafKeys.has(node.key))
        .reduce<Record<string, number>>((acc, node) => {
          node.mapped_marketplaces.forEach((market) => {
            if (enabled.has(node.key)) acc[market] = (acc[market] ?? 0) + 1;
          });
          return acc;
        }, {})
    : {};

  if (loading) {
    return (
      <div className="space-y-4">
        <div className="skeleton h-40 rounded-2xl" />
        <div className="skeleton h-96 rounded-2xl" />
      </div>
    );
  }

  if (error || !config) {
    return (
      <div className="rounded-2xl border border-rose/25 bg-rose/[0.04] p-6 text-sm text-mist-300">
        <p className="flex items-center gap-2 font-medium text-rose">
          <CircleAlert className="h-4 w-4" /> Scan configuration unavailable
        </p>
        <p className="mt-2">{error ?? "The backend did not return a configuration."}</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <section
        className={cn(
          "overflow-hidden rounded-2xl border bg-ink-900/70",
          confirmed ? "border-ink-700" : "border-rose/60 shadow-[0_0_0_1px_rgba(244,63,94,0.12)]",
        )}
      >
        <div
          className={cn(
            "flex flex-wrap items-start justify-between gap-4 border-b px-5 py-4",
            confirmed ? "border-ink-700/70" : "border-rose/30 bg-rose/[0.06]",
          )}
        >
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="eyebrow">Pre-scan configuration</span>
              {!confirmed && (
                <span className="chip border-rose/40 bg-rose/15 text-rose">
                  <ShieldAlert className="h-3 w-3" /> Not configured
                </span>
              )}
              {dirty && confirmed && (
                <span className="chip border-ember/30 bg-ember/10 text-ember">Unsaved changes</span>
              )}
            </div>
            <h2 className="mt-2 text-lg font-semibold text-mist-100">
              {confirmed ? "Choose what the next scan may buy" : "Scanning is paused until this is saved"}
            </h2>
            <p className="mt-1 max-w-3xl text-xs leading-relaxed text-mist-400">
              {confirmed
                ? "These toggles decide which marketplace branches are requested. Listings from disabled product classes are rejected before storage, comparable research or valuation."
                : "No marketplace request will run at launch or on a schedule until this configuration is confirmed. The initial Budget Electronics selection is preloaded below; review it and save."}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button className="btn-secondary" onClick={() => void save()} disabled={saving}>
              {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
              Save configuration
            </button>
            <button className="btn-primary" onClick={() => void startScan()} disabled={starting || saving}>
              {starting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Radar className="h-3.5 w-3.5" />}
              Start scan
            </button>
          </div>
        </div>

        <div className="grid gap-4 px-5 py-4 md:grid-cols-3">
          <div>
            <p className="eyebrow">Enabled categories</p>
            <p className="numeric mt-1 text-2xl font-semibold text-mist-100">
              {enabledLeaves.length}
              <span className="ml-1 text-xs font-normal text-mist-500">/ {config.nodes.filter((n) => !n.is_group).length}</span>
            </p>
            <p className="mt-1 text-[10px] text-mist-500">{enabledGroups} top-level groups selected</p>
          </div>
          <div>
            <p className="eyebrow">Purchase budget</p>
            <p className="numeric mt-1 text-2xl font-semibold text-mist-100">
              £{Number(minPrice || 0).toFixed(0)}–£{Number(maxPrice || 0).toFixed(0)}
            </p>
            <p className="mt-1 text-[10px] text-mist-500">Total cost, including known delivery and buyer fees</p>
          </div>
          <div>
            <p className="eyebrow">Marketplaces</p>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {MARKETPLACES.map((market) => (
                <span
                  key={market.key}
                  className={cn(
                    "chip",
                    marketplaces[market.key]
                      ? "border-lime-400/25 bg-lime-400/10 text-lime-300"
                      : "border-ink-600 bg-ink-800 text-mist-500",
                  )}
                >
                  {market.label} · {marketplaces[market.key] ? `${marketSummary[market.key] ?? 0} cats` : "off"}
                </span>
              ))}
            </div>
            <p className="mt-2 text-[10px] text-mist-500">
              Scanner {scannerRunning ? "running" : "idle"} · {sources.filter((source) => source.available).length} sources available
            </p>
          </div>
        </div>

        {!confirmed && (
          <div className="flex flex-wrap items-center gap-2 border-t border-rose/20 bg-rose/[0.04] px-5 py-3 text-xs text-rose">
            <ShieldAlert className="h-4 w-4" />
            Configuration highlighted because it has never been saved. Review the categories, budget and marketplaces, then press Save configuration.
          </div>
        )}
      </section>

      <section className="rounded-2xl border border-ink-700 bg-ink-900/60 p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="eyebrow">Preset</p>
            <p className="mt-1 text-xs text-mist-400">
              A preset is only a saved collection of toggles, marketplaces and budget. Selecting one does not start a scan.
            </p>
          </div>
          <button className="btn-ghost !px-2.5 !py-1.5 !text-[10px]" onClick={() => setDirty(true)}>
            <Pencil className="h-3 w-3" /> Custom changes
          </button>
        </div>
        <div className="mt-3 flex flex-wrap gap-1.5">
          {config.presets.map((preset) => (
            <button
              key={preset.slug}
              onClick={() => choosePreset(preset)}
              className={cn(
                "rounded-lg border px-2.5 py-1.5 text-[11px] font-medium transition",
                preset.slug === presetSlug
                  ? "border-lime-400/40 bg-lime-400/10 text-lime-200"
                  : "border-ink-600 bg-ink-850 text-mist-400 hover:border-ink-500 hover:text-mist-200",
              )}
            >
              {preset.name}
              {!preset.builtin && <span className="ml-1 text-[9px] text-mist-500">custom</span>}
            </button>
          ))}
        </div>

        <div className="mt-4 grid gap-3 border-t border-ink-700/70 pt-4 md:grid-cols-3">
          <div>
            <label className="eyebrow" htmlFor="min-price">Minimum purchase price (£)</label>
            <input
              id="min-price"
              className="field mt-1.5"
              inputMode="decimal"
              value={minPrice}
              onChange={(event) => {
                setMinPrice(event.target.value);
                markCustom();
              }}
            />
          </div>
          <div>
            <label className="eyebrow" htmlFor="max-price">Maximum purchase price (£)</label>
            <input
              id="max-price"
              className="field mt-1.5"
              inputMode="decimal"
              value={maxPrice}
              onChange={(event) => {
                setMaxPrice(event.target.value);
                markCustom();
              }}
            />
          </div>
          <div>
            <p className="eyebrow">Marketplace sources</p>
            <div className="mt-1.5 space-y-1">
              {MARKETPLACES.map((market) => (
                <button
                  key={market.key}
                  onClick={() => {
                    setMarketplaces((current) => ({ ...current, [market.key]: !current[market.key] }));
                    markCustom();
                  }}
                  className="flex w-full items-center justify-between gap-2 rounded-md border border-ink-700 bg-ink-850 px-2.5 py-1.5 text-[11px] text-mist-300 transition hover:border-ink-500"
                >
                  <span className="flex items-center gap-2">
                    <ToggleBox state={marketplaces[market.key] ? "all" : "none"} />
                    {market.label}
                  </span>
                  <span className="text-[9px] text-mist-500">{market.note}</span>
                </button>
              ))}
            </div>
          </div>
        </div>

        <div className="mt-4 rounded-lg border border-ink-700/70 bg-ink-850/60 px-3 py-2 text-[10px] leading-relaxed text-mist-500">
          Budget uses the total purchase cost. Vinted totals include the configured buyer-protection fee and standard delivery because catalogue cards do not state them; eBay uses listed postage when known; Gumtree is treated as a collection price. Prices over the limit are rejected before valuation.
        </div>

        <div className="mt-4 flex flex-wrap items-end gap-2 border-t border-ink-700/70 pt-4">
          <div className="min-w-[190px] flex-1">
            <label className="eyebrow" htmlFor="preset-name">Save current selection as a preset</label>
            <input
              id="preset-name"
              className="field mt-1.5"
              placeholder="e.g. Cheap GPUs and RAM"
              value={presetName}
              onChange={(event) => setPresetName(event.target.value)}
            />
          </div>
          <button className="btn-secondary" onClick={() => void createPreset()} disabled={busyPreset}>
            <Layers3 className="h-3.5 w-3.5" /> Save preset
          </button>
          {activeCustomPreset && (
            <>
              <div className="min-w-[170px]">
                <label className="eyebrow" htmlFor="rename-preset">Rename custom preset</label>
                <input
                  id="rename-preset"
                  className="field mt-1.5"
                  value={renameName}
                  onChange={(event) => setRenameName(event.target.value)}
                />
              </div>
              <button className="btn-ghost" onClick={() => void renamePreset()} disabled={busyPreset}>
                <Pencil className="h-3.5 w-3.5" /> Rename
              </button>
              <button className="btn-ghost" onClick={() => void duplicatePreset()} disabled={busyPreset}>
                <Copy className="h-3.5 w-3.5" /> Duplicate
              </button>
              <button className="btn-ghost !text-rose" onClick={() => void deletePreset()} disabled={busyPreset}>
                <Trash2 className="h-3.5 w-3.5" /> Delete
              </button>
            </>
          )}
        </div>
      </section>

      <section className="rounded-2xl border border-ink-700 bg-ink-900/60">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-ink-700/70 px-4 py-3">
          <div>
            <h3 className="text-sm font-semibold text-mist-100">Electronics categories</h3>
            <p className="mt-0.5 text-[10px] text-mist-500">
              Groups, subcategories and individual toggles. Every leaf maps to real marketplace branches.
            </p>
          </div>
          <div className="relative min-w-[210px]">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-mist-500" />
            <input
              className="field !pl-8"
              placeholder="Filter categories…"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>
        </div>
        <div className="space-y-2 p-3">
          {(children.get(null) ?? []).map((root) => (
            <CategoryBranch
              key={root.key}
              node={root}
              childrenMap={children}
              stateFor={stateFor}
              expanded={expanded}
              toggleExpanded={toggleExpanded}
              setGroup={setGroup}
              toggleLeaf={toggleLeaf}
              query={query}
            />
          ))}
        </div>
      </section>

      <section className="rounded-2xl border border-ink-700 bg-ink-900/60 p-4">
        <button className="flex w-full items-center justify-between gap-3 text-left" onClick={() => setCoverageOpen((value) => !value)}>
          <span>
            <span className="eyebrow">Coverage report</span>
            <span className="mt-1 block text-xs text-mist-400">
              Real marketplace branches discovered, mapped requests, selected requests and known gaps.
            </span>
          </span>
          {coverageOpen ? <ChevronDown className="h-4 w-4 text-mist-500" /> : <ChevronRight className="h-4 w-4 text-mist-500" />}
        </button>
        {coverageOpen && (
          <div className="mt-4 space-y-4 border-t border-ink-700/70 pt-4">
            <div className="grid gap-3 sm:grid-cols-3">
              {(["vinted", "gumtree", "ebay"] as const).map((market) => {
                const row = config.coverage[market];
                return (
                  <div key={market} className="rounded-lg border border-ink-700 bg-ink-850 px-3 py-2.5">
                    <p className="text-[10px] font-medium uppercase tracking-wider text-mist-500">{market}</p>
                    <p className="numeric mt-1 text-lg font-semibold text-mist-100">
                      {row.selected_categories}
                      <span className="ml-1 text-[10px] font-normal text-mist-500">/ {row.discovered_categories} requests</span>
                    </p>
                    <p className="mt-1 text-[10px] text-mist-500">
                      {row.mapped_leaves} product leaves mapped · {row.live_scan_rows} live scan rows
                    </p>
                    {row.live_unmapped.length > 0 && (
                      <p className="mt-1 text-[10px] text-ember">{row.live_unmapped.length} live branch(es) without a mapping</p>
                    )}
                  </div>
                );
              })}
            </div>
            <div className="grid gap-3 md:grid-cols-3">
              <div className="rounded-lg border border-ink-700 bg-ink-850 px-3 py-2.5">
                <p className="eyebrow">Taxonomy</p>
                <p className="numeric mt-1 text-sm text-mist-200">
                  {config.coverage.summary.leaves} leaves · {config.coverage.summary.groups} groups
                </p>
                <p className="mt-1 text-[10px] text-mist-500">
                  {config.coverage.summary.valued_leaves} valued · {config.coverage.summary.unvalued_leaves} collection-only
                </p>
              </div>
              <div className="rounded-lg border border-ink-700 bg-ink-850 px-3 py-2.5">
                <p className="eyebrow">Unmapped leaves</p>
                <p className="numeric mt-1 text-sm text-mist-200">{config.coverage.summary.unmapped_leaves}</p>
                <p className="mt-1 text-[10px] text-mist-500">
                  {config.coverage.summary.unmapped_keys.length
                    ? config.coverage.summary.unmapped_keys.slice(0, 4).join(", ")
                    : "Every leaf has at least one real marketplace branch"}
                </p>
              </div>
              <div className="rounded-lg border border-ember/25 bg-ember/[0.04] px-3 py-2.5">
                <p className="eyebrow text-ember">Honest limitation</p>
                <p className="mt-1 text-[10px] leading-relaxed text-mist-400">
                  Collection-only categories are available to scan but do not yet have a resale valuation model. They are marked “Not valued yet”.
                </p>
              </div>
            </div>
            <div className="space-y-1">
              {config.coverage.notes.map((note) => (
                <p key={note} className="flex gap-2 text-[10px] leading-relaxed text-mist-500">
                  <span className="mt-1 h-1 w-1 shrink-0 rounded-full bg-mist-600" />
                  {note}
                </p>
              ))}
            </div>
          </div>
        )}
      </section>

      <div className="flex justify-end gap-2 pb-2">
        <button className="btn-secondary" onClick={() => void save()} disabled={saving}>
          {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
          Save configuration
        </button>
        <button className="btn-primary" onClick={() => void startScan()} disabled={starting || saving}>
          {starting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Radar className="h-3.5 w-3.5" />}
          Start scan
        </button>
      </div>
    </div>
  );
}

function ToggleBox({ state }: { state: "all" | "some" | "none" }) {
  return (
    <span
      className={cn(
        "flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded border",
        state === "all"
          ? "border-lime-400 bg-lime-400 text-ink-950"
          : state === "some"
            ? "border-ember bg-ember/20 text-ember"
            : "border-ink-500 bg-ink-850",
      )}
    >
      {state === "all" ? <Check className="h-2.5 w-2.5" strokeWidth={3} /> : state === "some" ? <Minus className="h-2.5 w-2.5" strokeWidth={3} /> : null}
    </span>
  );
}

function descendantLeafCount(key: string, childrenMap: Map<string | null, ScanCategoryNode[]>): number {
  return (childrenMap.get(key) ?? []).reduce(
    (count, child) => count + (child.is_group ? descendantLeafCount(child.key, childrenMap) : 1),
    0,
  );
}

function CategoryBranch({
  node,
  childrenMap,
  stateFor,
  expanded,
  toggleExpanded,
  setGroup,
  toggleLeaf,
  query,
}: {
  node: ScanCategoryNode;
  childrenMap: Map<string | null, ScanCategoryNode[]>;
  stateFor: (key: string) => "all" | "some" | "none";
  expanded: Set<string>;
  toggleExpanded: (key: string) => void;
  setGroup: (key: string, on: boolean) => void;
  toggleLeaf: (key: string) => void;
  query: string;
}) {
  const childNodes = childrenMap.get(node.key) ?? [];
  const leafCount = descendantLeafCount(node.key, childrenMap);
  const state = stateFor(node.key);
  const search = query.trim().toLowerCase();
  const matches = (item: ScanCategoryNode): boolean => {
    if (!search) return true;
    if (item.label.toLowerCase().includes(search)) return true;
    return (childrenMap.get(item.key) ?? []).some(matches);
  };
  if (!matches(node)) return null;
  const isLeaf = !node.is_group;
  if (isLeaf) {
    return (
      <div className="flex items-center justify-between gap-3 rounded-lg border border-transparent px-3 py-1.5 hover:border-ink-700 hover:bg-ink-850/60">
        <button className="flex min-w-0 items-center gap-2.5 text-left" onClick={() => toggleLeaf(node.key)}>
          <ToggleBox state={state} />
          <span className="truncate text-[12px] text-mist-300">{node.label}</span>
          {!node.valued && (
            <span className="chip border-ember/20 bg-ember/5 !px-1.5 !py-0.5 !text-[8px] text-ember">Not valued yet</span>
          )}
        </button>
        <span className="shrink-0 text-[9px] text-mist-600">
          {node.mapping_count ? `${node.mapping_count} branch${node.mapping_count === 1 ? "" : "es"}` : "unmapped"}
        </span>
      </div>
    );
  }
  const open = expanded.has(node.key);
  const isRoot = node.parent === null;
  return (
    <div className={cn("overflow-hidden rounded-xl border", isRoot ? "border-ink-700 bg-ink-850/40" : "border-ink-700/60")}>
      <div className={cn("flex flex-wrap items-center gap-2 px-3 py-2", isRoot && "bg-ink-850/80")}>
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <button onClick={() => toggleExpanded(node.key)} aria-label={open ? "Collapse" : "Expand"}>
            {open ? <ChevronDown className="h-3.5 w-3.5 text-mist-500" /> : <ChevronRight className="h-3.5 w-3.5 text-mist-500" />}
          </button>
          <button
            onClick={() => setGroup(node.key, state !== "all")}
            aria-label={`Toggle ${node.label}`}
          >
            <ToggleBox state={state} />
          </button>
          <button className="min-w-0 text-left" onClick={() => toggleExpanded(node.key)}>
            <span className={cn("truncate font-medium", isRoot ? "text-[13px] text-mist-100" : "text-[12px] text-mist-200")}>
              {node.label}
            </span>
            <span className="ml-2 text-[9px] text-mist-600">
              {leafCount} leaves
            </span>
          </button>
        </div>
        <div className="flex items-center gap-1">
          <button className="text-[9px] font-medium text-mist-500 hover:text-lime-300" onClick={() => setGroup(node.key, true)}>
            Enable all
          </button>
          <span className="text-mist-700">·</span>
          <button className="text-[9px] font-medium text-mist-500 hover:text-rose" onClick={() => setGroup(node.key, false)}>
            Disable all
          </button>
        </div>
      </div>
      {open && (
        <div className={cn("space-y-1 px-2 py-2", !isRoot && "border-t border-ink-700/50")}>
          {childNodes.map((child) => (
            <CategoryBranch
              key={child.key}
              node={child}
              childrenMap={childrenMap}
              stateFor={stateFor}
              expanded={expanded}
              toggleExpanded={toggleExpanded}
              setGroup={setGroup}
              toggleLeaf={toggleLeaf}
              query={query}
            />
          ))}
        </div>
      )}
    </div>
  );
}
