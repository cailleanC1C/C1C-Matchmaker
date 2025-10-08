# Guardrails Gap Analysis — Whole Repository

| # | Gap | Severity | Notes |
|---|------|----------|-------|
| G-01 | No guardrails module folders under `REVIEW/MODULE_*` to map runtime surfaces (bot core, welcome cog, HTTP service). | medium | Without per-module directories + checklists we cannot scope surface-specific guardrails or track completion. |
| G-02 | Documentation architecture incomplete: `docs/` only contains the guardrails map/glossary; `docs/ADR/` missing. | medium | Guardrails rollout expects ADR staging and deeper runbooks/reference docs. |
| G-03 | Markdown files live outside the sanctioned docs trees (`README.md`, `CHANGELOG.md`). | low | Need conventions for root vs docs placement or relocation guidance. |
| G-04 | `.github/labels/harmonized.json` absent. | low | Canon label registry incomplete; guardrails require harmonized definitions. |
| G-05 | `.github/issue-batches/issues.json` and `guardrails-rollout.json` contain non-canonical labels (`P0-robustness`, `matchmaker`, `welcome`, `P1-startup`, `maintenance`). | medium | Violates guardrails label canon and conflicts with automation assumptions. |
| G-06 | `sync-labels.yml` watches `.github/labels/labels.yml` but repo only provides `labels.json`. | high | Automation will never trigger on label changes and manual dispatch could sync the wrong path. |
| G-07 | No CI/testing, lint, or type-check workflows despite existing reports in `REVIEW/`. | low | Guardrails expect automation coverage; absence noted for planning (actual workflow creation is out-of-scope here). |
| G-08 | Config/tooling scaffolding missing (no `.editorconfig`, lockfiles, Docker/compose, Makefile, env templates). | low | Needs documentation + future tasks to align with guardrails baselines. |
