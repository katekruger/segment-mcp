"""Shared test helper for tools/ tests: a region-prefixed, path-routed
fixture client. See tests/fixtures/http.py for the underlying transport.
"""

from __future__ import annotations

from segment_mcp.client.public_api import SegmentPublicAPIClient
from segment_mcp.client.regions import Region
from tests.fixtures.http import path_routed_transport


def make_client(
    routes: dict[str, str | list[str]], *, region: Region = Region.US
) -> SegmentPublicAPIClient:
    """Build a client whose transport routes by path to fixtures under
    `tests/fixtures/{region}/...` — `routes` keys are request paths,
    values are fixture paths relative to the region directory.
    """
    prefix = region.value
    resolved = {path: _prefix(prefix, spec) for path, spec in routes.items()}
    return SegmentPublicAPIClient("fake-token", region, transport=path_routed_transport(resolved))


def _prefix(prefix: str, spec: str | list[str]) -> str | list[str]:
    if isinstance(spec, list):
        return [f"{prefix}/{item}" for item in spec]
    return f"{prefix}/{spec}"
