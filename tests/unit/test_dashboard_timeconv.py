"""The timestamp conversions the adapters share (#403, #519): the formatter
every query API's window is written with, and its exact inverse."""

from __future__ import annotations

from hermes_otel.dashboard.backends.timeconv import (
    iso_to_ns,
    ns_from_any,
    ns_from_s,
    ns_to_rfc3339,
    rfc3339_to_ns,
)

NS = 1_000_000_000


class TestRfc3339:
    def test_round_trip_keeps_nine_digits(self):
        ns = 1_791_750_470_202_258_944
        assert ns_to_rfc3339(ns) == "2026-10-11T20:27:50.202258944Z"
        assert rfc3339_to_ns("2026-10-11T20:27:50.202258944Z") == ns
        assert rfc3339_to_ns("2026-10-11T20:27:50Z") == 1_791_750_470 * NS
        assert rfc3339_to_ns("2026-10-11T22:27:50+02:00") == 1_791_750_470 * NS
        assert rfc3339_to_ns("garbage") == 0 and rfc3339_to_ns(None) == 0

    def test_digits_select_the_backend_s_precision(self):
        ns = 1_791_754_699_123_456_789
        assert ns_to_rfc3339(ns, digits=6) == "2026-10-11T21:38:19.123456Z"  # Agenta (µs)
        assert ns_to_rfc3339(ns, digits=0) == "2026-10-11T21:38:19Z"  # Langfuse (s)
        assert ns_to_rfc3339(ns_from_s(1_791_754_699)) == "2026-10-11T21:38:19.000000000Z"

    def test_seconds_to_ns(self):
        assert ns_from_s(7) == 7 * NS and ns_from_s("7") == 7 * NS


class TestParsers:
    def test_ns_from_any_scales_by_magnitude(self):
        assert ns_from_any(1_791_754_699) == 1_791_754_699 * NS
        assert ns_from_any(1_791_754_699_123) == 1_791_754_699_123 * 1_000_000
        assert ns_from_any(1_791_754_699_123_456) == 1_791_754_699_123_456 * 1_000
        assert ns_from_any("1791754699") == 1_791_754_699 * NS
        assert ns_from_any("2026-10-11T21:38:19Z") == 1_791_754_699 * NS
        assert ns_from_any(None) is None and ns_from_any("") is None
        assert ns_from_any(True) is None and ns_from_any("nope") is None

    def test_iso_to_ns(self):
        assert iso_to_ns("2026-10-11T21:38:19Z") == 1_791_754_699 * NS
        assert iso_to_ns("") is None and iso_to_ns("nope") is None
