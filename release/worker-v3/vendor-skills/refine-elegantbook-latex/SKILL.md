---
name: refine-elegantbook-latex
description: Read-only diagnostic audit for generated ElegantBook or Overleaf-ready LaTeX projects. Use after mechanical rendering or compilation to identify LaTeX-local residue, structural, asset, compile, and page-layout risks without rewriting content, changing the template, deleting images, adding answer space, or producing a replacement project.
---

# Refine ElegantBook LaTeX

## Contract

This skill is a diagnostic observer, not a post-generation editor. It may inspect an existing project or ZIP, create an isolated copied review workspace, compile that copy, render its PDF, and write reports. It must not change the source project or turn a copied project into a promoted replacement.

The formal production boundary is:

```text
frozen render_plan + template_contract
  -> mechanical renderer (Spec 05)
  -> this read-only audit
  -> findings routed to the owning Spec 03, 04, or 05 stage
```

## Hard Boundaries

- Accept only `--mode audit`; `--mode polish` is intentionally rejected.
- Do not rewrite `main.tex`, chapter files, formulas, tables, headings, captions, metadata, or answers.
- Do not add packages, macros, environments, tcolorbox styles, answer areas, spacing commands, or layout patches.
- Do not delete, resize, replace, prune, or deduplicate images.
- Do not generate `refined-overleaf.zip` or claim that audit output is a corrected deliverable.
- Do not infer source truth from LaTeX alone. Source fidelity, reading order, transcription, and semantic mapping findings require the canonical ledger and PDF evidence in their owning stages.
- Compilation and rendering are diagnostic evidence only; they do not establish source fidelity or final acceptance.

## Run

From this skill's release directory, audit a directory:

```bash
python3 scripts/refine_elegantbook_latex.py \
  --project-dir /path/to/elegantbook-project \
  --out-dir /path/to/audit-output \
  --mode audit
```

Audit a ZIP and optionally compile/render the isolated copy:

```bash
python3 scripts/refine_elegantbook_latex.py \
  --zip /path/to/elegantbook-project.zip \
  --out-dir /path/to/audit-output \
  --mode audit \
  --compile \
  --render
```

Use `--force` only to replace this skill's prior audit-output directory. It never authorizes changes to the input project.

## Finding Ownership

- Missing, duplicated, mistranscribed, or source-unsupported content: return to Spec 03.
- Wrong hierarchy, teaching role, box, image representation, or render binding: return to Spec 04 and freeze a new render plan.
- Illegal LaTeX serialization, missing files, template drift, compile failure, or mechanical layout execution defect: return to Spec 05.
- Final per-page fidelity or visual failure: record in Spec 06, then return to the owning stage above.

Reports must separate observed facts from suggested ownership. They must never silently repair the artifact they diagnose.

## Outputs

Expected audit sidecars include `latex_polish_report.json`, `latex_polish_report.md`, `block_model.json`, and `editorial_decisions.json`; the historical filenames remain for compatibility but their mode must be `audit` and their change list must be empty. Optional compile/render folders are evidence only.

An audit passes only when the input bytes remain unchanged and no replacement ZIP is produced. Open findings keep the relevant product stage in `needs_review` or `failed`; they cannot be converted into a conditional pass here.
