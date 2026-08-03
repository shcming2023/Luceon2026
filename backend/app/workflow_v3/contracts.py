from __future__ import annotations

from dataclasses import asdict, dataclass


WORKFLOW_VERSION = "worker-v3.0.0-rc1"


class UnknownWorkflowVersion(ValueError):
    """Raised when persisted work refers to an unregistered workflow release."""


@dataclass(frozen=True)
class StageContract:
    key: str
    order: int
    owner: str
    skill_name: str
    stage_version: str
    input_schema: str
    output_schema: str
    acceptance_gates: tuple[str, ...]

    def to_dict(self) -> dict:
        value = asdict(self)
        value["acceptance_gates"] = list(self.acceptance_gates)
        return value


STAGE_CONTRACTS = (
    StageContract(
        key="intake_snapshot",
        order=10,
        owner="deterministic_code",
        skill_name="luceon-popo-to-refined-elegantbook",
        stage_version="spec01.v1",
        input_schema="luceon.v3.frozen-popo-source/v1",
        output_schema="luceon.v3.intake-snapshot/v1",
        acceptance_gates=(
            "source_pdf_identity_verified",
            "popo_manifest_identity_verified",
            "skill_release_identity_verified",
            "template_identity_verified",
        ),
    ),
    StageContract(
        key="source_scope_and_order",
        order=20,
        owner="bounded_llm_and_code",
        skill_name="pdf-clean-markdown-rebuild",
        stage_version="spec02.v1",
        input_schema="luceon.v3.intake-snapshot/v1",
        output_schema="luceon.v3.source-scope-order/v1",
        acceptance_gates=(
            "every_source_page_accounted_for",
            "body_scope_closed",
            "reading_order_closed",
            "open_source_ambiguities_zero",
        ),
    ),
    StageContract(
        key="canonical_block_ledger",
        order=30,
        owner="bounded_llm_and_code",
        skill_name="pdf-clean-markdown-rebuild",
        stage_version="spec03.v1",
        input_schema="luceon.v3.source-scope-order/v1",
        output_schema="luceon.v3.canonical-block-ledger/v1",
        acceptance_gates=(
            "canonical_ids_unique",
            "source_lineage_complete",
            "content_conservation_passed",
            "media_relations_closed",
        ),
    ),
    StageContract(
        key="outline_reconstruction",
        order=40,
        owner="bounded_llm_and_code",
        skill_name="pdf-clean-markdown-rebuild",
        stage_version="spec04a.v1",
        input_schema="luceon.v3.canonical-block-ledger/v1",
        output_schema="luceon.v3.outline/v1",
        acceptance_gates=(
            "outline_source_evidenced",
            "outline_hierarchy_valid",
            "outline_body_coverage_complete",
            "outline_accuracy_at_least_99_percent",
            "open_outline_decisions_zero",
        ),
    ),
    StageContract(
        key="semantic_annotation",
        order=50,
        owner="bounded_llm_and_code",
        skill_name="material-semantic-annotator",
        stage_version="spec04b.v1",
        input_schema="luceon.v3.outline/v1",
        output_schema="luceon.v3.semantic-annotation/v1",
        acceptance_gates=(
            "every_canonical_block_assigned_once",
            "semantic_relations_valid",
            "source_text_not_rewritten",
            "open_semantic_decisions_zero",
        ),
    ),
    StageContract(
        key="template_construct_binding",
        order=60,
        owner="deterministic_code",
        skill_name="cleanlatex-to-elegantbook",
        stage_version="spec04c.v1",
        input_schema="luceon.v3.semantic-annotation/v1",
        output_schema="luceon.v3.template-binding/v1",
        acceptance_gates=(
            "constructs_allowlisted",
            "template_hash_matches_release",
            "template_local_api_unchanged",
            "all_bindings_source_traceable",
        ),
    ),
    StageContract(
        key="frozen_render_plan",
        order=70,
        owner="deterministic_code",
        skill_name="cleanlatex-to-elegantbook",
        stage_version="spec04d.v1",
        input_schema="luceon.v3.template-binding/v1",
        output_schema="luceon.v3.frozen-render-plan/v1",
        acceptance_gates=(
            "render_plan_schema_valid",
            "render_plan_fully_bound",
            "render_plan_has_no_open_decisions",
            "volume_partition_valid",
        ),
    ),
    StageContract(
        key="deterministic_elegantbook",
        order=80,
        owner="deterministic_code",
        skill_name="cleanlatex-to-elegantbook",
        stage_version="spec05.v1",
        input_schema="luceon.v3.frozen-render-plan/v1",
        output_schema="luceon.v3.elegantbook-candidate/v1",
        acceptance_gates=(
            "formal_native_renderer_used",
            "protected_template_unchanged",
            "delivery_limits_passed",
            "xelatex_recompile_passed",
        ),
    ),
    StageContract(
        key="readonly_latex_audit",
        order=90,
        owner="independent_evaluator",
        skill_name="refine-elegantbook-latex",
        stage_version="spec05-audit.v1",
        input_schema="luceon.v3.elegantbook-candidate/v1",
        output_schema="luceon.v3.latex-audit/v1",
        acceptance_gates=(
            "audit_is_readonly",
            "compile_errors_zero",
            "missing_glyphs_zero",
            "obvious_overflow_zero",
        ),
    ),
    StageContract(
        key="independent_full_page_review",
        order=100,
        owner="independent_evaluator",
        skill_name="finished-textbook-final-review",
        stage_version="spec06.v1",
        input_schema="luceon.v3.latex-audit/v1",
        output_schema="luceon.v3.full-page-review/v1",
        acceptance_gates=(
            "review_pdf_hash_bound",
            "every_page_reviewed",
            "source_fidelity_reviewed",
            "blocking_findings_zero",
        ),
    ),
    StageContract(
        key="delivery_recompile",
        order=110,
        owner="independent_evaluator",
        skill_name="finished-textbook-final-review",
        stage_version="delivery.v1",
        input_schema="luceon.v3.full-page-review/v1",
        output_schema="luceon.v3.delivery-recompile/v1",
        acceptance_gates=(
            "downloaded_zip_hash_verified",
            "independent_xelatex_recompile_passed",
            "compiled_pdf_hash_recorded",
            "delivery_manifest_complete",
        ),
    ),
    StageContract(
        key="ready_for_user_acceptance",
        order=120,
        owner="independent_evaluator",
        skill_name="luceon-popo-to-refined-elegantbook",
        stage_version="promotion.v1",
        input_schema="luceon.v3.delivery-recompile/v1",
        output_schema="luceon.v3.acceptance-candidate/v1",
        acceptance_gates=(
            "all_prior_promotions_verified",
            "page_db_minio_lineage_consistent",
            "open_blockers_zero",
            "human_acceptance_not_self_attested",
        ),
    ),
)


_CONTRACTS_BY_VERSION = {WORKFLOW_VERSION: STAGE_CONTRACTS}


def contracts_for_version(workflow_version: str) -> tuple[StageContract, ...]:
    try:
        return _CONTRACTS_BY_VERSION[workflow_version]
    except KeyError as exc:
        raise UnknownWorkflowVersion(f"unregistered Worker V3 workflow version: {workflow_version}") from exc


def contract_for(workflow_version: str, stage_key: str) -> StageContract:
    for contract in contracts_for_version(workflow_version):
        if contract.key == stage_key:
            return contract
    raise KeyError(f"unknown stage for {workflow_version}: {stage_key}")


def stage_contracts(workflow_version: str = WORKFLOW_VERSION) -> list[dict]:
    return [contract.to_dict() for contract in contracts_for_version(workflow_version)]
