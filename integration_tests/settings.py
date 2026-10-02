"""Settings for the all-apps integration suite (every FlexCommerce app installed together)."""

import os
from urllib.parse import urlparse

SECRET_KEY = "integration-tests-only"
DEBUG = True
ALLOWED_HOSTS = ["*"]
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
if os.environ.get("FC_TEST_DATABASE_URL"):
    _url = urlparse(os.environ["FC_TEST_DATABASE_URL"])
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": _url.path.lstrip("/") or "postgres",
            "USER": _url.username or "postgres",
            "PASSWORD": _url.password or "",
            "HOST": _url.hostname or "localhost",
            "PORT": _url.port or 5432,
            "TEST": {"NAME": "test_flexcommerce_integration"},
        }
    }

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "rest_framework",
    "flexcommerce_core",
    "flexcommerce_catalog",
    "flexcommerce_pricing",
    "flexcommerce_inventory",
    "flexcommerce_discounts",
    "flexcommerce_shipping",
    "flexcommerce_cart",
    "flexcommerce_orders",
    "flexcommerce_payments",
    "flexcommerce_checkout",
    "flexcommerce_engagement",
    "flexcommerce_marketplace",
    "flexcommerce_notifications",
    "flexcommerce_analytics",
    "integration_tests.shop",
]
MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "flexcommerce_cart.middleware.CartMiddleware",
]
TEMPLATES = [
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
]
ROOT_URLCONF = "integration_tests.urls"
AUTH_USER_MODEL = "shop.Customer"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "Africa/Lagos"
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
DEFAULT_FROM_EMAIL = "orders@naijamart.example"
REST_FRAMEWORK = {"DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"]}

FLEXCOMMERCE = {
    "VAT_RATE": 0.075,
    "VAT_INCLUSIVE": True,
    "MARKETPLACE_MODE": True,
    "INVENTORY_TRACK_BY_DEFAULT": True,
    "THROTTLING_ENABLED": False,
    "PAYSTACK_SECRET_KEY": "sk_test_integration",
    "PAYMENT_CALLBACK_URL": "https://naijamart.example/checkout/complete",
    "PAYMENT_ALLOWED_CALLBACK_HOSTS": ["naijamart.example"],
    "BANK_TRANSFER_ACCOUNTS": [{"bank_name": "Access", "account_name": "Naija Mart", "account_number": "0011223344"}],
    "PAY_ON_DELIVERY_LIMIT": 300000,
    "STORE_NAME": "Naija Mart",
    "FRONTEND_URL": "https://naijamart.example",
    "NOTIFICATION_SMS_BACKEND": "flexcommerce_notifications.backends.NullBackend",
    "WEBHOOK_ALLOW_PRIVATE_URLS": True,
}
