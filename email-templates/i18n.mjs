// Fixed email labels per language. The personalised wording comes from the Proposal agent.
export const LABELS = {
  en: {
    found: "What we noticed on your website",
    prepared: "What we prepared for you (free, no obligation)",
    attachedLogos: "Logo concepts (attached)",
    attachedPreview: "A preview of a refreshed website (screenshots + PDF attached)",
    attachedAudit: "Your website audit report (PDF attached)",
    viewPreview: "View the private preview",
    reply: "Just reply to this email if you'd like to talk — no pressure either way.",
    unsubscribe: "Don't want emails like this? Unsubscribe",
    sentBy: "Sent by",
    followupIntro: "Just a quick follow-up on my previous email.",
    privacy: "We found your business through public information. We will not contact you again if you unsubscribe.",
  },
  de: {
    found: "Was uns auf Ihrer Website aufgefallen ist",
    prepared: "Was wir für Sie vorbereitet haben (kostenlos, unverbindlich)",
    attachedLogos: "Logo-Entwürfe (im Anhang)",
    attachedPreview: "Eine Vorschau einer erneuerten Website (Screenshots + PDF im Anhang)",
    attachedAudit: "Ihr Website-Audit (PDF im Anhang)",
    viewPreview: "Private Vorschau ansehen",
    reply: "Antworten Sie einfach auf diese E-Mail, wenn Sie sprechen möchten — ganz unverbindlich.",
    unsubscribe: "Keine solchen E-Mails mehr? Abmelden",
    sentBy: "Gesendet von",
    followupIntro: "Eine kurze Nachfrage zu meiner letzten E-Mail.",
    privacy: "Wir haben Ihr Unternehmen über öffentliche Informationen gefunden. Nach einer Abmeldung kontaktieren wir Sie nicht mehr.",
  },
  fr: {
    found: "Ce que nous avons remarqué sur votre site",
    prepared: "Ce que nous avons préparé pour vous (gratuit, sans engagement)",
    attachedLogos: "Propositions de logo (en pièce jointe)",
    attachedPreview: "Un aperçu d'un site rafraîchi (captures + PDF en pièce jointe)",
    attachedAudit: "L'audit de votre site (PDF en pièce jointe)",
    viewPreview: "Voir l'aperçu privé",
    reply: "Répondez simplement à cet e-mail si vous souhaitez en parler — sans aucune obligation.",
    unsubscribe: "Vous ne souhaitez plus recevoir ces e-mails ? Se désabonner",
    sentBy: "Envoyé par",
    followupIntro: "Un petit suivi de mon précédent e-mail.",
    privacy: "Nous avons trouvé votre entreprise via des informations publiques. Après désabonnement, nous ne vous recontacterons pas.",
  },
  es: {
    found: "Lo que notamos en su sitio web",
    prepared: "Lo que preparamos para usted (gratis, sin compromiso)",
    attachedLogos: "Propuestas de logotipo (adjuntas)",
    attachedPreview: "Una vista previa de un sitio renovado (capturas + PDF adjuntos)",
    attachedAudit: "La auditoría de su sitio (PDF adjunto)",
    viewPreview: "Ver la vista previa privada",
    reply: "Responda a este correo si desea conversar — sin ningún compromiso.",
    unsubscribe: "¿No quiere recibir estos correos? Darse de baja",
    sentBy: "Enviado por",
    followupIntro: "Un breve seguimiento de mi correo anterior.",
    privacy: "Encontramos su negocio mediante información pública. Si se da de baja, no volveremos a contactarle.",
  },
};

export function labels(lang) {
  return LABELS[lang] ?? LABELS.en;
}
