"""VAT and currency utilities for FlexCommerce."""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from ..conf import fc_setting

ZERO = Decimal("0.00")


def to_decimal(value, default=None) -> Decimal:
    """Convert ``value`` to ``Decimal`` safely (floats go through ``str``)."""
    if isinstance(value, Decimal):
        result = value
    else:
        try:
            result = Decimal(str(value).strip())
        except (InvalidOperation, ValueError, TypeError):
            if default is not None:
                return default
            raise ValueError(f"Invalid decimal value: {value!r}") from None
    if not result.is_finite():
        if default is not None:
            return default
        raise ValueError(f"Invalid decimal value: {value!r}")
    return result


def get_vat_rate() -> Decimal:
    return to_decimal(fc_setting("VAT_RATE", 0.075))


def is_vat_inclusive() -> bool:
    return bool(fc_setting("VAT_INCLUSIVE", False))


def round_price(amount, places: int = None) -> Decimal:
    if places is None:
        places = fc_setting("DECIMAL_PLACES", 2)
    quantum = Decimal(1).scaleb(-places) if places > 0 else Decimal("1")
    return to_decimal(amount).quantize(quantum, rounding=ROUND_HALF_UP)


def calculate_vat_exclusive(price, rate=None) -> dict:
    """Price is ex-VAT; VAT is added on top. Returns ``{net, vat, gross, rate}``."""
    rate = get_vat_rate() if rate is None else to_decimal(rate)
    net = round_price(price)
    vat = round_price(net * rate)
    return {"net": net, "vat": vat, "gross": round_price(net + vat), "rate": rate}


def calculate_vat_inclusive(price, rate=None) -> dict:
    """Price already includes VAT; extract it. Returns ``{net, vat, gross, rate}``."""
    rate = get_vat_rate() if rate is None else to_decimal(rate)
    gross = round_price(price)
    net = round_price(gross / (1 + rate))
    return {"net": net, "vat": round_price(gross - net), "gross": gross, "rate": rate}


def compute_tax(price, rate=None, inclusive: bool = None) -> dict:
    """Unified tax computation respecting global settings."""
    if inclusive is None:
        inclusive = is_vat_inclusive()
    if inclusive:
        return calculate_vat_inclusive(price, rate)
    return calculate_vat_exclusive(price, rate)


def format_currency(amount, symbol: str = None) -> str:
    if symbol is None:
        symbol = fc_setting("CURRENCY_SYMBOL", "₦")
    value = round_price(amount)
    sign = "-" if value < 0 else ""
    return f"{sign}{symbol}{abs(value):,.{max(-value.as_tuple().exponent, 0)}f}"
