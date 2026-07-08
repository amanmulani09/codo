"""FastAPI app: GitHub + Stripe webhooks. Verifies signatures, enqueues jobs."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from contextlib import asynccontextmanager

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI, Header, HTTPException, Request

from . import billing
from .config import get_settings
from .schemas import ReviewJob

log = logging.getLogger("codo.web")
settings = get_settings()

_REVIEWABLE_ACTIONS = {"opened", "synchronize", "reopened", "ready_for_review"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    yield
    await app.state.redis.close()


app = FastAPI(title="Codo", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


def _verify_github_signature(body: bytes, signature: str | None) -> bool:
    if not signature or not settings.github_webhook_secret:
        return False
    expected = "sha256=" + hmac.new(
        settings.github_webhook_secret.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


@app.post("/webhook")
async def github_webhook(
    request: Request,
    x_github_event: str = Header(default=""),
    x_hub_signature_256: str | None = Header(default=None),
) -> dict:
    body = await request.body()
    if not _verify_github_signature(body, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="invalid signature")

    payload = json.loads(body)

    # Billing: Marketplace purchase lifecycle.
    if x_github_event == "marketplace_purchase":
        billing.handle_marketplace_event(payload)
        return {"ok": True}

    # Reviews: pull_request events only.
    if x_github_event != "pull_request":
        return {"ok": True, "ignored": x_github_event}

    if payload.get("action") not in _REVIEWABLE_ACTIONS:
        return {"ok": True, "ignored_action": payload.get("action")}

    pr = payload["pull_request"]
    if pr.get("draft"):
        return {"ok": True, "ignored": "draft"}

    repo = payload["repository"]
    account = repo["owner"]["login"]
    job = ReviewJob(
        installation_id=payload["installation"]["id"],
        repo_full_name=repo["full_name"],
        repo_id=repo["id"],
        private=repo.get("private", False),
        pr_number=pr["number"],
        head_sha=pr["head"]["sha"],
        account_login=account,
        action=payload["action"],
    )
    await request.app.state.redis.enqueue_job("review_pr", job.model_dump())
    return {"ok": True, "enqueued": job.pr_number}


@app.post("/stripe")
async def stripe_webhook(
    request: Request, stripe_signature: str | None = Header(default=None)
) -> dict:
    body = await request.body()
    try:
        billing.handle_stripe_event(body, stripe_signature)
    except billing.BillingError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True}
