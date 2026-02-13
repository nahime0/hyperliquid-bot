"use client";

import { useStatus } from "@/hooks/useStatus";
import { useOpenPositions } from "@/hooks/usePositions";
import { useTrades } from "@/hooks/useTrades";
import { useEquity } from "@/hooks/useEquity";
import { useStats } from "@/hooks/useStats";
import { useEvents } from "@/hooks/useEvents";
import { Card, StatCard, Skeleton } from "@/components/shared/Card";
import { PnlBadge } from "@/components/shared/PnlBadge";
import { DirectionBadge } from "@/components/shared/DirectionBadge";
import { SideBadge } from "@/components/shared/SideBadge";
import { ScoreBar } from "@/components/shared/ScoreBar";
import { TimeAgo } from "@/components/shared/TimeAgo";
import { EmptyState } from "@/components/shared/EmptyState";
import { formatUsd, formatPrice, formatPct } from "@/lib/format";
import type { DashboardStatus, AggregateStats } from "@/lib/types";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from "recharts";

/* ── KPI Cards ──────────────────────────────────────────── */
function KpiCards({ status, stats }: { status: DashboardStatus | null; stats: AggregateStats | null }) {
  const balance = stats?.current_balance ?? status?.balance_usdc ?? 0;
  const peak = stats?.peak_balance ?? status?.peak_balance ?? 0;
  const drawdown = status?.drawdown_pct ?? 0;
  const dailyDD = status?.daily_drawdown_pct ?? 0;
  const utilization = status?.capital_utilization ?? 0;
  const winRate = stats?.win_rate ?? 0;

  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 xl:grid-cols-6 gap-3 mb-6">
      <StatCard
        label="Balance"
        value={<span className="text-text-primary">{formatUsd(balance)}</span>}
        trend={peak > 0 ? <span className="text-[11px] text-text-muted">Peak: {formatUsd(peak)}</span> : undefined}
      />
      <StatCard
        label="Total PnL"
        value={<PnlBadge value={stats?.total_pnl_all ?? 0} />}
        trend={
          stats && stats.total_trades > 0
            ? <span className="text-[11px] text-text-muted">{stats.total_trades} trades</span>
            : undefined
        }
      />
      <StatCard
        label="Win Rate"
        value={
          <span className={`${winRate >= 0.5 ? "text-profit" : winRate > 0 ? "text-warning" : "text-text-muted"}`}>
            {formatPct(winRate * 100)}
          </span>
        }
        trend={
          stats
            ? <span className="text-[11px] text-text-muted">PF: {stats.profit_factor == null ? "—" : stats.profit_factor === Infinity ? "∞" : stats.profit_factor.toFixed(2)}</span>
            : undefined
        }
      />
      <StatCard
        label="Drawdown"
        value={
          <span className={`${drawdown > 5 ? "text-loss" : drawdown > 2 ? "text-warning" : "text-text-primary"}`}>
            {formatPct(-drawdown)}
          </span>
        }
        trend={<span className="text-[11px] text-text-muted">Daily: {formatPct(-dailyDD)}</span>}
      />
      <StatCard
        label="Capital Used"
        value={<span className="text-accent">{formatPct(utilization * 100)}</span>}
        trend={
          <span className="text-[11px] text-text-muted">
            {status?.open_positions ?? 0} positions
          </span>
        }
      />
      <StatCard
        label="Cycle Stats"
        value={<span className="text-text-primary font-mono">#{status?.cycle ?? 0}</span>}
        trend={
          <span className="text-[11px] text-text-muted">
            {status?.signals_generated ?? 0} sig / {status?.trades_executed ?? 0} trades
          </span>
        }
      />
    </div>
  );
}

/* ── Equity Chart ──────────────────────────────────────── */
function EquityChart() {
  const { snapshots, isLoading } = useEquity();

  if (isLoading) {
    return (
      <Card title="Equity Curve" className="mb-6">
        <Skeleton className="w-full h-56" />
      </Card>
    );
  }

  if (snapshots.length === 0) {
    return (
      <Card title="Equity Curve" className="mb-6">
        <EmptyState title="No balance data yet" description="Equity curve will appear after the first cycle" />
      </Card>
    );
  }

  const data = snapshots.map((s) => ({
    time: new Date(s.timestamp + (s.timestamp.endsWith("Z") ? "" : "Z")).toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }),
    balance: s.total_usdc,
    peak: s.peak_balance,
  }));

  return (
    <Card title="Equity Curve" subtitle={`${snapshots.length} data points`} className="mb-6">
      <div className="h-64">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top: 5, right: 5, bottom: 0, left: 5 }}>
            <defs>
              <linearGradient id="balanceGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.2} />
                <stop offset="95%" stopColor="#3b82f6" stopOpacity={0} />
              </linearGradient>
            </defs>
            <XAxis
              dataKey="time"
              tick={{ fill: "#64748b", fontSize: 10 }}
              axisLine={{ stroke: "#1e2a3a" }}
              tickLine={false}
              interval="preserveStartEnd"
            />
            <YAxis
              tick={{ fill: "#64748b", fontSize: 10 }}
              axisLine={false}
              tickLine={false}
              domain={["auto", "auto"]}
              tickFormatter={(v: number) => `$${v.toFixed(0)}`}
              width={55}
            />
            <Tooltip
              contentStyle={{
                background: "#141a22",
                border: "1px solid #1e2a3a",
                borderRadius: 8,
                fontSize: 12,
                boxShadow: "0 4px 12px rgba(0,0,0,.3)",
              }}
              labelStyle={{ color: "#64748b", fontSize: 11 }}
              itemStyle={{ color: "#e2e8f0" }}
              formatter={(value) => [`$${Number(value).toFixed(2)}`, "Balance"]}
            />
            <Area
              type="monotone"
              dataKey="balance"
              stroke="#3b82f6"
              fill="url(#balanceGrad)"
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 4, stroke: "#3b82f6", strokeWidth: 2, fill: "#141a22" }}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </Card>
  );
}

/* ── Open Positions ────────────────────────────────────── */
function OpenPositionsTable() {
  const { positions, isLoading } = useOpenPositions();

  if (isLoading) {
    return (
      <Card title="Open Positions">
        <Skeleton className="w-full h-32" />
      </Card>
    );
  }

  if (positions.length === 0) {
    return (
      <Card title="Open Positions">
        <EmptyState title="No open positions" description="Positions will appear here when the bot opens trades" />
      </Card>
    );
  }

  return (
    <Card title={`Open Positions (${positions.length})`} noPadding>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-text-muted text-[11px] uppercase tracking-wider border-b border-border">
              <th className="text-left py-2.5 px-4">Symbol</th>
              <th className="text-left py-2.5 px-2">Dir</th>
              <th className="text-right py-2.5 px-2">Entry</th>
              <th className="text-right py-2.5 px-2">Qty</th>
              <th className="text-right py-2.5 px-2">SL</th>
              <th className="text-right py-2.5 px-2">TP</th>
              <th className="text-right py-2.5 px-2">Lev</th>
              <th className="text-left py-2.5 px-4">Strategy</th>
            </tr>
          </thead>
          <tbody>
            {positions.map((p) => (
              <tr key={p.id} className="border-b border-border/40 hover:bg-bg-card-hover transition-colors">
                <td className="py-2.5 px-4 font-semibold text-text-primary">{p.symbol}</td>
                <td className="py-2.5 px-2"><DirectionBadge direction={p.direction} /></td>
                <td className="py-2.5 px-2 text-right font-mono text-text-secondary">{formatPrice(p.entry_price)}</td>
                <td className="py-2.5 px-2 text-right font-mono text-text-secondary">{p.quantity.toFixed(4)}</td>
                <td className="py-2.5 px-2 text-right font-mono text-text-muted">
                  {p.trailing_sl ? (
                    <span className="text-warning" title="Trailing SL">{formatPrice(p.trailing_sl)}</span>
                  ) : p.stop_loss ? (
                    formatPrice(p.stop_loss)
                  ) : "—"}
                </td>
                <td className="py-2.5 px-2 text-right font-mono text-text-muted">
                  {p.take_profit ? formatPrice(p.take_profit) : "—"}
                </td>
                <td className="py-2.5 px-2 text-right">
                  <span className="text-accent font-medium">{p.leverage}x</span>
                </td>
                <td className="py-2.5 px-4 text-text-muted text-xs">{p.strategy}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

/* ── Recent Trades ─────────────────────────────────────── */
function RecentTradesTable() {
  const { trades, isLoading } = useTrades(8);

  if (isLoading) {
    return (
      <Card title="Recent Trades">
        <Skeleton className="w-full h-32" />
      </Card>
    );
  }

  if (trades.length === 0) {
    return (
      <Card title="Recent Trades">
        <EmptyState title="No trades yet" description="Trades will appear here after the first execution" />
      </Card>
    );
  }

  return (
    <Card title="Recent Trades" noPadding>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-text-muted text-[11px] uppercase tracking-wider border-b border-border">
              <th className="text-left py-2.5 px-4">Symbol</th>
              <th className="text-left py-2.5 px-2">Side</th>
              <th className="text-right py-2.5 px-2">Price</th>
              <th className="text-right py-2.5 px-2">PnL</th>
              <th className="text-right py-2.5 px-4">Time</th>
            </tr>
          </thead>
          <tbody>
            {trades.map((t) => (
              <tr key={t.id} className="border-b border-border/40 hover:bg-bg-card-hover transition-colors">
                <td className="py-2.5 px-4 font-semibold text-text-primary">{t.symbol}</td>
                <td className="py-2.5 px-2"><SideBadge side={t.side} /></td>
                <td className="py-2.5 px-2 text-right font-mono text-text-secondary">{formatPrice(t.price)}</td>
                <td className="py-2.5 px-2 text-right">
                  {t.pnl !== null ? <PnlBadge value={t.pnl} /> : <span className="text-text-muted">—</span>}
                </td>
                <td className="py-2.5 px-4 text-right">
                  <TimeAgo date={t.timestamp} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

/* ── Bot Activity Summary ──────────────────────────────── */
function BotActivity({ status }: { status: DashboardStatus | null }) {
  if (!status) return null;

  const items = [
    { label: "Coins Monitored", value: status.coins_monitored, color: "text-accent" },
    { label: "Signals Generated", value: status.signals_generated, color: "text-purple" },
    { label: "Decisions Approved", value: status.decisions_approved, color: "text-profit" },
    { label: "Decisions Blocked", value: status.decisions_blocked, color: "text-loss" },
    { label: "Trades Executed", value: status.trades_executed, color: "text-accent" },
  ];

  return (
    <Card title="Last Cycle Activity" subtitle={status.duration_sec ? `Duration: ${status.duration_sec.toFixed(1)}s` : undefined}>
      <div className="space-y-3">
        {items.map((item) => (
          <div key={item.label} className="flex items-center justify-between">
            <span className="text-xs text-text-muted">{item.label}</span>
            <span className={`text-sm font-bold font-mono ${item.color}`}>{item.value}</span>
          </div>
        ))}
      </div>
    </Card>
  );
}

/* ── Performance Summary ───────────────────────────────── */
function PerformanceSummary({ stats }: { stats: AggregateStats | null }) {
  if (!stats) return null;

  return (
    <Card title="Performance">
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <span className="text-xs text-text-muted">Avg Win</span>
          <span className="text-sm font-bold font-mono text-profit">{formatUsd(stats.avg_win ?? 0)}</span>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-xs text-text-muted">Avg Loss</span>
          <span className="text-sm font-bold font-mono text-loss">{formatUsd(stats.avg_loss ?? 0)}</span>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-xs text-text-muted">Profit Factor</span>
          <span className="text-sm font-bold font-mono text-text-primary">
            {stats.profit_factor == null ? "—" : stats.profit_factor === Infinity ? "∞" : stats.profit_factor.toFixed(2)}
          </span>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-xs text-text-muted">Open Positions</span>
          <span className="text-sm font-bold font-mono text-accent">{stats.open_position_count ?? 0}</span>
        </div>
      </div>
    </Card>
  );
}

/* ── Recent Signals ─────────────────────────────────────── */
function RecentSignals() {
  const { events, isLoading } = useEvents({ limit: 10, event_type: "SIGNAL" });

  if (isLoading) {
    return (
      <Card title="Recent Signals">
        <Skeleton className="w-full h-32" />
      </Card>
    );
  }

  if (events.length === 0) {
    return (
      <Card title="Recent Signals">
        <EmptyState title="No signals yet" description="Strategy signals will appear here" />
      </Card>
    );
  }

  return (
    <Card title={`Recent Signals (${events.length})`} noPadding>
      <div className="divide-y divide-border/30">
        {events.map((e) => {
          let details: Record<string, unknown> | null = null;
          try { if (e.details && e.details !== "{}") details = JSON.parse(e.details); } catch { /* ignore */ }

          return (
            <div key={e.id} className="py-2.5 px-4">
              <div className="flex items-center gap-2 mb-1.5">
                <span className="font-semibold text-sm text-text-primary">{e.symbol}</span>
                {e.action && (
                  <span className={`text-[11px] font-bold px-1.5 py-0.5 rounded border ${
                    e.action === "BUY" ? "text-profit bg-profit/10 border-profit/20"
                    : e.action === "SHORT" ? "text-loss bg-loss/10 border-loss/20"
                    : "text-text-secondary bg-bg-elevated border-border-light"
                  }`}>
                    {e.action}
                  </span>
                )}
                {e.confidence !== null && (
                  <div className="w-24">
                    <ScoreBar value={e.confidence} size="md" />
                  </div>
                )}
                <span className="text-[10px] text-text-muted ml-auto">{e.source}</span>
                <TimeAgo date={e.timestamp} />
              </div>
              {details && (
                <div className="flex gap-3 text-[11px]">
                  {details.rsi != null && (
                    <span className="text-text-muted">
                      RSI <span className={`font-mono font-medium ${Number(details.rsi) < 35 ? "text-profit" : Number(details.rsi) > 65 ? "text-loss" : "text-text-secondary"}`}>
                        {Number(details.rsi).toFixed(1)}
                      </span>
                    </span>
                  )}
                  {details.bb_pct != null && (
                    <span className="text-text-muted">
                      BB <span className="font-mono font-medium text-text-secondary">{(Number(details.bb_pct) * 100).toFixed(0)}%</span>
                    </span>
                  )}
                  {details.volume_ratio != null && (
                    <span className="text-text-muted">
                      Vol <span className={`font-mono font-medium ${Number(details.volume_ratio) > 1.5 ? "text-profit" : "text-text-secondary"}`}>
                        {Number(details.volume_ratio).toFixed(1)}x
                      </span>
                    </span>
                  )}
                  {details.trend != null && (
                    <span className={`font-medium ${details.trend === "up" ? "text-profit" : details.trend === "down" ? "text-loss" : "text-text-muted"}`}>
                      {String(details.trend)}
                    </span>
                  )}
                  {details.divergence != null && (
                    <span className="text-purple font-medium">{String(details.divergence)}</span>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </Card>
  );
}

/* ── Main Page ─────────────────────────────────────────── */
export default function OverviewPage() {
  const { status, isLoading } = useStatus();
  const { stats } = useStats();

  if (isLoading) {
    return (
      <div className="space-y-6">
        <div className="grid grid-cols-2 lg:grid-cols-4 xl:grid-cols-6 gap-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="bg-bg-card border border-border rounded-xl p-4">
              <Skeleton className="w-16 h-3 mb-3" />
              <Skeleton className="w-24 h-6" />
            </div>
          ))}
        </div>
        <Skeleton className="w-full h-72 rounded-xl" />
      </div>
    );
  }

  return (
    <div>
      <KpiCards status={status ?? null} stats={stats ?? null} />
      <EquityChart />
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6 mb-6">
        <div className="xl:col-span-2">
          <OpenPositionsTable />
        </div>
        <div className="space-y-6">
          <BotActivity status={status ?? null} />
          <PerformanceSummary stats={stats ?? null} />
        </div>
      </div>
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
        <RecentSignals />
        <RecentTradesTable />
      </div>
    </div>
  );
}
