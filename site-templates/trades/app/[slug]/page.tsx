import { notFound } from "next/navigation";
import { PageBody } from "@/components/site";
import { content, pageBySlug } from "@/lib/content";

/** Extra pages mirror the original site's structure (redesign mode keeps the same pages). */
export function generateStaticParams() {
  const slugs = content.pages.filter((p) => p.slug).map((p) => ({ slug: p.slug }));
  // Static export needs at least one param; the placeholder is never linked or built into nav.
  return slugs.length ? slugs : [{ slug: "_" }];
}

export const dynamicParams = false;

export default async function ExtraPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const page = pageBySlug(slug);
  if (!page) notFound();
  return <PageBody page={page} />;
}
