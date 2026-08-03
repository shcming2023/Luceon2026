# Luceon Codex ElegantBook Manifest Contract

Use this contract for every object published by `luceon-popo-to-refined-elegantbook`.

## Target

```text
bucket: eduassets-elegantbook
prefix: elegantbook/<material_id>/<popo_run_id>/<codex_run_id>/
manifest: elegantbook/<material_id>/<popo_run_id>/<codex_run_id>/manifest.json
```

`codex_run_id` must be unique for a production attempt. Use a timestamp or deterministic run id such as `codex-YYYYMMDD-HHMMSS-<short-hash>`.

## Required Manifest

```json
{
  "schema": "luceon-codex-elegantbook/v1",
  "stage": "elegantbook",
  "origin": "codex_refined",
  "material_id": "pdf-...",
  "popo_run_id": "popo-...",
  "codex_run_id": "codex-...",
  "created_at": "2026-07-07T00:00:00Z",
  "updated_at": "2026-07-07T00:00:00Z",
  "source": {
    "input_pdf": {
      "bucket": "eduassets-input",
      "object": "..."
    },
    "mineru_manifest": {
      "bucket": "eduassets-mineru",
      "object": "..."
    },
    "popo_manifest": {
      "bucket": "eduassets-minerupopo",
      "object": "..."
    },
    "legacy_latex_manifest": {
      "bucket": "eduassets-latex",
      "object": "latex/<material_id>/<popo_run_id>/manifest.json",
      "optional": true
    }
  },
  "objects": {
    "compiled_pdf": "compiled.pdf",
    "refined_overleaf_zip": "refined-overleaf.zip",
    "main_tex": "main.tex",
    "main_fallback_tex": "main-fallback.tex",
    "chapters_dir": "chapters/",
    "images_dir": "images/",
    "compile_report": "compile_report.json",
    "latex_polish_report": "latex_polish_report.md",
    "latex_polish_report_json": "latex_polish_report.json",
    "final_review_report": "final_review_report.md",
    "final_review_report_json": "final_review_report.json",
    "render_review": "render_review.md",
    "render_review_json": "render_review.json",
    "decision_log": "decision_log.json",
    "model_calls": "model_calls.jsonl",
    "run_state": "run_state.json",
    "source_trace": "source_trace.json",
    "source_outline_ledger": "source_outline_ledger.json",
    "page_review": "page_review.md",
    "page_review_json": "page_review.json",
    "worker_quality_report": "worker_quality_report.json"
  },
  "compile": {
    "status": "succeeded",
    "engine": "xelatex",
    "pages": 1
  },
  "qa": {
    "status": "passed",
    "hard_blockers": [],
    "review_status": "passed"
  },
  "stages": [
    {"id": "01-clean-markdown", "skill": "pdf-clean-markdown-rebuild", "status": "passed"},
    {"id": "02-semantic-annotation", "skill": "material-semantic-annotator", "status": "passed"},
    {"id": "03-elegantbook", "skill": "cleanlatex-to-elegantbook", "status": "passed"},
    {"id": "03.5-refine", "skill": "refine-elegantbook-latex", "status": "passed"},
    {"id": "04-final-review", "skill": "finished-textbook-final-review", "status": "passed"}
  ]
}
```

## Status Values

Use these values consistently:

- `origin`: `codex_refined` for the normal 03.5-refined output; `codex_elegantbook` only for explicit unrefined diagnostics.
- `qa.status`: `passed`, `needs_fix`, or `blocked`.
- stage `status`: `passed`, `needs_fix`, `blocked`, `failed`, or `skipped`.

Do not publish `origin=legacy_selfloop` into `eduassets-elegantbook`; legacy outputs stay in `eduassets-latex`.

## Object Rules

- Paths inside `objects` are relative to the manifest prefix.
- Include all files referenced by the manifest before uploading `manifest.json`.
- Use `compiled.pdf` for the PDF LuceonWeb should display.
- Use `refined-overleaf.zip` for the ZIP LuceonWeb should offer for manual Overleaf import.
- Keep reports in Markdown and JSON pairs when a stage has both human and machine-readable evidence.
- `page_review.json` must bind every real page render to the final `compiled.pdf` SHA-256 and page count.
- `worker_quality_report.json` must be `passed` and must show no page-flush suppression, chapter collision/late start, missing source tail, visible editorial residue, or stale render evidence.
- If an optional report is genuinely missing, omit that key or set the stage to `needs_fix`; do not point to a nonexistent object.

## Publish Order

1. Upload project files and reports.
2. Verify object existence and nonzero sizes.
3. Verify `compiled.pdf` starts with `%PDF`.
4. Verify `refined-overleaf.zip` starts with `PK`.
5. Upload `manifest.json` last.

This keeps LuceonWeb from indexing a half-published run.
