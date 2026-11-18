import { Site } from "@/components/site";
import { content, homePage } from "@/lib/content";

export default function Home() {
  return <Site page={homePage()} v={content.variant} />;
}
