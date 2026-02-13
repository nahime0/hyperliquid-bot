"use client";

import { use } from "react";
import useSWR from "swr";
import Link from "next/link";
import { Card, Skeleton } from "@/components/shared/Card";
import { PnlBadge } from "@/components/shared/PnlBadge";
import { DirectionBadge } from "@/components/shared/DirectionBadge";
import { SideBadge } from "@/components/shared/SideBadge";
import { EventTypeBadge } from "@/components/shared/EventTypeBadge";
import { ScoreBar } from "@/components/shared/ScoreBar";
import { StatusBadge } from "@/components/shared/StatusBadge";
import { TimeAgo } from "@/components/shared/TimeAgo";
import { EmptyState } from "@/components/shared/EmptyState";
import { formatPrice, formatUsd } from "@/lib/format";
import type { Trade, Position, Event, Coin } from "@/lib/types";

const fetcher = (url: string) => fetch(url).then((r) => r.json());

interface CoinDetail {
  coin: Coin;
  trades: Trade[];
  positions: Position[];
  events: Event[];
}

export default function CoinDetailPage({ params }: { params: Promise<{ symbol: string }> }) {
  const { symbol } = use(params);
  const { data, isLoading } = useSWR<CoinDetail>(`/api/coins/${symbol}`, fetcher, { refreshInterval: 5000 });

  if (isLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="w-48 h-8" />
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <Skeleton className="w-full h-64 rounded-xl" />
          <Skeleton className="w-full h-64 rounded-xl" />
        </div>
      </div>
    );
  }

  if (!data?.coin) {
    return (
      <div className="flex flex-col items-center justify-center py-20">
        <p className="text-loss font-medium">Coin not found</p>
        <Link href="/coins" className="text-accent text-sm mt-2 hover:underline">Back to coins</Link>
      </div>
    );
  }

  const { coin, trades, positions, events } = data;
  const openPositions = positions.filter(p => p.status === "OPEN");
  const closedPositions = positions.filter(p => p.status === "CLOSED");
  const totalPnl = positions.reduce((sum, p) => sum + (p.pnl ?? 0), 0);

  return (
    <div>
      {/* Header */}
      <div className="flex items-center gap-3 mb-6">
        <Link href="/coins" className="text-text-muted hover:text-accent transition-colors text-sm">
          &larr; Coins
        </Link>
        <h2 className="text-lg font-bold text-text-primary">{symbol}</h2>
        <span className={`px-2 py-0.5 rounded-md text-[11px] font-semibold border ${
          coin.is_active
            ? "bg-profit/10 text-profit border-profit/20"
            : "bg-text-muted/10 text-text-muted border-text-muted/20"
        }`}>
          {coin.is_active ? "Active" : "Inactive"}
        </span>
        {coin.avg_volume_24h > 0 && (
          <span className="text-xs text-text-muted">Vol 24h: ${formatPrice(coin.avg_volume_24h)}</span>
        )}
      </div>

      {/* Quick stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
        <div className="bg-bg-card border border-border rounded-xl p-3">
          <span className="text-[11px] text-text-muted uppercase">Positions</span>
          <div className="text-lg font-bold font-mono text-text-primary mt-0.5">{positions.length}</div>
          {openPositions.length > 0 && <span className="text-[11px] text-accent">{openPositions.length} open</span>}
        </div>
        <div className="bg-bg-card border border-border rounded-xl p-3">
          <span className="text-[11px] text-text-muted uppercase">Trades</span>
          <div className="text-lg font-bold font-mono text-text-primary mt-0.5">{trades.length}</div>
        </div>
        <div className="bg-bg-card border border-border rounded-xl p-3">
          <span className="text-[11px] text-text-muted uppercase">Total PnL</span>
          <div className="mt-0.5"><PnlBadge value={totalPnl} /></div>
        </div>
        <div className="bg-bg-card border border-border rounded-xl p-3">
          <span className="text-[11px] text-text-muted uppercase">Events</span>
          <div className="text-lg font-bold font-mono text-text-primary mt-0.5">{events.length}</div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
        {/* Event Timeline */}
        <Card title={`Events (${events.length})`} noPadding>
          <div className="max-h-96 overflow-y-auto divide-y divide-border/30">
            {events.length === 0 ? (
              <div className="p-4"><EmptyState title="No events" /></div>
            ) : (
              events.map((e) => (
                <div key={e.id} className="flex items-start gap-2.5 py-2.5 px-4">
                  <EventTypeBadge type={e.event_type} />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 text-xs">
                      {e.action && <span className="font-semibold text-text-primary">{e.action}</span>}
                      {e.event_type === "SIGNAL" && e.confidence !== null && (
                        <div className="w-20"><ScoreBar value={e.confidence} /></div>
                      )}
                      <span className="text-text-muted font-mono">cycle #{e.cycle}</span>
                      <span className="ml-auto"><TimeAgo date={e.timestamp} /></span>
                    </div>
                    {e.reasoning && (
                      <p className="text-xs text-text-muted mt-0.5 line-clamp-2">{e.reasoning}</p>
                    )}
                  </div>
                </div>
              ))
            )}
          </div>
        </Card>

        {/* Position History */}
        <Card title={`Positions (${positions.length})`} noPadding>
          <div className="max-h-96 overflow-y-auto divide-y divide-border/30">
            {positions.length === 0 ? (
              <div className="p-4"><EmptyState title="No positions" /></div>
            ) : (
              positions.map((p) => (
                <div key={p.id} className="flex items-center gap-3 py-2.5 px-4 text-sm">
                  <DirectionBadge direction={p.direction} />
                  <StatusBadge status={p.status} />
                  <span className="font-mono text-text-secondary text-xs">{formatPrice(p.entry_price)}</span>
                  {p.exit_price && (
                    <span className="text-text-muted text-xs">&rarr; {formatPrice(p.exit_price)}</span>
                  )}
                  {p.pnl !== null && <PnlBadge value={p.pnl} />}
                  {p.close_reason && <span className="text-[10px] text-text-muted">{p.close_reason}</span>}
                  <span className="ml-auto"><TimeAgo date={p.opened_at} /></span>
                </div>
              ))
            )}
          </div>
        </Card>
      </div>

      {/* Trade History */}
      <Card title={`Trades (${trades.length})`} noPadding>
        {trades.length === 0 ? (
          <div className="p-4"><EmptyState title="No trades for this coin" /></div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-text-muted text-[11px] uppercase tracking-wider border-b border-border">
                  <th className="text-left py-2.5 px-4">Side</th>
                  <th className="text-right py-2.5 px-2">Price</th>
                  <th className="text-right py-2.5 px-2">Qty</th>
                  <th className="text-right py-2.5 px-2">Fee</th>
                  <th className="text-right py-2.5 px-2">PnL</th>
                  <th className="text-left py-2.5 px-2">Strategy</th>
                  <th className="text-right py-2.5 px-4">Time</th>
                </tr>
              </thead>
              <tbody>
                {trades.map((t) => (
                  <tr key={t.id} className="border-b border-border/40 hover:bg-bg-card-hover transition-colors">
                    <td className="py-2.5 px-4"><SideBadge side={t.side} /></td>
                    <td className="py-2.5 px-2 text-right font-mono text-text-secondary">{formatPrice(t.price)}</td>
                    <td className="py-2.5 px-2 text-right font-mono text-text-secondary">{t.quantity.toFixed(4)}</td>
                    <td className="py-2.5 px-2 text-right font-mono text-text-muted">
                      {t.fee > 0 ? formatUsd(t.fee) : "—"}
                    </td>
                    <td className="py-2.5 px-2 text-right">
                      {t.pnl !== null ? <PnlBadge value={t.pnl} /> : <span className="text-text-muted">—</span>}
                    </td>
                    <td className="py-2.5 px-2 text-text-muted text-xs">{t.strategy}</td>
                    <td className="py-2.5 px-4 text-right"><TimeAgo date={t.timestamp} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
