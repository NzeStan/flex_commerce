"""
Product protocol helpers.

FlexCommerce works with *any* Django model as a purchasable product as long as
it is listed in ``FLEXCOMMERCE["PRODUCT_MODELS"]`` and has a UUID primary key.
Recognised attributes (all optional except ``price``):

=====================  ==================================================
``price``              unit price (Decimal)
``name`` / ``sku``     used for order snapshots
``is_active`` /        product can be bought when truthy (default True)
``is_purchasable``
``vat_exempt`` /       zero VAT
``vat_zero_rated``
``tax_category``       object with ``get_rate()`` (flexcommerce_pricing)
``vendor_id``          marketplace vendor UUID
``weight``             kg, for weight-based shipping
``stock``              used for stock checks when flexcommerce_inventory
                       is not installed
``category_ids``       iterable of category ids (coupon restrictions)
=====================  ==================================================
"""

from collections import defaultdict

from django.apps import apps
from django.contrib.contenttypes.models import ContentType

from ..conf import fc_setting
from ..exceptions import InvalidProductTypeError, ProductNotFoundError, ProductUnavailableError


def is_installed(app_name: str) -> bool:
    """``True`` if the FlexCommerce (or any) app is in ``INSTALLED_APPS``."""
    return apps.is_installed(app_name)


def get_product_model_paths():
    paths = fc_setting("PRODUCT_MODELS")
    if paths is None:
        paths = ["flexcommerce_catalog.ProductVariant"] if is_installed("flexcommerce_catalog") else []
    if isinstance(paths, str):
        paths = [paths]
    return list(paths)


def load_model(dotted_path: str):
    """Load a model from ``'app_label.ModelName'``."""
    parts = dotted_path.split(".")
    if len(parts) != 2:
        raise ValueError(f"Invalid model path: {dotted_path}. Expected 'app_label.ModelName'.")
    return apps.get_model(parts[0], parts[1])


def get_product_models():
    return [load_model(p) for p in get_product_model_paths()]


def product_type_label(model_or_instance) -> str:
    """``"app_label.modelname"`` – the public identifier of a product type."""
    return model_or_instance._meta.label_lower


def resolve_product_model(product_type: str = None):
    """
    Return the product model for a client-supplied ``product_type``.
    Only models listed in ``PRODUCT_MODELS`` are accepted — never trust input.
    When omitted and exactly one product model is configured, that one is used.
    """
    models = get_product_models()
    if not models:
        raise InvalidProductTypeError("No product models are configured (FLEXCOMMERCE['PRODUCT_MODELS']).")
    if not product_type:
        if len(models) == 1:
            return models[0]
        raise InvalidProductTypeError("product_type is required.")
    wanted = str(product_type).strip().lower()
    for model in models:
        label = model._meta.label_lower
        if wanted in (label, model._meta.model_name) or wanted == label.replace(".", "_"):
            return model
    raise InvalidProductTypeError(extra={"product_type": product_type})


def get_product(product_type, product_id, for_purchase=True):
    """
    Fetch a whitelisted product. Without ``product_type`` every configured model
    is tried (ids are UUIDs, so the match is unambiguous).
    """
    models = [resolve_product_model(product_type)] if product_type else get_product_models()
    if not models:
        raise InvalidProductTypeError("No product models are configured (FLEXCOMMERCE['PRODUCT_MODELS']).")
    product = None
    for model in models:
        try:
            product = model._default_manager.get(pk=product_id)
            break
        except Exception:  # noqa: S112 - DoesNotExist, or ValidationError for malformed UUIDs
            continue
    if product is None:
        raise ProductNotFoundError(extra={"product_id": str(product_id)})
    if for_purchase and not is_purchasable(product):
        raise ProductUnavailableError(extra={"product_id": str(product_id)})
    return product


def is_product_instance(obj) -> bool:
    return any(isinstance(obj, m) for m in get_product_models())


def is_purchasable(product) -> bool:
    for attr in ("is_purchasable", "is_active"):
        value = getattr(product, attr, True)
        if callable(value):
            value = value()
        if not value:
            return False
    return True


def product_name(product) -> str:
    name = getattr(product, "display_name", None) or getattr(product, "name", None) or getattr(product, "title", None)
    return str(name or product)[:255]


def product_sku(product) -> str:
    return str(getattr(product, "sku", "") or "")[:100]


def product_vendor_id(product):
    return getattr(product, "vendor_id", None)


def product_weight(product):
    from .vat import to_decimal

    return to_decimal(getattr(product, "weight", 0) or 0, default=to_decimal(0))


def product_category_ids(product):
    ids = getattr(product, "category_ids", None)
    if callable(ids):
        ids = ids()
    if ids is None:
        category_id = getattr(product, "category_id", None)
        ids = [category_id] if category_id else []
    return {str(i) for i in ids}


def prefetch_products(items, ct_field="content_type", id_field="object_id"):
    """
    Resolve generic products for many rows with one query per product type
    (avoids N+1). Returns ``{(content_type_id, object_id): product}``.
    """
    by_ct = defaultdict(set)
    for item in items:
        by_ct[getattr(item, f"{ct_field}_id")].add(getattr(item, id_field))
    result = {}
    for ct_id, ids in by_ct.items():
        model = ContentType.objects.get_for_id(ct_id).model_class()
        if model is None:
            continue
        for obj in model._default_manager.filter(pk__in=ids):
            result[(ct_id, obj.pk)] = obj
    return result
