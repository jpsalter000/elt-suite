import copy

import pytest

from conftest import FAKE_CONFIG, write_consumer
from elt_suite.config import ConfigError, list_consumers, load_consumer


def test_loads_consumer_and_derives_name(fake_consumer):
    assert fake_consumer.name == "demo_fake_extract_and_load"
    job = fake_consumer.job("widgets")
    assert job.fetch_function == "fetch_widgets"
    assert job.incremental_loading.lower_bound_dt.year == 2024


def test_list_consumers(fake_consumer):
    assert list_consumers() == ["demo_fake_extract_and_load"]


def test_file_name_must_match_consumer_name(elt_home):
    write_consumer(elt_home, FAKE_CONFIG)
    src = elt_home / "config" / "consumers" / "demo_fake_extract_and_load.json"
    src.rename(src.with_name("wrong_extract_and_load.json"))
    with pytest.raises(ConfigError, match="rename the file"):
        load_consumer("wrong_extract_and_load")


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda c: c["jobs"].append(copy.deepcopy(c["jobs"][0])), "duplicate job names"),
        (lambda c: c["jobs"][0].update(primary_keys=[]), "primary_keys"),
        (
            lambda c: c["jobs"][0]["incremental_loading"].update(lower_bound="01/01/2024"),
            "does not match datetime_format",
        ),
        (lambda c: c.update(unexpected=True), "unexpected"),
        (lambda c: c.update(base_url_env="lower-case"), "base_url_env"),
    ],
)
def test_invalid_configs(elt_home, mutate, message):
    config = copy.deepcopy(FAKE_CONFIG)
    mutate(config)
    name = write_consumer(elt_home, config)
    with pytest.raises(ConfigError, match=message):
        load_consumer(name)


def test_unknown_destination(elt_home):
    config = {**copy.deepcopy(FAKE_CONFIG), "destination": "nowhere"}
    with pytest.raises(ConfigError, match="unknown destination"):
        load_consumer(write_consumer(elt_home, config))


def test_repo_configs_are_valid(monkeypatch):
    from pathlib import Path

    monkeypatch.setenv("ELT_HOME", str(Path(__file__).parents[1]))
    names = list_consumers()
    assert "abc_salesforce_extract_and_load" in names
    assert "acme_netsuite_extract_and_load" in names
    for name in names:
        load_consumer(name)


# --- incremental loading options ------------------------------------------------------


def _incremental(**overrides):
    from elt_suite.config import IncrementalLoading

    fields = {
        "incremental_key": "modified",
        "lower_bound": "1767225600",
        "datetime_format": "epoch",
        **overrides,
    }
    return IncrementalLoading(**fields)


def test_epoch_datetime_format_round_trips_unix_seconds():
    from datetime import UTC, datetime

    inc = _incremental()
    assert inc.lower_bound_dt == datetime(2026, 1, 1, tzinfo=UTC)
    assert inc.format(datetime(2026, 1, 1, tzinfo=UTC)) == "1767225600"
    assert inc.parse(1767225600) == inc.parse("1767225600") == inc.lower_bound_dt


def test_epoch_lower_bound_must_be_unix_seconds():
    with pytest.raises(ValueError, match="does not match datetime_format 'epoch'"):
        _incremental(lower_bound="2026-01-01")


def test_lookback_defaults_to_zero_and_rejects_negatives():
    assert _incremental().lookback_seconds == 0
    with pytest.raises(ValueError, match="lookback_seconds"):
        _incremental(lookback_seconds=-1)
