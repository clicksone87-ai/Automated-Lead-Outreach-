export type Family = "social" | "google" | "registry" | "other";
export type Tier = "high" | "medium" | "unscored" | "low";

// Plain-language names, a one-line hint, and the phrase used in the "Showing…" sentence.
export interface FamilyInfo {
  label: string;
  hint: string;
  phrase: string;
}

export const ALL_FAMILIES: FamilyInfo = {
  label: "All leads",
  hint: "Every lead, wherever we found it.",
  phrase: "from every source",
};

export const FAMILY_INFO: Record<Family, FamilyInfo> = {
  social: {
    label: "Social media",
    hint: "People asking for a website, logo or SEO on Reddit, X, Instagram or Facebook.",
    phrase: "from social media",
  },
  google: {
    label: "Google search",
    hint: "Pages found on Google, including businesses with old or broken websites.",
    phrase: "from Google search",
  },
  registry: {
    label: "New companies",
    hint: "Newly registered businesses from official company registers.",
    phrase: "from company registers",
  },
  other: {
    label: "Other places",
    hint: "Feeds and directories.",
    phrase: "from other places",
  },
};

// Kept for existing imports.
export const FAMILIES: Record<Family, string> = {
  social: FAMILY_INFO.social.label,
  google: FAMILY_INFO.google.label,
  registry: FAMILY_INFO.registry.label,
  other: FAMILY_INFO.other.label,
};

const FAMILY_SOURCES: Record<Family, string[]> = {
  social: ["social_providers", "social_instagram", "social_facebook", "scout_profiles", "social_reddit", "social_x", "reddit", "x", "meta"],
  google: ["search_api", "local_sites", "site_health"],
  registry: ["companies_house", "opencorporates"],
  other: ["rss", "web_directory", "no_website_check"],
};

export const familySources = (f: Family) => FAMILY_SOURCES[f];

// Intent score is 0-100: how clearly the lead wants a website, logo or SEO.
// High 75+, Medium 60-74, Low <60. No tier chosen = good leads (60+) plus leads not scored yet.
// "unscored" asks the API for leads whose score is still empty.
export const QUALIFIED_MIN = 60;

export interface TierInfo {
  label: string;
  hint: string;
  one: string;
  many: string;
  min?: number;
  max?: number;
  unscored?: "include" | "only";
}

export const GOOD_LEADS: TierInfo = {
  label: "Good leads",
  hint: "Scored 60 or more out of 100 for how clearly they want a website, logo or SEO, plus new leads still waiting for a score.",
  one: "good or not-yet-scored lead",
  many: "good or not-yet-scored leads",
  min: QUALIFIED_MIN,
  unscored: "include",
};

export const TIERS: Record<Tier, TierInfo> = {
  high: {
    label: "Very interested",
    hint: "Scored 75 or more. These are asking for help right now.",
    one: "very interested lead",
    many: "very interested leads",
    min: 75,
  },
  medium: {
    label: "Somewhat interested",
    hint: "Scored 60 to 74. Worth contacting, but less urgent.",
    one: "somewhat interested lead",
    many: "somewhat interested leads",
    min: 60,
    max: 74,
  },
  unscored: {
    label: "Not scored yet",
    hint: "Our system hasn't scored these yet. They may still be worth a look.",
    one: "lead waiting for a score",
    many: "leads waiting for a score",
    unscored: "only",
  },
  low: {
    label: "Not interested yet",
    hint: "Scored below 60. Kept separate so they never mix with good leads.",
    one: "lead that isn't interested yet",
    many: "leads that aren't interested yet",
    max: 59,
  },
};
