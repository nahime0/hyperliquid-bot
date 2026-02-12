"use client";

import { useStatus } from "@/hooks/useStatus";
import { useOpenPositions } from "@/hooks/usePositions";
import { useTrades } from "@/hooks/useTrades";
import { useEquity } from "@/hooks/useEquity";
import { useStats } from "@/hooks/useStats";
import { Card } from "@/components/shared/Card";
import { PnlBadge } from "@/components/shared/PnlBadge";
import { DirectionBadge } from "@/components/shared/DirectionBadge";
import { TimeAgo } from "@/components/shared/TimeAgo";
import { formatUsd, formatPrice, formatPct } from "@/lib/format";
import type { BotStatus } from "@/lib/types";
import type { TradeStats } from "@/lib/types";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from "recharts";

function AlertBanner({ status }: { status: BotStatus }) {
  const { kill_switch, kill_reason, daily_paused, daily_pause_reason } = status.risk_metrics;
  if (!kill_switch && !daily_paused) return null;

  return (
    <div className={`mb-4 p-3 rounded-lg border ${kill_switch ? "bg-loss/10 border-loss/30 text-loss" : "bg-warning/10 border-warning/30 text-warning"}`}>
      <span className="font-medium">
        {kill_switch ? "KILL SWITCH ACTIVE" : "DAILY PAUSE"}
      </span>
      <span className="ml-2 text-sm opacity-80">
        {kill_switch ? kill_reason : daily_pause_reason}
      </span>
    </div>
  );
}

type StatsData = TradeStats & { total_pnl_all: number; current_balance: number; peak_balance: number; open_position_count: number };

function KpiCards({ status, stats }: { status: BotStatus | null; stats: StatsData | null }) {
  const balance = status?.risk_metrics.current_balance ?? 0;
  const drawdown = status?.risk_metrics.drawdown_pct ?? 0;
  const utilization = status?.risk_metrics.capital_utilization ?? 0;

  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
      <Card>
        <p className="text-xs text-text-muted mb-1">Balance</p>
        <p className="text-2xl font-bold font-mono">{formatUsd(balance)}</p>
      </Card>
      <Card>
        <p className="text-xs text-text-muted mb-1">Total PnL</p>
        <p className="text-2xl font-bold">
          <PnlBadge value={stats?.total_pnl_all ?? 0} />
        </p>
      </Card>
      <Card>
        <p className="text-xs text-text-muted mb-1">Drawdown</p>
        <p className={`text-2xl font-bold font-mono ${drawdown > 5 ? "text-loss" : "text-text-primary"}`}>
          {formatPct(-drawdown)}
        </p>
      </Card>
      <Card>
        <p className="text-xs text-text-muted mb-1">Utilization</p>
        <p className="text-2xl font-bold font-mono text-accent">
          {formatPct(utilization * 100)}
        </p>
      </Card>
    </div>
  );
}

function EquityChart() {
  const { snapshots } = useEquity();

  if (snapshots.length === 0) {
    return (
      <Card title="Equity Curve" className="mb-6">
        <p className="text-text-muted text-sm">No balance snapshots yet</p>
      </Card>
    );
  }

  const data = snapshots.map((s) => ({
    time: new Date(s.timestamp + "Z").toLocaleDateString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }),
    balance: s.total_usdc,
  }));

  return (
    <Card title="Equity Curve" className="mb-6">
      <div className="h-64">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data}>
            <defs>
              <linearGradient id="balanceGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#58a6ff" stopOpacity={0.3} />
                <stop offset="95%" stopColor="#58a6ff" stopOpacity={0} />
              </linearGradient>
            </defs>
            <XAxis
              dataKey="time"
              tick={{ fill: "#8b949e", fontSize: 11 }}
              axisLine={{ stroke: "#30363d" }}
              tickLine={false}
            />
            <YAxis
              tick={{ fill: "#8b949e", fontSize: 11 }}
              axisLine={{ stroke: "#30363d" }}
              tickLine={false}
              domain={["auto", "auto"]}
            />
            <Tooltip
              contentStyle={{ background: "#161b22", border: "1px solid #30363d", borderRadius: 8 }}
              labelStyle={{ color: "#8b949e" }}
              itemStyle={{ color: "#e6edf3" }}
            />
            <Area
              type="monotone"
              dataKey="balance"
              stroke="#58a6ff"
              fill="url(#balanceGrad)"
              strokeWidth={2}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </Card>
  );
}

function OpenPositionsTable() {
  const { positions } = useOpenPositions();
  const { status } = useStatus();

  if (positions.length === 0) {
    return (
      <Card title="Open Positions">
        <p className="text-text-muted text-sm">No open positions</p>
      </Card>
    );
  }

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

  return (
    <Card title={`Open Positions (${positions.length})`}>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-text-muted text-xs border-b border-border">
              <th className="text-left pb-2">Symbol</th>
              <th className="text-left pb-2">Dir</th>
              <th className="text-right pb-2">Entry</th>
              <th className="text-right pb-2">Current</th>
              <th className="text-right pb-2">PnL</th>
              <th className="text-right pb-2">SL</th>
              <th className="text-right pb-2">Lev</th>
              <th className="text-left pb-2">Strategy</th>
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
                  <td className="py-2 font-medium">{p.symbol}</td>
                  <td className="py-2"><DirectionBadge direction={p.direction} /></td>
                  <td className="py-2 text-right font-mono">{formatPrice(p.entry_price)}</td>
                  <td className="py-2 text-right font-mono">{mid ? formatPrice(mid) : "—"}</td>
                  <td className="py-2 text-right">
                    {mid ? (
                      <span className="flex flex-col items-end">
                        <PnlBadge value={pnl} />
                        <span className={`text-xs ${pnlPct >= 0 ? "text-profit" : "text-loss"}`}>
                          {formatPct(pnlPct)}
                        </span>
                      </span>
                    ) : "—"}
                  </td>
                  <td className="py-2 text-right font-mono text-text-muted">
                    {p.trailing_sl ? formatPrice(p.trailing_sl) : p.stop_loss ? formatPrice(p.stop_loss) : "—"}
                  </td>
                  <td className="py-2 text-right">{p.leverage}x</td>
                  <td className="py-2 text-text-muted">{p.strategy}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function RecentTradesTable() {
  const { trades } = useTrades(10);

  if (trades.length === 0) {
    return (
      <Card title="Recent Trades">
        <p className="text-text-muted text-sm">No trades yet</p>
      </Card>
    );
  }

  return (
    <Card title="Recent Trades">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-text-muted text-xs border-b border-border">
              <th className="text-left pb-2">Symbol</th>
              <th className="text-left pb-2">Side</th>
              <th className="text-right pb-2">Price</th>
              <th className="text-right pb-2">PnL</th>
              <th className="text-right pb-2">Time</th>
            </tr>
          </thead>
          <tbody>
            {trades.map((t) => (
              <tr key={t.id} className="border-b border-border/50 hover:bg-bg-card-hover">
                <td className="py-2 font-medium">{t.symbol}</td>
                <td className="py-2">
                  <span className={`text-xs font-medium ${
                    t.side === "BUY" ? "text-profit" : t.side === "SHORT" ? "text-loss" : "text-text-muted"
                  }`}>
                    {t.side}
                  </span>
                </td>
                <td className="py-2 text-right font-mono">{formatPrice(t.price)}</td>
                <td className="py-2 text-right">
                  {t.pnl !== null ? <PnlBadge value={t.pnl} /> : "—"}
                </td>
                <td className="py-2 text-right">
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

export default function OverviewPage() {
  const { status, isLoading } = useStatus();
  const { stats } = useStats();

  if (isLoading) {
    return <div className="text-text-muted">Loading...</div>;
  }

  return (
    <div>
      {status && <AlertBanner status={status} />}
      <KpiCards status={status ?? null} stats={stats ?? null} />
      <EquityChart />
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
        <OpenPositionsTable />
        <RecentTradesTable />
      </div>
    </div>
  );
}
