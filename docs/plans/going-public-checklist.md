# Going-public checklist

This repo starts **private**. Several security and CI defaults behave
differently on a private repo (metered Actions minutes, GHAS-gated secret
scanning) than they will once this flips public. Do this work at flip time,
not now, but keep the list here so it isn't reinvented then.

Planned for release, once v0.1.0 (BUILD-PLAN.md §9, M5) is ready to ship.

## Before flipping to public

- [x] Repo visibility: private → public — done 2026-08-30
- [x] Secret scanning: on
- [x] Push protection: on
- [x] CodeQL default setup: on (`state: configured`; the initial scan run
      was still populating `languages` at check time — expected, not a
      problem, it fills in after the first scan completes)
- [x] Private vulnerability reporting: on
- [x] Dependabot alerts + security updates: on
- [x] Expand `ci.yml` from a single job to a real Python version matrix
      (3.12, 3.13)
- [ ] Social preview image set (Settings → General → Social preview) —
      Step 6, not done yet
- [x] Confirmed no secrets, API responses, or internal notes in commit
      history — grepped the full history for key/token/write-key patterns
      and `.env` additions during Prompt 5 Step 1; every fixture ID is
      synthetic since no live workspace was ever used
- [ ] Confirm `LICENSE`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, `CONTRIBUTING.md`
      all read correctly as public-facing docs — not re-verified this pass,
      worth a human read-through
- [ ] Branch protection on `main`: require PR review, require the CI status
      check, disallow force-push — not done; Step 3 of Prompt 5 didn't list
      this explicitly and it changes how pushes to `main` work going
      forward, so it's flagged here rather than silently applied

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
