import {
  Ban,
  BarChart3,
  FileSearch,
  Inbox,
  MailCheck,
  Palette,
  Gauge,
  LayoutDashboard,
  Radar,
  Settings2,
  Users,
  type LucideIcon,
} from "lucide-react";

export interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  shortcut?: string;
  description: string;
}

export interface NavGroup {
  label: string;
  items: NavItem[];
}

/** Pages are added here as each build phase delivers them. */
export const NAV: NavGroup[] = [
  {
    label: "Operate",
    items: [
      { href: "/", label: "Overview", icon: LayoutDashboard, shortcut: "G O",
        description: "Pipeline, agents, costs and deliverability" },
      { href: "/leads", label: "Leads", icon: Users, shortcut: "G L",
        description: "Every lead with its full history" },
      { href: "/approvals", label: "Approval queue", icon: MailCheck, shortcut: "G Q",
        description: "Approve, edit or reject drafted emails (Stage 1)" },
      { href: "/inbox", label: "Inbox", icon: Inbox, shortcut: "G I",
        description: "Positive replies, deals and pricing" },
    ],
  },
  {
    label: "Deliverables",
    items: [
      { href: "/audits", label: "Audits", icon: FileSearch, shortcut: "G A",
        description: "Website & SEO audit reports with evidence" },
      { href: "/previews", label: "Previews & logos", icon: Palette, shortcut: "G P",
        description: "Logo concepts and private website previews with QA" },
      { href: "/reports", label: "Weekly report", icon: BarChart3, shortcut: "G R",
        description: "What produces replies; learning-loop adjustments" },
    ],
  },
  {
    label: "Configure",
    items: [
      { href: "/sources", label: "Sources", icon: Radar, shortcut: "G S",
        description: "Enable sources, health and API keys" },
      { href: "/suppressions", label: "Suppression list", icon: Ban, shortcut: "G X",
        description: "Permanent do-not-contact list" },
      { href: "/settings", label: "Settings", icon: Settings2, shortcut: "G ,",
        description: "Autonomy, limits, budgets, languages, pricing" },
    ],
  },
];

export const ALL_NAV_ITEMS = NAV.flatMap((g) => g.items);
export const BRAND_ICON = Gauge;
