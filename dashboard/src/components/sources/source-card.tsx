"use client";

import { AnimatePresence, motion } from "framer-motion";
import { ChevronDown, ExternalLink, KeyRound, Play, Plus, ShieldCheck, Trash2 } from "lucide-react";
import { useState } from "react";
import { ConfirmDialog } from "@/components/common/confirm-dialog";
import { StatusPill } from "@/components/common/status-pill";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import type { SourceOut } from "@/lib/api";
import { agoShort, dateTime, titleCase } from "@/lib/format";
import { usePatchSource, useRunSource } from "@/lib/queries";
import { SOURCE_STATUS } from "@/lib/status";
import { cn } from "@/lib/utils";

const SCHEDULES = [15, 20, 30, 60, 120, 360, 720, 1440];

function scheduleLabel(m: number) {
  return m < 60 ? `Every ${m} min` : m < 1440 ? `Every ${m / 60} h` : "Daily";
}

type UrlEntry = { url: string; country?: string | null; label?: string };

function UrlListEditor({
  value,
  onSave,
  saving,
  placeholder,
}: {
  value: UrlEntry[];
  onSave: (next: UrlEntry[]) => void;
  saving: boolean;
  placeholder: string;
}) {
  const [draft, setDraft] = useState("");
  const [items, setItems] = useState<UrlEntry[]>(value);
  const dirty = JSON.stringify(items) !== JSON.stringify(value);
  const valid = /^https?:\/\/\S+\.\S+/.test(draft.trim());
  return (
    <div className="space-y-2">
      <ul className="space-y-1">
        {items.map((item, i) => (
          <li key={`${item.url}-${i}`} className="flex items-center gap-2 rounded-md border bg-background px-2 py-1.5 text-xs">
            <span className="min-w-0 flex-1 truncate font-mono">{item.url}</span>
            <button
              type="button"
              onClick={() => setItems(items.filter((_, j) => j !== i))}
              className="text-muted-foreground hover:text-destructive"
              aria-label={`Remove ${item.url}`}
            >
              <Trash2 className="size-3.5" />
            </button>
          </li>
        ))}
        {items.length === 0 && <li className="text-xs text-muted-foreground">None configured yet.</li>}
      </ul>
      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (!valid) return;
          setItems([...items, { url: draft.trim() }]);
          setDraft("");
        }}
      >
        <Input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder={placeholder} className="h-8 text-xs" aria-label="URL to add" />
        <Button type="submit" variant="outline" size="sm" disabled={!valid}>
          <Plus className="size-3.5" /> Add
        </Button>
      </form>
      {dirty && (
        <div className="flex justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={() => setItems(value)}>Discard</Button>
          <Button size="sm" disabled={saving} onClick={() => onSave(items)}>Save list</Button>
        </div>
      )}
    </div>
  );
}

export function SourceCard({ source, index }: { source: SourceOut; index: number }) {
  const patch = usePatchSource();
  const run = useRunSource();
  const [open, setOpen] = useState(false);
  const integ = source.integration;
  const needsKey = integ?.state === "needs_api_key";
  const isMock = integ?.state === "mock";
  const status = source.last_status ? SOURCE_STATUS[source.last_status] : undefined;
  const urlKey = "feeds" in source.config ? "feeds" : "listing_urls" in source.config ? "listing_urls" : null;
  const numericKeys = Object.entries(source.config).filter(([, v]) => typeof v === "number");

  const setEnabled = (enabled: boolean) => patch.mutate({ key: source.key, patch: { enabled } });
  const toggle = (
    <Switch
      checked={source.enabled}
      onCheckedChange={(v) => (v && integ?.paid ? undefined : setEnabled(v))}
      disabled={patch.isPending}
      aria-label={`${source.enabled ? "Disable" : "Enable"} ${source.label}`}
    />
  );

  return (
    <motion.article
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.03, duration: 0.3 }}
      className={cn(
        "flex flex-col rounded-xl border bg-card shadow-soft transition-colors",
        source.enabled && !needsKey && "border-primary/25",
      )}
    >
      <div className="flex items-start gap-3 p-5">
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="font-semibold">{source.label}</h3>
            {needsKey ? (
              <StatusPill tone="warning" dot={false}>
                <KeyRound className="size-3" aria-hidden /> Needs API key
              </StatusPill>
            ) : isMock ? (
              <StatusPill tone="warning">Mock (demo mode)</StatusPill>
            ) : status ? (
              <StatusPill tone={status.tone}>{status.label}</StatusPill>
            ) : (
              <StatusPill tone="neutral">Not run yet</StatusPill>
            )}
            {integ?.paid && <StatusPill tone="info" dot={false}>Paid API</StatusPill>}
          </div>
          <p className="text-sm text-muted-foreground">{source.description}</p>
        </div>
        {source.enabled || !integ?.paid ? (
          toggle
        ) : (
          <ConfirmDialog
            trigger={<span>{toggle}</span>}
            title={`Enable ${source.label}?`}
            description={<>This source uses a paid, usage-priced API. Every run is charged against your budget caps (and pauses automatically when a cap is reached).</>}
            confirmLabel="Enable paid source"
            onConfirm={() => setEnabled(true)}
          />
        )}
      </div>

      {needsKey && integ && (
        <div className="mx-5 mb-4 rounded-lg border border-warning/30 bg-warning-soft px-3 py-2 text-xs text-warning-soft-foreground">
          Add <span className="font-mono font-semibold">{integ.env_vars.join(", ")}</span> to <span className="font-mono">.env</span>, then restart the stack.
          {integ.signup_url && (
            <a href={integ.signup_url} target="_blank" rel="noopener noreferrer" className="ml-1 inline-flex items-center gap-0.5 font-medium underline">
              Get a key <ExternalLink className="size-3" />
            </a>
          )}
        </div>
      )}

      <dl className="grid grid-cols-3 gap-3 border-t px-5 py-3 text-xs">
        <div>
          <dt className="text-muted-foreground">Last run</dt>
          <dd className="font-medium">{agoShort(source.last_run_at)}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Leads (7d)</dt>
          <dd className="tabular font-medium">{source.leads_7d}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Total</dt>
          <dd className="tabular font-medium">{source.leads_found_total}</dd>
        </div>
      </dl>

      {source.last_error && source.last_status === "error" && (
        <p className="mx-5 mb-3 truncate rounded-md bg-danger-soft px-2 py-1 font-mono text-[11px] text-danger-soft-foreground" title={source.last_error}>
          {source.last_error}
        </p>
      )}

      <div className="mt-auto flex flex-wrap items-center gap-2 border-t px-5 py-3">
        <Select
          value={String(source.schedule_minutes)}
          onValueChange={(v) => patch.mutate({ key: source.key, patch: { schedule_minutes: Number(v) } })}
        >
          <SelectTrigger size="sm" className="h-8 w-[140px]" aria-label="Schedule">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {SCHEDULES.map((m) => (
              <SelectItem key={m} value={String(m)}>{scheduleLabel(m)}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button
          variant="outline"
          size="sm"
          onClick={() => run.mutate(source.key)}
          disabled={run.isPending || (needsKey && !isMock)}
        >
          <Play className="size-3.5" /> Run now
        </Button>
        <Button variant="ghost" size="sm" className="ml-auto" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
          Details <ChevronDown className={cn("size-3.5 transition-transform", open && "rotate-180")} />
        </Button>
      </div>

      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.25, ease: [0.22, 1, 0.36, 1] }}
            className="overflow-hidden"
          >
            <div className="space-y-4 border-t bg-surface-sunken/50 px-5 py-4">
              <p className="flex gap-1.5 text-xs text-muted-foreground">
                <ShieldCheck className="mt-0.5 size-3.5 shrink-0 text-success" aria-hidden />
                <span><span className="font-medium text-foreground">Enforced in code:</span> {source.constraints}</span>
              </p>

              {urlKey && (
                <div>
                  <p className="mb-1.5 text-xs font-medium">{urlKey === "feeds" ? "Feed URLs" : "Directory listing URLs"}</p>
                  <UrlListEditor
                    value={(source.config[urlKey] as UrlEntry[]) ?? []}
                    saving={patch.isPending}
                    placeholder={urlKey === "feeds" ? "https://example.com/feed.xml" : "https://directory.example.com/plumbers"}
                    onSave={(next) => patch.mutate({ key: source.key, patch: { config: { [urlKey]: next } } })}
                  />
                </div>
              )}

              {numericKeys.length > 0 && (
                <div className="grid grid-cols-2 gap-3">
                  {numericKeys.map(([k, v]) => (
                    <label key={k} className="space-y-1 text-xs">
                      <span className="font-medium">{titleCase(k)}</span>
                      <Input
                        type="number"
                        min={1}
                        defaultValue={Number(v)}
                        className="h-8"
                        onBlur={(e) => {
                          const n = Number(e.target.value);
                          if (Number.isFinite(n) && n > 0 && n !== v) {
                            patch.mutate({ key: source.key, patch: { config: { [k]: n } } });
                          }
                        }}
                      />
                    </label>
                  ))}
                </div>
              )}

              <div>
                <p className="mb-1.5 text-xs font-medium">Recent runs</p>
                {source.runs.length === 0 ? (
                  <p className="text-xs text-muted-foreground">No runs yet.</p>
                ) : (
                  <ul className="space-y-1 text-xs">
                    {source.runs.map((r) => (
                      <li key={String(r.id)} className="flex items-center justify-between gap-2 rounded-md bg-background px-2 py-1">
                        <span className="text-muted-foreground">{dateTime(String(r.started_at))}</span>
                        <span className="tabular">{String(r.items_seen)} items · {String(r.leads_created)} leads</span>
                        <StatusPill tone={r.status === "succeeded" ? "success" : r.status === "failed" ? "danger" : "neutral"}>
                          {titleCase(String(r.status))}
                        </StatusPill>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.article>
  );
}
