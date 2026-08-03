---
name: material-semantic-annotator
description: Annotate cleaned teaching-material Markdown into a reusable semantic asset layer. Use after pdf-clean-markdown-rebuild has produced clean.md, images/, manifest.json, and QA artifacts, when Codex needs to identify chapters/units, lessons, teaching columns, examples, exercises, assessments, media relations, answer status, OCR/layout quality flags, and review items for downstream handout, textbook, worksheet, question-bank, or slide generation.
---

# Material Semantic Annotator

## ElegantBookCompiler formal-v1 boundary

This skill is not a semantic authority in the formal ElegantBookCompiler v1
pipeline. Formal Spec 04-A/B/C/D contracts, promotions, and the frozen
`render_plan.json` are owned by `luceon-popo-to-refined-elegantbook` and must be
derived from the active canonical ledger and decision index.

Within that formal mode, this skill may be used only as a read-only profile or
review-suggestion adapter over explicitly supplied source atom/span evidence.
Its output is non-authoritative until the owning Spec 04 producer validates and
commits it. It must not publish, mutate source or promoted artifacts, call an
LLM without a separately approved and recorded review plan, choose ElegantBook
constructs, or bypass an open review item. If its asset model conflicts with the
canonical contracts, the canonical contracts win and the run stops for review.

The skill's independent semantic-asset workflow remains available outside this
declared formal-v1 boundary.

## Goal

Turn a faithful clean Markdown master into a teaching-material asset layer. Keep one compatible core schema across books; adapt through profile JSON and optional book configuration, not through per-book hardcoding.

Expected input:

- `clean.md`
- `images/`
- optional `manifest.json`, `qa_report.md`, `outline_apply_report.json`

Expected output in `annotation/`:

- `document.json`
- `sections.json`
- `assets.json`
- `media.json`
- `relations.json`
- `review_items.json`
- `quality_report.md`
- `preview.html`

### Luceon Refined ElegantBook Mode

When invoked by `luceon-popo-to-refined-elegantbook`, write annotation outputs under the Codex run workspace, preferably `02-semantic-annotation/annotation/`, and include Luceon trace fields such as `material_id`, `popo_run_id`, section source spans, and input clean-md object or path refs where available. This mode must not publish to MinIO; final publication belongs to the main skill's `eduassets-elegantbook` manifest.

## Principles

- Preserve source fidelity. Do not rewrite teaching content during annotation.
- Use deterministic rules first. Use LLM only for selected ambiguous review items.
- Keep the schema stable: `document`, `section`, `asset`, `media`, `relation`, `quality_flag`.
- Use profiles for domain conventions such as math workbook or English grammar workbook.
- Use book config only for naming variations and answer policy.
- Record uncertainty instead of guessing. Prefer `review_items.json` over silent repair.

## Workflow

1. Inspect the clean rebuild artifacts and confirm the input is already body-only.
2. Choose a profile:
   - Use `profiles/general_textbook.json` for chapter/unit-based textbooks, coursebooks, and broad teaching books when no narrower workbook profile fits.
   - Use `profiles/math_workbook.json` for chapter/lesson/训练/例题 style math materials.
   - Use `profiles/english_grammar_workbook.json` for Unit/Grammar Point/Practice/Review style English grammar materials.
   - Use `auto` only when the heading tree is representative enough for profile detection.
3. From this skill's release directory, run:

```bash
python3 scripts/annotate_material.py /path/to/body-final/clean.md --out-dir /path/to/body-final/annotation --profile auto
```

4. Review `quality_report.md` and `preview.html`.
5. If the profile mapping is wrong, adjust profile JSON or pass `--profile`.
6. If book-specific naming is needed, pass `--book-config path/to/book_config.json`; do not edit the script for one book.
7. Only run LLM review after rule output identifies a small set of `review_items`.

## Output Contract

Sections represent source hierarchy with provenance:

```json
{"id":"sec-001","title":"...","level":2,"role":"lesson","source_span":{"start_line":1,"end_line":20}}
```

Assets represent reusable teaching units:

```json
{"id":"asset-001","asset_type":"exercise","role":"basic_practice","task_type":"fill_blank","content":{"stem_md":"..."}}
```

Media records every referenced image and its inferred relation:

```json
{"id":"media-001","src":"images/name.jpg","linked_asset_id":"asset-001","role":"problem_figure","confidence":0.82}
```

Review items are actionable uncertainties:

```json
{"severity":"P2","reason":"choice_count_mismatch","source_span":{"start_line":10,"end_line":18},"suggested_action":"manual_check"}
```

## Validation

For each run, verify:

- All Markdown image links are represented in `media.json`.
- Every asset has a `source_span`.
- Every section belongs to the tree or is marked root.
- The answer status is explicit, not implied by missing data.
- `preview.html` visually shows assets, media links, and review flags.
- `quality_report.md` reports profile, counts, review severities, and media link rate.

## Resources

- `references/annotation-model.md`: core data model and extension rules.
- `profiles/*.json`: domain profiles. Add profile rules here before changing code.
- `schemas/*.schema.json`: lightweight JSON schemas for output shape.
- `scripts/annotate_material.py`: v0.1 deterministic annotator and preview generator.
