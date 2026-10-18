"""The dashboard's Live source follows the profile of the request (#70)."""

from __future__ import annotations

from hermes_otel import live_store as ls
from hermes_otel import live_store_registry as reg


def test_a_creation_error_is_kept_until_the_store_opens(tmp_path, monkeypatch):
    monkeypatch.setattr(reg, "_LIVE_STORE", None)
    monkeypatch.setattr(reg, "_STORES_BY_PATH", {})
    monkeypatch.setattr(reg, "_CREATE_ERRORS", {})
    db = str(tmp_path / "live.db")
    monkeypatch.setenv("HERMES_OTEL_LIVE_DB", db)
    assert reg.create_error_for_home() is None

    def boom(**kwargs):
        raise OSError("disk full")

    real = reg.LiveStore
    monkeypatch.setattr(reg, "LiveStore", boom)
    assert reg.get_live_store_for_home() is None
    assert reg.create_error_for_home() == (db, "OSError: disk full")

    monkeypatch.setattr(reg, "LiveStore", real)
    store = reg.get_live_store_for_home()
    assert store is not None and reg.create_error_for_home() is None
    store.close()


def test_each_home_gets_its_own_store_and_the_singleton_is_reused(tmp_path, monkeypatch):
    monkeypatch.setattr(reg, "_LIVE_STORE", None)
    monkeypatch.setattr(reg, "_STORES_BY_PATH", {})
    monkeypatch.delenv("HERMES_OTEL_LIVE_DB", raising=False)
    home_a, home_b = tmp_path / "a", tmp_path / "b"
    home_a.mkdir()
    home_b.mkdir()

    monkeypatch.setattr(ls, "_default_db_path", lambda: ls.default_db_path_for(home_a))
    store_a = reg.get_live_store_for_home()
    assert store_a is not None and store_a.db_path == ls.default_db_path_for(home_a)
    assert reg.get_live_store_for_home() is store_a

    monkeypatch.setattr(ls, "_default_db_path", lambda: ls.default_db_path_for(home_b))
    store_b = reg.get_live_store_for_home()
    assert store_b is not None and store_b is not store_a
    assert store_b.db_path == ls.default_db_path_for(home_b)

    # The tracer's process-wide store is handed back when it is the same file.
    monkeypatch.setattr(reg, "_LIVE_STORE", store_b)
    monkeypatch.setattr(reg, "_STORES_BY_PATH", {})
    assert reg.get_live_store_for_home() is store_b
    for st in (store_a, store_b):
        st.close() if hasattr(st, "close") else None
