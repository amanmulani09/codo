# Codo — Design Doc (MVP)

**Status:** Draft v1
**Owner:** @mulaniaman0504
**Last updated:** 2026-07-08

A CodeRabbit-style GitHub App that reviews pull requests with Claude and posts
inline comments back on the PR. This doc covers the MVP scope, architecture,
data model, the review flow, and the monetization design.

---

## 1. Goals & non-goals

### Goals (MVP)
- Install as a **GitHub App** on a repo/org.
- On every PR (opened / new commits), post:
  - a **summary comment** (walkthrough + risk callouts), and
  - **inline review comments** anchored to specific lines.
- **Minimal-agentic** review: single Claude pass, with **one optional tool**
  (`get_file`) capped at a few calls so the model can pull extra context beyond
  the diff. No full tool-loop, no RAG, no vector DB in the MVP.
- **Monetization-ready from day one:** usage metering + plan gating, wired to
  **two** billing paths (Stripe + GitHub Marketplace).
- Cheap to run and easy to deploy (single Railway project).

### Non-goals (MVP — deferred)
- RAG over the codebase / pgvector semantic search (v2).
- Learning team conventions from history (v3 paid tier).
- Multi-file agentic refactors, auto-fix PRs.
- Web IDE, GitLab/Bitbucket support.
- SSO / on-prem / SOC2.

---

## 2. Architecture

```
                 GitHub
                   │  webhook (pull_request: opened, synchronize)
                   ▼
         ┌───────────────────────┐
         │  FastAPI  (web)       │   verify HMAC sig, respond 200 < 1s
         │  POST /webhook        │   enqueue job, return
         │  POST /stripe         │
         │  GET  /health         │
         └──────────┬────────────┘
                    │ enqueue (arq → Redis)
                    ▼
         ┌───────────────────────┐
         │  arq worker           │
         │  review_pr(job)       │
         └──────────┬────────────┘
                    │
     ┌──────────────┼───────────────────────────┐
     ▼              ▼                             ▼
 GitHub API     Anthropic API                 Postgres
 (gidgethub)    (Claude, tool-use)            (installs, plans, usage)
     │              │
     │   ┌──────────┴───────────────────────┐
     │   │  Review agent loop (capped)      │
     │   │  tools: get_file (≤3),           │
     │   │         submit_review (required) │
     │   └──────────┬───────────────────────┘
     ▼              ▼
 fetch diff    post summary + inline comments + Check Run
```

### Why these choices
- **FastAPI + gidgethub + arq**: async end-to-end. `gidgethub` handles webhook
  signature verification and the GitHub App JWT → installation-token flow.
  `arq` is a lightweight async Redis queue — reviews must be async so the
  webhook can return `200` fast (GitHub times out at ~10s).
- **Anthropic SDK, native tool-use**: structured output via a `submit_review`
  tool schema (no fragile JSON parsing). No LangChain — the agent loop is ~40
  lines and we want direct control over prompts (our moat) and caching.
- **Postgres + SQLModel**: one place for installs, plans, and usage counters.
- **Two billing paths** write to the same `plan` record, so gating logic is
  billing-agnostic.

---

## 3. Review flow (the core loop)

1. Webhook `pull_request` (`opened` | `synchronize` | `reopened`) arrives.
2. Verify HMAC. Enqueue `review_pr(installation_id, repo, pr_number, head_sha)`.
   Return `200`.
3. Worker resolves the **installation token** (App JWT → token, cached).
4. **Gating check** (see §5): plan lookup + usage limit. If blocked, post a
   short "upgrade" comment and stop.
5. Fetch PR metadata + changed files (`pulls.list_files`). Apply **path
   filters** (skip lockfiles, generated, vendored, binary, oversized).
6. For `synchronize`, only review the **incremental diff** since last reviewed
   SHA (stored per PR).
7. Build the prompt: PR title/description + filtered diff hunks + repo config.
8. Run the **capped agent loop**:
   - Call Claude with tools `[get_file, submit_review]`.
   - If it returns `tool_use: get_file`, read that file via GitHub API, return
     `tool_result`, loop. **Hard cap: 3 `get_file` calls / 6 iterations.**
   - When it returns `tool_use: submit_review`, stop.
9. Validate findings (pydantic). Drop findings whose line isn't in the diff
   (GitHub rejects inline comments off the diff).
10. Post: **summary** comment, **inline** review comments, and a **Check Run**
    (`neutral`/`success`) so the review shows as a PR status.
11. Clean up **stale** bot comments from prior runs on this PR. Record usage +
    `last_reviewed_sha`.

### Prompt / quality (the moat)
- System prompt: "senior reviewer, flag real correctness/security/perf issues,
  suppress style nitpicks unless config asks." Severity rubric:
  `critical | warning | nit`.
- **Prompt caching** on the system prompt + repo config to keep multi-turn
  cheap.
- Model: **Claude Sonnet 5** for the loop. (Opus verification pass reserved for
  a future paid tier.)

---

## 4. Data model

```
Installation
  id (gh installation id, PK)   account_login   account_type(org|user)
  created_at

Repo
  id (gh repo id, PK)   installation_id (FK)   full_name   private(bool)
  config_json (parsed .aicodereview.yaml, nullable)

Plan
  account_login (PK)    source(stripe|marketplace|free)   tier(free|pro|team)
  status(active|canceled|past_due)   limit_per_month(int)   seats(int)
  stripe_customer_id (nullable)   external_id (nullable)   updated_at

UsageCounter
  account_login + year_month (PK)    reviews_count(int)

PullRequestState
  repo_id + pr_number (PK)    last_reviewed_sha    bot_comment_ids(json)
```

Gating reads `Plan` + `UsageCounter`. Both billing webhooks upsert `Plan`.

---

## 5. Monetization

Two revenue paths, one gating path. Offering both maximizes conversion:
Marketplace captures GitHub-native discovery; Stripe gives control + margin +
trials/coupons/annual.

### Path A — GitHub Marketplace
- Listed plans; GitHub handles billing/tax/renewals + **discovery**.
- Handle `marketplace_purchase` webhook (`purchased|changed|cancelled`) → upsert
  `Plan(source=marketplace)`.

### Path B — Stripe
- Checkout for subscribe/upgrade; `customer.subscription.*` + `checkout.session.
  completed` webhooks → upsert `Plan(source=stripe)`.
- Flat subscription tiers for MVP. Usage-based add-on later.

### Tiers (start with ONE paid tier)
| Tier | Price | Limit | Notes |
|------|-------|-------|-------|
| Free / OSS | $0 | public repos or 1 private, N reviews/mo | growth engine |
| **Pro** | ~$15/dev/mo (or ~$25/repo/mo) | unlimited private | config file, priority |
| Team (later) | higher | unlimited | Opus verify pass, org controls |

### Gating logic
```
plan = lookup_plan(account_login)              # default: free
if repo.private and plan.tier == free:  block("upgrade to review private repos")
if usage_this_month(account) >= plan.limit_per_month:  block("monthly limit reached")
else: run review; increment usage
```

---

## 6. Config file (`.aicodereview.yaml`, repo root)

```yaml
enabled: true
tone: concise            # concise | detailed
review:
  nits: false            # suppress style nitpicks
  path_filters:          # extra globs to ignore
    - "**/*.lock"
    - "dist/**"
severity_threshold: warning   # only post >= this severity
```
Validated with pydantic; missing file → sane defaults.

---

## 7. Deployment

- **Railway** project: `web` (FastAPI/uvicorn) + `worker` (arq) + Redis +
  Postgres.
- Secrets (env): `GITHUB_APP_ID`, `GITHUB_PRIVATE_KEY`, `GITHUB_WEBHOOK_SECRET`,
  `ANTHROPIC_API_KEY`, `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`,
  `DATABASE_URL`, `REDIS_URL`.
- Landing page + install button + usage dashboard: Next.js on Vercel (later).
- **Cost:** ~$5–25/mo infra. Per review: single-shot ~$0.02–0.08; with a couple
  `get_file` calls ~$0.05–0.15. Price above this → healthy margin.

---

## 8. Security & safety
- Verify every webhook HMAC (`X-Hub-Signature-256`). Reject on mismatch.
- Installation tokens are short-lived; never log secrets or full file contents.
- Cap tool iterations + total tokens per PR (runaway-cost backstop).
- Never review binary/oversized files; enforce a max total diff size.
- Idempotency: skip if `head_sha == last_reviewed_sha`.

---

## 9. Build order (~3 weeks)
- **Week 1 — core loop:** webhook → auth → fetch diff → Claude tool-use →
  post comments + Check Run. *(This repo starts here.)*
- **Week 2 — quality/safety:** path filters, incremental review, stale cleanup,
  rubric tuning, config file, retry/backoff, prompt caching.
- **Week 3 — monetize/launch:** usage metering, Stripe + Marketplace webhooks,
  gating, landing page, Marketplace listing, Sentry.

---

## 10. Repo layout
```
codo/
  DESIGN.md
  README.md
  pyproject.toml
  .env.example
  app/
    __init__.py
    config.py          # settings via pydantic-settings
    main.py            # FastAPI app, routes
    schemas.py         # pydantic models (Finding, ReviewResult, RepoConfig)
    github_client.py   # App JWT → token, fetch diff, post comments, check run
    reviewer.py        # prompt build + capped agent loop (get_file/submit_review)
    worker.py          # arq settings + review_pr task
    gating.py          # plan lookup + usage limits
    billing.py         # stripe + marketplace webhook handlers
    db.py / models.py  # SQLModel tables
  tests/
```
```
```
