# Guardrails Migration Plan (Planning Only)

## Scope & Ownership
| Artifact | Action | Owner | Effort |
| --- | --- | --- | --- |
| `docs/ENGINEERING.md` | Draft and adopt engineering guardrails doc | Docs Lead (TBD) | Medium |
| `docs/DEVELOPMENT.md` | Draft development workflows & guardrails | Docs Lead (TBD) | Medium |
| `docs/ADR/` skeleton | Create directory, template, and seed ADR index | Architecture WG (TBD) | Medium |
| `REVIEW/MODULE_*/` | Define module review template & migrate existing notes | QA/Review Lead (TBD) | High |
| `.github/labels/harmonized.json` | Author harmonized canon & sync with GitHub | Operations (TBD) | Medium |
| `.github/issue-batches/*.json` | Split planning vs delivery batches | Program Manager (TBD) | Low |
| `.github/` templates & `CODEOWNERS` | Introduce review routing | Operations (TBD) | Medium |
| `.github/workflows/*.yml` | Audit (no change yet) | DevOps (TBD) | Low |

## Implementation Steps
1. **Docs foundation**
   - Create `docs/ENGINEERING.md` and `docs/DEVELOPMENT.md` with cross-links to ADRs, CODEOWNERS, and guardrails references.
   - Stand up `docs/ADR/` with an index and template for future records.
2. **Review structure**
   - Establish `REVIEW/MODULE_TEMPLATE/` skeleton and migrate relevant content from legacy review files into module-specific directories.
   - Archive or cross-link remaining legacy review documents.
3. **Labels canon**
   - Author `.github/labels/harmonized.json` aligning with desired taxonomy (include `bot:achievements`, `docs`, `ready`, `comp:*`).
   - Use existing label sync workflow to stage updates (dry-run first).
4. **Planning & governance assets**
   - Publish planning issue batches (`guardrails-rollout.json` validated, future implementation batch prepared separately).
   - Add CODEOWNERS and PR/issue templates in `.github/`.
5. **Workflow validation**
   - Review existing workflows for alignment; document any required adjustments (implementation deferred to subsequent phase).

## Rollback Strategy
- Maintain backups of legacy review files; if migration stalls, keep `REVIEW/` root artifacts as canonical until module structure completes.
- For labels, retain existing `labels.json` and disable sync jobs before introducing the canon; re-point workflows back if needed.
- Defer adoption of new docs by gating references behind feature flags in README/ops notes until validated.

## Dependencies & Sequencing Notes
- Docs must exist before referencing them in issue batches or templates.
- Harmonized labels file should be finalized before opening issues that rely on canon-only labels.
- CODEOWNERS depends on identifying responsible teams per module (derive from module review ownership).
- Workflow updates require consensus on guardrails metrics gathered from new docs and ADRs.
