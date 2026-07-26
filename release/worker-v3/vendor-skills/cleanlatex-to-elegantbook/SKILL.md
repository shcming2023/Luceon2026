---
name: cleanlatex-to-elegantbook
description: Mechanically render a closed canonical ledger and frozen render_plan into a user-supplied frozen ElegantBook template. Use for Spec 05 generation when semantic roles, target constructs, parameters, source assets, decision index, capability manifest, and template_contract are already fixed and must not be re-decided during LaTeX generation.
---

# CleanLaTeX to ElegantBook

## Formal Role

Despite its historical name, the approved production path is now a deterministic Spec 05 renderer. It consumes decisions made upstream and is forbidden from choosing headings, boxes, image representations, answer visibility, wording, or layout parameters.

Required immutable inputs:

- `canonical_block_ledger.jsonl` at a closed semantic checkpoint;
- closed `canonical_decision_index.json`;
- frozen `render_plan.json`;
- frozen `volume_partition_plan.json` (one volume by default, at most two);
- byte-bound `template_capability_manifest.json`;
- authoritative frozen `template_contract.json`;
- the exact user-supplied template directory;
- referenced assets and, when planned crops exist, the source PDF and bound page rasters.

For render plans using the media-source-representation v1 boundary, first run
the orchestrator's `validate-render-binding` check. The render node's
`media_binding` is execution evidence: this renderer verifies live source asset
bytes and regenerated crop bytes against it and never substitutes another
candidate.

Older accepted plans that predate `media_binding` remain reproducible and are
reported as `legacy_unbound`; this compatibility path must not be used for a new
formal plan. Do not retrofit or mutate an already accepted immutable plan merely
to make it look like it used the new contract.

The shared contract validator is owned by `luceon-popo-to-refined-elegantbook`. Read that skill before a formal run.

## Freeze the Template Contract

Spec 05 must first create the authoritative contract from the exact read-only template, its source ZIP, the Spec 04 capability manifest, source-grounded metadata, and an explicit body boundary. From this skill's release directory, run:

```bash
python3 scripts/freeze_template_contract.py \
  --template-dir /path/to/template/unpacked \
  --template-zip /path/to/template.zip \
  --capability-manifest /path/to/template_capability_manifest.json \
  --metadata-config /path/to/spec05_metadata.json \
  --presentation-config /path/to/spec05_presentation_config.json \
  --body-marker 'exact marker copied from the template' \
  --output /new/run/contracts/template_contract.json \
  --validation-report /new/run/reports/template_capability_validation_report.json
```

The freezer is read-only over the template. It inventories every non-entry template file as immutable, binds the class, packages, custom API, metadata surface, body boundary, compile policy, source ZIP, and capability manifest, and refuses metadata without source-hash evidence. New formal-native runs must also supply an approved `spec05-presentation-config/1.0`; this creates `template-contract/2.0`. Historical calls without that input remain `template-contract/1.0` compatibility only and retain their hash-bound inline-body transport; new contracts must use the approved generated-body transport.

The presentation config declares exactly `cover` and `logo`. Each uses one explicit mode: `template_default`, `source_region_asset`, or `approved_static_asset`. Every choice needs a unique closed decision and approved compatibility assertion. Source-region assets bind the source PDF, page raster, pixel bbox, exact crop bytes, fit policy, and—when applicable—the source-scope ledger. Source cover/title pages may support output presentation while remaining excluded from body coverage. Configured assets are added under distinct project paths; the original template cover/logo bytes remain frozen and only the existing `\cover{}` and `\logo{}` values may change. The renderer never infers a choice from language, filename, subject words, sample ids, pages, or known hashes.

The marker is explicit input because a universal marker string would be template-specific hardcoding.

## Mechanical Render

```bash
python3 scripts/render_frozen_plan.py \
  --template-dir /path/to/template/unpacked \
  --template-contract /path/to/template_contract.json \
  --ledger /path/to/canonical_block_ledger.jsonl \
  --decision-index /path/to/canonical_decision_index.json \
  --render-plan /path/to/render_plan.json \
  --capability-manifest /path/to/template_capability_manifest.json \
  --asset-root /path/to/mineru \
  --asset-root /path/to/minerupopo \
  --source-pdf /path/to/source.pdf \
  --source-page-dir /path/to/source-page-rasters \
  --media-evidence-ledger /path/to/media_evidence_ledger.json \
  --media-representation-plan /path/to/media_representation_plan.json \
  --out-dir /new/isolated/render-run
```

The renderer refuses to overwrite an output directory. A plan containing any
`media_binding` requires both media contract files. Before writing, the renderer
validates the canonical ledger, decision index, render plan, template contract,
capability manifest, media representation closure, and exact render binding.
It then copies the full template, changes only allowlisted metadata values and
the exact body insertion region, materializes only planned assets/crops, emits
a deterministic body and ZIP, and writes integrity/coverage reports.

For a frozen `response_list` node, the renderer may use only the already-declared `multicol` capability and standard TeX body primitives. It serializes the exact source-bound items, frozen one/two-column count, and frozen inline-rule or vertical-space parameters. It must not infer question boundaries, renumber source questions, choose columns from live text, or change answer-space policy. Heading display labels are likewise consumed exactly from Spec 04-D; their complete source title remains bound in the render payload.

## Formal-native Spec 05 Execution

For a new formal run, prefer `produce_native_spec05.py` over manually stitching
the freezer, renderer, Docker compile, warning review, and raster steps. The
producer accepts only the registry-selected `formal_native` Spec 04-D
promotion whose `full_spec04_status=passed`. A Spec 03-only or bounded media
fixture is not an eligible parent.

Every generated body leaf part, root `main.tex`, and the sole body loader must
be strictly smaller than `900,000` bytes. Equality fails. Editable-text totals
remain measured for deployment diagnostics, but the former 7 MB heuristic is
not a universal Community Edition hard gate. Body sharding is not a volume
split.

Every new formal delivery ZIP must be strictly smaller than `50,000,000`
bytes. This is the non-configurable `CP-H18` product gate: exactly
`50,000,000` bytes fails. The producer measures the exact packaged bytes,
writes `reports/delivery_size_report.json`, and stops before compilation when
the cap is not met. The independent promotion evaluator must measure the ZIP
again and compare its result with the bound producer report.

The same exact ZIP must contain strictly fewer than `2,000` file entities
(`CP-H19`; directory entries do not count). Image-bearing render nodes remain
ordinary native `.jpg`, `.jpeg`, or `.png` project assets. Combining them into
PDF packs, converting raster images to PDF, or using another container merely
to evade the entity limit is forbidden by `CP-H20`. The renderer may reuse one
native file for multiple nodes only when the source bytes are exactly
identical. Before compilation the producer writes
`reports/delivery_asset_report.json`, rejects every unresolved reference and
every unreferenced generated/project media asset, and independently promotion
rescans the exact ZIP. If exact-byte reuse and removal of genuinely
unreferenced files are insufficient, stop and return to Spec 02/03 for a
source-evidenced decision about decorative/noise media; Spec 05 must not delete
or reclassify it.

Every raster image in the exact ZIP must also be strictly smaller than
`1,000,000` bytes (`CP-H28`; equality fails). Spec 05 only verifies the frozen
asset. It must not compress, crop, delete, substitute, or silently degrade an
image to pass; return an oversized asset to Spec 03/04 for a new source-bound
media representation and visual-quality decision.

The formal product artifact is a `delivery_set` containing exactly one or two
independently compilable ElegantBook ZIP/PDF volumes. A single volume remains
mandatory whenever it satisfies the unchanged limits. A two-volume build is
legal only when the active Spec 04-D promotion freezes an exact
`volume_partition_plan/1.2` at a source-supported top-level semantic boundary.
Spec 05 executes that membership mechanically, applies only frozen per-volume
metadata labels, runs all existing template/compile/size/entity/media gates on
both volumes, and proves ordered, disjoint, complete cross-volume render-node
and source-atom coverage. It may not select the cut, create a third volume,
delete media, change image representation, or package both projects as one ZIP.

Every new delivery keeps a root `main.tex` as the project entry. The renderer
writes a sole approved body entry at `body/generated-body.tex` and places exactly
one standard `\input{body/generated-body.tex}` in the frozen body region. The
entry is a loader containing only ordered direct inputs of
`body/units/unit-NNNN/part-NNNN.tex`. Unit membership comes only from Spec
04-D's source-supported top-level structures; an oversized unit is split
inside itself at complete render-node or line boundaries. Concatenating all
parts must reproduce `rendered_body.tex` byte for byte; parts may not load files
or define TeX behavior. `CP-H25` and `CP-H27` are independently recomputed.

Outer ZIP/PDF filenames are mechanically derived from the frozen cover title
and optional frozen volume label, with Unicode-safe portable normalization.
The ZIP-internal entry remains `main.tex`. Never emit a generic outer
`elegantbook-project.zip`, use a material/sample ID, or infer a name from the
source filename. `CP-H26` independently recomputes the expected names.

The producer captures its executable capability before build decisions,
freezes the template contract, invokes the renderer above, packages the final
ZIP before compilation, compiles a fresh extraction of those exact bytes,
and creates a hash-bound page raster for every PDF page with the renderer named
by the approved build policy. Around every frozen render node, the mechanical
renderer emits deterministic standard `hyperref` start/end named destinations;
it adds no custom command or environment. After compilation the producer
resolves those destinations from the exact PDF and writes
`reports/final_pdf_page_provenance.json`, binding every candidate page to the
exact render nodes, source blocks, and physical source pages, or explicitly to
generated front matter / `mapping_uncertain`. The provenance binds the final
PDF, ledger, plan, partition, render execution, template contract,
presentation config, and render pack. `pdftoppm` and `pdftocairo` are
supported; select `pdftocairo` when renderer parity evidence shows that
`pdftoppm` drops embedded CJK glyphs. Its commit
order is `E -> build evidence B -> decision index D -> stage/build commit M`:
compile-warning decisions may cite only already-existing log, PDF, ZIP, and
render-pack evidence, avoiding a D/M hash cycle.

Before compilation, the producer must execute `TP-H14` against the exact
generated `rendered_body.tex`. The bound `template_capability_manifest.json`
is the sole inventory of template-local custom commands and environments; any
definition or call blocks the build. Standard LaTeX constructs and declared
`tcolorbox` styles remain legal. The producer writes
`reports/template_local_api_usage_report.json`, and formal promotion must
independently rescan the same bound bytes rather than trust this self-report.

Formal-native promotion requires `template-contract/2.0`, exact cover/logo bytes in the delivery ZIP, four presentation hard gates, and one closed `CP-R04` decision. A missing, open, hash-drifted, body-scope-conflicting, or silently inferred presentation choice blocks promotion.

All environment choices belong in an approved
`spec05-build-policy/1.0` document. Source-supported cover values belong in an
approved `spec05-metadata/1.0` document. C2 warning closures, when required,
belong in an exact-fingerprint `spec05-warning-review/1.0` document and must
cite rendered page numbers. The core contains no book names, sample ids,
fixed pages, or warning-message exemptions.

```bash
python3 scripts/produce_native_spec05.py \
  --run-dir /new/immutable/spec05-run \
  --run-id material-spec05-v1 \
  --promotion-registry /promotions/registry.json \
  --parent-promotion /promotions/spec04d.promotion.json \
  --parent-lineage-key material/spec04d-render-plan \
  --template-zip /inputs/template.zip \
  --template-intake /contracts/template_intake.json \
  --capability-manifest /spec04c/template_capability_manifest.json \
  --metadata-config /configs/spec05_metadata.json \
  --presentation-config /configs/spec05_presentation_config.json \
  --body-marker 'exact marker copied from the template' \
  --media-evidence-ledger /spec03/media_evidence_ledger.json \
  --media-representation-plan /spec03/media_representation_plan.json \
  --asset-root /inputs/mineru/images \
  --source-pdf /inputs/source.pdf \
  --source-page-dir /evidence/source_pages \
  --build-policy /configs/spec05_build_policy.json
```

The resulting `passed` status proves only `compile_pass` and a complete
`final_render_pack`. Run Spec 03 render coverage and Spec 06 separately; this
producer must not claim or perform them.

## Hard Failures

Fail and return to the owning stage when any of these occurs:

- open or invalidated decisions, open ledger reviews, or non-passed contract status;
- ledger/payload/hash mismatch, missing/duplicate logical coverage, or non-deterministic plan hash;
- a construct/style absent from the bound capability manifest;
- an unsupported target construct or parameter shape;
- missing assets, different-byte basename collisions, unknown crop coordinates, or source PDF hash mismatch;
- a source asset whose live bytes differ from the frozen payload/media
  representation hash, or a regenerated crop whose bytes differ from the
  hash-bound reviewed crop;
- template/class/package/API/masked-scaffold drift;
- any definition or call of a template-local custom command or environment in
  the generated body (`TP-H14`);
- a delivery ZIP whose measured size is greater than or equal to
  `50,000,000` bytes, or a missing/drifted size report (`CP-H18`);
- a delivery ZIP with `2,000` or more file entities, an unresolved media
  reference, or an unreferenced project/generated media asset (`CP-H19`);
- any image transported as a PDF/container instead of its frozen native raster
  representation (`CP-H20`);
- any raster image at or above `1,000,000` bytes (`CP-H28`);
- a delivery-set cardinality outside one or two, a cut that differs from the
  active Spec 04-D partition, a failed per-volume gate, or any cross-volume
  omission, duplication, or order drift (`CP-H21`-`CP-H24`);
- any body-side macro definition, package/input injection other than the exact
  approved generated-body transport, placeholder, fallback, or semantic choice;
- a missing/unbound root `main.tex`, uncontrolled/non-reconstructing body
  transport, any body transport `.tex` at or above 900K, or an
  outer name that differs from the frozen cover identity (`CP-H25`-`CP-H27`).

Do not delete content, switch boxes, resize by heuristics, add packages/macros,
or generate a fallback document to obtain a pass. Spec 05 may remove files that
are mechanically proved unreferenced, and may reuse exact-identical bytes; it
may not reselect, re-encode, pack, or degrade media. If either delivery cap
still cannot be met safely, return to Spec 02/03 (scope/media evidence) and
then replay the frozen downstream chain.

## Outputs and Scope

The renderer creates:

- `project/`, with root `main.tex`, `body/generated-body.tex`, controlled
  `body/units/unit-NNNN/part-NNNN.tex`, and a
  deterministic title-named delivery ZIP/PDF;
- `render/rendered_body.tex`;
- `reports/intermediate_contract_validation.json`;
- `reports/delivery_size_report.json`;
- `reports/delivery_asset_report.json`;
- `reports/overleaf_delivery_compatibility_report.json`;
- `reports/delivery_naming_report.json`;
- `reports/media_contract_validation.json` and
  `reports/media_render_binding_validation.json` for a media-bound plan;
- `reports/render_execution_report.json`;
- `reports/final_pdf_page_provenance.json`;
- `reports/asset_materialization_report.json`;
- `reports/template_integrity_report.{json,md}`;
- `reports/template_local_api_usage_report.json`;
- `manifests/mechanical_render_manifest.json`.

`ready_to_compile` proves only deterministic generation and template integrity. Compilation, full-page review, source fidelity, and user acceptance remain separate gates.

## Legacy Compatibility

`clean_to_elegantbook.py` and `semantic_markdown_to_cleanlatex.py` remain available only for historical or diagnostic workflows. They generate their own template/preamble and may infer mappings, so they are `diagnostic_only` and must not be used for an approved frozen-template product run. Do not extend their heuristic rules in this refactor round; upstream cleaning is explicitly outside scope.
