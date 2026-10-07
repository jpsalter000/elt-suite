"""Read the task graphs written by ``elt pipeline export``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SUPPORTED_VERSION = 1


class GraphError(Exception):
    """A pipeline graph or the adapter's settings are missing or invalid."""


def _validate(graph: dict[str, Any], source: Path) -> dict[str, Any]:
    version = graph.get("version")
    if version != SUPPORTED_VERSION:
        raise GraphError(
            f"{source}: graph version {version} is not supported; this adapter supports "
            f"version {SUPPORTED_VERSION}. Re-export with a matching elt-suite or upgrade "
            "elt-airflow."
        )
    for key in ("pipeline", "tasks"):
        if key not in graph:
            raise GraphError(f"{source}: missing {key!r}")
    ids = {task["id"] for task in graph["tasks"]}
    for task in graph["tasks"]:
        unknown = [d for d in task.get("depends_on", []) if d not in ids]
        if unknown:
            raise GraphError(
                f"{source}: task {task['id']!r} depends on {unknown}, which are not in the graph"
            )
        if not task.get("argv"):
            raise GraphError(f"{source}: task {task['id']!r} has no argv to run")
    return graph


def load_graphs(directory: str | Path) -> list[dict[str, Any]]:
    """Every ``<pipeline>.json`` graph in ``directory``, sorted by pipeline name."""
    folder = Path(directory)
    if not folder.is_dir():
        raise GraphError(
            f"no pipeline graphs at {folder}; write them with `elt pipeline export {folder}` "
            "(the Airflow image does this at build time) or set ELT_GRAPH_DIR"
        )
    graphs = []
    for path in sorted(folder.glob("*.json")):
        try:
            graph = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise GraphError(f"{path}: invalid JSON: {exc}") from exc
        graphs.append(_validate(graph, path))
    return graphs
