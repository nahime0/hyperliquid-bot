import useSWR from "swr";
import type { Order } from "@/lib/types";

const fetcher = (url: string) => fetch(url).then((r) => r.json());

interface OrdersResponse {
  orders: Order[];
  total: number;
  limit: number;
  offset: number;
}

export function useOrders(limit = 50, offset = 0) {
  const { data, error, isLoading } = useSWR<OrdersResponse>(
    `/api/orders?limit=${limit}&offset=${offset}`,
    fetcher,
    { refreshInterval: 10000 }
  );
  return {
    orders: data?.orders ?? [],
    total: data?.total ?? 0,
    error,
    isLoading,
  };
}
