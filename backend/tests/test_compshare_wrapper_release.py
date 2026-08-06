from __future__ import annotations

import pytest

from scripts.compshare_wrapper_release import (
    AFTER_SHA256,
    BEFORE_SHA256,
    NEW_BLOCK,
    OLD_BLOCK,
    REMOTE_ROLLBACK_BACKUP,
    REMOTE_SOURCE_PATH,
    WrapperSourceDrift,
    transform_source,
)


def test_task36_wrapper_release_candidate_preserves_row_keys_and_adds_complete_pagination():
    updated, status = transform_source(("prefix\n" + OLD_BLOCK + "suffix\n").encode(), enforce_remote_identity=False)
    text = updated.decode()
    assert status == "transformed"
    assert OLD_BLOCK not in text
    assert text.count(NEW_BLOCK) == 1
    assert 'key="jobs"' in text
    assert text.count('key="batches"') == 3
    assert '"total": total' in text
    assert '"next_cursor": str(next_offset) if has_more else ""' in text
    assert '"has_more": has_more' in text


def test_task36_wrapper_release_candidate_fails_closed_on_source_drift():
    with pytest.raises(WrapperSourceDrift, match="unexpected wrapper source sha256"):
        transform_source(b"not-the-live-wrapper")


def test_task36_wrapper_release_candidate_freezes_remote_and_rollback_identity():
    assert BEFORE_SHA256 == "3c0be8255cd6e6bef37900413cea496f14a0af253aa37e0e7763c0511923310f"
    assert AFTER_SHA256 == "cad0dbfe2e783c625d22c95931bf9495d577784de2eba9384118d1ee6e163673"
    assert REMOTE_SOURCE_PATH == "/root/mineru-popo-service/wrapper_app.py"
    assert REMOTE_ROLLBACK_BACKUP.endswith("task36-backup-20260806T093300Z")
