from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COMPOSE = (ROOT / "docker-compose.luceon-review.yml").read_text(encoding="utf-8")
RC_COMPOSE = (ROOT / "docker-compose.worker-v3-rc.yml").read_text(encoding="utf-8")


def _service(name: str) -> str:
    marker = f"  {name}:\n"
    start = COMPOSE.index(marker)
    next_service = COMPOSE.find("\n  ", start + len(marker))
    while next_service >= 0:
        line_end = COMPOSE.find("\n", next_service + 1)
        line = COMPOSE[next_service + 1 : line_end if line_end >= 0 else None]
        if line.endswith(":") and not line.startswith("    "):
            break
        next_service = COMPOSE.find("\n  ", next_service + 3)
    return COMPOSE[start : next_service if next_service >= 0 else len(COMPOSE)]


def test_v3_services_are_opt_in_and_v2_service_is_untouched_by_v3_mounts():
    for name in (
        "workflow-v3-executor",
        "workflow-v3-evaluator",
        "workflow-v3-promoter",
        "workflow-v3-projector",
    ):
        block = _service(name)
        assert "profiles:\n      - worker-v3" in block
        assert "/var/run/docker.sock" not in block
        assert "/.codex/skills" not in block
        assert "WORKFLOW_V3_ENABLED=${WORKFLOW_V3_ENABLED:-false}" in block
        if name != "workflow-v3-projector":
            assert (
                "${LUCEON_RUNTIME_ROOT:-./runtime}/worker-v3/releases:"
                "/worker-v3/releases:ro"
            ) in block
    assert "WORKFLOW_V3_" not in _service("workflow-v2-worker")


def test_all_v3_runtime_roles_use_the_same_dedicated_image_variable():
    for name in (
        "workflow-v3-executor",
        "workflow-v3-evaluator",
        "workflow-v3-promoter",
        "workflow-v3-projector",
    ):
        block = _service(name)
        assert "image: ${LUCEON_WORKER_V3_IMAGE" in block
        assert "LUCEON_REVIEW_BACKEND_IMAGE" not in block


def test_rc_compose_requires_digest_pinned_image_and_keeps_healthchecks_enabled():
    for name in (
        "workflow-v3-executor",
        "workflow-v3-evaluator",
        "workflow-v3-promoter",
        "workflow-v3-projector",
    ):
        block = _service(name)
        assert "healthcheck:\n      disable: true" not in block
        assert (
            f"  {name}:\n"
            "    image: ${LUCEON_WORKER_V3_IMAGE:?"
        ) in RC_COMPOSE
    assert "WORKER_V3_IMAGE_REFERENCE=${LUCEON_WORKER_V3_IMAGE:?" in RC_COMPOSE
    assert "luceonweb2026-worker-v3-runtime:local" not in RC_COMPOSE
    assert "${LUCEON_WORKER_V3_IMAGE:?" not in COMPOSE


def test_producer_evaluator_and_promoter_have_distinct_identities_and_work_roots():
    producer = _service("workflow-v3-executor")
    evaluator = _service("workflow-v3-evaluator")
    promoter = _service("workflow-v3-promoter")
    assert "worker-v3-producer-local" in producer
    assert "work/producer:/worker-v3/work" in producer
    assert "DASHSCOPE_API_KEY=${DASHSCOPE_API_KEY:-}" in producer
    assert (
        "DASHSCOPE_BASE_URL=${DASHSCOPE_BASE_URL:-"
        "https://dashscope.aliyuncs.com/compatible-mode/v1}"
    ) in producer
    assert "DASHSCOPE_VISION_MODEL" in producer
    assert "qwen3.7-plus-2026-05-26" in producer
    assert "worker-v3-independent-evaluator-local" in evaluator
    assert "work/evaluator:/worker-v3/evaluation-work" in evaluator
    assert "work/producer:/worker-v3/work:ro" in evaluator
    assert "work/producer:/worker-v3/work\n" not in evaluator
    assert "WORKFLOW_V3_ARTIFACT_BACKEND=minio" in evaluator
    assert "WORKFLOW_V3_EVALUATOR_MINIO_ACCESS_KEY" in evaluator
    assert "WORKFLOW_V3_EVALUATOR_MINIO_SECRET_KEY" in evaluator
    assert "artifacts:/worker-v3/artifacts" not in evaluator
    assert "worker-v3-promotion-controller-local" in promoter
    assert "work/promoter:/worker-v3/promotion-work" in promoter
    assert "WORKFLOW_V3_ARTIFACT_BACKEND=minio" in promoter
    assert "WORKFLOW_V3_PROMOTER_MINIO_ACCESS_KEY" in promoter
    assert "WORKFLOW_V3_PROMOTER_MINIO_SECRET_KEY" in promoter
    assert "artifacts:/worker-v3/artifacts" not in promoter


def test_ordinary_v3_roles_receive_only_role_specific_minio_credentials():
    role_services = {
        "workflow-v3-executor": "PRODUCER",
        "workflow-v3-evaluator": "EVALUATOR",
        "workflow-v3-promoter": "PROMOTER",
        "workflow-v3-projector": "PROJECTOR",
    }
    for service, role in role_services.items():
        block = _service(service)
        assert f"WORKFLOW_V3_{role}_MINIO_ENDPOINT" in block
        assert f"WORKFLOW_V3_{role}_MINIO_ACCESS_KEY" in block
        assert f"WORKFLOW_V3_{role}_MINIO_SECRET_KEY" in block
        assert "WORKFLOW_V3_MINIO_CREDENTIAL_FINGERPRINTS" in block
        assert "\n      - MINIO_ACCESS_KEY=" not in block
        assert "\n      - MINIO_SECRET_KEY=" not in block
        for peer in set(role_services.values()) - {role}:
            assert f"WORKFLOW_V3_{peer}_MINIO_ACCESS_KEY" not in block
            assert f"WORKFLOW_V3_{peer}_MINIO_SECRET_KEY" not in block


def test_rc_overlay_requires_each_role_credential_and_distinct_matrix():
    for role in ("PRODUCER", "EVALUATOR", "PROMOTER", "PROJECTOR"):
        assert (
            f"WORKFLOW_V3_{role}_MINIO_ACCESS_KEY=${{"
            f"WORKFLOW_V3_{role}_MINIO_ACCESS_KEY:?"
        ) in RC_COMPOSE
        assert (
            f"WORKFLOW_V3_{role}_MINIO_SECRET_KEY=${{"
            f"WORKFLOW_V3_{role}_MINIO_SECRET_KEY:?"
        ) in RC_COMPOSE
    assert RC_COMPOSE.count("LUCEON_ENVIRONMENT=rc") == 4
    assert RC_COMPOSE.count(
        "WORKFLOW_V3_MINIO_CREDENTIAL_FINGERPRINTS=${"
        "WORKFLOW_V3_MINIO_CREDENTIAL_FINGERPRINTS:?"
    ) == 4


def test_rc_overlay_admits_minio_for_backend_control_plane():
    assert (
        "  backend:\n"
        "    environment:\n"
        "      - WORKFLOW_V3_ARTIFACT_BACKEND=minio"
    ) in RC_COMPOSE


def test_v3_minio_namespaces_are_isolated_from_v2_defaults():
    producer = _service("workflow-v3-executor")
    projector = _service("workflow-v3-projector")
    expected_candidate_prefix = (
        "WORKFLOW_V3_CANDIDATE_PREFIX=${WORKFLOW_V3_CANDIDATE_PREFIX:-"
        "v3/candidates}"
    )
    assert expected_candidate_prefix in producer
    assert "WORKFLOW_V3_CANDIDATE_BUCKET=${WORKFLOW_V3_CANDIDATE_BUCKET:-worker-v3-candidates}" in producer
    assert "WORKFLOW_V3_FORMAL_BUCKET=${WORKFLOW_V3_FORMAL_BUCKET:-eduassets-elegantbook}" in projector
    assert "WORKFLOW_V3_FORMAL_PREFIX=${WORKFLOW_V3_FORMAL_PREFIX:-elegantbook/v3}" in projector
    assert "worker-v3-projector-local" in projector
    assert "work/projector:/worker-v3/projection-work" in projector
    assert (
        "${LUCEON_RUNTIME_ROOT:-./runtime}/worker-v3/releases:"
        "/worker-v3/releases:ro"
    ) in projector


def test_production_compose_has_no_codex_expert_runtime_surface():
    lowered = COMPOSE.lower()
    for forbidden in (
        "workflow-v3-expert",
        "workflow-v3-codex-expert",
        "workflow_v3_expert",
        "dockerfile.codex-expert",
        "codex_home",
        "app_server",
    ):
        assert forbidden not in lowered


def test_backend_feature_flag_defaults_off_and_release_mount_is_read_only():
    backend = _service("backend")
    assert "WORKFLOW_V3_ENABLED=${WORKFLOW_V3_ENABLED:-false}" in backend
    assert "WORKFLOW_V3_DATABASE_URL=${WORKFLOW_V3_DATABASE_URL:-sqlite:////data/workflow-v3.db}" in backend
    assert "WORKFLOW_V3_RELEASES_ROOT=/worker-v3/releases" in backend
    assert (
        "${LUCEON_RUNTIME_ROOT:-./runtime}/worker-v3/releases:"
        "/worker-v3/releases:ro"
    ) in backend


def test_all_runtime_bind_sources_share_one_overridable_root():
    runtime_volume_lines = [
        line.strip()
        for line in COMPOSE.splitlines()
        if (
            line.lstrip().startswith("- ")
            and "/runtime" in line
            and ":/" in line
        )
    ]
    assert runtime_volume_lines
    assert all("${LUCEON_RUNTIME_ROOT:-./runtime}" in line for line in runtime_volume_lines)
    assert "\n      - ./runtime" not in COMPOSE
