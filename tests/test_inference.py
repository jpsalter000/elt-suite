import json

import pytest

from elt_suite.inference import (
    IncompatibleTypeError,
    InferenceError,
    JobSchema,
    infer_value,
    merge,
    run_inference,
)
from elt_suite.inference.types import SchemaType


def t(value, formats=()):
    return infer_value(value, formats)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "null"),
        (True, "boolean"),
        (3, "integer"),
        (3.5, "number"),
        ("abc", "string"),
        ("2024-01-31", "string(date)"),
        ("2024-01-31T10:00:00+00:00", "string(date-time)"),
        ("2024-01-31 10:00:00", "string(date-time)"),
        ("20240131", "string"),
        ([1, 2], "array<integer>"),
        ([], "array<unknown>"),
        ({"a": 1}, "object"),
    ],
)
def test_infer_value(value, expected):
    assert t(value).describe() == expected


def test_custom_datetime_format():
    assert t("01/31/2024 10:00").format is None
    assert t("01/31/2024 10:00", ["%m/%d/%Y %H:%M"]).format == "date-time"


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        (1, 2.5, "number"),
        ("2024-01-01", "2024-01-01T00:00:00", "string(date-time)"),
        ("2024-01-01", "not a date", "string"),
        ([1], [2.5], "array<number>"),
        ([], ["x"], "array<string>"),
    ],
)
def test_widening(a, b, expected):
    assert merge(t(a), t(b)).describe() == expected
    assert merge(t(b), t(a)).describe() == expected


def test_null_marks_nullable_without_changing_type():
    merged = merge(t(None), t(5))
    assert merged.type == "integer" and merged.nullable


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("abc", ["a", "b", "c"]),
        ("abc", {"a": 1}),
        (True, 1),
        (1, "1"),
        ([1], ["1"]),
    ],
)
def test_incompatible(a, b):
    with pytest.raises(IncompatibleTypeError):
        merge(t(a), t(b))


def test_nested_object_merge_reports_path():
    with pytest.raises(IncompatibleTypeError, match="'address.city'"):
        merge(t({"address": {"city": "x"}}), t({"address": {"city": 1}}), path="")


def test_schema_type_json_roundtrip():
    original = merge(t({"a": [1], "o": {"b": None}, "c": "2024-01-01"}), t({"c": None}))
    assert SchemaType.from_json(json.loads(json.dumps(original.to_json()))) == original


def widget(**overrides):
    return {"id": 1, "name": "w", "updated_at": "2024-02-01 00:00:00", **overrides}


def test_run_inference_writes_schema(fake_consumer, fake_client, elt_home):
    fake_client.extend(
        [
            widget(id=1, price=10, tags=[]),
            widget(id=2, price=10.5, tags=["x"], extra=None),
            widget(id=3, name=None),
        ]
    )
    path = run_inference(fake_consumer, fake_consumer.jobs[0])
    assert path == elt_home / "schemas" / "demo_fake_extract_and_load" / "widgets.json"

    schema = JobSchema.load("demo_fake_extract_and_load", "widgets")
    assert schema.records_scanned == 3
    assert schema.primary_keys == ["id"]
    assert schema.incremental_key == "updated_at"
    f = schema.fields
    assert (f["id"].type, f["id"].nullable) == ("integer", False)
    assert (f["name"].type, f["name"].nullable) == ("string", True)
    assert (f["price"].type, f["price"].nullable) == ("number", True)
    assert f["tags"].describe() == "array<string>"
    assert f["updated_at"].format == "date-time"
    assert f["extra"].type == "null"


def test_run_inference_fails_on_incompatible_types(fake_consumer, fake_client):
    fake_client.extend([widget(id=1, name="abc"), widget(id=2, name=["a", "b", "c"])])
    with pytest.raises(InferenceError, match=r"record #2: incompatible types for field 'name'"):
        run_inference(fake_consumer, fake_consumer.jobs[0])


def test_run_inference_requires_key_fields(fake_consumer, fake_client):
    fake_client.append({"name": "no id", "updated_at": "2024-02-01 00:00:00"})
    with pytest.raises(InferenceError, match=r"never observed: \['id'\]"):
        run_inference(fake_consumer, fake_consumer.jobs[0])


def test_run_inference_rejects_empty_source(fake_consumer):
    with pytest.raises(InferenceError, match="no records"):
        run_inference(fake_consumer, fake_consumer.jobs[0])
