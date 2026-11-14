import { formatDistanceToNowStrict, format as fmt } from "date-fns";

export function usd(value: number, digits = 2): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value);
}

export function money(value: number, currency: "USD" | "EUR" | "GBP"): string {
  return new Intl.NumberFormat(currency === "USD" ? "en-US" : currency === "GBP" ? "en-GB" : "de-DE", {
    style: "currency",
    currency,
    maximumFractionDigits: 0,
  }).format(value);
}

export function pct(value: number, digits = 1): string {
  return `${(value * 100).toFixed(digits)}%`;
}

export function compact(value: number): string {
  return new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

export function ago(iso: string | null | undefined): string {
  if (!iso) return "never";
  return `${formatDistanceToNowStrict(new Date(iso))} ago`;
}

/** Compact relative time for tight spaces: "now", "45s", "11m", "3h", "2d" (+ " ago"). */
export function agoShort(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return "never";
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (s < 10) return "just now";
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export function dateTime(iso: string | null | undefined): string {
  return iso ? fmt(new Date(iso), "d MMM yyyy, HH:mm") : "—";
}

export function titleCase(value: string): string {
  return value.replace(/[_-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

const COUNTRY_NAMES: Record<string, string> = {
  US: "United States", GB: "United Kingdom", IE: "Ireland", DE: "Germany", FR: "France",
  ES: "Spain", NO: "Norway", IT: "Italy", NL: "Netherlands",
};

export function countryName(code: string | null | undefined): string {
  return code ? (COUNTRY_NAMES[code] ?? code) : "Unknown";
}

export function flag(code: string | null | undefined): string {
  if (!code || code.length !== 2) return "🏳️";
  return String.fromCodePoint(...[...code.toUpperCase()].map((c) => 0x1f1a5 + c.charCodeAt(0)));
}

const LANGUAGE_NAMES: Record<string, string> = {
  en: "English", de: "German", fr: "French", es: "Spanish", nb: "Norwegian", it: "Italian",
  nl: "Dutch", pt: "Portuguese",
};

export function languageName(code: string | null | undefined): string {
  return code ? (LANGUAGE_NAMES[code] ?? code.toUpperCase()) : "Unknown";
}
