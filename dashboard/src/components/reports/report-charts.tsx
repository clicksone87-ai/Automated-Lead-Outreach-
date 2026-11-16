"use client";

import { Bar, BarChart, CartesianGrid, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { DimRow, ReportData } from "@/lib/ops";

const STAGES: { key: keyof ReportData["funnel"]; label: string }[] = [
  { key: "discovered", label: "Discovered" },
  { key: "qualified", label: "Qualified" },
  { key: "sent", label: "Sent (real)" },
  { key: "replied", label: "Replied" },
  { key: "positive", label: "Positive" },
  { key: "won", label: "Won" },
];

function ChartTooltip({ active, payload, label }: { active?: boolean; payload?: { value: number }[]; label?: string }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-md border bg-popover px-2.5 py-1.5 text-xs shadow-soft">
      <div className="font-medium">{label}</div>
      <div className="tabular text-muted-foreground">{payload[0]?.value.toLocaleString()}</div>
    </div>
  );
}

/** Single-series horizontal bars (one hue, value labels, hover tooltip, recessive grid). */
function HBars({ rows, ariaLabel }: { rows: { label: string; value: number }[]; ariaLabel: string }) {
  const height = Math.max(120, rows.length * 34 + 24);
  return (
    <div style={{ height }} className="w-full" role="img" aria-label={ariaLabel}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 40, bottom: 4, left: 4 }} barCategoryGap={8}>
          <CartesianGrid horizontal={false} stroke="var(--border)" strokeDasharray="3 3" />
          <XAxis type="number" allowDecimals={false} tickLine={false} axisLine={false} tick={{ fill: "var(--muted-foreground)", fontSize: 11 }} />
          <YAxis type="category" dataKey="label" width={130} tickLine={false} axisLine={false} tick={{ fill: "var(--foreground)", fontSize: 12 }} />
          <Tooltip content={<ChartTooltip />} cursor={{ fill: "var(--muted)", opacity: 0.5 }} />
          <Bar dataKey="value" fill="var(--chart-1)" radius={[0, 4, 4, 0]} barSize={16} isAnimationActive={false}>
            <LabelList dataKey="value" position="right" className="tabular" style={{ fill: "var(--muted-foreground)", fontSize: 11 }} />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export function FunnelChart({ funnel }: { funnel: ReportData["funnel"] }) {
  return <HBars rows={STAGES.map((s) => ({ label: s.label, value: funnel[s.key] }))} ariaLabel="Lead funnel for the period" />;
}

export function BreakdownChart({ rows, dimension }: { rows: DimRow[]; dimension: string }) {
  const top = rows.slice(0, 10).map((r) => ({ label: r.key.length > 20 ? `${r.key.slice(0, 19)}…` : r.key, value: r.discovered }));
  return <HBars rows={top} ariaLabel={`Leads discovered by ${dimension}`} />;
}

const pct = (v: number, sent: number) => (sent ? `${(v * 100).toFixed(1)}%` : "—");

export function BreakdownTable({ rows }: { rows: DimRow[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-left text-xs text-muted-foreground">
          <tr>
            <th className="px-3 py-2 font-medium">Key</th>
            {["Found", "Qualified", "Sent", "Sandbox", "Replied", "Positive", "Reply rate", "Positive rate"].map((h) => (
              <th key={h} className="px-3 py-2 text-right font-medium">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key} className="border-t">
              <td className="max-w-[200px] truncate px-3 py-2 font-medium" title={r.key}>{r.key}</td>
              <td className="tabular px-3 py-2 text-right">{r.discovered}</td>
              <td className="tabular px-3 py-2 text-right">{r.qualified}</td>
              <td className="tabular px-3 py-2 text-right">{r.sent}</td>
              <td className="tabular px-3 py-2 text-right text-muted-foreground">{r.sandbox_sent}</td>
              <td className="tabular px-3 py-2 text-right">{r.replied}</td>
              <td className="tabular px-3 py-2 text-right">{r.positive}</td>
              <td className="tabular px-3 py-2 text-right">{pct(r.reply_rate, r.sent)}</td>
              <td className="tabular px-3 py-2 text-right">{pct(r.positive_rate, r.sent)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
