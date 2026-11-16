"""Orchestrator error types shared by agents and adapters (no imports: avoids cycles)."""


class TransientAgentError(Exception):
    """Raise (or wrap) for failures worth retrying with backoff (network, 5xx, timeouts)."""
