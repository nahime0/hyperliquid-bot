const STYLES: Record<string, string> = {
  BUY: "text-profit",
  SHORT: "text-loss",
  SELL: "text-warning",
  CLOSE: "text-text-secondary",
};

export function SideBadge({ side }: { side: string }) {
  const style = STYLES[side] ?? "text-text-muted";
  return (
    <span className={`text-xs font-bold ${style}`}>
      {side}
    </span>
  );
}
