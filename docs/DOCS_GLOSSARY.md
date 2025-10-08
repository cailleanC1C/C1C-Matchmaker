# Documentation Glossary

_Last updated: 2025-10-08_

- **ADR (Architecture Decision Record)**: A lightweight document that captures the context, decision, and consequences for a significant architectural choice. Stored under `docs/ADR/` using sequential numbering.
- **Acceptance Checklist**: A verifiable list of conditions that must be met before closing out a guardrails phase. Lives alongside the relevant review or rollout plan.
- **Batch Issues**: Predefined sets of GitHub issues described in `.github/issue-batches/*.json|yml` that can be bulk-created to drive initiatives.
- **Guardrails CI**: Automated workflows in `.github/workflows/` that enforce or verify compliance with the documented guardrails.
- **Structure Lint**: Planned automation that validates repository structure (e.g., docs locations, naming conventions) against the target guardrails layout.
- **Module Review Pack**: Collection of module-scoped artifacts (`REVIEW/MODULE_*/`) combining inventories, plans, and checklists for a subsystem.
- **Labels Canon**: The authoritative list of GitHub labels (`.github/labels/harmonized.json`) ensuring automation and issue batches use consistent naming.
- **Planning Pack**: The documentation set (inventory, gap analysis, migration plan, acceptance checklist) produced before implementing guardrails changes.
