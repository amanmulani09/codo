"""The review agent: builds the prompt and runs a capped tool-use loop.

Minimal-agentic by design — a single Claude pass that may pull a few extra
files for context via the `get_file` tool (hard-capped), then must call
`submit_review` to return structured findings.
"""

from __future__ import annotations

from typing import Awaitable, Callable

import anthropic

from .config import Settings
from .github_client import ChangedFile, should_skip
from .schemas import Finding, RepoConfig, ReviewResult

# Callable the loop uses to satisfy `get_file` tool calls: (path) -> text | None
FileFetcher = Callable[[str], Awaitable[str | None]]

SYSTEM_PROMPT = """You are a senior software engineer reviewing a GitHub pull request.

Review the diff for real problems: correctness bugs, security issues, resource \
leaks, race conditions, broken error handling, and clear performance traps. \
Prefer a handful of high-signal findings over exhaustive nitpicking. Do NOT flag \
pure style or naming unless the repo config asks for nits.

You may call `get_file` (up to a few times) to read a full file when the diff \
hunk alone is not enough to judge a change — e.g. to see a function you're \
calling or a caller you might break. Only anchor inline comments to lines that \
appear in the provided diff.

When done, call `submit_review` exactly once with a short PR-level summary and \
your findings. If the PR looks clean, submit an empty findings list with a \
one-line summary."""

TOOLS = [
    {
        "name": "get_file",
        "description": "Read the full current contents of a file in the repo for extra context.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Repo-relative file path"}
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "submit_review",
        "description": "Submit the final review. Call this exactly once when finished.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "Short PR-level walkthrough + risks"},
                "findings": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "file": {"type": "string"},
                            "line": {"type": "integer"},
                            "severity": {"type": "string", "enum": ["critical", "warning", "nit"]},
                            "comment": {"type": "string"},
                            "suggestion": {"type": "string"},
                        },
                        "required": ["file", "line", "severity", "comment"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["summary", "findings"],
            "additionalProperties": False,
        },
    },
]


def build_user_prompt(
    title: str, description: str, files: list[ChangedFile], config: RepoConfig
) -> str:
    parts = [f"# Pull request: {title}", ""]
    if description:
        parts += [description.strip(), ""]
    parts.append(f"Tone: {config.tone}. Report nits: {config.nits}.")
    parts.append("\n# Changed files (unified diff hunks)\n")
    for f in files:
        if f.patch is None:
            parts.append(f"## {f.path} ({f.status}) — patch omitted (binary or too large)\n")
            continue
        parts.append(f"## {f.path} ({f.status})\n```diff\n{f.patch}\n```\n")
    return "\n".join(parts)


class Reviewer:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    async def review(
        self,
        title: str,
        description: str,
        files: list[ChangedFile],
        config: RepoConfig,
        fetch_file: FileFetcher,
    ) -> ReviewResult:
        # Only send reviewable files to the model.
        review_files = [f for f in files if not should_skip(f.path)][: self._settings.max_files]
        if not review_files:
            return ReviewResult(summary="No reviewable code changes in this PR.", findings=[])

        messages: list[dict] = [
            {"role": "user", "content": build_user_prompt(title, description, review_files, config)}
        ]
        tool_calls_used = 0

        for _ in range(self._settings.max_agent_iterations):
            allow_get_file = tool_calls_used < self._settings.max_tool_calls
            tools = TOOLS if allow_get_file else [TOOLS[1]]  # drop get_file once capped

            resp = await self._client.messages.create(
                model=self._settings.review_model,
                max_tokens=8000,
                system=SYSTEM_PROMPT,
                tools=tools,
                messages=messages,
            )
            messages.append({"role": "assistant", "content": resp.content})

            tool_uses = [b for b in resp.content if b.type == "tool_use"]
            if not tool_uses:
                # Model stopped without submitting — nudge it once more.
                messages.append(
                    {"role": "user", "content": "Please call submit_review now with your findings."}
                )
                continue

            submitted = _find_submit(tool_uses)
            if submitted is not None:
                return _parse_result(submitted.input)

            # Otherwise satisfy get_file calls and loop.
            results = []
            for tu in tool_uses:
                if tu.name == "get_file":
                    tool_calls_used += 1
                    content = await fetch_file(tu.input.get("path", ""))
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tu.id,
                            "content": (content or "File not found or not readable.")[:20000],
                        }
                    )
            messages.append({"role": "user", "content": results})

        # Ran out of iterations without a submission.
        return ReviewResult(
            summary="Review did not complete within the iteration budget.", findings=[]
        )


def _find_submit(tool_uses: list) -> object | None:
    for tu in tool_uses:
        if tu.name == "submit_review":
            return tu
    return None


def _parse_result(data: dict) -> ReviewResult:
    findings = []
    for f in data.get("findings", []):
        try:
            findings.append(Finding(**f))
        except Exception:
            continue  # skip malformed findings rather than fail the whole review
    return ReviewResult(summary=data.get("summary", ""), findings=findings)
