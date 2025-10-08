# Documentation Map

_Last updated: 2025-10-08_

## Global Docs
- **Purpose**: Define engineering and development guardrails plus supporting references.
- **Location pattern**: `docs/*.md` (top-level).
- **Naming conventions**: Uppercase filenames (e.g., `ENGINEERING.md`, `DEVELOPMENT.md`, `DOCS_MAP.md`).
- **Create when…**: Establishing or updating organization-wide practices, onboarding references, or navigation aids.
- **Current files (2)**:
  - `docs/DOCS_MAP.md`
  - `docs/DOCS_GLOSSARY.md`

## ADRs
- **Purpose**: Record architectural decisions with context and consequences.
- **Location pattern**: `docs/ADR/*.md`.
- **Naming conventions**: `ADR-<NNN>-<short-title>.md` with chronological numbering.
- **Create when…**: Committing to significant technical decisions requiring traceability.
- **Current files (0)**: _None_

## Module Reviews
- **Purpose**: Capture per-module findings, plans, and acceptance criteria for guardrails compliance.
- **Location pattern**: `REVIEW/MODULE_*/**`.
- **Naming conventions**: Directory per module (e.g., `MODULE_MATCHMAKER/`), containing `PLAN.md`, `CHECKLIST.md`, etc.
- **Create when…**: Initiating module-specific guardrails rollout or audit.
- **Current files (legacy)**:
  - `REVIEW/ARCH_MAP.md`
  - `REVIEW/FINDINGS.md`
  - `REVIEW/HOTSPOTS.csv`
  - `REVIEW/LINT_REPORT.md`
  - `REVIEW/PERF_NOTES.md`
  - `REVIEW/REVIEW.md`
  - `REVIEW/TESTPLAN.md`
  - `REVIEW/THREATS.md`
  - `REVIEW/TODOS.md`
  - `REVIEW/TYPECHECK_REPORT.md`
  - `REVIEW/BOOTSTRAP_GUARDRAILS/INVENTORY.md`
  - `REVIEW/BOOTSTRAP_GUARDRAILS/GAP_ANALYSIS.md`
  - `REVIEW/BOOTSTRAP_GUARDRAILS/MIGRATION_PLAN.md`
  - `REVIEW/BOOTSTRAP_GUARDRAILS/ACCEPTANCE_CHECKLIST.md`

## Issue Batches
- **Purpose**: Group related GitHub issues for phased rollout tracking.
- **Location pattern**: `.github/issue-batches/*.json|yml`.
- **Naming conventions**: `<initiative>.json` with kebab-case names.
- **Create when…**: Planning or executing multi-issue initiatives that need batch creation.
- **Current files (2)**:
  - `.github/issue-batches/issues.json`
  - `.github/issue-batches/guardrails-rollout.json`

## Workflows
- **Purpose**: Automate guardrail enforcement, synchronization, and governance.
- **Location pattern**: `.github/workflows/*.yml`.
- **Naming conventions**: Kebab-case YAML files describing the workflow purpose.
- **Create when…**: Automations are needed to enforce or support guardrails.
- **Current files (4)**:
  - `.github/workflows/add-to-cross-bot-project.yml`
  - `.github/workflows/batch-issues.yml`
  - `.github/workflows/migrate-labels.yml`
  - `.github/workflows/sync-labels.yml`

## Labels Canon
- **Purpose**: Define the authoritative label set for the repository.
- **Location pattern**: `.github/labels/*.json` (target: `harmonized.json`).
- **Naming conventions**: Single canon file named `harmonized.json` with sorted label entries.
- **Create when…**: Establishing or updating the official label taxonomy used by automation and contributors.
- **Current files (1)**:
  - `.github/labels/labels.json` _(legacy filename)_

## Static Assets
- **Purpose**: Store diagrams, images, and other non-text assets referenced by docs.
- **Location pattern**: `docs/assets/**` or module-specific `assets/` directories.
- **Naming conventions**: Lowercase descriptive filenames with hyphens.
- **Create when…**: Visual aids are required for guardrails comprehension.
- **Current files (0)**: _None_
