"""Trigger: broken or outdated websites of local businesses found through a search API.

One "<trade> <city>" query per run (rotated). Each homepage is fetched once through the safe
fetcher and classified with the site-health signals; only broken or outdated sites become leads.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, ClassVar

from bs4 import BeautifulSoup

from app.adapters.fetcher import FetchError, RobotsDisallowedError
from app.agents.branding.extract import find_brand_name
from app.agents.discovery.base import RawBusiness, RawLead, SourceContext, industry_keywords
from app.agents.discovery.proactive_triggers.signals import classify_site
from app.db.models import EvidenceKind, TriggerType
from app.services.dedup import PLATFORM_DOMAINS, normalize_domain
from app.services.locale import COUNTRY_DEFAULT_LANGUAGE

logger = logging.getLogger(__name__)

# Small and mid-sized towns: local trades there are far more likely to run dated sites
# than the businesses ranking top in big cities (covers all 32 V2 markets).
CITIES: dict[str, list[str]] = {
    "GB": ["Wakefield", "Barnsley", "Rotherham", "Doncaster", "Huddersfield", "Halifax",
           "Burnley", "Wigan", "Stoke-on-Trent", "Telford", "Grimsby", "Kidderminster"],
    "US": ["Abilene TX", "Lubbock TX", "Joplin MO", "Dothan AL", "Muncie IN", "Casper WY",
           "Erie PA", "Duluth MN", "Yakima WA", "Macon GA", "Pueblo CO", "Bangor ME"],
    "AT": ["Linz", "Klagenfurt", "Villach", "Wels", "Sankt Pölten", "Dornbirn", "Steyr", "Feldkirch", "Bregenz", "Baden"],
    "BE": ["Namur", "Mons", "Aalst", "Mechelen", "Kortrijk", "Hasselt", "Tournai", "Ostend", "Sint-Niklaas", "Verviers"],
    "BG": ["Plovdiv", "Varna", "Burgas", "Ruse", "Stara Zagora", "Pleven", "Sliven", "Dobrich", "Shumen", "Pernik"],
    "HR": ["Split", "Rijeka", "Osijek", "Zadar", "Slavonski Brod", "Pula", "Karlovac", "Varaždin", "Šibenik", "Sisak"],
    "CY": ["Limassol", "Larnaca", "Paphos", "Famagusta", "Kyrenia", "Aradippou", "Paralimni", "Geri", "Ypsonas", "Dali"],
    "CZ": ["Plzeň", "Liberec", "Olomouc", "České Budějovice", "Hradec Králové", "Ústí nad Labem", "Pardubice", "Zlín", "Havířov", "Kladno"],
    "DK": ["Odense", "Aalborg", "Esbjerg", "Randers", "Kolding", "Horsens", "Vejle", "Roskilde", "Herning", "Silkeborg"],
    "EE": ["Tartu", "Narva", "Pärnu", "Kohtla-Järve", "Viljandi", "Rakvere", "Maardu", "Sillamäe", "Kuressaare", "Võru"],
    "FI": ["Tampere", "Oulu", "Turku", "Jyväskylä", "Lahti", "Kuopio", "Pori", "Kouvola", "Joensuu", "Vaasa"],
    "FR": ["Limoges", "Poitiers", "Pau", "Béziers", "Bourges", "Valence", "Lorient", "Quimper", "Troyes", "Tarbes", "Angoulême", "Niort"],
    "DE": ["Bielefeld", "Hagen", "Hamm", "Mülheim", "Herne", "Solingen", "Osnabrück", "Paderborn", "Göttingen", "Recklinghausen", "Bottrop", "Bremerhaven"],
    "GR": ["Patras", "Heraklion", "Larissa", "Volos", "Ioannina", "Trikala", "Chalcis", "Serres", "Alexandroupoli", "Xanthi"],
    "HU": ["Debrecen", "Szeged", "Miskolc", "Pécs", "Győr", "Nyíregyháza", "Kecskemét", "Székesfehérvár", "Szombathely", "Szolnok"],
    "IE": ["Waterford", "Drogheda", "Dundalk", "Bray", "Navan", "Kilkenny", "Ennis", "Carlow", "Tralee", "Athlone"],
    "IT": ["Bergamo", "Brescia", "Vicenza", "Treviso", "Forlì", "Ferrara", "Novara", "Piacenza", "Cremona", "Asti", "Ragusa", "Sassari"],
    "LV": ["Daugavpils", "Liepāja", "Jelgava", "Jūrmala", "Ventspils", "Rēzekne", "Valmiera", "Ogre", "Jēkabpils", "Tukums"],
    "LT": ["Kaunas", "Klaipėda", "Šiauliai", "Panevėžys", "Alytus", "Marijampolė", "Mažeikiai", "Jonava", "Utena", "Kėdainiai"],
    "LU": ["Esch-sur-Alzette", "Differdange", "Dudelange", "Ettelbruck", "Diekirch", "Wiltz", "Echternach", "Remich"],
    "MT": ["Birkirkara", "Qormi", "Mosta", "Żabbar", "San Pawl il-Baħar", "Saint John", "Fgura", "Żejtun", "Sliema", "Naxxar"],
    "NL": ["Apeldoorn", "Arnhem", "Enschede", "Zwolle", "Dordrecht", "Amersfoort", "Leiden", "Deventer", "Alkmaar", "Leeuwarden"],
    "PL": ["Radom", "Toruń", "Sosnowiec", "Kielce", "Gliwice", "Zabrze", "Bielsko-Biała", "Olsztyn", "Rzeszów", "Ruda Śląska"],
    "PT": ["Braga", "Guimarães", "Coimbra", "Setúbal", "Aveiro", "Viseu", "Leiria", "Faro", "Évora", "Portimão"],
    "RO": ["Timișoara", "Cluj-Napoca", "Iași", "Constanța", "Craiova", "Brașov", "Galați", "Ploiești", "Oradea", "Brăila"],
    "SK": ["Košice", "Prešov", "Žilina", "Nitra", "Banská Bystrica", "Trnava", "Martin", "Trenčín", "Poprad", "Prievidza"],
    "SI": ["Maribor", "Celje", "Kranj", "Koper", "Velenje", "Novo Mesto", "Ptuj", "Kamnik", "Trbovlje", "Jesenice"],
    "ES": ["Albacete", "Burgos", "Salamanca", "Logroño", "Badajoz", "Huelva", "Lleida", "Tarragona", "León", "Jaén", "Ourense", "Algeciras"],
    "SE": ["Norrköping", "Jönköping", "Helsingborg", "Lund", "Borås", "Sundsvall", "Gävle", "Halmstad", "Karlstad", "Växjö"],
    "NO": ["Drammen", "Fredrikstad", "Kristiansand", "Sandnes", "Tromsø", "Sarpsborg", "Skien", "Ålesund", "Sandefjord", "Haugesund"],
    "CH": ["Winterthur", "St. Gallen", "Biel", "Schaffhausen", "Fribourg", "Chur", "Neuchâtel", "Uster", "Sion", "Thun"],
    "IS": ["Kópavogur", "Hafnarfjörður", "Reykjanesbær", "Akureyri", "Garðabær", "Mosfellsbær", "Árborg", "Akranes", "Fjarðabyggð", "Ísafjörður"],
}

# Localized search words per language for home_services.
# Norwegian uses "nb" as per project standard.
TRADE_KEYWORDS_BY_LANG: dict[str, list[str]] = {
    "en": ["plumber", "electrician", "roofing", "handyman", "carpenter", "painter decorator", "landscaper", "builder"],
    "de": ["Klempner", "Elektriker", "Dachdecker", "Handwerker", "Schreiner", "Maler", "Gartenbau", "Bauunternehmen"],
    "fr": ["plombier", "électricien", "couvreur", "bricoleur", "menuisier", "peintre en bâtiment", "paysagiste", "maçon"],
    "es": ["fontanero", "electricista", "techador", "manitas", "carpintero", "pintor", "jardinería", "reformas"],
    "it": ["idraulico", "elettricista", "coperturista", "tuttofare", "falegname", "imbianchino", "giardiniere", "impresa edile"],
    "nl": ["loodgieter", "elektricien", "dakdekker", "klusjesman", "timmerman", "schilder", "hovenier", "aannemer"],
    "pt": ["canalizador", "eletricista", "telhador", "biscateiro", "carpinteiro", "pintor", "jardineiro", "construtor"],
    "pl": ["hydraulik", "elektryk", "dekarz", "złota rączka", "stolarz", "malarz", "ogrodnik", "usługi budowlane"],
    "sv": ["rörmokare", "elektriker", "takläggare", "hantverkare", "snickare", "målare", "trädgårdsanläggning", "byggföretag"],
    "da": ["blikkenslager", "elektriker", "tagdækker", "håndværker", "tømrer", "maler", "anlægsgartner", "byggefirma"],
    "nb": ["rørlegger", "elektriker", "taktekker", "handverker", "snekker", "maler", "anleggsgartner", "byggmester"],
    "fi": ["putkimies", "sähköasentaja", "kattoremontti", "remontoija", "puuseppä", "maalari", "pihasuunnittelu", "rakennusliike"],
    "cs": ["instalatér", "elektrikář", "pokrývač", "hodinový manžel", "truhlář", "malíř pokojů", "zahradník", "stavební firma"],
    "ro": ["instalator", "electrician", "acoperișuri", "meșter", "tâmplar", "zugrav", "amenajări grădini", "firmă construcții"],
    "el": ["υδραυλικός", "ηλεκτρολόγος", "σκεπές", "τεχνικός", "ξυλουργός", "ελαιοχρωματιστής", "κηπουρός", "οικοδομικές εργασίες"],
    "hu": ["vízvezeték szerelő", "villanyszerelő", "tetőfedő", "ezermester", "asztalos", "szobafestő", "kertépítés", "kivitelező"],
}
# Directories, marketplaces and aggregators are not a business's own website.
NOT_A_BUSINESS_SITE = PLATFORM_DOMAINS | {
    "checkatrade.com", "trustatrader.com", "mybuilder.com", "ratedpeople.com", "bark.com",
    "angi.com", "angieslist.com", "homeadvisor.com", "thumbtack.com", "houzz.com",
    "houzz.co.uk", "yellowpages.com", "bbb.org", "nextdoor.com", "nextdoor.co.uk",
    "mapquest.com", "tripadvisor.com", "indeed.com", "glassdoor.com", "wikipedia.org",
    "reddit.com", "quora.com", "porch.com", "networx.com", "fixr.com", "threebestrated.co.uk",
    "threebestrated.com", "expertise.com", "freeindex.co.uk", "scoot.co.uk", "thomsonlocal.com",
    "192.com", "cylex-uk.co.uk", "hotfrog.co.uk", "hotfrog.com", "manta.com", "yelp.co.uk",
    "trustpilot.com", "gov.uk", "which.co.uk", "homeserve.com", "airtasker.com", "taskrabbit.com",
}  # fmt: skip


def _business_name(html: str, title: str, domain: str) -> str:
    soup = BeautifulSoup(html or "", "lxml")
    name = (
        find_brand_name(soup, domain)
        or title.split(" - ", maxsplit=1)[0].split(" | ", maxsplit=1)[0]
    )
    return name.strip()[:200]


class LocalSitesSource:
    key: ClassVar[str] = "local_sites"
    label: ClassVar[str] = "Trigger: outdated local business websites (search)"
    description: ClassVar[str] = (
        "Finds local trade businesses' own websites through the search API and flags broken "
        "or outdated ones (errors, no HTTPS, no mobile layout, old design)."
    )
    integration: ClassVar[str] = "serper"
    default_enabled: ClassVar[bool] = True
    default_schedule_minutes: ClassVar[int] = 360
    constraints: ClassVar[str] = (
        "Licensed search API, organic results only (no Maps/Places bulk lists, no scraping of "
        "result pages); one homepage fetch per business via the safe fetcher."
    )
    default_config: ClassVar[dict[str, Any]] = {
        "queries_per_run": 1,
        "max_sites": 10,
        "pages": 2,
        "lighthouse_check": True,
        "min_performance": 40,
        "min_seo": 60,
    }

    @staticmethod
    async def _low_lighthouse(ctx: SourceContext, url: str) -> list[str]:
        if not ctx.config.get("lighthouse_check", True):
            return []
        try:
            result = await ctx.adapters.pagespeed.run(url, "mobile", session=ctx.session)
        except Exception as exc:  # quota reached, API hiccup: just skip the signal
            logger.info("pagespeed check skipped for %s: %s", url, exc)
            return []
        signals = []
        perf, seo = result.scores.get("performance"), result.scores.get("seo")
        if perf is not None and perf < float(ctx.config.get("min_performance", 40)):
            signals.append(f"lighthouse_performance_{perf:g}")
        if seo is not None and seo < float(ctx.config.get("min_seo", 60)):
            signals.append(f"lighthouse_seo_{seo:g}")
        return signals

    def queries(self, ctx: SourceContext) -> list[tuple[str, str, int]]:
        industry = ctx.runtime.active_industry
        countries = [c for c in (ctx.runtime.target_countries or ["GB", "US"]) if c in CITIES]
        pages = range(1, max(1, int(ctx.config.get("pages", 2))) + 1)
        res: list[tuple[str, str, int]] = []
        for page in pages:
            for c in countries:
                lang = COUNTRY_DEFAULT_LANGUAGE.get(c, "en")
                if industry == "home_services":
                    trades = TRADE_KEYWORDS_BY_LANG.get(lang, TRADE_KEYWORDS_BY_LANG["en"])
                else:
                    trades = industry_keywords(industry)[:8]
                for city in CITIES[c]:
                    for t in trades:
                        res.append((f"{t} {city}", c, page))
        return res

    @staticmethod
    def city_of(query: str, country: str) -> str:
        """'plumber Lubbock TX' → 'Lubbock, TX' (the town searched = where the business is)."""
        for city in CITIES.get(country, []):
            if query.endswith(city):
                parts = city.rsplit(" ", 1)
                return f"{parts[0]}, {parts[1]}" if country == "US" and len(parts) == 2 else city
        return ""

    async def fetch(self, ctx: SourceContext) -> list[RawLead]:
        queries = self.queries(ctx)
        if not queries:
            return []
        per_run = int(ctx.config.get("queries_per_run", 1))
        cursor = int(ctx.state.get("cursor", 0)) % len(queries)
        ctx.state["cursor"] = cursor + per_run
        leads: list[RawLead] = []
        seen: set[str] = set()
        for i in range(per_run):
            query, country, page = queries[(cursor + i) % len(queries)]
            results = await ctx.adapters.search.search(
                ctx.session,
                query,
                agent="discovery",
                country=country,
                language="en",
                count=10,
                page=page,
            )
            for r in results[: int(ctx.config.get("max_sites", 10))]:
                domain = normalize_domain(r.url)
                if (
                    not domain
                    or domain in seen
                    or domain in NOT_A_BUSINESS_SITE
                    or any(domain.endswith("." + d) for d in NOT_A_BUSINESS_SITE)
                ):
                    continue
                seen.add(domain)
                url = f"https://{domain}/"
                try:
                    resp = await ctx.adapters.fetcher.get(url)
                    health = classify_site(resp.status_code, resp.text, resp.final_url)
                    html, final_url = resp.text, resp.final_url
                except RobotsDisallowedError:
                    continue  # the site opted out of crawling
                except FetchError:
                    health, html, final_url = classify_site(None, None, None), "", url
                if health.status in ("protected", "unreachable"):
                    continue  # bot-protected (unknown) or possibly our network
                if health.status == "ok":
                    # "Very low Lighthouse": lab data via PageSpeed (cached,
                    # within the free daily cap) for sites that look fine statically.
                    low = await self._low_lighthouse(ctx, final_url)
                    if not low:
                        continue
                    health.status, health.signals = "outdated", [*health.signals, *low]
                name = _business_name(html, r.title, domain)
                if not name:
                    continue
                leads.append(
                    RawLead(
                        source_item_id=f"local:{domain}:{datetime.now(UTC):%Y%m}",
                        source_url=final_url,
                        trigger_type=TriggerType.BROKEN_OR_OUTDATED_SITE,
                        title=f"{name} — website {health.status}",
                        text=(
                            f"{name} ({domain}), found for '{query}': website {health.status}; "
                            f"signals: {', '.join(health.signals)}."
                        ),
                        business=RawBusiness(
                            name=name,
                            website=url,
                            address=self.city_of(query, country) or None,
                            country_hint=country,
                            industry=ctx.runtime.active_industry,
                            has_website=True,
                        ),
                        evidence_kind=EvidenceKind.METRIC,
                        evidence_data={
                            "site_status": health.status,
                            "signals": health.signals,
                            "query": query,
                            "search_result": r.url,
                        },
                    )
                )
        return leads
