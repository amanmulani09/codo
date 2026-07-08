"""Core logic tests — no live GitHub/Anthropic calls required."""

from __future__ import annotations

import hashlib
import hmac

from app import gating
from app.github_client import ChangedFile, _format_comment, should_skip
from app.main import _verify_github_signature, settings
from app.reviewer import _parse_result, build_user_prompt
from app.schemas import Finding, RepoConfig


def test_signature_verification_roundtrip():
    settings.github_webhook_secret = "topsecret"
    body = b'{"action":"opened"}'
    sig = "sha256=" + hmac.new(b"topsecret", body, hashlib.sha256).hexdigest()
    assert _verify_github_signature(body, sig) is True
    assert _verify_github_signature(body, "sha256=deadbeef") is False
    assert _verify_github_signature(body, None) is False


def test_path_skip_rules():
    assert should_skip("package-lock.json".replace(".json", ".lock"))
    assert should_skip("node_modules/foo/index.js")
    assert should_skip("assets/logo.png")
    assert not should_skip("app/main.py")


def test_gating_free_blocks_private(monkeypatch):
    gating.PLANS.clear()
    gating._USAGE.clear()
    decision = gating.check_and_gate("acme", private=True)
    assert decision.allowed is False
    assert "private" in decision.reason.lower()


def test_gating_pro_allows_private():
    gating.PLANS.clear()
    gating._USAGE.clear()
    gating.upsert_plan("acme", "pro")
    assert gating.check_and_gate("acme", private=True).allowed is True


def test_gating_usage_limit():
    gating.PLANS.clear()
    gating._USAGE.clear()
    for _ in range(gating._LIMITS["free"]):
        gating.record_review("acme")
    assert gating.check_and_gate("acme", private=False).allowed is False


def test_prompt_includes_diff():
    files = [ChangedFile(path="a.py", status="modified", additions=3, patch="+x = 1")]
    prompt = build_user_prompt("My PR", "desc", files, RepoConfig())
    assert "a.py" in prompt
    assert "+x = 1" in prompt


def test_parse_result_skips_malformed():
    data = {
        "summary": "looks fine",
        "findings": [
            {"file": "a.py", "line": 3, "severity": "warning", "comment": "leak"},
            {"file": "b.py"},  # malformed — dropped
        ],
    }
    result = _parse_result(data)
    assert result.summary == "looks fine"
    assert len(result.findings) == 1
    assert result.findings[0].file == "a.py"


def test_format_comment_includes_suggestion():
    f = Finding(file="a.py", line=1, severity="critical", comment="bug", suggestion="x = 2")
    out = _format_comment(f)
    assert "Critical" in out
    assert "```suggestion" in out
    assert "x = 2" in out
