"""Guards against `server.json` violating MCP Registry field limits, and
against `server.json`'s checked-in version drifting from the package
version.

Nothing validated the length limits before the v0.1.1 release, which
shipped a `description` over the registry's 100-character cap: PyPI
publish and the GitHub Release succeeded, but the `publish to the MCP
Registry` job in `release.yml` failed with a 422 after everything else
had already gone out — a partial, hard-to-cleanly-retry release state.
The MCP Registry schema (`server.schema.json`) caps `description` and
`title` at 100 characters and `name` at 200; this test catches a future
regression locally and in CI, before a tag is ever pushed.

Nothing validated the version fields either: `v0.1.2` was tagged and
pushed while `server.json` still said `"0.1.1"` in both places (CLOSE3-3).
`release.yml`'s `publish-mcp-registry` job stamps `server.json`'s version
fields with the tag version at release time, so a stale checked-in
version doesn't break that one publish run — but a version this far out
of sync is exactly the kind of drift that's invisible until publication
and expensive after, per AGENTS.md, so it's worth catching here too,
independent of whether that particular release-time stamp papers over it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
SERVER_JSON = json.loads((_REPO_ROOT / "server.json").read_text())

# From https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json's
# ServerDetail definition — not fetched live in tests (no live network calls,
# see AGENTS.md), so these are pinned constants. If the schema URL in
# server.json's $schema field is ever bumped, re-check these against it.
_MAX_DESCRIPTION_LENGTH = 100
_MAX_TITLE_LENGTH = 100
_MAX_NAME_LENGTH = 200


def _package_version() -> str:
    init_text = (_REPO_ROOT / "src" / "segment_mcp" / "__init__.py").read_text()
    match = re.search(r'^__version__ = "(.*)"$', init_text, re.MULTILINE)
    assert match is not None, "src/segment_mcp/__init__.py has no __version__ assignment"
    return match.group(1)


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


def test_top_level_version_matches_the_package_version() -> None:
    pkg_version = _package_version()
    assert SERVER_JSON["version"] == pkg_version, (
        f"server.json's top-level version is {SERVER_JSON['version']!r}, "
        f"but src/segment_mcp/__init__.py's __version__ is {pkg_version!r} "
        "— this class of drift is what left v0.1.2 tagged and pushed with "
        "server.json still saying 0.1.1 (CLOSE3-3)."
    )


def test_package_entry_version_matches_the_package_version() -> None:
    pkg_version = _package_version()
    package_entry_version = SERVER_JSON["packages"][0]["version"]
    assert package_entry_version == pkg_version, (
        f"server.json's packages[0].version is {package_entry_version!r}, "
        f"but src/segment_mcp/__init__.py's __version__ is {pkg_version!r}."
    )
