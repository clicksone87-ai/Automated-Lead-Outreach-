"""Rule-based prefilter for explicit requests: plain code, no LLM.

Phrase lists cover the 16 drafting languages (EN DE FR ES IT NL PT PL SV DA FI NO CS RO EL HU).
They are applied together, because language detection on short posts is unreliable. A post in
any other language is never rejected on its wording; the LLM decides.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

DRAFTING_LANGUAGES = frozenset(
    {"en", "de", "fr", "es", "it", "nl", "pt", "pl", "sv", "da", "fi", "no", "cs", "ro", "el", "hu"}
)


def _rx(*parts: str) -> re.Pattern[str]:
    return re.compile(r"(?<!\w)(?:" + "|".join(parts) + r")(?!\w)", re.I)


# Our services: website, web designer, logo, SEO, redesign — per language.
SERVICE = _rx(
    # en
    r"web ?sites?",
    r"web ?pages?",
    r"landing pages?",
    r"web ?(?:designer|developer|design)s?",
    r"logos?",
    r"logo ?design",
    r"branding",
    r"brand identity",
    r"seo",
    r"search engine optimi[sz]ation",
    r"re-?design",
    r"online presence",
    # de
    r"webseiten?",
    r"homepages?",
    r"internetseiten?",
    r"internetauftritt",
    r"webentwickler(?:in)?",
    r"suchmaschinenoptimierung",
    r"relaunch",
    r"neugestaltung",
    # fr
    r"sites? (?:web|internet|vitrine)",
    r"développeur web",
    r"création de site",
    r"référencement",
    r"refonte",
    # es / pt
    r"páginas? web",
    r"sitios? web",
    r"diseñador(?:a)? web",
    r"desarrollador(?:a)? web",
    r"logotipos?",
    r"posicionamiento",
    r"rediseño",
    r"sites?",
    r"sítios? web",
    r"desenvolvedor(?:a)? web",
    r"reformulação",
    # it
    r"siti? (?:web|internet)",
    r"sviluppatore web",
    r"posizionamento",
    r"restyling",
    r"rifacimento",
    # nl
    r"webontwikkelaar",
    r"vindbaarheid",
    # pl
    r"stron\w* (?:internetow\w*|www)",
    r"projektant\w* stron\w*",
    r"pozycjonowani\w*",
    r"przebudow\w*",
    # sv / da / no
    r"hemsidan?",
    r"webbplats(?:en)?",
    r"webbsid(?:a|an)",
    r"webbdesigner",
    r"loggan?",
    r"logotyp",
    r"sökmotoroptimering",
    r"hjemmesiden?",
    r"websiden?",
    r"søgemaskineoptimering",
    r"nettsiden?",
    r"søkemotoroptimalisering",
    # fi
    r"kotisivu\w*",
    r"verkkosivu\w*",
    r"nettisivu\w*",
    r"hakukoneoptimointi",
    # cs
    r"webov\w* str[áa]n\w*",
    r"webovky",
    # ro
    r"pagin[ăa] web",
    # el
    r"ιστοσελίδ\w*",
    r"ιστότοπ\w*",
    r"λογότυπ\w*",
    # hu
    r"weboldal\w*",
    r"honlap\w*",
    r"weblap\w*",
    r"logó\w*",
    r"keresőoptimalizálás",
)

# Someone asking for help — per language.
REQUEST = _rx(
    # en
    r"looking for",
    r"need(?:s|ed)?",
    r"hiring",
    r"seeking",
    r"recommend(?:ations?)?",
    r"anyone (?:know|recommend|build|do)",
    r"who can",
    r"can (?:anyone|someone|somebody)",
    r"quotes?",
    r"budget",
    r"help with",
    r"want (?:a|someone|to get)",
    r"\[hiring\]",
    r"\[task\]",
    # de
    r"suche(?:n)?",
    r"brauche(?:n)?",
    r"benötige(?:n)?",
    r"empfehl(?:en|ung|t)",
    r"wer kann",
    r"gesucht",
    # fr
    r"cherche",
    r"recherche",
    r"besoin",
    r"recommand(?:er|ez|ation)",
    r"quelqu'un",
    r"devis",
    # es
    r"busco",
    r"buscamos",
    r"necesito",
    r"necesitamos",
    r"recomienda(?:n|r)?",
    r"alguien",
    r"presupuesto",
    # it
    r"cerco",
    r"cerchiamo",
    r"ho bisogno",
    r"abbiamo bisogno",
    r"consiglia(?:te|re)",
    r"qualcuno",
    r"preventivo",
    # nl
    r"zoek(?:en)?",
    r"gezocht",
    r"nodig",
    r"aanraden",
    r"iemand",
    r"offerte",
    # pt
    r"procuro",
    r"procuramos",
    r"preciso",
    r"precisamos",
    r"recomenda(?:m|r)",
    r"alguém",
    r"orçamento",
    # pl
    r"szukam",
    r"szukamy",
    r"potrzebuj[ęe]",
    r"potrzebujemy",
    r"poleci(?:cie|ć)",
    r"ktoś",
    r"wycen[ay]",
    # sv
    r"söker",
    r"letar efter",
    r"behöver",
    r"rekommendera",
    r"någon",
    r"offert",
    # da / no
    r"søger",
    r"leder efter",
    r"har brug for",
    r"anbefale",
    r"nogen",
    r"tilbud",
    r"søker",
    r"ser etter",
    r"trenger",
    r"noen",
    # fi
    r"etsin",
    r"etsimme",
    r"tarvitse(?:n|mme)",
    r"suositel(?:la|kaa)",
    r"joku",
    r"tarjous",
    # cs
    r"hledám",
    r"hledáme",
    r"potřebuj(?:i|eme)",
    r"doporuč(?:te|it)",
    r"někdo",
    r"nabídk[ua]",
    # ro
    r"caut",
    r"căutăm",
    r"am nevoie",
    r"recomandați",
    r"cineva",
    r"ofert[ăa]",
    # el
    r"ψάχνω",
    r"ψάχνουμε",
    r"χρειάζομαι",
    r"χρειαζόμαστε",
    r"προτείνετε",
    r"κάποιος",
    # hu
    r"keres(?:ek|ünk)",
    r"kellene",
    r"szükségem",
    r"ajánl(?:anátok|jatok)",
    r"valaki",
    r"árajánlat",
)

# A clear first-person request for someone else to do the work; outweighs seller wording.
STRONG_BUYER = _rx(
    r"(?:looking for|need|hiring|seeking|recommend) (?:a|an|some|someone|somebody|good|reliable|"
    r"local|freelance)\b.{0,40}(?:designer|developer|agency|website|logo|seo)",
    r"(?:can|could) (?:anyone|someone|somebody) (?:recommend|build|make|design|help)",
    r"suche (?:eine[nr]?|jemand\w*)",
    r"je cherche (?:un|une|quelqu)",
    r"busco (?:un|una|alguien)",
    r"cerco (?:un|una|qualcuno)",
    r"ik zoek (?:een|iemand)",
    r"procuro (?:um|uma|alguém)",
)

# Designers and agencies advertising themselves. The offer has to name what is sold, because
# buyers describe their own business too ("a site that lists my services").
_SOLD = (
    r"(?:\w+ ){0,3}(?:web ?sites?|logos?|landing pages?|brands?|branding|seo|web ?design|"
    r"online (?:stores?|shops?)|e-?commerce)"
)
SELLER = _rx(
    r"\[for hire\]",
    r"\[offer\]",
    rf"i (?:design|build|make|create|develop|offer|do) {_SOLD}",
    rf"we (?:design|build|make|create|develop|offer|do) {_SOLD}",
    rf"i will (?:design|build|make|create|develop|do|fix|redesign) {_SOLD}",
    r"i(?:'| a)m an? (?:web|logo|graphic|seo|ux|freelance web|freelance graphic) ?(?:designer|developer|expert|specialist)",
    r"(?:web|logo|graphic) designer here",
    r"hire me",
    r"dm me (?:for|if)",
    r"available for (?:work|hire|new projects|freelance)",
    r"taking (?:on )?(?:new )?(?:clients|projects|orders)",
    r"looking for (?:new )?(?:clients|customers|projects|work)",
    r"check out my (?:portfolio|work|website)",
    r"my portfolio",
    r"(?:my|our) (?:design |web |seo )?(?:agency|studio)",
    r"(?:starting|starts|only) (?:at|from) [$£€]\s?\d+",
    r"get your (?:new )?(?:website|logo)",
    r"fiverr\.com",
    r"upwork\.com/freelancers",
    # de / fr / es / it / nl / pt
    r"ich erstelle",
    r"wir erstellen",
    r"ich biete",
    r"unsere agentur",
    r"mein portfolio",
    r"je crée",
    r"je réalise",
    r"nous créons",
    r"notre agence",
    r"mon portfolio",
    r"ofrezco",
    r"nuestra agencia",
    r"mi portafolio",
    r"realizzo",
    r"la nostra agenzia",
    r"ik maak",
    r"wij maken",
    r"ons bureau",
    r"crio sites",
    r"faço sites",
    r"nossa agência",
)

# Job boards: every listing is a request, whatever its wording.
BOARD_URL = re.compile(
    r"(?:freelancer\.[a-z.]+/projects/|upwork\.com/(?:jobs|freelance-jobs)/|"
    r"peopleperhour\.com/freelance-jobs/|guru\.com/work/)",
    re.I,
)


@dataclass(frozen=True, slots=True)
class Verdict:
    passed: bool
    reason: str = ""
    needs_llm: bool = False


def check_text(text: str, language: str | None, *, job_listing: bool = False) -> Verdict:
    """Is an explicit-request post worth the LLM? Rejects sellers and posts asking for nothing."""
    if not text.strip():
        return Verdict(False, "empty post")
    known = (language or "en") in DRAFTING_LANGUAGES
    if not known:
        return Verdict(True, "language without phrase lists: left to the LLM", needs_llm=True)
    if not job_listing and SELLER.search(text) and not STRONG_BUYER.search(text):
        return Verdict(False, "seller: someone advertising their own design/SEO services")
    if not SERVICE.search(text):
        return Verdict(False, "no logo, website, redesign or SEO mentioned")
    if not job_listing and not REQUEST.search(text) and not STRONG_BUYER.search(text):
        return Verdict(False, "mentions our services but asks for nothing")
    return Verdict(True, needs_llm=True)


def stale_reason(
    posted_at: datetime | None, *, family: str, now: datetime, max_age_hours: dict[str, int]
) -> str | None:
    """Too old to act on, or (social posts) no timestamp to tell."""
    limit = max_age_hours.get(family, max_age_hours.get("default"))
    if limit is None:
        return None
    if posted_at is None:
        return "social post without a timestamp" if family == "social" else None
    age_h = (now - posted_at).total_seconds() / 3600
    if age_h > limit:
        return f"post is {age_h:.0f} h old (limit {limit} h)"
    return None
