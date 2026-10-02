"""HTTP helper for gateways (thin wrapper over ``flexcommerce_core.http``)."""

from flexcommerce_core.http import HTTPClientError as GatewayHTTPError
from flexcommerce_core.http import _decode  # noqa: F401  (re-exported)
from flexcommerce_core.http import request_json as _core_request_json

from .conf import payments_setting


def request_json(method, url, headers=None, payload=None, timeout=None):
    return _core_request_json(
        method, url, headers=headers, payload=payload, timeout=timeout or payments_setting("PAYMENT_HTTP_TIMEOUT")
    )


__all__ = ["GatewayHTTPError", "request_json"]
