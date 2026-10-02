"""CSV helpers that neutralise spreadsheet formula injection."""

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value):
    """Prefix text that a spreadsheet would execute as a formula (CWE-1236)."""
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def safe_row(values):
    return [safe_cell(v) for v in values]


def safe_dict(row: dict) -> dict:
    return {k: safe_cell(v) for k, v in row.items()}
