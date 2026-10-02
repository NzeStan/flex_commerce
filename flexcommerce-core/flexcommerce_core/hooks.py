"""
In-transaction extension hooks.

Signals (see ``events.emit``) are for side effects that happen *after* commit.
Hooks are for plugins that must take part in the business operation itself and
may veto it by raising (e.g. splitting a marketplace order per vendor while the
order is being created, or adjusting a unit price).

Register in code::

    from flexcommerce_core import hooks

    @hooks.register("order.created")
    def split_by_vendor(order, **kwargs):
        ...

or in settings::

    FLEXCOMMERCE = {"HOOKS": {"order.created": ["myapp.hooks.split_by_vendor"]}}

Built-in hook names
-------------------
``price.modify``        (product, price, user=None, quantity=1) -> Decimal
``cart.item_added``     (cart, item)
``order.created``       (order, cart=None)
``order.paid``          (order, payment=None)
``order.cancelled``     (order, actor=None)
``order.delivered``     (order)
"""

from collections import defaultdict

from django.utils.module_loading import import_string

_registry = defaultdict(list)


def register(name, func=None, order=0):
    """Register ``func`` for hook ``name``. Usable as a decorator."""

    def _decorator(f):
        entries = _registry[name]
        if not any(existing is f for _, existing in entries):
            entries.append((order, f))
            entries.sort(key=lambda e: e[0])
        return f

    if func is not None:
        return _decorator(func)
    return _decorator


def unregister(name, func):
    _registry[name] = [(o, f) for o, f in _registry[name] if f is not func]


def get_hooks(name):
    from .conf import fc_setting

    funcs = [f for _, f in _registry.get(name, [])]
    for path in (fc_setting("HOOKS") or {}).get(name, []):
        funcs.append(import_string(path))
    return funcs


def run(name, *args, **kwargs):
    """Call every hook registered for ``name``; return their results as a list."""
    return [func(*args, **kwargs) for func in get_hooks(name)]


def run_pipeline(name, value, *args, **kwargs):
    """Thread ``value`` through each hook: ``value = hook(*args, value, **kwargs)``."""
    for func in get_hooks(name):
        value = func(*args, value, **kwargs)
    return value
