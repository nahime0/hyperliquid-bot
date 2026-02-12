"use client";

import { useState } from "react";
import { useTrades } from "@/hooks/useTrades";
import { useStats } from "@/hooks/useStats";
import { Card } from "@/components/shared/Card";
import { PnlBadge } from "@/components/shared/PnlBadge";
import { TimeAgo } from "@/components/shared/TimeAgo";
import { formatPrice, formatUsd, formatPct } from "@/lib/format";

export default function TradesPage() {
  const [page, setPage] = useState(0);
  const limit = 50;
  const { trades } = useTrades(limit, page * limit);
  const { stats } = useStats();

  return (
    <div>
      <h2 className="text-xl font-bold mb-4">Trades</h2>

      {/* Stats bar */}
      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-6">
          <Card>
            <p className="text-xs text-text-muted">Total Trades</p>
            <p className="text-xl font-bold font-mono">{stats.total_trades}</p>
          </Card>
          <Card>
            <p className="text-xs text-text-muted">Win Rate</p>
            <p className={`text-xl font-bold font-mono ${stats.win_rate >= 0.5 ? "text-profit" : "text-loss"}`}>
              {formatPct(stats.win_rate * 100)}
            </p>
          </Card>
          <Card>
            <p className="text-xs text-text-muted">Total PnL</p>
            <p className="text-xl font-bold">
              <PnlBadge value={stats.total_pnl_all} />
            </p>
          </Card>
          <Card>
            <p className="text-xs text-text-muted">Avg Win</p>
            <p className="text-xl font-bold font-mono text-profit">{formatUsd(stats.avg_win)}</p>
          </Card>
          <Card>
            <p className="text-xs text-text-muted">Avg Loss</p>
            <p className="text-xl font-bold font-mono text-loss">{formatUsd(stats.avg_loss)}</p>
          </Card>
        </div>
      )}

      <Card>
        {trades.length === 0 ? (
          <p className="text-text-muted text-sm py-4 text-center">No trades yet</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-text-muted text-xs border-b border-border">
                  <th className="text-left pb-2">#</th>
                  <th className="text-left pb-2">Symbol</th>
                  <th className="text-left pb-2">Side</th>
                  <th className="text-right pb-2">Price</th>
                  <th className="text-right pb-2">Qty</th>
                  <th className="text-right pb-2">Fee</th>
                  <th className="text-right pb-2">PnL</th>
                  <th className="text-left pb-2">Strategy</th>
                  <th className="text-left pb-2">Notes</th>
                  <th className="text-right pb-2">Time</th>
                </tr>
              </thead>
              <tbody>
                {trades.map((t) => (
                  <tr key={t.id} className="border-b border-border/50 hover:bg-bg-card-hover">
                    <td className="py-2 text-text-muted text-xs">{t.id}</td>
                    <td className="py-2 font-medium">{t.symbol}</td>
                    <td className="py-2">
                      <span className={`text-xs font-medium ${
                        t.side === "BUY" ? "text-profit" : t.side === "SHORT" ? "text-loss" : "text-text-muted"
                      }`}>
                        {t.side}
                      </span>
                    </td>
                    <td className="py-2 text-right font-mono">{formatPrice(t.price)}</td>
                    <td className="py-2 text-right font-mono">{t.quantity.toFixed(6)}</td>
                    <td className="py-2 text-right font-mono text-text-muted">{t.fee > 0 ? t.fee.toFixed(4) : "—"}</td>
                    <td className="py-2 text-right">{t.pnl !== null ? <PnlBadge value={t.pnl} /> : "—"}</td>
                    <td className="py-2 text-text-muted text-xs">{t.strategy}</td>
                    <td className="py-2 text-text-muted text-xs truncate max-w-32">{t.notes ?? "—"}</td>
                    <td className="py-2 text-right"><TimeAgo date={t.timestamp} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

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
            disabled={trades.length < limit}
            className="px-3 py-1 rounded text-sm border border-border disabled:opacity-30 hover:bg-bg-card-hover"
          >
            Next
          </button>
        </div>
      </Card>
    </div>
  );
}
