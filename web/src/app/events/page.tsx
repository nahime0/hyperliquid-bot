"use client";

import { useState } from "react";
import { useEvents } from "@/hooks/useEvents";
import { Card, Skeleton } from "@/components/shared/Card";
import { EventTypeBadge } from "@/components/shared/EventTypeBadge";
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

export default function EventsPage() {
  const [eventType, setEventType] = useState("");
  const [symbol, setSymbol] = useState("");
  const [page, setPage] = useState(0);
  const limit = 50;

  const { events, total, isLoading } = useEvents({
    limit,
    offset: page * limit,
    event_type: eventType || undefined,
    symbol: symbol || undefined,
  });

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
        {(eventType || symbol) && (
          <button
            onClick={() => { setEventType(""); setSymbol(""); setPage(0); }}
            className="text-xs text-text-muted hover:text-accent transition-colors"
          >
            Clear filters
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
            <div className="divide-y divide-border/30">
              {events.map((e) => {
                const details = parseDetails(e.details);
                const isExpanded = expanded.has(e.id);

                return (
                  <div key={e.id}>
                    <div
                      className="flex items-center gap-3 py-3 px-4 hover:bg-bg-card-hover cursor-pointer transition-colors"
                      onClick={() => toggleExpand(e.id)}
                    >
                      <span className="text-text-muted text-xs font-mono w-12 text-right shrink-0">
                        #{e.cycle}
                      </span>
                      <EventTypeBadge type={e.event_type} />
                      <span className="font-semibold text-sm text-text-primary w-16 shrink-0">{e.symbol}</span>
                      {e.action && (
                        <span className="text-xs font-medium text-accent bg-accent/5 px-1.5 py-0.5 rounded">
                          {e.action}
                        </span>
                      )}
                      {e.confidence !== null && (
                        <span className="text-xs text-text-muted font-mono">
                          {(e.confidence * 100).toFixed(0)}%
                        </span>
                      )}
                      {e.reasoning && (
                        <span className="text-xs text-text-muted truncate max-w-xs hidden lg:inline">
                          {e.reasoning}
                        </span>
                      )}
                      <span className="ml-auto shrink-0"><TimeAgo date={e.timestamp} /></span>
                      <span className="text-text-muted text-xs shrink-0 w-4">
                        {isExpanded ? "▼" : "▶"}
                      </span>
                    </div>
                    {isExpanded && (
                      <div className="px-4 pb-4 pt-1 ml-12 space-y-2">
                        {e.reasoning && (
                          <div className="bg-bg-secondary rounded-lg p-3 border border-border-light">
                            <span className="text-[11px] text-text-muted uppercase tracking-wide block mb-1">Reasoning</span>
                            <p className="text-xs text-text-secondary leading-relaxed">{e.reasoning}</p>
                          </div>
                        )}
                        {details && (
                          <div className="bg-bg-secondary rounded-lg p-3 border border-border-light">
                            <span className="text-[11px] text-text-muted uppercase tracking-wide block mb-1">Details</span>
                            <pre className="text-xs text-text-muted font-mono overflow-x-auto leading-relaxed">
                              {JSON.stringify(details, null, 2)}
                            </pre>
                          </div>
                        )}
                        <div className="flex gap-4 text-xs text-text-muted">
                          <span>Source: <span className="text-text-secondary">{e.source}</span></span>
                          {e.position_id && <span>Position: <span className="text-accent">#{e.position_id}</span></span>}
                          {e.trade_id && <span>Trade: <span className="text-accent">#{e.trade_id}</span></span>}
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
