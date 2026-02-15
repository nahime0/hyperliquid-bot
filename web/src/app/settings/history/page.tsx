"use client";

import { useState, useCallback, useMemo } from "react";
import Link from "next/link";
import { useConfigHistory } from "@/hooks/useConfig";
import { Card, Skeleton } from "@/components/shared/Card";
import { TimeAgo } from "@/components/shared/TimeAgo";
import { FIELD_LABELS, PREFIX_COLORS, getAllSections } from "@/lib/config-fields";
import type { ConfigHistoryEntry } from "@/lib/types";

const PAGE_SIZE = 30;

export default function ConfigHistoryPage() {
  const [page, setPage] = useState(0);
  const [fieldFilter, setFieldFilter] = useState("");
  const [sectionFilter, setSectionFilter] = useState("");

  // Build the field query: if section is selected, find matching field keys
  const fieldQuery = useMemo(() => {
    if (fieldFilter) return fieldFilter;
    return undefined;
  }, [fieldFilter]);

  const { history, total, isLoading } = useConfigHistory(PAGE_SIZE, page * PAGE_SIZE, fieldQuery);

  // Filter client-side by section (API only supports exact field match)
  const filtered = useMemo(() => {
    if (!sectionFilter) return history;
    return history.filter((entry) => {
      const info = FIELD_LABELS[entry.field_name];
      return info?.section === sectionFilter;
    });
  }, [history, sectionFilter]);

  const totalPages = Math.ceil(total / PAGE_SIZE);
  const sections = useMemo(() => getAllSections(), []);

  const handleFieldSearch = useCallback((value: string) => {
    setFieldFilter(value);
    setPage(0);
  }, []);

  const handleSectionFilter = useCallback((value: string) => {
    setSectionFilter(value);
    setPage(0);
  }, []);

  return (
    <div className="max-w-5xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <Link
            href="/settings"
            className="text-text-muted hover:text-text-primary transition-colors"
            title="Back to Settings"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M10.5 19.5L3 12m0 0l7.5-7.5M3 12h18" />
            </svg>
          </Link>
          <div>
            <h1 className="text-lg font-bold text-text-primary">Configuration History</h1>
            <p className="text-xs text-text-muted">{total} change{total !== 1 ? "s" : ""} recorded</p>
          </div>
        </div>
      </div>

      {/* Filters */}
      <Card className="mb-4">
        <div className="flex flex-wrap items-center gap-3">
          {/* Field search */}
          <div className="flex items-center gap-2 flex-1 min-w-[200px]">
            <svg className="w-4 h-4 text-text-muted shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z" />
            </svg>
            <input
              type="text"
              placeholder="Search by field name..."
              value={fieldFilter}
              onChange={(e) => handleFieldSearch(e.target.value)}
              className="flex-1 bg-bg-primary border border-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-accent/40 focus:border-accent transition-colors"
            />
            {fieldFilter && (
              <button
                type="button"
                onClick={() => handleFieldSearch("")}
                className="text-text-muted hover:text-text-primary text-xs"
              >
                Clear
              </button>
            )}
          </div>

          {/* Section dropdown */}
          <select
            value={sectionFilter}
            onChange={(e) => handleSectionFilter(e.target.value)}
            className="bg-bg-primary border border-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-accent/40 focus:border-accent transition-colors"
          >
            <option value="">All sections</option>
            {sections.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </div>
      </Card>

      {/* Table */}
      <Card noPadding>
        {isLoading ? (
          <div className="p-5">
            <Skeleton className="w-full h-64" />
          </div>
        ) : filtered.length === 0 ? (
          <div className="p-10 text-center">
            <p className="text-sm text-text-muted">No changes found</p>
            {(fieldFilter || sectionFilter) && (
              <button
                type="button"
                onClick={() => { setFieldFilter(""); setSectionFilter(""); }}
                className="mt-2 text-xs text-accent hover:underline"
              >
                Clear filters
              </button>
            )}
          </div>
        ) : (
          <>
            {/* Table header */}
            <div className="grid grid-cols-[auto_1fr_200px_200px_100px] gap-4 px-5 py-3 border-b border-border text-[11px] font-medium text-text-muted uppercase tracking-wider">
              <span className="w-16">Section</span>
              <span>Field</span>
              <span>Old Value</span>
              <span>New Value</span>
              <span className="text-right">When</span>
            </div>

            {/* Rows */}
            <div className="divide-y divide-border/30">
              {filtered.map((entry) => (
                <HistoryRow key={entry.id} entry={entry} />
              ))}
            </div>
          </>
        )}
      </Card>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between mt-4 px-1">
          <p className="text-xs text-text-muted">
            Page {page + 1} of {totalPages}
          </p>
          <div className="flex items-center gap-2">
            <button
              type="button"
              disabled={page === 0}
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              className="px-3 py-1.5 text-xs font-medium rounded-lg border border-border text-text-muted hover:text-text-primary hover:bg-bg-card-hover transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
            >
              Previous
            </button>

            {/* Page numbers */}
            <div className="flex items-center gap-1">
              {Array.from({ length: Math.min(totalPages, 7) }, (_, i) => {
                let pageNum: number;
                if (totalPages <= 7) {
                  pageNum = i;
                } else if (page < 3) {
                  pageNum = i;
                } else if (page > totalPages - 4) {
                  pageNum = totalPages - 7 + i;
                } else {
                  pageNum = page - 3 + i;
                }
                return (
                  <button
                    key={pageNum}
                    type="button"
                    onClick={() => setPage(pageNum)}
                    className={`w-8 h-8 rounded-lg text-xs font-medium transition-colors ${
                      page === pageNum
                        ? "bg-accent text-white"
                        : "text-text-muted hover:text-text-primary hover:bg-bg-card-hover"
                    }`}
                  >
                    {pageNum + 1}
                  </button>
                );
              })}
            </div>

            <button
              type="button"
              disabled={page >= totalPages - 1}
              onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
              className="px-3 py-1.5 text-xs font-medium rounded-lg border border-border text-text-muted hover:text-text-primary hover:bg-bg-card-hover transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
            >
              Next
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function HistoryRow({ entry }: { entry: ConfigHistoryEntry }) {
  const info = FIELD_LABELS[entry.field_name];
  const prefix = entry.field_name.split("_")[0];
  const prefixColor = PREFIX_COLORS[prefix] ?? "text-text-muted bg-bg-elevated";

  const isBoolField = entry.old_value === "0" || entry.old_value === "1" || entry.new_value === "0" || entry.new_value === "1";
  const isToggle = isBoolField && (
    (entry.old_value === "0" && entry.new_value === "1") ||
    (entry.old_value === "1" && entry.new_value === "0")
  );

  const formatValue = (val: string | null, isNew: boolean) => {
    if (val === null || val === "") return <span className="text-text-muted">—</span>;
    if (isToggle) {
      const on = val === "1";
      return (
        <span className={`inline-flex items-center gap-1 text-xs font-medium ${on ? "text-profit" : "text-loss"}`}>
          <span className={`w-1.5 h-1.5 rounded-full ${on ? "bg-profit" : "bg-loss"}`} />
          {on ? "ON" : "OFF"}
        </span>
      );
    }
    return (
      <span
        className={`font-mono text-xs truncate ${isNew ? "text-profit font-medium" : "text-loss"}`}
        title={val}
      >
        {val}
      </span>
    );
  };

  return (
    <div className="grid grid-cols-[auto_1fr_200px_200px_100px] gap-4 px-5 py-3 items-center hover:bg-bg-card-hover/50 transition-colors">
      {/* Section badge */}
      <div className="w-16">
        <span className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-bold uppercase whitespace-nowrap ${prefixColor}`}>
          {prefix}
        </span>
      </div>

      {/* Field: label + key */}
      <div className="min-w-0">
        <div className="text-sm font-medium text-text-primary truncate">
          {info?.label ?? entry.field_name}
        </div>
        <div className="text-[11px] text-text-muted font-mono truncate">{entry.field_name}</div>
      </div>

      {/* Old value */}
      <div className="min-w-0 flex items-center">
        {formatValue(entry.old_value, false)}
      </div>

      {/* New value */}
      <div className="min-w-0 flex items-center gap-2">
        <svg className="w-3 h-3 text-text-muted shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 4.5L21 12m0 0l-7.5 7.5M21 12H3" />
        </svg>
        {formatValue(entry.new_value, true)}
      </div>

      {/* Timestamp */}
      <div className="text-right">
        <TimeAgo date={entry.timestamp} />
      </div>
    </div>
  );
}
