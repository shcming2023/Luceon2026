# Annotation Model

## Core Objects

- `document`: one cleaned teaching-material master and run metadata.
- `section`: a heading-bounded source region such as unit, chapter, lesson, activity, or problem group.
- `asset`: a reusable teaching object: concept, example, exercise, assessment, reading, activity, table, figure, note, answer.
- `media`: an image reference plus inferred role and linked asset.
- `relation`: typed edge between sections, assets, media, answers, and source spans.
- `review_item`: an actionable uncertainty or quality defect.

## Stable Asset Types

Keep `asset_type` broad:

- `concept`
- `example`
- `exercise`
- `assessment`
- `reading`
- `activity`
- `table`
- `figure`
- `note`
- `answer`

Use `role` and `task_type` for domain detail.

## Answer Status

Use explicit values:

- `no_answer_in_source`
- `answer_section_excluded`
- `answer_present_unmatched`
- `answer_matched`
- `answer_missing`
- `manual_review_required`

## Quality Flags

Recommended flags:

- `formula_spacing_suspect`
- `blank_missing_suspect`
- `page_break_inside_asset`
- `choice_count_mismatch`
- `media_unlinked`
- `media_link_low_confidence`
- `table_structure_suspect`
- `heading_role_unknown`
- `ocr_review_needed`
- `empty_asset`

## Compatibility Rule

Do not add book-specific fields to the core schema. Put local detail in:

- `profile`
- `role`
- `task_type`
- `metadata`
- `quality.flags`
