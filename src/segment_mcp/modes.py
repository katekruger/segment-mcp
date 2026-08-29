"""The `read` | `write` | `admin` mode model.

`SEGMENT_MCP_MODE` defaults to `read`. Tier 1 (`POST /regulations` and
`POST /regulations/sources/{id}`) is unreachable in every mode — see
docs/decisions/0002-tier-1-permanently-unreachable.md. Implemented in
Prompt 1.
"""
