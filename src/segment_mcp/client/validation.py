"""Validation for resource identifiers reaching this client from a tool call.

Every `source_id`/`destination_id`/tracking-plan-`id` this package handles
gets interpolated directly into a Public API request path, e.g.
`f"/sources/{source_id}"`. `httpx` normalizes dot-segments in that path
before the request goes out, so a value like `"../regulations"` walks the
request out of the advertised `/sources/...` surface entirely — confirmed
live: `source_id="../regulations"` sends `GET https://api.segmentapis.com/regulations`,
and `source_id="../../v1beta/users?x=1"` both escapes further and injects a
query string. An LLM controls these parameters, so this is not a
theoretical input.

`validate_resource_id` is the one gate every such interpolation must pass
through. See `docs/decisions/0003-refuse-path-traversal-in-resource-ids.md`
for why this refuses rather than percent-encodes.
"""

from __future__ import annotations

import re

from segment_mcp.client.public_api import SegmentAPIError

# Segment resource IDs are opaque alphanumeric strings (see the fixtures
# under tests/fixtures/*/tools/ for real examples). This is deliberately
# narrow — anything a legitimate ID would never contain (a slash, a dot,
# a `?`, a `#`, whitespace) is refused, not tolerated.
_SEGMENT_ID_RE = re.compile(r"\A[A-Za-z0-9_-]{1,64}\Z")


class InvalidResourceIdError(SegmentAPIError):
    """Refused: a value used to build a request path was not a bare
    Segment identifier.

    Raised before any request is sent — for a tool argument, before the
    tool's client call is even made; for a value taken from a previous API
    response (e.g. `source.id`), before it's used to build the next
    request. There is no legitimate Segment ID this rejects; a value that
    hits this is either a mistaken input or an attempt to steer the
    request, and either way the correct response is to refuse, not repair.
    """


def validate_resource_id(value: str, *, kind: str) -> str:
    """Reject anything that is not a bare Segment resource identifier.

    Segment IDs are opaque alphanumeric strings. Anything containing a
    slash, a dot-segment, a query separator, or a fragment is not an ID;
    it is an attempt to steer the request. Refuse rather than escape,
    because there is no legitimate ID that needs escaping — percent-
    encoding `../regulations` would just turn it into a literal (and
    confusing) path segment, not a meaningful resource lookup.
    """
    if not _SEGMENT_ID_RE.match(value):
        raise InvalidResourceIdError(
            f"Refused: {kind!r} must be a bare Segment identifier "
            f"(alphanumeric, underscore, hyphen; 1-64 chars). Got {value!r}."
        )
    return value
