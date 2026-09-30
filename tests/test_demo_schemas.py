"""The committed schemas for the mock-API consumers must match what inference produces.

This keeps ``schemas/`` honest: if the mock data or a client changes shape, this
fails until ``elt infer`` is re-run and the new schema is committed.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import elt_suite.contract as contract
from elt_suite import paths
from elt_suite.config import load_consumer
from elt_suite.contract import iter_records
from elt_suite.inference.executor import infer_schema
from mock_apis import cartwheel, shopfront, ticketdesk

ROOT = Path(__file__).parents[1]
CASES = [
    ("globex_shopfront_extract_and_load", shopfront.create_app, "customers"),
    ("globex_shopfront_extract_and_load", shopfront.create_app, "orders"),
    ("initech_ticketdesk_extract_and_load", ticketdesk.create_app, "tickets"),
    ("initech_ticketdesk_extract_and_load", ticketdesk.create_app, "agents"),
    ("umbrella_cartwheel_extract_and_load", cartwheel.create_app, "customers"),
    ("umbrella_cartwheel_extract_and_load", cartwheel.create_app, "orders"),
    ("umbrella_cartwheel_extract_and_load", cartwheel.create_app, "order_items"),
]


@pytest.mark.parametrize(("name", "create_app", "job"), CASES)
def test_committed_schema_matches_inference(monkeypatch, name, create_app, job):
    monkeypatch.setenv("ELT_HOME", str(ROOT))
    monkeypatch.setenv("GLOBEX_SHOPFRONT_CLIENT_ID", shopfront.DEMO_CLIENT_ID)
    monkeypatch.setenv("GLOBEX_SHOPFRONT_CLIENT_SECRET", shopfront.DEMO_CLIENT_SECRET)
    monkeypatch.setenv("INITECH_TICKETDESK_API_KEY", ticketdesk.DEMO_API_KEY)
    monkeypatch.setenv("UMBRELLA_CARTWHEEL_USERNAME", cartwheel.DEMO_USERNAME)
    monkeypatch.setenv("UMBRELLA_CARTWHEEL_PASSWORD", cartwheel.DEMO_PASSWORD)
    app = create_app()
    monkeypatch.setattr(contract, "make_client", lambda: TestClient(app))

    consumer = load_consumer(name)
    inferred = infer_schema(consumer, consumer.job(job), iter_records(consumer, consumer.job(job)))
    committed = json.loads(paths.schema_file(name, job).read_text(encoding="utf-8"))

    actual = inferred.to_json()
    for volatile in ("generated_at",):
        actual.pop(volatile)
        committed.pop(volatile)
    assert actual == committed, f"re-run `elt infer {name} --job {job}` and commit the result"
