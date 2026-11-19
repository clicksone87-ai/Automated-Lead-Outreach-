// Compile the template's Tailwind CSS once per template version: node render/build-css.mjs
// The class names live in the components, so one stylesheet serves every preview.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import tailwind from "@tailwindcss/postcss";
import postcss from "postcss";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const from = path.join(root, "app", "globals.css");
const result = await postcss([tailwind({ base: root, optimize: true })]).process(
  fs.readFileSync(from, "utf8"),
  { from, to: path.join(root, "styles.css") },
);
fs.writeFileSync(path.join(root, "styles.css"), result.css, "utf8");
console.log(JSON.stringify({ bytes: result.css.length }));
