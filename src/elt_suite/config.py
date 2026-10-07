"""Consumer configuration models.

A *consumer* is one company's integration with one source system, e.g.
``acme_netsuite_extract_and_load``. Its config holds everything shared by the
consumer's jobs (credential pointers, base URL, destination) plus the ``jobs``
list describing each dataset to extract.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from elt_suite import paths

CONSUMER_SUFFIX = "_extract_and_load"
EPOCH = "epoch"  # datetime_format for unix-second timestamps
IDENTIFIER = r"^[a-z][a-z0-9_]*$"
ENV_VAR = r"^[A-Z_][A-Z0-9_]*$"


class ConfigError(Exception):
    """Raised when a consumer or destination config is missing or invalid."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class IncrementalLoading(_Strict):
    """How a job reads incrementally.

    ``datetime_format`` is a ``strptime`` format, or ``"epoch"`` for unix seconds.
    ``lookback_seconds`` re-reads that much before the saved watermark on each run,
    for sources whose updates become visible after their timestamp (late arrivals).
    """

    incremental_key: str
    lower_bound: str
    datetime_format: str
    lookback_seconds: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _lower_bound_matches_format(self) -> IncrementalLoading:
        try:
            self.parse(self.lower_bound)
        except ValueError as exc:
            raise ValueError(
                f"lower_bound {self.lower_bound!r} does not match "
                f"datetime_format {self.datetime_format!r}"
            ) from exc
        return self

    @property
    def is_epoch(self) -> bool:
        return self.datetime_format == EPOCH

    def parse(self, value: str | int | float) -> datetime:
        if self.is_epoch:
            try:
                return datetime.fromtimestamp(int(value), UTC)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(f"{value!r} is not unix seconds") from exc
        return datetime.strptime(str(value), self.datetime_format)

    def format(self, value: datetime) -> str:
        if self.is_epoch:
            return str(int(value.timestamp()))
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
    options: dict[str, Any] = Field(
        default_factory=dict,
        description="Source-specific settings for this job (e.g. query filters).",
    )

    @property
    def fetch_function(self) -> str:
        return self.fetch or f"fetch_{self.name}"


class ConsumerConfig(_Strict):
    company: str = Field(pattern=IDENTIFIER)
    source_system: str = Field(pattern=IDENTIFIER)
    base_url: str
    base_url_env: str | None = Field(
        default=None,
        pattern=ENV_VAR,
        description="Environment variable that, when set, overrides base_url (e.g. a mock).",
    )
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

    @property
    def effective_base_url(self) -> str:
        """``base_url``, unless ``base_url_env`` names a non-empty environment variable."""
        override = os.environ.get(self.base_url_env) if self.base_url_env else None
        return override or self.base_url

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
