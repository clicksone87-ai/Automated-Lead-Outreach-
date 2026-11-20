"use client";

import { Check, ExternalLink, Mail, Paperclip, Send } from "lucide-react";
import { PageHeader } from "@/components/common/page-header";
import { CardsSkeleton, EmptyState, ErrorState } from "@/components/common/states";
import { StatusPill } from "@/components/common/status-pill";
import { Button } from "@/components/ui/button";
import { API_URL } from "@/lib/api";
import { type GmailOutboxItem, useGmailOutbox, useMarkSent } from "@/lib/outreach";

const MODE: Record<string, string> = {
  draft: "Drafts with attachments are placed in your Gmail Drafts folder.",
  send: "Emails are sent directly through Gmail.",
  compose: "No App Password yet: each email opens as a pre-filled Gmail compose window. Attach the files below yourself.",
};

function safeGmailHref(url: string | null): string | null {
  if (!url) return null;
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" && parsed.hostname === "mail.google.com" ? parsed.toString() : null;
  } catch {
    return null;
  }
}

function OutboxCard({ item }: { item: GmailOutboxItem }) {
  const markSent = useMarkSent();
  const href = safeGmailHref(item.open_url);
  return (
    <article className="rounded-xl border bg-card p-4 shadow-sm">
      <header className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="font-semibold">{item.business_name ?? item.to_email}</h2>
          <p className="break-all text-sm text-muted-foreground">
            To {item.to_email}
            {item.country ? ` · ${item.country}` : ""}
            {item.follow_up_number ? ` · follow-up ${item.follow_up_number}` : ""}
          </p>
        </div>
        <StatusPill tone="info">{item.kind === "draft" ? "In Gmail Drafts" : "Compose link"}</StatusPill>
      </header>
      <p className="mb-2 text-sm font-medium">{item.subject}</p>
      {item.body_text ? (
        <p className="mb-3 line-clamp-4 whitespace-pre-wrap text-sm leading-relaxed text-muted-foreground">
          {item.body_text}
        </p>
      ) : null}
      {item.attachments.length > 0 && (
        <ul className="mb-3 flex flex-wrap gap-2" aria-label="Attachments">
          {item.attachments.map((a) => (
            <li key={a.url}>
              <a
                href={`${API_URL}${a.url}`}
                download={a.filename ?? undefined}
                className="inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs hover:bg-muted"
              >
                <Paperclip className="size-3" aria-hidden /> {a.filename ?? "file"}
              </a>
            </li>
          ))}
        </ul>
      )}
      <footer className="flex flex-wrap justify-end gap-2">
        {href ? (
          <Button asChild variant="outline" size="sm">
            <a href={href} target="_blank" rel="noopener noreferrer">
              <ExternalLink className="size-3.5" /> Open in Gmail
            </a>
          </Button>
        ) : null}
        <Button size="sm" onClick={() => markSent.mutate(item.message_id)} disabled={markSent.isPending}>
          <Check className="size-3.5" /> Mark as sent
        </Button>
      </footer>
    </article>
  );
}

export default function GmailOutboxPage() {
  const { data, isLoading, isError, error, refetch } = useGmailOutbox();
  const items = data?.items ?? [];
  return (
    <>
      <PageHeader
        title="Ready in Gmail"
        description={
          data
            ? `Track A: approved emails from ${data.sender || "your Gmail"}. ${MODE[data.mode] ?? ""} Press Send in Gmail, then Mark as sent here.`
            : "Track A: approved emails waiting in Gmail."
        }
        actions={data ? <StatusPill tone={items.length ? "warning" : "success"}>{items.length} waiting</StatusPill> : null}
      />
      {isError ? (
        <ErrorState error={error} onRetry={() => refetch()} />
      ) : isLoading ? (
        <CardsSkeleton count={3} />
      ) : items.length === 0 ? (
        <EmptyState
          icon={data?.mode === "compose" || data?.mode === "draft" ? Mail : Send}
          title="Nothing waiting in Gmail"
          description="Approved proposals for leads with an email address appear here."
        />
      ) : (
        <div className="grid gap-4">
          {items.map((item) => (
            <OutboxCard key={item.message_id} item={item} />
          ))}
        </div>
      )}
    </>
  );
}
