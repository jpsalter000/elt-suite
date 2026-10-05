"""The Power BI project (powerbi/Utilization.pbip) agrees with the dbt reporting contracts.

Power BI Desktop is the only full validator of a PBIP, and it doesn't run in CI.
These checks catch what would break when it opens the project:
- a partition reading a view that doesn't exist, or a column the view lacks;
- DAX, relationships or visuals referring to missing columns or measures;
- project files that don't match their published JSON schemas;
- hand edits that drift from powerbi/generate.py.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
POWERBI = ROOT / "powerbi"
MODEL = POWERBI / "Utilization.SemanticModel" / "definition"
REPORT = POWERBI / "Utilization.Report"
SCHEMAS = POWERBI / "schemas"
SCHEMA_PREFIX = "https://developer.microsoft.com/json-schemas/"


def _generator():
    spec = importlib.util.spec_from_file_location("powerbi_generate", POWERBI / "generate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def manifest(tmp_path_factory):
    """Reporting views and their contracted columns, from a fresh ``dbt parse``."""
    from dbt.cli.main import dbtRunner

    target = tmp_path_factory.mktemp("dbt-target")
    project = str(ROOT / "transform")
    result = dbtRunner().invoke(
        ["parse", "--project-dir", project, "--profiles-dir", project, "--target-path", str(target)]
    )
    assert result.success, result.exception
    nodes = json.loads((target / "manifest.json").read_text(encoding="utf-8"))["nodes"]
    return {
        node["alias"]: {c: col["data_type"] for c, col in node["columns"].items()}
        for node in nodes.values()
        if node["resource_type"] == "model" and node["schema"] == "reporting"
    }


def _tables() -> dict[str, dict]:
    """TMDL tables: name -> {view, columns: {friendly: source}, measures: set}."""
    tables = {}
    for path in (MODEL / "tables").glob("*.tmdl"):
        text = path.read_text(encoding="utf-8")
        name = re.match(r"table '?([^'\n]+?)'?\n", text).group(1)
        columns = dict(
            re.findall(r"\tcolumn '?([^'\n]+?)'?\n(?:\t\t.*\n)*?\t\tsourceColumn: (\S+)", text)
        )
        view = re.search(r'Item = "([^"]+)"', text)
        measures = set(re.findall(r"\tmeasure '?([^'=]+?)'? = ", text))
        tables[name] = {"view": view and view.group(1), "columns": columns, "measures": measures}
    return tables


def test_the_committed_project_matches_the_generator():
    expected = _generator().files()
    committed = {
        str(p.relative_to(POWERBI)).replace("\\", "/"): p.read_text(encoding="utf-8")
        for folder in ("Utilization.SemanticModel", "Utilization.Report")
        for p in (POWERBI / folder).rglob("*")
        if p.is_file() and ".pbi" not in p.parts
    }
    committed["Utilization.pbip"] = (POWERBI / "Utilization.pbip").read_text(encoding="utf-8")
    assert committed == expected, "run `uv run python powerbi/generate.py` and commit the result"


def test_every_table_reads_a_contracted_reporting_view(manifest):
    for name, table in _tables().items():
        if name == "Measures":
            continue
        assert table["view"] in manifest, f"{name} reads reporting.{table['view']}, not a contract"
        missing = set(table["columns"].values()) - set(manifest[table["view"]])
        assert not missing, f"{name}: {sorted(missing)} not in reporting.{table['view']}"


def test_every_dax_reference_resolves():
    tables = _tables()
    measures = tables["Measures"]["measures"]
    text = (MODEL / "tables" / "Measures.tmdl").read_text(encoding="utf-8")
    for expression in re.findall(r"\tmeasure '?[^'=]+?'? = (.+)", text):
        for table, column in re.findall(r"'?([A-Za-z][\w ]*?)'?\[([^\]]+)\]", expression):
            assert column in tables[table]["columns"], f"{table}[{column}] in {expression}"
        for ref in re.findall(r"(?<![\w'])\[([^\]]+)\]", expression):
            assert ref in measures, f"[{ref}] in {expression}"
    assert {"Net Utilization %", "Exempt %", "Project Work %", "Client BD %", "Internal %",
            "Project Profit %", "Contribution Margin %", "Client Rank"} <= measures  # fmt: skip


def test_relationships_join_existing_columns():
    tables = _tables()
    text = (MODEL / "relationships.tmdl").read_text(encoding="utf-8")
    pairs = re.findall(r"(fromColumn|toColumn): '?([^'.]+)'?\.'?([^'\n]+?)'?\n", text)
    assert len(pairs) == 14
    for _, table, column in pairs:
        assert column in tables[table]["columns"], f"{table}.{column}"


def test_the_date_table_is_marked_and_auto_date_tables_are_off():
    date = (MODEL / "tables" / "Date.tmdl").read_text(encoding="utf-8")
    assert "\tdataCategory: Time" in date and "\t\tisKey" in date
    model = (MODEL / "model.tmdl").read_text(encoding="utf-8")
    assert "annotation __PBI_TimeIntelligenceEnabled = 0" in model
    assert "defaultPowerBIDataSourceVersion: powerBI_V3" in model


def test_every_visual_field_exists():
    tables = _tables()
    measures = tables["Measures"]["measures"]
    visuals = list((REPORT / "definition" / "pages").rglob("visual.json"))
    assert len(visuals) >= 10
    for path in visuals:
        query = json.loads(path.read_text(encoding="utf-8"))["visual"]["query"]["queryState"]
        for bucket in query.values():
            for projection in bucket["projections"]:
                ((kind, ref),) = projection["field"].items()
                entity = ref["Expression"]["SourceRef"]["Entity"]
                prop = ref["Property"]
                if kind == "Measure":
                    assert prop in measures, f"{path}: [{prop}]"
                else:
                    assert prop in tables[entity]["columns"], f"{path}: {entity}[{prop}]"


def _registry():
    from referencing import Registry, Resource

    def retrieve(uri: str):
        local = SCHEMAS / uri.split("#")[0].removeprefix(SCHEMA_PREFIX)
        return Resource.from_contents(json.loads(local.read_text(encoding="utf-8")))

    return Registry(retrieve=retrieve)


def test_project_files_match_their_published_schemas():
    from jsonschema import Draft7Validator, validators

    registry = _registry()
    checked = 0
    for path in [*POWERBI.glob("*.pbip"), *REPORT.rglob("*"), *MODEL.parent.glob("*.pb*"),
                 *MODEL.parent.glob(".platform")]:  # fmt: skip
        if not path.is_file() or path.suffix == ".tmdl" or ".pbi" in path.parts:
            continue
        document = json.loads(path.read_text(encoding="utf-8"))
        uri = document["$schema"]
        # Validate through a $ref so the schema's relative references resolve against
        # its published URL, which the registry maps to the vendored copy.
        target = registry.get_or_retrieve(uri).value.contents
        cls = validators.validator_for(target, default=Draft7Validator)
        errors = sorted(cls({"$ref": uri}, registry=registry).iter_errors(document), key=str)
        assert not errors, f"{path.relative_to(POWERBI)}: {errors[0].message}"
        checked += 1
    assert checked >= 15
