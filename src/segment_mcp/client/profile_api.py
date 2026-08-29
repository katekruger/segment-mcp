"""Client for the Segment Profile API.

HTTP Basic auth with the access token as username and a blank password —
different from every other Segment API. Lowercase external-ID normalization.
Separate trust tier, separate credential (`SEGMENT_PROFILE_TOKEN`), gated
behind explicit opt-in. Implemented in Prompt 3.
"""
