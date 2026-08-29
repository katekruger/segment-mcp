"""Tests for `modes.py` — mode resolution and the tier-authorization gate.

Tier 1 itself is covered exhaustively in test_tier1_unreachable.py, not
here.
"""

from __future__ import annotations

import pytest

from segment_mcp.modes import Mode, ModeConfigError, Tier, authorize, resolve_mode

# --------------------------------------------------------------------------
# resolve_mode
# --------------------------------------------------------------------------


def test_resolve_mode_defaults_to_read_when_unset() -> None:
    assert resolve_mode(env={}) is Mode.READ


@pytest.mark.parametrize("blank", ["", "   "])
def test_resolve_mode_defaults_to_read_when_blank(blank: str) -> None:
    assert resolve_mode(env={"SEGMENT_MCP_MODE": blank}) is Mode.READ


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("read", Mode.READ), ("WRITE", Mode.WRITE), (" admin ", Mode.ADMIN)],
)
def test_resolve_mode_accepts_case_and_whitespace_variance(raw: str, expected: Mode) -> None:
    assert resolve_mode(env={"SEGMENT_MCP_MODE": raw}) is expected


def test_resolve_mode_rejects_unknown_values() -> None:
    with pytest.raises(ModeConfigError, match="not valid"):
        resolve_mode(env={"SEGMENT_MCP_MODE": "autonomous"})


# --------------------------------------------------------------------------
# Tier.READ
# --------------------------------------------------------------------------


@pytest.mark.parametrize("mode", [Mode.READ, Mode.WRITE, Mode.ADMIN])
def test_read_tier_is_always_allowed(mode: Mode) -> None:
    decision = authorize(Tier.READ, mode)
    assert decision.allowed is True
    assert decision.requires_confirmation is None


# --------------------------------------------------------------------------
# Tier.TIER3 — echo confirmation, write mode+
# --------------------------------------------------------------------------


def test_tier3_denied_in_read_mode_regardless_of_confirmation() -> None:
    decision = authorize(Tier.TIER3, Mode.READ, confirmed=True)
    assert decision.allowed is False
    assert "write" in (decision.reason or "").lower()


def test_tier3_in_write_mode_without_confirm_echoes_and_denies() -> None:
    decision = authorize(Tier.TIER3, Mode.WRITE)
    assert decision.allowed is False
    assert decision.requires_confirmation == "echo"
    assert "confirm=true" in (decision.reason or "")


def test_tier3_in_write_mode_with_confirm_is_allowed() -> None:
    decision = authorize(Tier.TIER3, Mode.WRITE, confirmed=True)
    assert decision.allowed is True
    assert decision.requires_confirmation is None


def test_tier3_in_admin_mode_with_confirm_is_allowed() -> None:
    decision = authorize(Tier.TIER3, Mode.ADMIN, confirmed=True)
    assert decision.allowed is True


# --------------------------------------------------------------------------
# Tier.TIER2 / Tier.TIER4 — typed confirmation, admin mode only
# --------------------------------------------------------------------------


def test_tier2_denied_in_write_mode_even_with_confirmation() -> None:
    decision = authorize(
        Tier.TIER2, Mode.WRITE, typed_confirmation="dst_123", expected_resource="dst_123"
    )
    assert decision.allowed is False
    assert "admin" in (decision.reason or "").lower()


def test_tier2_in_admin_mode_without_typed_confirmation_is_denied() -> None:
    decision = authorize(Tier.TIER2, Mode.ADMIN, expected_resource="dst_123")
    assert decision.allowed is False
    assert decision.requires_confirmation == "typed"
    assert "dst_123" in (decision.reason or "")


def test_tier2_boolean_confirm_alone_is_not_enough() -> None:
    # A bare confirmed=True must NOT satisfy a typed-confirmation tier —
    # this is the whole point of distinguishing "echo" from "typed".
    decision = authorize(Tier.TIER2, Mode.ADMIN, confirmed=True, expected_resource="dst_123")
    assert decision.allowed is False
    assert decision.requires_confirmation == "typed"


def test_tier2_with_matching_typed_confirmation_is_allowed() -> None:
    decision = authorize(
        Tier.TIER2, Mode.ADMIN, typed_confirmation="dst_123", expected_resource="dst_123"
    )
    assert decision.allowed is True


def test_tier2_with_mismatched_typed_confirmation_is_denied() -> None:
    decision = authorize(
        Tier.TIER2, Mode.ADMIN, typed_confirmation="dst_999", expected_resource="dst_123"
    )
    assert decision.allowed is False
    assert decision.requires_confirmation == "typed"


def test_tier2_without_expected_resource_is_a_caller_bug_not_a_bypass() -> None:
    # A tool that forgets to supply expected_resource must not accidentally
    # authorize a delete — it should fail closed.
    decision = authorize(Tier.TIER2, Mode.ADMIN, typed_confirmation="anything")
    assert decision.allowed is False


def test_tier4_follows_the_same_typed_confirmation_rule_as_tier2() -> None:
    decision = authorize(
        Tier.TIER4, Mode.ADMIN, typed_confirmation="aud_1", expected_resource="aud_1"
    )
    assert decision.allowed is True
    denied = authorize(Tier.TIER4, Mode.WRITE, expected_resource="aud_1")
    assert denied.allowed is False
