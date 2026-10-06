"""Generate the Power BI project (PBIP: TMDL semantic model + PBIR report).

The semantic model is derived from the dbt reporting contracts
(transform/models/reporting/_reporting.yml), so every Power BI column exists, with
its type, in a view Power BI can read. Measures port the workbook's DAX to the
modernized definitions in the dbt models.

    uv run python powerbi/generate.py   # rewrites powerbi/Utilization.*

tests/test_powerbi_model.py fails if the committed files differ from this output.
"""

from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).parents[1]
HERE = Path(__file__).parent
CONTRACTS = ROOT / "transform" / "models" / "reporting" / "_reporting.yml"
NAME = "Utilization"
SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/"
NAMESPACE = uuid.UUID("6f1c2f0e-3a59-4d43-9e3c-5d3f0b7c1a11")

# Power BI table -> (reporting view, dbt model holding its contract)
TABLES = {
    "Date": ("dim_date", "reporting_dim_date"),
    "Employee": ("dim_employee", "reporting_dim_employee"),
    "Client": ("dim_client", "reporting_dim_client"),
    "Project": ("dim_project", "reporting_dim_project"),
    "Item": ("dim_item", "reporting_dim_item"),
    "Time Entries": ("fct_time_entries", "reporting_fct_time_entries"),
    "Employee Capacity": ("fct_employee_capacity", "reporting_fct_employee_capacity"),
    "Project Margin": ("rpt_project_margin", "rpt_project_margin"),
}
FACTS = {"Time Entries", "Employee Capacity"}
ACRONYMS = {"id": "ID", "bd": "BD", "pct": "%"}

# (from table, from column, to table, to column)
RELATIONSHIPS = [
    ("Time Entries", "entry_date", "Date", "date_day"),
    ("Time Entries", "employee_id", "Employee", "employee_id"),
    ("Time Entries", "client_id", "Client", "client_id"),
    ("Time Entries", "project_id", "Project", "project_id"),
    ("Time Entries", "item_id", "Item", "item_id"),
    ("Employee Capacity", "date_day", "Date", "date_day"),
    ("Employee Capacity", "employee_id", "Employee", "employee_id"),
]


@dataclass(frozen=True)
class Measure:
    name: str
    dax: str
    fmt: str
    folder: str
    description: str


MEASURES = [
    Measure("Total Hours", "SUM('Time Entries'[Hours])", "#,0.0", "Hours",
            "All logged hours, including exempt and unmapped time."),
    Measure("Worked Hours", "SUM('Time Entries'[Worked Hours])", "#,0.0", "Hours",
            "Hours in categories that count as worked (everything but exempt and unmapped)."),
    Measure("Client Hours", "SUM('Time Entries'[Client Facing Hours])", "#,0.0", "Hours",
            "Project work plus client business development."),
    Measure("Exempt Hours", "SUM('Time Entries'[Exempt Hours])", "#,0.0", "Hours",
            "Holidays, PTO and other leave."),
    Measure("Expected Hours", "SUM('Employee Capacity'[Expected Hours])", "#,0.0", "Hours",
            "Weekday standard hours minus exempt time, never below zero, while employed."),
    Measure("Standard Hours", "SUM('Employee Capacity'[Standard Hours])", "#,0.0", "Hours",
            "Weekday standard hours while employed."),
    Measure("Net Utilization %", "DIVIDE([Worked Hours], [Expected Hours])", "0.0%",
            "Utilization", "Worked over expected hours (the workbook's Actual over Expected)."),
    Measure("Project Work %", "DIVIDE(SUM('Time Entries'[Project Work Hours]), [Expected Hours])",
            "0.0%", "Utilization", "Project work over expected hours."),
    Measure("Client BD %", "DIVIDE(SUM('Time Entries'[Client BD Hours]), [Expected Hours])",
            "0.0%", "Utilization", "Client business development over expected hours."),
    Measure("Internal %",
            "DIVIDE(SUM('Time Entries'[Internal Productive Hours]) "
            "+ SUM('Time Entries'[Internal Admin Hours]), [Expected Hours])",
            "0.0%", "Utilization", "Internal productive and admin time over expected hours."),
    Measure("Exempt %", "DIVIDE([Exempt Hours], [Standard Hours])", "0.0%", "Utilization",
            "Exempt time over standard hours."),
    Measure("Client Rank",
            "IF(ISINSCOPE(Client[Client Name]), "
            "RANKX(ALLSELECTED(Client[Client Name]), [Client Hours]))",
            "0", "Clients", "Rank by client hours in the current filters (was the Top-15 macro)."),
    Measure("Price", "SUM('Project Margin'[Price])", "\\$#,0", "Projects",
            "Invoices and credit memos for finalized or closed projects, otherwise sales orders."),
    Measure("Net Revenue", "SUM('Project Margin'[Net Revenue])", "\\$#,0", "Projects",
            "Price minus pass-through travel and incentive revenue."),
    Measure("Project Profit", "SUM('Project Margin'[Project Profit])", "\\$#,0", "Projects",
            "Price minus field, incentive and travel costs and time cost."),
    Measure("Project Profit %", "DIVIDE([Project Profit], [Net Revenue])", "0.0%", "Projects",
            "Profit over net revenue."),
    Measure("Contribution Margin", "SUM('Project Margin'[Contribution Margin])", "\\$#,0",
            "Projects", "Profit using the burdened (CB) cost rate."),
    Measure("Contribution Margin %", "DIVIDE([Contribution Margin], [Net Revenue])", "0.0%",
            "Projects", "Contribution margin over net revenue."),
]  # fmt: skip


def tag(*parts: str) -> str:
    return str(uuid.uuid5(NAMESPACE, "/".join(parts)))


def friendly(column: str) -> str:
    words = column.split("_")
    if words[-1] == "day" and words[0] == "date":
        return "Date"
    return " ".join(ACRONYMS.get(w, w.capitalize()) for w in words)


def quote(name: str) -> str:
    return f"'{name}'" if not name.isidentifier() else name


def contracts() -> dict[str, list[dict[str, Any]]]:
    doc = yaml.safe_load(CONTRACTS.read_text(encoding="utf-8"))
    return {m["name"]: m["columns"] for m in doc["models"]}


def tmdl_type(data_type: str) -> tuple[str, str | None]:
    if data_type == "integer":
        return "int64", "0"
    if data_type == "date":
        return "dateTime", "Short Date"
    if data_type == "boolean":
        return "boolean", None
    if data_type.startswith("numeric(9"):
        return "double", "0.0%"
    if data_type.startswith("numeric(14"):
        return "decimal", "\\$#,0.00"
    if data_type.startswith("numeric"):
        return "double", "#,0.00"
    return "string", None


# --- semantic model (TMDL) --------------------------------------------------------------


def table_tmdl(table: str, view: str, columns: list[dict[str, Any]]) -> str:
    lines = [f"table {quote(table)}", f"\tlineageTag: {tag('table', table)}"]
    if table == "Date":
        lines.append("\tdataCategory: Time")
    lines.append("")
    for col in columns:
        data_type, fmt = tmdl_type(col["data_type"])
        name = friendly(col["name"])
        lines.append(f"\tcolumn {quote(name)}")
        lines.append(f"\t\tdataType: {data_type}")
        if fmt:
            lines.append(f"\t\tformatString: {fmt}")
        if table == "Date" and col["name"] == "date_day":
            lines.append("\t\tisKey")
        if col["name"].endswith("_id") and table in FACTS:
            lines.append("\t\tisHidden")
        if col.get("description"):
            lines.append(f"\t\tdescription: {col['description']}")
        lines.append(f"\t\tlineageTag: {tag('column', table, col['name'])}")
        lines.append("\t\tsummarizeBy: none")
        lines.append(f"\t\tsourceColumn: {col['name']}")
        lines.append("")
        lines.append("\t\tannotation SummarizationSetBy = Automatic")
        lines.append("")
    lines += [
        f"\tpartition {quote(table)} = m",
        "\t\tmode: import",
        "\t\tsource =",
        "\t\t\t\tlet",
        "\t\t\t\t    Source = PostgreSQL.Database(Server, Database),",
        f'\t\t\t\t    Data = Source{{[Schema = "reporting", Item = "{view}"]}}[Data]',
        "\t\t\t\tin",
        "\t\t\t\t    Data",
        "",
        "\tannotation PBI_ResultType = Table",
        "",
    ]
    return "\n".join(lines)


def measures_tmdl() -> str:
    lines = ["table Measures", f"\tlineageTag: {tag('table', 'Measures')}", ""]
    for m in MEASURES:
        lines += [
            f"\tmeasure {quote(m.name)} = {m.dax}",
            f"\t\tformatString: {m.fmt}",
            f"\t\tdisplayFolder: {m.folder}",
            f"\t\tdescription: {m.description}",
            f"\t\tlineageTag: {tag('measure', m.name)}",
            "",
        ]
    lines += [
        "\tcolumn Value",
        "\t\tdataType: int64",
        "\t\tisHidden",
        "\t\tisNameInferred",
        f"\t\tlineageTag: {tag('column', 'Measures', 'Value')}",
        "\t\tsummarizeBy: none",
        "\t\tsourceColumn: [Value]",
        "",
        "\tpartition Measures = calculated",
        "\t\tmode: import",
        '\t\tsource = ROW("Value", 0)',
        "",
        "\tannotation PBI_Id = " + tag("measures-table").replace("-", ""),
        "",
    ]
    return "\n".join(lines)


def model_tmdl() -> str:
    refs = "\n".join(f"ref table {quote(t)}" for t in [*TABLES, "Measures"])
    return (
        "model Model\n"
        "\tculture: en-US\n"
        "\tdefaultPowerBIDataSourceVersion: powerBI_V3\n"
        "\tdiscourageImplicitMeasures\n"
        "\tsourceQueryCulture: en-US\n"
        "\tdataAccessOptions\n"
        "\t\tlegacyRedirects\n"
        "\t\treturnErrorValuesAsNull\n"
        "\n"
        "annotation __PBI_TimeIntelligenceEnabled = 0\n"
        "\n"
        f"{refs}\n"
        "\n"
        "ref expression Server\n"
        "ref expression Database\n"
    )


def expressions_tmdl() -> str:
    def parameter(name: str, value: str) -> str:
        return (
            f'expression {name} = "{value}" meta [IsParameterQuery = true, Type = "Text", '
            "IsParameterQueryRequired = true]\n"
            f"\tlineageTag: {tag('expression', name)}\n"
            "\n"
            "\tannotation PBI_ResultType = Text\n"
            "\n"
            "\tannotation PBI_NavigationStepName = Navigation\n"
        )

    return parameter("Server", "localhost:5432") + "\n" + parameter("Database", "warehouse")


def relationships_tmdl(columns: dict[str, list[dict[str, Any]]]) -> str:
    out = []
    for from_table, from_col, to_table, to_col in RELATIONSHIPS:
        out += [
            f"relationship {tag('relationship', from_table, from_col)}",
            f"\tfromColumn: {quote(from_table)}.{quote(friendly(from_col))}",
            f"\ttoColumn: {quote(to_table)}.{quote(friendly(to_col))}",
            "",
        ]
    return "\n".join(out)


# --- report (PBIR) ----------------------------------------------------------------------


def field(entity: str, prop: str, measure: bool = False) -> dict[str, Any]:
    kind = "Measure" if measure else "Column"
    return {
        "field": {kind: {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}},
        "queryRef": f"{entity}.{prop}",
        "nativeQueryRef": prop,
    }


def visual(name, kind, x, y, w, h, buckets, title):
    return {
        "$schema": SCHEMA + "item/report/definition/visualContainer/2.4.0/schema.json",
        "name": name,
        "position": {"x": x, "y": y, "z": 0, "width": w, "height": h},
        "visual": {
            "visualType": kind,
            "query": {
                "queryState": {
                    bucket: {"projections": [field(*f) for f in fields]}
                    for bucket, fields in buckets.items()
                }
            },
            "visualContainerObjects": {
                "title": [
                    {
                        "properties": {
                            "show": {"expr": {"Literal": {"Value": "true"}}},
                            "text": {"expr": {"Literal": {"Value": f"'{title}'"}}},
                        }
                    }
                ]
            },
            "drillFilterOtherVisuals": True,
        },
    }


M = "Measures"
DATE_SLICER = ("Date", "Date")
PAGES = {
    "main_report": ("Main Report", [
        visual("date_range", "slicer", 16, 16, 300, 80, {"Values": [DATE_SLICER]}, "Dates"),
        visual("net_utilization", "card", 332, 16, 220, 80,
               {"Values": [(M, "Net Utilization %", True)]}, "Net Utilization %"),
        visual("utilization_matrix", "pivotTable", 16, 112, 1248, 592, {
            "Rows": [("Employee", "Group Name"), ("Employee", "Department Name"),
                     ("Employee", "Employee Name"), ("Date", "Week Start")],
            "Values": [(M, "Net Utilization %", True), (M, "Exempt %", True),
                       (M, "Project Work %", True), (M, "Client BD %", True),
                       (M, "Internal %", True)],
        }, "Utilization by group, department, employee and week"),
    ]),
    "time_to_clients": ("Time to Clients", [
        visual("client_slicer", "slicer", 16, 16, 300, 200, {"Values": [("Client", "Client Name")]},
               "Clients"),
        visual("top_clients", "clusteredBarChart", 332, 16, 932, 300, {
            "Category": [("Client", "Client Name")], "Y": [(M, "Client Hours", True)],
        }, "Client hours by client"),
        visual("client_matrix", "pivotTable", 16, 332, 1248, 372, {
            "Rows": [("Client", "Client Name"), ("Employee", "Department Name"),
                     ("Employee", "Employee Name")],
            "Values": [(M, "Total Hours", True), (M, "Client Hours", True),
                       (M, "Client Rank", True)],
        }, "Hours by client and employee"),
    ]),
    "tasks_by_person": ("Breakdown - Tasks by Person", [
        visual("item_slicer", "slicer", 16, 16, 300, 300, {"Values": [("Item", "Item Name")]},
               "Items"),
        visual("tasks_matrix", "pivotTable", 332, 16, 932, 688, {
            "Rows": [("Project", "Department Name"), ("Project", "Project Display Name"),
                     ("Item", "Item Name"), ("Employee", "Employee Name")],
            "Values": [(M, "Total Hours", True)],
        }, "Hours by project, task and person"),
    ]),
    "persons_by_task": ("Breakdown - Persons by Task", [
        visual("item_slicer", "slicer", 16, 16, 300, 300, {"Values": [("Item", "Item Name")]},
               "Items"),
        visual("persons_matrix", "pivotTable", 332, 16, 932, 688, {
            "Rows": [("Employee", "Department Name"), ("Employee", "Employee Name"),
                     ("Item", "Item Name"), ("Project", "Project Display Name")],
            "Values": [(M, "Total Hours", True)],
        }, "Hours by person, task and project"),
    ]),
    "project_margin": ("Project Contribution Margin", [
        visual("margin_cards", "card", 16, 16, 300, 80, {"Values": [(M, "Project Profit %", True)]},
               "Project Profit %"),
        visual("margin_table", "tableEx", 16, 112, 1248, 592, {"Values": [
            ("Project Margin", "Department Name"), ("Project Margin", "Project Lead"),
            ("Project Margin", "Client Name"), ("Project Margin", "Project Display Name"),
            ("Project Margin", "Project Status Label"), ("Project Margin", "Total Hours"),
            ("Project Margin", "Price"), ("Project Margin", "Travel Cost"),
            ("Project Margin", "Incentive Cost"), ("Project Margin", "Field Cost"),
            (M, "Project Profit %", True), (M, "Contribution Margin %", True),
        ]}, "Projects"),
    ]),
}  # fmt: skip


# --- writing ----------------------------------------------------------------------------


def platform(kind: str) -> dict[str, Any]:
    return {
        "$schema": SCHEMA + "gitIntegration/platformProperties/2.0.0/schema.json",
        "metadata": {"type": kind, "displayName": NAME},
        "config": {"version": "2.0", "logicalId": tag("platform", kind)},
    }


def files() -> dict[str, str]:
    def js(data: Any) -> str:
        return json.dumps(data, indent=2, ensure_ascii=False) + "\n"

    cols = contracts()
    model = f"{NAME}.SemanticModel"
    report = f"{NAME}.Report"
    out: dict[str, str] = {
        f"{NAME}.pbip": js(
            {
                "$schema": SCHEMA + "pbip/pbipProperties/1.0.0/schema.json",
                "version": "1.0",
                "artifacts": [{"report": {"path": report}}],
                "settings": {"enableAutoRecovery": True},
            }
        ),
        f"{model}/.platform": js(platform("SemanticModel")),
        f"{model}/definition.pbism": js(
            {
                "$schema": SCHEMA + "item/semanticModel/definitionProperties/1.0.0/schema.json",
                "version": "4.2",
                "settings": {},
            }
        ),
        f"{model}/definition/database.tmdl": "database\n\tcompatibilityLevel: 1600\n",
        f"{model}/definition/model.tmdl": model_tmdl(),
        f"{model}/definition/expressions.tmdl": expressions_tmdl(),
        f"{model}/definition/relationships.tmdl": relationships_tmdl(cols),
        f"{model}/definition/tables/Measures.tmdl": measures_tmdl(),
        f"{report}/.platform": js(platform("Report")),
        f"{report}/definition.pbir": js(
            {
                "$schema": SCHEMA + "item/report/definitionProperties/2.0.0/schema.json",
                "version": "4.0",
                "datasetReference": {"byPath": {"path": f"../{model}"}},
            }
        ),
        f"{report}/definition/version.json": js(
            {
                "$schema": SCHEMA + "item/report/definition/versionMetadata/1.0.0/schema.json",
                "version": "2.0.0",
            }
        ),
        f"{report}/definition/report.json": js(
            {
                "$schema": SCHEMA + "item/report/definition/report/3.0.0/schema.json",
                "themeCollection": {},
                "settings": {
                    "useStylableVisualContainerHeader": True,
                    "defaultDrillFilterOtherVisuals": True,
                },
            }
        ),
        f"{report}/definition/pages/pages.json": js(
            {
                "$schema": SCHEMA + "item/report/definition/pagesMetadata/1.0.0/schema.json",
                "pageOrder": list(PAGES),
                "activePageName": next(iter(PAGES)),
            }
        ),
    }
    for table, (view, contract) in TABLES.items():
        out[f"{model}/definition/tables/{table}.tmdl"] = table_tmdl(table, view, cols[contract])
    for page, (display, visuals) in PAGES.items():
        out[f"{report}/definition/pages/{page}/page.json"] = js(
            {
                "$schema": SCHEMA + "item/report/definition/page/2.0.0/schema.json",
                "name": page,
                "displayName": display,
                "displayOption": "FitToPage",
                "height": 720,
                "width": 1280,
            }
        )
        for v in visuals:
            out[f"{report}/definition/pages/{page}/visuals/{v['name']}/visual.json"] = js(v)
    return out


def main() -> None:
    for folder in (f"{NAME}.SemanticModel", f"{NAME}.Report"):
        shutil.rmtree(HERE / folder, ignore_errors=True)
    for relative, text in files().items():
        path = HERE / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {len(files())} files under {HERE}")


if __name__ == "__main__":
    main()
