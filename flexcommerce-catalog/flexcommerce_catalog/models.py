"""
FlexCommerce Catalog.

Category tree, brands, products with variants and images — the purchasable unit
is ``ProductVariant`` (every product has at least one variant, Shopify-style), so
sizes/colours, SKUs, prices and stock are tracked per variant.
"""

from decimal import Decimal

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import F, Max, Min, Value
from django.db.models.functions import Concat, Substr
from django.utils.functional import cached_property
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from flexcommerce_core.models import TimeStampedUUIDModel
from flexcommerce_core.utils.products import is_installed

from .conf import catalog_setting

CATEGORY_TREE_CACHE_KEY = "flexcommerce:catalog:category_tree"


def unique_slug(model, value, instance_pk=None, field="slug", max_length=200):
    base = slugify(value)[: max_length - 9] or "item"
    slug, n = base, 2
    qs = model._default_manager.all()
    if instance_pk:
        qs = qs.exclude(pk=instance_pk)
    while qs.filter(**{field: slug}).exists():
        slug = f"{base}-{n}"
        n += 1
    return slug


# ── Category ──────────────────────────────────────────────────────────────────


class Category(TimeStampedUUIDModel):
    """Hierarchical category using a materialised path for fast subtree queries."""

    name = models.CharField(_("name"), max_length=150)
    slug = models.SlugField(_("slug"), max_length=200, unique=True, blank=True)
    parent = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="children", verbose_name=_("parent")
    )
    description = models.TextField(_("description"), blank=True)
    image_url = models.URLField(_("image URL"), max_length=500, blank=True)
    is_active = models.BooleanField(_("is active"), default=True)
    sort_order = models.PositiveIntegerField(_("sort order"), default=0)
    path = models.CharField(_("path"), max_length=1000, db_index=True, editable=False, default="")
    depth = models.PositiveSmallIntegerField(_("depth"), default=0, editable=False)
    meta_title = models.CharField(_("meta title"), max_length=200, blank=True)
    meta_description = models.CharField(_("meta description"), max_length=300, blank=True)

    class Meta:
        verbose_name = _("category")
        verbose_name_plural = _("categories")
        ordering = ["path", "sort_order", "name"]

    def __str__(self):
        return " › ".join(c.name for c in self.get_ancestors(include_self=True))

    def clean(self):
        parent = self.parent
        if parent is None:
            return
        if parent.pk == self.pk or (self.path and parent.path.startswith(self.path)):
            raise ValidationError({"parent": _("A category cannot be its own ancestor.")})
        if parent.depth + 1 >= catalog_setting("CATALOG_MAX_CATEGORY_DEPTH"):
            raise ValidationError({"parent": _("Category tree is too deep.")})

    def _compute_path(self):
        segment = self.pk.hex
        if self.parent_id:
            parent = Category.objects.only("path", "depth").get(pk=self.parent_id)
            return f"{parent.path}{segment}/", parent.depth + 1
        return f"{segment}/", 0

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slug(Category, self.name, self.pk)
        old_path = None
        if not self._state.adding:
            old_path = Category.objects.filter(pk=self.pk).values_list("path", flat=True).first()
        self.path, self.depth = self._compute_path()
        if kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = set(kwargs["update_fields"]) | {"path", "depth", "slug"}
        with transaction.atomic():
            super().save(*args, **kwargs)
            if old_path and old_path != self.path:
                # Re-root the whole subtree in a single UPDATE when the category moves.
                old_depth = old_path.count("/") - 1
                Category.objects.filter(path__startswith=old_path).exclude(pk=self.pk).update(
                    path=Concat(Value(self.path), Substr("path", len(old_path) + 1)),
                    depth=F("depth") + (self.depth - old_depth),
                )
        cache.delete(CATEGORY_TREE_CACHE_KEY)

    def delete(self, *args, **kwargs):
        result = super().delete(*args, **kwargs)
        cache.delete(CATEGORY_TREE_CACHE_KEY)
        return result

    def get_ancestors(self, include_self=False):
        ids = [seg for seg in self.path.strip("/").split("/") if seg]
        if not include_self and ids:
            ids = ids[:-1]
        if not ids:
            return []
        by_hex = {c.pk.hex: c for c in Category.objects.filter(pk__in=ids)}
        return [by_hex[i] for i in ids if i in by_hex]

    def get_descendants(self, include_self=True):
        qs = Category.objects.filter(path__startswith=self.path)
        return qs if include_self else qs.exclude(pk=self.pk)


# ── Brand ─────────────────────────────────────────────────────────────────────


class Brand(TimeStampedUUIDModel):
    name = models.CharField(_("name"), max_length=150, unique=True)
    slug = models.SlugField(_("slug"), max_length=200, unique=True, blank=True)
    description = models.TextField(_("description"), blank=True)
    logo_url = models.URLField(_("logo URL"), max_length=500, blank=True)
    is_active = models.BooleanField(_("is active"), default=True)
    is_official_store = models.BooleanField(_("official store"), default=False)

    class Meta:
        verbose_name = _("brand")
        verbose_name_plural = _("brands")
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slug(Brand, self.name, self.pk)
        super().save(*args, **kwargs)


# ── Product ───────────────────────────────────────────────────────────────────


class ProductQuerySet(models.QuerySet):
    def active(self):
        return self.filter(status=Product.STATUS_ACTIVE)

    def in_category(self, category):
        return self.filter(categories__path__startswith=category.path).distinct()


class Product(TimeStampedUUIDModel):
    STATUS_DRAFT = "draft"
    STATUS_ACTIVE = "active"
    STATUS_ARCHIVED = "archived"
    STATUS_CHOICES = [
        (STATUS_DRAFT, _("Draft")),
        (STATUS_ACTIVE, _("Active")),
        (STATUS_ARCHIVED, _("Archived")),
    ]

    name = models.CharField(_("name"), max_length=255)
    slug = models.SlugField(_("slug"), max_length=280, unique=True, blank=True)
    short_description = models.CharField(_("short description"), max_length=500, blank=True)
    description = models.TextField(_("description"), blank=True)
    brand = models.ForeignKey(
        Brand, on_delete=models.SET_NULL, null=True, blank=True, related_name="products", verbose_name=_("brand")
    )
    categories = models.ManyToManyField(Category, related_name="products", blank=True, verbose_name=_("categories"))
    status = models.CharField(_("status"), max_length=10, choices=STATUS_CHOICES, default=STATUS_DRAFT, db_index=True)
    is_featured = models.BooleanField(_("featured"), default=False, db_index=True)
    vendor_id = models.UUIDField(_("vendor ID"), null=True, blank=True, db_index=True)
    specifications = models.JSONField(
        _("specifications"), default=dict, blank=True, help_text=_('Key/value specs, e.g. {"RAM": "8GB"}')
    )
    tags = models.JSONField(_("tags"), default=list, blank=True)
    vat_exempt = models.BooleanField(_("VAT exempt"), default=False)
    tax_category_code = models.SlugField(
        _("tax category code"), blank=True, help_text=_("Code of a flexcommerce_pricing TaxCategory.")
    )
    meta_title = models.CharField(_("meta title"), max_length=200, blank=True)
    meta_description = models.CharField(_("meta description"), max_length=300, blank=True)

    # Denormalised for fast listing / filtering / sorting at scale.
    min_price = models.DecimalField(
        _("min price"), max_digits=14, decimal_places=2, default=Decimal("0.00"), db_index=True
    )
    max_price = models.DecimalField(_("max price"), max_digits=14, decimal_places=2, default=Decimal("0.00"))
    compare_at_price = models.DecimalField(
        _("compare-at price"),
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=_("Old price of the cheapest variant (for 'x% off' badges)."),
    )
    in_stock = models.BooleanField(_("in stock"), default=True, db_index=True)
    rating_avg = models.DecimalField(
        _("average rating"), max_digits=3, decimal_places=2, default=Decimal("0.00"), db_index=True
    )
    rating_count = models.PositiveIntegerField(_("rating count"), default=0)
    sold_count = models.PositiveIntegerField(_("units sold"), default=0, db_index=True)
    published_at = models.DateTimeField(_("published at"), null=True, blank=True, db_index=True)

    objects = ProductQuerySet.as_manager()

    class Meta:
        verbose_name = _("product")
        verbose_name_plural = _("products")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "-published_at"], name="catalog_prod_status_pub_idx"),
            models.Index(fields=["status", "min_price"], name="catalog_prod_status_price_idx"),
            models.Index(fields=["vendor_id", "status"], name="catalog_prod_vendor_idx"),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        from django.utils import timezone

        if not self.slug:
            self.slug = unique_slug(Product, self.name, self.pk, max_length=280)
        if self.status == self.STATUS_ACTIVE and not self.published_at:
            self.published_at = timezone.now()
            if kwargs.get("update_fields") is not None:
                kwargs["update_fields"] = set(kwargs["update_fields"]) | {"published_at"}
        super().save(*args, **kwargs)

    @property
    def is_active(self):
        return self.status == self.STATUS_ACTIVE

    @property
    def discount_percent(self):
        if self.compare_at_price and self.compare_at_price > self.min_price > 0:
            return int(((self.compare_at_price - self.min_price) / self.compare_at_price * 100).quantize(Decimal("1")))
        return 0

    @cached_property
    def tax_category(self):
        if not self.tax_category_code or not is_installed("flexcommerce_pricing"):
            return None
        from flexcommerce_pricing.models import TaxCategory

        return TaxCategory.objects.filter(code=self.tax_category_code).first()

    def category_ids(self):
        return [str(pk) for pk in self.categories.values_list("pk", flat=True)]

    def purchasable_ids(self):
        """Ids of the purchasable rows (variants) — used for 'verified purchase'."""
        return list(self.variants.values_list("pk", flat=True))

    @property
    def default_variant(self):
        variants = list(self.variants.all())
        active = [v for v in variants if v.is_active]
        for v in active:
            if v.is_default:
                return v
        return active[0] if active else None

    def refresh_price_range(self, save=True):
        active = self.variants.filter(is_active=True)
        agg = active.aggregate(lo=Min("price"), hi=Max("price"))
        self.min_price = agg["lo"] or Decimal("0.00")
        self.max_price = agg["hi"] or Decimal("0.00")
        # Old price of the cheapest variant, so "x% off" compares like with like.
        self.compare_at_price = active.order_by("price").values_list("compare_at_price", flat=True).first()
        if save:
            Product.objects.filter(pk=self.pk).update(
                min_price=self.min_price, max_price=self.max_price, compare_at_price=self.compare_at_price
            )


class ProductVariant(TimeStampedUUIDModel):
    """The purchasable unit. Implements the FlexCommerce product protocol."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="variants", verbose_name=_("product"))
    sku = models.CharField(_("SKU"), max_length=100, unique=True)
    name = models.CharField(_("option name"), max_length=255, blank=True, help_text=_('e.g. "Red / XL"'))
    attributes = models.JSONField(_("attributes"), default=dict, blank=True, help_text=_('e.g. {"color": "Red"}'))
    price = models.DecimalField(_("price"), max_digits=14, decimal_places=2)
    compare_at_price = models.DecimalField(
        _("compare-at price"), max_digits=14, decimal_places=2, null=True, blank=True
    )
    cost_price = models.DecimalField(_("cost price"), max_digits=14, decimal_places=2, null=True, blank=True)
    weight = models.DecimalField(_("weight (kg)"), max_digits=10, decimal_places=3, default=Decimal("0.000"))
    barcode = models.CharField(_("barcode"), max_length=64, blank=True)
    is_active = models.BooleanField(_("is active"), default=True)
    is_default = models.BooleanField(_("is default"), default=False)
    sort_order = models.PositiveIntegerField(_("sort order"), default=0)

    class Meta:
        verbose_name = _("product variant")
        verbose_name_plural = _("product variants")
        ordering = ["sort_order", "created_at"]
        indexes = [models.Index(fields=["product", "is_active"], name="catalog_variant_active_idx")]

    def __str__(self):
        return self.display_name

    def clean(self):
        if self.price is not None and self.price < 0:
            raise ValidationError({"price": _("Price cannot be negative.")})

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.product.refresh_price_range()

    def delete(self, *args, **kwargs):
        product = self.product
        result = super().delete(*args, **kwargs)
        product.refresh_price_range()
        return result

    # ── product protocol ──────────────────────────────────────────────────────
    @property
    def display_name(self):
        return f"{self.product.name} - {self.name}" if self.name else self.product.name

    @property
    def is_purchasable(self):
        return self.is_active and self.product.status == Product.STATUS_ACTIVE

    @property
    def vendor_id(self):
        return self.product.vendor_id

    @property
    def vat_exempt(self):
        return self.product.vat_exempt

    @property
    def tax_category(self):
        return self.product.tax_category

    def category_ids(self):
        return self.product.category_ids()

    @property
    def discount_percent(self):
        if self.compare_at_price and self.compare_at_price > self.price > 0:
            return int(((self.compare_at_price - self.price) / self.compare_at_price * 100).quantize(Decimal("1")))
        return 0

    def _display(self, image=""):
        return {
            "product_id": str(self.product_id),
            "product_slug": self.product.slug,
            "variant_name": self.name,
            "attributes": self.attributes,
            "image": image,
            "compare_at_price": str(self.compare_at_price) if self.compare_at_price else None,
        }

    def order_snapshot(self):
        """Stored on the order line so order history survives catalog edits."""
        image = next((img.get_url() for img in self.product.images.all()), "")
        return self._display(image)

    @classmethod
    def bulk_cart_display(cls, variants):
        """Display info for many variants in two queries (used by the cart API)."""
        ids = [v.pk for v in variants]
        rows = cls.objects.filter(pk__in=ids).select_related("product")
        images = {}
        for img in ProductImage.objects.filter(product__variants__pk__in=ids).order_by("-is_primary", "sort_order"):
            images.setdefault(img.product_id, img.get_url())
        return {v.pk: v._display(images.get(v.product_id, "")) for v in rows}


class ProductImage(TimeStampedUUIDModel):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="images")
    variant = models.ForeignKey(ProductVariant, on_delete=models.SET_NULL, null=True, blank=True, related_name="images")
    image = models.FileField(_("image file"), upload_to="flexcommerce/products/%Y/%m/", blank=True)
    url = models.URLField(_("image URL"), max_length=500, blank=True, help_text=_("Use instead of a file (e.g. CDN)."))
    alt_text = models.CharField(_("alt text"), max_length=255, blank=True)
    is_primary = models.BooleanField(_("is primary"), default=False)
    sort_order = models.PositiveIntegerField(_("sort order"), default=0)

    class Meta:
        verbose_name = _("product image")
        verbose_name_plural = _("product images")
        ordering = ["-is_primary", "sort_order", "created_at"]

    def __str__(self):
        return self.alt_text or self.get_url() or str(self.pk)

    def clean(self):
        if not self.image and not self.url:
            raise ValidationError(_("Provide an image file or an image URL."))

    def get_url(self):
        if self.image:
            try:
                return self.image.url
            except ValueError:
                return ""
        return self.url
