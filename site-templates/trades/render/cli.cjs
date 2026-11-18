// Render one preview: node render/cli.cjs <content.json> <out_dir>
// Uses the compiled renderer (dist-render/) and stylesheet (styles.css) next to this folder.
"use strict";

const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const [contentPath, outDir] = process.argv.slice(2);
if (!contentPath || !outDir) {
  console.error("usage: node render/cli.cjs <content.json> <out_dir>");
  process.exit(2);
}

const { renderSite } = require(path.join(root, "dist-render", "render", "render.js"));
const content = JSON.parse(fs.readFileSync(contentPath, "utf8"));
const pages = renderSite(content);

fs.mkdirSync(outDir, { recursive: true });
for (const [rel, html] of Object.entries(pages)) {
  const target = path.join(outDir, rel);
  if (!target.startsWith(path.resolve(outDir))) throw new Error(`refusing to write outside ${outDir}: ${rel}`);
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, html, "utf8");
}
fs.copyFileSync(path.join(root, "styles.css"), path.join(outDir, "styles.css"));
console.log(JSON.stringify({ pages: Object.keys(pages).length }));
