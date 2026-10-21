import { useState, type ReactNode } from "react";
import {
  Box,
  Check,
  Cpu,
  Gamepad2,
  HardDrive,
  Laptop,
  MemoryStick,
  Monitor,
  Radar,
  Smartphone,
  Tablet,
  TriangleAlert,
  X,
} from "lucide-react";
import { CATEGORY_LABELS, confidenceTone, marketplaceMeta } from "../lib/format";

export function cn(...values: Array<string | false | null | undefined>) {
  return values.filter(Boolean).join(" ");
}

export function Logo({ compact = false }: { compact?: boolean }) {
  return (
    <div className="flex items-center gap-2.5">
      <span className="relative flex h-9 w-9 items-center justify-center rounded-xl border border-lime-400/25 bg-lime-400/10">
        <Radar className="h-5 w-5 text-lime-400" strokeWidth={1.8} />
        <span className="absolute right-1.5 top-1.5 h-1.5 w-1.5 rounded-full bg-lime-400 shadow-[0_0_8px_2px_rgba(61,220,132,0.5)]" />
      </span>
      {!compact && (
        <span className="font-mono text-[15px] font-semibold tracking-[0.18em] text-mist-100">
          RIG<span className="text-lime-400">RADAR</span>
        </span>
      )}
    </div>
  );
}

export function MarketBadge({ marketplace, label, className = "" }: { marketplace: string; label?: string; className?: string }) {
  const meta = marketplaceMeta(marketplace);
  return (
    <span className={cn("chip backdrop-blur-md", meta.className, className)}>
      <span className={cn("h-1.5 w-1.5 rounded-full", meta.dot)} />
      {label ?? meta.label}
    </span>
  );
}

export function StatusPill({ state, running }: { state: string; running?: boolean }) {
  const normalised = state?.toLowerCase();
  const map: Record<string, { label: string; classes: string; dot: string }> = {
    healthy: { label: "Healthy", classes: "border-lime-400/25 bg-lime-400/10 text-lime-300", dot: "bg-lime-400" },
    ok: { label: "Healthy", classes: "border-lime-400/25 bg-lime-400/10 text-lime-300", dot: "bg-lime-400" },
    scanning: { label: "Scanning", classes: "border-sky-400/25 bg-sky-400/10 text-sky-300", dot: "bg-sky-400" },
    degraded: { label: "Degraded", classes: "border-ember/30 bg-ember/10 text-ember", dot: "bg-ember" },
    blocked: { label: "Blocked", classes: "border-rose/30 bg-rose/10 text-rose", dot: "bg-rose" },
    error: { label: "Error", classes: "border-rose/30 bg-rose/10 text-rose", dot: "bg-rose" },
    paused: { label: "Paused", classes: "border-ink-500 bg-ink-700/40 text-mist-400", dot: "bg-mist-500" },
    not_configured: { label: "Needs API key", classes: "border-ember/25 bg-ember/5 text-ember", dot: "bg-ember" },
    unavailable: { label: "Unavailable", classes: "border-ink-500 bg-ink-700/40 text-mist-400", dot: "bg-mist-500" },
    idle: { label: "Idle", classes: "border-ink-500 bg-ink-700/40 text-mist-400", dot: "bg-mist-500" },
    failed: { label: "Failed", classes: "border-rose/30 bg-rose/10 text-rose", dot: "bg-rose" },
  };
  const meta = map[running ? "scanning" : normalised] ?? map.idle;
  return (
    <span className={cn("chip", meta.classes)}>
      <span className={cn("h-1.5 w-1.5 rounded-full", meta.dot, running && "animate-pulse")} />
      {meta.label}
    </span>
  );
}

const CATEGORY_ICONS: Record<string, typeof Cpu> = {
  gpu: Cpu,
  cpu: Cpu,
  ram: MemoryStick,
  ssd: HardDrive,
  motherboard: Cpu,
  pc: Monitor,
  laptop: Laptop,
  console: Gamepad2,
  monitor: Monitor,
  phone: Smartphone,
  tablet: Tablet,
  other: Box,
};

export function ProductImage({
  src,
  alt,
  category,
  className = "",
}: {
  src: string | null;
  alt: string;
  category: string | null;
  className?: string;
}) {
  const [failed, setFailed] = useState(false);
  const Icon = CATEGORY_ICONS[category ?? "other"] ?? Box;
  if (!src || failed) {
    return (
      <div
        className={cn(
          "relative flex items-center justify-center overflow-hidden bg-gradient-to-br from-ink-700 via-ink-800 to-ink-900",
          className,
        )}
      >
        <div className="absolute inset-0 grid-noise opacity-60" />
        <div className="relative flex flex-col items-center gap-2 text-mist-500">
          <Icon className="h-8 w-8" strokeWidth={1.4} />
          <span className="font-mono text-[9px] uppercase tracking-[0.2em]">
            {CATEGORY_LABELS[category ?? "other"] ?? "Hardware"}
          </span>
        </div>
      </div>
    );
  }
  return (
    <img
      src={src}
      alt={alt}
      loading="lazy"
      referrerPolicy="no-referrer"
      onError={() => setFailed(true)}
      className={cn("h-full w-full object-cover", className)}
    />
  );
}

export function ConfidenceMeter({ value, compact = false }: { value: number; compact?: boolean }) {
  const tone = confidenceTone(value);
  return (
    <div className="w-full">
      <div className="mb-1 flex items-center justify-between gap-2">
        {!compact && <span className="text-[11px] text-mist-400">Confidence</span>}
        <span className={cn("numeric text-[11px] font-medium", tone.text)}>{Math.round(value * 100)}%</span>
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-ink-700">
        <div
          className={cn("h-full rounded-full transition-all duration-500", tone.bar)}
          style={{ width: `${Math.max(4, Math.min(100, value * 100))}%` }}
        />
      </div>
    </div>
  );
}

export function ScoreRing({ score, size = 44 }: { score: number; size?: number }) {
  const radius = (size - 6) / 2;
  const circumference = 2 * Math.PI * radius;
  const progress = Math.max(0, Math.min(100, score)) / 100;
  const tone = score >= 68 ? "#3ddc84" : score >= 55 ? "#f5b45b" : "#f27f7f";
  return (
    <div className="relative inline-flex items-center justify-center" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke="#203328" strokeWidth={3} />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={tone}
          strokeWidth={3}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={circumference * (1 - progress)}
        />
      </svg>
      <span className="numeric absolute text-[10px] font-semibold text-mist-200">{Math.round(score)}</span>
    </div>
  );
}

export function SkeletonCard() {
  return (
    <div className="panel overflow-hidden">
      <div className="skeleton aspect-[16/9] w-full rounded-none" />
      <div className="space-y-3 p-5">
        <div className="skeleton h-3 w-24" />
        <div className="skeleton h-5 w-4/5" />
        <div className="skeleton h-14 w-full" />
        <div className="skeleton h-2 w-full" />
      </div>
    </div>
  );
}

export function EmptyRadar({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="panel relative overflow-hidden px-6 py-16 text-center">
      <div className="pointer-events-none absolute inset-0 flex items-center justify-center opacity-40">
        <div className="h-72 w-72 rounded-full border border-lime-400/10" />
        <div className="absolute h-48 w-48 rounded-full border border-lime-400/10" />
        <div className="absolute h-24 w-24 rounded-full border border-lime-400/10" />
      </div>
      <div className="relative mx-auto flex max-w-md flex-col items-center gap-4">
        <span className="relative flex h-14 w-14 items-center justify-center rounded-2xl border border-lime-400/20 bg-lime-400/5">
          <Radar className="h-7 w-7 text-lime-400" />
          <span className="absolute inset-0 overflow-hidden rounded-2xl">
            <span className="absolute left-0 h-px w-full animate-scanline bg-gradient-to-r from-transparent via-lime-400/60 to-transparent" />
          </span>
        </span>
        <h3 className="text-lg font-semibold text-mist-100">{title}</h3>
        <p className="text-sm leading-relaxed text-mist-400 text-balance">{description}</p>
        {action}
      </div>
    </div>
  );
}

export type ToastData = { id: number; message: string; tone: "success" | "error" | "info" };

export function Toast({ toast, onClose }: { toast: ToastData; onClose: () => void }) {
  const Icon = toast.tone === "error" ? TriangleAlert : Check;
  return (
    <div
      role="status"
      className={cn(
        "panel pointer-events-auto flex max-w-sm items-start gap-3 border px-4 py-3 shadow-glow",
        toast.tone === "error" ? "border-rose/30" : "border-lime-400/25",
      )}
    >
      <span
        className={cn(
          "mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full",
          toast.tone === "error" ? "bg-rose/15 text-rose" : "bg-lime-400/15 text-lime-300",
        )}
      >
        <Icon className="h-3.5 w-3.5" />
      </span>
      <p className="flex-1 text-sm leading-snug text-mist-200">{toast.message}</p>
      <button onClick={onClose} className="text-mist-500 transition hover:text-mist-200" aria-label="Dismiss">
        <X className="h-4 w-4" />
      </button>
    </div>
  );
}

export function SectionHeading({
  eyebrow,
  title,
  description,
  action,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        {eyebrow && <p className="eyebrow mb-1.5">{eyebrow}</p>}
        <h2 className="text-xl font-semibold tracking-tight text-mist-100">{title}</h2>
        {description && <p className="mt-1 max-w-2xl text-sm text-mist-400">{description}</p>}
      </div>
      {action}
    </div>
  );
}
