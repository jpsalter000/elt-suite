"""Vendor the public JSON schemas the Power BI project files declare, for offline validation.

Every PBIP / PBIR / TMDL-folder JSON file names its schema in ``$schema``
(https://developer.microsoft.com/json-schemas/fabric/...). Those schemas, and every
schema they reference, are published in github.com/microsoft/json-schemas. This
script copies that closure into ``powerbi/schemas/`` so tests can validate the
files without network access.

    uv run python powerbi/vendor_schemas.py   # needs the gh CLI (authenticated)
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from urllib.parse import urldefrag, urljoin

HERE = Path(__file__).parent
SCHEMAS = HERE / "schemas"
PREFIX = "https://developer.microsoft.com/json-schemas/"
REPO = "repos/microsoft/json-schemas/contents/"


def declared_schemas() -> set[str]:
    found = set()
    for path in HERE.rglob("*"):
        suffixes = (".json", ".pbip", ".pbir", ".pbism")
        is_project_file = path.suffix in suffixes or path.name == ".platform"
        if is_project_file and SCHEMAS not in path.parents:
            try:
                schema = json.loads(path.read_text(encoding="utf-8")).get("$schema")
            except (json.JSONDecodeError, AttributeError):
                continue
            if schema and schema.startswith(PREFIX):
                found.add(schema)
    return found


def fetch(url: str) -> str:
    relative = url.removeprefix(PREFIX)
    result = subprocess.run(
        ["gh", "api", "-H", "Accept: application/vnd.github.raw", REPO + relative],
        capture_output=True, text=True, check=True, encoding="utf-8",
    )  # fmt: skip
    return result.stdout


def main() -> None:
    pending, seen = sorted(declared_schemas()), set()
    while pending:
        url = pending.pop()
        if url in seen:
            continue
        seen.add(url)
        text = fetch(url)
        target = SCHEMAS / url.removeprefix(PREFIX)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
        for ref in re.findall(r'"\$ref"\s*:\s*"([^"#][^"]*)"', text):
            pending.append(urldefrag(urljoin(url, ref)).url)
    print(f"vendored {len(seen)} schemas into {SCHEMAS}")


if __name__ == "__main__":
    main()
