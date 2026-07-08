"""The review engine — provider-agnostic via LangChain.

Uses LangChain's `init_chat_model`, so the inference model is chosen entirely by
config (`LLM_PROVIDER` / `LLM_MODEL`): swap Groq, Anthropic, or OpenAI with no
code change. Findings come back as structured output (a validated `ReviewResult`),
so there's no fragile JSON parsing.

Single-shot by design — the model sees the full diff and returns findings in one
call. (If you later want the agentic `get_file` step, LangChain's `bind_tools`
adds it in a provider-agnostic way.)
"""

from __future__ import annotations

import os

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, SystemMessage

from .config import Settings
from .github_client import ChangedFile, should_skip
from .schemas import Finding, RepoConfig, ReviewResult

SYSTEM_PROMPT = """You are a senior software engineer reviewing a GitHub pull request.

Review the diff for real problems: correctness bugs, security issues, resource \
leaks, race conditions, broken error handling, and clear performance traps. \
Prefer a handful of high-signal findings over exhaustive nitpicking. Do NOT flag \
pure style or naming unless the repo config asks for nits.

Only anchor a finding to a line that appears in the provided diff. If the PR \
looks clean, return an empty findings list with a one-line summary."""

# Maps a provider name to the env var LangChain reads for its API key.
_PROVIDER_ENV = {
    "groq": ("GROQ_API_KEY", "groq_api_key"),
    "anthropic": ("ANTHROPIC_API_KEY", "anthropic_api_key"),
    "openai": ("OPENAI_API_KEY", "openai_api_key"),
}


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
        provider = settings.llm_provider.lower()

        # Make the chosen provider's key visible to LangChain (it reads env vars).
        env = _PROVIDER_ENV.get(provider)
        if env:
            env_name, attr = env
            key = getattr(settings, attr, "")
            if key and not os.environ.get(env_name):
                os.environ[env_name] = key

        # No temperature override — some models (e.g. Claude Sonnet 5) reject
        # non-default sampling params. Structured output handles determinism.
        model = init_chat_model(settings.llm_model, model_provider=provider)
        self._llm = model.with_structured_output(ReviewResult)

    async def review(
        self,
        title: str,
        description: str,
        files: list[ChangedFile],
        config: RepoConfig,
        fetch_file=None,  # kept for interface compatibility; unused in single-shot
    ) -> ReviewResult:
        review_files = [f for f in files if not should_skip(f.path)][: self._settings.max_files]
        if not review_files:
            return ReviewResult(summary="No reviewable code changes in this PR.", findings=[])

        prompt = build_user_prompt(title, description, review_files, config)
        result = await self._llm.ainvoke(
            [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=prompt)]
        )
        if isinstance(result, ReviewResult):
            return result
        # Some providers return a dict — coerce and drop malformed findings.
        return _parse_result(result if isinstance(result, dict) else {})


def _parse_result(data: dict) -> ReviewResult:
    findings = []
    for f in data.get("findings", []):
        try:
            findings.append(Finding(**f))
        except Exception:
            continue  # skip malformed findings rather than fail the whole review
    return ReviewResult(summary=data.get("summary", ""), findings=findings)
