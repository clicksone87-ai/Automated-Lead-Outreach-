"use client";

import { ArrowLeft, User } from "lucide-react";
import Link from "next/link";
import { use } from "react";
import { AuditReport } from "@/components/audits/audit-report";
import { ErrorState, RowsSkeleton } from "@/components/common/states";
import { Button } from "@/components/ui/button";
import { useAudit } from "@/lib/queries";

export default function AuditPage({ params }: { params: Promise<{ leadId: string }> }) {
  const { leadId } = use(params);
  const { data, isLoading, isError, error, refetch } = useAudit(leadId);
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <Button asChild variant="ghost" size="sm" className="-ml-2">
          <Link href="/audits"><ArrowLeft className="size-3.5" /> All audits</Link>
        </Button>
        <Button asChild variant="ghost" size="sm">
          <Link href={`/leads/${leadId}`}><User className="size-3.5" /> Lead</Link>
        </Button>
      </div>
      {isError ? <ErrorState error={error} onRetry={() => refetch()} /> : isLoading || !data ? <RowsSkeleton rows={8} /> : <AuditReport audit={data} />}
    </div>
  );
}
