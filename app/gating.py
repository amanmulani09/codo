"""Plan lookup + usage metering.

MVP uses an in-memory store so the core loop runs without a database. The
interface (`lookup_plan`, `usage_this_month`, `record_review`) is what you swap
for Postgres in week 3 — the billing webhooks upsert into `PLANS`, and the
gating decision in `check_and_gate` stays unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

Tier = Literal["free", "pro", "team"]

_LIMITS: dict[Tier, int] = {"free": 20, "pro": 10_000, "team": 100_000}


@dataclass
class Plan:
    account_login: str
    tier: Tier = "free"
    status: str = "active"

    @property
    def limit(self) -> int:
        return _LIMITS[self.tier]


# In-memory stores. Replace with DB-backed lookups in production.
PLANS: dict[str, Plan] = {}
_USAGE: dict[tuple[str, str], int] = {}


def _month_key() -> str:
    now = datetime.now(timezone.utc)
    return f"{now.year:04d}-{now.month:02d}"


def lookup_plan(account_login: str) -> Plan:
    return PLANS.get(account_login, Plan(account_login=account_login, tier="free"))


def upsert_plan(account_login: str, tier: Tier, status: str = "active") -> None:
    PLANS[account_login] = Plan(account_login=account_login, tier=tier, status=status)


def usage_this_month(account_login: str) -> int:
    return _USAGE.get((account_login, _month_key()), 0)


def record_review(account_login: str) -> None:
    key = (account_login, _month_key())
    _USAGE[key] = _USAGE.get(key, 0) + 1


@dataclass
class GateDecision:
    allowed: bool
    reason: str | None = None


def check_and_gate(account_login: str, private: bool) -> GateDecision:
    """Decide whether to run a review. Same logic regardless of billing source."""
    plan = lookup_plan(account_login)

    if private and plan.tier == "free":
        return GateDecision(
            allowed=False,
            reason=(
                "🔒 Reviewing **private** repositories requires a paid plan. "
                "Upgrade to Pro to enable reviews on this repo."
            ),
        )

    if usage_this_month(account_login) >= plan.limit:
        return GateDecision(
            allowed=False,
            reason=(
                f"📊 You've hit your monthly review limit ({plan.limit}) on the "
                f"**{plan.tier}** plan. Upgrade for more."
            ),
        )

    return GateDecision(allowed=True)
