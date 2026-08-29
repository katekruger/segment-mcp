# Security Policy

## Reporting a Vulnerability

Please report security vulnerabilities using
[GitHub's private vulnerability reporting](https://github.com/katekruger/segment-mcp/security/advisories/new)
for this repository (Security tab → Report a vulnerability). Do not open a
public issue for a security report.

You should expect an initial response within a few days. This is currently a
solo-maintained project, so timelines are best-effort, not contractual.

## Scope Notes Specific to This Project

- This server holds a Segment Public API token, and later a separate Profile
  API token — never commit real credentials to a fixture, test, or example.
  See `AGENTS.md`.
- The Profile API returns PII on named individuals. Any code path that
  reaches it is a separate, higher trust tier — see
  `src/segment_mcp/client/profile_api.py` and BUILD-PLAN.md §5/§11.6. If you
  find a way to reach Profile API data without the explicit opt-in and
  per-call logging that tier requires, treat it as a security bug.
- Data-deletion creation (`POST /regulations` and its per-source sibling) is
  designed to be permanently unreachable in every mode — see
  `docs/decisions/0002-tier-1-permanently-unreachable.md`. If you find a
  path that reaches it, that is a critical security bug, not a feature gap.
- Region must be resolved explicitly on every call. A silent region mismatch
  is a correctness bug, not (on its own) a security one — but please report
  it either way if you find one, since it's the kind of bug that erodes
  trust in everything else the server reports.
