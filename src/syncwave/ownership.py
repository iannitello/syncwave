from __future__ import annotations

from typing import Any, Final

from pydantic import TypeAdapter

from .reactive import is_reactive

__all__ = []


_IMMUTABLE_TYPES: Final = (int, float, bool, str, bytes, type(None))


def detach(value: Any, ta: TypeAdapter) -> Any:
    if type(value) in _IMMUTABLE_TYPES or is_reactive(value):
        return value
    return ta.validate_json(ta.dump_json(value))


def ingest(value: Any, ta: TypeAdapter) -> Any:
    # Implicitly guards against dead references: all reactive values run `dead_guard`.
    validated = ta.validate_python(value)

    # Must run before the immutable fast path: a type inconsistent with its validator,
    # e.g. `Annotated[int, AfterValidator(str)]`, yields an immutable `str`.
    json = ta.dump_json(validated, warnings="error")

    if type(validated) in _IMMUTABLE_TYPES:
        return validated
    return ta.validate_json(json)
