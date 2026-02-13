export function ScoreBar({ value, showLabel = true, size = "sm" }: { value: number; showLabel?: boolean; size?: "sm" | "md" }) {
  const pct = Math.min(100, Math.max(0, value * 100));
  const color = pct >= 70 ? "bg-profit" : pct >= 50 ? "bg-accent" : pct >= 30 ? "bg-warning" : "bg-loss";
  const textColor = pct >= 70 ? "text-profit" : pct >= 50 ? "text-accent" : pct >= 30 ? "text-warning" : "text-loss";
  const h = size === "md" ? "h-2" : "h-1.5";
  const w = size === "md" ? "max-w-20" : "max-w-16";

  return (
    <div className="flex items-center gap-1.5">
      <div className={`flex-1 ${h} bg-bg-secondary rounded-full overflow-hidden ${w}`}>
        <div className={`h-full rounded-full ${color} transition-all`} style={{ width: `${pct}%` }} />
      </div>
      {showLabel && (
        <span className={`text-xs font-mono font-semibold ${textColor}`}>
          {pct.toFixed(0)}%
        </span>
      )}
    </div>
  );
}
