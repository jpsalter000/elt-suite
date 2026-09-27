"""Type lattice used for schema inference.

Every observed value maps to a :class:`SchemaType`. Types observed for the same
field are combined with :func:`merge`, which widens where a broader type can
represent both (``integer`` + ``number`` -> ``number``, ``date`` + ``date-time``
-> ``date-time``, formatted string + plain string -> ``string``) and raises
:class:`IncompatibleTypeError` otherwise (e.g. ``'abc'`` vs ``['a', 'b', 'c']``).
``null`` never conflicts; it only marks the field nullable.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Any

NULL = "null"
BOOLEAN = "boolean"
INTEGER = "integer"
NUMBER = "number"
STRING = "string"
OBJECT = "object"
ARRAY = "array"
DATE = "date"
DATE_TIME = "date-time"

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO_DATE_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")


class IncompatibleTypeError(TypeError):
    def __init__(self, path: str, left: SchemaType, right: SchemaType) -> None:
        self.path, self.left, self.right = path, left, right
        name = path or "<root>"
        super().__init__(
            f"incompatible types for field {name!r}: {left.describe()} vs {right.describe()}"
        )


@dataclass(frozen=True)
class SchemaType:
    type: str
    nullable: bool = False
    format: str | None = None
    properties: dict[str, SchemaType] = field(default_factory=dict)
    items: SchemaType | None = None  # None for arrays only ever seen empty

    def describe(self) -> str:
        if self.type == ARRAY:
            return f"array<{self.items.describe() if self.items else 'unknown'}>"
        if self.format:
            return f"{self.type}({self.format})"
        return self.type

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"type": self.type, "nullable": self.nullable}
        if self.format:
            out["format"] = self.format
        if self.type == OBJECT:
            out["properties"] = {k: v.to_json() for k, v in self.properties.items()}
        if self.type == ARRAY:
            out["items"] = self.items.to_json() if self.items else None
        return out

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> SchemaType:
        return cls(
            type=data["type"],
            nullable=data.get("nullable", False),
            format=data.get("format"),
            properties={k: cls.from_json(v) for k, v in data.get("properties", {}).items()},
            items=cls.from_json(data["items"]) if data.get("items") else None,
        )


def _string_format(value: str, datetime_formats: tuple[str, ...]) -> str | None:
    if _ISO_DATE.match(value):
        try:
            date.fromisoformat(value)
            return DATE
        except ValueError:
            pass
    if _ISO_DATE_TIME.match(value):
        try:
            datetime.fromisoformat(value)
            return DATE_TIME
        except ValueError:
            pass
    for fmt in datetime_formats:
        try:
            datetime.strptime(value, fmt)
            return DATE_TIME
        except ValueError:
            continue
    return None


def infer_value(value: Any, datetime_formats: Iterable[str] = ()) -> SchemaType:
    """Infer the type of a single (JSON-like) value.

    ``datetime_formats`` are extra ``strptime`` formats (e.g. the job's incremental
    ``datetime_format``) that mark a string as ``date-time`` beyond plain ISO 8601.
    """
    formats = tuple(datetime_formats)
    if value is None:
        return SchemaType(NULL, nullable=True)
    if isinstance(value, bool):  # before int: bool subclasses int
        return SchemaType(BOOLEAN)
    if isinstance(value, int):
        return SchemaType(INTEGER)
    if isinstance(value, float):
        return SchemaType(NUMBER)
    if isinstance(value, datetime):  # before date: datetime subclasses date
        return SchemaType(STRING, format=DATE_TIME)
    if isinstance(value, date):
        return SchemaType(STRING, format=DATE)
    if isinstance(value, str):
        return SchemaType(STRING, format=_string_format(value, formats))
    if isinstance(value, dict):
        return SchemaType(
            OBJECT, properties={str(k): infer_value(v, formats) for k, v in value.items()}
        )
    if isinstance(value, list | tuple):
        items: SchemaType | None = None
        for item in value:
            t = infer_value(item, formats)
            items = t if items is None else merge(items, t, path="[]")
        return SchemaType(ARRAY, items=items)
    raise TypeError(f"unsupported value type {type(value).__name__}: {value!r}")


def _merge_formats(a: str | None, b: str | None) -> str | None:
    if a == b:
        return a
    if {a, b} == {DATE, DATE_TIME}:
        return DATE_TIME
    return None  # a formatted string mixed with free text is just a string


def merge(a: SchemaType, b: SchemaType, path: str = "") -> SchemaType:
    """Combine two observed types into the narrowest type that holds both."""
    nullable = a.nullable or b.nullable
    if a.type == NULL:
        return replace(b, nullable=True)
    if b.type == NULL:
        return replace(a, nullable=True)

    if a.type == b.type:
        if a.type == STRING:
            return SchemaType(STRING, nullable, _merge_formats(a.format, b.format))
        if a.type == OBJECT:
            props = merge_properties(a.properties, b.properties, path)
            return SchemaType(OBJECT, nullable, properties=props)
        if a.type == ARRAY:
            if a.items is None or b.items is None:
                items = a.items or b.items
            else:
                items = merge(a.items, b.items, f"{path}[]")
            return SchemaType(ARRAY, nullable, items=items)
        return SchemaType(a.type, nullable)

    if {a.type, b.type} == {INTEGER, NUMBER}:
        return SchemaType(NUMBER, nullable)

    raise IncompatibleTypeError(path, a, b)


def merge_properties(
    a: dict[str, SchemaType], b: dict[str, SchemaType], path: str = ""
) -> dict[str, SchemaType]:
    """Merge object properties; keys absent from either side become nullable."""
    merged: dict[str, SchemaType] = {}
    for key in [*a, *(k for k in b if k not in a)]:  # first-seen order
        child = f"{path}.{key}" if path else key
        if key not in b:
            merged[key] = replace(a[key], nullable=True)
        elif key not in a:
            merged[key] = replace(b[key], nullable=True)
        else:
            merged[key] = merge(a[key], b[key], child)
    return merged
