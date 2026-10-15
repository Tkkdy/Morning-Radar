"""Observed community counters; missing data never becomes a measured zero."""
import math


def observed_count(value: object) -> int | None:
    if (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= 0 and int(value) == value):
        return int(value)
    return None


def attention_key(value: object) -> tuple[bool, int]:
    # Conservative policy: known measurements first; unknowns retain the caller's
    # source/time ordering. No synthetic popularity or promotion of unknowns.
    known = observed_count(value)
    return known is None, -known if known is not None else 0
