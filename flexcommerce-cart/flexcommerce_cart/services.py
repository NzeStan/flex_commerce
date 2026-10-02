"""
Cart services. All business logic lives here, not in views.

* ``CartPricingService`` – re-prices every line from the live price pipeline,
  re-validates the coupon (or picks the best automatic promotion) and stores
  the totals on the cart.
* ``CartService``        – item/coupon/save-for-later operations. Every mutation
  locks the cart row so double-clicks and parallel tabs cannot corrupt it.
* ``CartSessionManager`` – finds the right cart for a request (user, token
  header or session) and merges anonymous carts at login.
"""

import logging

from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, transaction
from django.utils import timezone

from flexcommerce_core.conf import fc_setting
from flexcommerce_core.events import emit
from flexcommerce_core.exceptions import (
    CartError,
    CartItemNotFoundError,
    CartLimitError,
    CartLockedError,
    DiscountError,
    InsufficientStockError,
    ProductUnavailableError,
)
from flexcommerce_core.utils.pricing import allocate_discount, build_line
from flexcommerce_core.utils.products import (
    get_product,
    is_installed,
    is_purchasable,
    prefetch_products,
    product_name,
)
from flexcommerce_core.utils.vat import ZERO, round_price

from . import signals as cart_signals
from .conf import cart_setting
from .models import Cart, CartItem, SavedItem

logger = logging.getLogger("flexcommerce.cart")


PRICE_FIELDS = [
    "unit_price",
    "unit_price_with_tax",
    "unit_net",
    "vat_rate",
    "vat_amount",
    "discount_amount",
]


def cart_payload(cart):
    email = cart.email or (cart.user.email if cart.user_id else "")
    return {
        "cart_id": str(cart.pk),
        "user_id": str(cart.user_id) if cart.user_id else None,
        "email": email,
        "items_count": cart.items_count,
        "total": str(cart.total_amount),
        "currency": cart.currency,
    }


# ── Pricing ──────────────────────────────────────────────────────────────────


class CartPricingService:
    """Recalculates prices, discount and totals after any mutation."""

    def __init__(self, cart: Cart):
        self.cart = cart

    def priced_lines(self, items=None):
        """``[PricedLine]`` for the cart's items (drops items no longer purchasable)."""
        items = list(items if items is not None else self.cart.items.all())
        products = prefetch_products(items)
        user = self.cart.user
        lines, removed = [], []
        for item in items:
            product = products.get((item.content_type_id, item.object_id))
            if product is None or not is_purchasable(product):
                removed.append((item, product))
                continue
            lines.append(build_line(product, item.quantity, key=item.pk, user=user, cart=self.cart))
        return lines, removed

    def _discount(self, lines, subtotal):
        cart = self.cart
        cart.coupon_error = ""
        if not is_installed("flexcommerce_discounts"):
            if cart.coupon_code:
                cart.coupon_code = ""
                cart.coupon_error = "Discounts are not enabled."
            return None
        if not lines:
            cart.coupon_code = ""
            return None
        from flexcommerce_discounts import services as discount_services
        from flexcommerce_discounts.conf import discounts_setting

        user = cart.user
        email = cart.email or getattr(user, "email", "")
        if cart.coupon_code:
            try:
                coupon = discount_services.get_coupon(cart.coupon_code)
                return discount_services.evaluate(coupon, lines, subtotal, user=user, email=email)
            except DiscountError as exc:
                cart.coupon_error = exc.message[:255]
                cart.coupon_code = ""
                return None
        if discounts_setting("AUTO_APPLY_PROMOTIONS"):
            return discount_services.best_automatic(lines, subtotal, user=user, email=email)
        return None

    @transaction.atomic
    def recalculate(self):
        cart = self.cart
        items = list(cart.items.select_related("content_type"))
        lines, removed = self.priced_lines(items)
        notices = []
        for item, product in removed:
            notices.append(f"{product_name(product) if product else 'An item'} is no longer available and was removed.")
            item.delete()
        subtotal = round_price(sum((line.line_gross for line in lines), ZERO))
        result = self._discount(lines, subtotal)
        discount = result.amount if result else ZERO
        allocations = result.allocations if result else {}

        by_pk = {item.pk: item for item in items}
        changed, tax_total = [], ZERO
        for line in lines:
            item = by_pk[line.key]
            line_discount = allocations.get(line.key, ZERO)
            tax_total += allocate_discount(line.line_gross, line.line_vat, line_discount)["vat"]
            new = {
                "unit_price": line.unit_price,
                "unit_price_with_tax": line.unit_gross,
                "unit_net": line.unit_net,
                "vat_rate": line.rate,
                "vat_amount": line.unit_vat,
                "discount_amount": line_discount,
            }
            if any(getattr(item, k) != v for k, v in new.items()):
                for k, v in new.items():
                    setattr(item, k, v)
                changed.append(item)
        if changed:
            CartItem.objects.bulk_update(changed, PRICE_FIELDS)

        cart.subtotal_amount = subtotal
        cart.discount_amount = round_price(discount)
        cart.free_shipping = bool(result and result.free_shipping)
        extra = {k: v for k, v in (cart.extra_data or {}).items() if k not in ("notices", "promotion")}
        if result and result.coupon.auto_apply and not cart.coupon_code:
            extra["promotion"] = {"code": result.coupon.code, "name": result.coupon.name}
        if notices:
            extra["notices"] = notices
        cart.extra_data = extra
        cart.total_amount = round_price(max(ZERO, subtotal - discount))
        cart.tax_amount = round_price(tax_total)
        cart.items_count = sum(line.quantity for line in lines)
        cart.save(
            update_fields=[
                "subtotal_amount",
                "discount_amount",
                "free_shipping",
                "coupon_code",
                "coupon_error",
                "total_amount",
                "tax_amount",
                "items_count",
                "extra_data",
                "updated_at",
                "last_activity",
            ]
        )
        return lines, result


# ── Mutations ────────────────────────────────────────────────────────────────


class CartService:
    def __init__(self, cart: Cart):
        self.cart = cart

    # helpers
    def _lock(self):
        """Lock the cart row for the rest of the transaction and refresh it."""
        locked = Cart.objects.select_for_update().get(pk=self.cart.pk)
        if locked.status == Cart.STATUS_LOCKED:
            raise CartLockedError()
        if locked.status != Cart.STATUS_ACTIVE:
            raise CartError("This cart can no longer be modified.", code="cart_inactive")
        self.cart.__dict__.update({f.attname: getattr(locked, f.attname) for f in Cart._meta.concrete_fields})
        return self.cart

    def _validate_quantity(self, quantity):
        limit = cart_setting("CART_MAX_QUANTITY_PER_ITEM")
        if quantity < 1:
            raise CartError("Quantity must be at least 1.", code="invalid_quantity")
        if limit and quantity > limit:
            raise CartLimitError(f"You can buy at most {limit} units of an item.", extra={"max_quantity": limit})

    def _validate_stock(self, product, quantity):
        if fc_setting("ALLOW_OVERSELL", False):
            return
        if is_installed("flexcommerce_inventory"):
            from flexcommerce_inventory.services import check_available

            check_available(product, quantity)
            return
        stock = getattr(product, "stock", None)
        if stock is not None and stock < quantity:
            raise InsufficientStockError(available=stock, requested=quantity)

    def _finish(self):
        CartPricingService(self.cart).recalculate()

    # items
    @transaction.atomic
    def add_item(self, product, quantity: int = 1) -> CartItem:
        self._lock()
        if not is_purchasable(product):
            raise ProductUnavailableError(extra={"product_id": str(product.pk)})
        ct = ContentType.objects.get_for_model(product)
        item = CartItem.objects.filter(cart=self.cart, content_type=ct, object_id=product.pk).first()
        new_qty = quantity + (item.quantity if item else 0)
        self._validate_quantity(new_qty)
        self._validate_stock(product, new_qty)
        created = item is None
        if created:
            max_items = cart_setting("CART_MAX_ITEMS")
            if max_items and self.cart.items.count() >= max_items:
                raise CartLimitError(f"Your cart can hold at most {max_items} different items.")
            item = CartItem.objects.create(cart=self.cart, content_type=ct, object_id=product.pk, quantity=quantity)
        else:
            item.quantity = new_qty
            item.save(update_fields=["quantity", "updated_at"])
        self._finish()
        item.refresh_from_db()
        emit(
            "cart.item_added",
            signal=cart_signals.item_added,
            sender=Cart,
            cart=self.cart,
            item=item,
            created=created,
        )
        return item

    @transaction.atomic
    def update_quantity(self, item_id, quantity: int):
        self._lock()
        item = self._get_item(item_id)
        if quantity <= 0:
            return self._remove(item)
        self._validate_quantity(quantity)
        product = item.product
        if product is None:
            raise CartItemNotFoundError("This product no longer exists.")
        self._validate_stock(product, quantity)
        item.quantity = quantity
        item.save(update_fields=["quantity", "updated_at"])
        self._finish()
        item.refresh_from_db()
        emit(
            "cart.item_updated",
            signal=cart_signals.item_updated,
            sender=Cart,
            cart=self.cart,
            item=item,
        )
        return item

    @transaction.atomic
    def remove_item(self, item_id) -> None:
        self._lock()
        self._remove(self._get_item(item_id))

    def _remove(self, item):
        item.delete()
        self._finish()
        emit(
            "cart.item_removed",
            signal=cart_signals.item_removed,
            sender=Cart,
            cart=self.cart,
            item=item,
        )

    def _get_item(self, item_id):
        try:
            return CartItem.objects.get(cart=self.cart, pk=item_id)
        except (CartItem.DoesNotExist, ValueError, TypeError):
            raise CartItemNotFoundError() from None
        except Exception:  # malformed UUID
            raise CartItemNotFoundError() from None

    @transaction.atomic
    def clear(self) -> None:
        self._lock()
        self.cart.items.all().delete()
        self.cart.coupon_code = ""
        self._finish()
        emit("cart.cleared", signal=cart_signals.cart_cleared, sender=Cart, cart=self.cart)

    # contact
    @transaction.atomic
    def set_contact(self, email="", phone=""):
        self._lock()
        self.cart.email = email or self.cart.email
        self.cart.phone = phone or self.cart.phone
        self.cart.save(update_fields=["email", "phone", "updated_at"])
        return self.cart

    # coupons
    @transaction.atomic
    def apply_coupon(self, code: str) -> dict:
        from flexcommerce_core.exceptions import CouponAlreadyAppliedError

        if not is_installed("flexcommerce_discounts"):
            raise CartError("Discounts are not enabled.", code="discounts_not_installed")
        from flexcommerce_discounts import services as discount_services

        self._lock()
        coupon = discount_services.get_coupon(code)
        if self.cart.coupon_code and self.cart.coupon_code.upper() == coupon.code:
            raise CouponAlreadyAppliedError()
        lines, _ = CartPricingService(self.cart).priced_lines()
        subtotal = sum((line.line_gross for line in lines), ZERO)
        user = self.cart.user
        result = discount_services.evaluate(
            coupon, lines, subtotal, user=user, email=self.cart.email or getattr(user, "email", "")
        )
        self.cart.coupon_code = coupon.code
        self.cart.save(update_fields=["coupon_code", "updated_at"])
        self._finish()
        emit(
            "cart.coupon_applied",
            signal=cart_signals.coupon_applied,
            sender=Cart,
            cart=self.cart,
            code=coupon.code,
            discount=result.to_dict(),
        )
        return result.to_dict()

    @transaction.atomic
    def remove_coupon(self):
        self._lock()
        self.cart.coupon_code = ""
        self.cart.save(update_fields=["coupon_code", "updated_at"])
        self._finish()
        emit("cart.coupon_removed", signal=cart_signals.coupon_removed, sender=Cart, cart=self.cart)

    # save for later
    @transaction.atomic
    def save_for_later(self, item_id) -> SavedItem:
        if not self.cart.user_id:
            raise CartError("Sign in to save items for later.", code="authentication_required")
        self._lock()
        item = self._get_item(item_id)
        saved, _ = SavedItem.objects.update_or_create(
            user=self.cart.user,
            content_type=item.content_type,
            object_id=item.object_id,
            defaults={"saved_price": item.unit_price_with_tax},
        )
        item.delete()
        self._finish()
        return saved

    def restore_saved_item(self, saved_item_id) -> CartItem:
        try:
            saved = SavedItem.objects.get(pk=saved_item_id, user=self.cart.user)
        except Exception:
            raise CartItemNotFoundError("Saved item not found.") from None
        product = saved.product
        if product is None:
            saved.delete()  # clean up the dangling entry, then report it
            raise CartItemNotFoundError("This product no longer exists.")
        with transaction.atomic():
            item = self.add_item(product, quantity=1)
            saved.delete()
        return item

    # merge
    @transaction.atomic
    def merge_with(self, anonymous_cart: Cart) -> Cart:
        """Merge an anonymous cart into this (user) cart. Quantities are capped."""
        self._lock()
        source = Cart.objects.select_for_update().get(pk=anonymous_cart.pk)
        if source.pk == self.cart.pk or source.status != Cart.STATUS_ACTIVE:
            return self.cart
        limit = cart_setting("CART_MAX_QUANTITY_PER_ITEM") or 10**9
        existing = {(i.content_type_id, i.object_id): i for i in self.cart.items.all()}
        for anon_item in source.items.all():
            key = (anon_item.content_type_id, anon_item.object_id)
            if key in existing:
                target = existing[key]
                target.quantity = min(limit, target.quantity + anon_item.quantity)
                target.save(update_fields=["quantity", "updated_at"])
            else:
                CartItem.objects.create(
                    cart=self.cart,
                    content_type_id=anon_item.content_type_id,
                    object_id=anon_item.object_id,
                    quantity=min(limit, anon_item.quantity),
                )
        if not self.cart.coupon_code and source.coupon_code:
            self.cart.coupon_code = source.coupon_code
            self.cart.save(update_fields=["coupon_code", "updated_at"])
        source.items.all().delete()
        source.status = Cart.STATUS_MERGED
        source.save(update_fields=["status", "updated_at"])
        self._finish()
        emit(
            "cart.merged",
            signal=cart_signals.cart_merged,
            sender=Cart,
            cart=self.cart,
            merged_from=source,
        )
        return self.cart

    # product lookup (for views)
    @staticmethod
    def resolve_product(product_type, product_id):
        return get_product(product_type, product_id, for_purchase=True)


# ── Request → cart ───────────────────────────────────────────────────────────


class CartSessionManager:
    SESSION_KEY = None  # kept for compatibility

    @classmethod
    def get_session_key(cls):
        return cart_setting("CART_SESSION_KEY")

    @staticmethod
    def _header_token(request):
        header = cart_setting("CART_TOKEN_HEADER")
        meta_key = "HTTP_" + header.upper().replace("-", "_")
        return (request.META.get(meta_key) or "").strip()[:64]

    @classmethod
    def _session_token(cls, request):
        session = getattr(request, "session", None)
        return session.get(cls.get_session_key(), "") if session is not None else ""

    @classmethod
    def _anonymous_cart(cls, request):
        token = cls._header_token(request) or cls._session_token(request)
        if token:
            cart = Cart.objects.filter(
                token=token, user__isnull=True, status__in=[Cart.STATUS_ACTIVE, Cart.STATUS_LOCKED]
            ).first()
            if cart is not None:
                return cart
        session_key = getattr(getattr(request, "session", None), "session_key", None)
        if session_key:
            return Cart.objects.filter(
                session_key=session_key,
                user__isnull=True,
                status__in=[Cart.STATUS_ACTIVE, Cart.STATUS_LOCKED],
            ).first()
        return None

    @classmethod
    def _remember(cls, request, cart):
        session = getattr(request, "session", None)
        if session is not None:
            session[cls.get_session_key()] = cart.token
        request.flexcommerce_cart_token = cart.token

    @classmethod
    def _user_cart(cls, user, create):
        cart = Cart.objects.filter(user=user, status__in=[Cart.STATUS_ACTIVE, Cart.STATUS_LOCKED]).first()
        if cart is not None or not create:
            return cart
        try:
            with transaction.atomic():
                return Cart.objects.create(user=user, currency=fc_setting("CURRENCY", "NGN"), email=user.email or "")
        except IntegrityError:  # created concurrently by another request
            return Cart.objects.get(user=user, status=Cart.STATUS_ACTIVE)

    @classmethod
    def get_cart(cls, request, create=False):
        """Cart for ``request`` (``None`` if it has none and ``create`` is False)."""
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            anon = cls._anonymous_cart(request)
            cart = cls._user_cart(user, create=create or bool(anon and anon.items_count))
            if anon is not None and cart is not None and anon.status == Cart.STATUS_ACTIVE and anon.items.exists():
                CartService(cart).merge_with(anon)
                cart.refresh_from_db()
            if cart is not None:
                cls._remember(request, cart)
            return cart
        cart = cls._anonymous_cart(request)
        if cart is None and create:
            session = getattr(request, "session", None)
            session_key = ""
            if session is not None and not cls._header_token(request):
                if not session.session_key:
                    session.save()
                session_key = session.session_key or ""
            try:
                with transaction.atomic():
                    cart = Cart(session_key=session_key, currency=fc_setting("CURRENCY", "NGN"))
                    cart.set_expiry(save=False)
                    cart.save()
            except IntegrityError:
                cart = cls._anonymous_cart(request)
        if cart is not None:
            cls._remember(request, cart)
        return cart

    @classmethod
    def get_or_create_cart(cls, request) -> Cart:
        return cls.get_cart(request, create=True)

    @classmethod
    def handle_login_merge(cls, request, user) -> Cart:
        token = cls._session_token(request) or cls._header_token(request)
        anon = Cart.objects.filter(token=token, user__isnull=True, status=Cart.STATUS_ACTIVE).first() if token else None
        if anon is None or not anon.items.exists():
            return cls._user_cart(user, create=False)
        user_cart = cls._user_cart(user, create=True)
        CartService(user_cart).merge_with(anon)
        cls._remember(request, user_cart)
        return user_cart


def on_user_logged_in(sender, request, user, **kwargs):
    if request is None or not cart_setting("CART_MERGE_ON_LOGIN"):
        return
    try:
        CartSessionManager.handle_login_merge(request, user)
    except Exception:  # never break login because of the cart
        logger.exception("FlexCommerce: cart merge on login failed")


# ── Jobs ─────────────────────────────────────────────────────────────────────


def expire_carts(limit=5000):
    """Job: expire stale anonymous carts and purge very old inactive ones."""
    from datetime import timedelta

    now = timezone.now()
    expired_ids = list(
        Cart.objects.filter(status=Cart.STATUS_ACTIVE, user__isnull=True, expires_at__lt=now).values_list(
            "pk", flat=True
        )[:limit]
    )
    for cart in Cart.objects.filter(pk__in=expired_ids):
        emit("cart.expired", signal=cart_signals.cart_expired, sender=Cart, cart=cart)
    Cart.objects.filter(pk__in=expired_ids).update(status=Cart.STATUS_EXPIRED, updated_at=now)
    purge_before = now - timedelta(days=cart_setting("CART_PURGE_DAYS"))
    purge_ids = list(
        Cart.objects.filter(
            status__in=[Cart.STATUS_EXPIRED, Cart.STATUS_MERGED],
            user__isnull=True,
            updated_at__lt=purge_before,
        ).values_list("pk", flat=True)[:limit]
    )
    Cart.objects.filter(pk__in=purge_ids).delete()
    return {"expired": len(expired_ids), "purged": len(purge_ids)}


def notify_abandoned_carts(limit=1000):
    """Job: emit ``cart.abandoned`` once per idle cart that has a way to contact the customer."""
    from datetime import timedelta

    from django.db.models import Q

    cutoff = timezone.now() - timedelta(hours=cart_setting("CART_ABANDONMENT_HOURS"))
    carts = list(
        Cart.objects.filter(
            status=Cart.STATUS_ACTIVE,
            last_activity__lt=cutoff,
            abandoned_notified_at__isnull=True,
            items_count__gt=0,
        )
        .filter(Q(user__isnull=False) | ~Q(email=""))
        .select_related("user")[:limit]
    )
    now = timezone.now()
    Cart.objects.filter(pk__in=[c.pk for c in carts]).update(abandoned_notified_at=now)
    for cart in carts:
        emit(
            "cart.abandoned",
            signal=cart_signals.cart_abandoned,
            sender=Cart,
            payload=cart_payload(cart),
            cart=cart,
        )
    return {"notified": len(carts)}


def recalculate_totals(cart):
    return CartPricingService(cart).recalculate()
