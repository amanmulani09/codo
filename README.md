# Codo

A CodeRabbit-style GitHub App that reviews pull requests with Claude and posts
inline review comments back on the PR. Minimal-agentic, monetization-ready.

See [DESIGN.md](DESIGN.md) for the full architecture, data model, and roadmap.

## How it works

```
PR opened / new commits
   → GitHub webhook  → FastAPI (verify HMAC, enqueue)  → arq worker
     → gating (plan + usage)
     → fetch diff + repo config
     → Claude review loop (get_file ≤3, submit_review)
     → post summary + inline comments + Check Run
```

The review is a single Claude pass (Sonnet 5) with **one optional tool**
(`get_file`, hard-capped) so it can pull extra context beyond the diff, then it
must call `submit_review` to return structured findings.

## Prerequisites

- Python 3.12+, [`uv`](https://docs.astral.sh/uv/)
- Redis (local: `redis-server`, or Upstash/Railway in prod)
- A **GitHub App** (see below)
- An **Anthropic API key**

## Setup

```bash
uv sync                       # create venv + install deps
cp .env.example .env          # then fill in the values
```

### Create the GitHub App

1. GitHub → Settings → Developer settings → **GitHub Apps** → New GitHub App.
2. Permissions: **Pull requests** (Read & write), **Contents** (Read),
   **Checks** (Read & write).
3. Subscribe to events: **Pull request**.
4. Set the **Webhook URL** to `https://<your-host>/webhook` and a
   **Webhook secret** (put the same value in `.env`).
5. Generate a **private key** (.pem) and point `GITHUB_PRIVATE_KEY_PATH` at it
   (or paste the contents into `GITHUB_PRIVATE_KEY`).
6. Note the **App ID** → `GITHUB_APP_ID`.
7. Install the App on a test repo.

## Run

Two processes — the web server and the worker:

```bash
# terminal 1 — webhook receiver
uv run uvicorn app.main:app --reload --port 8000

# terminal 2 — review worker
uv run arq app.worker.WorkerSettings
```

For local testing, expose port 8000 with a tunnel (e.g. `ngrok http 8000`) and
use that URL as the App's webhook URL. Open a PR on the installed repo and watch
the worker log; the review appears on the PR.

## Repo config (optional)

Drop `.aicodereview.yaml` in a repo root:

```yaml
enabled: true
tone: concise            # concise | detailed
review:
  nits: false
  path_filters:
    - "dist/**"
severity_threshold: warning   # only post >= this severity
```

## Monetization

Two revenue paths write to the same plan store (`app/gating.py`):

- **GitHub Marketplace** — `marketplace_purchase` webhooks → `app/billing.py`.
- **Stripe** — `customer.subscription.*` / `checkout.session.completed` → `/stripe`.

Gating (`check_and_gate`) is billing-agnostic: public/OSS repos are free, private
repos and volume require a paid tier. The MVP uses an in-memory plan/usage store;
swap it for Postgres for production (see DESIGN.md §4).

## Tests

```bash
uv run pytest
```

## Layout

| File | Purpose |
|------|---------|
| `app/main.py` | FastAPI webhooks (GitHub + Stripe), signature verify, enqueue |
| `app/worker.py` | arq worker — the end-to-end review flow |
| `app/reviewer.py` | Prompt + capped `get_file`/`submit_review` agent loop |
| `app/github_client.py` | App auth, diff fetch, posting comments + Check Run |
| `app/gating.py` | Plan lookup + usage metering |
| `app/billing.py` | Marketplace + Stripe webhook handlers |
| `app/schemas.py` | Pydantic models (Finding, RepoConfig, ReviewJob) |
| `app/config.py` | Settings via pydantic-settings |
