"use client";

import { Fragment, useState } from "react";
import { useDeferred } from "@/hooks/useDeferred";
import { Card, Skeleton } from "@/components/shared/Card";
import { ScoreBar } from "@/components/shared/ScoreBar";
import { TimeAgo } from "@/components/shared/TimeAgo";
import { EmptyState } from "@/components/shared/EmptyState";
import { formatPrice } from "@/lib/format";
import type { DeferredOpportunity } from "@/lib/types";

interface Conditions {
  wait_cycles?: number;
  wait_until_price_above?: number;
  wait_until_price_below?: number;
}

function parseConditions(raw: string | null): Conditions | null {
  if (!raw || raw === "{}") return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function DeferredDetail({ item: d }: { item: DeferredOpportunity }) {
  const cond = parseConditions(d.conditions);
  const priceAbove = cond?.wait_until_price_above;
  const priceBelow = cond?.wait_until_price_below;

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-4 p-4 bg-bg-secondary rounded-lg border border-border-light text-xs">
      <div>
        <span className="text-text-muted block mb-0.5">Deferred at Cycle</span>
        <span className="font-mono font-medium text-text-primary">#{d.deferred_at_cycle}</span>
      </div>
      {cond?.wait_cycles != null && (
        <div>
          <span className="text-text-muted block mb-0.5">Wait Cycles</span>
          <span className="font-mono font-medium text-accent">{cond.wait_cycles}</span>
        </div>
      )}
      {priceAbove != null && (
        <div>
          <span className="text-text-muted block mb-0.5">Wait Until Price Above</span>
          <span className="font-mono font-medium text-profit">{formatPrice(priceAbove)}</span>
        </div>
      )}
      {priceBelow != null && (
        <div>
          <span className="text-text-muted block mb-0.5">Wait Until Price Below</span>
          <span className="font-mono font-medium text-loss">{formatPrice(priceBelow)}</span>
        </div>
      )}
    </div>
  );
}

export default function DeferredPage() {
  const { deferred, isLoading } = useDeferred();
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  const toggle = (id: number) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-5">
        <h2 className="text-lg font-bold text-text-primary">Deferred Opportunities</h2>
        <span className="text-xs text-text-muted font-mono">{deferred.length} active</span>
      </div>

      {isLoading ? (
        <Skeleton className="w-full h-40" />
      ) : deferred.length === 0 ? (
        <Card>
          <EmptyState
            title="No deferred opportunities"
            description="When the AI advisor defers a decision for later re-evaluation, it will appear here with its conditions"
          />
        </Card>
      ) : (
        <Card noPadding>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-text-muted text-[11px] uppercase tracking-wider border-b border-border">
                  <th className="w-6 py-2.5 px-3" />
                  <th className="text-left py-2.5 px-2">Symbol</th>
                  <th className="text-left py-2.5 px-2">Action</th>
                  <th className="text-left py-2.5 px-2">Score</th>
                  <th className="text-left py-2.5 px-2">Strategy</th>
                  <th className="text-right py-2.5 px-2">Wait</th>
                  <th className="text-right py-2.5 px-2">Price Condition</th>
                  <th className="text-right py-2.5 px-3">Created</th>
                </tr>
              </thead>
              <tbody>
                {deferred.map((d) => {
                  const cond = parseConditions(d.conditions);
                  const priceAbove = cond?.wait_until_price_above;
                  const priceBelow = cond?.wait_until_price_below;

                  return (
                    <Fragment key={d.id}>
                      <tr
                        className="border-b border-border/40 hover:bg-bg-card-hover cursor-pointer transition-colors"
                        onClick={() => toggle(d.id)}
                      >
                        <td className="py-2.5 px-3 text-text-muted text-xs">
                          {expanded.has(d.id) ? "▼" : "▶"}
                        </td>
                        <td className="py-2.5 px-2 font-semibold text-text-primary">{d.symbol}</td>
                        <td className="py-2.5 px-2">
                          <span className={`text-[11px] font-bold px-2 py-0.5 rounded-md border ${
                            d.original_action === "BUY"
                              ? "text-profit bg-profit/10 border-profit/20"
                              : d.original_action === "SHORT"
                              ? "text-loss bg-loss/10 border-loss/20"
                              : "text-text-secondary bg-bg-elevated border-border-light"
                          }`}>
                            {d.original_action}
                          </span>
                        </td>
                        <td className="py-2.5 px-2">
                          <div className="w-20">
                            <ScoreBar value={d.confidence} />
                          </div>
                        </td>
                        <td className="py-2.5 px-2 text-text-muted text-xs whitespace-nowrap">
                          {d.strategy_type && d.strategy_type !== "unknown" ? d.strategy_type.replace(/_/g, " ") : "—"}
                        </td>
                        <td className="py-2.5 px-2 text-right font-mono text-text-secondary">
                          {cond?.wait_cycles != null ? `${cond.wait_cycles} cycles` : "—"}
                        </td>
                        <td className="py-2.5 px-2 text-right">
                          {priceAbove || priceBelow ? (
                            <div className="flex flex-col items-end gap-0.5">
                              {priceAbove && (
                                <span className="text-xs font-mono">
                                  <span className="text-text-muted">above </span>
                                  <span className="text-profit font-semibold">{formatPrice(priceAbove)}</span>
                                </span>
                              )}
                              {priceBelow && (
                                <span className="text-xs font-mono">
                                  <span className="text-text-muted">below </span>
                                  <span className="text-loss font-semibold">{formatPrice(priceBelow)}</span>
                                </span>
                              )}
                            </div>
                          ) : (
                            <span className="text-text-muted">—</span>
                          )}
                        </td>
                        <td className="py-2.5 px-3 text-right">
                          <TimeAgo date={d.created_at} />
                        </td>
                      </tr>
                      {expanded.has(d.id) && (
                        <tr>
                          <td colSpan={8} className="p-3">
                            <DeferredDetail item={d} />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
