import { notFound } from "next/navigation";
import { Site } from "@/components/site";
import { DESIGNS, homePage, type Variant } from "@/lib/content";
import { sample } from "@/lib/sample";

/** The other designs, side by side with "/": the client compares all three in one preview. */
export function generateStaticParams() {
  return DESIGNS.map((variant) => ({ variant }));
}

export const dynamicParams = false;

export default async function DesignPage({ params }: { params: Promise<{ variant: string }> }) {
  const { variant } = await params;
  if (!DESIGNS.includes(variant as Variant)) notFound();
  return <Site c={sample} page={homePage(sample)} v={variant as Variant} />;
}
