"use client";

import { useStatus } from "@/hooks/useStatus";
import { timeAgo } from "@/lib/format";

export function Header() {
  const { status } = useStatus();

  if (!status) return null;

  const isStale = status.updated_at
    ? Date.now() - new Date(status.updated_at).getTime() > 120_000
    : true;

  return (
    <header className="h-12 border-b border-border bg-bg-card px-4 flex items-center gap-4 text-sm">
      <div className="flex items-center gap-2">
        <span
          className={`w-2 h-2 rounded-full ${isStale ? "bg-loss" : "bg-profit"}`}
        />
        <span className="text-text-muted">
          {isStale ? "Stale" : "Live"}
        </span>
      </div>

      <span className="text-text-muted">|</span>

      <span className={`font-medium ${status.mode === "mainnet" ? "text-warning" : "text-accent"}`}>
        {status.mode.toUpperCase()}
      </span>

      {status.paper && (
        <span className="px-1.5 py-0.5 rounded text-xs bg-warning/20 text-warning">
          PAPER
        </span>
      )}

      {status.no_ai && (
        <span className="px-1.5 py-0.5 rounded text-xs bg-text-muted/20 text-text-muted">
          NO AI
        </span>
      )}

      <span className="text-text-muted">Cycle #{status.cycle_count}</span>

      <span className="ml-auto text-text-muted text-xs">
        {status.updated_at ? timeAgo(status.updated_at) : "—"}
      </span>
    </header>
  );
}
