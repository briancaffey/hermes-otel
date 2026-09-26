"""Keyset paging for logs (#186/#194): the cursor on LogFilter, the page
envelope, the live store and every adapter that serves logs."""

from __future__ import annotations

from urllib import parse as _urlparse

import pytest

from hermes_otel.dashboard.backends import _loki
from hermes_otel.dashboard.backends import signoz as sz
from hermes_otel.dashboard.backends import uptrace as up
from hermes_otel.dashboard.backends.base import LogFilter, log_end_ns, log_page, strictly_older
from hermes_otel.dashboard.backends.openobserve import OpenObserveAdapter
from hermes_otel.live_store import LiveStore

NS = 1_000_000_000


def _rec(t, body="x"):
    return {"time_unix_nano": t, "body": body, "logger": "l", "level": "INFO"}


class TestHelpers:
    def test_end_bound_is_window_end_or_the_cursor_whichever_is_earlier(self):
        assert log_end_ns(100, LogFilter()) == 100 * NS
        assert log_end_ns(100, LogFilter(before_ns=50 * NS + 7)) == 50 * NS + 7
        assert log_end_ns(100, LogFilter(before_ns=500 * NS)) == 100 * NS

    def test_strictly_older_cuts_at_the_nanosecond(self):
        rows = [_rec(10), _rec(9), _rec(8)]
        assert strictly_older(rows, LogFilter()) == rows
        assert [r["time_unix_nano"] for r in strictly_older(rows, LogFilter(before_ns=9))] == [8]

    def test_page_envelope(self):
        page = log_page([_rec(30), _rec(20), _rec(10), _rec(5)], limit=3)
        assert (page["has_more"], page["next_before_ns"], len(page["logs"])) == (True, 10, 3)
        short = log_page([_rec(30)], limit=3)
        assert (short["has_more"], short["next_before_ns"]) == (False, None)
        exact = log_page([_rec(30), _rec(20), _rec(10)], limit=3)
        assert (exact["has_more"], exact["next_before_ns"]) == (False, None)

    def test_page_keeps_rows_tied_at_its_boundary(self):
        # Three rows share t=10; a page of 2 must carry all of them so the next
        # page ("strictly older than 10") loses none and repeats none.
        rows = [_rec(20), _rec(10, "a"), _rec(10, "b"), _rec(10, "c"), _rec(5)]
        page = log_page(rows, limit=2)
        assert [r["body"] for r in page["logs"]] == ["x", "a", "b", "c"]
        assert (page["has_more"], page["next_before_ns"]) == (True, 10)
        nxt = strictly_older(rows, LogFilter(before_ns=page["next_before_ns"]))
        assert [r["time_unix_nano"] for r in nxt] == [5]


class TestLiveStore:
    def test_before_ns_pages_without_repeats_or_gaps(self, tmp_path):
        store = LiveStore(db_path=str(tmp_path / "live.db"))
        for i in range(1, 8):
            store.add_log(
                {"time_unix_nano": i * NS, "body": f"line {i}", "level": "INFO", "logger": "t"}
            )
        first = log_page(store.query_logs(limit=3 + 50), 3)
        assert [r["time_unix_nano"] for r in first["logs"]] == [7 * NS, 6 * NS, 5 * NS]
        second = log_page(store.query_logs(limit=3 + 50, before_ns=first["next_before_ns"]), 3)
        assert [r["time_unix_nano"] for r in second["logs"]] == [4 * NS, 3 * NS, 2 * NS]
        third = log_page(store.query_logs(limit=3 + 50, before_ns=second["next_before_ns"]), 3)
        assert [r["time_unix_nano"] for r in third["logs"]] == [1 * NS] and third[
            "has_more"
        ] is False
        store.close()


@pytest.fixture()
def capture(monkeypatch):
    seen = {}

    def fake_get(url, headers=None, timeout=10.0):
        seen["url"] = url
        return {"data": {"result": []}, "spans": [], "hits": []}

    def fake_post(url, body, headers=None, timeout=10.0):
        seen["url"], seen["body"] = url, body
        return {"data": {"result": []}, "hits": []}

    monkeypatch.setattr(sz, "http_post_json", fake_post)
    monkeypatch.setattr(up, "http_get_json", fake_get)
    monkeypatch.setattr(_loki, "http_get_json", fake_get)
    import hermes_otel.dashboard.backends.openobserve as oo

    monkeypatch.setattr(oo, "http_post_json", fake_post)
    return seen


CURSOR = 1_790_433_574_196_187_904  # ns; ms part .196, so the ms bound must include it


class TestAdapterBounds:
    def test_signoz_ends_at_the_cursor_millisecond(self, capture):
        a = sz.SigNozAdapter(
            {"type": "signoz", "endpoint": "http://s:4318/v1/traces", "api_key": "k"}
        )
        a.logs_search(LogFilter(before_ns=CURSOR), 1_790_400_000, 1_790_500_000, 10)
        assert capture["body"]["end"] == CURSOR // 1_000_000 + 1
        a.logs_search(LogFilter(), 1_790_400_000, 1_790_500_000, 10)
        assert capture["body"]["end"] == 1_790_500_000 * 1000

    def test_uptrace_ends_at_the_next_whole_second_and_over_fetches(self, capture):
        # Uptrace floors time_lt to the second, so the bound is the next second
        # above the cursor and the fetch is padded to cover that second.
        a = up.UptraceAdapter(
            {"type": "uptrace", "endpoint": "http://u:14318/v1/traces", "user_token": "t"}
        )
        a.logs_search(LogFilter(before_ns=CURSOR), 1_790_400_000, 1_790_500_000, 10)
        q = _urlparse.parse_qs(_urlparse.urlparse(capture["url"]).query)
        assert q["time_lt"] == [str((CURSOR // 1_000_000_000 + 1) * 1000)]
        assert q["time_gte"] == ["1790400000000"] and q["limit"] == ["510"]
        a.logs_search(LogFilter(), 1_790_400_000, 1_790_500_000, 10)
        q = _urlparse.parse_qs(_urlparse.urlparse(capture["url"]).query)
        assert q["time_lt"] == ["1790500000000"] and q["limit"] == ["10"]

    def test_loki_uses_the_exact_nanosecond(self, capture):
        _loki.logs_search(
            "http://l:3100", LogFilter(before_ns=CURSOR), 1_790_400_000, 1_790_500_000, 10
        )
        q = _urlparse.parse_qs(_urlparse.urlparse(capture["url"]).query)
        assert q["end"] == [str(CURSOR)]

    def test_openobserve_adds_a_microsecond_predicate(self, capture):
        a = OpenObserveAdapter(
            {
                "type": "openobserve",
                "endpoint": "http://o:5080/api/default/v1/traces",
                "user": "u",
                "password": "p",
            }
        )
        a.logs_search(LogFilter(before_ns=CURSOR), 1_790_400_000, 1_790_500_000, 10)
        assert f"_timestamp < {CURSOR // 1000}" in capture["body"]["query"]["sql"]
