"""The contract between the generic executors and source-specific clients.

A consumer satisfies its half of the contract by providing, in
``consumers.<source_system>.client``, one function per job::

    def fetch_<job_name>(ctx: JobContext) -> Iterator[dict]: ...

The fetch function owns authentication, pagination and incremental filtering
(using ``ctx.lower_bound``). The executors only consume the iterator, so any
source that yields dict records plugs into schema inference and loading.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from types import ModuleType
from typing import Any, cast

import httpx

from elt_suite.config import ConfigError, ConsumerConfig, JobConfig
from elt_suite.credentials import resolve_credentials
from elt_suite.http import make_client

CLIENTS_PACKAGE = "consumers"

Record = dict[str, Any]


class SourceError(Exception):
    """A source API rejected a request; the message carries the API's own explanation."""


@dataclass
class JobContext:
    consumer: ConsumerConfig
    job: JobConfig
    credentials: dict[str, str]
    http: httpx.Client
    lower_bound: datetime | None = None
    log: logging.Logger = field(default_factory=lambda: logging.getLogger("elt_suite.fetch"))

    @property
    def base_url(self) -> str:
        """Base URL with ``{credential}`` placeholders filled in."""
        return self.consumer.effective_base_url.format(**self.credentials)


FetchFn = Callable[[JobContext], Iterator[Record]]


def client_module(consumer: ConsumerConfig) -> ModuleType:
    module_name = f"{CLIENTS_PACKAGE}.{consumer.source_system}.client"
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name and module_name.startswith(exc.name):
            raise ConfigError(
                f"{consumer.name}: no client module {module_name!r} for "
                f"source_system {consumer.source_system!r}"
            ) from exc
        raise


def resolve_fetch(consumer: ConsumerConfig, job: JobConfig) -> FetchFn:
    module = client_module(consumer)
    fn = getattr(module, job.fetch_function, None)
    if not callable(fn):
        available = sorted(n for n in dir(module) if n.startswith("fetch_"))
        raise ConfigError(
            f"{consumer.name}.{job.name}: {module.__name__} has no {job.fetch_function}(); "
            f"available: {available}"
        )
    return cast(FetchFn, fn)


def build_context(
    consumer: ConsumerConfig, job: JobConfig, lower_bound: datetime | None = None
) -> JobContext:
    """Build the fetch context. ``lower_bound`` defaults to the job's configured bound."""
    inc = job.incremental_loading
    if lower_bound is None and inc:
        lower_bound = inc.lower_bound_dt
    return JobContext(
        consumer=consumer,
        job=job,
        credentials=resolve_credentials(consumer),
        http=make_client(),
        lower_bound=lower_bound,
    )


def iter_records(
    consumer: ConsumerConfig, job: JobConfig, lower_bound: datetime | None = None
) -> Iterator[Record]:
    """Resolve the job's fetch function and stream its records to completion."""
    fetch = resolve_fetch(consumer, job)
    ctx = build_context(consumer, job, lower_bound)
    with ctx.http:
        for record in fetch(ctx):
            if not isinstance(record, dict):
                raise TypeError(
                    f"{job.fetch_function} yielded {type(record).__name__}, expected dict"
                )
            yield record
