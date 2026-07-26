from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_release_prefix_isolated_from_material_namespace() -> None:
    publish = load_script("publish_stable_release.py")
    assert publish.clean_prefix("compiler-releases") == "compiler-releases"
    for forbidden in ("elegantbook", "elegantbook/releases", "latex", "minerupopo"):
        try:
            publish.clean_prefix(forbidden)
        except SystemExit:
            pass
        else:
            raise AssertionError(f"unsafe prefix accepted: {forbidden}")


def test_env_reader_does_not_require_shell(tmp_path: Path) -> None:
    publish = load_script("publish_stable_release.py")
    env = tmp_path / ".env"
    env.write_text("MINIO_ENDPOINT=http://127.0.0.1:9000\nMINIO_ACCESS_KEY='user'\nMINIO_SECRET_KEY=secret\n")
    values = publish.read_env(env)
    assert values["MINIO_ACCESS_KEY"] == "user"
    assert publish.endpoint_config(values["MINIO_ENDPOINT"]) == ("127.0.0.1:9000", False)


def test_stable_cli_version_is_not_release_candidate() -> None:
    text = (ROOT / "scripts" / "elegantbookcompiler.py").read_text(encoding="utf-8")
    assert 'VERSION = "1.0.0"' in text
    assert 'VERSION = "1.0.0-rc1"' not in text
