from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = BACKEND_ROOT / "Dockerfile.worker-v3"
PYTHON_LOCK = BACKEND_ROOT / "requirements-worker-v3.lock"
SYSTEM_LOCK = BACKEND_ROOT / "worker-v3-system-packages.lock"
IDENTITY_SCRIPT = BACKEND_ROOT / "scripts" / "workflow_v3_runtime_identity.py"
REPOSITORY_ROOT = BACKEND_ROOT.parent
RELEASE_ROOT = REPOSITORY_ROOT / "release" / "worker-v3"
RELEASE_RECIPE = RELEASE_ROOT / "recipe.current-audit.json"
RUNTIME_EVIDENCE_ROOT = RELEASE_ROOT / "runtime"
PINNED_BASE = (
    "python:3.12.13-slim@"
    "sha256:64695412729fbe8cf054511723820c82bbe5a077d4a6b4070cd4a7225d3422ce"
)


def _identity_module():
    spec = importlib.util.spec_from_file_location("workflow_v3_runtime_identity", IDENTITY_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_worker_v3_runtime_is_digest_pinned_hash_locked_and_non_root() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert f"FROM {PINNED_BASE}" in dockerfile
    assert "--require-hashes -r /app/requirements-worker-v3.lock" in dockerfile
    assert "apt-get upgrade" not in dockerfile
    assert "USER 10003:10003" in dockerfile
    assert "HEALTHCHECK" in dockerfile
    assert "Dockerfile.codex-expert" not in dockerfile
    assert "chmod 0444 /opt/worker-v3/control-plane-baseline.json" in dockerfile
    assert "chown -R 10003:10003 /worker-v3 /opt/worker-v3" not in dockerfile


def test_worker_v3_system_lock_is_installed_in_bounded_batches() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert "while IFS= read -r package; do" in dockerfile
    assert 'install -y --no-install-recommends "$package"' in dockerfile
    assert "$(cat /tmp/worker-v3-system-packages.install)" not in dockerfile


def test_worker_v3_docker_context_excludes_codex_expert_runtime() -> None:
    dockerignore = (BACKEND_ROOT / ".dockerignore").read_text(encoding="utf-8")
    required_patterns = {
        "Dockerfile.codex-expert",
        "requirements-codex-expert.txt",
        "app/workflow_v3/codex_app_server_broker.py",
        "app/workflow_v3/codex_sdk_adapter.py",
        "app/workflow_v3/expert.py",
        "app/workflow_v3/expert_broker.py",
        "app/workflow_v3/expert_capability.py",
        "app/workflow_v3/expert_control.py",
        "app/workflow_v3/expert_models.py",
        "app/workflow_v3/expert_spool.py",
        "scripts/workflow_v3_codex_expert.py",
        "scripts/workflow_v3_expert_*.py",
        "scripts/codex_*.py",
        "tests/test_codex_*.py",
        "**/__pycache__/",
        "**/*.pyc",
        "**/*.pyo",
    }
    active_patterns = {
        line.strip()
        for line in dockerignore.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert required_patterns <= active_patterns


def test_worker_v3_runtime_declares_stage8_to_11_toolchain() -> None:
    packages = SYSTEM_LOCK.read_text(encoding="utf-8")
    required = {
        "fontconfig",
        "fonts-noto-core",
        "fonts-noto-cjk",
        "fonts-texgyre",
        "poppler-utils",
        "qpdf",
        "ghostscript",
        "texlive-xetex",
        "texlive-bibtex-extra",
        "latexmk",
        "texlive-latex-extra",
        "texlive-fonts-recommended",
        "texlive-fonts-extra",
        "texlive-lang-chinese",
        "texlive-science",
        "texlive-pictures",
        "texlive-plain-generic",
        "biber",
    }
    locked = {
        line.split("=", 1)[0]
        for line in packages.splitlines()
        if line and not line.startswith("#")
    }
    assert required <= locked
    assert all(
        "=" in line
        for line in packages.splitlines()
        if line and not line.startswith("#")
    )


def test_worker_v3_python_lock_is_transitive_and_hash_pinned() -> None:
    requirements = (BACKEND_ROOT / "requirements.txt").read_text(encoding="utf-8")
    lock = PYTHON_LOCK.read_text(encoding="utf-8")

    for raw_line in requirements.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        normalized = line.replace("[standard]", "")
        assert normalized.lower() in lock.lower()
    assert lock.count("--hash=sha256:") > 20


def test_runtime_identity_validator_fails_closed() -> None:
    module = _identity_module()
    identity = {
        "non_root": False,
        "locks": {
            "python": {"sha256": "actual", "expected_sha256": "expected"},
            "system": {"sha256": "same", "expected_sha256": "same"},
        },
        "system_packages": {
            "qpdf": {"matches": False},
        },
        "tools": {
            "xelatex": {"available": False},
        },
    }

    errors = module.validate(identity)
    assert "runtime_id_unbound" in errors
    assert "base_image_unknown" in errors
    assert "container_image_digest_missing_or_invalid" in errors
    assert "control_plane_baseline_missing_or_invalid" in errors
    assert "runtime_must_not_run_as_root" in errors
    assert "python_lock_sha256_mismatch" in errors
    assert "system_package_version_mismatch:qpdf" in errors
    assert "required_tool_unavailable:xelatex" in errors


def test_runtime_image_reference_accepts_only_content_addressed_forms(
    monkeypatch,
) -> None:
    module = _identity_module()
    digest = f"sha256:{'d' * 64}"

    monkeypatch.setenv("WORKER_V3_IMAGE_REFERENCE", digest)
    assert module._runtime_image_identity() == (digest, True)

    monkeypatch.setenv(
        "WORKER_V3_IMAGE_REFERENCE",
        f"registry.example:5000/luceon/worker-v3@{digest}",
    )
    assert module._runtime_image_identity() == (digest, True)

    for mutable in (
        "luceon/worker-v3:rc",
        f"luceon/worker-v3:rc@{digest}",
        "",
    ):
        monkeypatch.setenv("WORKER_V3_IMAGE_REFERENCE", mutable)
        assert module._runtime_image_identity() == (None, False)


def test_control_plane_baseline_detects_drift(tmp_path, monkeypatch) -> None:
    module = _identity_module()
    app_root = tmp_path / "app"
    (app_root / "app" / "workflow_v3").mkdir(parents=True)
    (app_root / "scripts").mkdir()
    source = app_root / "app" / "workflow_v3" / "runtime.py"
    source.write_text("BOUND = True\n", encoding="utf-8")
    (app_root / "scripts" / "workflow_v3_worker.py").write_text(
        "print('worker')\n",
        encoding="utf-8",
    )
    manifest_path = tmp_path / "control-plane.json"
    monkeypatch.setattr(module, "APP_ROOT", app_root)
    monkeypatch.setattr(module, "CONTROL_PLANE_MANIFEST", manifest_path)

    module.write_control_plane_baseline(manifest_path)
    assert module.measure_control_plane_baseline()["matches"] is True

    source.write_text("BOUND = False\n", encoding="utf-8")
    measured = module.measure_control_plane_baseline()
    assert measured["matches"] is False
    assert measured["actual_tree_sha256"] != measured["expected_tree_sha256"]


def test_sbom_contains_python_and_all_dpkg_components(monkeypatch) -> None:
    module = _identity_module()
    monkeypatch.setattr(
        module,
        "_all_installed_system_packages",
        lambda: {"qpdf:arm64": "12.2.0-1", "texlive-xetex": "2024.20250309-1"},
    )
    monkeypatch.setattr(
        module,
        "_python_components",
        lambda: [
            {
                "type": "library",
                "name": "PyMuPDF",
                "version": "1.26.1",
                "purl": "pkg:pypi/pymupdf@1.26.1",
                "properties": [{"name": "luceon.component.ecosystem", "value": "python"}],
            }
        ],
    )
    identity = {
        "runtime_id": "runtime-test",
        "base_image": PINNED_BASE,
        "locks": {"python": {"sha256": "a"}, "system": {"sha256": "b"}},
    }

    sbom = module.build_sbom(identity)

    assert sbom["bomFormat"] == "CycloneDX"
    assert {row["name"] for row in sbom["components"]} == {
        "PyMuPDF",
        "qpdf:arm64",
        "texlive-xetex",
    }


def test_dockerfile_embeds_current_python_lock_digest() -> None:
    expected_python = hashlib.sha256(PYTHON_LOCK.read_bytes()).hexdigest()
    expected_system = hashlib.sha256(SYSTEM_LOCK.read_bytes()).hexdigest()
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    assert f"WORKER_V3_PYTHON_LOCK_SHA256={expected_python}" in dockerfile
    assert f"WORKER_V3_SYSTEM_LOCK_SHA256={expected_system}" in dockerfile


def test_release_recipe_binds_exact_runtime_build_evidence() -> None:
    recipe = json.loads(RELEASE_RECIPE.read_text(encoding="utf-8"))
    identity = json.loads(
        (RUNTIME_EVIDENCE_ROOT / "ordinary-runtime-identity.json").read_text(
            encoding="utf-8"
        )
    )
    build = json.loads(
        (RUNTIME_EVIDENCE_ROOT / "ordinary-runtime-build-proof.json").read_text(
            encoding="utf-8"
        )
    )

    assert recipe["release"]["source"]["git_sha"] == build["source_revision"]
    assert (
        recipe["runtime"]["system_tools"]["runtime_id"]
        == identity["runtime_id"]
        == build["runtime_id"]
    )
    assert (
        recipe["runtime"]["container_image_digest"]
        == identity["image_digest"]
        == build["image_id"]
        == build["local_manifest_digest"]
    )
