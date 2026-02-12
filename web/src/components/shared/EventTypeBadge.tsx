const COLORS: Record<string, string> = {
  SIGNAL: "bg-accent/15 text-accent",
  AI_REVIEW: "bg-purple-500/15 text-purple-400",
  DEFERRED: "bg-warning/15 text-warning",
  RISK_APPROVED: "bg-profit/15 text-profit",
  RISK_BLOCKED: "bg-loss/15 text-loss",
  TRADE_ENTRY: "bg-profit/15 text-profit",
  TRADE_EXIT: "bg-loss/15 text-loss",
  POSITION_SCALED: "bg-accent/15 text-accent",
  TRAILING_UPDATE: "bg-warning/15 text-warning",
  SL_TP_TRIGGER: "bg-loss/15 text-loss",
  POSITION_ADJUSTED: "bg-purple-500/15 text-purple-400",
};

export function EventTypeBadge({ type }: { type: string }) {
  const color = COLORS[type] ?? "bg-text-muted/15 text-text-muted";
  return (
    <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium ${color}`}>
      {type}
    </span>
  );
}
