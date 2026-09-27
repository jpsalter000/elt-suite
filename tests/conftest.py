"""Shared fixtures: an isolated ELT_HOME and a fake in-memory source client.

The fake client is registered as ``consumers.fake.client`` so it is resolved
through exactly the same contract as the real Salesforce/NetSuite clients.
"""

from __future__ import annotations

import json
import sys
import types
from collections.abc import Iterator

import pytest

from elt_suite.config import load_consumer

FAKE_RECORDS: list[dict] = []


def fetch_widgets(ctx) -> Iterator[dict]:
    bound = ctx.lower_bound
    inc = ctx.job.incremental_loading
    for record in FAKE_RECORDS:
        if bound and inc.parse(record[inc.incremental_key]) < bound:
            continue
        yield dict(record)


@pytest.fixture(autouse=True)
def fake_client(monkeypatch):
    package = types.ModuleType("consumers.fake")
    client = types.ModuleType("consumers.fake.client")
    client.fetch_widgets = fetch_widgets
    package.client = client
    monkeypatch.setitem(sys.modules, "consumers.fake", package)
    monkeypatch.setitem(sys.modules, "consumers.fake.client", client)
    FAKE_RECORDS.clear()
    yield FAKE_RECORDS
    FAKE_RECORDS.clear()


FAKE_CONFIG = {
    "company": "demo",
    "source_system": "fake",
    "base_url": "https://fake.invalid",
    "credentials": {"api_key": "DEMO_FAKE_API_KEY"},
    "destination": "warehouse",
    "target_schema": "demo_fake",
    "jobs": [
        {
            "name": "widgets",
            "primary_keys": ["id"],
            "incremental_loading": {
                "incremental_key": "updated_at",
                "lower_bound": "2024-01-01 00:00:00",
                "datetime_format": "%Y-%m-%d %H:%M:%S",
            },
        }
    ],
}


@pytest.fixture
def elt_home(tmp_path, monkeypatch):
    (tmp_path / "config" / "consumers").mkdir(parents=True)
    (tmp_path / "config" / "destinations.json").write_text(
        json.dumps({"warehouse": {"type": "postgres", "dsn_env": "WAREHOUSE_DSN"}})
    )
    monkeypatch.setenv("ELT_HOME", str(tmp_path))
    monkeypatch.setenv("DEMO_FAKE_API_KEY", "not-a-secret")
    return tmp_path


def write_consumer(home, config: dict) -> str:
    name = f"{config['company']}_{config['source_system']}_extract_and_load"
    (home / "config" / "consumers" / f"{name}.json").write_text(json.dumps(config))
    return name


@pytest.fixture
def fake_consumer(elt_home):
    return load_consumer(write_consumer(elt_home, FAKE_CONFIG))
