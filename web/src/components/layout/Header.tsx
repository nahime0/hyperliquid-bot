"use client";

import { useStatus } from "@/hooks/useStatus";
import { timeAgo, parseTimestamp, formatPct } from "@/lib/format";

export function Header() {
  const { status } = useStatus();

  if (!status) {
    return (
      <header className="h-12 border-b border-border bg-bg-secondary px-5 flex items-center">
        <div className="skeleton w-24 h-4" />
      </header>
    );
  }

  const isStale = status.timestamp
    ? Date.now() - parseTimestamp(status.timestamp).getTime() > 120_000
    : true;

  return (
    <header className="h-12 border-b border-border bg-bg-secondary px-5 flex items-center gap-3 text-xs">
      {/* Live indicator */}
      <div className="flex items-center gap-1.5">
        <span className={`w-2 h-2 rounded-full ${isStale ? "bg-loss" : "bg-profit pulse-dot"}`} />
        <span className={`font-semibold ${isStale ? "text-loss" : "text-profit"}`}>
          {isStale ? "Offline" : "Live"}
        </span>
      </div>

      <span className="w-px h-4 bg-border" />

      {/* Cycle */}
      <span className="text-text-muted font-mono">Cycle <span className="text-text-secondary">#{status.cycle}</span></span>

      <span className="w-px h-4 bg-border" />

      {/* Mode badges */}
      <div className="flex items-center gap-1.5">
        {status.paper && (
          <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-warning/10 text-warning border border-warning/20">
            PAPER
          </span>
        )}
        {status.mode && (
          <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-bg-elevated text-text-secondary border border-border-light">
            {status.mode.toUpperCase()}
          </span>
        )}
        {status.strategy_mode && (
          <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-bg-elevated text-text-secondary border border-border-light">
            {status.strategy_mode}
          </span>
        )}
        {status.leverage && (
          <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-accent/10 text-accent border border-accent/20">
            {status.leverage}x
          </span>
        )}
        {status.no_ai && (
          <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-text-muted/10 text-text-muted border border-text-muted/20">
            NO AI
          </span>
        )}
      </div>

      {/* Alerts */}
      {status.kill_switch && (
        <>
          <span className="w-px h-4 bg-border" />
          <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-loss/15 text-loss border border-loss/30 animate-pulse">
            KILL SWITCH
          </span>
        </>
      )}
      {status.daily_paused && (
        <>
          <span className="w-px h-4 bg-border" />
          <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-warning/15 text-warning border border-warning/30">
            DAILY PAUSE
          </span>
        </>
      )}

      {/* Right side info */}
      <div className="ml-auto flex items-center gap-3">
        {status.open_positions > 0 && (
          <span className="text-text-secondary">
            <span className="text-accent font-bold">{status.open_positions}</span> open
          </span>
        )}
        <span className="w-px h-4 bg-border" />
        <span className="text-text-secondary">
          DD <span className={`font-mono font-medium ${status.drawdown_pct > 5 ? "text-loss" : "text-text-primary"}`}>
            {formatPct(-status.drawdown_pct)}
          </span>
        </span>
        <span className="w-px h-4 bg-border" />
        <span className="text-text-muted">
          {status.timestamp ? timeAgo(status.timestamp) : "—"}
        </span>
      </div>
    </header>
  );
}
