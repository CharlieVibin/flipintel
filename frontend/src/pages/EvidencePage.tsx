import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  BadgeCheck,
  BarChart3,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Database,
  Download,
  FileSpreadsheet,
  Gauge,
  Link2,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  ShieldAlert,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { api } from "../lib/api";
import type {
  CalibrationReport,
  EvidenceCoverage,
  EvidenceImportResult,
  EvidenceItem,
  EvidenceResponse,
} from "../lib/types";
import { conditionLabel, money, timeAgo } from "../lib/format";
import { cn } from "../components/ui";

interface Props {
  refreshTick: number;
  onToast: (message: string, tone?: "success" | "error" | "info") => void;
  onRefreshSummary: () => void;
}

const CONDITIONS = ["new", "open_box", "like_new", "excellent", "very_good", "good", "used", "fair", "poor", "unknown"];

const EVIDENCE_LABELS: Record<string, string> = {
  user_entered: "User entered",
  user_imported: "CSV import",
  marketplace_sale: "Marketplace sale",
  marketplace_asking: "Asking price",
  retail_anchor: "Retail anchor",
};

const PAGE_SIZE = 50;

export function EvidencePage({ refreshTick, onToast, onRefreshSummary }: Props) {
  const [evidence, setEvidence] = useState<EvidenceResponse | null>(null);
  const [coverage, setCoverage] = useState<EvidenceCoverage | null>(null);
  const [calibration, setCalibration] = useState<CalibrationReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [productFilter, setProductFilter] = useState("");
  const [sourceFilter, setSourceFilter] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [page, setPage] = useState(1);
  const [showAdd, setShowAdd] = useState(false);
  const [editing, setEditing] = useState<EvidenceItem | null>(null);
  const [busy, setBusy] = useState(false);
  const [selectedFile, setSelectedFile] = useState<{ name: string; content: string } | null>(null);
  const [preview, setPreview] = useState<EvidenceImportResult | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedQuery(query.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    setPage(1);
  }, [debouncedQuery, productFilter, sourceFilter, dateFrom, dateTo]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const filters = {
        q: debouncedQuery || undefined,
        product_key: productFilter || undefined,
        source: sourceFilter || undefined,
        date_from: dateFrom || undefined,
        date_to: dateTo || undefined,
        page,
        limit: PAGE_SIZE,
      };
      const [rows, cover, cal] = await Promise.all([
        api.evidence(filters),
        api.evidenceCoverage(),
        api.calibration(),
      ]);
      setEvidence(rows);
      setCoverage(cover);
      setCalibration(cal);
    } catch (error) {
      onToast(error instanceof Error ? error.message : "Could not load evidence", "error");
    } finally {
      setLoading(false);
    }
  }, [debouncedQuery, productFilter, sourceFilter, dateFrom, dateTo, page, onToast]);

  useEffect(() => {
    void load();
  }, [load, refreshTick]);

  async function handleFile(file: File | undefined) {
    if (!file) return;
    if (file.size > 2 * 1024 * 1024) {
      onToast("CSV is larger than the 2 MB limit", "error");
      return;
    }
    const content = await file.text();
    setSelectedFile({ name: file.name, content });
    setPreview(null);
    setBusy(true);
    try {
      const result = await api.importEvidence({ filename: file.name, content, commit: false });
      setPreview(result);
    } catch (error) {
      onToast(error instanceof Error ? error.message : "Could not validate CSV", "error");
      setSelectedFile(null);
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  async function commitImport() {
    if (!selectedFile || !preview) return;
    setBusy(true);
    try {
      const result = await api.importEvidence({
        filename: selectedFile.name,
        content: selectedFile.content,
        commit: true,
      });
      setPreview(result);
      onToast(
        `Import complete: ${result.counts.imported} added, ${result.counts.updated} updated, ${result.counts.rejected} rejected`,
        result.counts.rejected ? "info" : "success",
      );
      setSelectedFile(null);
      await load();
      onRefreshSummary();
    } catch (error) {
      onToast(error instanceof Error ? error.message : "Could not import CSV", "error");
    } finally {
      setBusy(false);
    }
  }

  async function remove(item: EvidenceItem) {
    if (!window.confirm(`Delete the ${money(item.price)} sale for “${item.title}”?`)) return;
    setBusy(true);
    try {
      await api.deleteEvidence(item.id);
      onToast("Evidence deleted");
      await load();
      onRefreshSummary();
    } catch (error) {
      onToast(error instanceof Error ? error.message : "Could not delete evidence", "error");
    } finally {
      setBusy(false);
    }
  }

  const exportUrl = useMemo(
    () =>
      api.evidenceExportUrl({
        product_key: productFilter || undefined,
        source: sourceFilter || "user",
      }),
    [productFilter, sourceFilter],
  );

  const pages = evidence ? Math.max(1, Math.ceil(evidence.total / PAGE_SIZE)) : 1;

  return (
    <div className="space-y-5">
      <section className="panel relative overflow-hidden p-5 lg:p-6">
        <div className="pointer-events-none absolute -right-20 -top-28 h-72 w-72 rounded-full bg-lime-400/10 blur-3xl" />
        <div className="relative flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="eyebrow flex items-center gap-2 text-lime-300">
              <Database className="h-3.5 w-3.5" /> Evidence ledger
            </p>
            <h2 className="mt-2 text-lg font-semibold text-mist-100">Completed-sale intelligence</h2>
            <p className="mt-1 max-w-2xl text-[12px] leading-relaxed text-mist-400">
              Import and correct sold-price evidence, verify product coverage and see whether calibration is
              statistically supported. User-supplied records are always labelled separately from independent sales.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <a className="btn-secondary !py-2" href={api.evidenceTemplateUrl()} download>
              <FileSpreadsheet className="h-3.5 w-3.5" /> Template
            </a>
            <a className="btn-secondary !py-2" href={exportUrl} download>
              <Download className="h-3.5 w-3.5" /> Export CSV
            </a>
            <button className="btn-secondary !py-2" onClick={() => void load()} disabled={loading}>
              <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} /> Refresh
            </button>
          </div>
        </div>

        <div className="relative mt-5 grid grid-cols-2 gap-2.5 lg:grid-cols-4">
          <Metric label="Sold records" value={`${coverage?.totals.total ?? 0}`} sub={`${coverage?.totals.products_with_evidence ?? 0} products`} />
          <Metric label="User supplied" value={`${coverage?.totals.user_supplied ?? 0}`} sub="entered or imported" />
          <Metric label="Independent" value={`${coverage?.totals.independent ?? 0}`} sub="marketplace-observed" />
          <Metric
            label="Calibration"
            value={calibration?.active ? "Active" : "Not supported"}
            sub={calibration?.active ? `${calibration.global_factor ? Math.round(calibration.global_factor * 100) : "—"}% global factor` : "Conservative fallback"}
            tone={calibration?.active ? "good" : "warn"}
          />
        </div>
      </section>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_340px]">
        <div className="min-w-0 space-y-5">
          <section className="panel p-5">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h3 className="flex items-center gap-2 text-sm font-semibold text-mist-100">
                  <Upload className="h-4 w-4 text-lime-300" /> Bulk import completed sales
                </h3>
                <p className="mt-1 text-[11px] text-mist-500">
                  CSV is validated before anything is written. Re-importing the same sale updates it instead of
                  duplicating it. Limit: 2 MB / 2,000 rows.
                </p>
              </div>
              <button className="btn-secondary !py-2" onClick={() => fileRef.current?.click()} disabled={busy}>
                <Upload className="h-3.5 w-3.5" /> Choose CSV
              </button>
              <input
                ref={fileRef}
                type="file"
                accept=".csv,text/csv"
                className="hidden"
                onChange={(event) => void handleFile(event.target.files?.[0])}
              />
            </div>

            {preview && (
              <div className="mt-4 rounded-xl border border-ink-600 bg-ink-900/70">
                <div className="flex flex-wrap items-center justify-between gap-3 border-b border-ink-700 px-4 py-3">
                  <div className="flex flex-wrap items-center gap-2 text-[11px]">
                    <span className="font-medium text-mist-200">{preview.filename}</span>
                    <span className="chip border-lime-400/20 bg-lime-400/10 text-lime-300">
                      {preview.counts.imported} new
                    </span>
                    <span className="chip border-sky-400/20 bg-sky-400/10 text-sky-300">
                      {preview.counts.updated} update
                    </span>
                    <span className="chip border-ink-500 bg-ink-800 text-mist-400">
                      {preview.counts.skipped} skip
                    </span>
                    <span className={cn("chip", preview.counts.rejected ? "border-rose/30 bg-rose/10 text-rose" : "border-ink-500 bg-ink-800 text-mist-400")}>
                      {preview.counts.rejected} rejected
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    {preview.commit ? (
                      <span className="flex items-center gap-1 text-[11px] font-medium text-lime-300">
                        <CheckCircle2 className="h-3.5 w-3.5" /> Written to evidence ledger
                      </span>
                    ) : (
                      <button className="btn-primary !py-2" onClick={() => void commitImport()} disabled={busy || !selectedFile}>
                        {busy ? "Importing…" : "Confirm import"}
                      </button>
                    )}
                    <button
                      className="icon-btn"
                      aria-label="Clear preview"
                      onClick={() => {
                        setPreview(null);
                        setSelectedFile(null);
                      }}
                    >
                      <X className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </div>
                {(preview.warnings.length > 0 || preview.rejected.length > 0) && (
                  <div className="grid gap-2 border-b border-ink-700 px-4 py-3 lg:grid-cols-2">
                    {preview.warnings.map((warning) => (
                      <p key={warning} className="flex items-start gap-2 text-[11px] text-ember">
                        <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" /> {warning}
                      </p>
                    ))}
                    {preview.rejected.slice(0, 6).map((item) => (
                      <p key={`${item.row}-${item.reason}`} className="flex items-start gap-2 text-[11px] text-rose">
                        <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" /> Row {item.row}: {item.reason}
                      </p>
                    ))}
                  </div>
                )}
                <div className="max-h-64 overflow-auto">
                  <table className="w-full text-left text-[11px]">
                    <thead className="sticky top-0 bg-ink-850 text-[9px] uppercase tracking-wider text-mist-500">
                      <tr>
                        <th className="px-3 py-2">Row</th>
                        <th className="px-3 py-2">Action</th>
                        <th className="px-3 py-2">Product</th>
                        <th className="px-3 py-2">Sale</th>
                        <th className="px-3 py-2 text-right">Price</th>
                        <th className="px-3 py-2">Reason</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-ink-700/60">
                      {preview.rows.slice(0, 80).map((row) => (
                        <tr key={`${row.row}-${row.source_id}`}>
                          <td className="px-3 py-2 text-mist-500">{row.row}</td>
                          <td className="px-3 py-2">
                            <span className={cn("chip", row.action === "imported" ? "text-lime-300" : row.action === "updated" ? "text-sky-300" : "text-mist-500")}>
                              {row.action}
                            </span>
                          </td>
                          <td className="max-w-[220px] truncate px-3 py-2 text-mist-300">{row.product_key}</td>
                          <td className="max-w-[240px] truncate px-3 py-2 text-mist-400">{row.title}</td>
                          <td className="numeric px-3 py-2 text-right text-mist-200">{money(row.price)}</td>
                          <td className="max-w-[220px] truncate px-3 py-2 text-mist-500">{row.reason || "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {preview.truncated && <p className="px-4 py-2 text-[10px] text-mist-500">Preview truncated; all valid rows will still be imported.</p>}
              </div>
            )}
          </section>

          <section className="panel overflow-hidden">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-ink-700 px-5 py-4">
              <div>
                <h3 className="text-sm font-semibold text-mist-100">Saved sold evidence</h3>
                <p className="mt-0.5 text-[11px] text-mist-500">
                  {evidence?.total ?? 0} record{evidence?.total === 1 ? "" : "s"}
                  {productFilter ? ` for ${productFilter}` : ""}
                </p>
              </div>
              <button className="btn-primary !py-2" onClick={() => setShowAdd((value) => !value)}>
                <Plus className="h-3.5 w-3.5" /> Add sale
              </button>
            </div>

            {showAdd && (
              <CompForm
                onCancel={() => setShowAdd(false)}
                onSaved={async () => {
                  setShowAdd(false);
                  await load();
                  onRefreshSummary();
                }}
                onToast={onToast}
                initialProduct={productFilter}
              />
            )}

            <div className="flex flex-wrap items-center gap-2 border-b border-ink-700 px-4 py-3">
              <label className="relative min-w-[180px] flex-1">
                <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-mist-500" />
                <input
                  className="field !py-2 !pl-8"
                  placeholder="Search title, product key or notes"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                />
              </label>
              <input
                className="field !w-auto !py-2"
                placeholder="product:key"
                value={productFilter}
                onChange={(event) => setProductFilter(event.target.value)}
                aria-label="Filter by product key"
              />
              <select className="field !w-auto !py-2" value={sourceFilter} onChange={(event) => setSourceFilter(event.target.value)} aria-label="Evidence source">
                <option value="">All evidence</option>
                <option value="user">User supplied</option>
                <option value="manual_csv">CSV imports</option>
                <option value="manual">Manual entries</option>
                <option value="verified">Verified only</option>
              </select>
              <input type="date" className="field !w-auto !py-2" value={dateFrom} onChange={(event) => setDateFrom(event.target.value)} aria-label="From date" />
              <input type="date" className="field !w-auto !py-2" value={dateTo} onChange={(event) => setDateTo(event.target.value)} aria-label="To date" />
              {(query || productFilter || sourceFilter || dateFrom || dateTo) && (
                <button
                  className="text-[11px] text-mist-400 hover:text-mist-200"
                  onClick={() => {
                    setQuery("");
                    setProductFilter("");
                    setSourceFilter("");
                    setDateFrom("");
                    setDateTo("");
                  }}
                >
                  Clear
                </button>
              )}
            </div>

            {loading && !evidence ? (
              <div className="space-y-2 p-4">
                {[0, 1, 2, 3].map((item) => <div key={item} className="skeleton h-14 rounded-lg" />)}
              </div>
            ) : (evidence?.evidence.length ?? 0) === 0 ? (
              <div className="px-5 py-12 text-center">
                <Database className="mx-auto h-8 w-8 text-mist-600" />
                <p className="mt-3 text-sm font-medium text-mist-200">No matching sold evidence</p>
                <p className="mx-auto mt-1 max-w-md text-[11px] leading-relaxed text-mist-500">
                  Import a CSV or add a sale manually. Asking prices are never counted as completed sales.
                </p>
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[850px] text-left text-[11px]">
                  <thead className="bg-ink-850/70 text-[9px] uppercase tracking-wider text-mist-500">
                    <tr>
                      <th className="px-4 py-2.5">Product</th>
                      <th className="px-3 py-2.5">Sale</th>
                      <th className="px-3 py-2.5">Provenance</th>
                      <th className="px-3 py-2.5">Condition</th>
                      <th className="px-3 py-2.5">Sold</th>
                      <th className="px-3 py-2.5 text-right">Price</th>
                      <th className="px-3 py-2.5 text-right">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-ink-700/60">
                    {evidence?.evidence.map((item) => (
                      <tr key={item.id} className="align-top transition hover:bg-ink-800/40">
                        <td className="max-w-[230px] px-4 py-3">
                          <p className="truncate font-medium text-mist-200">{item.product_name || item.product_key}</p>
                          <p className="mt-0.5 truncate font-mono text-[9px] text-mist-600">{item.product_key}</p>
                          {item.variants && <p className="mt-0.5 truncate text-[9px] text-mist-600">{item.variants}</p>}
                        </td>
                        <td className="max-w-[260px] px-3 py-3">
                          <p className="truncate text-mist-300">{item.title}</p>
                          <p className="mt-0.5 text-[9px] text-mist-600">
                            {item.marketplace} · {item.kind || "kind unknown"} · {timeAgo(item.observed_at)}
                          </p>
                          {item.url && (
                            <a href={item.url} target="_blank" rel="noopener noreferrer" className="mt-1 inline-flex items-center gap-1 text-[9px] text-sky-300 hover:underline">
                              <Link2 className="h-2.5 w-2.5" /> Source listing
                            </a>
                          )}
                        </td>
                        <td className="px-3 py-3">
                          <span className={cn("chip", item.source === "manual_csv" ? "border-sky-400/20 bg-sky-400/10 text-sky-300" : "border-amber-400/20 bg-amber-400/10 text-amber-300")}>
                            {EVIDENCE_LABELS[item.evidence_type] ?? item.evidence_type}
                          </span>
                          <p className="mt-1 text-[9px] text-mist-600">{Math.round(item.evidence_confidence * 100)}% weight</p>
                        </td>
                        <td className="px-3 py-3 text-mist-400">{conditionLabel(item.condition_norm)}</td>
                        <td className="px-3 py-3 text-mist-400">{item.observed_at.slice(0, 10)}</td>
                        <td className="numeric px-3 py-3 text-right text-mist-100">
                          {money(item.price)}
                          {item.postage > 0 && <p className="text-[9px] font-normal text-mist-500">+{money(item.postage)} post</p>}
                        </td>
                        <td className="px-3 py-3">
                          <div className="flex items-center justify-end gap-1">
                            <button className="icon-btn !h-7 !w-7" onClick={() => setEditing(item)} aria-label="Correct evidence">
                              <Pencil className="h-3 w-3" />
                            </button>
                            <button className="icon-btn !h-7 !w-7 hover:text-rose" onClick={() => void remove(item)} aria-label="Delete evidence" disabled={busy}>
                              <Trash2 className="h-3 w-3" />
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {pages > 1 && (
              <div className="flex items-center justify-between border-t border-ink-700 px-4 py-3 text-[11px] text-mist-500">
                <span>Page {page} of {pages}</span>
                <div className="flex items-center gap-1">
                  <button className="icon-btn !h-7 !w-7" disabled={page <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))}>
                    <ChevronLeft className="h-3.5 w-3.5" />
                  </button>
                  <button className="icon-btn !h-7 !w-7" disabled={page >= pages} onClick={() => setPage((value) => Math.min(pages, value + 1))}>
                    <ChevronRight className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
            )}
          </section>
        </div>

        <aside className="space-y-5">
          <CalibrationPanel report={calibration} />
          <CoveragePanel
            coverage={coverage}
            onSelect={(key) => {
              setProductFilter(key);
              setPage(1);
            }}
          />
        </aside>
      </div>

      {editing && (
        <EditEvidenceDialog
          item={editing}
          onClose={() => setEditing(null)}
          onSaved={async () => {
            setEditing(null);
            await load();
            onRefreshSummary();
          }}
          onToast={onToast}
        />
      )}
    </div>
  );
}

function Metric({ label, value, sub, tone = "default" }: { label: string; value: string; sub: string; tone?: "default" | "good" | "warn" }) {
  return (
    <div className="panel-soft px-3.5 py-3">
      <p className="eyebrow">{label}</p>
      <p className={cn("mt-1 text-lg font-semibold", tone === "good" ? "text-lime-300" : tone === "warn" ? "text-ember" : "text-mist-100")}>{value}</p>
      <p className="mt-0.5 text-[10px] text-mist-500">{sub}</p>
    </div>
  );
}

function CalibrationPanel({ report }: { report: CalibrationReport | null }) {
  return (
    <section className="panel p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="eyebrow flex items-center gap-1.5"><Gauge className="h-3 w-3" /> Valuation calibration</p>
          <h3 className="mt-1.5 text-sm font-semibold text-mist-100">
            {report?.active ? "Asking-to-sold model active" : "Conservative fallback in use"}
          </h3>
        </div>
        <span className={cn("chip", report?.active ? "border-lime-400/25 bg-lime-400/10 text-lime-300" : "border-ember/25 bg-ember/10 text-ember")}>
          {report?.status === "active" ? <BadgeCheck className="h-3 w-3" /> : <AlertTriangle className="h-3 w-3" />}
          {report?.active ? "Validated" : "Not supported"}
        </span>
      </div>
      <p className="mt-2 text-[11px] leading-relaxed text-mist-400">{report?.reason ?? "Loading calibration evidence…"}</p>
      <div className="mt-3 grid grid-cols-3 gap-2">
        <MiniStat label="Matched" value={`${report?.matched_products ?? 0}`} />
        <MiniStat label="Sales" value={`${report?.sold_observations ?? 0}`} />
        <MiniStat label="Independ." value={`${report?.independent_sold ?? 0}`} />
      </div>
      {report?.active && report.global_factor && (
        <div className="mt-3 rounded-lg border border-lime-400/20 bg-lime-400/[0.04] px-3 py-2">
          <p className="text-[10px] text-mist-400">Applied factor</p>
          <p className="numeric text-lg font-semibold text-lime-300">{Math.round(report.global_factor * 100)}%</p>
          <p className="mt-0.5 text-[9px] text-mist-500">Only categories passing held-out validation override the {Math.round(report.fallback_factor * 100)}% fallback.</p>
        </div>
      )}
      {(report?.categories.length ?? 0) > 0 && (
        <div className="mt-3 space-y-1.5">
          {report?.categories.map((category) => (
            <div key={category.category} className="rounded-lg border border-ink-700/70 bg-ink-850/50 px-2.5 py-2">
              <div className="flex items-center justify-between gap-2">
                <span className="text-[11px] text-mist-300">{category.label}</span>
                <span className={cn("text-[9px] font-medium uppercase tracking-wider", category.active ? "text-lime-300" : "text-mist-500")}>
                  {category.status}
                </span>
              </div>
              <p className="mt-0.5 text-[9px] text-mist-500">
                {category.products} products · {category.factor ? `${Math.round(category.factor * 100)}% factor` : "no factor"}
                {category.holdout_mae_pct != null ? ` · ${category.holdout_mae_pct.toFixed(1)}% holdout error` : ""}
                {category.confidence > 0 ? ` · ${Math.round(category.confidence * 100)}% confidence` : ""}
              </p>
              {(category.status !== "active") && <p className="mt-1 text-[9px] leading-relaxed text-mist-600">{category.reason}</p>}
            </div>
          ))}
        </div>
      )}
      <p className="mt-3 border-t border-ink-700 pt-2 text-[9px] leading-relaxed text-mist-600">
        Calibration is fitted only on matched products with recent asks and multiple completed sales, then checked
        on products excluded from the fit. Imported user evidence is shown transparently but never applied on its own.
      </p>
    </section>
  );
}

function CoveragePanel({ coverage, onSelect }: { coverage: EvidenceCoverage | null; onSelect: (key: string) => void }) {
  const products = (coverage?.products ?? []).slice(0, 10);
  return (
    <section className="panel p-4">
      <p className="eyebrow flex items-center gap-1.5"><BarChart3 className="h-3 w-3" /> Product coverage</p>
      <h3 className="mt-1.5 text-sm font-semibold text-mist-100">Where evidence is strongest</h3>
      {products.length === 0 ? (
        <p className="mt-3 text-[11px] leading-relaxed text-mist-500">
          No product has completed-sale evidence yet. Import sales for exact product keys to unlock stronger valuations.
        </p>
      ) : (
        <div className="mt-3 space-y-1.5">
          {products.map((product) => (
            <button
              key={product.product_key}
              className="w-full rounded-lg border border-ink-700/70 bg-ink-850/40 px-2.5 py-2 text-left transition hover:border-ink-500 hover:bg-ink-800"
              onClick={() => onSelect(product.product_key)}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-[11px] text-mist-300">{product.product_name || product.product_key}</span>
                <span className={cn("chip shrink-0", product.evidence_level === "strong" ? "text-lime-300" : product.evidence_level === "moderate" ? "text-amber-300" : "text-mist-500")}>
                  {product.sold_count}
                </span>
              </div>
              <p className="mt-0.5 truncate font-mono text-[9px] text-mist-600">{product.product_key}</p>
              <p className="mt-0.5 text-[9px] text-mist-600">
                {money(product.min_price)}–{money(product.max_price)} · {product.independent_count} independent
              </p>
            </button>
          ))}
        </div>
      )}
      {(coverage?.imports.length ?? 0) > 0 && (
        <div className="mt-4 border-t border-ink-700 pt-3">
          <p className="eyebrow">Recent imports</p>
          <div className="mt-2 space-y-1.5">
            {coverage?.imports.slice(0, 4).map((item) => (
              <div key={item.id} className="text-[10px] text-mist-500">
                <p className="truncate text-mist-400">{item.filename}</p>
                <p>{item.imported} new · {item.updated} updated · {item.rejected} rejected · {timeAgo(item.created_at)}</p>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

function MiniStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md bg-ink-850/70 px-2 py-1.5">
      <p className="text-[9px] uppercase tracking-wider text-mist-600">{label}</p>
      <p className="numeric text-sm font-semibold text-mist-200">{value}</p>
    </div>
  );
}

function CompForm({
  initialProduct,
  onCancel,
  onSaved,
  onToast,
}: {
  initialProduct: string;
  onCancel: () => void;
  onSaved: () => Promise<void>;
  onToast: Props["onToast"];
}) {
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState({
    product_key: initialProduct,
    product_name: "",
    title: "",
    price: "",
    postage: "0",
    condition: "good",
    kind: "",
    observed_at: new Date().toISOString().slice(0, 10),
    url: "",
    notes: "",
  });
  const set = (key: keyof typeof form, value: string) => setForm((current) => ({ ...current, [key]: value }));

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!form.product_key.trim() || !form.title.trim() || !form.price) {
      onToast("Product key, title and sold price are required", "error");
      return;
    }
    setSaving(true);
    try {
      await api.addComp({
        product_key: form.product_key.trim().toLowerCase(),
        product_name: form.product_name.trim() || undefined,
        title: form.title.trim(),
        price: Number(form.price),
        postage: Number(form.postage || 0),
        condition: form.condition,
        kind: form.kind || null,
        observed_at: `${form.observed_at}T12:00:00+00:00`,
        url: form.url.trim() || undefined,
        notes: form.notes.trim() || undefined,
        verified: false,
      });
      onToast("Sold evidence added");
      await onSaved();
    } catch (error) {
      onToast(error instanceof Error ? error.message : "Could not add evidence", "error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={(event) => void submit(event)} className="grid gap-2.5 border-b border-ink-700 bg-ink-900/50 p-4 sm:grid-cols-2 lg:grid-cols-4">
      <Field label="Exact product key" value={form.product_key} onChange={(value) => set("product_key", value)} placeholder="gpu:rtx-4070-12gb" required />
      <Field label="Product name" value={form.product_name} onChange={(value) => set("product_name", value)} placeholder="RTX 4070 12GB" />
      <Field label="Sale title" value={form.title} onChange={(value) => set("title", value)} placeholder="eBay completed sale" required />
      <Field label="Sold price (GBP)" value={form.price} onChange={(value) => set("price", value)} type="number" step="0.01" required />
      <Field label="Postage" value={form.postage} onChange={(value) => set("postage", value)} type="number" step="0.01" />
      <label className="block">
        <span className="eyebrow">Condition</span>
        <select className="field mt-1" value={form.condition} onChange={(event) => set("condition", event.target.value)}>
          {CONDITIONS.map((condition) => <option key={condition} value={condition}>{conditionLabel(condition)}</option>)}
        </select>
      </label>
      <Field label="Kind" value={form.kind} onChange={(value) => set("kind", value)} placeholder="gpu" />
      <Field label="Sale date" value={form.observed_at} onChange={(value) => set("observed_at", value)} type="date" required />
      <Field label="Source URL" value={form.url} onChange={(value) => set("url", value)} placeholder="https://…" />
      <Field label="Notes" value={form.notes} onChange={(value) => set("notes", value)} placeholder="Exact spec / provenance" />
      <div className="flex items-end gap-2 sm:col-span-2">
        <button className="btn-primary !py-2" disabled={saving}>{saving ? "Saving…" : "Save evidence"}</button>
        <button type="button" className="btn-secondary !py-2" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}

function EditEvidenceDialog({
  item,
  onClose,
  onSaved,
  onToast,
}: {
  item: EvidenceItem;
  onClose: () => void;
  onSaved: () => Promise<void>;
  onToast: Props["onToast"];
}) {
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState({
    title: item.title,
    product_key: item.product_key,
    price: String(item.price),
    postage: String(item.postage),
    condition: item.condition_norm,
    kind: item.kind ?? "",
    observed_at: item.observed_at.slice(0, 10),
    notes: item.notes ?? "",
    url: item.url ?? "",
  });
  const set = (key: keyof typeof form, value: string) => setForm((current) => ({ ...current, [key]: value }));

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setSaving(true);
    try {
      await api.updateEvidence(item.id, {
        ...form,
        price: Number(form.price),
        postage: Number(form.postage || 0),
        kind: form.kind || undefined,
        observed_at: `${form.observed_at}T12:00:00+00:00`,
      });
      onToast("Evidence corrected");
      await onSaved();
    } catch (error) {
      onToast(error instanceof Error ? error.message : "Could not correct evidence", "error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-[70] grid place-items-center bg-ink-950/80 p-4 backdrop-blur-sm" role="dialog" aria-modal="true">
      <form onSubmit={(event) => void submit(event)} className="panel max-h-[90vh] w-full max-w-2xl overflow-y-auto p-5">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="eyebrow">Correct sold evidence</p>
            <h3 className="mt-1 text-sm font-semibold text-mist-100">{item.source_id}</h3>
          </div>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Close"><X className="h-4 w-4" /></button>
        </div>
        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          <Field label="Exact product key" value={form.product_key} onChange={(value) => set("product_key", value)} required />
          <Field label="Sale title" value={form.title} onChange={(value) => set("title", value)} required />
          <Field label="Sold price (GBP)" value={form.price} onChange={(value) => set("price", value)} type="number" step="0.01" required />
          <Field label="Postage" value={form.postage} onChange={(value) => set("postage", value)} type="number" step="0.01" />
          <label className="block">
            <span className="eyebrow">Condition</span>
            <select className="field mt-1" value={form.condition} onChange={(event) => set("condition", event.target.value)}>
              {CONDITIONS.map((condition) => <option key={condition} value={condition}>{conditionLabel(condition)}</option>)}
            </select>
          </label>
          <Field label="Kind" value={form.kind} onChange={(value) => set("kind", value)} />
          <Field label="Sale date" value={form.observed_at} onChange={(value) => set("observed_at", value)} type="date" />
          <Field label="Source URL" value={form.url} onChange={(value) => set("url", value)} />
          <div className="sm:col-span-2"><Field label="Notes" value={form.notes} onChange={(value) => set("notes", value)} /></div>
        </div>
        <div className="mt-4 flex items-center justify-end gap-2 border-t border-ink-700 pt-3">
          <button type="button" className="btn-secondary" onClick={onClose}>Cancel</button>
          <button className="btn-primary" disabled={saving}>{saving ? "Saving…" : "Save correction"}</button>
        </div>
      </form>
    </div>
  );
}

function Field({
  label,
  value,
  onChange,
  placeholder,
  type = "text",
  step,
  required,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  type?: string;
  step?: string;
  required?: boolean;
}) {
  return (
    <label className="block">
      <span className="eyebrow">{label}</span>
      <input
        className="field mt-1"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        type={type}
        step={step}
        required={required}
      />
    </label>
  );
}
