# Ownership boundary helpers. Syncwave's in-memory source of truth must be exclusively
# owned by Syncwave for every non-reactive value: `ingest` takes ownership of values
# coming in from user code, and `detach` hands out copies so user code can never hold a
# reference into store state. Reactive values are exempt (all their mutations are
# tracked), and so are immutable types (aliasing them is harmless).

from __future__ import annotations

from typing import Any, Final

from pydantic import TypeAdapter

from .reactive import DeadReferenceError, is_reactive

__all__ = []


_IMMUTABLE_TYPES: Final = (int, float, bool, str, bytes, type(None))


def detach(value: Any, ta: TypeAdapter) -> Any:
    if type(value) in _IMMUTABLE_TYPES or is_reactive(value):
        return value
    return ta.validate_json(ta.dump_json(value))


def ingest(value: Any, ta: TypeAdapter) -> Any:
    validated = ta.validate_python(value)
    if type(validated) in _IMMUTABLE_TYPES:
        return validated
    if is_reactive(validated):
        # uninitialized reactive objects are treated as live
        live = validated.__dict__.get("__syncwave_live__", True)
        if not live:
            raise DeadReferenceError(reference=validated)
    return ta.validate_json(ta.dump_json(validated))
