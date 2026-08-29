"""Load recorded HTTP-response fixtures into `httpx.MockTransport`s.

See `tests/fixtures/README.md` for the fixture format and its provenance.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

FIXTURES_DIR = Path(__file__).parent


def load_fixture(relative_path: str) -> dict[str, Any]:
    """Load one fixture JSON file, relative to `tests/fixtures/`."""
    return json.loads((FIXTURES_DIR / relative_path).read_text())


def response_from_fixture(fixture: dict[str, Any]) -> httpx.Response:
    """Build an `httpx.Response` from a loaded fixture dict."""
    status_code = fixture["status_code"]
    headers = fixture.get("headers", {})
    if "raw_body" in fixture:
        return httpx.Response(status_code, headers=headers, content=fixture["raw_body"].encode())
    return httpx.Response(status_code, headers=headers, json=fixture.get("body", {}))


def mock_transport(*fixture_paths: str) -> httpx.MockTransport:
    """A transport that returns each fixture in order, one per request,
    then repeats the last fixture for any further requests."""
    fixtures = [load_fixture(path) for path in fixture_paths]
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        index = min(calls["count"], len(fixtures) - 1)
        calls["count"] += 1
        return response_from_fixture(fixtures[index])

    return httpx.MockTransport(handler)
