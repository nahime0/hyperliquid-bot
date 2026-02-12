"use client";

import { useState } from "react";
import useSWR from "swr";
import Link from "next/link";
import type { Coin } from "@/lib/types";
import { TimeAgo } from "@/components/shared/TimeAgo";
import { Skeleton } from "@/components/shared/Card";
import { EmptyState } from "@/components/shared/EmptyState";
import { formatPrice } from "@/lib/format";

const fetcher = (url: string) => fetch(url).then((r) => r.json());

export default function CoinsPage() {
  const [showAll, setShowAll] = useState(false);
  const { data: coins, isLoading } = useSWR<Coin[]>(
    `/api/coins${showAll ? "?all=true" : ""}`,
    fetcher,
    { refreshInterval: 30000 }
  );

  const activeCount = coins?.filter(c => c.is_active).length ?? 0;
  const inactiveCount = (coins?.length ?? 0) - activeCount;

  return (
    <div>
      <div className="flex items-center justify-between mb-5">
        <div className="flex items-center gap-3">
          <h2 className="text-lg font-bold text-text-primary">Coins</h2>
          {coins && (
            <span className="text-xs text-text-muted font-mono">
              {activeCount} active{showAll ? ` / ${inactiveCount} inactive` : ""}
            </span>
          )}
        </div>
        <button
          onClick={() => setShowAll(!showAll)}
          className="text-xs text-text-muted hover:text-accent transition-colors"
        >
          {showAll ? "Show active only" : "Show all"}
        </button>
      </div>

      {isLoading ? (
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-3">
          {Array.from({ length: 10 }).map((_, i) => (
            <Skeleton key={i} className="w-full h-28 rounded-xl" />
          ))}
        </div>
      ) : !coins || coins.length === 0 ? (
        <EmptyState title="No coins found" description="Coins will appear here as the bot discovers them" />
      ) : (
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-3">
          {coins.map((coin) => (
            <Link
              key={coin.symbol}
              href={`/coins/${coin.symbol}`}
              className="bg-bg-card border border-border rounded-xl p-4 hover:bg-bg-card-hover hover:border-accent/30 transition-all group"
            >
              <div className="flex items-center justify-between mb-3">
                <span className="font-bold text-sm text-text-primary group-hover:text-accent transition-colors">
                  {coin.symbol}
                </span>
                <span className={`w-2.5 h-2.5 rounded-full ${coin.is_active ? "bg-profit pulse-dot" : "bg-text-muted/40"}`} />
              </div>
              <div className="space-y-1.5">
                {coin.avg_volume_24h > 0 && (
                  <div className="text-xs text-text-muted">
                    Vol 24h: <span className="text-text-secondary font-mono">${formatPrice(coin.avg_volume_24h)}</span>
                  </div>
                )}
                {coin.sz_decimals !== null && (
                  <div className="text-xs text-text-muted">
                    Size dec: <span className="text-text-secondary">{coin.sz_decimals}</span>
                  </div>
                )}
              </div>
              <div className="mt-3 pt-2 border-t border-border/50">
                <TimeAgo date={coin.last_seen} className="text-[10px]" />
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
