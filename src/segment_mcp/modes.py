"""The `read` | `write` | `admin` mode model — BUILD-PLAN.md §6.

Reuses the *pattern* already proven in `instantly-mcp`'s autonomy tiers
(risk tier per action, a hard-block no level can override, a confirm gate
that echoes the pending change back before executing, redaction in
anything logged) — reimplemented natively here rather than taken on as a
dependency, and renamed to this project's own vocabulary: `SEGMENT_MCP_MODE`
(`read`/`write`/`admin`) instead of `AUTONOMY_LEVEL`
(`manual`/`assisted`/`autonomous`), and BUILD-PLAN's own Tier 1-4 instead
of instantly-mcp's READ/LOW_WRITE/HIGH_WRITE.

`SEGMENT_MCP_MODE` defaults to `read` — shipping v0.1 with zero write
tools is a feature (AGENTS.md). No tool in this repo is above `Tier.READ`
yet; `Tier.TIER2`/`TIER3`/`TIER4` exist now so the framework is ready
when v0.2/v0.3 add gated writes, per BUILD-PLAN.md §6's mode table:

    read  (default) — every v0.1 tool. No mutations reachable.
    write            — adds Tier 3 PATCHes and label management, each
                        echoing the change back for confirmation BEFORE
                        executing.
    admin            — adds Tier 2 deletes, gated behind a *typed*
                        confirmation naming the exact resource (not just
                        `confirm=true` — see `authorize()`).

Tier 1 (`POST /regulations` and `POST /regulations/sources/{id}` —
permanent, irreversible data deletion) is unreachable in *every* mode.
`authorize()` raises `Tier1UnreachableError` unconditionally for it,
before even checking what mode is active — see
`docs/decisions/0002-tier-1-permanently-unreachable.md` and
`docs/what-this-refuses-to-do.md`. `tests/test_tier1_unreachable.py`
proves this belt-and-braces: this module refuses it, the client refuses
the path independently (`client/public_api.py`), and no tool definition
references it.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

_VALID_VALUES = "'read', 'write', 'admin'"


class Mode(StrEnum):
    READ = "read"
    WRITE = "write"
    ADMIN = "admin"


class ModeConfigError(RuntimeError):
    """`SEGMENT_MCP_MODE` is set to something other than read/write/admin."""


def resolve_mode(env: Mapping[str, str] | None = None) -> Mode:
    """Resolve `SEGMENT_MCP_MODE` from the environment. Defaults to `read`
    — unlike `regions.resolve_region()`, an unset mode is not an error;
    read-only-by-default is the point (AGENTS.md)."""
    source = env if env is not None else os.environ
    raw = source.get("SEGMENT_MCP_MODE")
    if raw is None or not raw.strip():
        return Mode.READ
    normalized = raw.strip().lower()
    try:
        return Mode(normalized)
    except ValueError:
        raise ModeConfigError(
            f"SEGMENT_MCP_MODE={raw!r} is not valid. Set it to one of: "
            f"{_VALID_VALUES}, or leave it unset for the default (read)."
        ) from None


class Tier(StrEnum):
    """BUILD-PLAN.md §6's destructive-action tiers."""

    READ = "read"
    """Every v0.1 tool. Reachable in every mode."""

    TIER3 = "tier3"
    """Replace-semantics PATCHes and label management (the "sneaky" tier —
    a PUT that reads as "update" to a model but replaces everything).
    Reachable in `write` and `admin`, gated behind an echo-and-confirm."""

    TIER2 = "tier2"
    """Deletes that destroy configuration and break live pipelines.
    Reachable only in `admin`, gated behind a typed, resource-naming
    confirmation."""

    TIER4 = "tier4"
    """Side-effecting, expensive, or noisy (fires activations, deploys
    code, burns a metered budget). No tool at this tier exists yet; the
    slot is reserved so a future one has somewhere defined to go rather
    than being bolted on ad hoc."""

    TIER1 = "tier1"
    """`POST /regulations` and `POST /regulations/sources/{id}` — see the
    module docstring. Not reachable in ANY mode. `authorize()` raises
    before even looking at the mode for this tier."""


# Which modes reach which tier. Tier.TIER1 is deliberately absent from
# this mapping — `authorize()` never even performs the lookup for it, so
# there's no membership test whose logic could be gotten wrong later.
_TIER_REQUIRES_MODE: dict[Tier, frozenset[Mode]] = {
    Tier.READ: frozenset({Mode.READ, Mode.WRITE, Mode.ADMIN}),
    Tier.TIER3: frozenset({Mode.WRITE, Mode.ADMIN}),
    Tier.TIER2: frozenset({Mode.ADMIN}),
    Tier.TIER4: frozenset({Mode.ADMIN}),
}


class Tier1UnreachableError(RuntimeError):
    """Raised by `authorize()` for any Tier 1 action, unconditionally.

    This is not a normal "permission denied" — Tier 1 is not a mode a
    workspace owner can opt into. If you're reading this because a test
    or a tool call hit it, that's the guard working as designed; the fix
    is never to catch and route around this exception, but to not have
    called `authorize(Tier.TIER1, ...)` in the first place.
    """


ConfirmationKind = Literal["echo", "typed"]


@dataclass(frozen=True, slots=True)
class ModeDecision:
    """The result of `authorize()`. `allowed=False` is not an error — it's
    the normal shape of "not yet, here's what's needed" for a gated write,
    which a tool returns to the caller rather than raising."""

    allowed: bool
    reason: str | None = None
    requires_confirmation: ConfirmationKind | None = None


def authorize(
    tier: Tier,
    mode: Mode,
    *,
    confirmed: bool = False,
    typed_confirmation: str | None = None,
    expected_resource: str | None = None,
) -> ModeDecision:
    """Decide whether an action at `tier` may run under `mode`.

    - `Tier.TIER1` always raises `Tier1UnreachableError` — see above.
    - `Tier.READ` is always allowed.
    - `Tier.TIER3` (write mode+) requires `confirmed=True` — the caller
      is expected to have first called with `confirmed=False`, shown the
      echoed pending change to a human, and re-called only after they
      approved it. This is the "echo" confirmation kind.
    - `Tier.TIER2`/`Tier.TIER4` (admin mode) require
      `typed_confirmation == expected_resource` exactly — a boolean
      `confirmed=True` is not enough for a delete. This is the "typed"
      confirmation kind, and `expected_resource` must be provided by the
      caller (e.g. the destination ID about to be deleted).
    """
    if tier is Tier.TIER1:
        raise Tier1UnreachableError(
            "Tier 1 actions (regulation/deletion creation) are permanently "
            "unreachable, in every mode. See "
            "docs/decisions/0002-tier-1-permanently-unreachable.md."
        )

    allowed_modes = _TIER_REQUIRES_MODE[tier]
    if mode not in allowed_modes:
        required = " or ".join(sorted(m.value for m in allowed_modes))
        return ModeDecision(
            allowed=False,
            reason=(
                f"This action requires {required} mode; SEGMENT_MCP_MODE "
                f"is currently {mode.value!r}."
            ),
        )

    if tier is Tier.READ:
        return ModeDecision(allowed=True)

    if tier is Tier.TIER3:
        if confirmed:
            return ModeDecision(allowed=True)
        return ModeDecision(
            allowed=False,
            requires_confirmation="echo",
            reason=(
                "This change was echoed back, not executed. Review it, "
                "then re-call with confirm=true to apply it."
            ),
        )

    # Tier.TIER2 and Tier.TIER4: typed confirmation naming the exact resource.
    if expected_resource is None:
        return ModeDecision(
            allowed=False,
            reason="This tool must supply expected_resource to require a typed confirmation.",
        )
    if typed_confirmation == expected_resource:
        return ModeDecision(allowed=True)
    return ModeDecision(
        allowed=False,
        requires_confirmation="typed",
        reason=(
            f"This action permanently affects {expected_resource!r}. "
            f"Re-call with confirm_resource={expected_resource!r} — typed "
            f"exactly, not a boolean — to execute it."
        ),
    )
