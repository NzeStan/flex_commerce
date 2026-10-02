from decimal import Decimal

import pytest

from flexcommerce_core import conf
from flexcommerce_core.conf import (
    fc_setting,
    get_flexcommerce_settings,
    register_defaults,
    throttle_rate,
)
from flexcommerce_core.utils.vat import (
    calculate_vat_exclusive,
    calculate_vat_inclusive,
    compute_tax,
    format_currency,
    get_vat_rate,
    is_vat_inclusive,
    round_price,
    to_decimal,
)
from flexcommerce_core.validators import normalize_phone, validate_phone


class TestSettings:
    def test_defaults_are_nigerian(self, fc):
        fc()
        assert fc_setting("CURRENCY") == "NGN"
        assert fc_setting("CURRENCY_SYMBOL") == "₦"
        assert fc_setting("VAT_RATE") == 0.075

    def test_user_override_wins(self, fc):
        fc(CURRENCY="GHS", VAT_RATE=0.15)
        assert fc_setting("CURRENCY") == "GHS"
        assert get_vat_rate() == Decimal("0.15")

    def test_unknown_key_returns_default(self):
        assert fc_setting("NOPE", "fallback") == "fallback"

    def test_known_key_ignores_call_default(self, fc):
        fc()
        assert fc_setting("CURRENCY", "USD") == "NGN"

    def test_register_defaults(self, fc):
        register_defaults({"MY_PKG_OPTION": 42})
        assert fc_setting("MY_PKG_OPTION") == 42
        fc(MY_PKG_OPTION=7)
        assert fc_setting("MY_PKG_OPTION") == 7

    def test_cache_reset_on_setting_change(self, settings):
        settings.FLEXCOMMERCE = {"CURRENCY": "KES"}
        assert get_flexcommerce_settings()["CURRENCY"] == "KES"
        settings.FLEXCOMMERCE = {"CURRENCY": "ZAR"}
        assert fc_setting("CURRENCY") == "ZAR"

    def test_missing_flexcommerce_setting(self, settings):
        del settings.FLEXCOMMERCE
        conf._cache = None
        assert fc_setting("CURRENCY") == "NGN"

    def test_throttle_rate_override(self, fc):
        fc(THROTTLE_RATES={"checkout": "1/minute"})
        assert throttle_rate("checkout") == "1/minute"
        assert throttle_rate("coupon") == "30/minute"
        assert throttle_rate("unknown") is None


class TestMoney:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (1, Decimal("1")),
            ("2.50", Decimal("2.50")),
            (0.1, Decimal("0.1")),
            (Decimal("3"), Decimal("3")),
        ],
    )
    def test_to_decimal(self, value, expected):
        assert to_decimal(value) == expected

    @pytest.mark.parametrize("bad", ["abc", None, "", "NaN", "Infinity", object()])
    def test_to_decimal_rejects_garbage(self, bad):
        with pytest.raises(ValueError):
            to_decimal(bad)

    def test_to_decimal_default(self):
        assert to_decimal("x", default=Decimal("0")) == Decimal("0")
        assert to_decimal("NaN", default=Decimal("1")) == Decimal("1")

    def test_round_price_half_up(self):
        assert round_price(Decimal("1.005")) == Decimal("1.01")
        assert round_price(Decimal("1.004")) == Decimal("1.00")
        assert round_price(Decimal("10.5"), places=0) == Decimal("11")

    def test_round_price_respects_decimal_places_setting(self, fc):
        fc(DECIMAL_PLACES=0)
        assert round_price(Decimal("99.5")) == Decimal("100")

    def test_vat_exclusive(self):
        r = calculate_vat_exclusive(Decimal("10000"), Decimal("0.075"))
        assert r == {
            "net": Decimal("10000.00"),
            "vat": Decimal("750.00"),
            "gross": Decimal("10750.00"),
            "rate": Decimal("0.075"),
        }

    def test_vat_inclusive(self):
        r = calculate_vat_inclusive(Decimal("10750"), Decimal("0.075"))
        assert r["net"] == Decimal("10000.00")
        assert r["vat"] == Decimal("750.00")
        assert r["gross"] == Decimal("10750.00")

    def test_inclusive_net_plus_vat_equals_gross_always(self):
        for cents in range(1, 5000, 37):
            price = Decimal(cents) / 100
            r = calculate_vat_inclusive(price, Decimal("0.075"))
            assert r["net"] + r["vat"] == r["gross"]

    def test_compute_tax_uses_setting(self, fc):
        fc(VAT_INCLUSIVE=True)
        assert is_vat_inclusive() is True
        assert compute_tax(Decimal("107.50"))["net"] == Decimal("100.00")
        fc(VAT_INCLUSIVE=False)
        assert compute_tax(Decimal("100"))["gross"] == Decimal("107.50")

    def test_compute_tax_explicit_rate_and_mode(self):
        assert compute_tax(Decimal("100"), rate=Decimal("0"), inclusive=False)["vat"] == Decimal("0.00")
        assert compute_tax("200", rate="0.1", inclusive=True)["net"] == Decimal("181.82")

    def test_format_currency(self, fc):
        fc()
        assert format_currency(Decimal("1234567.5")) == "₦1,234,567.50"
        assert format_currency(Decimal("-10")) == "-₦10.00"
        assert format_currency(5, symbol="$") == "$5.00"


class TestPhone:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("08012345678", "2348012345678"),
            ("+2348012345678", "2348012345678"),
            ("0801 234 5678", "2348012345678"),
            ("002348012345678", "2348012345678"),
            ("+14155552671", "14155552671"),
        ],
    )
    def test_normalize(self, raw, expected):
        assert normalize_phone(raw) == expected

    @pytest.mark.parametrize("ok", ["08012345678", "+2348012345678", "+14155552671", ""])
    def test_valid(self, ok):
        validate_phone(ok)

    @pytest.mark.parametrize("bad", ["123", "phone", "0801234567a", "+0123456789"])
    def test_invalid(self, bad):
        from django.core.exceptions import ValidationError

        with pytest.raises(ValidationError):
            validate_phone(bad)
