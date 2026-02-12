export function formatUsd(value: number): string {
  return (value ?? 0).toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

export function formatPrice(value: number): string {
  const v = value ?? 0;
  if (v >= 1000) return v.toLocaleString("en-US", { maximumFractionDigits: 2 });
  if (v >= 1) return v.toLocaleString("en-US", { maximumFractionDigits: 4 });
  if (v >= 0.01) return v.toLocaleString("en-US", { maximumFractionDigits: 6 });
  return v.toLocaleString("en-US", { maximumFractionDigits: 8 });
}

export function formatPct(value: number): string {
  const v = value ?? 0;
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

export function formatPnl(value: number): string {
  const v = value ?? 0;
  const sign = v >= 0 ? "+" : "";
  return `${sign}${formatUsd(v)}`;
}

export function parseTimestamp(isoString: string): Date {
  // DB timestamps end with Z, bot_status uses +00:00 — both are valid ISO
  // Only append Z if there's no timezone indicator at all
  if (/[Zz]$/.test(isoString) || /[+-]\d{2}:\d{2}$/.test(isoString)) {
    return new Date(isoString);
  }
  return new Date(isoString + "Z");
}

export function timeAgo(isoString: string): string {
  const now = Date.now();
  const then = parseTimestamp(isoString).getTime();
  if (isNaN(then)) return "—";
  const diff = now - then;

  if (diff < 0) return "just now";
  const seconds = Math.floor(diff / 1000);
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

export function formatDuration(minutes: number): string {
  if (minutes < 60) return `${Math.round(minutes)}m`;
  const h = Math.floor(minutes / 60);
  const m = Math.round(minutes % 60);
  return m > 0 ? `${h}h ${m}m` : `${h}h`;
}
