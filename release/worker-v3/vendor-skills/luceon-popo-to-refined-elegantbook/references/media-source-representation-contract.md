# Media source representation contract

Use this contract at the Spec 03 to Spec 04 boundary. It does not perform OCR
cleaning or ElegantBook construct selection.

## Artifacts

- `canonical_block_ledger.jsonl` owns each formal `media_contract` atom and is
  bound to the frozen decision index before sidecar views are generated.
- `media_evidence_ledger.json` records source atoms, upstream candidates, asset
  bytes, PDF geometry, crop diagnostics, and review evidence.
- `media_representation_plan.json` chooses exactly one evidence candidate for
  every included atom or records a blocking review item.
- `media_representation_validation.json` independently verifies both artifacts
  against live PDF and asset bytes.
- `source_lineage_integrity_report.json` binds the exact source-reconciled
  parent ledger and decision index, normalized fragment groups, actionable
  source-order audit, and review closure used by formal-native promotion.
- `page_visual_region_inventory.json` renders the exact declared page scope
  from the original PDF and binds every page to a closed visual review.
- `visual_region_integrity_report.json` disposes missing meaningful regions and
  standalone-crop suitability before native media production.

## Source lineage invariants

- The source parent starts with `record_type=ledger_header`; its
  `current_ledger_hash` is the canonical JSON hash of the ordered
  `source_block` records only.
- Decision snapshots are cumulative. Every parent decision object must appear
  unchanged in the child inventory; the stage may append its own closed
  decision but may not silently drop or rewrite inherited decisions.
- `source_block_ids[]` is a fragment group, not a one-to-one bbox assumption.
  One media atom may own multiple fragments when asset identity and source
  evidence show that they form one visual. Each fragment is assigned exactly
  once and the normalized inventory must cover its declared media scope.
- Review queues contain actionable events only: a concrete missing-source
  insertion, reading-order reanchor, or unresolved manual ambiguity with
  affected source refs and evidence. Layout features without a correction or
  ambiguity are signals, not queue items.
- `formal_full_source` requires review of every source physical page.
  `bounded_media_regression` may review only fixture pages and must never be
  reported as full source fidelity.

## Representation vocabulary

- `source_asset_image`: a hash-verified upstream image asset.
- `source_region_image`: a reproducible crop from the source PDF.
- `structured_formula`: a verified formula transcription.
- `structured_table`: a verified table cell/grid reconstruction.
- `structured_chart`: a verified data/visual reconstruction.
- `vector_reconstruction`: a separately reviewed source-faithful reconstruction.

The vocabulary describes source representation. Spec 04 still decides the legal
template construct that carries the frozen representation.

For ElegantBook delivery, `source_asset_image` and `source_region_image` must
materialize as native `.jpg`, `.jpeg`, or `.png` assets. The source may be a PDF
page or PDF crop recipe, but the delivered image may not be converted to or
packed inside a PDF. Exact-identical image bytes may share one project asset;
near-duplicate or merely similar images remain distinct unless a source-scope
decision excludes them.

## Page visual and composite integrity

- Existing block coverage does not prove page visual completeness. The page
  inventory is generated from the original PDF independently of the block tree.
- Every page in the declared scope is reviewed. Every discovered meaningful
  visual region is represented, source-backed excluded, or open and blocking.
- MinerU/Popo byte identity proves candidate provenance only. A separate
  byte-bound review decides whether each candidate is suitable standalone.
- Inseparable photos, labels, captions, maps, or wrapped text become one
  explicit composite fragment group; members may not also emit independently.
- A source-backed composite crop from the PDF is a safe fallback and is
  delivered as a hash-bound native raster image. Erasing neighbors,
  inpainting, invented pixels, or a lookalike reconstruction is not.
- Direct PDF clips use an isolated PDF instance per crop so exact crop hashes
  cannot vary with unrelated page-cache activity.

## Fail-closed behavior

- A source-region crop always needs human review bound to its exact crop SHA-256.
  Ink density alone cannot close it.
- The crop recipe is explicit. `direct_pdf_clip` and
  `source_page_raster_cropbox_to_mediabox` are distinct evidence paths; the
  latter also binds the page-raster hash, raster coordinate space, padding, and
  resulting crop hash. Never silently substitute one recipe for the other.
- An unverified OCR transcription is evidence, not renderable structured content.
- If multiple usable candidates disagree by hash or structure, do not pick the
  first one. Create a review item.
- Missing assets, hash drift, unknown coordinate space, invalid bbox, blank crop,
  and edge-truncation signals block the corresponding representation.
- Review closure is invalidated whenever the selected candidate artifact hash or
  source evidence hash changes.

## Layer boundary

For a formal chain, use the native producer to commit
`media_contracts[]` and their exact `frozen_representation` into the canonical
ledger. The producer's input candidate package must not contain render-node,
ElegantBook-construct, or historical render-plan decisions.
Generate the media ledger and plan mechanically from those native atoms with
`build-from-canonical`; the outputs must bind the exact canonical-ledger and
decision-index byte hashes and must reproduce every frozen representation.
The media plan must
be frozen before `render_plan`. A render node carrying media
must cite `media_id`, `representation_id`, and the media plan hash. The renderer
may copy an asset or execute the already specified crop only; it may not change
candidate, bbox, coordinate space, transcription, or layout semantics.

Each media render node uses a `media_binding` object with `media_id`,
`representation_id`, `representation_type`, `selected_candidate_id`,
`artifact_sha256`, and `media_representation_plan_sha256`. Run
`media_source_representation.py validate-render-binding` before Spec 05. Every
closed media representation must occur exactly once; excluded or open items may
not occur.

Before Spec 04 consumption, an independent promotion gate must revalidate live
bytes, D-to-L acyclicity, decision closure, ledger identity, media closure, and
formal-native run integrity. Formal-native promotion also requires the external
source-lineage report and rechecks cumulative child decisions, exact fragment
copies, standard child ledger identity, and review-queue closure. Migration
compatibility runs remain consumable only as that explicitly weaker class;
absence of the new report does not upgrade them to formal-native. `stage
status=passed` without a promoted manifest is not consumable.
