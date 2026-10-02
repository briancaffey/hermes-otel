"""The live store and the debug log hold full prompts: both must be owner-only (#256)."""

from __future__ import annotations

import os
import stat
import sys

import pytest

from hermes_otel import debug_utils as du
from hermes_otel.live_store import LiveStore

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")


def _mode(path: str) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


class TestLiveStoreMode:
    def test_new_store_and_wal_sidecar_are_owner_only(self, tmp_path):
        old = os.umask(0o022)
        try:
            db = str(tmp_path / "live.db")
            store = LiveStore(db_path=db)
            try:
                store.add_span({"name": "s", "trace_id": "t", "span_id": "a", "attributes": {}})
                store.flush()
                assert _mode(db) == 0o600
                # SQLite copies the database file's mode onto the WAL sidecars.
                for sidecar in (db + "-wal", db + "-shm"):
                    assert os.path.exists(sidecar), sidecar
                    assert _mode(sidecar) == 0o600, sidecar
            finally:
                store.close()
        finally:
            os.umask(old)

    def test_existing_world_readable_store_is_tightened_on_open(self, tmp_path):
        db = str(tmp_path / "live.db")
        store = LiveStore(db_path=db)
        store.close()
        os.chmod(db, 0o644)
        assert _mode(db) == 0o644
        store = LiveStore(db_path=db)
        store.close()
        assert _mode(db) == 0o600


class TestDebugLogMode:
    @pytest.fixture(autouse=True)
    def _fresh_debug_file(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setattr(du, "_DEBUG_ENABLED", True)
        monkeypatch.setattr(du, "_debug_file", None)
        yield
        du.close_debug_log()

    def test_debug_log_is_created_owner_only(self, tmp_path):
        old = os.umask(0o022)
        try:
            du.debug_log("hello")
        finally:
            os.umask(old)
        path = du.debug_log_path()
        assert os.path.exists(path)
        assert _mode(path) == 0o600

    def test_existing_debug_log_is_tightened(self, tmp_path):
        path = du.debug_log_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("old\n")
        os.chmod(path, 0o644)
        du.debug_log("new")
        assert _mode(path) == 0o600
        with open(path, encoding="utf-8") as f:
            assert f.read() == "old\nnew\n"
