"""arq worker: consumes review jobs and runs the end-to-end review flow."""

from __future__ import annotations

import logging

from arq.connections import RedisSettings

from .config import get_settings
from .gating import check_and_gate, record_review
from .github_client import GitHubClient
from .reviewer import Reviewer
from .schemas import ReviewJob

log = logging.getLogger("codo.worker")


async def review_pr(ctx: dict, job_data: dict) -> str:
    """Entrypoint for a single PR review. `job_data` is a serialized ReviewJob."""
    settings = get_settings()
    job = ReviewJob(**job_data)
    gh: GitHubClient = ctx["github"]
    reviewer: Reviewer = ctx["reviewer"]

    # 1. Gating — plan + usage.
    decision = check_and_gate(job.account_login, job.private)
    if not decision.allowed:
        await gh.post_notice(job.installation_id, job.repo_full_name, job.pr_number, decision.reason)
        return f"gated: {decision.reason}"

    # 2. Repo config + changed files.
    config = await gh.get_repo_config(job.installation_id, job.repo_full_name, job.head_sha)
    if not config.enabled:
        return "disabled by repo config"

    files = await gh.list_changed_files(job.installation_id, job.repo_full_name, job.pr_number)
    total_bytes = sum(len(f.patch or "") for f in files)
    if total_bytes > settings.max_diff_bytes:
        files = [f for f in files if (len(f.patch or "") < 20_000)]  # drop the biggest hunks

    # 3. Run the review agent.
    async def fetch_file(path: str):
        return await gh.get_file(job.installation_id, job.repo_full_name, path, job.head_sha)

    result = await reviewer.review(
        title=f"PR #{job.pr_number}",
        description="",
        files=files,
        config=config,
        fetch_file=fetch_file,
    )

    # Apply the severity threshold from repo config.
    order = {"nit": 0, "warning": 1, "critical": 2}
    threshold = order[config.severity_threshold]
    findings = [f for f in result.findings if order[f.severity] >= threshold]

    # 4. Post results.
    await gh.cleanup_stale_comments(job.installation_id, job.repo_full_name, job.pr_number)
    await gh.post_review(
        job.installation_id,
        job.repo_full_name,
        job.pr_number,
        job.head_sha,
        result.summary,
        findings,
    )
    conclusion = "neutral" if findings else "success"
    title = f"{len(findings)} finding(s)" if findings else "No issues found"
    await gh.post_check_run(
        job.installation_id, job.repo_full_name, job.head_sha, conclusion, title, result.summary
    )

    # 5. Meter usage.
    record_review(job.account_login)
    return f"reviewed: {len(findings)} findings"


async def startup(ctx: dict) -> None:
    settings = get_settings()
    ctx["github"] = GitHubClient(settings)
    ctx["reviewer"] = Reviewer(settings)
    log.info("worker started")


class WorkerSettings:
    functions = [review_pr]
    on_startup = startup
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
