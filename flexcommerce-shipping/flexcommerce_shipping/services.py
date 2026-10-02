"""
Shipping quotes. Used by the shipping API and by checkout (which must never
trust a client-supplied price or a method that does not serve the address).
"""

from dataclasses import dataclass
from decimal import Decimal

from flexcommerce_core.exceptions import ShippingMethodNotAvailableError
from flexcommerce_core.utils.vat import ZERO, round_price, to_decimal

from .models import PickupStation, ShippingMethod, ShippingZone


@dataclass
class ShippingQuote:
    method: ShippingMethod
    zone: ShippingZone
    net: Decimal
    vat: Decimal
    gross: Decimal
    pickup_station: PickupStation = None
    free_shipping_applied: bool = False

    def to_dict(self):
        earliest, latest = self.method.estimated_delivery()
        return {
            "method_id": str(self.method.pk),
            "name": self.method.name,
            "carrier": self.method.carrier,
            "is_pickup": self.method.is_pickup,
            "pay_on_delivery": self.method.pay_on_delivery,
            "zone": self.zone.zone_code,
            "cost": str(self.gross),
            "cost_net": str(self.net),
            "cost_vat": str(self.vat),
            "free_shipping_applied": self.free_shipping_applied,
            "estimated_delivery": {"from": earliest.isoformat(), "to": latest.isoformat()},
            "pickup_station": self.pickup_station.to_snapshot() if self.pickup_station else None,
        }


def _quote(method, zone, cart_total, item_count, weight, free_shipping=False, station=None):
    info = method.get_cost_with_vat(cart_total, item_count, weight)
    if station is not None and station.fee is not None:
        info = {"net": round_price(station.fee), "vat": ZERO, "gross": round_price(station.fee)}
    if free_shipping:
        return ShippingQuote(method, zone, ZERO, ZERO, ZERO, station, free_shipping_applied=True)
    return ShippingQuote(method, zone, info["net"], info["vat"], info["gross"], station)


def available_methods(zone, weight=ZERO):
    return [
        m
        for m in ShippingMethod.objects.filter(is_active=True, zones=zone).order_by("sort_order", "base_rate")
        if m.supports_weight(weight)
    ]


def quote_all(state="", country="", cart_total=ZERO, item_count=0, weight=ZERO, free_shipping=False):
    """All options for an address, cheapest first within sort order."""
    zone = ShippingZone.get_zone_for_address(state=state, country=country)
    if zone is None:
        return None, []
    weight = to_decimal(weight)
    return zone, [
        _quote(m, zone, to_decimal(cart_total), item_count, weight, free_shipping)
        for m in available_methods(zone, weight)
    ]


def quote_method(
    method_id,
    state="",
    country="",
    cart_total=ZERO,
    item_count=0,
    weight=ZERO,
    free_shipping=False,
    pickup_station_id=None,
) -> ShippingQuote:
    """Authoritative quote for the method chosen at checkout. Raises if not allowed."""
    zone = ShippingZone.get_zone_for_address(state=state, country=country)
    if zone is None:
        raise ShippingMethodNotAvailableError("We do not deliver to this location yet.", extra={"state": state})
    weight = to_decimal(weight)
    method = ShippingMethod.objects.filter(pk=method_id, is_active=True, zones=zone).first()
    if method is None or not method.supports_weight(weight):
        raise ShippingMethodNotAvailableError(extra={"method_id": str(method_id), "zone": zone.zone_code})
    station = None
    if method.is_pickup:
        if not pickup_station_id:
            raise ShippingMethodNotAvailableError("Choose a pickup station.", code="pickup_station_required")
        station = PickupStation.objects.filter(pk=pickup_station_id, is_active=True).first()
        if station is None:
            raise ShippingMethodNotAvailableError("Pickup station not found.", code="pickup_station_not_found")
    return _quote(method, zone, to_decimal(cart_total), item_count, weight, free_shipping, station)


def nigerian_states():
    return [{"name": state, "zone": zone} for zone, states in ShippingZone.NIGERIAN_STATES.items() for state in states]
