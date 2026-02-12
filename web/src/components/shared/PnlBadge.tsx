import { formatPnl, formatPct } from "@/lib/format";

export function PnlBadge({ value, type = "usd" }: { value: number; type?: "usd" | "pct" }) {
  const isPositive = value >= 0;
  return (
    <span className={`font-mono text-sm ${isPositive ? "text-profit" : "text-loss"}`}>
      {type === "pct" ? formatPct(value) : formatPnl(value)}
    </span>
  );
}
