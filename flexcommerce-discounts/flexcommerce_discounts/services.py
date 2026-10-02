"""
Discount engine.

Everything works on ``flexcommerce_core.utils.pricing.PricedLine`` objects so the
same rules apply to the cart preview and to the authoritative checkout.

    result = evaluate(coupon, lines, subtotal, user=user, email=email)
    result.amount, result.free_shipping, result.allocations

``redeem`` (inside the checkout transaction) enforces global and per-customer
limits atomically; ``restore`` gives the usage back when an order is cancelled.
"""

from dataclasses import dataclass, field
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import F
from django.db.models.functions import Greatest
from django.utils import timezone

from flexcommerce_core import events
from flexcommerce_core.exceptions import (
    CouponAlreadyAppliedError,
    CouponExpiredError,
    CouponMinimumValueError,
    CouponNotApplicableError,
    CouponNotFoundError,
    CouponUsageLimitError,
    DiscountError,
)
from flexcommerce_core.utils.products import is_installed, prefetch_products
from flexcommerce_core.utils.vat import ZERO, round_price, to_decimal

from .conf import discounts_setting
from .models import Coupon, CouponRedemption, CouponUsage, FlashSale, FlashSaleItem

FLASH_CACHE_KEY = "flexcommerce:discounts:flash_map:v2"
AUTO_CACHE_KEY = "flexcommerce:discounts:auto_coupons:v2"


@dataclass
class DiscountResult:
    coupon: Coupon
    amount: Decimal = ZERO
    free_shipping: bool = False
    allocations: dict = field(default_factory=dict)  # line.key -> discount share

    @property
    def code(self):
        return self.coupon.code

    def to_dict(self):
        return {
            "coupon_code": self.coupon.code,
            "coupon_type": self.coupon.coupon_type,
            "discount_amount": str(self.amount),
            "free_shipping": self.free_shipping,
        }


# ── Lookup & validation ─────────────────────────────────────────────────────


def get_coupon(code: str) -> Coupon:
    code = (code or "").strip().upper()
    coupon = Coupon.objects.filter(code=code).first() if code else None
    if coupon is None:
        raise CouponNotFoundError()
    return coupon


def customer_key(user=None, email=""):
    if user is not None and getattr(user, "is_authenticated", False):
        return f"user:{user.pk}"
    if email:
        return f"email:{email.strip().lower()}"
    return ""


def has_previous_orders(user=None, email=""):
    if not is_installed("flexcommerce_orders"):
        return False
    from flexcommerce_orders.models import Order

    qs = Order.objects.exclude(status=Order.STATUS_CANCELLED)
    if user is not None and getattr(user, "is_authenticated", False):
        return qs.filter(user=user).exists()
    if email:
        return qs.filter(email__iexact=email).exists()
    return False


def check_customer(coupon, user=None, email=""):
    if coupon.first_order_only and has_previous_orders(user, email):
        raise CouponNotApplicableError("This coupon is only valid on your first order.")
    key = customer_key(user, email)
    if coupon.per_user_limit and key:
        used = CouponRedemption.objects.filter(coupon=coupon, customer_key=key).values_list("count", flat=True).first()
        if used is not None and used >= coupon.per_user_limit:
            raise CouponUsageLimitError("You have already used this coupon the maximum number of times.")


def check_coupon(coupon, user=None, email=""):
    now = timezone.now()
    if not coupon.is_active or (coupon.expires_at and now > coupon.expires_at):
        raise CouponExpiredError()
    if coupon.starts_at and now < coupon.starts_at:
        raise CouponExpiredError("This coupon is not active yet.")
    if coupon.usage_limit is not None and coupon.used_count >= coupon.usage_limit:
        raise CouponUsageLimitError()
    check_customer(coupon, user, email)


# ── Evaluation ───────────────────────────────────────────────────────────────


def _as_set(values):
    return {str(v) for v in (values or [])}


def is_line_eligible(coupon, line) -> bool:
    if coupon.vendor_id and line.vendor_id != str(coupon.vendor_id):
        return False
    if line.ids & _as_set(coupon.excluded_products):
        return False
    only_products = _as_set(coupon.restricted_products)
    only_categories = _as_set(coupon.restricted_categories)
    if not only_products and not only_categories:
        return True
    return bool(line.ids & only_products) or bool(set(line.category_ids) & only_categories)


def _allocate(amount, lines):
    """Split ``amount`` over lines proportionally; remainder goes to the last line."""
    total = sum((line.line_gross for line in lines), ZERO)
    allocations, running = {}, ZERO
    for index, line in enumerate(lines):
        if index == len(lines) - 1:
            share = amount - running
        else:
            share = round_price(amount * line.line_gross / total) if total else ZERO
        allocations[line.key] = share
        running += share
    return allocations


def _buy_x_get_y(coupon, lines):
    group = coupon.buy_quantity + coupon.get_quantity
    units = sum(line.quantity for line in lines)
    free_units = (units // group) * coupon.get_quantity if group else 0
    amount, allocations = ZERO, {}
    for line in sorted(lines, key=lambda line: line.unit_gross):
        if free_units <= 0:
            break
        take = min(free_units, line.quantity)
        share = round_price(line.unit_gross * take)
        allocations[line.key] = share
        amount += share
        free_units -= take
    return amount, allocations


def evaluate(coupon, lines, subtotal=None, user=None, email="", check=True) -> DiscountResult:
    """Compute the discount ``coupon`` gives on ``lines``. Raises on invalid use."""
    if check:
        check_coupon(coupon, user, email)
    lines = list(lines)
    subtotal = sum((line.line_gross for line in lines), ZERO) if subtotal is None else to_decimal(subtotal)
    if subtotal < coupon.minimum_cart_value:
        raise CouponMinimumValueError(
            extra={
                "minimum": str(round_price(coupon.minimum_cart_value)),
                "current": str(round_price(subtotal)),
            }
        )
    eligible = [line for line in lines if is_line_eligible(coupon, line)]
    if not eligible:
        raise CouponNotApplicableError()
    eligible_total = sum((line.line_gross for line in eligible), ZERO)
    result = DiscountResult(coupon=coupon)
    if coupon.coupon_type == Coupon.TYPE_FREE_SHIPPING:
        result.free_shipping = True
        return result
    if coupon.coupon_type == Coupon.TYPE_BUY_X_GET_Y:
        result.amount, result.allocations = _buy_x_get_y(coupon, eligible)
        if result.amount <= 0:
            raise CouponNotApplicableError(
                f"Add {coupon.buy_quantity + coupon.get_quantity} eligible items to use this offer."
            )
        return result
    result.amount = coupon.calculate_discount(eligible_total)
    result.allocations = _allocate(result.amount, eligible)
    return result


def best_automatic(lines, subtotal, user=None, email=""):
    """The best automatic (code-less) promotion for ``lines``, or ``None``."""
    coupons = cache.get(AUTO_CACHE_KEY)
    if coupons is None:
        coupons = list(Coupon.objects.filter(auto_apply=True, is_active=True))
        cache.set(AUTO_CACHE_KEY, coupons, discounts_setting("DISCOUNT_CACHE_SECONDS"))
    best = None
    for coupon in coupons:
        try:
            result = evaluate(coupon, lines, subtotal, user=user, email=email)
        except DiscountError:
            continue
        if best is None or (result.amount, result.free_shipping) > (
            best.amount,
            best.free_shipping,
        ):
            best = result
    return best


# ── Cart integration (simple API) ──────────────────────────────────────────


class CouponService:
    @classmethod
    def apply_to_cart(cls, cart, code: str) -> dict:
        coupon = get_coupon(code)
        if cart.coupon_code and cart.coupon_code.upper() == coupon.code:
            raise CouponAlreadyAppliedError()
        from flexcommerce_cart.services import CartService

        return CartService(cart).apply_coupon(coupon.code)

    @classmethod
    def record_usage(cls, coupon_code, cart, order_ref=""):
        """Record a usage for ``cart`` (prefer ``redeem``)."""
        return redeem(
            coupon_code,
            order_ref=order_ref,
            amount=cart.discount_amount,
            user=cart.user,
            session_key=cart.session_key,
        )

    @classmethod
    def get_active_flash_sale(cls, product):
        now = timezone.now()
        for sale in FlashSale.objects.filter(is_active=True, starts_at__lte=now, ends_at__gte=now):
            if not sale.applicable_products or str(product.pk) in _as_set(sale.applicable_products):
                return sale
        return None


# ── Redemption (checkout) ────────────────────────────────────────────────────


def _bump_used_count(coupon_id):
    Coupon.objects.filter(pk=coupon_id).update(used_count=F("used_count") + 1)


@transaction.atomic
def redeem(code, order_ref="", amount=ZERO, user=None, email="", session_key=""):
    """
    Consume one use of a coupon. Call inside the checkout transaction so a failed
    checkout rolls the redemption back. Global limits use a conditional UPDATE;
    per-customer limits use a per-customer counter row (no global lock).
    """
    coupon = get_coupon(code)
    check_coupon(coupon, user, email)
    if coupon.usage_limit is not None:
        claimed = Coupon.objects.filter(pk=coupon.pk, used_count__lt=F("usage_limit")).update(
            used_count=F("used_count") + 1
        )
        if not claimed:
            raise CouponUsageLimitError()
    else:
        # No global cap: avoid a hot-row update inside the checkout transaction.
        events.on_commit(lambda: _bump_used_count(coupon.pk))
    key = customer_key(user, email)
    if key:
        try:
            with transaction.atomic():
                CouponRedemption.objects.get_or_create(coupon=coupon, customer_key=key)
        except IntegrityError:  # concurrent first redemption by the same customer
            pass
        qs = CouponRedemption.objects.filter(coupon=coupon, customer_key=key)
        if coupon.per_user_limit:
            qs = qs.filter(count__lt=coupon.per_user_limit)
        if not qs.update(count=F("count") + 1):
            raise CouponUsageLimitError("You have already used this coupon the maximum number of times.")
    return CouponUsage.objects.create(
        coupon=coupon,
        user=user if getattr(user, "is_authenticated", False) else None,
        email=(email or getattr(user, "email", "") or "").lower(),
        session_key=session_key or "",
        order_ref=order_ref,
        discount_applied=to_decimal(amount),
    )


@transaction.atomic
def restore(order_ref):
    """Give back coupon uses of a cancelled order. Idempotent."""
    restored = 0
    for usage in CouponUsage.objects.filter(order_ref=order_ref).select_related("coupon"):
        Coupon.objects.filter(pk=usage.coupon_id).update(used_count=Greatest(F("used_count") - 1, 0))
        key = customer_key(usage.user, usage.email)
        if key:
            CouponRedemption.objects.filter(coupon_id=usage.coupon_id, customer_key=key).update(
                count=Greatest(F("count") - 1, 0)
            )
        usage.delete()
        restored += 1
    return restored


# ── Flash sales ──────────────────────────────────────────────────────────────


def invalidate_caches(*args, **kwargs):
    cache.delete_many([FLASH_CACHE_KEY, AUTO_CACHE_KEY])


def _flash_map():
    data = cache.get(FLASH_CACHE_KEY)
    if data is not None:
        return data
    now = timezone.now()
    data = {"items": {}, "legacy": []}
    sales = FlashSale.objects.filter(is_active=True, ends_at__gte=now).prefetch_related("items")
    for sale in sales:
        items = list(sale.items.all())
        window = (sale.starts_at, sale.ends_at)
        if items:
            for item in items:
                if item.remaining == 0:
                    continue
                data["items"][(item.content_type_id, str(item.object_id))] = {
                    "item_id": str(item.pk),
                    "sale_price": item.sale_price,
                    "percentage": sale.discount_percentage,
                    "window": window,
                    "limited": item.quantity_limit is not None,
                }
        else:
            data["legacy"].append(
                {
                    "percentage": sale.discount_percentage,
                    "products": _as_set(sale.applicable_products),
                    "window": window,
                }
            )
    cache.set(FLASH_CACHE_KEY, data, discounts_setting("DISCOUNT_CACHE_SECONDS"))
    return data


def _in_window(entry):
    start, end = entry["window"]
    return start <= timezone.now() <= end


def flash_entry(product):
    data = _flash_map()
    ct_id = ContentType.objects.get_for_model(product).pk
    candidates = [str(product.pk)]
    parent = getattr(product, "product_id", None)
    if parent:
        candidates.append(str(parent))
    for pid in candidates:
        entry = data["items"].get((ct_id, pid))
        if entry and _in_window(entry):
            return entry
    for entry in data["legacy"]:
        if _in_window(entry) and (not entry["products"] or set(candidates) & entry["products"]):
            return entry
    return None


def flash_sale_price(product, price, **kwargs):
    """``price.modify`` hook: apply the running flash sale, if any."""
    entry = flash_entry(product)
    if entry is None:
        return price
    if entry.get("sale_price") is not None:
        return min(price, entry["sale_price"])
    return price - price * to_decimal(entry["percentage"]) / Decimal("100")


def claim_flash_quantities(order, **kwargs):
    """``order.created`` hook: consume limited flash-sale units atomically."""
    claims = []
    items = [item for item in order.items.all() if item.content_type_id is not None]
    products = prefetch_products(items)
    for item in items:
        product = products.get((item.content_type_id, item.object_id))
        entry = flash_entry(product) if product is not None else None
        if not entry or not entry.get("limited"):
            continue
        claimed = FlashSaleItem.objects.filter(
            pk=entry["item_id"], sold_quantity__lte=F("quantity_limit") - item.quantity
        ).update(sold_quantity=F("sold_quantity") + item.quantity)
        if not claimed:
            invalidate_caches()
            raise DiscountError(
                f"The flash sale price for '{item.product_name}' is sold out. Please review your cart.",
                code="flash_sale_sold_out",
            )
        claims.append({"item_id": entry["item_id"], "quantity": item.quantity})
    if claims:
        invalidate_caches()
        type(order).objects.filter(pk=order.pk).update(extra_data={**order.extra_data, "flash_claims": claims})
        order.extra_data = {**order.extra_data, "flash_claims": claims}
    return claims


@transaction.atomic
def release_flash_quantities(order, **kwargs):
    """``order.cancelled`` hook: put flash units back and restore coupon uses. Idempotent."""
    locked = type(order).objects.select_for_update().only("extra_data").get(pk=order.pk)
    claims = (locked.extra_data or {}).get("flash_claims", [])
    for claim in claims:
        FlashSaleItem.objects.filter(pk=claim["item_id"]).update(
            sold_quantity=Greatest(F("sold_quantity") - claim["quantity"], 0)
        )
    if claims:
        extra = {k: v for k, v in locked.extra_data.items() if k != "flash_claims"}
        type(order).objects.filter(pk=order.pk).update(extra_data=extra)
        order.extra_data = extra
        invalidate_caches()
    restore(order.order_number)


def active_flash_sales():
    now = timezone.now()
    return FlashSale.objects.filter(is_active=True, starts_at__lte=now, ends_at__gte=now).prefetch_related("items")
