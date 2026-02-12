"use client";

import { useEffect, useState } from "react";
import { timeAgo } from "@/lib/format";

export function TimeAgo({ date, className = "" }: { date: string; className?: string }) {
  const [text, setText] = useState(() => timeAgo(date));

  useEffect(() => {
    setText(timeAgo(date));
    const id = setInterval(() => setText(timeAgo(date)), 10_000);
    return () => clearInterval(id);
  }, [date]);

  return <span className={`text-text-muted text-xs ${className}`}>{text}</span>;
}
