"""Reusable validators (Nigeria-aware, but permissive for international numbers)."""

import re

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

_PHONE_CLEAN = re.compile(r"[\s\-().]")
_E164 = re.compile(r"^\+?[1-9]\d{7,14}$")


def normalize_phone(value: str, default_country_code: str = "234") -> str:
    """
    Normalise a phone number to E.164 digits without ``+``.
    ``08012345678`` → ``2348012345678``; ``+2348012345678`` → ``2348012345678``.
    """
    phone = _PHONE_CLEAN.sub("", str(value or ""))
    if phone.startswith("+"):
        return phone[1:]
    if phone.startswith("00"):
        return phone[2:]
    if phone.startswith("0") and len(phone) == 11 and default_country_code:
        return default_country_code + phone[1:]
    return phone


def validate_phone(value: str):
    if not value:
        return
    phone = _PHONE_CLEAN.sub("", str(value))
    local_ng = phone.startswith("0") and len(phone) == 11 and phone.isdigit()
    if not (local_ng or _E164.match(phone)):
        raise ValidationError(_("Enter a valid phone number, e.g. 08012345678 or +2348012345678."))
