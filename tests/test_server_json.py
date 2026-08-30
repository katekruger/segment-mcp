"""Guards against `server.json` violating MCP Registry field limits.

Nothing validated this before the v0.1.1 release, which shipped a
`description` over the registry's 100-character cap: PyPI publish and the
GitHub Release succeeded, but the `publish to the MCP Registry` job in
`release.yml` failed with a 422 after everything else had already gone
out — a partial, hard-to-cleanly-retry release state. The MCP Registry
schema (`server.schema.json`) caps `description` and `title` at 100
characters and `name` at 200; this test catches a future regression
locally and in CI, before a tag is ever pushed.
"""

from __future__ import annotations

import json
from pathlib import Path

SERVER_JSON = json.loads((Path(__file__).parent.parent / "server.json").read_text())

# From https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json's
# ServerDetail definition — not fetched live in tests (no live network calls,
# see AGENTS.md), so these are pinned constants. If the schema URL in
# server.json's $schema field is ever bumped, re-check these against it.
_MAX_DESCRIPTION_LENGTH = 100
_MAX_TITLE_LENGTH = 100
_MAX_NAME_LENGTH = 200


def test_description_is_within_the_registry_length_limit() -> None:
    description = SERVER_JSON["description"]
    assert len(description) <= _MAX_DESCRIPTION_LENGTH, (
        f"server.json's description is {len(description)} chars, over the "
        f"MCP Registry's {_MAX_DESCRIPTION_LENGTH}-char limit — this is "
        "exactly what broke the v0.1.1 release's MCP Registry publish step."
    )


def test_title_is_within_the_registry_length_limit() -> None:
    title = SERVER_JSON["title"]
    assert len(title) <= _MAX_TITLE_LENGTH


def test_name_is_within_the_registry_length_limit() -> None:
    name = SERVER_JSON["name"]
    assert len(name) <= _MAX_NAME_LENGTH
