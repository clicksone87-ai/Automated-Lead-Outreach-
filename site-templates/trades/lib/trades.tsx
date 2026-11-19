/** One simple line icon per trade, matched from the trade word in any supported language. */
const ICONS: { match: RegExp; paths: string[] }[] = [
  { match: /plumb|klemp|sanit|rørl|rorl|plomb|fontan|idraul|loodgiet|hydraul|hydraulik|vvs|putkiasen|instalat|encanad|υδραυλ|vízszerel/i,
    paths: ["M12 3c3 4 5 6.5 5 9a5 5 0 0 1-10 0c0-2.5 2-5 5-9Z", "M10 13.5a2 2 0 0 0 2 2"] },
  { match: /electr|elektr|élect|elettric|eletric|sähk|electric|ηλεκτρ|villanyszer/i,
    paths: ["M13 2 4.5 13.5H12L11 22l8.5-11.5H12L13 2Z"] },
  { match: /roof|dach|couvr|tetto|telhad|dak|tak|katt|dekar|pokryw|acoperi|στέγ|tetőfed|tejad/i,
    paths: ["M3 11.5 12 4l9 7.5", "M5.5 9.5V20h13V9.5", "M10 20v-5h4v5"] },
  { match: /paint|maler|peint|pint|schilder|malarz|maal|malíř|zugrav|βαφ|festő/i,
    paths: ["M4 4h12v5H4z", "M16 6.5h3v5l-7 2V17", "M10.5 17h3v4h-3z"] },
  { match: /carpent|joiner|tischl|schrein|menuis|carpint|falegn|timmer|snickar|tømrer|tomrer|puusep|tesař|stolar|tâmpl|ξυλουρ|asztalos|ács/i,
    paths: ["M14.5 4.5 19.5 9.5", "M3 21l9-9", "M12 12l2.5 2.5L21 8 16 3l-6.5 6.5Z"] },
  { match: /garden|landscap|garten|jardin|giardin|tuin|ogrod|ogród|trädg|anlæg|puutar|zahrad|grădin|κηπ|kertész/i,
    paths: ["M5 19c0-8 6-14 15-14-1 9-7 15-15 14Z", "M5 19 13 11"] },
  { match: /clean|reinig|nettoy|limpi|pulizi|schoonm|sprząt|städ|rengør|siivo|úklid|curăț|καθαρ|takarít/i,
    paths: ["M12 3v4M12 17v4M3 12h4M17 12h4", "M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6"] },
  { match: /lock|schlüss|serrur|cerraj|fabbro|slotenm|ślus|lås|lukkos|zámeč|lăcătu|κλειδαρ|lakatos/i,
    paths: ["M7.5 14.5a4 4 0 1 1 5.6-5.6", "M13 9l7 7v3h-3v-2h-2v-2h-2"] },
  { match: /heat|hvac|boiler|heiz|chauff|calef|riscald|verwarm|ogrzew|värme|varme|lämm|topen|încălz|θέρμαν|fűtés/i,
    paths: ["M12 3c1 3 4 4.5 4 8.5a4 4 0 0 1-8 0c0-2 1-3 2-4 0 2 1 3 2 3 0-3-1-5 0-7.5Z"] },
  { match: /build|bau|construct|bouw|budow|bygg|rakenn|stav|construc|οικοδ|építő|maurer|maçon|alban/i,
    paths: ["M3 20h18", "M5 20V10h6v10", "M11 14h8v6", "M7 13h2M7 16h2M14 17h2"] },
];
const DEFAULT = ["M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.6 2.6-2.4-.6-.6-2.4 2.6-2.6Z"];

export function tradeIconPaths(trade: string): string[] {
  return ICONS.find((i) => i.match.test(trade))?.paths ?? DEFAULT;
}

export function TradeIcon({ trade, className = "size-6" }: { trade: string; className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth={1.75}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      {tradeIconPaths(trade).map((d) => <path key={d} d={d} />)}
    </svg>
  );
}
