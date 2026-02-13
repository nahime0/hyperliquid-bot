"use client";

import { useDeferred } from "@/hooks/useDeferred";
import { Card, Skeleton } from "@/components/shared/Card";
import { TimeAgo } from "@/components/shared/TimeAgo";
import { EmptyState } from "@/components/shared/EmptyState";
import { formatPrice } from "@/lib/format";

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

export default function DeferredPage() {
  const { deferred, isLoading } = useDeferred();

  return (
    <div>
      <div className="flex items-center justify-between mb-5">
        <h2 className="text-lg font-bold text-text-primary">Deferred Opportunities</h2>
        <span className="text-xs text-text-muted font-mono">{deferred.length} active</span>
      </div>

      {isLoading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="w-full h-40 rounded-xl" />
          ))}
        </div>
      ) : deferred.length === 0 ? (
        <Card>
          <EmptyState
            title="No deferred opportunities"
            description="When the AI advisor defers a decision for later re-evaluation, it will appear here with its conditions"
          />
        </Card>
      ) : (
        <>
          {/* Table view */}
          <Card noPadding className="mb-6">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-text-muted text-[11px] uppercase tracking-wider border-b border-border">
                    <th className="text-left py-2.5 px-4">Symbol</th>
                    <th className="text-left py-2.5 px-2">Action</th>
                    <th className="text-left py-2.5 px-2">Type</th>
                    <th className="text-right py-2.5 px-2">Deferred at Cycle</th>
                    <th className="text-right py-2.5 px-2">Wait Cycles</th>
                    <th className="text-right py-2.5 px-2">Price Condition</th>
                    <th className="text-right py-2.5 px-4">Created</th>
                  </tr>
                </thead>
                <tbody>
                  {deferred.map((d) => {
                    const cond = parseConditions(d.conditions);
                    const priceAbove = cond?.wait_until_price_above;
                    const priceBelow = cond?.wait_until_price_below;

                    return (
                      <tr key={d.id} className="border-b border-border/40 hover:bg-bg-card-hover transition-colors">
                        <td className="py-3 px-4 font-semibold text-text-primary">{d.symbol}</td>
                        <td className="py-3 px-2">
                          <span className={`text-xs font-bold px-2 py-0.5 rounded-md border ${
                            d.original_action === "BUY"
                              ? "text-profit bg-profit/10 border-profit/20"
                              : d.original_action === "SHORT"
                              ? "text-loss bg-loss/10 border-loss/20"
                              : "text-text-secondary bg-bg-elevated border-border-light"
                          }`}>
                            {d.original_action}
                          </span>
                        </td>
                        <td className="py-3 px-2">
                          <span className="text-xs text-text-muted bg-bg-elevated px-1.5 py-0.5 rounded border border-border-light">
                            {d.type}
                          </span>
                        </td>
                        <td className="py-3 px-2 text-right font-mono text-text-secondary">
                          #{d.deferred_at_cycle}
                        </td>
                        <td className="py-3 px-2 text-right font-mono text-text-secondary">
                          {cond?.wait_cycles ?? "—"}
                        </td>
                        <td className="py-3 px-2 text-right">
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
                        <td className="py-3 px-4 text-right">
                          <TimeAgo date={d.created_at} />
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </Card>

          {/* Cards view with details */}
          <h3 className="text-sm font-semibold text-text-muted uppercase tracking-wide mb-3">Details</h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {deferred.map((d) => {
              const cond = parseConditions(d.conditions);
              const priceAbove = cond?.wait_until_price_above;
              const priceBelow = cond?.wait_until_price_below;

              return (
                <div key={d.id} className="bg-bg-card border border-border rounded-xl p-4">
                  {/* Header */}
                  <div className="flex items-center gap-2 mb-3">
                    <span className="text-lg font-bold text-text-primary">{d.symbol}</span>
                    <span className={`text-xs font-bold px-2 py-0.5 rounded-md border ${
                      d.original_action === "BUY"
                        ? "text-profit bg-profit/10 border-profit/20"
                        : d.original_action === "SHORT"
                        ? "text-loss bg-loss/10 border-loss/20"
                        : "text-text-secondary bg-bg-elevated border-border-light"
                    }`}>
                      {d.original_action}
                    </span>
                    <span className="text-xs text-text-muted bg-bg-elevated px-1.5 py-0.5 rounded border border-border-light">
                      {d.type}
                    </span>
                    <span className="ml-auto">
                      <TimeAgo date={d.created_at} />
                    </span>
                  </div>

                  {/* Conditions */}
                  <div className="bg-bg-secondary rounded-lg p-3 border border-border-light space-y-2">
                    <span className="text-[11px] text-text-muted uppercase tracking-wide block">Conditions</span>

                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <span className="text-[10px] text-text-muted block">Deferred at Cycle</span>
                        <span className="text-sm font-mono font-semibold text-text-primary">#{d.deferred_at_cycle}</span>
                      </div>

                      {cond?.wait_cycles != null && (
                        <div>
                          <span className="text-[10px] text-text-muted block">Wait Cycles</span>
                          <span className="text-sm font-mono font-semibold text-accent">{cond.wait_cycles}</span>
                        </div>
                      )}

                      {priceAbove != null && (
                        <div>
                          <span className="text-[10px] text-text-muted block">Wait Until Price Above</span>
                          <span className="text-sm font-mono font-semibold text-profit">{formatPrice(priceAbove)}</span>
                        </div>
                      )}

                      {priceBelow != null && (
                        <div>
                          <span className="text-[10px] text-text-muted block">Wait Until Price Below</span>
                          <span className="text-sm font-mono font-semibold text-loss">{formatPrice(priceBelow)}</span>
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
