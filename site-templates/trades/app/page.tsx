import { PageBody } from "@/components/site";
import { pageBySlug } from "@/lib/content";

export default function Home() {
  const page = pageBySlug("") ?? { slug: "", title: "Home", sections: ["hero", "services", "about", "areas", "contact"] as const };
  return <PageBody page={{ ...page, sections: [...page.sections] }} />;
}
