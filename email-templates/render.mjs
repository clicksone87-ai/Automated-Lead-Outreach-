// Render service: reads {"template": "proposal"|"followup", "props": {...}} from stdin and
// prints {"html": "...", "text": "..."} to stdout. Called by the backend render adapter.
import { render } from "@react-email/render";
import React from "react";
import { TEMPLATES } from "./templates.mjs";

async function readStdin() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  return Buffer.concat(chunks).toString("utf8");
}

const input = JSON.parse(await readStdin());
const Component = TEMPLATES[input.template];
if (!Component) {
  process.stderr.write(`unknown template: ${input.template}\n`);
  process.exit(2);
}
const element = React.createElement(Component, input.props);
const html = await render(element, { pretty: false });
const text = await render(element, { plainText: true });
process.stdout.write(JSON.stringify({ html, text }));
