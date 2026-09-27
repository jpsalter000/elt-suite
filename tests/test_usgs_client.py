"""The USGS demo client, exercised through the real contract with a mocked HTTP layer."""

from pathlib import Path

import httpx
import pytest

import elt_suite.contract as contract
from elt_suite.config import load_consumer
from elt_suite.contract import iter_records
from elt_suite.inference.executor import infer_schema


def feature(event_id: str, mag, updated_ms: int, felt=None) -> dict:
    return {
        "type": "Feature",
        "id": event_id,
        "properties": {
            "mag": mag,
            "place": "somewhere",
            "time": 1788226151420,
            "updated": updated_ms,
            "felt": felt,
            "tz": None,
            "ids": f",{event_id},",
            "sources": ",us,",
            "types": ",origin,phase-data,",
        },
        "geometry": {"type": "Point", "coordinates": [-178.12, -20.48, 536.09]},
    }


@pytest.fixture
def usgs(monkeypatch):
    monkeypatch.setenv("ELT_HOME", str(Path(__file__).parents[1]))
    consumer = load_consumer("demo_usgs_extract_and_load")
    consumer = consumer.model_copy(update={"extra": {"page_size": 2}})
    pages = {
        1: [feature("a", 5, 1790000000000), feature("b", 4.7, 1790000001000, felt=3)],
        3: [feature("c", 2.5, 1790000002000)],
    }
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        offset = int(request.url.params["offset"])
        return httpx.Response(200, json={"features": pages.get(offset, [])})

    monkeypatch.setattr(
        contract, "make_client", lambda: httpx.Client(transport=httpx.MockTransport(handler))
    )
    return consumer, requests


def test_pages_until_short_page_and_passes_options(usgs):
    consumer, requests = usgs
    job = consumer.job("earthquakes")
    records = list(iter_records(consumer, job))

    assert [r["id"] for r in records] == ["a", "b", "c"]
    assert [int(r.url.params["offset"]) for r in requests] == [1, 3]
    params = requests[0].url.params
    assert params["minmagnitude"] == "2.5"
    assert params["starttime"] == "2026-09-01"
    assert params["updatedafter"] == "2026-09-01T00:00:00.000"


def test_flattens_features(usgs):
    consumer, _ = usgs
    record = next(iter_records(consumer, consumer.job("earthquakes")))
    assert record["updated"] == "2026-09-21T14:13:20.000+00:00"
    assert record["types"] == ["origin", "phase-data"]
    assert (record["longitude"], record["latitude"], record["depth_km"]) == (
        -178.12,
        -20.48,
        536.09,
    )


def test_schema_inference_on_usgs_records(usgs):
    consumer, _ = usgs
    job = consumer.job("earthquakes")
    schema = infer_schema(consumer, job, iter_records(consumer, job))
    f = schema.fields
    assert f["mag"].type == "number"  # 5 (integer) widened with 4.7
    assert (f["felt"].type, f["felt"].nullable) == ("integer", True)
    assert f["tz"].type == "null"
    assert f["updated"].format == "date-time"
    assert f["sources"].describe() == "array<string>"
