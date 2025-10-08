# Guardrails Inventory — Full Repository Audit

## Layout Overview
- **Root files**: `README.md`, `CHANGELOG.md`, `requirements.txt`, `bot_clanmatch_prefix.py`, `welcome.py`.
- **Top-level folders**: `.github/`, `docs/`, `REVIEW/`.
- **Missing expected trees**: no `src/`, `cogs/`, `services/`, `tests/`, `utils/`, `adapters/`, `scripts/`, `assets/`, or `docs/ADR/` folders. Guardrails module folders under `REVIEW/MODULE_*` are absent.

## Guardrails + Review Assets
- `REVIEW/` contains aggregate docs: `REVIEW.md`, `ARCH_MAP.md`, `FINDINGS.md`, `PERF_NOTES.md`, `TESTPLAN.md`, `THREATS.md`, `TODOS.md`, plus generated reports (`HOTSPOTS.csv`, `LINT_REPORT.md`, `TYPECHECK_REPORT.md`).
- `REVIEW/BOOTSTRAP_GUARDRAILS/` holds the working guardrails planning set (`INVENTORY.md`, `GAP_ANALYSIS.md`, `MIGRATION_PLAN.md`, `ACCEPTANCE_CHECKLIST.md`).
- No module-specific folders (e.g., `REVIEW/MODULE_matchmaker/`) exist yet, so there is nowhere to anchor per-surface checklists.

## Code & Modules
- Runtime code lives in two Python entry points at repo root: `bot_clanmatch_prefix.py` (core bot, HTTP server, schedulers) and `welcome.py` (Cog for welcome commands).
- There are no package folders (`src/`, `cogs/`, `adapters/`, etc.) to map to guardrails modules; everything is in the monolithic root scripts.
- No automated tests or harnesses (`tests/`, `scripts/`) are present.

## Documentation State
- Formal docs under `docs/`: `DOCS_MAP.md`, `DOCS_GLOSSARY.md` (both minimal and due for refresh in this audit).
- Planning / review docs under `REVIEW/` as noted above.
- Markdown outside `docs/` or `REVIEW/`: `README.md` and `CHANGELOG.md` remain at repo root (should be noted for guardrails placement guidance).
- No ADRs, runbooks, or onboarding guides yet.

## Workflow Automation (`.github/workflows/`)
- `add-to-cross-bot-project.yml`: pushes issues into a project and syncs Priority based on `P*` labels.
- `batch-issues.yml`: manual (`workflow_dispatch`) runner that accepts a `file` input (default `.github/issue-batches/issues.json`) and bulk creates labels/issues.
- `migrate-labels.yml`: manual migration from historical `area:*` to canonical `bot:*` / `comp:*` labels; auto-detects bot label from repo name.
- `sync-labels.yml`: dispatchable and `push`-triggered for `.github/labels/labels.yml`, but the repo only ships `labels.json` (mismatch).
- No CI/testing workflows, no scheduled jobs.

## Labels & Issue Batches
- Canon labels defined in `.github/labels/labels.json`; no `.github/labels/harmonized.json` file present.
- Issue batches live under `.github/issue-batches/` (`issues.json`, `guardrails-rollout.json`). Current batches reference non-canonical labels (`P0-robustness`, `matchmaker`, `welcome`, etc.).
- `batch-issues.yml` default points at `.github/issue-batches/issues.json`; guardrails batch must stay in sync with that tooling.

## Configuration & Tooling Inventory
- Dependency pinning: `requirements.txt`; no `pyproject.toml`, `Pipfile`, or lockfile.
- No lint/type-check config files (`pyproject`, `ruff.toml`, `mypy.ini`) committed despite reports in `REVIEW/`.
- No `.editorconfig`, `Dockerfile`, `docker-compose.yml`, or `Makefile`.
- No environment templates (`.env.sample`) or deployment manifests.

## Summary Signals
- Repository is small but lacks the structured directories expected by guardrails rollout (module docs, ADRs, tests, automation configs).
- Guardrails planning artifacts exist but need refresh to capture the above gaps and plan remediation.
