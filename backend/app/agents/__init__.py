"""Per-lead agents. Importing this package registers them with the orchestrator.

Aliases are private on purpose: binding e.g. `audit` here would shadow the
`app.agents.audit` subpackage attribute with its `agent` module.
"""

from app.agents.audit import agent as _audit_agent
from app.agents.contact_enrichment import agent as _contact_agent
from app.agents.requirements import agent as _requirements_agent

REGISTERED_MODULES = (_requirements_agent, _contact_agent, _audit_agent)
