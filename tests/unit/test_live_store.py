"""Unit tests for the in-process live store (zero-config dashboard)."""

from hermes_otel.live_store import LiveStore, get_live_store


class TestLiveStore:
    def test_bounded_ring_buffer(self):
        s = LiveStore(max_spans=3)
        for i in range(5):
            s.add_span({"name": f"s{i}"})
        names = [sp["name"] for sp in s.spans()]
        assert names == ["s2", "s3", "s4"]  # oldest dropped

    def test_monotonic_cursor_and_since(self):
        s = LiveStore()
        s.add_span({"name": "a"})
        s.add_metric("m", 1, {}, 0)
        s.add_span({"name": "b"})
        cur_after_a = s.spans()[0]["seq"]
        # since filters strictly greater
        later = s.spans(since=cur_after_a)
        assert [sp["name"] for sp in later] == ["b"]
        assert s.cursor() == 3  # 3 items total, shared sequence

    def test_seq_shared_across_signals(self):
        s = LiveStore()
        s.add_span({"name": "a"})
        s.add_metric("m", 1, {}, 0)
        s.add_log({"body": "x"})
        assert s.spans()[0]["seq"] == 1
        assert s.metrics()[0]["seq"] == 2
        assert s.logs()[0]["seq"] == 3

    def test_limit(self):
        s = LiveStore()
        for i in range(10):
            s.add_span({"name": str(i)})
        assert len(s.spans(limit=3)) == 3
        assert [sp["name"] for sp in s.spans(limit=3)] == ["7", "8", "9"]

    def test_metric_shape(self):
        s = LiveStore()
        s.add_metric("hermes.cost.usage", 0.05, {"model": "gpt-4"}, 1234)
        m = s.metrics()[0]
        assert m["name"] == "hermes.cost.usage"
        assert m["value"] == 0.05
        assert m["attributes"] == {"model": "gpt-4"}
        assert m["time_unix_nano"] == 1234

    def test_stats_and_clear(self):
        s = LiveStore()
        s.add_span({"name": "a"})
        s.add_metric("m", 1, {}, 0)
        st = s.stats()
        assert st["spans"] == 1 and st["metrics"] == 1 and st["cursor"] == 2
        s.clear()
        assert s.stats() == {"spans": 0, "metrics": 0, "logs": 0, "cursor": 0}

    def test_singleton_create_flag(self):
        # create=False never builds it; create=True does and is sticky.
        import hermes_otel.live_store as ls

        ls._LIVE_STORE = None
        assert get_live_store(create=False) is None
        a = get_live_store(create=True, max_spans=10)
        b = get_live_store(create=False)
        assert a is b is not None
        ls._LIVE_STORE = None  # cleanup for other tests
