"""USGS Earthquake Catalog client: a public, no-credentials demo source.

Queries the FDSN event service (https://earthquake.usgs.gov/fdsnws/event/1/)
for GeoJSON events. Paging uses 1-based ``offset``/``limit`` until a short page
is returned. Incremental loading maps the job's lower bound to ``updatedafter``,
so revised events (magnitude updates, reviews) are picked up on later runs.

Job ``options`` are passed straight through as query parameters, e.g.
``starttime`` (event-time floor; USGS defaults to the last 30 days without it),
``minmagnitude`` or ``minsig``.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from elt_suite.contract import JobContext, Record

LIST_FIELDS = ("ids", "sources", "types")  # USGS encodes these as ",a,b,"


def _timestamp(ms: int | None) -> str | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat(timespec="milliseconds")


def _utc_param(value: datetime) -> str:
    if value.tzinfo is not None:
        value = value.astimezone(UTC).replace(tzinfo=None)
    return value.isoformat(timespec="milliseconds")


def to_record(feature: dict[str, Any]) -> Record:
    """Flatten a GeoJSON feature into a single record."""
    props = dict(feature["properties"])
    for key in LIST_FIELDS:
        if props.get(key) is not None:
            props[key] = [part for part in props[key].split(",") if part]
    props["time"] = _timestamp(props.get("time"))
    props["updated"] = _timestamp(props.get("updated"))
    longitude, latitude, depth_km = (feature.get("geometry") or {}).get(
        "coordinates", [None, None, None]
    )
    return {
        "id": feature["id"],
        **props,
        "longitude": longitude,
        "latitude": latitude,
        "depth_km": depth_km,
    }


def query_events(ctx: JobContext) -> Iterator[Record]:
    limit = ctx.consumer.extra.get("page_size", 500)
    params: dict[str, Any] = {"format": "geojson", "orderby": "time-asc", **ctx.job.options}
    if ctx.lower_bound:
        params["updatedafter"] = _utc_param(ctx.lower_bound)

    offset = 1
    while True:
        resp = ctx.http.get(
            f"{ctx.base_url}/query", params={**params, "limit": limit, "offset": offset}
        )
        resp.raise_for_status()
        features = resp.json()["features"]
        for feature in features:
            yield to_record(feature)
        if len(features) < limit:
            return
        offset += limit


def fetch_earthquakes(ctx: JobContext) -> Iterator[Record]:
    return query_events(ctx)


def fetch_significant_earthquakes(ctx: JobContext) -> Iterator[Record]:
    return query_events(ctx)
