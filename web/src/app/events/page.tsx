"use client";

import { useState } from "react";
import { useEvents } from "@/hooks/useEvents";
import { Card } from "@/components/shared/Card";
import { EventTypeBadge } from "@/components/shared/EventTypeBadge";
import { TimeAgo } from "@/components/shared/TimeAgo";

const EVENT_TYPES = [
  "SIGNAL", "AI_REVIEW", "DEFERRED", "RISK_APPROVED", "RISK_BLOCKED",
  "TRADE_ENTRY", "TRADE_EXIT", "POSITION_SCALED", "TRAILING_UPDATE",
  "SL_TP_TRIGGER", "POSITION_ADJUSTED",
] as const;

export default function EventsPage() {
  const [eventType, setEventType] = useState("");
  const [symbol, setSymbol] = useState("");
  const [page, setPage] = useState(0);
  const limit = 50;

  const { events, total } = useEvents({
    limit,
    offset: page * limit,
    event_type: eventType || undefined,
    symbol: symbol || undefined,
  });

  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  const toggleExpand = (id: number) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <div>
      <h2 className="text-xl font-bold mb-4">Events</h2>

      {/* Filters */}
      <div className="flex flex-wrap gap-3 mb-4">
        <select
          value={eventType}
          onChange={(e) => { setEventType(e.target.value); setPage(0); }}
          className="bg-bg-card border border-border rounded px-3 py-1.5 text-sm text-text-primary"
        >
          <option value="">All types</option>
          {EVENT_TYPES.map((t) => (
            <option key={t} value={t}>{t}</option>
          ))}
        </select>
        <input
          type="text"
          placeholder="Filter by symbol..."
          value={symbol}
          onChange={(e) => { setSymbol(e.target.value.toUpperCase()); setPage(0); }}
          className="bg-bg-card border border-border rounded px-3 py-1.5 text-sm text-text-primary placeholder:text-text-muted w-40"
        />
        <span className="text-text-muted text-sm self-center">{total} events</span>
      </div>

      <Card>
        {events.length === 0 ? (
          <p className="text-text-muted text-sm py-4 text-center">No events match filters</p>
        ) : (
          <div className="space-y-0">
            {events.map((e) => (
              <div key={e.id}>
                <div
                  className="flex items-center gap-3 py-2 px-1 border-b border-border/30 hover:bg-bg-card-hover cursor-pointer"
                  onClick={() => toggleExpand(e.id)}
                >
                  <span className="text-text-muted text-xs font-mono w-10">#{e.cycle}</span>
                  <EventTypeBadge type={e.event_type} />
                  <span className="font-medium text-sm w-16">{e.symbol}</span>
                  {e.action && <span className="text-xs text-accent">{e.action}</span>}
                  {e.confidence !== null && (
                    <span className="text-xs text-text-muted">conf {(e.confidence * 100).toFixed(0)}%</span>
                  )}
                  <span className="ml-auto"><TimeAgo date={e.timestamp} /></span>
                  <span className="text-text-muted text-xs">{expanded.has(e.id) ? "▼" : "▶"}</span>
                </div>
                {expanded.has(e.id) && (
                  <div className="pl-12 pr-4 py-2 bg-bg-primary/50 text-xs space-y-1 border-b border-border/30">
                    {e.reasoning && (
                      <p><span className="text-text-muted">Reasoning:</span> {e.reasoning}</p>
                    )}
                    {e.details && e.details !== "{}" && (
                      <pre className="text-text-muted overflow-x-auto">
                        {JSON.stringify(JSON.parse(e.details), null, 2)}
                      </pre>
                    )}
                    <p className="text-text-muted">
                      Source: {e.source}
                      {e.position_id && ` | Position #${e.position_id}`}
                      {e.trade_id && ` | Trade #${e.trade_id}`}
                    </p>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}

        {/* Pagination */}
        <div className="flex gap-2 mt-4 justify-center">
          <button
            onClick={() => setPage(Math.max(0, page - 1))}
            disabled={page === 0}
            className="px-3 py-1 rounded text-sm border border-border disabled:opacity-30 hover:bg-bg-card-hover"
          >
            Prev
          </button>
          <span className="px-3 py-1 text-sm text-text-muted">
            Page {page + 1} of {Math.ceil(total / limit) || 1}
          </span>
          <button
            onClick={() => setPage(page + 1)}
            disabled={(page + 1) * limit >= total}
            className="px-3 py-1 rounded text-sm border border-border disabled:opacity-30 hover:bg-bg-card-hover"
          >
            Next
          </button>
        </div>
      </Card>
    </div>
  );
}
