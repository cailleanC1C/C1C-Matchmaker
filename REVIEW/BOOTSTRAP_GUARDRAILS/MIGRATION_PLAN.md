# Guardrails Migration Plan — Whole Repo Refresh

> Owners are placeholders until staffing is assigned. Sequence prioritizes documentation, then labels, then workflow automation.

## Phase 1 — Document the structure (Owner: `[Docs Lead]`)
1. Draft a `docs/README.md` that explains doc placement conventions and references guardrails artifacts (covers G-02/G-03).
2. Create `docs/ADR/README.md` stub plus template ADR to establish the decision log home (G-02).
3. Update `docs/DOCS_MAP.md` and `docs/DOCS_GLOSSARY.md` after new docs land (tracked in acceptance).
4. Document tooling/config expectations in a new `docs/OPERATIONS.md` (or similar) referencing missing assets (G-08).

## Phase 2 — Establish module guardrails (Owner: `[Architecture]`)
1. Create `REVIEW/MODULE_matchmaker/` to cover `bot_clanmatch_prefix.py`; include checklist, surface map, and dependencies (G-01).
2. Create `REVIEW/MODULE_welcome/` for `welcome.py` Cog with matching checklist (G-01).
3. Add shared guidance in each module README about future test harness placement (ties to G-07 planning).

## Phase 3 — Align label canon (Owner: `[DevX]`)
1. Author `.github/labels/harmonized.json` mirroring `labels.json` plus guardrails metadata (G-04).
2. Normalize `.github/issue-batches/issues.json` and `guardrails-rollout.json` to canonical labels only; capture mapping decisions in docs (G-05).
3. Refresh `docs/DOCS_MAP.md` entry for issue batches/labels to reference the canon artifacts.

## Phase 4 — Fix automation drift (Owner: `[Automation]`)
1. Update `.github/workflows/sync-labels.yml` to monitor the correct file (`labels.json`) and document manual dispatch usage (G-06).
2. Evaluate and document gaps for CI/test workflows (G-07) — planning only; implementation deferred to follow-up guardrails waves.
3. Record outcomes + next steps in `REVIEW/BOOTSTRAP_GUARDRAILS/ACCEPTANCE_CHECKLIST.md`.

## Communication & Sign-off
- Share updated planning docs with Docs/Architecture/DevX for review.
- Use refreshed guardrails issue batch to track implementation once plan is approved.
