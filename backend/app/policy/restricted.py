"""Restricted actions: never automated at any autonomy stage."""

from __future__ import annotations

from enum import StrEnum


class RestrictedAction(StrEnum):
    SIGN_CONTRACT = "sign_contract"
    ACCEPT_PAYMENT = "accept_payment"
    CHANGE_CLIENT_LIVE_SITE = "change_client_live_site"
    BUY_DOMAIN_OR_SERVICE = "buy_domain_or_service"
    PUBLISH_FINAL_DELIVERABLE = "publish_final_deliverable"


class RestrictedActionError(PermissionError):
    pass


def is_restricted(action: str) -> bool:
    return action in {a.value for a in RestrictedAction}


def forbid_automation(action: str) -> None:
    """Called by any automated code path before an action. Always raises for restricted ones."""
    if is_restricted(action):
        raise RestrictedActionError(
            f"'{action}' is a restricted action and can only be done manually by a human"
        )
