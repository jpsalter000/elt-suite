"""Resolve credential pointers (environment variable names) into values."""

import os

from elt_suite.config import ConfigError, ConsumerConfig


def resolve_credentials(consumer: ConsumerConfig) -> dict[str, str]:
    missing = [env for env in consumer.credentials.values() if not os.environ.get(env)]
    if missing:
        raise ConfigError(f"{consumer.name}: missing environment variables {missing}")
    return {key: os.environ[env] for key, env in consumer.credentials.items()}
