/** Fixed interface labels per language (copy itself comes from content.json). */
export interface Labels {
  call: string; services: string; about: string; areas: string; contact: string; name: string;
  email: string; message: string; send: string; formDisabled: string; menu: string; rights: string; design: string;
}

const LABELS: Record<string, Labels> = {
  en: {
    call: "Call", services: "Services", about: "About us", areas: "Areas we cover",
    contact: "Get in touch", name: "Your name", email: "Email", message: "How can we help?",
    send: "Send message", formDisabled: "Preview — this form is not connected yet.",
    menu: "Menu", rights: "All rights reserved.", design: "Design",
  },
  de: {
    call: "Anrufen", services: "Leistungen", about: "Über uns", areas: "Einsatzgebiet",
    contact: "Kontakt", name: "Ihr Name", email: "E-Mail", message: "Wie können wir helfen?",
    send: "Nachricht senden", formDisabled: "Vorschau — dieses Formular ist noch nicht verbunden.",
    menu: "Menü", rights: "Alle Rechte vorbehalten.", design: "Design",
  },
  fr: {
    call: "Appeler", services: "Services", about: "À propos", areas: "Zones d’intervention",
    contact: "Nous contacter", name: "Votre nom", email: "E-mail", message: "Comment pouvons-nous aider ?",
    send: "Envoyer", formDisabled: "Aperçu — ce formulaire n’est pas encore connecté.",
    menu: "Menu", rights: "Tous droits réservés.", design: "Style",
  },
  es: {
    call: "Llamar", services: "Servicios", about: "Sobre nosotros", areas: "Zonas de servicio",
    contact: "Contacto", name: "Su nombre", email: "Correo", message: "¿Cómo podemos ayudar?",
    send: "Enviar mensaje", formDisabled: "Vista previa — este formulario aún no está conectado.",
    menu: "Menú", rights: "Todos los derechos reservados.", design: "Diseño",
  },
  it: {
    call: "Chiama", services: "Servizi", about: "Chi siamo", areas: "Zone servite",
    contact: "Contattaci", name: "Il tuo nome", email: "Email", message: "Come possiamo aiutarti?",
    send: "Invia messaggio", formDisabled: "Anteprima — questo modulo non è ancora collegato.",
    menu: "Menu", rights: "Tutti i diritti riservati.", design: "Stile",
  },
  nl: {
    call: "Bellen", services: "Diensten", about: "Over ons", areas: "Werkgebied",
    contact: "Contact opnemen", name: "Uw naam", email: "E-mail", message: "Hoe kunnen we helpen?",
    send: "Bericht verzenden", formDisabled: "Voorbeeld — dit formulier is nog niet gekoppeld.",
    menu: "Menu", rights: "Alle rechten voorbehouden.", design: "Ontwerp",
  },
  pt: {
    call: "Ligar", services: "Serviços", about: "Sobre nós", areas: "Áreas de atuação",
    contact: "Contacte-nos", name: "O seu nome", email: "Email", message: "Como podemos ajudar?",
    send: "Enviar mensagem", formDisabled: "Pré-visualização — este formulário ainda não está ligado.",
    menu: "Menu", rights: "Todos os direitos reservados.", design: "Design",
  },
  pl: {
    call: "Zadzwoń", services: "Usługi", about: "O nas", areas: "Obszar działania",
    contact: "Kontakt", name: "Twoje imię", email: "E-mail", message: "W czym możemy pomóc?",
    send: "Wyślij wiadomość", formDisabled: "Podgląd — ten formularz nie jest jeszcze podłączony.",
    menu: "Menu", rights: "Wszelkie prawa zastrzeżone.", design: "Projekt",
  },
  sv: {
    call: "Ring", services: "Tjänster", about: "Om oss", areas: "Områden vi täcker",
    contact: "Kontakta oss", name: "Ditt namn", email: "E-post", message: "Hur kan vi hjälpa till?",
    send: "Skicka meddelande", formDisabled: "Förhandsvisning — detta formulär är inte anslutet än.",
    menu: "Meny", rights: "Alla rättigheter förbehållna.", design: "Design",
  },
  da: {
    call: "Ring", services: "Ydelser", about: "Om os", areas: "Områder vi dækker",
    contact: "Kontakt os", name: "Dit navn", email: "E-mail", message: "Hvad kan vi hjælpe med?",
    send: "Send besked", formDisabled: "Forhåndsvisning — denne formular er ikke tilsluttet endnu.",
    menu: "Menu", rights: "Alle rettigheder forbeholdes.", design: "Design",
  },
  nb: {
    call: "Ring", services: "Tjenester", about: "Om oss", areas: "Områder vi dekker",
    contact: "Kontakt oss", name: "Ditt navn", email: "E-post", message: "Hvordan kan vi hjelpe?",
    send: "Send melding", formDisabled: "Forhåndsvisning — dette skjemaet er ikke tilkoblet ennå.",
    menu: "Meny", rights: "Alle rettigheter forbeholdt.", design: "Design",
  },
  fi: {
    call: "Soita", services: "Palvelut", about: "Tietoa meistä", areas: "Toiminta-alueet",
    contact: "Ota yhteyttä", name: "Nimesi", email: "Sähköposti", message: "Miten voimme auttaa?",
    send: "Lähetä viesti", formDisabled: "Esikatselu — tämä lomake ei ole vielä käytössä.",
    menu: "Valikko", rights: "Kaikki oikeudet pidätetään.", design: "Ulkoasu",
  },
  cs: {
    call: "Zavolat", services: "Služby", about: "O nás", areas: "Oblast působnosti",
    contact: "Kontakt", name: "Vaše jméno", email: "E-mail", message: "S čím vám můžeme pomoci?",
    send: "Odeslat zprávu", formDisabled: "Náhled — tento formulář zatím není připojen.",
    menu: "Menu", rights: "Všechna práva vyhrazena.", design: "Vzhled",
  },
  ro: {
    call: "Sunați", services: "Servicii", about: "Despre noi", areas: "Zone deservite",
    contact: "Contactați-ne", name: "Numele dumneavoastră", email: "E-mail", message: "Cu ce vă putem ajuta?",
    send: "Trimite mesaj", formDisabled: "Previzualizare — acest formular nu este încă conectat.",
    menu: "Meniu", rights: "Toate drepturile rezervate.", design: "Design",
  },
  el: {
    call: "Κλήση", services: "Υπηρεσίες", about: "Σχετικά με εμάς", areas: "Περιοχές κάλυψης",
    contact: "Επικοινωνία", name: "Το όνομά σας", email: "Email", message: "Πώς μπορούμε να βοηθήσουμε;",
    send: "Αποστολή μηνύματος", formDisabled: "Προεπισκόπηση — αυτή η φόρμα δεν είναι ακόμη συνδεδεμένη.",
    menu: "Μενού", rights: "Με επιφύλαξη παντός δικαιώματος.", design: "Σχέδιο",
  },
  hu: {
    call: "Hívás", services: "Szolgáltatások", about: "Rólunk", areas: "Működési területek",
    contact: "Kapcsolat", name: "Az Ön neve", email: "E-mail", message: "Miben segíthetünk?",
    send: "Üzenet küldése", formDisabled: "Előnézet — ez az űrlap még nincs csatlakoztatva.",
    menu: "Menü", rights: "Minden jog fenntartva.", design: "Dizájn",
  },
};

export function labels(lang: string): Labels {
  const code = lang === "no" ? "nb" : lang;
  return LABELS[code] ?? (LABELS.en as Labels);
}
