"""Business registries → newly registered businesses (trigger: new_business).

- Companies House (UK): free API key, advanced company search by incorporation date + SIC.
- OpenCorporates (US states and more): API token; confirm licence terms for commercial use.
- Brønnøysund (Norway): open data, no key.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any, ClassVar

import httpx

from app.agents.discovery.base import RawBusiness, RawLead, SourceContext, industry_keywords
from app.db.models import EvidenceKind, TriggerType

# UK SIC 2007 codes for home services / trades
UK_SIC_HOME_SERVICES = [
    "43210",
    "43220",
    "43290",
    "43310",
    "43320",
    "43330",
    "43341",
    "43342",
    "43390",
    "43910",
    "43999",
    "81210",
    "81221",
    "81229",
    "81291",
    "81300",
    "80200",
    "95220",
]
# Norwegian NACE codes for the same trades
NO_NACE_HOME_SERVICES = ["43.210", "43.220", "43.310", "43.320", "43.341", "43.910", "81.210"]


def _since(ctx: SourceContext, default_days: int) -> date:
    raw = ctx.state.get("since")
    if raw:
        return date.fromisoformat(raw)
    return (datetime.now(UTC) - timedelta(days=default_days)).date()


class CompaniesHouseSource:
    key: ClassVar[str] = "companies_house"
    label: ClassVar[str] = "Companies House (UK)"
    description: ClassVar[str] = "Newly incorporated UK companies in the active industry."
    integration: ClassVar[str] = "companies_house"
    default_enabled: ClassVar[bool] = True
    default_schedule_minutes: ClassVar[int] = 360
    constraints: ClassVar[str] = "Official API; data used under Companies House terms."
    default_config: ClassVar[dict[str, Any]] = {"lookback_days": 7, "max_results": 100}
    ENDPOINT = "https://api.company-information.service.gov.uk/advanced-search/companies"

    async def fetch(self, ctx: SourceContext) -> list[RawLead]:
        key = ctx.settings.companies_house_api_key
        if key is None:
            return []
        since = _since(ctx, int(ctx.config.get("lookback_days", 7)))
        today = datetime.now(UTC).date()
        params = {
            "incorporated_from": since.isoformat(),
            "incorporated_to": today.isoformat(),
            "sic_codes": ",".join(UK_SIC_HOME_SERVICES),
            "company_status": "active",
            "size": str(ctx.config.get("max_results", 100)),
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(self.ENDPOINT, params=params, auth=(key.get_secret_value(), ""))
        if resp.status_code == 404:  # no results
            return []
        resp.raise_for_status()
        ctx.state["since"] = today.isoformat()
        leads = []
        for item in resp.json().get("items", []):
            number = item.get("company_number")
            name = item.get("company_name")
            if not number or not name:
                continue
            addr = item.get("registered_office_address", {}) or {}
            address = ", ".join(
                str(addr[k]) for k in ("address_line_1", "locality", "postal_code") if addr.get(k)
            )
            url = f"https://find-and-update.company-information.service.gov.uk/company/{number}"
            leads.append(
                RawLead(
                    source_item_id=f"ch:{number}",
                    source_url=url,
                    trigger_type=TriggerType.NEW_BUSINESS,
                    title=f"New company: {name}",
                    text=f"{name} incorporated {item.get('date_of_creation')} "
                    f"(SIC {', '.join(item.get('sic_codes', []))}). {address}",
                    business=RawBusiness(
                        name=name,
                        address=address or None,
                        country_hint="GB",
                        registry_number=number,
                        registry_source="companies_house",
                        industry=ctx.runtime.active_industry,
                    ),
                    evidence_kind=EvidenceKind.REGISTRY_RECORD,
                    evidence_data={"registry": "companies_house", "record": item},
                )
            )
        return leads


class OpenCorporatesSource:
    key: ClassVar[str] = "opencorporates"
    label: ClassVar[str] = "OpenCorporates (US states)"
    description: ClassVar[str] = "Newly registered US companies matching industry keywords."
    integration: ClassVar[str] = "opencorporates"
    default_enabled: ClassVar[bool] = False
    default_schedule_minutes: ClassVar[int] = 720
    constraints: ClassVar[str] = "Requires API token; confirm licence terms for commercial use."
    default_config: ClassVar[dict[str, Any]] = {
        "jurisdictions": ["us_tx", "us_fl", "us_ca", "us_ny"],
        "lookback_days": 7,
        "keywords_per_run": 3,
    }
    ENDPOINT = "https://api.opencorporates.com/v0.4/companies/search"

    async def fetch(self, ctx: SourceContext) -> list[RawLead]:
        token = ctx.settings.opencorporates_api_key
        if token is None:
            return []
        since = _since(ctx, int(ctx.config.get("lookback_days", 7)))
        kws = industry_keywords(ctx.runtime.active_industry)
        per_run = int(ctx.config.get("keywords_per_run", 3))
        cursor = int(ctx.state.get("cursor", 0))
        batch = [kws[(cursor + i) % len(kws)] for i in range(per_run)]
        ctx.state["cursor"] = cursor + per_run
        leads: list[RawLead] = []
        async with httpx.AsyncClient(timeout=30) as client:
            for jurisdiction in ctx.config.get("jurisdictions", []):
                for kw in batch:
                    resp = await client.get(
                        self.ENDPOINT,
                        params={
                            "q": kw,
                            "jurisdiction_code": jurisdiction,
                            "per_page": "50",
                            "incorporation_date": f"{since.isoformat()}:",
                            "inactive": "false",
                            "api_token": token.get_secret_value(),
                        },
                    )
                    resp.raise_for_status()
                    for wrapper in resp.json().get("results", {}).get("companies", []):
                        c = wrapper.get("company", {})
                        if not c.get("company_number") or not c.get("name"):
                            continue
                        leads.append(
                            RawLead(
                                source_item_id=f"oc:{c['jurisdiction_code']}:{c['company_number']}",
                                source_url=c.get("opencorporates_url") or self.ENDPOINT,
                                trigger_type=TriggerType.NEW_BUSINESS,
                                title=f"New company: {c['name']}",
                                text=f"{c['name']} incorporated {c.get('incorporation_date')} in "
                                f"{c['jurisdiction_code']}. "
                                f"{c.get('registered_address_in_full') or ''}",
                                business=RawBusiness(
                                    name=c["name"],
                                    address=c.get("registered_address_in_full"),
                                    country_hint="US",
                                    registry_number=c["company_number"],
                                    registry_source=f"opencorporates:{c['jurisdiction_code']}",
                                    industry=ctx.runtime.active_industry,
                                ),
                                evidence_kind=EvidenceKind.REGISTRY_RECORD,
                                evidence_data={"registry": "opencorporates", "record": c},
                            )
                        )
        ctx.state["since"] = datetime.now(UTC).date().isoformat()
        return leads


class BrregSource:
    key: ClassVar[str] = "brreg"
    label: ClassVar[str] = "Brønnøysund Register (Norway)"
    description: ClassVar[str] = "Newly registered Norwegian businesses (open data)."
    integration: ClassVar[str] = "brreg"
    default_enabled: ClassVar[bool] = False  # NO is not a target country yet
    default_schedule_minutes: ClassVar[int] = 720
    constraints: ClassVar[str] = "Open data (NLOD licence)."
    default_config: ClassVar[dict[str, Any]] = {"lookback_days": 7}
    ENDPOINT = "https://data.brreg.no/enhetsregisteret/api/enheter"

    async def fetch(self, ctx: SourceContext) -> list[RawLead]:
        since = _since(ctx, int(ctx.config.get("lookback_days", 7)))
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                self.ENDPOINT,
                params={
                    "fraRegistreringsdatoEnhetsregisteret": since.isoformat(),
                    "naeringskode": ",".join(NO_NACE_HOME_SERVICES),
                    "size": "100",
                },
            )
        resp.raise_for_status()
        ctx.state["since"] = datetime.now(UTC).date().isoformat()
        leads = []
        for e in resp.json().get("_embedded", {}).get("enheter", []):
            number, name = e.get("organisasjonsnummer"), e.get("navn")
            if not number or not name:
                continue
            addr = e.get("forretningsadresse") or {}
            address = ", ".join(
                filter(
                    None,
                    [*(addr.get("adresse") or []), addr.get("postnummer"), addr.get("poststed")],
                )
            )
            website = e.get("hjemmeside")
            leads.append(
                RawLead(
                    source_item_id=f"brreg:{number}",
                    source_url=f"https://virksomhet.brreg.no/oppslag/enheter/{number}",
                    trigger_type=TriggerType.NEW_BUSINESS,
                    title=f"Ny virksomhet: {name}",
                    text=f"{name} registrert {e.get('registreringsdatoEnhetsregisteret')}. {address}",
                    business=RawBusiness(
                        name=name,
                        address=address or None,
                        country_hint="NO",
                        website=f"https://{website}"
                        if website and "://" not in website
                        else website,
                        registry_number=number,
                        registry_source="brreg",
                        industry=ctx.runtime.active_industry,
                        has_website=bool(website),
                    ),
                    evidence_kind=EvidenceKind.REGISTRY_RECORD,
                    evidence_data={"registry": "brreg", "record": e},
                    language_hint="nb",
                )
            )
        return leads
