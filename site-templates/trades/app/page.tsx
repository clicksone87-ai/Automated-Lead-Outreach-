import { Site } from "@/components/site";
import { homePage } from "@/lib/content";
import { sample } from "@/lib/sample";

export default function Home() {
  return <Site c={sample} page={homePage(sample)} v={sample.variant} />;
}
