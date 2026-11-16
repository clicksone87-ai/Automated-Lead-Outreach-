/** Fixed interface labels per language (copy itself comes from content.json). */
const LABELS = {
  en: {
    call: "Call", services: "Services", about: "About us", areas: "Areas we cover",
    contact: "Get in touch", name: "Your name", email: "Email", message: "How can we help?",
    send: "Send message", formDisabled: "Preview — this form is not connected yet.",
    menu: "Menu", rights: "All rights reserved.",
  },
  de: {
    call: "Anrufen", services: "Leistungen", about: "Über uns", areas: "Einsatzgebiet",
    contact: "Kontakt", name: "Ihr Name", email: "E-Mail", message: "Wie können wir helfen?",
    send: "Nachricht senden", formDisabled: "Vorschau — dieses Formular ist noch nicht verbunden.",
    menu: "Menü", rights: "Alle Rechte vorbehalten.",
  },
  fr: {
    call: "Appeler", services: "Services", about: "À propos", areas: "Zones d’intervention",
    contact: "Nous contacter", name: "Votre nom", email: "E-mail", message: "Comment pouvons-nous aider ?",
    send: "Envoyer", formDisabled: "Aperçu — ce formulaire n’est pas encore connecté.",
    menu: "Menu", rights: "Tous droits réservés.",
  },
  es: {
    call: "Llamar", services: "Servicios", about: "Sobre nosotros", areas: "Zonas de servicio",
    contact: "Contacto", name: "Su nombre", email: "Correo", message: "¿Cómo podemos ayudar?",
    send: "Enviar mensaje", formDisabled: "Vista previa — este formulario aún no está conectado.",
    menu: "Menú", rights: "Todos los derechos reservados.",
  },
} as const;

export type Labels = (typeof LABELS)["en"];

export function labels(lang: string): Labels {
  return (LABELS as Record<string, Labels>)[lang] ?? LABELS.en;
}
