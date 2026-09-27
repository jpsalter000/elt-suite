"""Consumer configuration models.

A *consumer* is one company's integration with one source system, e.g.
``acme_netsuite_extract_and_load``. Its config holds everything shared by the
consumer's jobs (credential pointers, base URL, destination) plus the ``jobs``
list describing each dataset to extract.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from elt_suite import paths

CONSUMER_SUFFIX = "_extract_and_load"
IDENTIFIER = r"^[a-z][a-z0-9_]*$"


class ConfigError(Exception):
    """Raised when a consumer or destination config is missing or invalid."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class IncrementalLoading(_Strict):
    incremental_key: str
    lower_bound: str
    datetime_format: str

    @model_validator(mode="after")
    def _lower_bound_matches_format(self) -> IncrementalLoading:
        try:
            datetime.strptime(self.lower_bound, self.datetime_format)
        except ValueError as exc:
            raise ValueError(
                f"lower_bound {self.lower_bound!r} does not match "
                f"datetime_format {self.datetime_format!r}"
            ) from exc
        return self

    def parse(self, value: str) -> datetime:
        return datetime.strptime(value, self.datetime_format)

    def format(self, value: datetime) -> str:
        return value.strftime(self.datetime_format)

    @property
    def lower_bound_dt(self) -> datetime:
        return self.parse(self.lower_bound)


class JobConfig(_Strict):
    name: str = Field(pattern=IDENTIFIER)
    primary_keys: list[str] = Field(min_length=1)
    incremental_loading: IncrementalLoading | None = None
    fetch: str | None = Field(
        default=None,
        description="Override for the client function name (defaults to fetch_<name>).",
    )

    @property
    def fetch_function(self) -> str:
        return self.fetch or f"fetch_{self.name}"


class ConsumerConfig(_Strict):
    company: str = Field(pattern=IDENTIFIER)
    source_system: str = Field(pattern=IDENTIFIER)
    base_url: str
    credentials: dict[str, str] = Field(
        default_factory=dict,
        description="Logical credential name -> environment variable holding the value.",
    )
    destination: str
    target_schema: str = Field(pattern=IDENTIFIER)
    extra: dict[str, Any] = Field(default_factory=dict)
    jobs: list[JobConfig] = Field(min_length=1)

    @field_validator("jobs")
    @classmethod
    def _unique_job_names(cls, jobs: list[JobConfig]) -> list[JobConfig]:
        names = [j.name for j in jobs]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise ValueError(f"duplicate job names: {dupes}")
        return jobs

    @property
    def name(self) -> str:
        return f"{self.company}_{self.source_system}{CONSUMER_SUFFIX}"

    def job(self, name: str) -> JobConfig:
        for job in self.jobs:
            if job.name == name:
                return job
        raise ConfigError(
            f"{self.name} has no job {name!r}; configured: {[j.name for j in self.jobs]}"
        )


class DestinationConfig(_Strict):
    type: Literal["postgres"]
    dsn_env: str


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"invalid JSON in {path}: {exc}") from exc


def load_destinations() -> dict[str, DestinationConfig]:
    raw = _read_json(paths.destinations_file())
    return {name: DestinationConfig.model_validate(cfg) for name, cfg in raw.items()}


def load_destination(name: str) -> DestinationConfig:
    destinations = load_destinations()
    if name not in destinations:
        raise ConfigError(f"unknown destination {name!r}; configured: {sorted(destinations)}")
    return destinations[name]


def list_consumers() -> list[str]:
    return sorted(p.stem for p in paths.consumers_dir().glob(f"*{CONSUMER_SUFFIX}.json"))


def load_consumer(name: str) -> ConsumerConfig:
    path = paths.consumers_dir() / f"{name}.json"
    try:
        consumer = ConsumerConfig.model_validate(_read_json(path))
    except ValueError as exc:  # pydantic.ValidationError subclasses ValueError
        raise ConfigError(f"invalid consumer config {path}:\n{exc}") from exc
    if consumer.name != name:
        raise ConfigError(
            f"{path.name} describes consumer {consumer.name!r}; "
            f"rename the file to {consumer.name}.json"
        )
    load_destination(consumer.destination)
    return consumer
