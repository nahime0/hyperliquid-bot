export function DirectionBadge({ direction }: { direction: string }) {
  const isLong = direction === "LONG";
  return (
    <span
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs font-semibold ${
        isLong
          ? "bg-profit/10 text-profit border border-profit/20"
          : "bg-loss/10 text-loss border border-loss/20"
      }`}
    >
      <span className="text-[10px]">{isLong ? "▲" : "▼"}</span>
      {direction}
    </span>
  );
}
