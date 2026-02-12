"use client";

import { use } from "react";
import useSWR from "swr";
import Link from "next/link";
import { Card } from "@/components/shared/Card";
import { PnlBadge } from "@/components/shared/PnlBadge";
import { DirectionBadge } from "@/components/shared/DirectionBadge";
import { EventTypeBadge } from "@/components/shared/EventTypeBadge";
import { TimeAgo } from "@/components/shared/TimeAgo";
import { formatPrice } from "@/lib/format";
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

  if (isLoading) return <div className="text-text-muted">Loading...</div>;
  if (!data?.coin) return <div className="text-loss">Coin not found</div>;

  const { coin, trades, positions, events } = data;

  return (
    <div>
      <div className="flex items-center gap-3 mb-6">
        <Link href="/coins" className="text-text-muted hover:text-accent text-sm">&larr; Coins</Link>
        <h2 className="text-xl font-bold">{symbol}</h2>
        <span className={`px-2 py-0.5 rounded text-xs ${coin.is_active ? "bg-profit/15 text-profit" : "bg-text-muted/15 text-text-muted"}`}>
          {coin.is_active ? "Active" : "Inactive"}
        </span>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
        {/* Event Timeline */}
        <Card title={`Events (${events.length})`}>
          <div className="max-h-96 overflow-y-auto space-y-2">
            {events.length === 0 && <p className="text-text-muted text-sm">No events</p>}
            {events.map((e) => (
              <div key={e.id} className="flex items-start gap-2 py-1.5 border-b border-border/30">
                <EventTypeBadge type={e.event_type} />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 text-xs">
                    {e.action && <span className="font-medium">{e.action}</span>}
                    <span className="text-text-muted">cycle #{e.cycle}</span>
                    <TimeAgo date={e.timestamp} />
                  </div>
                  {e.reasoning && (
                    <p className="text-xs text-text-muted mt-0.5 truncate">{e.reasoning}</p>
                  )}
                </div>
              </div>
            ))}
          </div>
        </Card>

        {/* Position History */}
        <Card title={`Positions (${positions.length})`}>
          <div className="max-h-96 overflow-y-auto space-y-2">
            {positions.length === 0 && <p className="text-text-muted text-sm">No positions</p>}
            {positions.map((p) => (
              <div key={p.id} className="flex items-center gap-3 py-1.5 border-b border-border/30 text-sm">
                <DirectionBadge direction={p.direction} />
                <span className={`px-1.5 py-0.5 rounded text-xs ${p.status === "OPEN" ? "bg-accent/15 text-accent" : "bg-text-muted/15 text-text-muted"}`}>
                  {p.status}
                </span>
                <span className="font-mono">{formatPrice(p.entry_price)}</span>
                {p.exit_price && <span className="text-text-muted">&rarr; {formatPrice(p.exit_price)}</span>}
                {p.pnl !== null && <PnlBadge value={p.pnl} />}
                <span className="ml-auto"><TimeAgo date={p.opened_at} /></span>
              </div>
            ))}
          </div>
        </Card>
      </div>

      {/* Trade History */}
      <Card title={`Trades (${trades.length})`}>
        {trades.length === 0 ? (
          <p className="text-text-muted text-sm">No trades</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-text-muted text-xs border-b border-border">
                  <th className="text-left pb-2">Side</th>
                  <th className="text-right pb-2">Price</th>
                  <th className="text-right pb-2">Qty</th>
                  <th className="text-right pb-2">PnL</th>
                  <th className="text-left pb-2">Strategy</th>
                  <th className="text-right pb-2">Time</th>
                </tr>
              </thead>
              <tbody>
                {trades.map((t) => (
                  <tr key={t.id} className="border-b border-border/50 hover:bg-bg-card-hover">
                    <td className="py-2">
                      <span className={`text-xs font-medium ${t.side === "BUY" ? "text-profit" : t.side === "SHORT" ? "text-loss" : "text-text-muted"}`}>
                        {t.side}
                      </span>
                    </td>
                    <td className="py-2 text-right font-mono">{formatPrice(t.price)}</td>
                    <td className="py-2 text-right font-mono">{t.quantity.toFixed(6)}</td>
                    <td className="py-2 text-right">{t.pnl !== null ? <PnlBadge value={t.pnl} /> : "—"}</td>
                    <td className="py-2 text-text-muted">{t.strategy}</td>
                    <td className="py-2 text-right"><TimeAgo date={t.timestamp} /></td>
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
