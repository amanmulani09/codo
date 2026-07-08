"""Thin GitHub App REST client: auth, diff fetch, and posting review output.

Uses a short-lived App JWT to mint per-installation tokens, then talks to the
REST API with httpx. Installation tokens are cached until shortly before expiry.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
import jwt
import yaml

from .config import Settings
from .schemas import Finding, RepoConfig

API_ROOT = "https://api.github.com"
BOT_MARKER = "<!-- codo-bot -->"

# File paths we never review — lockfiles, generated, vendored, binaries.
_SKIP_SUFFIXES = (
    ".lock",
    ".min.js",
    ".map",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".pdf",
    ".ico",
    ".woff",
    ".woff2",
)
_SKIP_SEGMENTS = ("node_modules/", "dist/", "build/", "vendor/", "__pycache__/")


@dataclass
class ChangedFile:
    path: str
    status: str
    additions: int
    patch: str | None  # unified diff hunk; None for binary / too-large files


class GitHubClient:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._token_cache: dict[int, tuple[str, float]] = {}

    # --- auth -------------------------------------------------------------
    def _app_jwt(self) -> str:
        now = int(time.time())
        payload = {"iat": now - 60, "exp": now + 9 * 60, "iss": self._settings.github_app_id}
        return jwt.encode(payload, self._settings.github_private_key, algorithm="RS256")

    async def _installation_token(self, installation_id: int) -> str:
        cached = self._token_cache.get(installation_id)
        if cached and cached[1] - time.time() > 60:
            return cached[0]
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{API_ROOT}/app/installations/{installation_id}/access_tokens",
                headers={
                    "Authorization": f"Bearer {self._app_jwt()}",
                    "Accept": "application/vnd.github+json",
                },
            )
            resp.raise_for_status()
            data = resp.json()
        expires = datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00"))
        self._token_cache[installation_id] = (data["token"], expires.timestamp())
        return data["token"]

    async def _client(self, installation_id: int) -> httpx.AsyncClient:
        token = await self._installation_token(installation_id)
        return httpx.AsyncClient(
            base_url=API_ROOT,
            timeout=30,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    # --- reads ------------------------------------------------------------
    async def list_changed_files(
        self, installation_id: int, repo: str, pr_number: int
    ) -> list[ChangedFile]:
        files: list[ChangedFile] = []
        async with await self._client(installation_id) as client:
            page = 1
            while True:
                resp = await client.get(
                    f"/repos/{repo}/pulls/{pr_number}/files",
                    params={"per_page": 100, "page": page},
                )
                resp.raise_for_status()
                batch = resp.json()
                if not batch:
                    break
                for f in batch:
                    files.append(
                        ChangedFile(
                            path=f["filename"],
                            status=f["status"],
                            additions=f.get("additions", 0),
                            patch=f.get("patch"),
                        )
                    )
                if len(batch) < 100:
                    break
                page += 1
        return files

    async def get_file(
        self, installation_id: int, repo: str, path: str, ref: str
    ) -> str | None:
        """Fetch a file's raw text at a ref. Returns None if missing/binary."""
        async with await self._client(installation_id) as client:
            resp = await client.get(
                f"/repos/{repo}/contents/{path}",
                params={"ref": ref},
                headers={"Accept": "application/vnd.github.raw"},
            )
        if resp.status_code == 200:
            return resp.text
        return None

    async def get_repo_config(
        self, installation_id: int, repo: str, ref: str
    ) -> RepoConfig:
        raw = await self.get_file(installation_id, repo, ".aicodereview.yaml", ref)
        if not raw:
            return RepoConfig()
        try:
            data = yaml.safe_load(raw) or {}
            review = data.get("review", {}) or {}
            return RepoConfig(
                enabled=data.get("enabled", True),
                tone=data.get("tone", "concise"),
                nits=review.get("nits", False),
                path_filters=review.get("path_filters", []),
                severity_threshold=data.get("severity_threshold", "warning"),
            )
        except Exception:
            return RepoConfig()

    # --- writes -----------------------------------------------------------
    async def cleanup_stale_comments(
        self, installation_id: int, repo: str, pr_number: int
    ) -> None:
        """Delete this bot's inline review comments from prior runs on the PR."""
        async with await self._client(installation_id) as client:
            resp = await client.get(
                f"/repos/{repo}/pulls/{pr_number}/comments", params={"per_page": 100}
            )
            if resp.status_code != 200:
                return
            for c in resp.json():
                if BOT_MARKER in (c.get("body") or ""):
                    await client.delete(f"/repos/{repo}/pulls/comments/{c['id']}")

    async def post_review(
        self,
        installation_id: int,
        repo: str,
        pr_number: int,
        head_sha: str,
        summary: str,
        findings: list[Finding],
    ) -> None:
        """Post a summary + inline comments as a single PR review."""
        comments = [
            {
                "path": f.file,
                "line": f.line,
                "side": "RIGHT",
                "body": _format_comment(f),
            }
            for f in findings
        ]
        body = f"{BOT_MARKER}\n## 🐾 Codo review\n\n{summary}"
        async with await self._client(installation_id) as client:
            resp = await client.post(
                f"/repos/{repo}/pulls/{pr_number}/reviews",
                json={
                    "commit_id": head_sha,
                    "body": body,
                    "event": "COMMENT",
                    "comments": comments,
                },
            )
            # If some lines fall outside the diff, GitHub 422s the whole review.
            # Retry with the summary only so the PR still gets feedback.
            if resp.status_code == 422 and comments:
                await client.post(
                    f"/repos/{repo}/pulls/{pr_number}/reviews",
                    json={"commit_id": head_sha, "body": body, "event": "COMMENT"},
                )

    async def post_check_run(
        self,
        installation_id: int,
        repo: str,
        head_sha: str,
        conclusion: str,
        title: str,
        summary: str,
    ) -> None:
        async with await self._client(installation_id) as client:
            await client.post(
                f"/repos/{repo}/check-runs",
                json={
                    "name": "Codo",
                    "head_sha": head_sha,
                    "status": "completed",
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "conclusion": conclusion,
                    "output": {"title": title, "summary": summary},
                },
            )

    async def post_notice(
        self, installation_id: int, repo: str, pr_number: int, message: str
    ) -> None:
        """Post a plain PR comment (used for gating/upgrade notices)."""
        async with await self._client(installation_id) as client:
            await client.post(
                f"/repos/{repo}/issues/{pr_number}/comments",
                json={"body": f"{BOT_MARKER}\n{message}"},
            )


# --- helpers --------------------------------------------------------------
def should_skip(path: str) -> bool:
    if any(seg in path for seg in _SKIP_SEGMENTS):
        return True
    return path.endswith(_SKIP_SUFFIXES)


_SEVERITY_EMOJI = {"critical": "🔴", "warning": "🟡", "nit": "🔵"}


def _format_comment(f: Finding) -> str:
    header = f"{_SEVERITY_EMOJI.get(f.severity, '🟡')} **{f.severity.title()}** {BOT_MARKER}"
    body = f"{header}\n\n{f.comment}"
    if f.suggestion:
        body += f"\n\n```suggestion\n{f.suggestion}\n```"
    return body
