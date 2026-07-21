"""Numeric coercion and change derivation."""

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from yolab_quote.models import Bar, ProviderHealth, Quote, to_float


class FakeNumpyScalar:
    """Stand-in for ``numpy.float64``: convertible, but not a real float.

    One of the bots passed values like this straight into its message
    formatting, so the package guarantees they never escape.
    """

    def __init__(self, value: float) -> None:
        self._value = value

    def __float__(self) -> float:
        return self._value


class TestToFloat:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (1, 1.0),
            (1.5, 1.5),
            ("2.25", 2.25),
            (Decimal("3.5"), 3.5),
            (FakeNumpyScalar(4.5), 4.5),
        ],
    )
    def test_converts(self, raw, expected):
        result = to_float(raw)
        assert result == expected
        assert type(result) is float

    @pytest.mark.parametrize("raw", [None, "", "abc", object(), float("nan"), float("inf"), float("-inf")])
    def test_rejects(self, raw):
        assert to_float(raw) is None

    @pytest.mark.parametrize("raw", [True, False])
    def test_bool_is_not_a_number_here(self, raw):
        """``float(True) == 1.0`` would silently turn a flag into a price."""
        assert to_float(raw) is None


class TestChangeDerivation:
    def test_derives_change_and_percent(self):
        quote = Quote.create(
            symbol="2330.TW", market="tw_stock", source="t", price=110.0, previous_close=100.0
        )
        assert quote.change == pytest.approx(10.0)
        assert quote.change_percent == pytest.approx(10.0)

    def test_provider_supplied_values_win(self):
        quote = Quote.create(
            symbol="X", market="us_stock", source="t",
            price=110.0, previous_close=100.0, change=1.0, change_percent=2.0,
        )
        assert quote.change == 1.0
        assert quote.change_percent == 2.0

    def test_zero_previous_close_does_not_divide(self):
        """Regression: one bot divided without a guard and could raise."""
        quote = Quote.create(
            symbol="X", market="us_stock", source="t", price=5.0, previous_close=0.0
        )
        assert quote.change == pytest.approx(5.0)
        assert quote.change_percent is None

    def test_missing_previous_close_leaves_change_unknown(self):
        quote = Quote.create(symbol="X", market="us_stock", source="t", price=5.0)
        assert quote.change is None
        assert quote.change_percent is None

    def test_negative_move(self):
        quote = Quote.create(
            symbol="X", market="us_stock", source="t", price=90.0, previous_close=100.0
        )
        assert quote.change == pytest.approx(-10.0)
        assert quote.change_percent == pytest.approx(-10.0)
        assert quote.is_up is False


class TestQuote:
    def test_numeric_fields_are_real_floats(self):
        quote = Quote.create(
            symbol="2330.TW", market="tw_stock", source="t",
            price=FakeNumpyScalar(100.0), volume=FakeNumpyScalar(1e6),
            open=FakeNumpyScalar(99.0), high="101", low=Decimal("98.5"),
        )
        for value in (quote.price, quote.volume, quote.open, quote.high, quote.low):
            assert type(value) is float

    def test_defaults(self):
        quote = Quote.create(symbol="X", market="us_stock", source="t")
        assert quote.is_delayed is True
        assert quote.extra == {}
        assert quote.raw == {}
        assert quote.is_up is None
        assert quote.updated_at.tzinfo is not None

    def test_extra_and_raw_are_copied_not_shared(self):
        extra = {"pe_ratio": 20.0}
        quote = Quote.create(symbol="X", market="us_stock", source="t", extra=extra)
        extra["pe_ratio"] = 999.0
        assert quote.extra["pe_ratio"] == 20.0

    def test_is_frozen(self):
        quote = Quote.create(symbol="X", market="us_stock", source="t")
        with pytest.raises(FrozenInstanceError):
            quote.price = 1.0  # type: ignore[misc]

    def test_to_dict_flattens_extra_and_drops_raw(self):
        quote = Quote.create(
            symbol="2330.TW", market="tw_stock", source="yfinance",
            price=100.0, extra={"pe_ratio": 15.5}, raw={"huge": "payload"},
            updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        data = quote.to_dict()
        assert data["pe_ratio"] == 15.5
        assert "raw" not in data
        assert data["updated_at"] == "2026-01-01T00:00:00+00:00"


class TestBar:
    def test_short_key_dict(self):
        bar = Bar(date="2026-01-02", open=1.0, high=2.0, low=0.5, close=1.5, volume=100.0)
        assert bar.to_dict() == {
            "date": "2026-01-02", "o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5, "v": 100.0
        }


class TestProviderHealth:
    def test_to_dict(self):
        health = ProviderHealth(
            provider="yfinance", ok=False, status="unavailable",
            detail="not installed", markets=("tw_stock",),
        )
        assert health.to_dict() == {
            "provider": "yfinance", "ok": False, "status": "unavailable",
            "detail": "not installed", "markets": ["tw_stock"],
        }
