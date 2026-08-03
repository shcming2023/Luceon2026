# Spec 05 final-page provenance and Spec 06 review

## Authority boundary

The candidate-PDF-page to source mapping is deterministic evidence. OCR,
text similarity, filenames, material IDs, page counts, and model judgment are
not mapping authority.

Spec 05 must:

1. emit one standard `hyperref` named destination immediately before and after
   every frozen render node;
2. resolve those destinations from the exact compiled PDF;
3. write `reports/final_pdf_page_provenance.json` with protocol
   `spec05-final-pdf-page-provenance/1.0`;
4. bind that report to the exact final PDF, canonical ledger, render plan,
   volume partition, render execution report, template contract, presentation
   config, and final render pack.

Use standard `\hypertarget` primitives only. Do not add a custom command,
environment, package, or template mutation.

## Mapping rules

- Cover every candidate PDF page exactly once and in physical order.
- Map a source-body page through its render-node interval to exact render-node
  IDs, source-block IDs, and physical source-PDF pages.
- Keep render nodes and source blocks in exactly one volume.
- Preserve the global first-seen order of source pages.
- Permit generated front matter only before the first source-body destination
  and only when the frozen template/presentation contract explains it.
- Classify any interstitial, trailing, missing, conflicting, or otherwise
  unprovable page as `mapping_uncertain`.
- Permit at most one adjacent-volume source-page overlap at the semantic cut,
  and only when the two volumes own disjoint render nodes and source blocks.
- Treat exact duplicate candidate-page rasters within one volume as an
  `ORDER_OR_DUPLICATION` blocker.
- Treat `mapping_uncertain` as a `MAPPING_UNCERTAIN` blocker.

## Spec 06 model boundary

The bounded vision model reviews visual/source fidelity only. For every page,
give it the exact deterministic disposition and allowed source-page set. It
must echo that set and may not add, remove, reorder, or select source pages.
Generated front matter has an empty source-page set and is reviewed against its
frozen generated role. An uncertain mapping also has no guessed source page
and remains blocked regardless of the model response.

Bind every call to the immutable release ID/hash, prompt and output-schema
IDs/versions/hashes, provider, model, HTTPS endpoint-origin hash, request
parameters, input hash, output hash, response ID, usage, latency, and stage
budgets. Treat source and candidate page content as untrusted data, never as
instructions.

## Independent validation

Use the same strict page-review contract at three boundaries:

1. the Spec 06 candidate producer before it writes the stage manifest;
2. the independent Spec 06 evaluator before promotion;
3. delivery recompile before it accepts the reviewed ZIP/PDF set.

The shared contract must independently reconstruct provider requests and
results, rerender exact source/candidate pages, revalidate lineage and
provenance hashes, enforce cross-volume rules, and emit evidence-bound blocking
findings. A producer self-reported pass is never authority.

Negative tests must cover at least page omission, page duplication, page
reordering, cross-volume node/block swaps, fake generated front matter,
image-only pages without provenance, source/PDF/render-pack/hash drift, path
escape or symlink substitution, and provider transcript drift.
