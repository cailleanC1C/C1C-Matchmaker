# Documentation Glossary

| Term | Definition |
|------|------------|
| **Guardrails** | The documentation + automation framework that enforces baseline quality for the bot family (inventory, gap analysis, migration plans, acceptance gates). |
| **Module Guardrails** | Per-surface folders under `REVIEW/MODULE_*` that enumerate responsibilities, dependencies, and checklists for each runtime component. |
| **Canon Labels** | The authoritative set of GitHub labels declared in `.github/labels/labels.json` (and future `harmonized.json`) that all automation and issue batches must use. |
| **Issue Batch** | JSON/YAML file consumed by `batch-issues.yml` to seed GitHub issues with shared defaults. |
| **Harmonized Labels** | Extended metadata file required by guardrails to align label names, colors, and semantics across repos. Missing today; scheduled in migration plan. |
| **ADR (Architecture Decision Record)** | Lightweight document stored under `docs/ADR/` that captures context, decision, and consequences for architectural changes. |
| **Planning Wave** | Guardrails rollout phase focusing on documentation + automation alignment before implementation work begins. |
| **Automation Drift** | Mismatches between workflow assumptions and repo state (e.g., `sync-labels.yml` watching the wrong path). |
| **Surface Map** | Overview of code pathways and dependencies within a module; required for module guardrails. |
| **Acceptance Checklist** | Final gating list verifying that plan deliverables are complete before moving to implementation. |
