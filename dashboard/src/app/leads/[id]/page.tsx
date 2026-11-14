"use client";

import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { use } from "react";
import { Panel } from "@/components/common/panel";
import { LeadDetailView } from "@/components/leads/lead-detail";
import { Button } from "@/components/ui/button";

export default function LeadPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return (
    <div className="space-y-4">
      <Button asChild variant="ghost" size="sm" className="-ml-2">
        <Link href="/leads">
          <ArrowLeft className="size-3.5" /> All leads
        </Link>
      </Button>
      <Panel className="p-6">
        <LeadDetailView id={id} />
      </Panel>
    </div>
  );
}
