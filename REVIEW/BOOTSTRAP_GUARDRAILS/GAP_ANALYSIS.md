# Guardrails Gap Analysis

- **Global docs (ENGINEERING.md / DEVELOPMENT.md)** — *major*: Neither foundational document exists; no interim doc covers engineering or development guardrails.
- **ADRs (`docs/ADR/*.md`)** — *major*: No ADR framework or historical decisions captured.
- **Module review structure (`REVIEW/MODULE_*/`)** — *major*: Current review assets are monolithic; per-module directories and checklists absent.
- **Planning review pack (`REVIEW/BOOTSTRAP_GUARDRAILS/*`)** — *minor*: Newly added in this planning exercise; future modules will need consistent naming.
- **Issue batches (`.github/issue-batches/*.json|yml`)** — *minor*: Feature-focused batch exists, but no planning-only batch aligned with guardrails rollout.
- **Labels canon (`.github/labels/harmonized.json`)** — *major*: Canon file missing; existing `labels.json` may be outdated/inconsistent with desired taxonomy.
- **Workflows (`.github/workflows/*.yml`)** — *minor*: Workflows exist but may need validation against guardrails requirements (scope for later review).
- **Templates & CODEOWNERS** — *major*: No CODEOWNERS file or PR/issue templates to enforce review coverage and guardrails adherence.
