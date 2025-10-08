# Documentation Map

## Repository Guides
- `README.md` — product overview, features, env configuration (root; relocate guidance pending guardrails decision).
- `CHANGELOG.md` — release notes (root).
- `docs/README.md` *(planned in migration)* — docs structure + guardrails orientation.
- `docs/OPERATIONS.md` *(planned)* — runtime/config expectations and missing scaffolding tracker.
- `docs/ADR/` *(planned)* — architecture decision record home with template.

## Guardrails & Review Assets
- `REVIEW/REVIEW.md` — prior audit summary.
- `REVIEW/ARCH_MAP.md` — component overview.
- `REVIEW/FINDINGS.md` — prioritized issues.
- `REVIEW/PERF_NOTES.md` — performance notes.
- `REVIEW/TESTPLAN.md` — current testing strategy gaps.
- `REVIEW/THREATS.md` — threat modelling summary.
- `REVIEW/TODOS.md` — outstanding tasks backlog.
- `REVIEW/HOTSPOTS.csv`, `REVIEW/LINT_REPORT.md`, `REVIEW/TYPECHECK_REPORT.md` — generated analysis artifacts.
- `REVIEW/BOOTSTRAP_GUARDRAILS/*.md` — guardrails planning set (inventory, gaps, migration plan, acceptance checklist).
- *(Planned)* `REVIEW/MODULE_matchmaker/` & `REVIEW/MODULE_welcome/` — module-specific guardrails once created.

## Automation & Issue Planning
- `.github/issue-batches/issues.json` — general issue batch (needs label canon cleanup).
- `.github/issue-batches/guardrails-rollout.json` — guardrails planning batch (overwritten by this audit).
- `.github/labels/labels.json` — canonical labels registry.
- `.github/labels/harmonized.json` *(planned)* — harmonized label metadata.
- `.github/workflows/*.yml` — automation entry points (batch issues, label sync, migrations, project sync).

## Reference Lists & Glossary
- `docs/DOCS_GLOSSARY.md` — shared terminology across guardrails and docs.

## Pending Additions Called Out in Migration Plan
- Tests/automation documentation once CI plan is defined.
- Module checklists and supporting runbooks for each runtime surface.
