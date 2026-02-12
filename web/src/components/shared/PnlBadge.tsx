import { formatPnl, formatPct } from "@/lib/format";

export function PnlBadge({ value, type = "usd" }: { value: number; type?: "usd" | "pct" }) {
  const isPositive = value > 0;
  const isZero = value === 0;
  return (
    <span
      className={`font-mono text-sm font-medium ${
        isZero ? "text-text-muted" : isPositive ? "text-profit" : "text-loss"
      }`}
    >
      {type === "pct" ? formatPct(value) : formatPnl(value)}
    </span>
  );
}
