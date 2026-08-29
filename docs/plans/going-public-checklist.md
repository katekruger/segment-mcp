# Going-public checklist

This repo starts **private**. Several security and CI defaults behave
differently on a private repo (metered Actions minutes, GHAS-gated secret
scanning) than they will once this flips public. Do this work at flip time,
not now, but keep the list here so it isn't reinvented then.

Planned for release, once v0.1.0 (BUILD-PLAN.md §9, M5) is ready to ship.

## Before flipping to public

- [ ] Repo visibility: private → public (Settings → Danger Zone)
- [ ] Secret scanning: on (free automatically once public)
- [ ] Push protection: on
- [ ] CodeQL default setup: on (Settings → Code security)
- [ ] Private vulnerability reporting: on (already referenced from
      `SECURITY.md`; confirm the toggle itself is enabled)
- [ ] Dependabot alerts + security updates: on
- [ ] Expand `ci.yml` from a single job to a real Python version matrix
      (private-repo Actions minutes are metered; public repos on the free
      tier get much more headroom)
- [ ] Social preview image set (Settings → General → Social preview)
- [ ] Confirm `LICENSE`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, `CONTRIBUTING.md`
      all read correctly as public-facing docs, not written assuming a
      private, single-maintainer context
- [ ] Branch protection on `main`: require PR review, require the CI status
      check, disallow force-push
- [ ] Confirm no secrets, API responses, or internal notes ended up in commit
      history while the repo was private (a private repo is not a good place
      to be sloppy about this, but double-check before it's public)

## At release time (v0.1.0)

- [x] `release.yml` written (tag-triggered, verifies tag/CHANGELOG match,
      calls `ci.yml`, builds, publishes via Trusted Publishing into a
      `release` environment requiring manual approval, creates the GitHub
      Release) — **not yet verified end-to-end against a real tag push**,
      since that requires the PyPI Trusted Publisher (next item) to exist
      first, and no `release` environment exists in this repo yet either
      (Settings → Environments → New environment named exactly `release`,
      with required reviewers — the `publish` job's manual-approval gate
      does nothing until that environment exists)
- [ ] PyPI Trusted Publisher configured **before** tagging (human-only: needs
      a PyPI account and can't be done from this repo) — PyPI → your
      account → Publishing → Add a new pending publisher, with this
      repo (`katekruger/segment-mcp`), workflow filename (`release.yml`),
      and environment name (`release`) filled in exactly
- [ ] Terminal GIF recorded and embedded in the README (BUILD-PLAN.md §9, M5
      — "one question → one answer that would take 40 UI clicks")
- [ ] Registry submissions: awesome-mcp-servers, MCP Registry, Smithery,
      Anthropic plugin directory (BUILD-PLAN.md §10)
