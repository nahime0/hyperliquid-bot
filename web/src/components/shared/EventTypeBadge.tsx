const COLORS: Record<string, string> = {
  SIGNAL: "bg-accent/10 text-accent border-accent/20",
  AI_REVIEW: "bg-purple/10 text-purple border-purple/20",
  DEFERRED: "bg-warning/10 text-warning border-warning/20",
  RISK_APPROVED: "bg-profit/10 text-profit border-profit/20",
  RISK_BLOCKED: "bg-loss/10 text-loss border-loss/20",
  TRADE_ENTRY: "bg-profit/10 text-profit border-profit/20",
  TRADE_EXIT: "bg-loss/10 text-loss border-loss/20",
  POSITION_SCALED: "bg-accent/10 text-accent border-accent/20",
  TRAILING_UPDATE: "bg-warning/10 text-warning border-warning/20",
  SL_TP_TRIGGER: "bg-loss/10 text-loss border-loss/20",
  POSITION_ADJUSTED: "bg-purple/10 text-purple border-purple/20",
  MANUAL_CLOSE: "bg-warning/10 text-warning border-warning/20",
  PARTIAL_TP: "bg-accent/10 text-accent border-accent/20",
};

const LABELS: Record<string, string> = {
  SIGNAL: "Signal",
  AI_REVIEW: "AI Review",
  DEFERRED: "Deferred",
  RISK_APPROVED: "Approved",
  RISK_BLOCKED: "Blocked",
  TRADE_ENTRY: "Entry",
  TRADE_EXIT: "Exit",
  POSITION_SCALED: "Scale Up",
  TRAILING_UPDATE: "Trail Update",
  SL_TP_TRIGGER: "SL/TP Hit",
  POSITION_ADJUSTED: "Adjusted",
  MANUAL_CLOSE: "Manual Close",
  PARTIAL_TP: "Partial TP",
};

export function EventTypeBadge({ type }: { type: string }) {
  const color = COLORS[type] ?? "bg-text-muted/10 text-text-muted border-text-muted/20";
  const label = LABELS[type] ?? type;
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-md text-[11px] font-semibold border ${color}`}>
      {label}
    </span>
  );
}
