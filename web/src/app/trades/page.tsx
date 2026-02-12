"use client";

import { useState } from "react";
import { useTrades } from "@/hooks/useTrades";
import { useStats } from "@/hooks/useStats";
import { Card, StatCard, Skeleton } from "@/components/shared/Card";
import { PnlBadge } from "@/components/shared/PnlBadge";
import { SideBadge } from "@/components/shared/SideBadge";
import { DirectionBadge } from "@/components/shared/DirectionBadge";
import { TimeAgo } from "@/components/shared/TimeAgo";
import { EmptyState } from "@/components/shared/EmptyState";
import { Pagination } from "@/components/shared/Pagination";
import { formatPrice, formatUsd, formatPct } from "@/lib/format";

export default function TradesPage() {
  const [page, setPage] = useState(0);
  const limit = 50;
  const { trades, isLoading } = useTrades(limit, page * limit);
  const { stats } = useStats();

  return (
    <div>
      <div className="flex items-center justify-between mb-5">
        <h2 className="text-lg font-bold text-text-primary">Trades</h2>
      </div>

      {/* Stats bar */}
      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3 mb-6">
          <StatCard
            label="Total Trades"
            value={<span className="text-text-primary">{stats.total_trades}</span>}
          />
          <StatCard
            label="Win Rate"
            value={
              <span className={stats.win_rate >= 0.5 ? "text-profit" : stats.win_rate > 0 ? "text-warning" : "text-text-muted"}>
                {formatPct(stats.win_rate * 100)}
              </span>
            }
          />
          <StatCard
            label="Total PnL"
            value={<PnlBadge value={stats.total_pnl_all} />}
          />
          <StatCard
            label="Avg Win"
            value={<span className="text-profit">{formatUsd(stats.avg_win)}</span>}
          />
          <StatCard
            label="Avg Loss"
            value={<span className="text-loss">{formatUsd(stats.avg_loss)}</span>}
          />
          <StatCard
            label="Profit Factor"
            value={
              <span className="text-text-primary">
                {stats.profit_factor === Infinity ? "∞" : stats.profit_factor.toFixed(2)}
              </span>
            }
          />
        </div>
      )}

      <Card noPadding>
        {isLoading && page === 0 ? (
          <div className="p-5"><Skeleton className="w-full h-60" /></div>
        ) : trades.length === 0 ? (
          <div className="p-5">
            <EmptyState title="No trades yet" description="Trade history will appear here after the first execution" />
          </div>
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-text-muted text-[11px] uppercase tracking-wider border-b border-border">
                    <th className="text-left py-2.5 px-4">ID</th>
                    <th className="text-left py-2.5 px-2">Symbol</th>
                    <th className="text-left py-2.5 px-2">Side</th>
                    <th className="text-left py-2.5 px-2">Dir</th>
                    <th className="text-right py-2.5 px-2">Price</th>
                    <th className="text-right py-2.5 px-2">Qty</th>
                    <th className="text-right py-2.5 px-2">Fee</th>
                    <th className="text-right py-2.5 px-2">PnL</th>
                    <th className="text-left py-2.5 px-2">Strategy</th>
                    <th className="text-left py-2.5 px-2">Notes</th>
                    <th className="text-right py-2.5 px-4">Time</th>
                  </tr>
                </thead>
                <tbody>
                  {trades.map((t) => (
                    <tr key={t.id} className="border-b border-border/40 hover:bg-bg-card-hover transition-colors">
                      <td className="py-2.5 px-4 text-text-muted text-xs font-mono">#{t.id}</td>
                      <td className="py-2.5 px-2 font-semibold text-text-primary">{t.symbol}</td>
                      <td className="py-2.5 px-2"><SideBadge side={t.side} /></td>
                      <td className="py-2.5 px-2">
                        {t.direction ? <DirectionBadge direction={t.direction} /> : <span className="text-text-muted">—</span>}
                      </td>
                      <td className="py-2.5 px-2 text-right font-mono text-text-secondary">{formatPrice(t.price)}</td>
                      <td className="py-2.5 px-2 text-right font-mono text-text-secondary">{t.quantity.toFixed(4)}</td>
                      <td className="py-2.5 px-2 text-right font-mono text-text-muted">
                        {t.fee > 0 ? `$${t.fee.toFixed(4)}` : "—"}
                      </td>
                      <td className="py-2.5 px-2 text-right">
                        {t.pnl !== null ? <PnlBadge value={t.pnl} /> : <span className="text-text-muted">—</span>}
                      </td>
                      <td className="py-2.5 px-2 text-text-muted text-xs">{t.strategy ?? "—"}</td>
                      <td className="py-2.5 px-2 text-text-muted text-xs truncate max-w-36">{t.notes ?? "—"}</td>
                      <td className="py-2.5 px-4 text-right"><TimeAgo date={t.timestamp} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="px-4 pb-2">
              <Pagination
                page={page}
                totalPages={trades.length < limit && page === 0 ? 1 : page + 2}
                onPageChange={setPage}
              />
            </div>
          </>
        )}
      </Card>
    </div>
  );
}
