"""Scalar coercion (coerce.py): the four never raise and differ only in what
"nothing" becomes (#522)."""

import pytest

from hermes_otel.coerce import coerce_bool, optional_number, to_int, to_optional_int


class TestToInt:
    def test_int_input(self):
        assert to_int(42) == 42

    def test_float_input(self):
        assert to_int(3.7) == 3

    def test_string_integer(self):
        assert to_int("42") == 42

    def test_string_float(self):
        assert to_int("3.7") == 3

    def test_empty_string(self):
        assert to_int("") == 0

    def test_whitespace_string(self):
        assert to_int("  ") == 0

    def test_none_returns_zero(self):
        assert to_int(None) == 0

    def test_bool_true_returns_zero(self):
        assert to_int(True) == 0

    def test_bool_false_returns_zero(self):
        assert to_int(False) == 0

    def test_non_numeric_string(self):
        assert to_int("abc") == 0

    def test_negative_int(self):
        assert to_int(-5) == -5

    def test_zero(self):
        assert to_int(0) == 0

    def test_list_returns_zero(self):
        assert to_int([1, 2]) == 0


class TestToOptionalInt:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (429, 429),
            (500.0, 500),
            ("503", 503),
            ("  408 ", 408),
            (None, None),
            ("", None),
            ("   ", None),
            ("nope", None),
            (True, None),  # bools are not status codes
            (False, None),
        ],
    )
    def test_parsing(self, value, expected):
        assert to_optional_int(value) == expected


class TestOptionalNumber:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (3, 3.0),
            (2.5, 2.5),
            ("7", 7.0),
            (" 12.5 ", 12.5),
            (None, None),
            ("", None),
            ("abc", None),
            (True, None),  # bools are not measurements
            (False, None),
            ([1], None),
        ],
    )
    def test_parsing(self, value, expected):
        assert optional_number(value) == expected


class TestCoerceBool:
    @pytest.mark.parametrize("value", [True, 1, "true", "True", "1", "yes", "on"])
    def test_truthy(self, value):
        assert coerce_bool(value) is True

    @pytest.mark.parametrize("value", [False, 0, "false", "0", "no", "off"])
    def test_falsey(self, value):
        assert coerce_bool(value) is False

    @pytest.mark.parametrize("value", [None, "maybe", "x"])
    def test_undecidable_is_none(self, value):
        assert coerce_bool(value) is None


class TestUnsupportedTypes:
    """Containers are not numbers or flags; nothing guesses."""

    def test_to_optional_int(self):
        assert to_optional_int([1]) is None

    def test_coerce_bool(self):
        assert coerce_bool([True]) is None
