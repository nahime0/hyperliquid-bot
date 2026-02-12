export function DirectionBadge({ direction }: { direction: string }) {
  const isLong = direction === "LONG";
  return (
    <span
      className={`inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium ${
        isLong ? "bg-profit/15 text-profit" : "bg-loss/15 text-loss"
      }`}
    >
      {direction}
    </span>
  );
}
