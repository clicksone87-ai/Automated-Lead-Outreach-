import { SiteBody } from "@/components/site";
import { homePage } from "@/lib/content";
import { sample } from "@/lib/sample";

export default function Home() {
  return <SiteBody c={sample} page={homePage(sample)} />;
}
