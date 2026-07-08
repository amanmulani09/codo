"""Pydantic models: review findings, repo config, and the job payload."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["critical", "warning", "nit"]


class Finding(BaseModel):
    """A single review comment produced by the model."""

    file: str = Field(description="Repo-relative path of the changed file")
    line: int = Field(description="1-indexed line in the file's new version to anchor the comment to")
    severity: Severity = "warning"
    comment: str = Field(description="What the issue is and why it matters")
    suggestion: str | None = Field(
        default=None, description="Optional concrete fix, as a code snippet or one-liner"
    )


class ReviewResult(BaseModel):
    """The full structured output of a review pass."""

    summary: str = Field(description="A short PR-level walkthrough and risk callout")
    findings: list[Finding] = Field(default_factory=list)


class RepoConfig(BaseModel):
    """Parsed `.aicodereview.yaml`. Missing file → these defaults."""

    enabled: bool = True
    tone: Literal["concise", "detailed"] = "concise"
    nits: bool = False
    path_filters: list[str] = Field(default_factory=list)
    severity_threshold: Severity = "warning"


class ReviewJob(BaseModel):
    """Payload enqueued by the webhook and consumed by the worker."""

    installation_id: int
    repo_full_name: str
    repo_id: int
    private: bool
    pr_number: int
    head_sha: str
    account_login: str
    action: str
