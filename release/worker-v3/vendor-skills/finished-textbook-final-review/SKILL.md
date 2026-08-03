---
name: finished-textbook-final-review
description: Perform final delivery review for completed textbook, workbook, handout, or course-packet HTML/PDF packages. Use to check full content, layout, answer completeness, TOC/page numbers, cover/course-description quality, image/table/formula alignment, editing traces, and student/teacher edition consistency before print or delivery.
---

# Finished Textbook Final Review

## Boundary

This is the final教材交付 gate.

- It reviews finished HTML/PDF packages, not raw clean material.
- It may propose fixes, but substantial edits should be explicit revision work through `legacy-textbook-revision` or `material-to-new-textbook-builder`.
- Do not pass a book on API success, file existence, or script completion alone. Use rendered pages.

### Luceon Refined ElegantBook Mode

When invoked by `luceon-popo-to-refined-elegantbook`, this is an internal report-only gate, not an independent LuceonWeb final-review node. Emit `final_review_report.md` and `final_review_report.json` with status `passed`, `needs_fix`, or `blocked`, plus rendered-page evidence and hard-gate findings. Also emit `page_review.json` as `{pdf_sha256,page_count,pages:[{page,image,status,findings}]}` for the final compiled PDF. The main skill decides whether the run is publishable and writes the `eduassets-elegantbook` manifest.

## Required Review Areas

Read `references/final-review-checklist.md` for the detailed delivery checklist.

1. Front matter:
   - cover title, course/grade/edition, formal tone, no internal notes;
   - course description matches actual body;
   - TOC entries and page numbers match rendered body pages.

2. Body content:
   - no missing stems, subparts, options, answer areas, examples, tables, or figures;
   - every visible written-response prompt has adequate printable answer room before the next task or chapter;
   - concepts/examples/practice align pedagogically;
   - bilingual text follows edition policy.

3. Visual/layout:
   - no clipping, overflow, orphaned headings, broken MathJax, broken images, or visible debug text;
   - no chapter title overlaps the preceding body line or starts too late to carry useful chapter content;
   - no low-resolution photo is enlarged into a dominant print area when a smaller faithful placement is possible;
   - diagrams have correct semantic alignment, not just sharp rendering.
   - no figure is followed by an OCR label dump that duplicates words already visible in the figure;
   - a teaching figure plus ruled answer lines is accepted as a structured response page even when text extraction is sparse.

4. Completeness and provenance:
   - every source teaching item has both its heading and substantive tail in the final PDF;
   - the last chapter is fully flushed before document end;
   - all page renders exist and their ledger SHA-256/page count match the exact final PDF.

5. Answers and editions:
   - teacher edition includes answers/explanations as intended;
   - student edition hides/removes answers and hidden support text correctly;
   - page counts and front matter differ only as expected.

## Workflow

1. Build a review pack: text extraction, page images, HTML image refs, low-text pages, and editing-trace scan.
2. Inspect cover, course-description, TOC, first body page, answer sections, and all changed/suspect pages.
3. For true final review, render and inspect every page; unit/chapter sampling alone is insufficient for a publishable Luceon artifact.
4. Report `pass`, `revise_before_delivery`, or `needs_source_confirmation`.
5. If fixes are approved, regenerate outputs and re-review changed pages plus TOC/page-number affected pages.

## Related Scripts

Use `teaching-handout-final-review/scripts/build_review_pack.py` until a dedicated final-review script is migrated here.
