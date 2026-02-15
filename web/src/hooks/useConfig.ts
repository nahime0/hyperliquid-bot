import useSWR from "swr";
import type { BotConfig, ConfigHistoryEntry } from "@/lib/types";

const fetcher = (url: string) => fetch(url).then((r) => r.json());

export function useConfig() {
  const { data, error, isLoading, mutate } = useSWR<BotConfig>("/api/config", fetcher);
  return { config: data, error, isLoading, mutate };
}

export function useConfigHistory(limit = 100, offset = 0, field?: string) {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (field) params.set("field", field);

  const { data, error, isLoading, mutate } = useSWR<{ history: ConfigHistoryEntry[]; total: number }>(
    `/api/config/history?${params.toString()}`,
    fetcher,
  );
  return {
    history: data?.history ?? [],
    total: data?.total ?? 0,
    error,
    isLoading,
    mutate,
  };
}

export async function saveConfig(changes: Record<string, unknown>): Promise<{
  ok: boolean;
  changed: Record<string, { old: string; new: string }>;
  error?: string;
}> {
  const res = await fetch("/api/config", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(changes),
  });
  const data = await res.json();
  if (!res.ok) {
    return { ok: false, changed: {}, error: data.error || `HTTP ${res.status}` };
  }
  return data;
}
