from __future__ import annotations

_THOUSAND = 1_000
_SIGNIFICANT_DIGITS = 3
_SUFFIXES = ("", "k", "M", "B", "T")


def format_compact_count(value: int) -> str:
    if value < _THOUSAND:
        return str(value)
    step = 10 ** (len(str(value)) - _SIGNIFICANT_DIGITS)
    rounded = (value + step // 2) // step * step
    group = min((len(str(rounded)) - 1) // 3, len(_SUFFIXES) - 1)
    unit = _THOUSAND**group
    whole, fraction = divmod(rounded, unit)
    decimals = f"{fraction * _THOUSAND // unit:03d}".rstrip("0")
    number = f"{whole}.{decimals}" if decimals else str(whole)
    return f"{number}{_SUFFIXES[group]}"


def format_count_markdown(value: int) -> str:
    compact = format_compact_count(value)
    if value < _THOUSAND:
        return compact
    return f"{compact} _({value:,})_"
