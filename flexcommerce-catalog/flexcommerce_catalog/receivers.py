"""
Optional integrations. Each is connected only if the other app is installed.

* inventory ``stock_changed``   → ``Product.in_stock``
* orders   ``order_confirmed``  → ``Product.sold_count``
* engagement ``rating_changed`` → ``Product.rating_avg`` / ``rating_count``
"""

import logging
from collections import defaultdict

from django.contrib.contenttypes.models import ContentType
from django.db.models import F

logger = logging.getLogger("flexcommerce.catalog")


def _variant_ct():
    from .models import ProductVariant

    return ContentType.objects.get_for_model(ProductVariant)


def refresh_product_stock(product_ids):
    """Recompute ``in_stock`` for the given products from inventory records."""
    from flexcommerce_inventory.services import availability_map

    from .models import Product, ProductVariant

    for product_id in set(product_ids):
        variants = list(ProductVariant.objects.filter(product_id=product_id, is_active=True))
        available = availability_map(variants)
        in_stock = any(a is None or a > 0 for a in available.values())
        Product.objects.filter(pk=product_id).exclude(in_stock=in_stock).update(in_stock=in_stock)


def on_stock_changed(sender, item, **kwargs):
    from .models import ProductVariant

    if item.content_type_id != _variant_ct().pk:
        return
    product_id = ProductVariant.objects.filter(pk=item.object_id).values_list("product_id", flat=True).first()
    if product_id:
        refresh_product_stock([product_id])


def on_order_confirmed(sender, order, **kwargs):
    from .models import Product, ProductVariant

    ct_id = _variant_ct().pk
    quantities = defaultdict(int)
    items = [i for i in order.items.all() if i.content_type_id == ct_id]
    product_by_variant = dict(
        ProductVariant.objects.filter(pk__in=[i.object_id for i in items]).values_list("pk", "product_id")
    )
    for item in items:
        product_id = product_by_variant.get(item.object_id)
        if product_id:
            quantities[product_id] += item.quantity
    for product_id, qty in quantities.items():
        Product.objects.filter(pk=product_id).update(sold_count=F("sold_count") + qty)


def on_rating_changed(sender, content_type, object_id, average, count, **kwargs):
    from .models import Product

    if content_type.model_class() is Product:
        Product.objects.filter(pk=object_id).update(rating_avg=average, rating_count=count)


def connect():
    from flexcommerce_core.utils.products import is_installed

    if is_installed("flexcommerce_inventory"):
        from flexcommerce_inventory.signals import stock_changed

        stock_changed.connect(on_stock_changed, dispatch_uid="catalog_stock_changed")
    if is_installed("flexcommerce_orders"):
        from flexcommerce_orders.signals import order_confirmed

        order_confirmed.connect(on_order_confirmed, dispatch_uid="catalog_order_confirmed")
    if is_installed("flexcommerce_engagement"):
        from flexcommerce_engagement.signals import rating_changed

        rating_changed.connect(on_rating_changed, dispatch_uid="catalog_rating_changed")
