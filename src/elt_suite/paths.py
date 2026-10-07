"""Filesystem locations for configs and generated schemas.

Everything is resolved relative to ``ELT_HOME`` (defaults to the current working
directory), so the CLI can be run from the repo root or pointed elsewhere.
"""

import os
from pathlib import Path


def home() -> Path:
    return Path(os.environ.get("ELT_HOME", Path.cwd()))


def config_dir() -> Path:
    return home() / "config"


def consumers_dir() -> Path:
    return config_dir() / "consumers"


def pipelines_dir() -> Path:
    return config_dir() / "pipelines"


def dbt_project_dir() -> Path:
    return home() / "transform"


def destinations_file() -> Path:
    return config_dir() / "destinations.json"


def schemas_dir() -> Path:
    return home() / "schemas"


def schema_file(consumer: str, job: str) -> Path:
    return schemas_dir() / consumer / f"{job}.json"
