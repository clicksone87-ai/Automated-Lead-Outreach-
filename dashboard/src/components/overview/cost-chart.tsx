"use client";

import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { Costs } from "@/lib/api";
import { usd } from "@/lib/format";

/** Single-series daily spend: 2px line, soft fill, recessive grid, crosshair tooltip. */
export function CostChart({ data, dailyCap }: { data: Costs["daily"]; dailyCap: number }) {
  // Fill missing days so the line reads as time, not as a list of points.
  const byDay = new Map(data.map((d) => [d.day, d.amount_usd]));
  const days: { day: string; amount: number }[] = [];
  for (let i = 29; i >= 0; i--) {
    const d = new Date();
    d.setUTCDate(d.getUTCDate() - i);
    const key = d.toISOString().slice(0, 10);
    days.push({ day: key, amount: byDay.get(key) ?? 0 });
  }
  return (
    <div className="h-44 w-full" role="img" aria-label="Daily API spend over the last 30 days">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={days} margin={{ top: 8, right: 4, bottom: 0, left: -12 }}>
          <defs>
            <linearGradient id="spendFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--chart-1)" stopOpacity={0.28} />
              <stop offset="100%" stopColor="var(--chart-1)" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid vertical={false} stroke="var(--border)" strokeDasharray="3 3" />
          <XAxis
            dataKey="day"
            tickLine={false}
            axisLine={false}
            interval={6}
            tick={{ fill: "var(--muted-foreground)", fontSize: 11 }}
            tickFormatter={(d: string) => new Date(d).toLocaleDateString("en-GB", { day: "numeric", month: "short" })}
          />
          <YAxis
            tickLine={false}
            axisLine={false}
            width={48}
            tick={{ fill: "var(--muted-foreground)", fontSize: 11 }}
            tickFormatter={(v: number) => `$${v.toFixed(v < 1 ? 2 : 0)}`}
            domain={[0, (max: number) => Math.max(max, dailyCap * 0.25, 0.5)]}
          />
          <Tooltip
            cursor={{ stroke: "var(--border-strong)", strokeWidth: 1 }}
            content={({ active, payload }) =>
              active && payload?.[0] ? (
                <div className="rounded-lg border bg-popover px-3 py-2 text-xs shadow-elevated">
                  <div className="text-muted-foreground">
                    {new Date(String(payload[0].payload.day)).toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" })}
                  </div>
                  <div className="tabular mt-0.5 font-semibold text-foreground">{usd(Number(payload[0].value))}</div>
                </div>
              ) : null
            }
          />
          <Area
            type="monotone"
            dataKey="amount"
            stroke="var(--chart-1)"
            strokeWidth={2}
            fill="url(#spendFill)"
            activeDot={{ r: 4, strokeWidth: 2, stroke: "var(--card)" }}
            isAnimationActive
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
