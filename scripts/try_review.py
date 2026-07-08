"""Run Codo's review agent against a sample diff — no GitHub or Redis needed.

Exercises the real review call end-to-end so you can iterate on the prompt and
see what findings it produces. Uses whatever LLM_PROVIDER/LLM_MODEL you set in
.env (default: Groq — free). Set the matching API key.

    uv run python scripts/try_review.py
"""

from __future__ import annotations

import asyncio

from app.config import get_settings
from app.github_client import ChangedFile
from app.reviewer import Reviewer
from app.schemas import RepoConfig

# A deliberately buggy diff so the model has something real to find.
SAMPLE_FILES = [
    ChangedFile(
        path="app/payments.py",
        status="modified",
        additions=9,
        patch=(
            "@@ -10,3 +10,12 @@ def get_user(db, user_id):\n"
            "     return db.query(User).get(user_id)\n"
            "+\n"
            "+def charge(db, user_id, amount):\n"
            "+    user = get_user(db, user_id)\n"
            "+    # bug: no None check — user may not exist\n"
            "+    balance = user.balance\n"
            "+    # bug: SQL built with string formatting (injection)\n"
            "+    db.execute(f\"UPDATE accounts SET balance = {balance - amount} \"\n"
            "+               f\"WHERE id = {user_id}\")\n"
            "+    # bug: division by user-supplied value, can be zero\n"
            "+    fee = amount / user.discount_divisor\n"
            "+    return fee\n"
        ),
    ),
]


async def main() -> None:
    settings = get_settings()
    keys = {
        "groq": settings.groq_api_key,
        "anthropic": settings.anthropic_api_key,
        "openai": settings.openai_api_key,
    }
    if not keys.get(settings.llm_provider.lower()):
        raise SystemExit(
            f"Set the API key for LLM_PROVIDER='{settings.llm_provider}' in .env first."
        )

    reviewer = Reviewer(settings)

    async def fetch_file(path: str) -> str | None:
        # Stub: pretend the rest of the repo isn't available.
        return None

    result = await reviewer.review(
        title="Add charge() to payments",
        description="Adds a function to charge a user's account.",
        files=SAMPLE_FILES,
        config=RepoConfig(),
        fetch_file=fetch_file,
    )

    print(f"\nPROVIDER/MODEL: {settings.llm_provider} / {settings.llm_model}")
    print("\n=== SUMMARY ===")
    print(result.summary)
    print(f"\n=== FINDINGS ({len(result.findings)}) ===")
    for f in result.findings:
        print(f"\n[{f.severity.upper()}] {f.file}:{f.line}")
        print(f"  {f.comment}")
        if f.suggestion:
            print(f"  suggestion: {f.suggestion}")


if __name__ == "__main__":
    asyncio.run(main())
