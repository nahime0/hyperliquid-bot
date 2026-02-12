const STYLES: Record<string, string> = {
  OPEN: "bg-accent/10 text-accent border-accent/20",
  CLOSED: "bg-text-muted/10 text-text-muted border-text-muted/20",
  NEW: "bg-accent/10 text-accent border-accent/20",
  PARTIALLY_FILLED: "bg-warning/10 text-warning border-warning/20",
  FILLED: "bg-profit/10 text-profit border-profit/20",
  CANCELED: "bg-text-muted/10 text-text-muted border-text-muted/20",
};

export function StatusBadge({ status }: { status: string }) {
  const style = STYLES[status] ?? "bg-text-muted/10 text-text-muted border-text-muted/20";
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-md text-[11px] font-semibold border ${style}`}>
      {status}
    </span>
  );
}
