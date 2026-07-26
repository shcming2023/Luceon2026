# Rebuild Contract

## Output Standard

The output is a clean body-only content master for later teaching-material production, question extraction, and handout creation. Prefer completeness of the main instructional content, traceability, and plain structure over decorative formatting.

## Keep

- Main chapter/unit/section headings in source order.
- Reading passages, grammar explanations, examples, notes, sidebars, summaries, reviews, appendices, glossary, index.
- Exercises, prompts, blanks, choices, answer lines, listening references, and "about you" activities.
- Tables with row/column relationships.
- Formulas and symbols in LaTeX when possible.
- Figures, photos, diagrams, captions, and source footnotes.
- Page footnotes, converted to nearby Markdown footnotes or local footnote blocks.

## Remove

- Covers, title pages, copyright pages, table of contents, credits, acknowledgments, reviewers, ads, resource catalog pages, appendices, glossary, index, notes pages, and back cover unless the user explicitly asks to keep them.
- Repeated copyright footers and running headers.
- Standalone page numbers and repeated page labels.
- OCR control characters, empty headings, duplicate image-only artifacts, and layout-only fragments.
- Decorative color/position signals unless they identify a semantic class such as note, warning, grammar box, caption, sidebar, or exercise.

## Markdown Rules

- Use heading levels for source hierarchy, not for visual size.
- Use normal paragraphs for recombined multi-column prose.
- Use ordered/unordered lists only when the source has list semantics.
- Use HTML tables for complex tables with merged cells.
- Keep source text distinct from editorial QA notes. Put QA notes in the report, not inline, unless marking an unavoidable uncertainty.
- Use relative image links: `![caption](images/name.jpg)`.
- Keep filenames stable and ASCII-safe when creating new output assets.
- Generate `preview.html` beside `clean.md` for human inspection; it is a QA view, not a replacement for the Markdown master.

## Image Policy

Default to source-faithful images. Do not alter instructional content through beautification.

When source images are low quality:

1. Prefer re-cropping from the original PDF at higher DPI using known bbox/page data.
2. If enhancement is necessary, keep original and enhanced copies and document which one is linked.
3. Never use generated replacement images for textbook figures unless the user explicitly asks for illustrative substitutes.

## Validation Checklist

- Confirm source page count and final section coverage.
- Reconcile counts for text blocks, images, tables, formulas, footnotes, and dropped noise blocks.
- Sample at least: title/TOC pages, one reading passage, one grammar table, one exercise-heavy page, one image-heavy page, one appendix/glossary/index page.
- Verify every Markdown image link resolves inside the final output folder.
- Record unreferenced source images separately; do not delete original extraction assets.
