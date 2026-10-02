"""
Smoke-test the *built wheels*: run inside a fresh virtualenv where only the
wheels from ``dist/`` are installed (never the source tree).

    pip install --find-links dist "flexcommerce[all]"
    python scripts/smoke_install.py
"""

import os
import sys
import tempfile
from decimal import Decimal
from io import StringIO
from pathlib import Path

import django
from django.conf import settings


def configure(db_path):
    from flexcommerce import FLEXCOMMERCE_APPS, MARKETPLACE_APPS

    settings.configure(
        SECRET_KEY="smoke",
        DEBUG=True,
        ALLOWED_HOSTS=["*"],
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": db_path}},
        INSTALLED_APPS=[
            "django.contrib.admin",
            "django.contrib.auth",
            "django.contrib.contenttypes",
            "django.contrib.sessions",
            "django.contrib.messages",
            "rest_framework",
            *FLEXCOMMERCE_APPS,
            *MARKETPLACE_APPS,
        ],
        MIDDLEWARE=[
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.contrib.auth.middleware.AuthenticationMiddleware",
            "django.contrib.messages.middleware.MessageMiddleware",
        ],
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "APP_DIRS": True,
                "OPTIONS": {
                    "context_processors": [
                        "django.contrib.auth.context_processors.auth",
                        "django.contrib.messages.context_processors.messages",
                        "django.template.context_processors.request",
                    ]
                },
            }
        ],
        ROOT_URLCONF="smoke_urls",
        USE_TZ=True,
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        FLEXCOMMERCE={"MARKETPLACE_MODE": True},
    )


def main():
    here = Path(tempfile.mkdtemp())
    (here / "smoke_urls.py").write_text(
        "from django.urls import include, path\nurlpatterns = [path('api/', include('flexcommerce_core.urls'))]\n"
    )
    sys.path.insert(0, str(here))
    configure(str(here / "db.sqlite3"))
    django.setup()

    from django.core import mail
    from django.core.management import call_command
    from django.test import Client

    import flexcommerce_core

    assert "site-packages" in flexcommerce_core.__file__, f"not testing the wheel: {flexcommerce_core.__file__}"
    call_command("check")
    call_command("migrate", verbosity=0)
    call_command("makemigrations", "--check", "--dry-run", verbosity=0)
    call_command("seed_nigeria_shipping", "--with-methods", stdout=StringIO())

    from flexcommerce_catalog.models import Product, ProductVariant
    from flexcommerce_shipping.models import ShippingMethod

    product = Product.objects.create(name="Garri 5kg", status="active")
    variant = ProductVariant.objects.create(product=product, sku="GARRI-5", price=Decimal("4500"))
    client = Client()
    resp = client.post(
        "/api/cart/add/", {"product_id": str(variant.pk), "quantity": 2}, content_type="application/json"
    )
    assert resp.status_code == 201, resp.content
    address = {
        "first_name": "Uche",
        "last_name": "N",
        "line1": "1 Road",
        "city": "Ikeja",
        "state": "Lagos",
        "phone": "08030000000",
    }
    resp = client.post(
        "/api/checkout/",
        {
            "payment_method": "pay_on_delivery",
            "shipping_address": address,
            "email": "u@example.com",
            "shipping_method_id": str(ShippingMethod.objects.get(code="door-lagos").pk),
        },
        content_type="application/json",
    )
    assert resp.status_code == 201, resp.content
    order = resp.json()["order"]
    assert order["status"] == "confirmed" and order["grand_total"] == "11175.00", order
    assert mail.outbox and "<html" in mail.outbox[0].alternatives[0][0], "email template missing from wheel"
    print(f"Smoke test passed on Django {django.get_version()}: order {order['order_number']}")


if __name__ == "__main__":
    os.environ.pop("DJANGO_SETTINGS_MODULE", None)
    main()
