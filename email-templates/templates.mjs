// React Email templates written with React.createElement (no JSX build step needed).
import {
  Body,
  Container,
  Head,
  Hr,
  Html,
  Link,
  Preview,
  Section,
  Text,
} from "@react-email/components";
import React from "react";
import { labels } from "./i18n.mjs";

const h = React.createElement;

const styles = {
  body: { backgroundColor: "#f6f7f9", fontFamily: "Helvetica, Arial, sans-serif", margin: 0, padding: "24px 0" },
  container: { backgroundColor: "#ffffff", borderRadius: "8px", maxWidth: "580px", margin: "0 auto", padding: "28px 32px" },
  p: { color: "#1f2937", fontSize: "15px", lineHeight: "24px", margin: "0 0 14px" },
  h: { color: "#111827", fontSize: "15px", fontWeight: 700, margin: "20px 0 8px" },
  li: { color: "#1f2937", fontSize: "15px", lineHeight: "22px", margin: "0 0 6px" },
  muted: { color: "#6b7280", fontSize: "12px", lineHeight: "18px", margin: "0 0 6px" },
  link: { color: "#1d4ed8" },
};

function Paragraphs({ text }) {
  return h(
    React.Fragment,
    null,
    String(text || "")
      .split(/\n{2,}/)
      .filter(Boolean)
      .map((p, i) => h(Text, { key: i, style: styles.p }, p.trim())),
  );
}

function Footer({ sender, unsubscribeUrl, t }) {
  return h(
    Section,
    null,
    h(Hr, { style: { borderColor: "#e5e7eb", margin: "24px 0 16px" } }),
    h(Text, { style: styles.muted }, `${t.sentBy} ${sender.name}, ${sender.businessName} · ${sender.postalAddress}`),
    sender.portfolioUrl
      ? h(Text, { style: styles.muted }, h(Link, { href: sender.portfolioUrl, style: styles.link }, sender.portfolioUrl))
      : null,
    h(Text, { style: styles.muted }, t.privacy),
    h(Text, { style: styles.muted }, h(Link, { href: unsubscribeUrl, style: styles.link }, t.unsubscribe)),
  );
}

export function ProposalEmail(props) {
  const t = labels(props.language);
  const prepared = [];
  if (props.attachments?.logos) prepared.push(t.attachedLogos);
  if (props.attachments?.preview) prepared.push(t.attachedPreview);
  if (props.attachments?.audit) prepared.push(t.attachedAudit);
  return h(
    Html,
    { lang: props.language },
    h(Head, null),
    h(Preview, null, props.previewText || props.subject),
    h(
      Body,
      { style: styles.body },
      h(
        Container,
        { style: styles.container },
        h(Text, { style: styles.p }, props.greeting),
        h(Paragraphs, { text: props.intro }),
        props.findings?.length
          ? h(
              Section,
              null,
              h(Text, { style: styles.h }, t.found),
              ...props.findings.map((f, i) => h(Text, { key: `f${i}`, style: styles.li }, `• ${f}`)),
            )
          : null,
        prepared.length
          ? h(
              Section,
              null,
              h(Text, { style: styles.h }, t.prepared),
              ...prepared.map((p, i) => h(Text, { key: `p${i}`, style: styles.li }, `• ${p}`)),
              props.previewUrl
                ? h(Text, { style: styles.p }, h(Link, { href: props.previewUrl, style: styles.link }, t.viewPreview))
                : null,
            )
          : null,
        h(Paragraphs, { text: props.closing }),
        h(Text, { style: styles.p }, t.reply),
        h(Text, { style: styles.p }, props.signoff, h("br"), props.sender.name, h("br"), props.sender.businessName),
        h(Footer, { sender: props.sender, unsubscribeUrl: props.unsubscribeUrl, t }),
      ),
    ),
  );
}

export function FollowupEmail(props) {
  const t = labels(props.language);
  return h(
    Html,
    { lang: props.language },
    h(Head, null),
    h(Preview, null, t.followupIntro),
    h(
      Body,
      { style: styles.body },
      h(
        Container,
        { style: styles.container },
        h(Text, { style: styles.p }, props.greeting),
        h(Text, { style: styles.p }, t.followupIntro),
        h(Paragraphs, { text: props.body }),
        h(Text, { style: styles.p }, t.reply),
        h(Text, { style: styles.p }, props.signoff, h("br"), props.sender.name),
        h(Footer, { sender: props.sender, unsubscribeUrl: props.unsubscribeUrl, t }),
      ),
    ),
  );
}

export const TEMPLATES = { proposal: ProposalEmail, followup: FollowupEmail };
