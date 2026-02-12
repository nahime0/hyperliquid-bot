"use client";

import useSWR from "swr";
import Link from "next/link";
import { useStatus } from "@/hooks/useStatus";
import type { Coin } from "@/lib/types";
import { TimeAgo } from "@/components/shared/TimeAgo";

const fetcher = (url: string) => fetch(url).then((r) => r.json());

function getSignalData(status: ReturnType<typeof useStatus>["status"], symbol: string) {
  if (!status?.strategies) return null;
  const strats = status.strategies as Record<string, unknown>;
  const subs = strats.sub_strategies as Record<string, { signals?: Record<string, Record<string, unknown>> }> | undefined;
  if (subs) {
    for (const sub of Object.values(subs)) {
      if (sub.signals?.[symbol]) return sub.signals[symbol];
    }
  }
  const signals = strats.signals as Record<string, Record<string, unknown>> | undefined;
  if (signals?.[symbol]) return signals[symbol];
  return null;
}

export default function CoinsPage() {
  const { data: coins } = useSWR<Coin[]>("/api/coins", fetcher, { refreshInterval: 30000 });
  const { status } = useStatus();

  return (
    <div>
      <h2 className="text-xl font-bold mb-4">Coins ({coins?.length ?? 0})</h2>
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-3">
        {coins?.map((coin) => {
          const sig = getSignalData(status, coin.symbol);
          const price = sig?.price as number | undefined;
          const rsi = sig?.rsi as number | undefined;
          const trend = sig?.trend as string | undefined;
          const signal = sig?.signal as string | undefined;
          const divergence = sig?.divergence as string | undefined;

          const hasSignal = (signal && signal !== "NEUTRAL") || (divergence && divergence !== "NONE");

          return (
            <Link
              key={coin.symbol}
              href={`/coins/${coin.symbol}`}
              className="bg-bg-card border border-border rounded-lg p-3 hover:bg-bg-card-hover hover:border-accent/30 transition-colors"
            >
              <div className="flex items-center justify-between mb-2">
                <span className="font-bold text-sm">{coin.symbol}</span>
                {hasSignal && (
                  <span className="w-2 h-2 rounded-full bg-accent animate-pulse" />
                )}
              </div>
              <div className="text-lg font-mono font-bold mb-1">
                {price ? price.toLocaleString("en-US", { maximumFractionDigits: price >= 1 ? 2 : 6 }) : "—"}
              </div>
              <div className="flex items-center gap-2 text-xs">
                {rsi !== undefined && (
                  <span className={`${rsi < 30 ? "text-profit" : rsi > 70 ? "text-loss" : "text-text-muted"}`}>
                    RSI {rsi.toFixed(0)}
                  </span>
                )}
                {trend && (
                  <span className={`${
                    trend === "BULLISH" ? "text-profit" : trend === "BEARISH" ? "text-loss" : "text-text-muted"
                  }`}>
                    {trend}
                  </span>
                )}
              </div>
              {divergence && divergence !== "NONE" && (
                <div className="mt-1">
                  <span className={`text-xs px-1.5 py-0.5 rounded ${
                    divergence.includes("BULLISH") ? "bg-profit/15 text-profit" : "bg-loss/15 text-loss"
                  }`}>
                    {divergence}
                  </span>
                </div>
              )}
              <div className="mt-2 text-xs text-text-muted">
                <TimeAgo date={coin.last_seen} />
              </div>
            </Link>
          );
        })}
      </div>
    </div>
  );
}
