"""Billing webhooks. Two paths, one gating store.

Both GitHub Marketplace and Stripe events resolve to `gating.upsert_plan`, so
the review flow's gating logic never has to know which one paid.
"""

from __future__ import annotations

import logging

from .config import get_settings
from .gating import Tier, upsert_plan

log = logging.getLogger("codo.billing")


class BillingError(Exception):
    pass


# Map a Marketplace plan name (lowercased) to a tier. Adjust to your listing.
_MARKETPLACE_TIERS: dict[str, Tier] = {"free": "free", "pro": "pro", "team": "team"}


def handle_marketplace_event(payload: dict) -> None:
    action = payload.get("action")  # purchased | changed | cancelled | pending_change
    purchase = payload.get("marketplace_purchase", {})
    account = purchase.get("account", {}).get("login")
    if not account:
        return

    if action == "cancelled":
        upsert_plan(account, "free", status="canceled")
        log.info("marketplace cancel: %s -> free", account)
        return

    plan_name = (purchase.get("plan", {}).get("name") or "free").lower()
    tier = _MARKETPLACE_TIERS.get(plan_name, "free")
    upsert_plan(account, tier, status="active")
    log.info("marketplace %s: %s -> %s", action, account, tier)


# Map a Stripe price/product lookup key to a tier. Configure in your Stripe dashboard.
_STRIPE_TIERS: dict[str, Tier] = {"pro": "pro", "team": "team"}


def handle_stripe_event(body: bytes, signature: str | None) -> None:
    settings = get_settings()
    if not settings.stripe_secret_key or not settings.stripe_webhook_secret:
        raise BillingError("stripe not configured")

    import stripe  # imported lazily so the app runs without stripe configured

    stripe.api_key = settings.stripe_secret_key
    try:
        event = stripe.Webhook.construct_event(
            body, signature, settings.stripe_webhook_secret
        )
    except Exception as e:  # signature / parse failure
        raise BillingError(f"invalid stripe signature: {e}") from e

    etype = event["type"]
    obj = event["data"]["object"]

    # We expect the GitHub org login stored in metadata at checkout time.
    account = (obj.get("metadata") or {}).get("github_account")
    if not account:
        return

    if etype in ("checkout.session.completed", "customer.subscription.updated"):
        lookup = (obj.get("metadata") or {}).get("tier", "pro").lower()
        upsert_plan(account, _STRIPE_TIERS.get(lookup, "pro"), status="active")
        log.info("stripe %s: %s -> %s", etype, account, lookup)
    elif etype in ("customer.subscription.deleted",):
        upsert_plan(account, "free", status="canceled")
        log.info("stripe cancel: %s -> free", account)
