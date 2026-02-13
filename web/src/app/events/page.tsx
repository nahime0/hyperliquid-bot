"use client";

import { Fragment, useState } from "react";
import { useEvents } from "@/hooks/useEvents";
import { Card, Skeleton } from "@/components/shared/Card";
import { EventTypeBadge } from "@/components/shared/EventTypeBadge";
import { ScoreBar } from "@/components/shared/ScoreBar";
import { TimeAgo } from "@/components/shared/TimeAgo";
import { EmptyState } from "@/components/shared/EmptyState";
import { Pagination } from "@/components/shared/Pagination";

const EVENT_TYPES = [
  "SIGNAL", "AI_REVIEW", "DEFERRED", "RISK_APPROVED", "RISK_BLOCKED",
  "TRADE_ENTRY", "TRADE_EXIT", "POSITION_SCALED", "TRAILING_UPDATE",
  "SL_TP_TRIGGER", "POSITION_ADJUSTED",
] as const;

function parseDetails(details: string): Record<string, unknown> | null {
  if (!details || details === "{}") return null;
  try {
    return JSON.parse(details);
  } catch {
    return null;
  }
}

const SCORE_OPTIONS = [
  { value: 0, label: "All" },
  { value: 30, label: "≥ 30%" },
  { value: 40, label: "≥ 40%" },
  { value: 50, label: "≥ 50%" },
  { value: 60, label: "≥ 60%" },
  { value: 70, label: "≥ 70%" },
  { value: 80, label: "≥ 80%" },
] as const;

export default function EventsPage() {
  const [eventType, setEventType] = useState("");
  const [symbol, setSymbol] = useState("");
  const [minScore, setMinScore] = useState(50);
  const [page, setPage] = useState(0);
  const limit = 50;

  const { events: rawEvents, total, isLoading } = useEvents({
    limit,
    offset: page * limit,
    event_type: eventType || undefined,
    symbol: symbol || undefined,
  });

  // Filter: SIGNAL events by min score, DEFERRED always visible, rest always visible
  const events = minScore > 0
    ? rawEvents.filter((e) => {
        if (e.event_type === "SIGNAL") {
          return e.confidence !== null && e.confidence * 100 >= minScore;
        }
        return true; // DEFERRED and all other types always pass
      })
    : rawEvents;

  const filteredCount = rawEvents.length - events.length;

  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  const toggleExpand = (id: number) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  const totalPages = Math.max(1, Math.ceil(total / limit));

  return (
    <div>
      <div className="flex items-center justify-between mb-5">
        <h2 className="text-lg font-bold text-text-primary">Events</h2>
        <span className="text-xs text-text-muted font-mono">{total.toLocaleString()} total</span>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-3 mb-5">
        <select
          value={eventType}
          onChange={(e) => { setEventType(e.target.value); setPage(0); }}
          className="bg-bg-card border border-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:border-accent/50"
        >
          <option value="">All types</option>
          {EVENT_TYPES.map((t) => (
            <option key={t} value={t}>{t.replace(/_/g, " ")}</option>
          ))}
        </select>
        <input
          type="text"
          placeholder="Filter by symbol..."
          value={symbol}
          onChange={(e) => { setSymbol(e.target.value.toUpperCase()); setPage(0); }}
          className="bg-bg-card border border-border rounded-lg px-3 py-2 text-sm text-text-primary placeholder:text-text-muted w-44 focus:outline-none focus:border-accent/50"
        />
        <div className="flex items-center gap-2">
          <span className="text-xs text-text-muted">Min Score:</span>
          <select
            value={minScore}
            onChange={(e) => { setMinScore(Number(e.target.value)); setPage(0); }}
            className="bg-bg-card border border-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:border-accent/50"
          >
            {SCORE_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
        </div>
        {filteredCount > 0 && (
          <span className="text-[11px] text-text-muted self-center">
            {filteredCount} signal{filteredCount !== 1 ? "s" : ""} hidden
          </span>
        )}
        {(eventType || symbol || minScore !== 50) && (
          <button
            onClick={() => { setEventType(""); setSymbol(""); setMinScore(50); setPage(0); }}
            className="text-xs text-text-muted hover:text-accent transition-colors"
          >
            Reset filters
          </button>
        )}
      </div>

      <Card noPadding>
        {isLoading && page === 0 ? (
          <div className="p-5"><Skeleton className="w-full h-60" /></div>
        ) : events.length === 0 ? (
          <div className="p-5">
            <EmptyState title="No events match filters" description="Try adjusting the filters or wait for new events" />
          </div>
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-text-muted text-[11px] uppercase tracking-wider border-b border-border">
                    <th className="w-6 py-2.5 px-3" />
                    <th className="text-right py-2.5 px-2">Cycle</th>
                    <th className="text-left py-2.5 px-2">Type</th>
                    <th className="text-left py-2.5 px-2">Symbol</th>
                    <th className="text-left py-2.5 px-2">Action</th>
                    <th className="text-left py-2.5 px-2">Score</th>
                    <th className="text-left py-2.5 px-2">Source</th>
                    <th className="text-left py-2.5 px-2 hidden xl:table-cell">Reasoning</th>
                    <th className="text-right py-2.5 px-3">Time</th>
                  </tr>
                </thead>
                <tbody>
                  {events.map((e) => {
                    const details = parseDetails(e.details);
                    const isExpanded = expanded.has(e.id);

                    return (
                      <Fragment key={e.id}>
                        <tr
                          className="border-b border-border/40 hover:bg-bg-card-hover cursor-pointer transition-colors"
                          onClick={() => toggleExpand(e.id)}
                        >
                          <td className="py-2.5 px-3 text-text-muted text-xs">
                            {isExpanded ? "▼" : "▶"}
                          </td>
                          <td className="py-2.5 px-2 text-text-muted text-xs font-mono text-right">
                            #{e.cycle}
                          </td>
                          <td className="py-2.5 px-2">
                            <EventTypeBadge type={e.event_type} />
                          </td>
                          <td className="py-2.5 px-2 font-semibold text-text-primary">
                            {e.symbol}
                          </td>
                          <td className="py-2.5 px-2">
                            {e.action ? (
                              <span className="text-xs font-medium text-accent bg-accent/5 px-1.5 py-0.5 rounded">
                                {e.action}
                              </span>
                            ) : (
                              <span className="text-text-muted">—</span>
                            )}
                          </td>
                          <td className="py-2.5 px-2">
                            {e.confidence !== null ? (
                              <div className="w-24">
                                <ScoreBar value={e.confidence} size={e.event_type === "SIGNAL" ? "md" : "sm"} />
                              </div>
                            ) : (
                              <span className="text-text-muted">—</span>
                            )}
                          </td>
                          <td className="py-2.5 px-2 text-text-muted text-xs">
                            {e.source}
                          </td>
                          <td className="py-2.5 px-2 text-text-muted text-xs truncate max-w-xs hidden xl:table-cell">
                            {e.reasoning ?? "—"}
                          </td>
                          <td className="py-2.5 px-3 text-right">
                            <TimeAgo date={e.timestamp} />
                          </td>
                        </tr>
                        {isExpanded && (
                          <tr>
                            <td colSpan={9} className="p-0">
                              <div className="px-5 pb-4 pt-2 bg-bg-primary/30 space-y-2">
                                {/* Signal indicators grid */}
                                {e.event_type === "SIGNAL" && details && (
                                  <div className="bg-bg-secondary rounded-lg p-3 border border-border-light">
                                    <span className="text-[11px] text-text-muted uppercase tracking-wide block mb-2">Signal Indicators</span>
                                    <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-3">
                                      {e.confidence !== null && (
                                        <div>
                                          <span className="text-[10px] text-text-muted block">Score</span>
                                          <div className="mt-0.5 w-20"><ScoreBar value={e.confidence} size="md" /></div>
                                        </div>
                                      )}
                                      {details.rsi != null && (
                                        <div>
                                          <span className="text-[10px] text-text-muted block">RSI</span>
                                          <span className={`text-sm font-mono font-semibold ${Number(details.rsi) < 35 ? "text-profit" : Number(details.rsi) > 65 ? "text-loss" : "text-text-secondary"}`}>
                                            {Number(details.rsi).toFixed(1)}
                                          </span>
                                        </div>
                                      )}
                                      {details.rsi_1h != null && (
                                        <div>
                                          <span className="text-[10px] text-text-muted block">RSI 1H</span>
                                          <span className={`text-sm font-mono font-semibold ${Number(details.rsi_1h) < 35 ? "text-profit" : Number(details.rsi_1h) > 65 ? "text-loss" : "text-text-secondary"}`}>
                                            {Number(details.rsi_1h).toFixed(1)}
                                          </span>
                                        </div>
                                      )}
                                      {details.bb_pct != null && (
                                        <div>
                                          <span className="text-[10px] text-text-muted block">BB%</span>
                                          <span className="text-sm font-mono font-semibold text-text-secondary">
                                            {(Number(details.bb_pct) * 100).toFixed(1)}%
                                          </span>
                                        </div>
                                      )}
                                      {details.volume_ratio != null && (
                                        <div>
                                          <span className="text-[10px] text-text-muted block">Vol Ratio</span>
                                          <span className={`text-sm font-mono font-semibold ${Number(details.volume_ratio) > 1.5 ? "text-profit" : "text-text-secondary"}`}>
                                            {Number(details.volume_ratio).toFixed(2)}x
                                          </span>
                                        </div>
                                      )}
                                      {details.trend != null && (
                                        <div>
                                          <span className="text-[10px] text-text-muted block">Trend</span>
                                          <span className={`text-sm font-semibold ${details.trend === "up" ? "text-profit" : details.trend === "down" ? "text-loss" : "text-text-secondary"}`}>
                                            {String(details.trend)}
                                          </span>
                                        </div>
                                      )}
                                      {details.divergence != null && (
                                        <div>
                                          <span className="text-[10px] text-text-muted block">Divergence</span>
                                          <span className="text-sm font-semibold text-purple">
                                            {String(details.divergence)}
                                          </span>
                                        </div>
                                      )}
                                    </div>
                                    {details.reason != null && (
                                      <div className="mt-2 pt-2 border-t border-border/30">
                                        <span className="text-[10px] text-text-muted">Reason: </span>
                                        <span className="text-xs text-text-secondary">{String(details.reason)}</span>
                                      </div>
                                    )}
                                  </div>
                                )}
                                {/* Reasoning */}
                                {e.reasoning && (
                                  <div className="bg-bg-secondary rounded-lg p-3 border border-border-light">
                                    <span className="text-[11px] text-text-muted uppercase tracking-wide block mb-1">Reasoning</span>
                                    <p className="text-xs text-text-secondary leading-relaxed">{e.reasoning}</p>
                                  </div>
                                )}
                                {/* Raw details for non-signal events */}
                                {details && e.event_type !== "SIGNAL" && (
                                  <div className="bg-bg-secondary rounded-lg p-3 border border-border-light">
                                    <span className="text-[11px] text-text-muted uppercase tracking-wide block mb-1">Details</span>
                                    <pre className="text-xs text-text-muted font-mono overflow-x-auto leading-relaxed">
                                      {JSON.stringify(details, null, 2)}
                                    </pre>
                                  </div>
                                )}
                                {/* Metadata */}
                                <div className="flex gap-4 text-xs text-text-muted">
                                  {e.position_id && <span>Position: <span className="text-accent">#{e.position_id}</span></span>}
                                  {e.trade_id && <span>Trade: <span className="text-accent">#{e.trade_id}</span></span>}
                                </div>
                              </div>
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
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
