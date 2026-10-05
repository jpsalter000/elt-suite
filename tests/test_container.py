"""Smoke tests for the built Docker image.

Run after ``docker build -t elt-suite:test .`` with ``ELT_IMAGE=elt-suite:test``;
skipped otherwise (CI builds the image and sets it).
"""

import os
import shutil
import subprocess

import pytest

IMAGE = os.environ.get("ELT_IMAGE")

pytestmark = [
    pytest.mark.container,
    pytest.mark.skipif(not IMAGE or not shutil.which("docker"), reason="ELT_IMAGE not built"),
]


def docker_run(*args: str, entrypoint: str | None = None) -> subprocess.CompletedProcess:
    cmd = ["docker", "run", "--rm", "--read-only", "--tmpfs", "/tmp"]
    if entrypoint:
        cmd += ["--entrypoint", entrypoint]
    return subprocess.run([*cmd, IMAGE, *args], capture_output=True, text=True, timeout=120)


def test_default_command_lists_every_consumer():
    result = docker_run()
    assert result.returncode == 0, result.stderr
    for consumer in [
        "abc_salesforce_extract_and_load",
        "acme_netsuite_extract_and_load",
        "demo_usgs_extract_and_load",
        "globex_shopfront_extract_and_load",
        "initech_ticketdesk_extract_and_load",
    ]:
        assert consumer in result.stdout


def test_runs_as_non_root_user():
    result = docker_run("-u", entrypoint="id")
    assert result.stdout.strip() == "10001"


def test_committed_schemas_ship_with_the_image():
    result = docker_run("/app/schemas", entrypoint="ls")
    assert "demo_usgs_extract_and_load" in result.stdout.split()


def test_dev_and_mock_dependencies_are_not_installed():
    code = (
        "import importlib.util as u; "
        "print([m for m in ('pytest', 'fastapi', 'ruff') if u.find_spec(m)])"
    )
    result = docker_run("-c", code, entrypoint="python")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"


def test_tests_and_infra_are_not_copied_in():
    result = docker_run("/app", entrypoint="ls")
    contents = result.stdout.split()
    assert "config" in contents and "schemas" in contents
    assert not {"tests", "infra", ".git", ".env", "src"} & set(contents)


def test_missing_warehouse_is_reported_clearly():
    result = docker_run("run", "demo_usgs_extract_and_load", "--job", "earthquakes")
    assert result.returncode == 1
    assert "WAREHOUSE_DSN is not set" in result.stderr


# --- pipelines and dbt -------------------------------------------------------------------


def test_pipelines_and_the_dbt_project_ship_with_the_image():
    result = docker_run("pipeline", "list")
    assert result.returncode == 0, result.stderr
    assert "utilization_daily" in result.stdout
    shown = docker_run("pipeline", "show", "utilization_daily", "--json")
    assert '"version": 1' in shown.stdout
    files = docker_run("/app/transform", entrypoint="ls").stdout.split()
    assert {"dbt_project.yml", "profiles.yml", "models", "seeds"} <= set(files)
    assert not {"target", "logs"} & set(files)


def test_dbt_parses_the_project_on_a_read_only_filesystem():
    result = docker_run(
        "-c",
        "cd /app/transform && DBT_TARGET_PATH=/tmp/target DBT_LOG_PATH=/tmp/logs "
        "DBT_SEND_ANONYMOUS_USAGE_STATS=false dbt parse --profiles-dir .",
        entrypoint="sh",
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_dbt_task_without_a_warehouse_fails_with_the_reason():
    result = docker_run("pipeline", "run-task", "utilization_daily", "transform")
    assert result.returncode == 1
    assert "WAREHOUSE_DSN is not set" in result.stdout + result.stderr


MOCKS_IMAGE = os.environ.get("ELT_MOCKS_IMAGE")


@pytest.mark.skipif(not MOCKS_IMAGE, reason="ELT_MOCKS_IMAGE not built")
def test_the_mocks_image_serves_the_mock_apis_as_non_root():
    cmd = ["docker", "run", "--rm", "--read-only", "--tmpfs", "/tmp", MOCKS_IMAGE, "--help"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    assert "netsuite" in result.stdout
    user = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "id", MOCKS_IMAGE, "-u"],
        capture_output=True, text=True, timeout=120,
    )  # fmt: skip
    assert user.stdout.strip() == "10001"
