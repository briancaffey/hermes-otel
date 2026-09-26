"""The dashboard's Live source follows the profile of the request (#70)."""

from __future__ import annotations

from hermes_otel import live_store as ls


def test_each_home_gets_its_own_store_and_the_singleton_is_reused(tmp_path, monkeypatch):
    monkeypatch.setattr(ls, "_LIVE_STORE", None)
    monkeypatch.setattr(ls, "_STORES_BY_PATH", {})
    monkeypatch.delenv("HERMES_OTEL_LIVE_DB", raising=False)
    home_a, home_b = tmp_path / "a", tmp_path / "b"
    home_a.mkdir()
    home_b.mkdir()

    monkeypatch.setattr(ls, "_default_db_path", lambda: ls.default_db_path_for(home_a))
    store_a = ls.get_live_store_for_home()
    assert store_a is not None and store_a.db_path == ls.default_db_path_for(home_a)
    assert ls.get_live_store_for_home() is store_a

    monkeypatch.setattr(ls, "_default_db_path", lambda: ls.default_db_path_for(home_b))
    store_b = ls.get_live_store_for_home()
    assert store_b is not None and store_b is not store_a
    assert store_b.db_path == ls.default_db_path_for(home_b)

    # The tracer's process-wide store is handed back when it is the same file.
    monkeypatch.setattr(ls, "_LIVE_STORE", store_b)
    monkeypatch.setattr(ls, "_STORES_BY_PATH", {})
    assert ls.get_live_store_for_home() is store_b
    for st in (store_a, store_b):
        st.close() if hasattr(st, "close") else None
