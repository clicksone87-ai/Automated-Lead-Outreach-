"use client";

import { Check, Copy, ExternalLink, Inbox, SkipForward } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { PageHeader } from "@/components/common/page-header";
import { CardsSkeleton, EmptyState, ErrorState } from "@/components/common/states";
import { StatusPill } from "@/components/common/status-pill";
import { Button } from "@/components/ui/button";
import {
  type ManualOutreachItem,
  useManualOutreach,
  useManualOutreachDone,
  useManualOutreachSkip,
} from "@/lib/outreach";

function safeHref(url: string | null): string | null {
  if (!url) return null;
  try {
    const parsed = new URL(url);
    return parsed.protocol === "http:" || parsed.protocol === "https:" ? parsed.toString() : null;
  } catch {
    return null;
  }
}

function CopyButton({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <Button
      variant="outline"
      size="sm"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setCopied(true);
          toast.success(`${label} copied`);
        } catch {
          toast.error("Could not copy the draft");
        }
      }}
    >
      {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
      {copied ? "Copied" : "Copy"}
    </Button>
  );
}

function DraftCard({ item }: { item: ManualOutreachItem }) {
  const done = useManualOutreachDone();
  const skip = useManualOutreachSkip();
  const href = safeHref(item.post_link);
  const drafts = [
    ["Comment", item.comment],
    ["DM", item.dm],
    ["Contact form", item.contact_form],
  ] as const;
  return (
    <article className="rounded-xl border bg-card p-4 shadow-sm">
      <header className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="font-semibold">{item.business_name ?? "Manual lead"}</h2>
          <p className="text-sm text-muted-foreground">{item.channel}</p>
        </div>
        {href ? (
          <a
            href={href}
            target="_blank"
            rel="noopener noreferrer nofollow"
            className="inline-flex items-center gap-1 text-sm text-primary hover:underline"
          >
            Open post <ExternalLink className="size-3.5" />
          </a>
        ) : null}
      </header>
      <div className="grid gap-3 md:grid-cols-3">
        {drafts.map(([label, text]) => (
          <section key={label} className="rounded-lg border p-3">
            <div className="mb-2 flex items-center justify-between">
              <span className="text-sm font-medium">{label}</span>
              {text ? <CopyButton text={text} label={label} /> : null}
            </div>
            <p className="whitespace-pre-wrap text-sm leading-relaxed">{text || "No draft"}</p>
          </section>
        ))}
      </div>
      <footer className="mt-4 flex justify-end gap-2">
        <Button variant="outline" size="sm" onClick={() => skip.mutate(item.id)} disabled={skip.isPending}>
          <SkipForward className="size-3.5" /> Skip
        </Button>
        <Button size="sm" onClick={() => done.mutate(item.id)} disabled={done.isPending}>
          <Check className="size-3.5" /> Mark as done
        </Button>
      </footer>
    </article>
  );
}

export default function ManualOutreachPage() {
  const { data, isLoading, isError, error, refetch } = useManualOutreach("pending");
  return (
    <>
      <PageHeader
        title="Manual outreach"
        description="Copy-ready Track B drafts for comment, DM or contact form. Nothing is posted automatically."
        actions={data ? <StatusPill tone={data.length ? "warning" : "success"}>{data.length} pending</StatusPill> : null}
      />
      {isError ? (
        <ErrorState error={error} onRetry={() => refetch()} />
      ) : isLoading ? (
        <CardsSkeleton count={3} />
      ) : !data?.length ? (
        <EmptyState icon={Inbox} title="No manual outreach queued" description="Track B leads will appear here." />
      ) : (
        <div className="grid gap-4">{data.map((item) => <DraftCard key={item.id} item={item} />)}</div>
      )}
    </>
  );
}
