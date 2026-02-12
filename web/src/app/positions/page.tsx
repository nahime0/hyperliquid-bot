"use client";

import { useState } from "react";
import { useOpenPositions, useClosedPositions } from "@/hooks/usePositions";
import { useStatus } from "@/hooks/useStatus";
import { Card } from "@/components/shared/Card";
import { PnlBadge } from "@/components/shared/PnlBadge";
import { DirectionBadge } from "@/components/shared/DirectionBadge";
import { TimeAgo } from "@/components/shared/TimeAgo";
import { formatPrice, formatPct, formatDuration, parseTimestamp } from "@/lib/format";

function OpenTab() {
  const { positions } = useOpenPositions();
  const { status } = useStatus();

  const getMidPrice = (symbol: string): number | null => {
    if (!status?.strategies) return null;
    const strats = status.strategies as Record<string, unknown>;
    const subs = strats.sub_strategies as Record<string, { signals?: Record<string, { price?: number }> }> | undefined;
    if (subs) {
      for (const sub of Object.values(subs)) {
        if (sub.signals?.[symbol]?.price) return sub.signals[symbol].price;
      }
    }
    const signals = strats.signals as Record<string, { price?: number }> | undefined;
    if (signals?.[symbol]?.price) return signals[symbol].price;
    return null;
  };

  if (positions.length === 0) {
    return <p className="text-text-muted text-sm py-8 text-center">No open positions</p>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-text-muted text-xs border-b border-border">
            <th className="text-left pb-2 pr-3">Symbol</th>
            <th className="text-left pb-2 pr-3">Direction</th>
            <th className="text-right pb-2 pr-3">Entry</th>
            <th className="text-right pb-2 pr-3">Current</th>
            <th className="text-right pb-2 pr-3">Qty</th>
            <th className="text-right pb-2 pr-3">PnL</th>
            <th className="text-right pb-2 pr-3">PnL %</th>
            <th className="text-right pb-2 pr-3">SL</th>
            <th className="text-right pb-2 pr-3">TP</th>
            <th className="text-right pb-2 pr-3">Trail SL</th>
            <th className="text-right pb-2 pr-3">Lev</th>
            <th className="text-left pb-2 pr-3">Strategy</th>
            <th className="text-right pb-2">Opened</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((p) => {
            const mid = getMidPrice(p.symbol);
            let pnl = 0;
            let pnlPct = 0;
            if (mid) {
              pnl = p.direction === "LONG"
                ? (mid - p.entry_price) * p.quantity
                : (p.entry_price - mid) * p.quantity;
              pnlPct = p.direction === "LONG"
                ? ((mid - p.entry_price) / p.entry_price) * 100
                : ((p.entry_price - mid) / p.entry_price) * 100;
            }
            return (
              <tr key={p.id} className="border-b border-border/50 hover:bg-bg-card-hover">
                <td className="py-2 pr-3 font-medium">{p.symbol}</td>
                <td className="py-2 pr-3"><DirectionBadge direction={p.direction} /></td>
                <td className="py-2 pr-3 text-right font-mono">{formatPrice(p.entry_price)}</td>
                <td className="py-2 pr-3 text-right font-mono">{mid ? formatPrice(mid) : "—"}</td>
                <td className="py-2 pr-3 text-right font-mono">{p.quantity.toFixed(6)}</td>
                <td className="py-2 pr-3 text-right">{mid ? <PnlBadge value={pnl} /> : "—"}</td>
                <td className="py-2 pr-3 text-right">{mid ? <PnlBadge value={pnlPct} type="pct" /> : "—"}</td>
                <td className="py-2 pr-3 text-right font-mono text-text-muted">{p.stop_loss ? formatPrice(p.stop_loss) : "—"}</td>
                <td className="py-2 pr-3 text-right font-mono text-text-muted">{p.take_profit ? formatPrice(p.take_profit) : "—"}</td>
                <td className="py-2 pr-3 text-right font-mono text-warning">{p.trailing_sl ? formatPrice(p.trailing_sl) : "—"}</td>
                <td className="py-2 pr-3 text-right">{p.leverage}x</td>
                <td className="py-2 pr-3 text-text-muted">{p.strategy}</td>
                <td className="py-2 text-right"><TimeAgo date={p.opened_at} /></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function ClosedTab() {
  const [page, setPage] = useState(0);
  const limit = 25;
  const { positions } = useClosedPositions(limit, page * limit);

  if (positions.length === 0 && page === 0) {
    return <p className="text-text-muted text-sm py-8 text-center">No closed positions</p>;
  }

  return (
    <div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-text-muted text-xs border-b border-border">
              <th className="text-left pb-2 pr-3">Symbol</th>
              <th className="text-left pb-2 pr-3">Direction</th>
              <th className="text-right pb-2 pr-3">Entry</th>
              <th className="text-right pb-2 pr-3">Exit</th>
              <th className="text-right pb-2 pr-3">PnL</th>
              <th className="text-right pb-2 pr-3">Duration</th>
              <th className="text-left pb-2 pr-3">Reason</th>
              <th className="text-left pb-2 pr-3">Strategy</th>
              <th className="text-right pb-2">Closed</th>
            </tr>
          </thead>
          <tbody>
            {positions.map((p) => {
              const durationMin = p.opened_at && p.closed_at
                ? (parseTimestamp(p.closed_at).getTime() - parseTimestamp(p.opened_at).getTime()) / 60000
                : 0;
              return (
                <tr key={p.id} className="border-b border-border/50 hover:bg-bg-card-hover">
                  <td className="py-2 pr-3 font-medium">{p.symbol}</td>
                  <td className="py-2 pr-3"><DirectionBadge direction={p.direction} /></td>
                  <td className="py-2 pr-3 text-right font-mono">{formatPrice(p.entry_price)}</td>
                  <td className="py-2 pr-3 text-right font-mono">{p.exit_price ? formatPrice(p.exit_price) : "—"}</td>
                  <td className="py-2 pr-3 text-right">{p.pnl !== null ? <PnlBadge value={p.pnl} /> : "—"}</td>
                  <td className="py-2 pr-3 text-right text-text-muted">{durationMin > 0 ? formatDuration(durationMin) : "—"}</td>
                  <td className="py-2 pr-3 text-text-muted text-xs">{p.close_reason ?? "—"}</td>
                  <td className="py-2 pr-3 text-text-muted">{p.strategy}</td>
                  <td className="py-2 text-right">{p.closed_at ? <TimeAgo date={p.closed_at} /> : "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="flex gap-2 mt-4 justify-center">
        <button
          onClick={() => setPage(Math.max(0, page - 1))}
          disabled={page === 0}
          className="px-3 py-1 rounded text-sm border border-border disabled:opacity-30 hover:bg-bg-card-hover"
        >
          Prev
        </button>
        <span className="px-3 py-1 text-sm text-text-muted">Page {page + 1}</span>
        <button
          onClick={() => setPage(page + 1)}
          disabled={positions.length < limit}
          className="px-3 py-1 rounded text-sm border border-border disabled:opacity-30 hover:bg-bg-card-hover"
        >
          Next
        </button>
      </div>
    </div>
  );
}

export default function PositionsPage() {
  const [tab, setTab] = useState<"open" | "closed">("open");

  return (
    <div>
      <h2 className="text-xl font-bold mb-4">Positions</h2>
      <Card>
        <div className="flex gap-4 mb-4 border-b border-border">
          <button
            onClick={() => setTab("open")}
            className={`pb-2 text-sm font-medium border-b-2 transition-colors ${
              tab === "open" ? "border-accent text-accent" : "border-transparent text-text-muted hover:text-text-primary"
            }`}
          >
            Open
          </button>
          <button
            onClick={() => setTab("closed")}
            className={`pb-2 text-sm font-medium border-b-2 transition-colors ${
              tab === "closed" ? "border-accent text-accent" : "border-transparent text-text-muted hover:text-text-primary"
            }`}
          >
            Closed
          </button>
        </div>
        {tab === "open" ? <OpenTab /> : <ClosedTab />}
      </Card>
    </div>
  );
}
