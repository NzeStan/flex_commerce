# flexcommerce-catalog

Product catalogue: category tree, brands, products with variants (size, colour…), images, search, filters, facets and related products. Approved marketplace vendors manage their own products.

Part of **[FlexCommerce](https://github.com/NzeStan/flex_commerce/blob/main/README.md)** — a complete, modular e-commerce and marketplace backend for
Django + DRF, built for Nigeria and Africa. Every app is optional.

## Features

- Materialised-path category tree with cached nested endpoint
- Products → variants (the purchasable unit, each with SKU, price, old price, weight, attributes)
- Filters: category (incl. children), brand, price, rating, in stock, on sale, attributes; 7 sort orders
- Pluggable search (database, PostgreSQL full-text, or Elasticsearch/Meilisearch via a function)
- Denormalised price range, stock status, rating and units sold for fast listings
- Facets endpoint (price range + brand counts) for filter sidebars

## Install

```bash
pip install flexcommerce-catalog
```

```python
INSTALLED_APPS = [..., "rest_framework", "flexcommerce_core", "flexcommerce_catalog"]
urlpatterns = [path("api/", include("flexcommerce_core.urls"))]
```

```bash
python manage.py migrate
```

Example: `GET /api/catalog/products/?category=phones&brand=tecno&ordering=price`

## Documentation

- [Integration guide](https://github.com/NzeStan/flex_commerce/blob/main/INTEGRATION.md)
- [Settings](https://github.com/NzeStan/flex_commerce/blob/main/docs/settings.md) · [API](https://github.com/NzeStan/flex_commerce/blob/main/docs/api.md) · [Events & notifications](https://github.com/NzeStan/flex_commerce/blob/main/docs/events.md)
- [Extending](https://github.com/NzeStan/flex_commerce/blob/main/docs/extending.md) · [Operations](https://github.com/NzeStan/flex_commerce/blob/main/docs/operations.md) · [Changelog](https://github.com/NzeStan/flex_commerce/blob/main/CHANGELOG.md)

Requires Python 3.10+, Django 4.2+, Django REST framework 3.14+. MIT licensed.
