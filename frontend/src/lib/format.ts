export function money(value: number | null | undefined, compact = false): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  if (compact && Math.abs(value) >= 1000) {
    return `£${(value / 1000).toFixed(value >= 10000 ? 0 : 1)}k`;
  }
  return new Intl.NumberFormat("en-GB", {
    style: "currency",
    currency: "GBP",
    maximumFractionDigits: value % 1 === 0 ? 0 : 2,
  }).format(value);
}

export function pct(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

export function timeAgo(value: string | null | undefined): string {
  if (!value) return "never";
  const then = new Date(value).getTime();
  if (Number.isNaN(then)) return "unknown";
  const seconds = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (seconds < 45) return "just now";
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h ago`;
  if (seconds < 86400 * 7) return `${Math.round(seconds / 86400)}d ago`;
  return new Date(value).toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}

export function timeUntil(value: string | null | undefined): string {
  if (!value) return "";
  const target = new Date(value).getTime();
  if (Number.isNaN(target)) return "";
  const seconds = Math.max(0, Math.round((target - Date.now()) / 1000));
  if (seconds < 45) return "now";
  if (seconds < 3600) return `in ${Math.round(seconds / 60)}m`;
  if (seconds < 86400) return `in ${Math.round(seconds / 3600)}h`;
  return `in ${Math.round(seconds / 86400)}d`;
}

export function clockTime(value: string | null | undefined): string {
  if (!value) return "—";
  return new Date(value).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
}

export function formatDuration(ms: number | null | undefined): string {
  if (!ms) return "—";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

export function daysAgo(hours: number): string {
  if (hours < 1) return "just now";
  if (hours < 24) return `${Math.round(hours)}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

export const CATEGORY_LABELS: Record<string, string> = {
  gpu: "Graphics card",
  cpu: "Processor",
  ram: "Memory",
  ssd: "Storage",
  motherboard: "Motherboard",
  pc: "Complete PC",
  laptop: "Laptop",
  console: "Console",
  monitor: "Monitor",
  phone: "Phone",
  tablet: "Tablet",
  networking: "Networking",
  other: "Tech",
};

export const MARKETPLACE_META: Record<string, { label: string; className: string; dot: string }> = {
  vinted: { label: "Vinted", className: "bg-[#0f3d39] text-[#7fe7dd] border-[#1d5c55]", dot: "bg-[#5eead4]" },
  gumtree: { label: "Gumtree", className: "bg-[#13301f] text-[#9be6b4] border-[#24533a]", dot: "bg-[#77dd97]" },
  ebay: { label: "eBay UK", className: "bg-[#2f2416] text-[#f5c27b] border-[#58401f]", dot: "bg-[#f5b45b]" },
  facebook: { label: "Facebook", className: "bg-[#1a2747] text-[#9db8ff] border-[#2f4478]", dot: "bg-[#8aa8ff]" },
  cex: { label: "CeX", className: "bg-[#3a1c24] text-[#f5a3b3] border-[#5c2a36]", dot: "bg-[#f27f7f]" },
};

export function marketplaceMeta(key: string) {
  return (
    MARKETPLACE_META[key] ?? {
      label: key ? key.charAt(0).toUpperCase() + key.slice(1) : "Unknown",
      className: "bg-ink-700 text-mist-300 border-ink-600",
      dot: "bg-mist-400",
    }
  );
}

export function confidenceTone(value: number): { label: string; text: string; bar: string } {
  if (value >= 0.7) return { label: "High confidence", text: "text-lime-400", bar: "bg-lime-400" };
  if (value >= 0.5) return { label: "Medium confidence", text: "text-ember", bar: "bg-ember" };
  return { label: "Low confidence", text: "text-rose", bar: "bg-rose" };
}

export function scoreGrade(score: number): string {
  if (score >= 80) return "A+";
  if (score >= 68) return "A";
  if (score >= 55) return "B";
  return "C";
}

export function methodLabel(method: string | null): string {
  switch (method) {
    case "sold_comps":
      return "Completed sales";
    case "mixed_comps":
      return "Sales + asking prices";
    case "cex_anchor":
      return "CeX anchored";
    case "component_model":
      return "Component model";
    case "live_asking_comps":
      return "Live asking prices";
    case "insufficient_data":
      return "Insufficient data";
    default:
      return "Modelled";
  }
}

export function conditionLabel(value: string): string {
  return (
    {
      new: "New",
      open_box: "Open box",
      like_new: "Like new",
      excellent: "Excellent",
      very_good: "Very good",
      good: "Good",
      used: "Used",
      fair: "Fair",
      poor: "Poor",
      faulty: "Faulty",
      unknown: "Used",
    }[value] ?? value
  );
}
