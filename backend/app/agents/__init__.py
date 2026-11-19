"""Per-lead agents. Importing this package registers them with the orchestrator.

Aliases are private on purpose: binding e.g. `audit` here would shadow the
`app.agents.audit` subpackage attribute with its `agent` module.
"""

from app.agents.audit import agent as _audit_agent
from app.agents.audit import light as _light_audit_agent
from app.agents.branding import agent as _branding_agent
from app.agents.contact_enrichment import agent as _contact_agent
from app.agents.outreach import agent as _outreach_agent
from app.agents.prefilter import agent as _prefilter_agent
from app.agents.proposal import agent as _proposal_agent
from app.agents.requirements import agent as _requirements_agent
from app.agents.website_builder import agent as _website_builder_agent
from app.quality import agent as _quality_agent

REGISTERED_MODULES = (
    _prefilter_agent,
    _requirements_agent,
    _contact_agent,
    _light_audit_agent,
    _audit_agent,
    _branding_agent,
    _website_builder_agent,
    _quality_agent,
    _proposal_agent,
    _outreach_agent,
)
