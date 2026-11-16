"use client";

import { Check, FileText, MailCheck, Paperclip, Pencil, ShieldCheck, X } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { PageHeader } from "@/components/common/page-header";
import { Panel, PanelHeader } from "@/components/common/panel";
import { CardsSkeleton, EmptyState, ErrorState } from "@/components/common/states";
import { StatusPill } from "@/components/common/status-pill";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { fileUrl } from "@/lib/api";
import { type ProposalItem, useApprovals, useApprove, useEditProposal, useReject } from "@/lib/outreach";
import { cn } from "@/lib/utils";

function EmailPreview({ p }: { p: ProposalItem }) {
  // Rendered in a fully sandboxed iframe: no scripts, no navigation, no same-origin access.
  return (
    <iframe
      title={`Email preview: ${p.subject}`}
      sandbox=""
      srcDoc={p.body_html}
      className="h-[560px] w-full rounded-lg border bg-white"
    />
  );
}

function Editor({ p, onDone }: { p: ProposalItem; onDone: () => void }) {
  const edit = useEditProposal();
  const [subject, setSubject] = useState(p.subject);
  const [intro, setIntro] = useState(p.draft.intro ?? "");
  const [closing, setClosing] = useState(p.draft.closing ?? "");
  return (
    <form
      className="space-y-3"
      onSubmit={(e) => {
        e.preventDefault();
        edit.mutate({ id: p.id, patch: { subject, intro, closing } }, { onSuccess: onDone });
      }}
    >
      <label className="block space-y-1 text-xs font-medium">
        Subject
        <Input value={subject} onChange={(e) => setSubject(e.target.value)} maxLength={90} />
      </label>
      <label className="block space-y-1 text-xs font-medium">
        Opening (no factual claims here — claims stay tied to evidence)
        <textarea value={intro} onChange={(e) => setIntro(e.target.value)} rows={5} maxLength={1200} className="w-full rounded-md border bg-background p-2 text-sm" />
      </label>
      <label className="block space-y-1 text-xs font-medium">
        Closing
        <textarea value={closing} onChange={(e) => setClosing(e.target.value)} rows={3} maxLength={800} className="w-full rounded-md border bg-background p-2 text-sm" />
      </label>
      <div className="flex justify-end gap-2">
        <Button type="button" variant="ghost" size="sm" onClick={onDone}>Cancel</Button>
        <Button type="submit" size="sm" disabled={edit.isPending}>Save draft</Button>
      </div>
    </form>
  );
}

export default function ApprovalsPage() {
  const { data, isLoading, isError, error, refetch } = useApprovals();
  const approve = useApprove();
  const reject = useReject();
  const [index, setIndex] = useState(0);
  const [editing, setEditing] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const items = useMemo(() => data ?? [], [data]);
  const p = items[Math.min(index, Math.max(items.length - 1, 0))];

  const onKey = useCallback(
    (e: KeyboardEvent) => {
      if (!p || editing || rejecting) return;
      const tag = (e.target as HTMLElement).tagName;
      if (tag === "INPUT" || tag === "TEXTAREA") return;
      if (e.key === "j") setIndex((i) => Math.min(i + 1, items.length - 1));
      if (e.key === "k") setIndex((i) => Math.max(i - 1, 0));
      if (e.key === "a") approve.mutate(p.id);
      if (e.key === "e") setEditing(true);
      if (e.key === "r") setRejecting(true);
    },
    [p, editing, rejecting, items.length, approve],
  );
  useEffect(() => {
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onKey]);

  return (
    <>
      <PageHeader
        title="Approval queue"
        description="Stage 1: every email waits for you. Check the claims against their evidence, edit the wording if you like, then approve or reject. Keys: j/k next/previous · a approve · e edit · r reject."
        actions={data && <StatusPill tone={items.length ? "warning" : "success"}>{items.length} waiting</StatusPill>}
      />
      {isError ? (
        <ErrorState error={error} onRetry={() => refetch()} />
      ) : isLoading || !data ? (
        <CardsSkeleton count={2} />
      ) : !p ? (
        <EmptyState icon={MailCheck} title="No data yet" description="Drafted emails appear here once a lead passes the quality check and the Proposal agent writes its email." />
      ) : (
        <div className="grid gap-6 xl:grid-cols-[260px_1fr]">
          <nav aria-label="Waiting proposals" className="space-y-1">
            {items.map((it, i) => (
              <button
                key={it.id}
                type="button"
                onClick={() => {
                  setIndex(i);
                  setEditing(false);
                }}
                className={cn("w-full rounded-lg border px-3 py-2 text-left text-sm transition-colors", i === index ? "border-primary/40 bg-primary-soft/40" : "bg-card hover:bg-muted")}
              >
                <div className="truncate font-medium">{it.business_name ?? "Unnamed"}</div>
                <div className="truncate text-xs text-muted-foreground">{it.subject}</div>
              </button>
            ))}
          </nav>
          <div className="grid gap-6 2xl:grid-cols-2">
            <Panel>
              <PanelHeader
                title={p.subject}
                description={`To ${p.to_email ?? "manual route"} · ${p.language.toUpperCase()} · v${p.version}${p.edited_by_human ? " · edited by you" : ""}`}
                action={<Link href={`/leads?lead=${p.lead_id}`} className="text-xs font-medium text-primary hover:underline">Open lead</Link>}
              />
              <div className="p-4">{editing ? <Editor p={p} onDone={() => setEditing(false)} /> : <EmailPreview p={p} />}</div>
            </Panel>
            <div className="space-y-6">
              <Panel>
                <PanelHeader title="Claims → evidence" description="Every factual sentence is tied to a stored evidence record. Drafts with an untied claim never reach this queue." />
                <ul className="space-y-2 p-4 text-sm">
                  {p.claims.length === 0 && <li className="text-muted-foreground">No factual claims in this email.</li>}
                  {p.claims.map((c) => (
                    <li key={c.evidence_id + c.text} className="rounded-lg border p-3">
                      <p className="font-medium">“{c.text}”</p>
                      <p className="mt-1 flex items-center gap-1.5 text-xs text-muted-foreground">
                        <ShieldCheck className="size-3.5 text-success" aria-hidden />
                        {c.source?.title ?? "Finding"}{c.source?.severity ? ` · ${c.source.severity}` : ""} · evidence {c.evidence_id.slice(0, 8)}
                      </p>
                    </li>
                  ))}
                </ul>
              </Panel>
              <Panel>
                <PanelHeader title="Attachments" description="Local mode: screenshots and PDFs instead of live links." />
                <ul className="space-y-1 p-4 text-sm">
                  {p.attachments.map((a) => (
                    <li key={a.path} className="flex items-center gap-2">
                      {a.content_type === "application/pdf" ? <FileText className="size-4 text-muted-foreground" /> : <Paperclip className="size-4 text-muted-foreground" />}
                      <a href={fileUrl(a.path) ?? "#"} target="_blank" rel="noopener noreferrer" className="text-primary hover:underline">{a.filename}</a>
                    </li>
                  ))}
                </ul>
              </Panel>
              <Panel>
                <div className="space-y-3 p-4">
                  {rejecting ? (
                    <form
                      className="flex gap-2"
                      onSubmit={(e) => {
                        e.preventDefault();
                        if (reason.trim().length < 3) return;
                        reject.mutate({ id: p.id, reason }, { onSuccess: () => { setRejecting(false); setReason(""); } });
                      }}
                    >
                      <Input autoFocus value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Why reject? (kept for learning)" aria-label="Rejection reason" />
                      <Button type="submit" variant="destructive" disabled={reject.isPending || reason.trim().length < 3}>Reject</Button>
                      <Button type="button" variant="ghost" onClick={() => setRejecting(false)}>Cancel</Button>
                    </form>
                  ) : (
                    <div className="flex flex-wrap gap-2">
                      <Button onClick={() => approve.mutate(p.id)} disabled={approve.isPending}>
                        <Check className="size-4" /> Approve & send
                      </Button>
                      <Button variant="outline" onClick={() => setEditing((v) => !v)}>
                        <Pencil className="size-4" /> {editing ? "Preview" : "Edit wording"}
                      </Button>
                      <Button variant="ghost" onClick={() => setRejecting(true)}>
                        <X className="size-4" /> Reject
                      </Button>
                    </div>
                  )}
                  <p className="text-xs text-muted-foreground">Approving runs every policy check again before sending. In testing mode the email lands in Mailpit — nothing is really sent.</p>
                </div>
              </Panel>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
