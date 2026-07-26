---
name: pdf-clean-markdown-rebuild
description: Rebuild local MinIO eduassets MinerU-Popo tasks or MinerU/PDF extraction folders into faithful, clean body-only Markdown master files, an images folder, an HTML preview, and QA reports. Use when Codex needs to turn eduassets-minerupopo plus eduassets-mineru/input assets, textbook PDFs, scanned or mixed-layout PDFs, or existing full.md/content_list/layout.json/image artifacts into downstream-ready semantic master material, then publish validated outputs to eduassets-raw as the starting point for later eduassets-clean cleaning and annotation.
---

# PDF Clean Markdown Rebuild

## ElegantBookCompiler formal-v1 boundary

When this skill is used inside the formal ElegantBookCompiler v1 pipeline, it is
only a read-only intake and source-evidence reconstruction adapter. The original
PDF plus complete MinerU and MinerU-Popo provenance remain the required inputs,
and the canonical Spec 01/02 ledger and decision index owned by
`luceon-popo-to-refined-elegantbook` are the only downstream authority.

In this formal mode:

- `clean.md`, an outline, or any rebuilt Markdown is an evidence view, never a
  replacement for a closed canonical ledger or decision index;
- do not publish to MinIO, mutate upstream objects, invoke LLM review, apply an
  outline suggestion, or silently repair source content;
- do not emit authoritative Spec 03/04 decisions or override a promoted stage;
- unresolved evidence must stop as `blocked` or `needs_review` and return to the
  owning Spec 01/02 stage.

These restrictions do not remove the skill's separately authorized
eduassets-raw rebuild workflow; they apply whenever the caller declares the
ElegantBookCompiler formal-v1 contract.

## Goal

Create a faithful body-only content master, not a visual clone. Preserve main chapter content and logical structure; discard covers, copyright pages, TOC/front matter, appendices/back matter, decorative layout, repeated boilerplate, page headers, page footers, and page numbers unless the user asks for them.

Default production source is local MinIO, not an ad hoc folder. Treat bucket roles as:

- `eduassets-input`: original PDFs and `_status/` task/source indexes.
- `eduassets-mineru`: canonical MinerU structured assets and source evidence.
- `eduassets-minerupopo`: Popo post-processed task output; this is the semantic rebuild entry point.
- `eduassets-raw`: semantic-rebuild clean master output layer; this is the input to later `eduassets-clean`.
- `eduassets-clean`: later cleaning, annotation, question extraction, and handout-production output layer. This skill may read it for downstream-state checks, but must not write this skill's raw master outputs there.

Pipeline boundary is strict:

- This skill starts after MinerU-Popo has produced upstream artifacts and moves toward `eduassets-raw`.
- Treat the original PDF as the pipeline's primary material identity. Show the source PDF name first in user-facing task menus; use `pdf_id`, `job_id`, source hash, and bucket prefixes as trace keys.
- For staged/recovery first-stage assets, `job_id` in this skill means the
  MinerU-Popo run id that will become the `eduassets-raw` run id. The upstream
  MinerU run id may differ. Discovery and materialization must resolve the
  upstream MinerU asset by `material_id` and Popo manifest lineage
  (`upstream_mineru`, `stage_run_ids.mineru`, or manifest object), not by
  assuming `mineru_job_id == popo_job_id`.
- Every output in `eduassets-raw` must remain traceable back to the source PDF and upstream MinerU-Popo/MinerU task.
- It may read `eduassets-input`, `eduassets-mineru`, and `eduassets-minerupopo`.
- It must never write, delete, rename, mirror, overwrite, tag, or otherwise mutate `eduassets-input`, `eduassets-mineru`, or `eduassets-minerupopo`.
- It may read downstream buckets such as `eduassets-clean` for pipeline-state checks.
- It must never write, delete, rename, mirror, overwrite, tag, or otherwise mutate downstream buckets such as `eduassets-clean`.
- The only MinIO bucket this skill may edit is `eduassets-raw`, and only after explicit user confirmation for the selected task scope.
- If a command would use `mc cp`, `mc mirror`, `mc rm`, or any write-like MinIO operation against a non-`eduassets-raw` bucket, stop immediately and report the unsafe target.

### Luceon Refined ElegantBook Mode

When this skill is invoked by `luceon-popo-to-refined-elegantbook`, it is an internal local stage only:

- Produce `body-final/clean.md`, `images/`, QA reports, and `source_trace.json` inside the isolated Codex run workspace.
- Do not publish, mirror, or update `eduassets-raw`.
- Do not mutate `eduassets-input`, `eduassets-mineru`, `eduassets-minerupopo`, `eduassets-latex`, `eduassets-clean`, or `eduassets-standard`.
- Hand local artifacts back to the main skill; the main skill alone publishes final objects to `eduassets-elegantbook`.

Final deliverables should normally be:

- `clean.md`: UTF-8 Markdown with stable headings, body text, tables, formulas, footnotes, and `images/...` links.
- `images/`: only images referenced by `clean.md`, copied or re-cropped from the source.
- `preview.html`: lightweight HTML rendering of the Markdown for visual inspection, with LaTeX math rendered by MathJax when network access is available.
- `outline-view.html` / `outline-anchor-check.html`: two-pane outline QA view; visibly show the original source PDF filename near the top for quick lookup; clicking the left Markdown outline jumps to the exact heading insertion anchor in `clean.md` on the right, and each item shows the line range covered by that heading's chunk.
- `qa_report.md` or `manifest.json`: counts, omissions, uncertain OCR, image/table decisions, and representative validation notes.
- `final_acceptance_gate.md` / `final_acceptance_gate.json`, `objective_completion_audit.md` / `objective_completion_audit.html`, `regression_stability_snapshot.json`, `outline_inventory.md` / `outline_inventory.html`, `outline_inventory_link_audit.md`, `human_review_status.md` / `human_review_status.html`, `manual_anchor_spotcheck.md` / `manual_anchor_spotcheck.html`, `manual_anchor_link_audit.md`, `manual_review_decision_sheet.md` / `manual_review_decision_sheet.html`, `manual_review_command_sheet.md`, `pending_manual_fact_review_summary.md`, `pending_review_queue.md` / `pending_review_queue.html`, `pending_review_link_audit.md`, and `manual_review_status_template.json` during regression: human-readable and machine-readable final acceptance gate, requirement-by-requirement objective completion audit, anti-regression stability snapshot, full outline inventory for checking directory completeness/levels/chunk ranges, full outline link audit, manual acceptance ledger, representative anchor spotcheck links, spotcheck link audit, human approval decision sheet, exact manual-status update command sheet, pending fact summary, prioritized review queue, and queue-link audit that stay separate from mechanical QA, so samples are only treated as accepted after recorded human spot-check of the outline tree and clean.md anchors.
- Regression reports must distinguish `passed_count` mechanical QA from `accepted_count` human review. Treat `fully_accepted=true` as the 12-sample acceptance signal; `passed_count=tested_count` alone is not enough.

## Task State And Modes

Treat task selection as a small stateful scheduler, not as a one-shot file scan. There are two state layers:

- Source readiness: whether `eduassets-minerupopo` has a Popo task, `eduassets-mineru` has canonical MinerU assets, and `eduassets-input/_status` marks the upstream task as done.
- Rebuild state: whether this skill has produced, reviewed, and published a semantic master in `eduassets-raw`.

Use these rebuild states:

- `not_started`: source is ready, but no `eduassets-raw` output or rebuild status exists.
- `running`: a rebuild attempt is in progress. If it is older than the current session and no active process is known, treat as `stale_running` and ask before retrying.
- `failed`: a previous rebuild attempt failed before validated raw publish.
- `needs_review`: artifacts exist locally or in raw, but QA/manual review did not pass.
- `published`: validated semantic master exists in `eduassets-raw`.
- `blocked`: source task is not rebuildable because Popo, MinerU, or upstream done status is missing.

Mode semantics:

- All rebuild: rebuild every source-ready task, including `published` tasks. Use only for rule upgrades or full regression. Confirm overwrite/version behavior before publishing.
- Continue rebuild: process `not_started`, `failed`, `needs_review`, and `stale_running` tasks, but skip `published` tasks unless the user explicitly includes them.
- Retry failed: process only `failed` and user-approved `stale_running` tasks.
- Selected rebuild: process explicit `pdf_id/job_id` tasks, regardless of state, after showing each selected task's source readiness and rebuild state.
- Random test rebuild: choose one task from `not_started` by default; allow `--include-published` only for regression testing.
- Discover only: report source readiness and rebuild state without materializing, rebuilding, or publishing.
- Local folder rebuild: bypass MinIO scheduling, but still produce local run state and QA artifacts.

Do not call a mode "continue" unless the selected set is derived from recorded state. Do not call a mode "retry" unless previous failure/review/running state is visible and reported.

## Workflow

1. When the skill starts without an explicit task selection, show a task-mode prompt first:
   - Run `scripts/task_prompt.py`.
   - Present the available modes to the user: all rebuild, continue rebuild, retry failed, selected rebuild, random test rebuild, discover only, or local-folder rebuild.
   - After the user chooses, restate the selected scope, whether outputs will be written to `eduassets-raw`, and wait for confirmation before executing rebuild or publish steps.
   - If the user already gave an explicit mode and task IDs, skip the menu but still confirm any write to `eduassets-raw`.
2. For local MinIO workflows, discover tasks from `eduassets-minerupopo` first:
   - Run `scripts/discover_minio_tasks.py`.
   - Present task choices by source PDF name, not only by `pdf_id/job_id`.
   - Pick representative tasks during skill testing; do not hardcode task IDs or tune rules for one title.
   - Confirm each selected task has corresponding `eduassets-mineru` assets and `eduassets-input/_status` evidence.
3. Materialize the selected task:
   - Run `scripts/materialize_minio_task.py --pdf-id ... --job-id ... --out-dir ...`.
   - Use the generated `rebuild_input/` as the input folder for the existing rebuild pipeline.
   - Keep `source_trace.json` with the final QA artifacts.
4. For direct local-folder workflows, inspect the extraction folder before rebuilding. Prefer `scripts/inspect_mineru_project.py` when MinerU-style files are present.
5. Read `references/rebuild-contract.md` before designing the output rules for textbooks, worksheets, tables, exercises, or image-heavy books.
6. Build a canonical block stream from structured data first:
   - Prefer `*_content_list.json` for flat content blocks.
   - Use `content_list_v2.json` for page-grouped blocks.
   - Use `layout.json` to separate `para_blocks` from discarded headers, footers, page numbers, and footnotes.
   - Treat `full.md` as a readable preview, not as the source of truth.
7. Set the content scope:
   - Default to body-only chapters/units.
   - Exclude front matter: cover, title, copyright, contents, credits, acknowledgments, reviewers, resource ads.
   - Treat pages made mostly of TOC entries such as chapter/unit titles followed by page numbers or dot leaders as front matter even when the page lacks an explicit "Contents" or "目录" title.
   - Exclude back matter: appendices, glossary, index, notes, back cover.
   - When a reliable Popo/TOC outline spans beyond heuristic body-end detection, use the outline/back-matter evidence to protect the full body range. Do not let an in-body lowercase word such as `index` truncate a textbook before the final listed unit/topic.
   - Back-matter detection must not truncate before the last reliable TOC/outline anchor. If an early body title looks like `Answers`, `Index`, or a similar back-matter marker but the source outline still has later anchored chapters/units/topics, keep the body range through the later outline evidence and flag uncertainty instead of cutting early.
   - If the selected source PDF is itself a short excerpt whose primary source-visible title is an appendix or other back-matter label, do not silently drop the whole task. Preserve it as the task's body only when the source evidence shows the excerpt has no preceding main chapters in the selected PDF, and flag it for manual body-scope review such as `appendix_as_primary_title_manual_decision`.
   - Use manual `--start-page` and `--end-page` when automatic boundaries are uncertain.
8. Remove noise conservatively:
   - Drop repeated copyright footers, running headers, page numbers, and OCR artifacts.
   - Keep source footnotes and captions.
   - Never drop a table, image, exercise, formula, appendix, glossary, or index item without recording it in QA.
9. Rebuild semantic hierarchy:
   - Infer real levels from TOC, numbering patterns, page order, and repeated unit/chapter patterns.
   - Do not trust OCR heading levels blindly; MinerU often marks many unrelated headings as level 1.
   - Treat rule-generated missing headings as candidates, not facts. If MinerU/MinerU-Popo evidence lacks a visible TOC/body anchor for a suspected missing chapter, unit, or section, do not silently fill it with rules.
   - Missing-outline recovery must use one of two explicit evidence paths: visual LLM review of the corresponding PDF page(s), or reasoning LLM validation against available Popo/MinerU evidence. If neither path validates the candidate, keep the text in the body and mark the outline gap for QA instead of promoting it to a Markdown heading.
   - Output a stable directory with at most 3 heading levels. Treat any semantic structure deeper than level 3 as body content located between the nearest valid headings, not as Markdown headings.
   - Preserve source TOC category containers when they are explicit or layout-recoverable, even if they have no printed page number. For unit-based textbooks, use `Unit` as H1, category rows such as `Number`, `Algebra`, `Shape and Space`, and `Probability and Statistics` as H2, and listed sections as H3.
   - Do not apply a subject-specific TOC category sequence to other Unit-based materials unless the early TOC pages contain real category-row evidence for that sequence. If a source only lists `Unit N Benchmark Test` / `Unit N Unit Test` with skill rows, keep those skills directly under the Unit or source-visible test parent rather than inventing math categories.
   - For flat multi-column Unit TOCs without explicit category rows, preserve the source/content stream order, carry the active Unit across page breaks, and filter credit/copyright/image-source blocks before extracting TOC topics. Do not reorder entries solely by visual top coordinate when columns interleave.
   - Source-visible numbered TOC heading rows such as `1 Algebra 1` or prefixed rows such as `P3 7 Further algebra 165` may become chapter parents only when the row itself is a TOC heading candidate and can be anchored by chapter number or title in the body. Do not promote ordinary child rows such as `1 Review exercise...` into chapters.
   - When a Popo source tree exposes strong structural headings with child sections and no trustworthy TOC exists, treat those headings as source-tree evidence. Merge adjacent split heading fragments on the same page/depth before emitting the directory, and reserve LLM/visual validation for synthetic or weak missing-heading recovery.
   - Ensure the output has a real top-level structure. For standalone short articles or excerpts with no chapter/unit pattern, promote the primary source title to H1 instead of leaving the document with only H2/H3 headings.
   - For chapter/unit textbooks, protect the source-visible hierarchy: chapter headings are top-level, real unit openers are second-level, numbered sub-sections such as `11.1` are below the unit, and local labels such as tips, key terms, article titles, questions, and exercise headings must not pollute the chapter/unit directory.
   - Preserve local teaching labels such as tips, hints, key terms, practice prompts, self-checks, review reflections, speakers, steps, table headers, and numbered list prompts as body text rather than directory headings.
   - When a TOC child entry has a parent chapter/unit, its Markdown anchor must fall inside that parent's body range. If OCR page numbers place the child outside the parent range, prefer a source-visible title match inside the parent range; otherwise keep the candidate out of the directory and flag it for QA.
   - Do not let short normalized tokens drive fuzzy heading matches. A one-letter or very short heading may be body text or an answer choice, and must not satisfy a long TOC title by substring containment.
   - Strong workbook codes such as `P1`, `P1.01`, `A2.03`, or comparable chapter/topic numbering may be used to recover missing body headings from source-visible text, but ordinary `Exercise`, `Activity`, or local prompt labels remain body content unless the stable code pattern proves they are part of the source directory.
   - When the body contains `Unit N Title` evidence, validate that Chapter-internal H2 headings match true unit titles; otherwise the output is not safe for downstream chunking.
   - Preserve page provenance with comments or manifest entries when traceability matters.
10. Use LLM refinement when heading semantics are ambiguous:
   - Prefer DeepSeek via `DEEPSEEK_API_KEY` when available.
   - Before ordinary outline review, run `scripts/deepseek_outline_candidate_validate.py` for `validation_required` outline candidates, then run `scripts/apply_outline_candidate_validation.py`. Accepted or revised candidates may remain in the outline; rejected candidates and candidates marked `needs_visual_review` must be removed from the directory layer or demoted to body text.
   - When DeepSeek marks a candidate as `needs_visual_review`, use HY Vision through `HY_VISION_API_KEY` / `HY_VISION_BASE_URL` / `HY_VISION_MODEL` and `scripts/hyvision_outline_candidate_validate.py` to inspect the corresponding original PDF page image. Visual validation may accept, reject, or revise the candidate title, but it must cite page-image evidence and must not invent textbook content.
   - Run `scripts/deepseek_outline_review.py` on the draft Markdown.
   - For long books, use compacted batch review rather than sending the whole book as one unbounded request. Keep per-request and total subprocess timeouts enabled.
   - Treat the LLM output as a structured review plan, not as rewritten source text.
   - Only apply changes that reference existing line numbers/block evidence.
11. Handle tables and formulas by fidelity:
   - Use Markdown tables only for simple, rectangular tables.
   - Keep complex tables as HTML `<table>` with `rowspan`/`colspan`.
   - Preserve LaTeX inline/display formulas as `$...$` or `$$...$$`; flag uncertain OCR.
12. Handle images by source fidelity:
   - Copy only images referenced by the final Markdown into the output `images/`.
   - Copy images referenced inside HTML table `<img src="images/...">` as required deliverables too.
   - Do not AI-enhance by default.
   - If quality is poor, prefer high-DPI re-cropping from the original PDF by bbox; keep enhancement or re-crop decisions in QA.
13. Validate against the source:
   - Compare block counts, image counts, table counts, and sampled pages.
   - Check reading order on multi-column pages.
   - Check exercise blanks, answer choices, footnotes, tables, formulas, appendices, glossary, and index.
14. For skill regression across MinIO samples, run `scripts/regression_minio_tasks.py`:
   - It materializes tasks, rebuilds locally, optionally runs DeepSeek review, applies safe edits, and writes a local regression report.
   - It never publishes to MinIO.
   - It writes a regression review index and acceptance artifacts: `review-index.html`, `final_acceptance_gate.md`, `final_acceptance_gate.json`, `objective_completion_audit.md`, `objective_completion_audit.html`, `regression_stability_snapshot.json`, `outline_inventory.md`, `outline_inventory.html`, `outline_inventory_link_audit.md`, `acceptance_audit.md`, `outline_fact_reconciliation.md`, `body_scope_audit.md`, `anchor_integrity_audit.md`, `manual_anchor_link_audit.md`, `manual_anchor_spotcheck.md`, `manual_anchor_spotcheck.html`, `manual_review_checklist.md`, `manual_review_decision_sheet.md`, `manual_review_decision_sheet.html`, `manual_review_command_sheet.md`, `pending_manual_fact_review_summary.md`, `human_review_status.md`, `human_review_status.html`, `pending_review_queue.md`, `pending_review_queue.html`, `pending_review_link_audit.md`, and `manual_review_status_template.json`.
   - `objective_completion_audit.html` maps the active 12-sample acceptance objective to concrete evidence and remaining work. Use it before claiming completion; if any row is `NOT PROVED`, the goal is still open.
   - `manual_anchor_spotcheck.html` is the preferred human spot-check work surface when sampling anchors. It links directly into each sample's two-pane `outline-view.html` at representative `clean.md` heading anchors, including parent-intro boundaries, first/middle/last headings, H3 headings when present, and review/practice/assessment-style boundaries. Use it to quickly inspect whether directory anchors preserve intro text and chunk boundaries before marking rows approved.
   - It must report both mechanical pass/fail and human acceptance progress. Do not call the regression accepted until mechanical QA has no false gates, full outline inventory links are valid, regression stability comparison passes, every selected sample is marked human `approved`, and `final_acceptance_gate.md` says `ACCEPTED`.
   - For automation or final handoff, prefer `scripts/finalize_regression_review.py <regression-root> --baseline-root <baseline-root> --manual-review-status <ledger>` after any manual-status update. It refreshes artifacts, runs stability comparison when a baseline is provided, refreshes the final gate again, and then runs `scripts/assert_final_acceptance.py`. The final assertion must exit 0 before the 12-sample regression can be considered accepted; a nonzero exit means the active goal is still incomplete, and the assertion output includes the next artifact or command path to inspect.
   - After the user explicitly approves or rejects reviewed samples, update the local manual review ledger with `scripts/update_manual_review_status.py`; match by `pdf_id`, `job_id`, or a unique PDF name fragment. If using numeric selectors from the pending review queue or pending fact summary, pass `--row-scope pending --regression-root <regression-root>` so the row number matches the review page order instead of raw ledger order. Numeric selectors may be repeated or batched as `--select 1,2,3`, `--select 1-3`, or `--select 1、2、3` when the user explicitly approves or rejects multiple pending rows. If only human status changed, refresh local review artifacts with `scripts/refresh_regression_review_artifacts.py`; after rebuild-rule changes, rerun full regression with `--manual-review-status`.
   - Its QA gates include required files, image closure, MathJax preview support, heading sanity, H1 density, and DeepSeek review errors when LLM review is enabled.
   - Its QA gates must require the outline anchor QA view (`outline-view.html` or `outline-anchor-check.html`) so later sample checks can inspect both directory correctness and insertion-anchor correctness.
   - Its QA gates must fail if the complete canonical outline evidence is not fully emitted into the final Markdown/`popo_outline.json`; a small emitted prefix must not pass merely because the preview and anchors are internally consistent.
   - Its hierarchy QA must also catch chapter/unit pollution: for books with visible `Unit N Title` evidence, Chapter-internal H2 headings should be true Unit titles, not tips, exercises, article names, or numbered sections.
   - Its hierarchy QA must fail if `clean.md` contains heading levels deeper than H3.
   - Its hierarchy QA must fail if `clean.md` has headings but no H1 top-level structure.
   - Its hierarchy QA must fail if headings jump levels, such as H1 directly followed by H3 without an H2 parent.
   - Its hierarchy QA must fail if any leaf heading owns an empty chunk; container headings may have child sections, but a terminal directory node must contain body/table/image/formula content.
   - If a source-visible terminal heading corresponds to an intentionally blank response area or OCR-empty source page, preserve the heading and add a minimal provenance comment such as `source_empty_chunk` instead of deleting the heading or inventing textbook text.
   - Its hierarchy QA must fail if generic `Chapter N. Topic M` markers are promoted to Markdown headings; keep those as page/layout evidence or body text unless a meaningful source title is validated.
   - Its hierarchy QA must fail when the same parent unit/chapter contains duplicate numbered topic headings, such as two `Topic 5` children, because this usually indicates a misplaced OCR page anchor or parent-range violation.
   - Its QA should flag unusually dense H2 structures for review, because dense textbook/workbook outputs may still pass hard gates while containing column labels or activity boxes that should be body content rather than directory nodes.
   - Use it after every generalized rule change to verify that previously passing samples still pass.
   - After any generalized rule change, compare the previous accepted or working regression root with the new root using `scripts/compare_regression_stability.py <baseline-root> <current-root> --out-md <current-root>/regression_stability_comparison.md --out-json <current-root>/regression_stability_comparison.json`. Treat mechanical QA loss, anchor failure, new missing images, and heading-signature changes on already human-approved samples as blocking regressions.
15. Publish only validated semantic master outputs to `eduassets-raw`:
   - Run `scripts/publish_raw_to_minio.py /path/to/body-final --pdf-id ... --job-id ...`.
   - Default target prefix is `eduassets-raw/raw/<pdf-id>/<job-id>/`.
   - Do not publish to `eduassets-clean`; that bucket is read-only for this skill and is for later cleaning/annotation outputs.
   - Never publish, repair, or backfill anything into `eduassets-input`, `eduassets-mineru`, or `eduassets-minerupopo`.

## Script Usage

For local MinIO eduassets tasks, run from this skill's release directory:

```bash
python3 scripts/task_prompt.py
python3 scripts/discover_minio_tasks.py --out /tmp/pdf-clean-md/tasks.json
python3 scripts/materialize_minio_task.py --pdf-id pdf-... --job-id job-... --out-dir /tmp/pdf-clean-md/task --force
# If a historical task lacks manifest lineage, pass --mineru-job-id explicitly
# after confirming the upstream MinerU run under eduassets-mineru/mineru/<pdf-id>/.
python3 scripts/bootstrap_clean_markdown.py /tmp/pdf-clean-md/task/rebuild_input --out-dir /tmp/pdf-clean-md/task/body-final
python3 scripts/deepseek_outline_candidate_validate.py /tmp/pdf-clean-md/task/rebuild_input /tmp/pdf-clean-md/task/body-final/popo_outline.json --out /tmp/pdf-clean-md/task/body-final/outline_candidate_validation.json
python3 scripts/hyvision_outline_candidate_validate.py /tmp/pdf-clean-md/task/rebuild_input /tmp/pdf-clean-md/task/body-final/popo_outline.json /tmp/pdf-clean-md/task/body-final/outline_candidate_validation.json
python3 scripts/apply_outline_candidate_validation.py /tmp/pdf-clean-md/task/body-final
python3 scripts/deepseek_outline_review.py /tmp/pdf-clean-md/task/body-final/clean.md --out /tmp/pdf-clean-md/task/body-final/outline_llm_review.json
python3 scripts/apply_outline_review.py /tmp/pdf-clean-md/task/body-final/clean.md /tmp/pdf-clean-md/task/body-final/outline_llm_review.json --out-md /tmp/pdf-clean-md/task/body-final/clean.md --out-html /tmp/pdf-clean-md/task/body-final/preview.html
cp /tmp/pdf-clean-md/task/source_trace.json /tmp/pdf-clean-md/task/body-final/source_trace.json
python3 scripts/publish_raw_to_minio.py /tmp/pdf-clean-md/task/body-final --pdf-id pdf-... --job-id job-...
```

For local MinIO regression without publishing, run:

```bash
python3 scripts/regression_minio_tasks.py --out-root /tmp/pdf-clean-md-regression --with-deepseek --with-hyvision --deepseek-max-headings-per-unit 40 --deepseek-batch-size 4 --manual-review-status /path/to/manual-review-status.json --force
python3 scripts/update_manual_review_status.py /tmp/pdf-clean-md-regression/manual_review_status_template.json --status approved --select 2 --row-scope pending --regression-root /tmp/pdf-clean-md-regression --notes "User spot-checked outline tree and clean.md anchors."
python3 scripts/update_manual_review_status.py /tmp/pdf-clean-md-regression/manual_review_status_template.json --status approved --select 1-3 --row-scope pending --regression-root /tmp/pdf-clean-md-regression --notes "User spot-checked outline tree and clean.md anchors."
python3 scripts/update_manual_review_status.py /tmp/pdf-clean-md-regression/manual_review_status_template.json --status needs_fix --select "unique pdf name fragment" --notes "User found outline or anchor boundary issues."
python3 scripts/refresh_regression_review_artifacts.py /tmp/pdf-clean-md-regression --manual-review-status /tmp/pdf-clean-md-regression/manual_review_status_template.json
python3 scripts/compare_regression_stability.py /tmp/pdf-clean-md-baseline /tmp/pdf-clean-md-regression --out-md /tmp/pdf-clean-md-regression/regression_stability_comparison.md --out-json /tmp/pdf-clean-md-regression/regression_stability_comparison.json
python3 scripts/assert_final_acceptance.py /tmp/pdf-clean-md-regression
python3 scripts/finalize_regression_review.py /tmp/pdf-clean-md-regression --baseline-root /tmp/pdf-clean-md-baseline --manual-review-status /tmp/pdf-clean-md-regression/manual_review_status_template.json
```

For local MinerU-style folders, run:

```bash
python3 scripts/inspect_mineru_project.py /path/to/extraction-folder --out /path/to/report.json
python3 scripts/bootstrap_clean_markdown.py /path/to/extraction-folder --out-dir /path/to/rebuild
python3 scripts/deepseek_outline_review.py /path/to/rebuild/clean.md --out /path/to/rebuild/outline_llm_review.json
python3 scripts/apply_outline_review.py /path/to/rebuild/clean.md /path/to/rebuild/outline_llm_review.json --out-md /path/to/rebuild/clean.md --out-html /path/to/rebuild/preview.html
```

The bootstrap script creates `clean.md`, `preview.html`, `manifest.json`, and `qa_report.md`. It defaults to `--scope body`; use `--scope all` only when the user asks for front/back matter. Use `--link-images` for fast local HTML preview on cloud-synced folders, `--no-copy-images` only for structure-only dry runs, and no image flag for final deliverables that physically copy images. Treat the draft as the first pass, then refine headings, exercise structure, complex tables, and image decisions according to the contract.

For sample review and regression, always open `outline-view.html` first. It must show a clickable outline on the left and the rendered `clean.md` text on the right; each outline entry must jump to the actual inserted Markdown heading anchor and display the heading's line range/chunk boundary.

The DeepSeek review script emits JSON suggestions for heading moves, level fixes, split-heading merges, and noise headings. For long documents it should batch compact Unit/Chapter heading trees with `--batch-document`, `--batch-size`, and `--max-headings-per-unit`; if a batch response fails JSON parsing, the script falls back to smaller unit-level requests and records any remaining errors. Do not let LLM free-write textbook content; apply only evidence-backed structural edits.

The apply script only auto-applies safe structural changes: heading level fixes, heading noise deletion, and adjacent split-heading merges. It records skipped OCR corrections, missing headings, and unit-heading moves in `outline_apply_report.json` for manual review. For short non-Unit handouts or topic packets, pass `--allow-nonunit-h1` only after confirming the whole document has no true Unit/chapter H1 pattern and the LLM is assigning source headings to top-level modules.

The publish script writes semantic rebuild outputs to `eduassets-raw`. Use it only after validation passes and the QA report records any manual-review gaps.

## Stop Conditions

Do not present the rebuild as final if:

- Major table/image/formula counts do not reconcile.
- The output was produced only from `full.md` when structured JSON exists.
- Repeated page noise remains throughout the document.
- Front matter or appendices/back matter remain in a body-only deliverable.
- OCR blanks, exercise prompts, or table columns are visibly corrupted and unflagged.
- The final Markdown references images not present in the final `images/` folder.
