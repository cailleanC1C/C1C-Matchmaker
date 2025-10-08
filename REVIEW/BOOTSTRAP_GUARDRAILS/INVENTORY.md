# Guardrails Inventory — Current State

- **docs/ENGINEERING.md**: Missing (no docs/ directory existed prior to this planning pack).
- **docs/DEVELOPMENT.md**: Missing.
- **docs/ADR/**: No ADR directory or records present.
- **REVIEW/MODULE_*/**: No module-specific subdirectories; existing review artifacts (e.g., REVIEW.md, FINDINGS.md) live at the top level of `REVIEW/`.
- **REVIEW/** (general): Contains legacy review artifacts: `REVIEW.md`, `FINDINGS.md`, `ARCH_MAP.md`, `HOTSPOTS.csv`, `LINT_REPORT.md`, `PERF_NOTES.md`, `TESTPLAN.md`, `THREATS.md`, `TODOS.md`, `TYPECHECK_REPORT.md`.
- **.github/issue-batches/**: Contains `issues.json` (feature/maintenance mix). Planning batch `guardrails-rollout.json` to be introduced here.
- **.github/labels/harmonized.json**: Missing; only `labels.json` exists (naming does not match target canon).
- **.github/workflows/**: Present (`add-to-cross-bot-project.yml`, `batch-issues.yml`, `migrate-labels.yml`, `sync-labels.yml`).
- **.github/** templates: No PR/issue templates detected; `.github/` only holds workflows, `labels/`, and `issue-batches/` directories.
- **CODEOWNERS**: Missing.

_Notable oddities_: Labels file uses non-canonical filename (`labels.json`). Issue batch `issues.json` mixes feature work with planning, so a dedicated planning batch is absent. Review artifacts are centralized instead of per-module.
