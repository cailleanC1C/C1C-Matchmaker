# Guardrails Planning — Acceptance Checklist

- [ ] `docs/ENGINEERING.md` exists, references ADR guidance, and links to CODEOWNERS plus `docs/DOCS_MAP.md`.
- [ ] `docs/DEVELOPMENT.md` exists and cites guardrails workflows defined in `docs/ENGINEERING.md` and relevant ADRs.
- [ ] `docs/ADR/` directory exists with an index (`README.md` or similar) and at least one ADR template.
- [ ] `docs/DOCS_MAP.md` lists all global docs, ADRs, review packs, issue batches, workflows, and labels canon with correct paths.
- [ ] `docs/DOCS_GLOSSARY.md` defines ADR, Acceptance Checklist, Batch Issues, Guardrails CI, Structure Lint, and other core terms.
- [ ] `.github/labels/harmonized.json` exists and active repository labels are a subset of this canon.
- [ ] `.github/issue-batches/guardrails-rollout.json` is merged and dedicated to planning-only tasks.
- [ ] `.github/issue-batches/` includes a follow-up implementation batch or tracking plan referencing guardrails rollout.
- [ ] `.github/` contains CODEOWNERS and aligned PR/issue templates referencing the guardrails docs.
- [ ] `REVIEW/MODULE_*/` directories exist for each major subsystem or an explicit N/A justification is documented.
- [ ] Legacy review artifacts either migrated or cross-linked from module directories; stale duplicates removed or archived.
- [ ] Workflows in `.github/workflows/` documented in docs and validated against new guardrails requirements.
