import {
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
    ],
  },
  {
    label: "Configure",
    items: [
      { href: "/sources", label: "Sources", icon: Radar, shortcut: "G S",
        description: "Enable sources, health and API keys" },
      { href: "/settings", label: "Settings", icon: Settings2, shortcut: "G ,",
        description: "Autonomy, limits, budgets, languages, pricing" },
    ],
  },
];

export const ALL_NAV_ITEMS = NAV.flatMap((g) => g.items);
export const BRAND_ICON = Gauge;
