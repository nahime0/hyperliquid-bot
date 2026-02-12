"use client";

import { Fragment, useState } from "react";
import { useOpenPositions, useClosedPositions } from "@/hooks/usePositions";
import { Card, Skeleton } from "@/components/shared/Card";
import { PnlBadge } from "@/components/shared/PnlBadge";
import { DirectionBadge } from "@/components/shared/DirectionBadge";
import { StatusBadge } from "@/components/shared/StatusBadge";
import { TimeAgo } from "@/components/shared/TimeAgo";
import { EmptyState } from "@/components/shared/EmptyState";
import { Pagination } from "@/components/shared/Pagination";
import { formatPrice, formatDuration, formatUsd, parseTimestamp } from "@/lib/format";
import type { Position } from "@/lib/types";

function PositionDetail({ position: p }: { position: Position }) {
  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-4 p-4 bg-bg-secondary rounded-lg border border-border-light text-xs">
      <div>
        <span className="text-text-muted block mb-0.5">Entry Price</span>
        <span className="font-mono font-medium text-text-primary">{formatPrice(p.entry_price)}</span>
      </div>
      <div>
        <span className="text-text-muted block mb-0.5">Quantity</span>
        <span className="font-mono font-medium text-text-primary">{p.quantity.toFixed(6)}</span>
      </div>
      <div>
        <span className="text-text-muted block mb-0.5">Stop Loss</span>
        <span className="font-mono font-medium text-text-primary">{p.stop_loss ? formatPrice(p.stop_loss) : "—"}</span>
        {p.original_sl && p.original_sl !== p.stop_loss && (
          <span className="text-text-muted block">orig: {formatPrice(p.original_sl)}</span>
        )}
      </div>
      <div>
        <span className="text-text-muted block mb-0.5">Take Profit</span>
        <span className="font-mono font-medium text-text-primary">{p.take_profit ? formatPrice(p.take_profit) : "—"}</span>
      </div>
      <div>
        <span className="text-text-muted block mb-0.5">Trailing SL</span>
        <span className="font-mono font-medium text-warning">{p.trailing_sl ? formatPrice(p.trailing_sl) : "—"}</span>
      </div>
      <div>
        <span className="text-text-muted block mb-0.5">Liquidation</span>
        <span className="font-mono font-medium text-loss">{p.liquidation_price ? formatPrice(p.liquidation_price) : "—"}</span>
      </div>
      <div>
        <span className="text-text-muted block mb-0.5">Funding Paid</span>
        <span className={`font-mono font-medium ${p.funding_paid < 0 ? "text-loss" : p.funding_paid > 0 ? "text-profit" : "text-text-muted"}`}>
          {p.funding_paid !== 0 ? formatUsd(p.funding_paid) : "—"}
        </span>
      </div>
      <div>
        <span className="text-text-muted block mb-0.5">
          {p.direction === "LONG" ? "Max Price" : "Min Price"}
        </span>
        <span className="font-mono font-medium text-text-primary">
          {p.direction === "LONG"
            ? p.max_price_seen ? formatPrice(p.max_price_seen) : "—"
            : p.min_price_seen ? formatPrice(p.min_price_seen) : "—"
          }
        </span>
      </div>
      {p.exit_price && (
        <div>
          <span className="text-text-muted block mb-0.5">Exit Price</span>
          <span className="font-mono font-medium text-text-primary">{formatPrice(p.exit_price)}</span>
        </div>
      )}
      {p.close_reason && (
        <div>
          <span className="text-text-muted block mb-0.5">Close Reason</span>
          <span className="font-medium text-text-secondary">{p.close_reason}</span>
        </div>
      )}
    </div>
  );
}

function OpenTab() {
  const { positions, isLoading } = useOpenPositions();
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  const toggle = (id: number) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  if (isLoading) return <Skeleton className="w-full h-40" />;

  if (positions.length === 0) {
    return <EmptyState title="No open positions" description="Positions will appear here when the bot enters trades" />;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-text-muted text-[11px] uppercase tracking-wider border-b border-border">
            <th className="w-6 py-2.5 px-3" />
            <th className="text-left py-2.5 px-2">Symbol</th>
            <th className="text-left py-2.5 px-2">Direction</th>
            <th className="text-right py-2.5 px-2">Entry</th>
            <th className="text-right py-2.5 px-2">Qty</th>
            <th className="text-right py-2.5 px-2">SL</th>
            <th className="text-right py-2.5 px-2">TP</th>
            <th className="text-right py-2.5 px-2">Trail SL</th>
            <th className="text-right py-2.5 px-2">Leverage</th>
            <th className="text-left py-2.5 px-2">Strategy</th>
            <th className="text-right py-2.5 px-3">Opened</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((p) => (
            <Fragment key={p.id}>
              <tr
                className="border-b border-border/40 hover:bg-bg-card-hover cursor-pointer transition-colors"
                onClick={() => toggle(p.id)}
              >
                <td className="py-2.5 px-3 text-text-muted text-xs">{expanded.has(p.id) ? "▼" : "▶"}</td>
                <td className="py-2.5 px-2 font-semibold text-text-primary">{p.symbol}</td>
                <td className="py-2.5 px-2"><DirectionBadge direction={p.direction} /></td>
                <td className="py-2.5 px-2 text-right font-mono text-text-secondary">{formatPrice(p.entry_price)}</td>
                <td className="py-2.5 px-2 text-right font-mono text-text-secondary">{p.quantity.toFixed(4)}</td>
                <td className="py-2.5 px-2 text-right font-mono text-text-muted">{p.stop_loss ? formatPrice(p.stop_loss) : "—"}</td>
                <td className="py-2.5 px-2 text-right font-mono text-text-muted">{p.take_profit ? formatPrice(p.take_profit) : "—"}</td>
                <td className="py-2.5 px-2 text-right font-mono text-warning">{p.trailing_sl ? formatPrice(p.trailing_sl) : "—"}</td>
                <td className="py-2.5 px-2 text-right"><span className="text-accent font-medium">{p.leverage}x</span></td>
                <td className="py-2.5 px-2 text-text-muted text-xs">{p.strategy}</td>
                <td className="py-2.5 px-3 text-right"><TimeAgo date={p.opened_at} /></td>
              </tr>
              {expanded.has(p.id) && (
                <tr>
                  <td colSpan={11} className="p-3">
                    <PositionDetail position={p} />
                  </td>
                </tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ClosedTab() {
  const [page, setPage] = useState(0);
  const limit = 25;
  const { positions, isLoading } = useClosedPositions(limit, page * limit);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  const toggle = (id: number) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  if (isLoading && page === 0) return <Skeleton className="w-full h-40" />;

  if (positions.length === 0 && page === 0) {
    return <EmptyState title="No closed positions" description="Position history will appear here" />;
  }

  return (
    <div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-text-muted text-[11px] uppercase tracking-wider border-b border-border">
              <th className="w-6 py-2.5 px-3" />
              <th className="text-left py-2.5 px-2">Symbol</th>
              <th className="text-left py-2.5 px-2">Direction</th>
              <th className="text-right py-2.5 px-2">Entry</th>
              <th className="text-right py-2.5 px-2">Exit</th>
              <th className="text-right py-2.5 px-2">PnL</th>
              <th className="text-right py-2.5 px-2">Duration</th>
              <th className="text-left py-2.5 px-2">Reason</th>
              <th className="text-left py-2.5 px-2">Strategy</th>
              <th className="text-right py-2.5 px-3">Closed</th>
            </tr>
          </thead>
          <tbody>
            {positions.map((p) => {
              const durationMin = p.opened_at && p.closed_at
                ? (parseTimestamp(p.closed_at).getTime() - parseTimestamp(p.opened_at).getTime()) / 60000
                : 0;
              return (
                <Fragment key={p.id}>
                  <tr
                    className="border-b border-border/40 hover:bg-bg-card-hover cursor-pointer transition-colors"
                    onClick={() => toggle(p.id)}
                  >
                    <td className="py-2.5 px-3 text-text-muted text-xs">{expanded.has(p.id) ? "▼" : "▶"}</td>
                    <td className="py-2.5 px-2 font-semibold text-text-primary">{p.symbol}</td>
                    <td className="py-2.5 px-2"><DirectionBadge direction={p.direction} /></td>
                    <td className="py-2.5 px-2 text-right font-mono text-text-secondary">{formatPrice(p.entry_price)}</td>
                    <td className="py-2.5 px-2 text-right font-mono text-text-secondary">{p.exit_price ? formatPrice(p.exit_price) : "—"}</td>
                    <td className="py-2.5 px-2 text-right">{p.pnl !== null ? <PnlBadge value={p.pnl} /> : "—"}</td>
                    <td className="py-2.5 px-2 text-right text-text-muted text-xs">{durationMin > 0 ? formatDuration(durationMin) : "—"}</td>
                    <td className="py-2.5 px-2">
                      {p.close_reason && <StatusBadge status={p.close_reason} />}
                    </td>
                    <td className="py-2.5 px-2 text-text-muted text-xs">{p.strategy}</td>
                    <td className="py-2.5 px-3 text-right">{p.closed_at ? <TimeAgo date={p.closed_at} /> : "—"}</td>
                  </tr>
                  {expanded.has(p.id) && (
                    <tr>
                      <td colSpan={10} className="p-3">
                        <PositionDetail position={p} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
      <Pagination
        page={page}
        totalPages={positions.length < limit && page === 0 ? 1 : page + 2}
        onPageChange={setPage}
      />
    </div>
  );
}

export default function PositionsPage() {
  const [tab, setTab] = useState<"open" | "closed">("open");

  return (
    <div>
      <div className="flex items-center justify-between mb-5">
        <h2 className="text-lg font-bold text-text-primary">Positions</h2>
      </div>
      <Card noPadding>
        <div className="flex border-b border-border">
          <button
            onClick={() => setTab("open")}
            className={`px-5 py-3 text-sm font-medium border-b-2 transition-colors ${
              tab === "open"
                ? "border-accent text-accent"
                : "border-transparent text-text-muted hover:text-text-primary"
            }`}
          >
            Open
          </button>
          <button
            onClick={() => setTab("closed")}
            className={`px-5 py-3 text-sm font-medium border-b-2 transition-colors ${
              tab === "closed"
                ? "border-accent text-accent"
                : "border-transparent text-text-muted hover:text-text-primary"
            }`}
          >
            Closed
          </button>
        </div>
        <div className="p-4">
          {tab === "open" ? <OpenTab /> : <ClosedTab />}
        </div>
      </Card>
    </div>
  );
}
