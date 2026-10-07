import pytest

from elt_suite.config import ConfigError, JobConfig
from elt_suite.contract import iter_records, resolve_fetch


def test_resolves_fetch_by_job_name(fake_consumer):
    fn = resolve_fetch(fake_consumer, fake_consumer.job("widgets"))
    assert fn.__name__ == "fetch_widgets"


def test_missing_fetch_function_lists_available(fake_consumer):
    job = JobConfig(name="gadgets", primary_keys=["id"])
    with pytest.raises(ConfigError, match=r"no fetch_gadgets\(\).*fetch_widgets"):
        resolve_fetch(fake_consumer, job)


def test_unknown_source_system(fake_consumer):
    consumer = fake_consumer.model_copy(update={"source_system": "nope"})
    with pytest.raises(ConfigError, match="no client module"):
        resolve_fetch(consumer, consumer.jobs[0])


def test_missing_credentials(fake_consumer, monkeypatch):
    monkeypatch.delenv("DEMO_FAKE_API_KEY")
    with pytest.raises(ConfigError, match="DEMO_FAKE_API_KEY"):
        list(iter_records(fake_consumer, fake_consumer.jobs[0]))


def test_incremental_lower_bound_passed_to_client(fake_consumer, fake_client):
    fake_client.extend(
        [
            {"id": 1, "updated_at": "2023-12-31 23:59:59"},
            {"id": 2, "updated_at": "2024-01-01 00:00:00"},
        ]
    )
    assert [r["id"] for r in iter_records(fake_consumer, fake_consumer.jobs[0])] == [2]


def test_real_clients_satisfy_contract(monkeypatch):
    from pathlib import Path

    from elt_suite.config import list_consumers, load_consumer

    monkeypatch.setenv("ELT_HOME", str(Path(__file__).parents[1]))
    for name in list_consumers():
        consumer = load_consumer(name)
        for job in consumer.jobs:
            assert callable(resolve_fetch(consumer, job))


# --- base URL overrides -----------------------------------------------------------------


def _with_base_url_env(consumer, var="DEMO_FAKE_BASE_URL"):
    return consumer.model_copy(update={"base_url_env": var})


def test_base_url_env_overrides_the_configured_base_url(fake_consumer, monkeypatch):
    from elt_suite.contract import build_context

    monkeypatch.setenv("DEMO_FAKE_BASE_URL", "http://mock-fake:9000")
    consumer = _with_base_url_env(fake_consumer)
    assert consumer.effective_base_url == "http://mock-fake:9000"
    assert build_context(consumer, consumer.jobs[0]).base_url == "http://mock-fake:9000"


def test_base_url_env_falls_back_to_base_url_when_unset_or_empty(fake_consumer, monkeypatch):
    consumer = _with_base_url_env(fake_consumer)
    monkeypatch.delenv("DEMO_FAKE_BASE_URL", raising=False)
    assert consumer.effective_base_url == "https://fake.invalid"
    monkeypatch.setenv("DEMO_FAKE_BASE_URL", "")
    assert consumer.effective_base_url == "https://fake.invalid"


def test_without_base_url_env_the_base_url_is_used(fake_consumer):
    assert fake_consumer.base_url_env is None
    assert fake_consumer.effective_base_url == "https://fake.invalid"
