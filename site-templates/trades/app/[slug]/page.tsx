import { notFound } from "next/navigation";
import { SiteBody } from "@/components/site";
import { pageBySlug } from "@/lib/content";
import { sample } from "@/lib/sample";

/** Extra pages mirror the original site's structure (redesign mode keeps the same pages). */
export function generateStaticParams() {
  const slugs = sample.pages.filter((p) => p.slug).map((p) => ({ slug: p.slug }));
  // Static export needs at least one param; the placeholder is never linked.
  return slugs.length ? slugs : [{ slug: "_" }];
}

export const dynamicParams = false;

export default async function ExtraPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const page = pageBySlug(sample, slug);
  if (!page) notFound();
  return <SiteBody c={sample} page={page} />;
}
