"""A read-first MCP server for Twilio Segment.

Answers which destinations get which events, which sources are dead, and
which are governed by nothing. Read-only by default; regulation/deletion
creation is not exposed in any mode. See BUILD-PLAN.md for the design.
"""

__version__ = "0.1.0"
