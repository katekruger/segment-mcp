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


def path_routed_transport(routes: dict[str, str | list[str]]) -> httpx.MockTransport:
    """A transport that routes each request by its URL path (ignoring the
    query string), for tools that compose calls to several distinct
    endpoints rather than one endpoint called several times.

    `routes` maps an exact request path (e.g. "/sources/src_1/settings")
    to one fixture path, or a list of fixture paths consumed in order for
    repeated requests to that same path (e.g. two differently-windowed
    calls to "/events/volume") — the last fixture in a list repeats for
    any further requests past the list's length, same as `mock_transport`.
    """
    queues: dict[str, list[dict[str, Any]]] = {
        path: [load_fixture(p) for p in ([spec] if isinstance(spec, str) else spec)]
        for path, spec in routes.items()
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        queue = queues.get(path)
        if not queue:
            raise AssertionError(
                f"No fixture routed for {request.method} {path}. Routed paths: {sorted(queues)}"
            )
        fixture = queue.pop(0) if len(queue) > 1 else queue[0]
        return response_from_fixture(fixture)

    return httpx.MockTransport(handler)
