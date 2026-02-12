"use client";

export function Pagination({
  page,
  totalPages,
  onPageChange,
}: {
  page: number;
  totalPages: number;
  onPageChange: (page: number) => void;
}) {
  if (totalPages <= 1) return null;

  return (
    <div className="flex items-center justify-center gap-1.5 pt-4 border-t border-border mt-4">
      <button
        onClick={() => onPageChange(Math.max(0, page - 1))}
        disabled={page === 0}
        className="px-3 py-1.5 rounded-lg text-xs font-medium border border-border bg-bg-card text-text-secondary hover:bg-bg-card-hover hover:text-text-primary disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
      >
        Prev
      </button>
      <span className="px-3 py-1.5 text-xs text-text-muted font-mono">
        {page + 1} / {totalPages}
      </span>
      <button
        onClick={() => onPageChange(Math.min(totalPages - 1, page + 1))}
        disabled={page >= totalPages - 1}
        className="px-3 py-1.5 rounded-lg text-xs font-medium border border-border bg-bg-card text-text-secondary hover:bg-bg-card-hover hover:text-text-primary disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
      >
        Next
      </button>
    </div>
  );
}
