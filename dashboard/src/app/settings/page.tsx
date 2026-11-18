"use client";

import { Check, ExternalLink, KeyRound, Lock, ShieldCheck, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { ConfirmDialog } from "@/components/common/confirm-dialog";
import { PageHeader } from "@/components/common/page-header";
import { AutonomyControls } from "@/components/outreach/autonomy-controls";
import { Panel, PanelHeader } from "@/components/common/panel";
import { ErrorState, RowsSkeleton } from "@/components/common/states";
import { StatusPill } from "@/components/common/status-pill";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { SettingsOut } from "@/lib/api";
import { countryName, languageName, money, titleCase, usd } from "@/lib/format";
import { useOverview, usePatchSettings, useSettings } from "@/lib/queries";
import { cn } from "@/lib/utils";
import { LANGUAGES as LANGS } from "@/lib/markets";

const SECTIONS = [
  ["autonomy", "Autonomy"],
  ["limits", "Limits"],
  ["budgets", "Budgets"],
  ["languages", "Languages"],
  ["market", "Market"],
  ["integrations", "Integrations"],
  ["pricing", "Pricing"],
  ["sender", "Sender identity"],
] as const;

//const LANGS = ["en", "de", "fr", "es"];
const RECOMMENDED_MONTHLY_CAP = 120;

type Runtime = {
  autonomy_stage: number;
  daily_send_limit: number;
  hourly_send_limit: number;
  min_intent_score: number;
  cooldown_days: number;
  max_follow_ups: number;
  daily_budget_usd: number;
  monthly_budget_usd: number;
  enabled_languages: string[];
  sending_languages: string[];
  target_countries: string[];
  active_industry: string;
};

function Section({ id, title, description, children, action }: { id: string; title: string; description?: string; children: React.ReactNode; action?: React.ReactNode }) {
  return (
    <Panel id={id} className="scroll-mt-20">
      <PanelHeader title={title} description={description} action={action} />
      <div className="p-5">{children}</div>
    </Panel>
  );
}

function NumberField({ label, hint, value, onChange, min = 0, max, step = 1, prefix }: { label: string; hint?: string; value: number; onChange: (n: number) => void; min?: number; max?: number; step?: number; prefix?: string }) {
  const id = `f-${label.replace(/\W+/g, "-").toLowerCase()}`;
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id} className="text-xs">{label}</Label>
      <div className="relative">
        {prefix && <span className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-xs text-muted-foreground">{prefix}</span>}
        <Input id={id} type="number" value={Number.isFinite(value) ? value : ""} min={min} max={max} step={step} onChange={(e) => onChange(Number(e.target.value))} className={cn("h-9", prefix && "pl-6")} />
      </div>
      {hint && <p className="text-[11px] text-muted-foreground">{hint}</p>}
    </div>
  );
}

function SaveBar({ dirty, saving, onSave, onReset }: { dirty: boolean; saving: boolean; onSave: () => void; onReset: () => void }) {
  if (!dirty) return null;
  return (
    <div className="mt-5 flex justify-end gap-2 border-t pt-4">
      <Button variant="ghost" size="sm" onClick={onReset}>Discard</Button>
      <Button size="sm" onClick={onSave} disabled={saving}>{saving ? "Saving…" : "Save changes"}</Button>
    </div>
  );
}

function useDraft<T extends object>(source: T | undefined) {
  // `source` is rebuilt on every render; key it by content so the reset effect only fires
  // when the saved values actually change (otherwise: infinite render loop).
  const json = JSON.stringify(source ?? null);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const stable = useMemo(() => source, [json]);
  const [draft, setDraft] = useState<T | undefined>(stable);
  useEffect(() => setDraft(stable), [stable]);
  const dirty = !!stable && !!draft && json !== JSON.stringify(draft);
  return { draft, setDraft, dirty, reset: () => setDraft(stable) };
}

function pick<K extends keyof Runtime>(rt: Runtime, keys: readonly K[]): Pick<Runtime, K> {
  return Object.fromEntries(keys.map((k) => [k, rt[k]])) as Pick<Runtime, K>;
}

function SettingsView({ data }: { data: SettingsOut }) {
  const rt = data.runtime as unknown as Runtime;
  const save = usePatchSettings();
  const { data: overview } = useOverview();

  const limits = useDraft(pick(rt, ["daily_send_limit", "hourly_send_limit", "min_intent_score", "cooldown_days", "max_follow_ups"] as const));
  const budgets = useDraft(pick(rt, ["daily_budget_usd", "monthly_budget_usd"] as const));
  const langs = useDraft(pick(rt, ["enabled_languages", "sending_languages"] as const));
  const market = useDraft(pick(rt, ["target_countries"] as const));

  const commit = (changes: object, reason: string) => save.mutate({ changes: changes as Record<string, unknown>, reason });
  const budgetIncrease = budgets.draft && (budgets.draft.monthly_budget_usd > rt.monthly_budget_usd || budgets.draft.daily_budget_usd > rt.daily_budget_usd);
  const newSendingLangs = langs.draft?.sending_languages.filter((l) => !rt.sending_languages.includes(l)) ?? [];

  return (
    <div className="grid gap-8 lg:grid-cols-[180px_1fr]">
      <nav className="hidden lg:block" aria-label="Settings sections">
        <ul className="sticky top-20 space-y-0.5 text-sm">
          {SECTIONS.map(([id, label]) => (
            <li key={id}>
              <a href={`#${id}`} className="block rounded-md px-2.5 py-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground">{label}</a>
            </li>
          ))}
        </ul>
      </nav>

      <div className="min-w-0 space-y-6">
        <Section id="autonomy" title="Autonomy stage" description="Switchable from the dashboard only. Moving up is blocked until every condition is met.">
          <div className="grid gap-3 md:grid-cols-3">
            {[
              { n: 1, title: "Human approves every email", cond: "Starts on day one of live use" },
              { n: 2, title: "Sends within low daily limits", cond: "Bounce < 3%, complaints < 0.1%, two clean weeks" },
              { n: 3, title: "Full autonomy", cond: "Stage 2 stable and budget caps working" },
            ].map((s) => (
              <div key={s.n} className={cn("rounded-xl border p-4", rt.autonomy_stage === s.n ? "border-primary/40 bg-primary-soft/40 shadow-glow" : "bg-surface-raised")}>
                <div className="flex items-center justify-between">
                  <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Stage {s.n}</span>
                  {rt.autonomy_stage === s.n ? <StatusPill tone="accent">Current</StatusPill> : rt.autonomy_stage > s.n ? <Check className="size-4 text-success" /> : <Lock className="size-3.5 text-muted-foreground" />}
                </div>
                <p className="mt-2 text-sm font-medium">{s.title}</p>
                <p className="mt-1 text-xs text-muted-foreground">{s.cond}</p>
              </div>
            ))}
          </div>
          <AutonomyControls />
          {overview && (
            <div className="mt-4 grid gap-3 text-xs sm:grid-cols-3">
              <Metric label="Bounce rate (30d)" ok={overview.deliverability.bounce_rate < 0.03} value={`${(overview.deliverability.bounce_rate * 100).toFixed(1)}%`} target="< 3%" />
              <Metric label="Complaint rate (30d)" ok={overview.deliverability.complaint_rate < 0.001} value={`${(overview.deliverability.complaint_rate * 100).toFixed(2)}%`} target="< 0.1%" />
              <Metric label="Emails sent (30d)" ok={overview.deliverability.sent_30d > 0} value={String(overview.deliverability.sent_30d)} target="needs sending history" />
            </div>
          )}
          <div className="mt-4 flex items-center gap-2 rounded-lg border border-dashed p-3 text-xs text-muted-foreground">
            <ShieldCheck className="size-4 shrink-0 text-success" aria-hidden />
            Restricted at every stage: signing contracts, accepting payments, changing a client's live site, buying domains or services, publishing final deliverables.
          </div>
        </Section>

        <Section id="limits" title="Limits" description="Applied by the policy layer before every send.">
          {limits.draft && (
            <>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                <NumberField label="Daily send limit" value={limits.draft.daily_send_limit} onChange={(n) => limits.setDraft({ ...limits.draft!, daily_send_limit: n })} hint="Domain warm-up may lower it further" />
                <NumberField label="Hourly send limit" value={limits.draft.hourly_send_limit} onChange={(n) => limits.setDraft({ ...limits.draft!, hourly_send_limit: n })} />
                <NumberField label="Minimum intent score" value={limits.draft.min_intent_score} max={100} onChange={(n) => limits.setDraft({ ...limits.draft!, min_intent_score: n })} hint="Below this, no money is spent on a lead" />
                <NumberField label="Cooldown (days)" value={limits.draft.cooldown_days} onChange={(n) => limits.setDraft({ ...limits.draft!, cooldown_days: n })} hint="Before any new outreach to a business" />
                <NumberField label="Max follow-ups" value={limits.draft.max_follow_ups} max={5} onChange={(n) => limits.setDraft({ ...limits.draft!, max_follow_ups: n })} />
              </div>
              <SaveBar dirty={limits.dirty} saving={save.isPending} onReset={limits.reset} onSave={() => commit(limits.draft!, "limits updated")} />
            </>
          )}
        </Section>

        <Section id="budgets" title="Budgets" description="Hard caps in backend code. When a cap is reached, paid agents pause.">
          {budgets.draft && (
            <>
              <div className="grid gap-4 sm:grid-cols-2">
                <NumberField label="Daily budget" prefix="$" step={0.5} value={budgets.draft.daily_budget_usd} onChange={(n) => budgets.setDraft({ ...budgets.draft!, daily_budget_usd: n })} />
                <NumberField label="Monthly budget" prefix="$" step={5} value={budgets.draft.monthly_budget_usd} onChange={(n) => budgets.setDraft({ ...budgets.draft!, monthly_budget_usd: n })} hint={`Approved cap: ${usd(RECOMMENDED_MONTHLY_CAP, 0)}/month`} />
              </div>
              {budgets.draft.monthly_budget_usd > RECOMMENDED_MONTHLY_CAP && (
                <p className="mt-3 rounded-md bg-warning-soft px-3 py-2 text-xs text-warning-soft-foreground">Above the approved {usd(RECOMMENDED_MONTHLY_CAP, 0)}/month cap.</p>
              )}
              {budgets.dirty &&
                (budgetIncrease ? (
                  <div className="mt-5 flex justify-end gap-2 border-t pt-4">
                    <Button variant="ghost" size="sm" onClick={budgets.reset}>Discard</Button>
                    <ConfirmDialog
                      trigger={<Button size="sm">Save changes</Button>}
                      title="Raise budget caps?"
                      description={<>Paid agents will be allowed to spend up to {usd(budgets.draft.daily_budget_usd)} per day and {usd(budgets.draft.monthly_budget_usd, 0)} per month.</>}
                      confirmLabel="Raise caps"
                      onConfirm={() => commit(budgets.draft!, "budget caps raised")}
                    />
                  </div>
                ) : (
                  <SaveBar dirty saving={save.isPending} onReset={budgets.reset} onSave={() => commit(budgets.draft!, "budget caps lowered")} />
                ))}
            </>
          )}
        </Section>

        <Section id="languages" title="Languages" description="Leads in languages that aren't enabled are parked. Sending is allowed only for reviewed languages.">
          {langs.draft && (
            <>
              <div className="overflow-hidden rounded-lg border">
                <table className="w-full text-sm">
                  <thead className="bg-surface-sunken/60 text-xs text-muted-foreground">
                    <tr><th className="px-4 py-2 text-left font-medium">Language</th><th className="px-4 py-2 text-left font-medium">Process leads</th><th className="px-4 py-2 text-left font-medium">Send emails</th></tr>
                  </thead>
                  <tbody>
                    {LANGS.map((l) => {
                      const processing = langs.draft!.enabled_languages.includes(l);
                      const sending = langs.draft!.sending_languages.includes(l);
                      return (
                        <tr key={l} className="border-t">
                          <td className="px-4 py-2.5 font-medium">{languageName(l)}</td>
                          <td className="px-4 py-2.5">
                            <Checkbox checked={processing} aria-label={`Process ${languageName(l)} leads`} onCheckedChange={(v) => {
                              const enabled = v ? [...langs.draft!.enabled_languages, l] : langs.draft!.enabled_languages.filter((x) => x !== l);
                              langs.setDraft({ enabled_languages: enabled, sending_languages: langs.draft!.sending_languages.filter((x) => enabled.includes(x)) });
                            }} />
                          </td>
                          <td className="px-4 py-2.5">
                            <Checkbox checked={sending} disabled={!processing} aria-label={`Send ${languageName(l)} emails`} onCheckedChange={(v) => langs.setDraft({ ...langs.draft!, sending_languages: v ? [...langs.draft!.sending_languages, l] : langs.draft!.sending_languages.filter((x) => x !== l) })} />
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              {langs.dirty &&
                (newSendingLangs.length ? (
                  <div className="mt-5 flex justify-end gap-2 border-t pt-4">
                    <Button variant="ghost" size="sm" onClick={langs.reset}>Discard</Button>
                    <ConfirmDialog
                      trigger={<Button size="sm">Save changes</Button>}
                      title={`Enable sending in ${newSendingLangs.map(languageName).join(", ")}?`}
                      description="Only do this after reviewing a sample of emails, logos and previews in that language."
                      confirmLabel="I've reviewed the samples"
                      onConfirm={() => commit(langs.draft!, `sending enabled: ${newSendingLangs.join(",")}`)}
                    />
                  </div>
                ) : (
                  <SaveBar dirty saving={save.isPending} onReset={langs.reset} onSave={() => commit(langs.draft!, "languages updated")} />
                ))}
            </>
          )}
        </Section>

        <Section id="market" title="Market" description={`Active industry: ${titleCase(rt.active_industry)}. Country rules come from the legal review (country_rules.yaml).`}>
          {market.draft && (
            <>
              <div className="overflow-hidden rounded-lg border">
                <table className="w-full text-sm">
                  <thead className="bg-surface-sunken/60 text-xs text-muted-foreground">
                    <tr>
                      <th className="px-4 py-2 text-left font-medium">Country</th>
                      <th className="px-4 py-2 text-left font-medium">Target</th>
                      <th className="px-4 py-2 text-left font-medium">Draft</th>
                      <th className="px-4 py-2 text-left font-medium">Send</th>
                      <th className="px-4 py-2 text-left font-medium">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(data.country_rules).map(([code, rule]) => {
                      const r = rule as { enabled: boolean; legal_reviewed: boolean; can_draft: boolean; can_send: boolean; regime_note?: string };
                      return (
                        <tr key={code} className="border-t">
                          <td className="px-4 py-2.5 font-medium">
                            <span className="mr-2 inline-flex w-7 justify-center rounded bg-muted px-1 font-mono text-[11px] text-muted-foreground">{code}</span>
                            {countryName(code)}
                          </td>
                          <td className="px-4 py-2.5">
                            <Checkbox
                              checked={market.draft!.target_countries.includes(code)}
                              disabled={!r.enabled}
                              aria-label={`Target ${countryName(code)}`}
                              onCheckedChange={(v) => market.setDraft({ target_countries: v ? [...market.draft!.target_countries, code] : market.draft!.target_countries.filter((c) => c !== code) })}
                            />
                          </td>
                          <td className="px-4 py-2.5">{r.can_draft ? <Check className="size-4 text-success" aria-label="yes" /> : <X className="size-4 text-muted-foreground" aria-label="no" />}</td>
                          <td className="px-4 py-2.5">{r.can_send ? <Check className="size-4 text-success" aria-label="yes" /> : <X className="size-4 text-muted-foreground" aria-label="no" />}</td>
                          <td className="px-4 py-2.5">
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <span>
                                  {!r.enabled ? <StatusPill tone="neutral">Blocked — no rules</StatusPill> : !r.legal_reviewed ? <StatusPill tone="warning">Awaiting legal review</StatusPill> : <StatusPill tone="success">Reviewed</StatusPill>}
                                </span>
                              </TooltipTrigger>
                              {r.regime_note && <TooltipContent className="max-w-xs">{r.regime_note}</TooltipContent>}
                            </Tooltip>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <p className="mt-3 text-xs text-muted-foreground">Countries not listed are blocked automatically.</p>
              <SaveBar dirty={market.dirty} saving={save.isPending} onReset={market.reset} onSave={() => commit(market.draft!, "target countries updated")} />
            </>
          )}
        </Section>

        <Section id="integrations" title="Integrations" description="Add keys to .env and restart the stack. Missing keys disable a feature without crashing anything.">
          <ul className="divide-y rounded-lg border">
            {data.integrations.map((i) => (
              <li key={i.key} className="flex flex-wrap items-center gap-3 px-4 py-2.5">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium">{i.label}</span>
                    {i.paid && <span className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Paid</span>}
                  </div>
                  {i.env_vars.length > 0 && <p className="truncate font-mono text-[11px] text-muted-foreground">{i.env_vars.join(" · ")}</p>}
                </div>
                {i.state === "ready" ? (
                  <StatusPill tone="success">Ready</StatusPill>
                ) : i.state === "mock" ? (
                  <StatusPill tone="warning">Mock (demo)</StatusPill>
                ) : (
                  <StatusPill tone="warning" dot={false}><KeyRound className="size-3" aria-hidden /> Needs API key</StatusPill>
                )}
                {i.signup_url && i.state !== "ready" && (
                  <a href={i.signup_url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline">
                    Sign up <ExternalLink className="size-3" />
                  </a>
                )}
              </li>
            ))}
          </ul>
        </Section>

        <Section id="pricing" title="Pricing & packages" description="Starter values — confirm before quoting to clients.">
          <div className="overflow-x-auto rounded-lg border">
            <table className="w-full min-w-[560px] text-sm">
              <thead className="bg-surface-sunken/60 text-xs text-muted-foreground">
                <tr>
                  <th className="px-4 py-2 text-left font-medium">Service</th>
                  <th className="px-4 py-2 text-left font-medium">Package</th>
                  <th className="px-4 py-2 text-right font-medium">USD</th>
                  <th className="px-4 py-2 text-right font-medium">EUR</th>
                  <th className="px-4 py-2 text-right font-medium">GBP</th>
                </tr>
              </thead>
              <tbody>
                {data.pricing.map((p) => (
                  <tr key={String(p.id)} className="border-t">
                    <td className="px-4 py-2.5">{titleCase(String(p.service))}</td>
                    <td className="px-4 py-2.5">
                      <div className="font-medium">{String(p.package)}</div>
                      <div className="text-xs text-muted-foreground">{String(p.description ?? "")}</div>
                    </td>
                    <td className="tabular px-4 py-2.5 text-right">{money(Number(p.price_usd), "USD")}</td>
                    <td className="tabular px-4 py-2.5 text-right">{money(Number(p.price_eur), "EUR")}</td>
                    <td className="tabular px-4 py-2.5 text-right">{p.price_gbp != null ? money(Number(p.price_gbp), "GBP") : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>

        <Section id="sender" title="Sender identity" description="Shown in every email (business name, postal address, portfolio, unsubscribe). Set in .env.">
          <dl className="grid gap-4 sm:grid-cols-2">
            {Object.entries(data.sender_identity).map(([k, v]) => (
              <div key={k}>
                <dt className="text-xs text-muted-foreground">{titleCase(k)}</dt>
                <dd className={cn("mt-0.5 text-sm", !v && "text-warning-soft-foreground")}>{v || "Not set"}</dd>
              </div>
            ))}
          </dl>
        </Section>
      </div>
    </div>
  );
}

function Metric({ label, value, target, ok }: { label: string; value: string; target: string; ok: boolean }) {
  return (
    <div className="flex items-center justify-between rounded-lg border bg-surface-raised px-3 py-2">
      <div>
        <div className="text-muted-foreground">{label}</div>
        <div className="tabular text-sm font-semibold">{value}</div>
      </div>
      <div className="text-right">
        {ok ? <Check className="ml-auto size-4 text-success" aria-label="met" /> : <X className="ml-auto size-4 text-muted-foreground" aria-label="not met" />}
        <div className="text-[11px] text-muted-foreground">{target}</div>
      </div>
    </div>
  );
}

export default function SettingsPage() {
  const { data, isLoading, isError, error, refetch } = useSettings();
  return (
    <>
      <PageHeader title="Settings" description="Autonomy, limits, budgets, languages, market, integrations and pricing." />
      {isError ? <ErrorState error={error} onRetry={() => refetch()} /> : isLoading || !data ? <RowsSkeleton rows={10} /> : <SettingsView data={data} />}
    </>
  );
}
