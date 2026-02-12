"use client";

import { useState } from "react";
import { useAiDecisions, useAiStats } from "@/hooks/useAiDecisions";
import { Card, StatCard, Skeleton } from "@/components/shared/Card";
import { TimeAgo } from "@/components/shared/TimeAgo";
import { EmptyState } from "@/components/shared/EmptyState";
import { Pagination } from "@/components/shared/Pagination";
import { formatUsd, formatPct } from "@/lib/format";

const ACTION_COLORS: Record<string, string> = {
  BUY: "bg-profit/10 text-profit border-profit/20",
  SHORT: "bg-loss/10 text-loss border-loss/20",
  SELL: "bg-warning/10 text-warning border-warning/20",
  HOLD: "bg-text-muted/10 text-text-secondary border-text-muted/20",
  CLOSE: "bg-loss/10 text-loss border-loss/20",
  SCALE_UP: "bg-accent/10 text-accent border-accent/20",
  ADJUST: "bg-purple/10 text-purple border-purple/20",
};

function ActionBadge({ action }: { action: string }) {
  const style = ACTION_COLORS[action] ?? "bg-text-muted/10 text-text-muted border-text-muted/20";
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-md text-[11px] font-semibold border ${style}`}>
      {action}
    </span>
  );
}

function TierBadge({ tier }: { tier: string | null }) {
  if (!tier) return <span className="text-text-muted text-xs">—</span>;
  const styles: Record<string, string> = {
    opus: "text-purple",
    cli: "text-accent",
    haiku: "text-text-secondary",
    prescreen: "text-text-muted",
    fallback: "text-warning",
  };
  const color = styles[tier] ?? "text-text-muted";
  return <span className={`text-xs font-medium ${color}`}>{tier}</span>;
}

function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.min(100, Math.max(0, value * 100));
  const color = pct >= 70 ? "bg-profit" : pct >= 40 ? "bg-warning" : "bg-loss";
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 bg-bg-secondary rounded-full overflow-hidden max-w-16">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs font-mono text-text-secondary">{pct.toFixed(0)}%</span>
    </div>
  );
}

function AiStatsCards() {
  const { stats, isLoading } = useAiStats();

  if (isLoading) {
    return (
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3 mb-6">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="w-full h-20 rounded-xl" />
        ))}
      </div>
    );
  }

  if (!stats) return null;

  return (
    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3 mb-6">
      <StatCard
        label="Total Decisions"
        value={<span className="text-text-primary">{stats.total_decisions}</span>}
      />
      <StatCard
        label="Executed"
        value={<span className="text-profit">{stats.executed_count}</span>}
        trend={
          <span className="text-[11px] text-text-muted">
            Rate: {formatPct((stats.execution_rate ?? 0) * 100)}
          </span>
        }
      />
      <StatCard
        label="Avg Confidence"
        value={
          <span className={`${(stats.avg_confidence ?? 0) >= 0.6 ? "text-profit" : "text-warning"}`}>
            {formatPct((stats.avg_confidence ?? 0) * 100)}
          </span>
        }
      />
      <StatCard
        label="Total Cost"
        value={<span className="text-text-primary">{formatUsd(stats.total_cost_usd ?? 0)}</span>}
      />
      <StatCard
        label="By Tier"
        value={
          <div className="flex flex-wrap gap-1">
            {(stats.by_tier ?? []).map((t) => (
              <span key={t.tier} className="text-xs text-text-muted">
                <TierBadge tier={t.tier} />
                <span className="ml-0.5 font-mono">{t.cnt}</span>
              </span>
            ))}
          </div>
        }
      />
    </div>
  );
}

export default function AiDecisionsPage() {
  const [page, setPage] = useState(0);
  const limit = 50;
  const { decisions, total, isLoading } = useAiDecisions(limit, page * limit);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  const totalPages = Math.max(1, Math.ceil(total / limit));

  const toggleExpand = (id: number) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-5">
        <h2 className="text-lg font-bold text-text-primary">AI Advisor</h2>
        <span className="text-xs text-text-muted font-mono">{total.toLocaleString()} decisions</span>
      </div>

      <AiStatsCards />

      <Card noPadding>
        {isLoading && page === 0 ? (
          <div className="p-5"><Skeleton className="w-full h-60" /></div>
        ) : decisions.length === 0 ? (
          <div className="p-5">
            <EmptyState title="No AI decisions yet" description="AI advisor decisions will appear here when the bot uses AI" />
          </div>
        ) : (
          <>
            <div className="divide-y divide-border/30">
              {decisions.map((d) => {
                const isExpanded = expanded.has(d.id);

                return (
                  <div key={d.id}>
                    <div
                      className="flex items-center gap-3 py-3 px-4 hover:bg-bg-card-hover cursor-pointer transition-colors"
                      onClick={() => toggleExpand(d.id)}
                    >
                      <span className="text-text-muted text-xs font-mono w-10 text-right shrink-0">
                        #{d.id}
                      </span>
                      <ActionBadge action={d.action} />
                      <span className="font-semibold text-sm text-text-primary w-16 shrink-0">{d.symbol}</span>
                      <ConfidenceBar value={d.confidence} />
                      <TierBadge tier={d.tier} />
                      <span className={`text-xs font-medium ${d.executed ? "text-profit" : "text-text-muted"}`}>
                        {d.executed ? "Executed" : "Not executed"}
                      </span>
                      {d.cost_usd !== null && d.cost_usd > 0 && (
                        <span className="text-[10px] text-text-muted font-mono">${d.cost_usd.toFixed(4)}</span>
                      )}
                      <span className="ml-auto shrink-0"><TimeAgo date={d.timestamp} /></span>
                      <span className="text-text-muted text-xs shrink-0 w-4">
                        {isExpanded ? "▼" : "▶"}
                      </span>
                    </div>
                    {isExpanded && (
                      <div className="px-4 pb-4 pt-1 ml-10 space-y-2">
                        {d.reasoning && (
                          <div className="bg-bg-secondary rounded-lg p-3 border border-border-light">
                            <span className="text-[11px] text-text-muted uppercase tracking-wide block mb-1">Reasoning</span>
                            <p className="text-xs text-text-secondary leading-relaxed whitespace-pre-wrap">{d.reasoning}</p>
                          </div>
                        )}
                        {d.execution_result && (
                          <div className="bg-bg-secondary rounded-lg p-3 border border-border-light">
                            <span className="text-[11px] text-text-muted uppercase tracking-wide block mb-1">Execution Result</span>
                            <p className="text-xs text-text-secondary">{d.execution_result}</p>
                          </div>
                        )}
                        <div className="flex gap-4 text-xs text-text-muted">
                          <span>Tier: <TierBadge tier={d.tier} /></span>
                          <span>Confidence: <span className="text-text-secondary font-mono">{(d.confidence * 100).toFixed(1)}%</span></span>
                          {d.cost_usd !== null && (
                            <span>Cost: <span className="text-text-secondary font-mono">${d.cost_usd.toFixed(4)}</span></span>
                          )}
                          {d.snapshot_hash && (
                            <span className="font-mono truncate max-w-32" title={d.snapshot_hash}>
                              Hash: {d.snapshot_hash.slice(0, 12)}...
                            </span>
                          )}
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
            <div className="px-4 pb-2">
              <Pagination page={page} totalPages={totalPages} onPageChange={setPage} />
            </div>
          </>
        )}
      </Card>
    </div>
  );
}
