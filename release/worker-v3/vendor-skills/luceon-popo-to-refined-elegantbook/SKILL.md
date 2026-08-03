---
name: luceon-popo-to-refined-elegantbook
description: Orchestrate evidence-first conversion from source PDF plus MinerU/MinerU-Popo artifacts to a source-faithful frozen-plan ElegantBook project. Use when Codex must enforce the six product specs, immutable canonical ledger and decision index, semantic render_plan, template_contract, mechanical generation, compilation, and final page review without overwriting upstream evidence or letting downstream polish hide defects.
---

# Luceon Popo to Refined ElegantBook

## Role

This is the thin orchestration and contract-owning skill for the full product workflow. It does not redefine correctness from whatever older stage scripts can produce. The source PDF is final visual truth; MinerU-Popo is the formal processing entry; all later artifacts are immutable views of one evidence chain.

Read the workspace `AGENTS.md` and Specs 01–06 before any formal run. If they conflict with this skill, the workspace contract wins.

## Stage Contract

```text
Spec 01  read-only intake and provenance
  -> Spec 02  body scope and reading order
  -> Spec 03  canonical evidence ledger and coverage
  -> Spec 04  semantic mapping and frozen render_plan
  -> Spec 05  authoritative template_contract and mechanical rendering/compile
  -> Spec 06  every-page source, order, and visual acceptance
```

The five round-one contracts are:

1. `canonical_block_ledger.jsonl`: immutable source atoms and stage state;
2. `canonical_decision_index.json`: all human decisions, expiry, and closure;
3. `render_plan.json`: Spec 04's complete and deterministic construct bindings;
4. `volume_partition_plan.json`: Spec 04-D's frozen one-or-two-volume membership and semantic boundary;
5. `template_contract.json`: Spec 05's only legal template mutation surface.

Their schemas are in `schemas/`. Schema conformance alone is insufficient; use the validator for hashes, closure, coverage, D-to-L acyclicity, capability bindings, and live template bytes.

### Outline-guided pedagogical layout boundary

When an outline-derived lecture needs nested body headings, coherent numbering after reassembly, or dense exercise layout, Spec 04-D may consume a closed `outline-pedagogical-layout-plan/1.0` embedded in the reviewed render policy.

- The plan binds the exact source-reconciled ledger and records full source titles/text plus hashes.
- Source section-number removal and topic-local exercise labels are display-label transformations only; the source label remains evidence.
- Question labels remain source labels. Order restoration belongs to Spec 02/source reconciliation and is limited to a pure permutation of one uninterrupted text-only run with unique contiguous explicit labels.
- `response_list` is a template capability only when the frozen template declares `multicol`. The plan, not Spec 05, selects one/two columns and exact answer-space parameters.
- Local body hierarchy does not imply TOC visibility. Pedagogical local headings remain `toc=false` unless a separate Spec 04-A TOC decision says otherwise.

The producer and the independent Spec 04-D promotion evaluator both compare every response node and applicable local-heading node with the exact frozen pedagogical contract. Any missing, duplicated, drifted, or open group blocks promotion. Plans without this contract retain historical single-paragraph behavior.

### Media source representation boundary

Before Spec 04 chooses an ElegantBook construct, Spec 03 must freeze media
evidence and representation with the rules in
`references/media-source-representation-contract.md`. Use
`scripts/media_source_representation.py` to build and independently validate:

1. `media_evidence_ledger.json`;
2. `media_representation_plan.json`;
3. `media_review_queue.json`;
4. `media_representation_validation.json`.

The normalized input may be produced by a MinerU/Popo adapter or canonical
ledger projection, but the core script never branches on material identity. A
PDF crop cannot close from ink metrics alone: its human review must cite the
exact crop hash. OCR table/chart/formula serialization remains candidate
evidence until a verified structured transformation exists.

The MinerU/Popo adapter requires an explicit upstream bbox space
(`normalized_0_1`, `normalized_0_1000`, or `cropbox_points_top_left`). Never
infer it from coordinate magnitude; an unknown provider coordinate contract is
a blocking intake defect.

Before formal-native media production, freeze page-level visual completeness
and composite-media integrity with `visual_region_integrity.py`. This stage
renders the declared page scope directly from the original PDF, independently
of whether MinerU/Popo created a block. Its page review must cover the exact
scope, and its standalone-media review is bound to the exact candidate
fingerprints. Matching MinerU/Popo hashes prove provenance only; they do not
prove that a crop is suitable for standalone display.

Spatially inseparable photographs, labels, captions, neighboring graphics, or
wrapped text become one reviewed `composite-media-integrity/1.0` fragment group
and one source-PDF region. If page review discovers a meaningful visual region
absent from the canonical ledger, the stage creates a deterministic
`visual_region` source atom with source bbox, raster hash, shared reading
anchor, and closed decision. It never erases, inpaints, invents pixels,
reconstructs formulae/tables, or chooses a template/render construct.

When a formal delivery fails the strict file-entity limit, run
`media_scope_review_queue.py` at the Spec 02/03 boundary. It mechanically scans
the active media ledger and representation plan for compact, edge-adjacent,
saturated, or thin image candidates and creates contact sheets. These are
review candidates only: the script never excludes media. A closed exclusion
must cite the exact source page, bbox, block/media ids, artifact hash, and
classify the item as navigation/page decoration or OCR noise. Instructional
icons, formulas, diagrams, tables, and other meaningful visuals remain body
media. No filename, title, material id, known hash, or fixed page may trigger
the rule.

```bash
python3 scripts/media_scope_review_queue.py \
  --media-evidence-ledger /path/to/media_evidence_ledger.json \
  --media-representation-plan /path/to/media_representation_plan.json \
  --output-dir /new/immutable/media-scope-review
```

```bash
python3 scripts/visual_region_integrity.py produce \
  --parent-ledger /path/to/source_reconciled_parent.jsonl \
  --parent-decision-index /path/to/parent_decisions.json \
  --normalized-candidates /path/to/normalized_media_candidates.json \
  --source-pdf /path/to/source.pdf \
  --review-bundle /review/visual_region_review.json \
  --ledger-snapshot-id material-visual-reconciled-v1 \
  --ledger-version 2 \
  --decision-snapshot-id material-visual-decisions-v1 \
  --stage-decision-id DEC-VISUAL-REGION-001 \
  --run-id material-visual-region-v1 \
  --report-id material-visual-region-integrity-v1 \
  --output-dir /new/immutable/visual-region-run

python3 scripts/visual_region_integrity.py validate-run \
  --run-dir /new/immutable/visual-region-run
```

The native media producer must consume the corrected ledger, decisions, and
normalized candidates from that exact run and bind
`--visual-integrity-report`. The source-lineage report binds the same report;
promotion independently rerenders pages and composite crops and fails
`PG-H14` when evidence is missing, stale, incomplete, or open.

Before a formal-native Spec 03 commit, build a live-verifiable
`source_lineage_integrity_report` with
`build_source_lineage_integrity.py`. It requires the standard source-block-only
ledger identity, exact cumulative decision inheritance, an exact partition of
canonical media fragments into media atoms (one atom may own multiple
fragments, but one fragment may not belong to multiple atoms), and a structured
source-order audit whose queue contains only actionable correction/ambiguity
events. Broad layout signals such as “multi-column page detected” are not review
events unless they identify an affected source atom and a required action.

Use `formal_full_source` only when the complete source page scope has been
reviewed. A regression fixture may use `bounded_media_regression`, but that
status must remain visible and never implies full Spec 01/02 or source fidelity.

For a new formal chain, run `produce_native_spec03_media.py`. It preflights an
independent candidate package as existing evidence E, freezes D, writes native
`media_contracts[]` plus their `frozen_representation` into L, mechanically
projects the formal media views, and commits M. It rejects candidate packages
that contain render-node keys or a historical `render_plan.json` reference.
`build-from-canonical` is the internal/mechanical projection step; it is not a
replacement for the D-to-L producer. The generic `build` command remains for
adapter tests, intake diagnostics, and pre-ledger candidate work.

```bash
python3 scripts/media_source_representation.py build \
  --input /path/to/normalized_media_candidates.json \
  --source-pdf /path/to/source.pdf \
  --asset-root mineru=/path/to/mineru \
  --asset-root popo=/path/to/popo \
  --output-dir /path/to/media-contract

python3 scripts/media_source_representation.py build-from-canonical \
  --canonical-ledger /path/to/canonical_block_ledger.jsonl \
  --decision-index /path/to/canonical_decision_index.json \
  --source-pdf /path/to/source.pdf \
  --asset-root source_assets=/path/to/upstream-task \
  --asset-root source_pages=/path/to/page-rasters \
  --output-dir /path/to/media-contract

python3 scripts/produce_native_spec03_media.py \
  --parent-ledger /path/to/source_reconciled_parent.jsonl \
  --parent-decision-index /path/to/parent_decision_index.json \
  --normalized-candidates /path/to/normalized_media_candidates.json \
  --source-pdf /path/to/source.pdf \
  --visual-integrity-report /path/to/visual-region-run/reports/visual_region_integrity_report.json \
  --asset-root mineru=/path/to/mineru \
  --asset-root popo=/path/to/popo \
  --ledger-snapshot-id source-media-v2 \
  --ledger-version 2 \
  --decision-snapshot-id decisions-media-v2 \
  --stage-decision-id DEC-MEDIA-COMMIT-001 \
  --run-id media-run-v1 \
  --output-dir /new/immutable/spec03-media-run

python3 scripts/build_source_lineage_integrity.py \
  --parent-ledger /path/to/source_reconciled_parent.jsonl \
  --parent-decision-index /path/to/parent_decision_index.json \
  --normalized-candidates /path/to/normalized_media_candidates.json \
  --source-order-audit /path/to/automated_scope_order_audit.json \
  --source-review-closure /path/to/source_review_closure.json \
  --scope-mode formal_full_source \
  --media-inventory-rule source_label_compatible \
  --report-id material-source-lineage-v1 \
  --output /promotion-root/source-integrity-v1.json

python3 scripts/media_source_representation.py validate \
  --ledger /path/to/media-contract/media_evidence_ledger.json \
  --plan /path/to/media-contract/media_representation_plan.json \
  --report /path/to/media-contract/media_representation_validation.json

python3 scripts/media_source_representation.py validate-render-binding \
  --ledger /path/to/media-contract/media_evidence_ledger.json \
  --plan /path/to/media-contract/media_representation_plan.json \
  --render-plan /path/to/render_plan.json \
  --report /path/to/media-contract/media_render_binding_validation.json
```

An exit code of `3` from `build` means evidence was built but at least one
review remains open; it is not a pass and must not be suppressed.

### Spec 04-A source outline and TOC boundary

Run Spec 04-A before choosing teaching boxes or other ElegantBook constructs.
It freezes one exact relationship among the source TOC, body hierarchy, and
abstract final TOC. The generic producer never branches on a title string,
language, publisher, material id, page number, or known hash. Book-specific
scope and hierarchy judgments belong in a reviewed
`spec04a-outline-review-bundle/1.0` configuration bound to exact source-page
evidence, the active Spec 03 promotion, and the parent ledger payload.

The producer inventories every included block labelled or typed as `title`.
Only reviewed structural evidence may promote a candidate into a hierarchy
node. Every remaining candidate receives the explicit disposition
`local_heading`; a title label alone never creates a book-level TOC entry.
Media-only or bounded regression ledgers are ineligible because they cannot
establish a complete book outline.

Spec 04-A owns only:

1. source TOC include/exclude decisions;
2. source-supported body nodes, parents, levels, and source order;
3. exact title-candidate partition into structural titles and local headings;
4. final TOC visibility, level, and reviewed title normalization.

It explicitly does not choose teaching roles beyond structural node roles,
ElegantBook environments, boxes, render nodes, formula/table reconstruction,
LaTeX, or upstream cleaning changes. A passed 04-A slice therefore records
`full_spec04_status=not_evaluated`.

```bash
python3 scripts/spec04a_structure_contract.py inventory \
  --ledger /path/to/promoted-spec03-ledger.jsonl \
  --output /review/title-candidate-inventory.json

python3 scripts/spec04a_structure_contract.py produce \
  --parent-ledger /path/to/promoted-spec03-ledger.jsonl \
  --parent-decision-index /path/to/parent-decisions.json \
  --source-pdf /path/to/source.pdf \
  --promotion-registry /path/to/active-registry.json \
  --parent-promotion /path/to/spec03.promotion.json \
  --parent-lineage-key material/spec03-media \
  --review-bundle /review/spec04a_outline_review.json \
  --ledger-snapshot-id material-spec04a-v1 \
  --ledger-version 3 \
  --decision-snapshot-id material-decisions-spec04a-v1 \
  --stage-decision-id DEC-SPEC04A-001 \
  --run-id material-spec04a-v1 \
  --output-dir /new/immutable/spec04a-run

python3 scripts/stage_promotion_gate.py evaluate-spec04a-structure \
  --run-dir /new/immutable/spec04a-run \
  --promotion-id material-spec04a-v1 \
  --lineage-key material/spec04a-structure \
  --output /promotion-root/spec04a-v1.promotion.json
```

The producer verifies an already promoted ancestor by its frozen promotion,
artifact, stage-manifest, and committed capability-file hashes. It does not
require historical shared scripts to equal today's skill bytes. The new
Spec 04-A producer and promotion evaluator are still live-rehashed before the
new promotion can pass. This separates immutable ancestor identity from
current executable capability without weakening artifact lineage.

### Spec 04-B semantic spans and teaching-column membership

Run Spec 04-B only from the active Spec 04-A promotion. It must consume the
exact promoted ledger, cumulative decision index, source outline, and final TOC;
it may not rediscover hierarchy or reclassify the Spec 04-A title partition.

The stage has two outputs with deliberately different breadth:

1. an exact semantic-span partition that disposes every included source atom
   once, using conservative roles such as `book_structure`, `local_heading`,
   `plain_body`, and `fragile_or_media` where no narrower claim is supported;
2. teaching-column groups only for closed, source-page-supported marker/body
   membership. Every group must have a non-empty text body, must remain on one
   physical page, and must not consume equations, tables, charts, images, or
   other fragile atoms.

A visual teaching marker may be OCR-labelled `title`, `text`, or `aside_text`.
The generic producer never decides from its visible string: an exact reviewed
bundle supplies the marker and body block ids plus page evidence. A reviewed
text marker is allowed only when it does not conflict with a Spec 04-A
structure node. Unreliable grouping safely degrades to individual spans rather
than an empty box.

Spec 04-B never selects an ElegantBook environment, tcolorbox style, template
construct, render node, or LaTeX syntax. It also does not rebuild formulae or
tables. These keys are mechanically prohibited, and a passed slice records
`full_spec04_status=not_evaluated`.

```bash
python3 scripts/spec04b_semantic_span_contract.py produce \
  --parent-ledger /path/to/promoted-spec04a-ledger.jsonl \
  --parent-decision-index /path/to/spec04a-decisions.json \
  --source-pdf /path/to/source.pdf \
  --promotion-registry /path/to/active-registry.json \
  --parent-promotion /path/to/spec04a.promotion.json \
  --parent-lineage-key material/spec04a-structure \
  --review-bundle /review/spec04b_semantic_review.json \
  --ledger-snapshot-id material-spec04b-v1 \
  --ledger-version 4 \
  --decision-snapshot-id material-decisions-spec04b-v1 \
  --stage-decision-id DEC-SPEC04B-001 \
  --run-id material-spec04b-v1 \
  --output-dir /new/immutable/spec04b-run

python3 scripts/stage_promotion_gate.py evaluate-spec04b-semantic-spans \
  --run-dir /new/immutable/spec04b-run \
  --promotion-id material-spec04b-v1 \
  --lineage-key material/spec04b-semantic \
  --output /promotion-root/spec04b-v1.promotion.json
```

### Spec 04-C template construct binding

Run Spec 04-C only from the active Spec 04-B promotion. It consumes the exact
semantic span ledger and teaching-column group ledger; it may not regroup
source atoms or reassign their semantic roles. Before binding anything, the
producer deterministically extracts `template_capability_manifest.json` from
the exact supplied template archive and intake hashes. This is the first stage
allowed to select an existing template construct.

The capability manifest also extracts the effective pre-TOC `tocdepth`, the
semantic entry-type/depth map, native visible entry types, and legal standard
serialization strategies. Unknown depth fails closed. A capability may expose
`localized_depth_override` only when it preserves the original TOC entry type
and PDF outline level, is scoped to one `.toc` entry group, uses standard LaTeX
commands, and changes neither the template API, class, nor preamble.

Every confirmed teaching group and standalone semantic label is bound exactly
once. A non-empty, source-evidenced teaching group may use an existing
`tcolorbox` style when the closed review bundle explains the semantic fit. A
standalone label has no confirmed body and therefore may not become a box; its
safe fallback is an existing unnumbered local-heading construct. The generic
producer never maps source-visible strings, material IDs, pages, or hashes to a
construct. Role-to-construct choices live in reviewed profile/book
configuration and remain book-level until cross-sample evidence justifies
promotion.

The contract records construct and parameters only. It forbids render nodes,
payload, output anchors, LaTeX, formula/table reconstruction, and upstream
cleaning. A passed 04-C slice still records
`full_spec04_status=not_evaluated`; a later slice must freeze the complete
render plan without reselecting these constructs.

```bash
python3 scripts/spec04c_construct_binding_contract.py produce \
  --parent-ledger /path/to/promoted-spec04b-ledger.jsonl \
  --parent-decision-index /path/to/spec04b-decisions.json \
  --parent-semantic-span-ledger /path/to/semantic_span_ledger.json \
  --parent-teaching-group-ledger /path/to/teaching_column_group_ledger.json \
  --source-pdf /path/to/source.pdf \
  --template-intake /path/to/template_intake.json \
  --template-zip /path/to/template.zip \
  --promotion-registry /path/to/active-registry.json \
  --parent-promotion /path/to/spec04b.promotion.json \
  --parent-lineage-key material/spec04b-semantic \
  --review-bundle /review/spec04c_construct_review.json \
  --ledger-snapshot-id material-spec04c-v1 \
  --ledger-version 5 \
  --decision-snapshot-id material-decisions-spec04c-v1 \
  --stage-decision-id DEC-SPEC04C-001 \
  --run-id material-spec04c-v1 \
  --output-dir /new/immutable/spec04c-run

python3 scripts/stage_promotion_gate.py evaluate-spec04c-construct-bindings \
  --run-dir /new/immutable/spec04c-run \
  --promotion-id material-spec04c-v1 \
  --lineage-key material/spec04c-construct \
  --output /promotion-root/spec04c-v1.promotion.json
```

### Spec 04-D complete render-plan freeze

Run Spec 04-D only after the registry has active promotions for the exact
Spec 04-C construct contract, Spec 04-A source outline, and Spec 03 media
contract. The producer mechanically freezes structure, plain body, inherited
teaching constructs, closed media representations, source order, payload
hashes, and output anchors. It never reads a historical render plan and never
reselects a semantic role, tcolorbox style, or media representation.

Run `preflight` first. Every included `image`, `table`, `chart`, and `equation`
atom must have exactly one closed active Spec 03 representation. Captions and
footnotes may remain source-exact paragraphs; fragile atoms may not. A
structure node whose source evidence is itself a media atom is represented as
a source-outline-backed virtual structure node, while the atom is output once
through its media node. This preserves both hierarchy and the exactly-once
source-atom invariant.

Treat `heading_evidence_block_ids` as structure evidence, not automatically as
visible title text. Any included non-title/non-media evidence in that set must
have a closed `structure_source_role_overrides` decision. Emit a reviewed
`post_heading_body` atom as its own source-exact paragraph render node; never
count it as title coverage or leave it in an unconsumed structure payload.
Promotion independently verifies the role, raw-content hash, node kind, and
exactly-once coverage.

Only a passed 04-D run may record `ledger_checkpoint=semantic_frozen` and
`full_spec04_status=passed`. This closes Spec 04 only: no LaTeX, compilation,
layout repair, or final-page acceptance is implied.

Every source-required final TOC node must be renderable under the exact active
Spec 04-C capability. Spec 04-D binds semantic level, body heading construct,
TOC entry type, effective template depth, and visibility strategy. If native
depth is insufficient, an explicitly reviewed hierarchy-preserving localized
override may be frozen. Flattening `subsection` to `section`, dropping the
entry, or asking Spec 05 to mutate `tocdepth` globally is a hard failure.

Spec 04-D also freezes `volume_partition_plan/1.2`. The default is one volume.
Internal generated-body sharding does not change volume count. A two-volume
plan requires per-volume capacity-preflight evidence for generated-body bytes,
900K leaf-part feasibility, the largest atomic TeX line, and the largest raster
image. Each volume also freezes `body_units` from source-supported top-level
structure nodes; names, page numbers, and sample IDs are never partition logic.
Two volumes are permitted only when closed evidence shows that the single
delivery cannot satisfy an unchanged Spec 05 limit after legal asset cleanup
and exact-byte reuse. The cut must begin at a top-level source-supported
structure node, must not cross parent anchors or semantic groups, and must
partition every render node and included source atom exactly once in original
order. Both estimated volume budgets must be below 2,000 file entities,
50,000,000 ZIP bytes, 900,000 bytes per generated body leaf part, and
1,000,000 bytes per raster image. Editable-text total is recorded but is not a
universal 7 MB CE hard gate. If no legal two-volume cut exists, fail; do not create three
volumes or defer the decision to Spec 05.

```bash
python3 scripts/spec04d_render_plan_contract.py preflight \
  --parent-ledger /path/to/promoted-spec04c-ledger.jsonl \
  --media-representation-plan /path/to/promoted-spec03-media-plan.json \
  --render-policy /reviewed/spec04d-render-policy.json \
  --report /reports/spec04d-preflight.json

python3 scripts/spec04d_render_plan_contract.py produce \
  --parent-ledger /path/to/promoted-spec04c-ledger.jsonl \
  --parent-decision-index /path/to/spec04c-decisions.json \
  --construct-binding-ledger /path/to/construct_binding_ledger.json \
  --template-capability-manifest /path/to/template_capability_manifest.json \
  --source-outline-ledger /path/to/source_outline_ledger.json \
  --final-toc-plan /path/to/final_toc_plan.json \
  --media-evidence-ledger /path/to/media_evidence_ledger.json \
  --media-representation-plan /path/to/media_representation_plan.json \
  --source-pdf /path/to/source.pdf \
  --promotion-registry /path/to/active-registry.json \
  --parent-04c-promotion /path/to/spec04c.promotion.json \
  --parent-04c-lineage material/spec04c-construct \
  --structure-promotion /path/to/spec04a.promotion.json \
  --structure-lineage material/spec04a-structure \
  --media-promotion /path/to/spec03.promotion.json \
  --media-lineage material/spec03-media \
  --render-policy /reviewed/spec04d-render-policy.json \
  --ledger-snapshot-id material-spec04d-v1 \
  --ledger-version 6 \
  --decision-snapshot-id material-decisions-spec04d-v1 \
  --stage-decision-id DEC-SPEC04D-001 \
  --run-id material-spec04d-v1 \
  --output-dir /new/immutable/spec04d-run

python3 scripts/stage_promotion_gate.py evaluate-spec04d-render-plan \
  --run-dir /new/immutable/spec04d-run \
  --promotion-id material-spec04d-v1 \
  --lineage-key material/spec04d-render-plan \
  --output /promotion-root/spec04d-v1.promotion.json
```

### Formal-native Spec 05 promotion

Spec 05 is eligible only from the exact active `formal_native` Spec 04-D
promotion with `full_spec04_status=passed`. The mechanical producer is owned by
`cleanlatex-to-elegantbook`; this orchestrator independently promotes or
rejects its immutable output:

```bash
python3 scripts/stage_promotion_gate.py evaluate-spec05-build \
  --run-dir /new/immutable/spec05-run \
  --promotion-id material-spec05-v1 \
  --lineage-key material/spec05-build \
  --output /promotion-root/spec05-v1.promotion.json
```

The gate revalidates the registry-selected Spec 04-D parent, all 14 template
and 20 compile hard gates, exact ZIP-as-build-input identity, the contiguous
hash-bound final render pack, decision closure, the `E -> B -> D -> M` commit
order, immutable run bytes, and live producer/evaluator capability snapshots.
For `TP-H14`, it independently rescans the exact promoted `rendered_body.tex`
against the promoted template capability manifest and verifies that the
producer report is byte-bound and identical in inventory and findings; it does
not trust a producer self-reported pass.
For `CP-H18`, it independently measures the exact promoted ZIP and requires it
to be strictly smaller than `50,000,000` bytes; it also requires the producer's
bound `delivery_size_report.json` to declare the same hash, byte count,
exclusive limit, and comparison operator. This limit cannot be relaxed by a
sample profile or build policy.
For `CP-H19` and `CP-H20`, it independently enumerates file entities, recursively
follows the safe TeX input closure from root `main.tex`, resolves all
image/cover/logo references, rejects unreferenced project media,
and rejects PDF-pack or image-to-PDF transport. It then compares this rescan to
the producer's bound `delivery_asset_report.json`. The thresholds are strict:
`2,000` file entities fails, and only native `.jpg`, `.jpeg`, or `.png` image
assets are admissible. `CP-H28` independently rejects every raster image at or
above `1,000,000` bytes and compares the scan to the producer asset report.
For `CP-H25`, the evaluator independently requires one root `main.tex`, one
hash-bound `body/generated-body.tex`, and the exact standard input literal once.
The body loader may contain only ordered direct inputs of
`body/units/unit-NNNN/part-NNNN.tex`; concatenated parts must reproduce the
frozen body and may not load or define TeX behavior. `CP-H27` independently
measures the root, loader, and every leaf part against the strict 900K limit.
For `CP-H26`, it derives
the expected outer ZIP/PDF names independently from frozen title and volume
metadata. Producer self-reports are evidence, not authority.
For a two-volume stage the evaluator independently rescans each ZIP and child
build, then verifies `CP-H21`-`CP-H28`: exact two-volume cardinality, every
legacy hard gate on both volumes, ordered/disjoint/complete aggregate coverage,
and exact agreement with the active Spec 04-D partition. A pass from one
volume never compensates for a failure in the other.
It rejects downstream claims: render coverage, Spec 06, reusable maturity, and
product acceptance remain separate stages.

### Spec 05 final-page provenance and Spec 06 review

Spec 05 must create deterministic final-PDF page provenance from standard
named destinations placed around every frozen render node. Spec 06 must consume
that provenance; OCR, text similarity, and model judgment may not choose the
candidate-to-source mapping. The bounded vision model reviews fidelity only
against the exact allowed source-page set.

Read
`references/spec05-final-page-provenance-and-spec06-review.md` before producing,
evaluating, repairing, or resuming Spec 05/06 evidence. Missing, conflicting,
uncertain, duplicated, reordered, cross-volume-drifted, or hash-drifted
provenance fails closed. Reuse the same strict contract in the Spec 06 producer,
independent evaluator, and delivery-recompile boundary.

### Promotion gate

Stage self-reported `passed` is never downstream authority. Evaluate each
immutable Spec 03 media run with `stage_promotion_gate.py`; keep the resulting
promotion manifest outside the run. Only `disposition=promoted` may enter Spec
04. Compose immutable registry snapshots to resolve the active promotion per
lineage. Spec 04 must verify the registry, lineage key, promotion-manifest hash,
and promoted artifact hashes together; a formerly promoted but no longer active
manifest is not consumable. Rejected entries remain audit evidence and never
replace an active promotion.

```bash
python3 scripts/stage_promotion_gate.py evaluate-spec03-media \
  --run-dir /path/to/spec03-media-run \
  --promotion-id promotion-media-v1 \
  --lineage-key material-lineage/spec03-media \
  --source-integrity-report /promotion-root/source-integrity-v1.json \
  --output /promotion-root/media-v1.promotion.json

python3 scripts/stage_promotion_gate.py compose-registry \
  --promotion-manifest /promotion-root/media-v1.promotion.json \
  --registry-id stage-promotions \
  --snapshot-id promotion-registry-v1 \
  --output /promotion-root/registry-v1.json
```

### Execution capability boundary

Every new formal-native producer must freeze
`precommit/execution_capability_manifest.json` as existing evidence E before
the stage decision index D. The manifest binds the exact skill contract,
declared entrypoints, statically reachable local Python modules, machine
schemas/profile/configuration actually used, sanitized invocation, Python
runtime, and third-party distribution versions. It must not hash the whole
skill directory: unused tests, caches, examples, and temporary files are not
execution identity, while an imported local module may never be omitted.

`produce_native_spec03_media.py` captures and self-validates this evidence,
then binds it through D, the stage manifest M, and the immutable run manifest.
`stage_promotion_gate.py` live-rehashes the producer snapshot and creates a
separate evaluator capability snapshot beside the promotion manifest. A
formal-native promotion fails if either snapshot is missing, drifted, or only
represented by a version string.

The submission order is:

```text
execution/preflight evidence E
  -> canonical decision index D
  -> child ledger and formal views L
  -> stage manifest M
  -> independent evaluator capability and promotion P
```

Use `execution_capability.py` directly when another deterministic stage needs
the same contract:

```bash
python3 scripts/execution_capability.py capture \
  --manifest-id stage-capability-v1 \
  --skill-root /path/to/skill \
  --entrypoint stage_producer=/path/to/producer.py \
  --resource machine_schema=/path/to/schema.json \
  --invocation-arg producer.py \
  --output /new/immutable/execution_capability_manifest.json

python3 scripts/execution_capability.py validate \
  --manifest /path/to/execution_capability_manifest.json
```

Never record secret values. The capability collector redacts secret-bearing
flags and URI user info and records no environment values. A Git commit may be
additional evidence, but never replaces file hashes.

Historical migration runs without producer snapshots remain
`historical_unbound`. They may receive a newly bound evaluator audit but may
not be retroactively promoted to `formal_native`. Any later change to a bound
skill contract, executable module, schema/profile/configuration, runtime, or
dependency invalidates live verification for a new or currently evaluated
stage and requires a new immutable run and promotion; editing the old manifest
is forbidden. A frozen ancestor remains selectable by its immutable promotion
and committed hashes, but it cannot be re-described as live-verified under the
new code version.

## Validate the Boundary

From this skill's release directory, run:

```bash
python3 scripts/validate_intermediate_contracts.py \
  --ledger /path/to/canonical_block_ledger.jsonl \
  --decision-index /path/to/canonical_decision_index.json \
  --render-plan /path/to/render_plan.json \
  --template-contract /path/to/template_contract.json \
  --template-dir /path/to/template/unpacked \
  --capability-manifest /path/to/template_capability_manifest.json \
  --report /path/to/intermediate_contract_validation.json
```

Any failed check blocks Spec 05. Never patch a failed contract inside the renderer.

## Orchestration Rules

- Materialize PDF, MinerU, Popo, and template inputs read-only into an isolated versioned run.
- Keep identifiers, bucket prefixes, profiles, and paths in manifests/configuration; never trigger behavior from book names, sample ids, page numbers, or known hashes.
- Freeze decision index `D`, then child ledger/render artifacts `L`, then a stage manifest `M` binding both. `D` must not refer to its descendants.
- Every child decision snapshot must carry its complete parent decision
  inventory unchanged and add only the decisions owned by the new stage.
- Use `record_type=ledger_header` and the declared canonical record-only hash
  scope. Descriptive or whole-file hash phrases are not interoperable ledger
  identities.
- Treat `source_block_ids[]` as an exact fragment group: one media atom may bind
  one or more fragments, every in-scope fragment must be bound, and no fragment
  may be reused by a second media atom.
- Require an independent promotion manifest before Spec 04 consumes a Spec 03
  media ledger; stage manifests and directory names are not selection authority.
- Before full semantic mapping, consume an active Spec 04-A promotion and its
  exact source outline/final TOC. Do not reclassify local title candidates or
  derive book-level headings from OCR title labels.
- Before choosing ElegantBook constructs, consume an active Spec 04-B promotion
  and its exact span/group ledgers. Do not regroup columns from adjacency,
  visible labels, or the old render plan, and never turn a standalone label
  into an empty teaching box.
- Before freezing a render plan, consume an active Spec 04-C promotion and its
  exact template capability manifest and construct-binding ledger. Do not
  reselect styles or reinterpret standalone labels.
- The complete Spec 04-D plan must also bind the exact active Spec 04-A and
  Spec 03 promotions. Run the fragile-media preflight first; every included
  image/table/chart/equation atom needs one closed media representation.
- If a reviewed structure node is supported by a media atom, emit a virtual
  source-outline-backed structure node and output the media atom only once.
  Never duplicate the atom or discard either reviewed relationship.
- Spec 03 selects and freezes the source representation; Spec 04 chooses only a
  semantically compatible template construct, parameters, and render order.
- Spec 04 must carry every closed Spec 03 media representation into exactly one
  render node through a hash-bound `media_binding`; it may choose a template
  construct but may not change the selected candidate or artifact bytes.
- When migrating an already reviewed plan, use
  `freeze_canonical_media_contract.py` and `bind_media_to_render_plan.py` as
  immutable migration-only commit boundaries. Never use that migration freezer
  as the producer for a new sample. The latter is mechanical: it may add
  `media_binding` and asset hashes, but may not re-decide the inherited
  semantics, construct, parameters, payload content, or order.
- Invoke `cleanlatex-to-elegantbook/scripts/produce_native_spec05.py` for a new
  formal Spec 05 run; its internal renderer may serialize and escape only.
  Manual `render_frozen_plan.py` execution remains the lower-level mechanical
  boundary and cannot by itself establish a formal compile promotion.
- New formal-native Spec 05 runs require a versioned presentation configuration
  declaring exact cover and logo modes, bytes, provenance or approval, fit
  policy, and closed decisions. Source cover/title pages may be presentation
  evidence without entering body scope. Never infer presentation choices from
  book names, languages, sample ids, page constants, or known hashes, and never
  replace the frozen template cover/logo members in place.
- Invoke `refine-elegantbook-latex` only as a read-only diagnostic audit. It is not a mandatory mutation stage and cannot produce the promoted project.
- Route audit/final-review defects to Spec 03, 04, or 05, freeze new immutable snapshots, and rebuild mechanically.
- Compile from a fresh extraction of the deterministic delivery ZIP. Bind compile logs, PDF, full render pack, and page review to exact hashes.
- Do not publish or write MinIO unless the user separately authorizes that external action. Uploading a manifest last remains a publication rule, not implied permission.

## Hard Gates

- Original PDF unavailable: formal source-fidelity and final acceptance remain `blocked`.
- Any open, invalidated, expired, or unreviewed decision: the owning spec cannot pass.
- Any included logical source atom not represented exactly once: Spec 03 fails.
- Any included media atom without exactly one closed source representation, or
  any source-region review not bound to the generated crop hash: Spec 03 fails.
- Any formal-native scope without exact page-level visual review, any meaningful
  visual region left unrepresented/unexcluded/open, any media candidate without
  a byte-bound standalone-suitability disposition, or any contaminated crop not
  replaced by a closed source-backed composite group: Spec 03 and `PG-H14` fail.
- Missing, rejected, mismatched, or drifted promotion evidence: Spec 04 intake fails.
- A formal-native promotion without a live-valid source-lineage integrity
  report, unchanged cumulative decisions, exact child fragment copies, standard
  ledger identity, or precise closed source-order queue fails.
- A formal-native promotion without a live-valid producer execution capability,
  exact E-to-D-to-L-to-M binding, and independently bound evaluator capability
  fails. A matching version label without matching bytes is insufficient.
- Any semantic choice made during rendering: Spec 04/05 boundary fails.
- Any source-required TOC level without an exact template-renderable,
  hierarchy-preserving strategy: Spec 04-D and its promotion fail.
- Any frozen template or API drift: Spec 05 fails.
- Any definition or call of a template-local custom command or environment in
  the generated body fails `TP-H14`; the producer report and independent
  promotion rescan must both pass on the same bound bytes.
- Any formal delivery ZIP greater than or equal to `50,000,000` bytes, or any
  producer/evaluator size-report mismatch, fails `CP-H18` and the independent
  Spec 05 promotion. Size optimization may touch only a new delivery copy and
  may not silently change media selection, count, semantic binding, or source
  evidence; if safe transport encoding is insufficient, return to Spec 03/04.
- Any formal delivery ZIP with `2,000` or more file entities, any unresolved or
  unreferenced project media, or any PDF-pack/image-to-PDF transport fails
  `CP-H19`/`CP-H20`. Exact-byte deduplication is legal; semantic deletion is
  not. If the cap cannot be met mechanically, return to Spec 02/03 to review
  source-backed decorative/noise candidates and replay all downstream stages.
- Any formal-native Spec 05 run without `template-contract/2.0`, exact approved
  cover/logo materialization, closed presentation decisions, or the
  `presentation_inference` prohibition: promotion fails.
- Any delivery whose root entry/body payload contract is not independently
  reproducible, or whose outer ZIP/PDF name does not match the frozen cover
  title and volume label, fails `CP-H25`/`CP-H26`.
- Any uncontrolled body loader, non-reconstructing semantic unit sequence, or
  body transport `.tex` at or above `900,000` bytes fails `CP-H25`/`CP-H27`.
- Any raster image at or above `1,000,000` bytes fails `CP-H28` and returns to
  the media-representation owner; Spec 05 must not alter it.
- Placeholder/fallback content, silent deletion, or missing asset substitution: Spec 05 fails.
- Compile success without complete render coverage and page review: not a product pass.
- Any final candidate page without exact deterministic provenance, any model-
  selected page mapping, any unexplained generated page, any duplicate
  candidate raster, or any producer/evaluator provenance disagreement: Spec 06
  fails closed.
- Golden sample without explicit user acceptance: not `human_accepted`.

## Ownership and Private Modules

- Upstream evidence rebuilding may use `pdf-clean-markdown-rebuild` or equivalent, but this round does not comprehensively rewrite those cleaners.
- Semantic annotation may use `material-semantic-annotator`, but its output must be consumed into the canonical ledger and frozen plan.
- Mechanical rendering is owned by `cleanlatex-to-elegantbook`'s new frozen-plan renderer.
- LaTeX-local observation is owned by audit-only `refine-elegantbook-latex`.
- Final page review remains independent and must inspect every page.

Before invoking any private skill, read its `SKILL.md`. Scripts, skill rules, and model judgment are three separate capability layers; wiring scripts together does not establish full compliance.

## Completion Language

Report the exact achieved gate: contract validation, mechanical render, template integrity, compile pass, render coverage, full-page review, or human acceptance. Do not compress these into “done.” A single golden sample proves that sample and provides regression evidence; reusable capability requires additional material-type and language samples.
