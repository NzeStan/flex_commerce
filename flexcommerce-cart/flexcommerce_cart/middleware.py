"""Optional middleware: ``request.cart`` (lazy — no query unless used)."""

from django.utils.functional import SimpleLazyObject

from .services import CartSessionManager


class CartMiddleware:
    """
    Add ``"flexcommerce_cart.middleware.CartMiddleware"`` after
    ``AuthenticationMiddleware``. ``request.cart`` is the visitor's cart or ``None``;
    it is resolved lazily, so requests that never touch it cost nothing.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.cart = SimpleLazyObject(lambda: CartSessionManager.get_cart(request, create=False))
        return self.get_response(request)
